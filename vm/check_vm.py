#!/usr/bin/env python3
"""Check that the jcode VM has every tool the plan needs, and that each works.

Origin: plan step 1.0.b, block D (automation "setup script", 2026-10-20).
Same idea as train/check_env.py on Windows: one OK/FAIL line per piece and
"VM READY" at the end. Each check does a small real job (encode a file,
measure a loudness, open a browser), because "importable" is not "working".

Run inside venv-audio:   python ~/jcode-adaptado/vm/check_vm.py
Exit code 0 = all OK, 1 = something failed.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

SAMPLE_RATE_HZ = 48_000  # plan: working rate 48 kHz
EXPECTED_PYTHON = (3, 14)  # requirements-ref.txt is pinned for 3.14
MIN_NODE_MAJOR = 20  # oldest Node still maintained in 2026
LIBRARIES = ["numpy", "scipy", "soundfile", "pyloudnorm", "librosa", "nara_wpe", "playwright"]
BROWSERS = ["chromium", "firefox", "webkit"]
BROWSER_TIMEOUT_MS = 60_000

# A full-scale 997 Hz sine measures -3.01 LUFS (ITU-R BS.1770 calibration);
# at amplitude 0.1 (-20 dB) it must read -23.01 LUFS.
LOUDNESS_TEST_AMPLITUDE = 0.1
LOUDNESS_EXPECTED_LUFS = -23.01
LOUDNESS_TOLERANCE_LU = 0.5


def check_python() -> str:
    if sys.version_info[:2] != EXPECTED_PYTHON:
        raise RuntimeError(f"Python {sys.version.split()[0]}, expected {EXPECTED_PYTHON}")
    if sys.prefix == sys.base_prefix:
        raise RuntimeError("not inside a virtual environment (activate venv-audio)")
    return f"{sys.version.split()[0]} in {sys.prefix}"


def check_libraries() -> str:
    versions = []
    for name in LIBRARIES:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", None)
        if version is None:
            from importlib.metadata import version as dist_version

            version = dist_version(name.replace("_", "-"))
        versions.append(f"{name} {version}")
    return ", ".join(versions)


def check_fft() -> str:
    import numpy as np

    signal = np.random.default_rng(0).standard_normal(64)
    n = np.arange(64)
    naive_dft = np.exp(-2j * np.pi * np.outer(n, n) / 64) @ signal
    error = np.max(np.abs(np.fft.fft(signal) - naive_dft))
    if error > 1e-9:  # plan F2: FFT reference tolerance
        raise RuntimeError(f"numpy FFT differs from the DFT by {error:.2e}")
    return f"numpy FFT vs DFT error {error:.1e}"


def sine(frequency_hz: float, seconds: float, amplitude: float):
    import numpy as np

    t = np.arange(int(seconds * SAMPLE_RATE_HZ)) / SAMPLE_RATE_HZ
    return amplitude * np.sin(2 * np.pi * frequency_hz * t)


def check_wav_roundtrip() -> str:
    import numpy as np
    import soundfile

    original = sine(440, 0.5, 0.5)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "roundtrip.wav"
        soundfile.write(path, original, SAMPLE_RATE_HZ, subtype="PCM_16")
        restored, rate = soundfile.read(path)
    error = np.max(np.abs(restored - original))
    if rate != SAMPLE_RATE_HZ or error > 1 / 32767:  # one 16-bit step
        raise RuntimeError(f"rate {rate}, error {error:.2e}")
    return f"16-bit WAV at {rate} Hz, error {error:.1e}"


def check_loudness() -> str:
    import pyloudnorm

    meter = pyloudnorm.Meter(SAMPLE_RATE_HZ)
    lufs = meter.integrated_loudness(sine(997, 5.0, LOUDNESS_TEST_AMPLITUDE))
    if abs(lufs - LOUDNESS_EXPECTED_LUFS) > LOUDNESS_TOLERANCE_LU:
        raise RuntimeError(f"{lufs:.2f} LUFS, expected {LOUDNESS_EXPECTED_LUFS}")
    return f"997 Hz sine at -20 dB = {lufs:.2f} LUFS"


def check_pitch() -> str:
    import librosa
    import numpy as np

    pitch = librosa.yin(sine(220, 1.0, 0.5), fmin=60, fmax=500, sr=SAMPLE_RATE_HZ)
    median_hz = float(np.median(pitch))
    if abs(median_hz - 220) > 2.2:  # 1 % of 220 Hz
        raise RuntimeError(f"YIN gives {median_hz:.1f} Hz for a 220 Hz sine")
    return f"YIN on a 220 Hz sine = {median_hz:.1f} Hz"


def check_wpe() -> str:
    import numpy as np
    from nara_wpe.wpe import wpe

    rng = np.random.default_rng(1)
    shape = (9, 1, 200)  # (frequency bins, channels, frames)
    spectrum = rng.standard_normal(shape) + 1j * rng.standard_normal(shape)
    result = wpe(spectrum, taps=5, delay=2, iterations=2)
    if result.shape != shape or not np.all(np.isfinite(result)):
        raise RuntimeError(f"WPE returned shape {result.shape} or non-finite values")
    return "nara_wpe runs on a small STFT"


def run_tool(arguments: list[str], timeout_s: int = 60) -> str:
    completed = subprocess.run(
        arguments, capture_output=True, text=True, timeout=timeout_s, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip().splitlines()[-1:] or "failed")
    return completed.stdout


def check_node() -> str:
    if shutil.which("node") is None or shutil.which("npm") is None:
        raise RuntimeError("node/npm not on PATH (open a new terminal after setup)")
    version = run_tool(["node", "--version"]).strip()
    if int(version.lstrip("v").split(".")[0]) < MIN_NODE_MAJOR:
        raise RuntimeError(f"Node {version} is older than {MIN_NODE_MAJOR}")
    return f"Node {version}, npm {run_tool(['npm', '--version']).strip()}"


def check_ffmpeg_formats() -> str:
    """Encode the formats of plan step 1.8 (m4a, ogg/opus, mp3, wav)."""
    encoders = {"m4a": "aac", "ogg": "libopus", "mp3": "libmp3lame", "wav": "pcm_s16le"}
    with tempfile.TemporaryDirectory() as folder:
        for extension, encoder in encoders.items():
            target = Path(folder) / f"test.{extension}"
            run_tool(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency=440:duration=1:sample_rate={SAMPLE_RATE_HZ}",
                    "-c:a",
                    encoder,
                    str(target),
                ]
            )
            if target.stat().st_size == 0:
                raise RuntimeError(f"{extension} file is empty")
    return "encodes " + ", ".join(encoders)


# Runs inside each browser: the decoding/resampling API the app relies on.
AUDIO_PROBE_JS = """() => {
    const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    return new Offline(1, 4800, 48000).sampleRate;
}"""


def check_browsers() -> str:
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as playwright:
        for name in BROWSERS:
            browser = getattr(playwright, name).launch(timeout=BROWSER_TIMEOUT_MS)
            try:
                rate = browser.new_page().evaluate(AUDIO_PROBE_JS)
            finally:
                browser.close()
            if rate != SAMPLE_RATE_HZ:
                raise RuntimeError(f"{name}: OfflineAudioContext rate {rate}")
            results.append(name)
    return "Web Audio at 48 kHz in " + ", ".join(results)


CHECKS: list[tuple[str, Callable[[], str]]] = [
    ("Python", check_python),
    ("libraries", check_libraries),
    ("FFT", check_fft),
    ("WAV", check_wav_roundtrip),
    ("loudness", check_loudness),
    ("pitch", check_pitch),
    ("WPE", check_wpe),
    ("Node.js", check_node),
    ("ffmpeg", check_ffmpeg_formats),
    ("browsers", check_browsers),
]


def main() -> int:
    failures = 0
    for label, check in CHECKS:
        try:
            print(f"OK    {label:10} {check()}")
        except Exception as error:  # noqa: BLE001  (report every failure, then continue)
            failures += 1
            print(f"FAIL  {label:10} {type(error).__name__}: {error}")
    print()
    print("VM READY" if failures == 0 else f"{failures} check(s) failed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
