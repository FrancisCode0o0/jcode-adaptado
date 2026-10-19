#!/usr/bin/env python3
# MIT License - Copyright (c) 2026 Prof. Dr. Joern Fischer (full text: see README.md)
"""jprompt - a small, dependency-free multiline prompt editor for the terminal.

The editor mimics the input box of Claude Code: the text starts on its own
line (below an optional header such as "Task>"), wraps at the terminal
width, scrolls vertically when it is taller than the terminal and keeps the
cursor visible at all times.  The terminal is read character by character
(cbreak-like raw mode); nothing is echoed by the terminal itself.

Keys
    Enter            submit the text
    Ctrl+J           insert a new line (Shift+Enter / Alt+Enter work too
                     where the terminal reports them)
    Ctrl+C           abort the input        -> raises Abort (a KeyboardInterrupt
                     whose .text holds the discarded input, "" if it was empty)
    Ctrl+D           exit the application   -> raises EOFError
    Ctrl+V / Cmd+V   paste.  Terminals normally deliver a paste themselves
                     (bracketed paste is enabled so newlines in the pasted
                     text do NOT submit).  If the terminal passes Ctrl+V
                     through, the system clipboard is read as a fallback.
    Arrows           move the cursor.  Up on the first row and Down on the
                     last row do nothing.
    Home/End, Ctrl+A/Ctrl+E, Backspace, Delete, Tab (= 4 spaces)

Usage
    from jprompt import prompt
    text = prompt("Task>")          # header is printed, editing starts below

Architecture (every part can be tested without a terminal)
    Buffer     lines + cursor and all editing operations
    Layout     wraps the Buffer lines to a width, maps cursor <-> screen cell
    Editor     Buffer + Layout + rendering (ANSI) into any file-like object
    KeyReader  turns raw characters into KeyEvents (escape sequences, paste)
    prompt()   wires everything to the real terminal (raw mode, SIGWINCH)

Known limitation: terminals that *reflow* old text on resize (iTerm2,
kitty, Windows Terminal) may leave a stale copy of the editor above the
freshly drawn one after a resize; the editor itself and its cursor stay
correct because every redraw is relative to the cursor position.
"""
from __future__ import annotations

import codecs
import os
import select
import shutil
import signal
import subprocess
import sys
import unicodedata
from dataclasses import dataclass

try:
    import termios
except ImportError:  # Windows
    termios = None
try:
    import msvcrt
except ImportError:
    msvcrt = None

__all__ = ["prompt", "Abort", "Buffer", "Layout", "Editor", "KeyReader", "KeyEvent", "RESIZE"]

class Abort(KeyboardInterrupt):
    """Raised on Ctrl+C; `.text` is the input that was being edited."""

    def __init__(self, text: str = "") -> None:
        super().__init__("input aborted")
        self.text = text

# ------------------------------------------------------------- Text helpers
def char_width(ch: str) -> int:
    """Display width of one character (0 for combining marks, 2 for CJK)."""
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1

def str_width(s: str) -> int:
    return sum(char_width(ch) for ch in s)

