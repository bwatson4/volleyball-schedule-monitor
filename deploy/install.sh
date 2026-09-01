#!/usr/bin/env bash
# Install or update the volleyball monitor on Raspberry Pi OS Lite.
set -euo pipefail

APP_DIR=/opt/volleyball-schedule-monitor
RUNTIME_DIR=/var/lib/volleyball-schedule-monitor
APP_ENV=/etc/volleyball-schedule-monitor.env
WIFI_ENV=/etc/volleyball-wifi-provision.env
SERVICE_USER=volleyball
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo ./deploy/install.sh" >&2
  exit 1
fi
if [[ $(uname -s) != Linux ]]; then
  echo "This installer supports Raspberry Pi OS/Linux only." >&2
  exit 1
fi
case "$(uname -m)" in
  armv6l|armv7l|armv8l|aarch64) ;;
  *) echo "This installer is intended for ARM Raspberry Pi hardware (got $(uname -m))." >&2; exit 1 ;;
esac
if ! command -v nmcli >/dev/null; then
  echo "NetworkManager/nmcli is required. Enable/install NetworkManager in Raspberry Pi OS before running this installer." >&2
  exit 1
fi
if ! command -v apt-get >/dev/null; then
  echo "This installer requires an apt-based Raspberry Pi OS system." >&2
  exit 1
fi

apt-get update
# Runtime libraries required by lxml (used by caldav). apt-get install is
# idempotent, and these are runtime packages rather than development headers.
apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates libxslt1.1 libxml2

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
install -d -o root -g root -m 0755 "$APP_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$RUNTIME_DIR" "$RUNTIME_DIR/pdfs"

# Copy code only. Runtime state, virtualenv, and protected configuration stay put.
tar --exclude=.git --exclude=.env --exclude=.venv --exclude=venv --exclude=runtime --exclude=state --exclude=data --exclude=logs --exclude=__pycache__ --exclude='*.pdf' -C "$SOURCE_DIR" -cf - . | tar -C "$APP_DIR" -xf -
chown -R root:root "$APP_DIR"
find "$APP_DIR" -type d -exec chmod 0755 {} +
find "$APP_DIR" -type f -exec chmod 0644 {} +
chmod 0755 "$APP_DIR/deploy/install.sh" "$APP_DIR/deploy/force-wifi-setup.sh"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --no-cache-dir -r "$APP_DIR/requirements.txt"

if [[ ! -e "$APP_ENV" ]]; then
  install -o root -g "$SERVICE_USER" -m 0640 "$SOURCE_DIR/examples/volleyball-monitor.env.example" "$APP_ENV"
  echo "Created $APP_ENV from the sanitized example; edit it with your application credentials before using the monitor."
else
  echo "Preserving existing application environment: $APP_ENV"
fi

SETTINGS_FILE="$RUNTIME_DIR/settings.json"
if [[ ! -e "$SETTINGS_FILE" ]]; then
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0640 "$SOURCE_DIR/examples/settings.json.example" "$SETTINGS_FILE"
  echo "Created $SETTINGS_FILE from the sanitized example; update it in the LAN UI."
fi

if [[ ! -e "$WIFI_ENV" ]]; then
  setup_password=${WIFI_SETUP_PASSWORD:-}
  if [[ -z "$setup_password" ]]; then
    read -r -s -p "Choose a Wi-Fi setup AP password (8-63 characters): " setup_password
    echo
    read -r -s -p "Confirm Wi-Fi setup AP password: " setup_password_confirm
    echo
    [[ "$setup_password" == "$setup_password_confirm" ]] || { echo "Passwords did not match." >&2; exit 1; }
  fi
  [[ ${#setup_password} -ge 8 && ${#setup_password} -le 63 ]] || { echo "Setup AP password must be 8-63 characters." >&2; exit 1; }
  umask 077
  printf 'WIFI_SETUP_PASSWORD=%s\nWIFI_BOOT_GRACE_SECONDS=75\nWIFI_INTERFACE=wlan0\nWIFI_PORTAL_PORT=80\n' "$setup_password" > "$WIFI_ENV"
  chown root:root "$WIFI_ENV"
  chmod 0600 "$WIFI_ENV"
  unset setup_password setup_password_confirm
else
  echo "Preserving existing Wi-Fi setup credentials: $WIFI_ENV"
fi

install -o root -g root -m 0644 "$SOURCE_DIR/deploy/volleyball-schedule.service" /etc/systemd/system/volleyball-schedule.service
install -o root -g root -m 0644 "$SOURCE_DIR/deploy/volleyball-schedule.timer" /etc/systemd/system/volleyball-schedule.timer
install -o root -g root -m 0644 "$SOURCE_DIR/deploy/volleyball-ui.service" /etc/systemd/system/volleyball-ui.service
install -o root -g root -m 0644 "$SOURCE_DIR/deploy/volleyball-wifi-provision.service" /etc/systemd/system/volleyball-wifi-provision.service

systemctl daemon-reload
systemctl enable volleyball-schedule.timer volleyball-ui.service volleyball-wifi-provision.service
systemctl start volleyball-wifi-provision.service
echo "Installed. Set application credentials in $APP_ENV, then run:"
echo "  sudo systemctl start volleyball-ui.service volleyball-schedule.timer"
