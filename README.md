# Volleyball Schedule Monitor

A lightweight, headless monitor for a volleyball schedule webpage. It detects
schedule PDF updates, parses a configured team and gyms, updates a CalDAV
calendar, and emails the configured recipients. A small LAN-only UI manages
the non-secret team settings. It also supports a Raspberry Pi fallback Wi-Fi
setup hotspot when NetworkManager is available.

No browser, desktop environment, Chromium, Docker, or Node.js is needed on
the Pi.

## Hardware and requirements

- Raspberry Pi Zero W, Zero 2 W, or another compatible Raspberry Pi/Linux
  system. Raspberry Pi OS Lite 32-bit is recommended for Zero W compatibility.
- Python 3 with `venv`, Internet access for schedule/calendar/email work, and
  NetworkManager (`nmcli`) when using Wi-Fi provisioning.
- A CalDAV account and an SMTP account with app passwords, if calendar and
  email notifications are enabled.

## Configuration and runtime data

`examples/volleyball-monitor.env.example` is the protected configuration
template. It contains the schedule URL and calendar/mail credentials; copy it
to `/etc/volleyball-schedule-monitor.env` on a Pi (or `.env` for local
development) and replace every placeholder.

`examples/settings.json.example` contains the non-secret team-name aliases,
schedule match text (for example, `Wednesday`), gyms, and notification
recipients. The installer creates its working copy at
`/var/lib/volleyball-schedule-monitor/settings.json`; update it through the UI.

The monitor discovers schedule-like links dynamically from the public KVA
adult-indoor page (`https://kvapack.ca/adult-indoor/`). It validates candidate
documents and selects the PDF whose extracted text matches the configured
schedule match text; no PDF filename or direct URL is hard-coded.

Runtime state, downloaded PDFs, and settings live under
`/var/lib/volleyball-schedule-monitor`. Logs go to journald. None of those
files belong in Git.

## Installation on Raspberry Pi OS Lite

1. Flash Raspberry Pi OS Lite (32-bit), create an initial user, enable SSH,
   and ensure NetworkManager is enabled (`nmcli` works).
2. On the Pi, install Git if needed, then clone your public repository:

   ```bash
   git clone https://github.com/OWNER/volleyball-schedule-monitor.git
   cd volleyball-schedule-monitor
   sudo ./deploy/install.sh
   ```

3. The installer creates a `volleyball` service account, virtual environment,
   runtime directories, systemd units, protected environment templates, and a
   prompted Wi-Fi setup password. Edit the protected application settings
   before starting the monitor:

   ```bash
   sudoedit /etc/volleyball-schedule-monitor.env
   sudo systemctl start volleyball-ui.service volleyball-schedule.timer
   sudo systemctl start volleyball-schedule.service  # optional immediate scan
   ```

Rerunning the installer is safe: it refreshes code and units while preserving
the protected environment files and existing settings.

## Local UI and services

Open `http://<pi-hostname>.local:8080` from a trusted LAN device to set team
aliases, the schedule match text, gyms, and recipient addresses. The UI binds to all interfaces by default
for LAN use; do not expose this port to the public Internet, forward it, or
put it behind a public tunnel.

- `volleyball-schedule.timer` runs the monitor every 30 minutes.
- `volleyball-ui.service` runs the local configuration UI.
- `volleyball-wifi-provision.service` starts a temporary `VolleyballPi-Setup`
  hotspot only when no known Wi-Fi network connects after the grace period.

Useful checks:

```bash
systemctl status volleyball-wifi-provision.service volleyball-ui.service
systemctl list-timers volleyball-schedule.timer
journalctl -u volleyball-schedule.service -n 100 --no-pager
journalctl -u volleyball-wifi-provision.service -n 150 --no-pager
```

To force the setup hotspot from SSH, run
`sudo ./deploy/force-wifi-setup.sh`; run it with `--normal` afterwards to
restore normal boot behavior.

## Updates

```bash
cd volleyball-schedule-monitor
git pull
sudo ./deploy/install.sh
sudo systemctl restart volleyball-ui.service
```

The installer never overwrites protected environment files or runtime data.

## Security

Never commit `.env`, `/etc/volleyball-schedule-monitor.env`, Wi-Fi passwords,
or the contents of the runtime directory. The provisioning page and management
UI are for a trusted LAN only. Existing NetworkManager profiles are preserved;
the installer does not need a personal username, SSH key, or GitHub credential.

## Local development

Copy the two example files to local runtime/config locations, substitute test
credentials, then install development dependencies:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

On Windows, use the equivalent `.venv\\Scripts\\python.exe` commands.