def _clean(text: str) -> str:
    """Normalises newlines, expands tabs and drops control characters."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    return "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch) != "Cc")

# ------------------------------------------------------------------- Buffer
class Buffer:
    """Editable text: a list of logical lines plus a cursor (row, col)."""

    def __init__(self, text: str = "") -> None:
        self.lines: list[str] = _clean(text).split("\n")
        self.row = len(self.lines) - 1
        self.col = len(self.lines[-1])

    # -- state
    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def cursor(self) -> tuple[int, int]:
        return self.row, self.col

    # -- editing
    def insert(self, text: str) -> None:
        parts = _clean(text).split("\n")
        line = self.lines[self.row]
        head, tail = line[: self.col], line[self.col :]
        if len(parts) == 1:
            self.lines[self.row] = head + parts[0] + tail
            self.col += len(parts[0])
            return
        self.lines[self.row : self.row + 1] = [head + parts[0], *parts[1:-1], parts[-1] + tail]
        self.row += len(parts) - 1
        self.col = len(parts[-1])

    def backspace(self) -> None:
        if self.col > 0:
            line = self.lines[self.row]
            self.lines[self.row] = line[: self.col - 1] + line[self.col :]
            self.col -= 1
        elif self.row > 0:
            self.col = len(self.lines[self.row - 1])
            self.lines[self.row - 1] += self.lines.pop(self.row)
            self.row -= 1

    def delete(self) -> None:
        line = self.lines[self.row]
        if self.col < len(line):
            self.lines[self.row] = line[: self.col] + line[self.col + 1 :]
        elif self.row + 1 < len(self.lines):
            self.lines[self.row] += self.lines.pop(self.row + 1)

    # -- horizontal movement
    def left(self) -> None:
        if self.col > 0:
            self.col -= 1
        elif self.row > 0:
            self.row -= 1
            self.col = len(self.lines[self.row])

    def right(self) -> None:
        if self.col < len(self.lines[self.row]):
            self.col += 1
        elif self.row + 1 < len(self.lines):
            self.row += 1
            self.col = 0

    def home(self) -> None:
        self.col = 0

    def end(self) -> None:
        self.col = len(self.lines[self.row])

# ------------------------------------------------------------------- Layout
@dataclass(frozen=True)
class VisualRow:
    line: int    # index of the logical line
    start: int   # first character (inclusive)
    end: int     # last character (exclusive)
    last: bool   # last visual row of its logical line?

class Layout:
    """Wraps logical lines to `width` display columns.

    A logical line of exactly `width` columns is NOT split: the cursor may sit
    at display column `width` (which is why Editor uses terminal width - 1).
    A cursor at the end of a non-last visual row is shown at the start of the
    next row - the same convention prompt_toolkit uses.
    """

    def __init__(self, lines: list[str], width: int) -> None:
        self.lines = lines
        self.width = max(1, width)
        self.rows: list[VisualRow] = []
        for li, line in enumerate(lines):
            start = acc = 0
            for i, ch in enumerate(line):
                w = char_width(ch)
                if acc + w > self.width and i > start:
                    self.rows.append(VisualRow(li, start, i, False))
                    start, acc = i, 0
                acc += w
            self.rows.append(VisualRow(li, start, len(line), True))

    def row_text(self, r: VisualRow) -> str:
        return self.lines[r.line][r.start : r.end]

    def cursor_to_visual(self, row: int, col: int) -> tuple[int, int]:
        """(logical row, col) -> (visual row index, display column)."""
        for vr, r in enumerate(self.rows):
            if r.line == row and (r.start <= col < r.end or (r.last and col == r.end)):
                return vr, str_width(self.lines[row][r.start : col])
        return len(self.rows) - 1, 0  # unreachable for a consistent buffer

    def visual_to_cursor(self, vr: int, want_col: int) -> tuple[int, int]:
        """(visual row index, wanted display column) -> nearest (row, col)."""
        r = self.rows[vr]
        line = self.lines[r.line]
        limit = r.end if r.last else r.end - 1  # end of a wrapped row belongs to the next row
        col, acc = r.start, 0
        while col < limit:
            w = char_width(line[col])
            if acc + w > want_col:
                break
            acc, col = acc + w, col + 1
        return r.line, col

# ---------------------------------------------------------------- KeyEvents
@dataclass(frozen=True)
class KeyEvent:
    kind: str        # text, newline, enter, backspace, delete, left, right, up,
                     # down, home, end, ctrl_c, ctrl_d, resize, ignore
    text: str = ""   # only for kind == "text" (typed char or pasted block)

RESIZE = object()  # sentinel a key source returns when the window was resized

# ------------------------------------------------------------------- Editor
class Editor:
    """Buffer + rendering.  `out` needs write()/flush(); `size()` -> (cols, rows).

    The editor assumes the terminal cursor is at column 0 of the line where
    the text should start when render() is first called.  All later drawing is
    relative to the cursor, so scrolling of the terminal does not matter.
    """

    def __init__(self, out, size, text: str = "") -> None:
        self.buf = Buffer(text)
        self.out = out
        self._size = size
        self._want_col: int | None = None  # sticky column for Up/Down
        self._top = 0        # first visible visual row (vertical scrolling)
        self._cur_row = 0    # screen row of the cursor relative to the first drawn row
        self._drawn = 0      # number of rows drawn by the last render()
        self.hidden_above = 0  # visual rows scrolled out of view (shown as markers)
        self.hidden_below = 0

    # -- geometry
    @property
    def width(self) -> int:
        """Wrap width: one less than the terminal so the cursor never touches the auto-wrap column."""
        return max(1, self._size()[0] - 1)

    @property
    def height(self) -> int:
        """Max. visible rows: one less than the terminal so a header line above survives."""
        return max(1, self._size()[1] - 1)

    @property
    def text(self) -> str:
        return self.buf.text

    def layout(self) -> Layout:
        return Layout(self.buf.lines, self.width)

    # -- keys
    def handle(self, key: KeyEvent) -> bool:
        """Applies one key; returns True when the text was submitted."""
        k = key.kind
        if k == "ctrl_c":
            raise Abort(self.text)
        if k == "ctrl_d":
            raise EOFError
        if k == "enter":
            return True
        if k == "text":
            self.buf.insert(key.text)
        elif k == "newline":
            self.buf.insert("\n")
        elif k == "backspace":
            self.buf.backspace()
        elif k == "delete":
            self.buf.delete()
        elif k == "left":
            self.buf.left()
        elif k == "right":
            self.buf.right()
        elif k == "home":
            self.buf.home()
        elif k == "end":
            self.buf.end()
        elif k == "up":
            self.move_up()
        elif k == "down":
            self.move_down()
        if k not in ("up", "down", "ignore"):
            self._want_col = None
        self.render()
        return False

    def move_up(self) -> bool:
        return self._vertical(-1)

    def move_down(self) -> bool:
        return self._vertical(+1)

    def _vertical(self, delta: int) -> bool:
        """Moves one *visual* row; returns False at the top/bottom edge."""
        lay = self.layout()
        vr, vc = lay.cursor_to_visual(*self.buf.cursor)
        target = vr + delta
        if not 0 <= target < len(lay.rows):
            return False
        if self._want_col is None:
            self._want_col = vc
        self.buf.row, self.buf.col = lay.visual_to_cursor(target, self._want_col)
        return True

    # -- drawing
    def _viewport(self, vr: int, n: int) -> tuple[int, int, bool, bool]:
        """Chooses the first visible row so the cursor row is on screen.

        Returns (top, rows shown, marker above?, marker below?).  Each marker
        line ("... N more lines ...") costs one row of the available height,
        so the choice is iterated until it is stable.
        """
        h, top = self.height, self._top
        above = below = False
        for _ in range(3):
            avail = max(1, h - above - below)
            if vr < top:
                top = vr
            elif vr >= top + avail:
                top = vr - avail + 1
            top = max(0, min(top, n - avail))
            if h < 3:                              # no room for markers
                return top, avail, False, False
            new = (top > 0, top + avail < n)
            if new == (above, below):
                return top, avail, above, below
            above, below = new
        return top, avail, above, below

    def _marker(self, count: int, where: str) -> str:
        text = f"\u2026 {count} more line{'s' if count != 1 else ''} {where} \u2026"
        if str_width(text) > self.width:
            arrow = "\u2191" if where == "above" else "\u2193"
            text = f"\u2026{count}{arrow}"
        return f"\x1b[2m{text}\x1b[0m"

    def render(self) -> None:
        lay = self.layout()
        vr, vc = lay.cursor_to_visual(*self.buf.cursor)
        n = len(lay.rows)
        top, avail, above, below = self._viewport(vr, n)
        self._top = top
        self.hidden_above, self.hidden_below = top, max(0, n - top - avail)

        lines = [lay.row_text(r) for r in lay.rows[top : top + avail]]
        if above:
            lines.insert(0, self._marker(self.hidden_above, "above"))
        if below:
            lines.append(self._marker(self.hidden_below, "below"))
        crow = vr - top + int(above)

        parts = []
        if self._cur_row > 0:                      # back to the first drawn row
            parts.append(f"\x1b[{self._cur_row}A")
        parts.append("\r\x1b[J")                   # column 0, clear to end of screen
        parts.append("\r\n".join(lines))
        back = len(lines) - 1 - crow
        if back > 0:
            parts.append(f"\x1b[{back}A")
        parts.append("\r")
        if vc > 0:
            parts.append(f"\x1b[{vc}C")
        self.out.write("".join(parts))
        self.out.flush()
        self._cur_row, self._drawn = crow, len(lines)

    def finish(self) -> None:
        """Moves the cursor below the text and starts a fresh line."""
        down = self._drawn - 1 - self._cur_row
        self.out.write((f"\x1b[{down}B" if down > 0 else "") + "\r\n")
        self.out.flush()

# ---------------------------------------------------------------- KeyReader
class KeyReader:
    """Decodes characters from a `source` into KeyEvents.

    source.read()            blocks; returns one character or RESIZE
    source.pending(timeout)  True if a character arrives within `timeout` s
    """

    ESC_TIMEOUT = 0.05

    _CTRL = {"\r": "enter", "\n": "newline", "\x7f": "backspace", "\x08": "backspace",
             "\x03": "ctrl_c", "\x04": "ctrl_d", "\x01": "home", "\x05": "end"}
    _CSI_FINAL = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
    _CSI_TILDE = {"1": "home", "7": "home", "4": "end", "8": "end", "3": "delete"}

    def __init__(self, source) -> None:
        self.source = source
        self._resized = False

    def read_key(self) -> KeyEvent:
        if self._resized:
            self._resized = False
            return KeyEvent("resize")
        ch = self.source.read()
        if ch is RESIZE:
            return KeyEvent("resize")
        if ch == "\x1b":
            return self._escape()
        if ch == "\x16":                              # Ctrl+V passed through by the terminal
            text = _clipboard()
            return KeyEvent("text", text) if text else KeyEvent("ignore")
        if ch == "\t":
            return KeyEvent("text", "    ")
        if ch in self._CTRL:
            return KeyEvent(self._CTRL[ch])
        if unicodedata.category(ch) != "Cc":
            return KeyEvent("text", ch)
        return KeyEvent("ignore")

    def _next(self) -> str:
        """Next character inside an escape sequence; remembers resizes for later."""
        while True:
            ch = self.source.read()
            if ch is RESIZE:
                self._resized = True
                continue
            return ch

    def _escape(self) -> KeyEvent:
        if not self.source.pending(self.ESC_TIMEOUT):
            return KeyEvent("ignore")                 # a lone Esc
        c = self._next()
        if c in ("\r", "\n"):
            return KeyEvent("newline")                # Alt+Enter
        if c == "O":                                  # SS3: cursor keys in application mode
            return KeyEvent(self._CSI_FINAL.get(self._next(), "ignore"))
        if c != "[":
            return KeyEvent("ignore")                 # Alt+<key>
        seq = ""
        while True:
            d = self._next()
            seq += d
            if "\x40" <= d <= "\x7e":
                break
            if len(seq) > 32:
                return KeyEvent("ignore")
        return self._csi(seq)

    def _csi(self, seq: str) -> KeyEvent:
        final, params = seq[-1], seq[:-1]
        if seq == "200~":                             # bracketed paste
            return self._paste()
        if final in self._CSI_FINAL:
            return KeyEvent(self._CSI_FINAL[final])
        if final == "~":
            return KeyEvent(self._CSI_TILDE.get(params.split(";")[0], "ignore"))
        if final == "u" and params.split(";")[0] == "13":  # kitty protocol: Shift/Ctrl+Enter
            return KeyEvent("newline")
        return KeyEvent("ignore")

    def _paste(self) -> KeyEvent:
        end, buf = "\x1b[201~", []
        while True:
            buf.append(self._next())
            if len(buf) >= len(end) and "".join(buf[-len(end):]) == end:
                return KeyEvent("text", "".join(buf[: -len(end)]))

def _clipboard() -> str:
    """Best-effort read of the system clipboard (fallback for a raw Ctrl+V)."""
    for cmd in (["pbpaste"], ["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"],
                ["xsel", "-b", "-o"], ["powershell", "-NoProfile", "-Command", "Get-Clipboard"]):
        if shutil.which(cmd[0]):
            try:
                return subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout
            except Exception:  # noqa: BLE001
                continue
    return ""

# -------------------------------------------------------- Terminal plumbing
class _TtySource:
    """Character source for a POSIX tty; `wake_fd` (optional) signals resizes."""

    def __init__(self, fd: int, wake_fd: int | None = None) -> None:
        self.fd, self.wake = fd, wake_fd
        self._dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buf: list[str] = []   # decoded but not yet consumed characters

    def read(self):
        fds = [self.fd] if self.wake is None else [self.fd, self.wake]
        while not self._buf:
            ready, _, _ = select.select(fds, [], [])
            if self.wake in ready:
                try:
                    os.read(self.wake, 64)
                except OSError:
                    pass
                return RESIZE
            # Read whole chunks: a large paste must be drained quickly, the
            # kernel's tty input queue is small (1 KB on macOS).
            b = os.read(self.fd, 65536)
            if not b:
                raise EOFError
            self._buf.extend(self._dec.decode(b))
        return self._buf.pop(0)

    def pending(self, timeout: float) -> bool:
        if self._buf:
            return True
        ready, _, _ = select.select([self.fd], [], [], timeout)
        return bool(ready)

class _WinSource:
    """Character source for the Windows console (msvcrt); special keys become CSI sequences."""

    _SPECIAL = {"H": "A", "P": "B", "K": "D", "M": "C", "G": "H", "O": "F", "S": "3~"}

    def __init__(self) -> None:
        self._queue: list[str] = []

    def read(self):
        if self._queue:
            return self._queue.pop(0)
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            code = self._SPECIAL.get(msvcrt.getwch())
            if code is None:
                return self.read()
            self._queue.extend("[" + code)
            return "\x1b"
        return ch

    def pending(self, timeout: float) -> bool:
        return bool(self._queue) or msvcrt.kbhit()

class _RawMode:
    """Character-at-a-time input without echo; Enter stays '\\r', Ctrl+J stays '\\n'."""

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def __enter__(self):
        if termios is None:
            return self
        self.old = termios.tcgetattr(self.fd)
        new = termios.tcgetattr(self.fd)
        new[0] &= ~(termios.ICRNL | termios.INLCR | termios.IXON)              # iflag
        new[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN)  # lflag
        new[6][termios.VMIN], new[6][termios.VTIME] = 1, 0
        termios.tcsetattr(self.fd, termios.TCSANOW, new)
        return self

    def __exit__(self, *exc):
        if termios is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)

class _ResizeWatcher:
    """Turns SIGWINCH into a readable byte on `self.fd` (None when unsupported)."""

    def __enter__(self):
        self.fd = None
        if not hasattr(signal, "SIGWINCH"):
            return self
        r, w = os.pipe()
        try:
            os.set_blocking(w, False)
            self._old_wake = signal.set_wakeup_fd(w)
            self._old_handler = signal.signal(signal.SIGWINCH, lambda *_: None)
            self.fd, self._pipe = r, (r, w)
        except (ValueError, OSError):  # e.g. not in the main thread
            os.close(r), os.close(w)
        return self

    def __exit__(self, *exc):
        if self.fd is None:
            return
        signal.signal(signal.SIGWINCH, self._old_handler)
        signal.set_wakeup_fd(self._old_wake)
        for fd in self._pipe:
            os.close(fd)

def _terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size((80, 24))
    return size.columns, size.lines

def _enable_windows_vt() -> None:
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        handle, mode = k32.GetStdHandle(-11), ctypes.c_uint32()
        if k32.GetConsoleMode(handle, ctypes.byref(mode)):
            k32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:  # noqa: BLE001
        pass

def _interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty() and (termios or msvcrt) is not None
    except (AttributeError, ValueError):
        return False

# ------------------------------------------------------------------- prompt
def prompt(header: str = "", text: str = "") -> str:
    """Prints `header` (if any) and edits `text` on the following line(s).

    Returns the entered text on Enter.  Raises Abort (a KeyboardInterrupt with
    the current input in `.text`) on Ctrl+C and EOFError on Ctrl+D.  Without a
    terminal (pipe), one line is read from stdin instead.
    """
    if header:
        print(header, flush=True)
    if not _interactive():
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        return line.rstrip("\r\n")

    out = sys.stdout
    editor = Editor(out, _terminal_size, text)
    if termios is None:
        _enable_windows_vt()
        source, fd = _WinSource(), None
    else:
        fd = sys.stdin.fileno()
    with _RawMode(fd), _ResizeWatcher() as watcher:
        if termios is not None:
            source = _TtySource(fd, watcher.fd)
        reader = KeyReader(source)
        out.write("\x1b[?25h\x1b[?2004h")  # cursor visible, bracketed paste on
        try:
            editor.render()
            while not editor.handle(reader.read_key()):
                pass
        finally:
            editor.finish()
            out.write("\x1b[?2004l")
            out.flush()
    return editor.text

if __name__ == "__main__":
    try:
        result = prompt("jprompt demo - Enter submits, Ctrl+J = new line, Ctrl+C aborts, Ctrl+D exits\nText>")
        print(f"You entered: {result!r}")
    except KeyboardInterrupt:
        print("(aborted)")
    except EOFError:
        print("(exit)")
