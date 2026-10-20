#!/usr/bin/env bash
# Mount the WD disk's datos/ folder inside the VM, read-only, at /mnt/datos.
#
# Origin: plan step 1.0.b, block D (2026-10-20). Decision in the plan:
# "datos/ shared with the VM read-only -> jcode uses the datasets without
# copying them and cannot delete them".
#
# Two locks, so one mistake is not enough to expose the data:
#   1. VMware (Windows side): the shared folder is marked "Read-only".
#   2. Inside the VM: the mount itself uses the "ro" option.
#
# BEFORE running: in VMware, VM settings -> Options -> Shared Folders ->
# Always enabled -> Add -> host path = the datos folder on the WD disk,
# name = datos, tick "Read-only".
#
# Run:  bash ~/jcode-adaptado/vm/mount_data.sh
# Safe to run again. /etc/fstab is backed up first and restored if the new
# line does not mount; "nofail" means a missing share never blocks booting.

set -Eeuo pipefail

readonly SHARE_NAME="datos"
readonly MOUNT_POINT="/mnt/datos"
readonly FSTAB="/etc/fstab"

die() { echo "ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run as your normal user, without sudo"
command -v vmhgfs-fuse >/dev/null || sudo apt-get install -y open-vm-tools
vmware-hgfsclient | grep -qx "$SHARE_NAME" \
    || die "VMware does not share a folder named '$SHARE_NAME' (see BEFORE above)"

fstab_line=".host:/$SHARE_NAME $MOUNT_POINT fuse.vmhgfs-fuse ro,allow_other,nofail,uid=$(id -u),gid=$(id -g) 0 0"

if grep -q "[[:space:]]${MOUNT_POINT}[[:space:]]" "$FSTAB"; then
    echo "$FSTAB already has an entry for $MOUNT_POINT. Not changed."
else
    backup="$FSTAB.bak-$(date +%Y%m%d-%H%M%S)"
    sudo cp "$FSTAB" "$backup"
    echo "$fstab_line" | sudo tee -a "$FSTAB" >/dev/null
    echo "Added to $FSTAB (backup: $backup)."
fi

sudo mkdir -p "$MOUNT_POINT"
sudo systemctl daemon-reload
if ! mountpoint -q "$MOUNT_POINT"; then
    if ! sudo mount "$MOUNT_POINT"; then
        [ -n "${backup:-}" ] && sudo cp "$backup" "$FSTAB" && sudo systemctl daemon-reload \
            && echo "fstab restored from $backup."
        die "mount failed; nothing left changed"
    fi
fi

# The only proof of read-only is a write that fails.
probe="$MOUNT_POINT/.mount_data_write_probe_$$"
if ( : > "$probe" ) 2>/dev/null; then
    rm -f "$probe"
    sudo umount "$MOUNT_POINT"
    die "the share is WRITABLE. Unmounted. Tick 'Read-only' in VMware and check the ro option"
fi
echo "OK: $MOUNT_POINT mounted read-only ($(find "$MOUNT_POINT" -mindepth 1 -maxdepth 1 | wc -l) entries)."
