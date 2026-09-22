#!/usr/bin/env bash
# Safely update an existing Volleyball Schedule Monitor Raspberry Pi install.
# This is intentionally separate from deploy/install.sh, which bootstraps a
# clean machine and creates protected configuration and runtime directories.
set -Eeuo pipefail

APP_DIR=/opt/volleyball-schedule-monitor
RUNTIME_DIR=/var/lib/volleyball-schedule-monitor
UI_SERVICE=volleyball-ui.service
SCHEDULE_TIMER=volleyball-schedule.timer
WIFI_SERVICE=volleyball-wifi-provision.service

step=0
old_commit=""
new_commit=""

fail() {
    printf '\nUpdate failed at step %s. See the diagnostics above.\n' "$step" >&2
}
trap fail ERR

say() {
    printf '\n[%s/6] %s\n' "$1" "$2"
}

require_sudo() {
    if ! sudo -v; then
        echo "Administrator access is required to update the installed application and systemd units." >&2
        exit 1
    fi
}

copy_if_changed() {
    local source=$1 destination=$2
    if ! sudo test -f "$destination" || ! sudo cmp -s "$source" "$destination"; then
        sudo install -o root -g root -m 0644 "$source" "$destination"
        units_changed=1
    fi
}

if [[ ${EUID} -eq 0 ]]; then
    echo "Run this updater as the Git checkout owner, not as root: ./scripts/update-pi.sh" >&2
    exit 1
fi
if [[ $(uname -s) != Linux ]]; then
    echo "This updater supports the installed Raspberry Pi OS/Linux deployment only." >&2
    exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$SOURCE_DIR"

printf '%s\n' "========================================" "Volleyball Schedule Monitor Update" "========================================"

step=1
say "$step" "Checking repository and existing installation..."
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "$SOURCE_DIR is not a Git repository." >&2
    exit 1
fi
if [[ $(git rev-parse --show-toplevel) != "$SOURCE_DIR" ]]; then
    echo "The updater must live directly under the repository's scripts directory." >&2
    exit 1
fi
branch=$(git symbolic-ref --quiet --short HEAD) || {
    echo "Detached HEAD is not supported. Check out a branch that tracks origin first." >&2
    exit 1
}
if ! git remote get-url origin >/dev/null 2>&1; then
    echo "Repository remote 'origin' is required for updates." >&2
    exit 1
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Local tracked changes are present. Commit, stash, or discard them before updating; nothing was changed." >&2
    exit 1
fi
if [[ ! -x "$APP_DIR/.venv/bin/python" ]] || ! "$APP_DIR/.venv/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "The installed virtual environment is missing or unusable at $APP_DIR/.venv. Run sudo ./deploy/install.sh to repair this installation." >&2
    exit 1
fi
if [[ ! -d "$RUNTIME_DIR" ]]; then
    echo "The installed runtime directory is missing at $RUNTIME_DIR. Run sudo ./deploy/install.sh to repair this installation." >&2
    exit 1
fi
old_commit=$(git rev-parse --short HEAD)
printf 'Current commit: %s (%s)\n' "$old_commit" "$branch"
require_sudo

step=2
say "$step" "Pulling latest code..."
git fetch --prune origin "$branch"
if ! git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
    echo "origin/$branch was not found after fetch; refusing to update." >&2
    exit 1
fi
git merge --ff-only "origin/$branch"
new_commit=$(git rev-parse --short HEAD)
printf 'New commit: %s\n' "$new_commit"

requirements_changed=0
if [[ "$old_commit" != "$new_commit" ]] && git diff --quiet "$old_commit" "$new_commit" -- requirements.txt; then
    :
elif [[ "$old_commit" != "$new_commit" ]]; then
    requirements_changed=1
fi

step=3
say "$step" "Updating application code and Python dependencies..."
# git archive contains only source-controlled application files. In particular,
# it cannot copy untracked runtime files, settings, credentials, PDFs, or a
# virtualenv from the checkout into the installed application. The production
# runtime normally lives in $RUNTIME_DIR and is never read or written here.
git archive --format=tar HEAD | sudo tar --no-same-owner -C "$APP_DIR" -xf -

