#!/usr/bin/env bash
#
# Set up any-ctrl on a Raspberry Pi (or any Debian-based Linux) so it can
# emulate a Pro Controller over Bluetooth.
#
#   ./scripts/setup-pi.sh
#
# It installs the system packages, creates a virtualenv, installs any-ctrl,
# and offers to disable bluetoothd's input plugin, which otherwise owns the
# HID ports we need. Every step is safe to run more than once.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${REPO_ROOT}/.venv"
OVERRIDE_DIR=/etc/systemd/system/bluetooth.service.d
OVERRIDE=${OVERRIDE_DIR}/10-anyctrl-noinput.conf

say() { printf '\n== %s\n' "$1"; }
note() { printf '   %s\n' "$1"; }

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This script is for Linux; on macOS use the USB bridge (firmware/README.md)." >&2
    exit 1
fi

say "Installing system packages"
# python3-dbus comes from apt rather than pip: the pip build needs D-Bus
# headers and a compiler, which is a slow and failure-prone detour on a Pi.
sudo apt-get update
sudo apt-get install -y python3-venv python3-dbus bluez bluez-tools

say "Creating the virtualenv at ${VENV}"
# --system-site-packages so the apt-installed python3-dbus is visible inside.
if [[ ! -d "${VENV}" ]]; then
    python3 -m venv --system-site-packages "${VENV}"
fi
"${VENV}/bin/pip" install --upgrade pip >/dev/null
"${VENV}/bin/pip" install "${REPO_ROOT}"

if ! "${VENV}/bin/python" -c "import dbus" 2>/dev/null; then
    echo "python3-dbus is not visible inside the virtualenv." >&2
    echo "Recreate it with: rm -rf ${VENV} && python3 -m venv --system-site-packages ${VENV}" >&2
    exit 1
fi

say "Checking bluetoothd's input plugin"
CURRENT_EXEC="$(systemctl cat bluetooth.service 2>/dev/null | grep -m1 '^ExecStart=' || true)"
if [[ -z "${CURRENT_EXEC}" ]]; then
    note "bluetooth.service not found; is BlueZ installed and enabled?"
elif [[ "${CURRENT_EXEC}" == *"-P input"* || "${CURRENT_EXEC}" == *"--noplugin=input"* ]]; then
    note "already disabled: ${CURRENT_EXEC}"
else
    BLUETOOTHD="${CURRENT_EXEC#ExecStart=}"
    BLUETOOTHD="${BLUETOOTHD%% *}"
    note "bluetoothd currently runs as: ${CURRENT_EXEC}"
    note "Its input plugin claims the HID ports any-ctrl needs (L2CAP 17 and 19)."
    note "Disabling it stops real Bluetooth keyboards, mice and controllers from"
    note "working on this machine until you undo it."
    read -r -p "   Disable the input plugin now? [y/N] " reply
    if [[ "${reply}" =~ ^[Yy]$ ]]; then
        sudo mkdir -p "${OVERRIDE_DIR}"
        sudo tee "${OVERRIDE}" >/dev/null <<EOF
[Service]
ExecStart=
ExecStart=${BLUETOOTHD} -P input
EOF
        sudo systemctl daemon-reload
        sudo systemctl restart bluetooth
        note "done. Undo with: sudo rm ${OVERRIDE} && sudo systemctl daemon-reload && sudo systemctl restart bluetooth"
    else
        note "skipped. The bluez backend will not work until this is done."
    fi
fi

say "Setup finished"
cat <<EOF
   Check the result:
       sudo ${VENV}/bin/anyctrl doctor

   Then, with the console on System Settings > Controllers > Change Grip/Order:
       sudo ${VENV}/bin/anyctrl run ${REPO_ROOT}/macros/connection-test.macro

   Add --console switch2 for a Switch 2.
EOF