if [[ $requirements_changed -eq 1 ]]; then
    "$APP_DIR/.venv/bin/python" -m pip install --no-cache-dir -r "$APP_DIR/requirements.txt"
else
    echo "requirements.txt is unchanged; keeping the existing virtual environment."
fi

step=4
say "$step" "Updating systemd service definitions..."
units_changed=0
copy_if_changed "$SOURCE_DIR/deploy/volleyball-schedule.service" "/etc/systemd/system/volleyball-schedule.service"
copy_if_changed "$SOURCE_DIR/deploy/volleyball-schedule.timer" "/etc/systemd/system/volleyball-schedule.timer"
copy_if_changed "$SOURCE_DIR/deploy/volleyball-ui.service" "/etc/systemd/system/volleyball-ui.service"
copy_if_changed "$SOURCE_DIR/deploy/volleyball-wifi-provision.service" "/etc/systemd/system/volleyball-wifi-provision.service"

# This is deployed with the related provisioner configuration, but is not a
# user configuration file; it is safe to refresh from the checked-out source.
if ! sudo test -f /etc/NetworkManager/dnsmasq-shared.d/volleyballpi-captive.conf || \
   ! sudo cmp -s "$SOURCE_DIR/deploy/volleyballpi-captive.conf" /etc/NetworkManager/dnsmasq-shared.d/volleyballpi-captive.conf; then
    sudo install -d -o root -g root -m 0755 /etc/NetworkManager/dnsmasq-shared.d
    sudo install -o root -g root -m 0644 "$SOURCE_DIR/deploy/volleyballpi-captive.conf" /etc/NetworkManager/dnsmasq-shared.d/volleyballpi-captive.conf
fi
if [[ $units_changed -eq 1 ]]; then
    sudo systemctl daemon-reload
else
    echo "Systemd unit files are unchanged."
fi
sudo systemctl enable "$SCHEDULE_TIMER" "$UI_SERVICE" "$WIFI_SERVICE"

step=5
say "$step" "Restarting application services..."
# The monitor is a oneshot service driven by its timer. Restarting its timer
# preserves its normal cadence without triggering an extra scan/email run.
sudo systemctl restart "$SCHEDULE_TIMER"
sudo systemctl restart "$UI_SERVICE"

step=6
say "$step" "Verifying services..."
verify_active() {
    local unit=$1 label=$2
    if sudo systemctl is-active --quiet "$unit"; then
        printf '%s: active\n' "$label"
        return 0
    fi
    printf '%s: failed to start\n' "$label" >&2
    sudo journalctl -u "$unit" -n 40 --no-pager >&2 || true
    return 1
}
verify_active "$SCHEDULE_TIMER" "volleyball-schedule timer"
verify_active "$UI_SERVICE" "volleyball-ui"

ui_port=$(sudo awk -F= '/^[[:space:]]*UI_PORT=/{value=$2} END {print value}' /etc/volleyball-schedule-monitor.env 2>/dev/null || true)
ui_port=${ui_port:-8080}
if [[ ! $ui_port =~ ^[0-9]+$ ]] || (( ui_port < 1 || ui_port > 65535 )); then
    echo "Web UI: skipped (invalid UI_PORT in /etc/volleyball-schedule-monitor.env)" >&2
elif command -v curl >/dev/null 2>&1; then
    ui_ok=0
    for _attempt in {1..10}; do
        if curl --fail --silent --show-error --max-time 3 "http://127.0.0.1:$ui_port/" >/dev/null; then
            ui_ok=1
            break
        fi
        sleep 1
    done
    if [[ $ui_ok -ne 1 ]]; then
        echo "Web UI health check failed at http://127.0.0.1:$ui_port/. Recent UI logs:" >&2
        sudo journalctl -u "$UI_SERVICE" -n 40 --no-pager >&2 || true
        exit 1
    fi
    echo "Web UI: OK (http://127.0.0.1:$ui_port/)"
else
    echo "Web UI: skipped (curl is not installed)"
fi

printf '\nUpdate successful\nOld commit: %s\nNew commit: %s\n' "$old_commit" "$new_commit"
