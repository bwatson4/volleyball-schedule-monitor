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
- Debian/Raspberry Pi OS runtime libraries `libxslt1.1` and `libxml2` (the
  installer installs these automatically for the `lxml`/CalDAV dependency).
- A CalDAV account and an SMTP account with app passwords, if calendar and
  email notifications are enabled.

The runtime uses `pdfminer.six` directly for PDF text extraction. It does not
render PDFs and does not require Chromium, PDFium, `pypdfium2`, or Pillow.
This keeps the dependency set suitable for the original Raspberry Pi Zero W
(armv6l, 512 MB RAM); use a 32-bit Raspberry Pi OS userspace on that device.

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

## Updating the Raspberry Pi

For a normal deployed Pi, update from the Git checkout used during installation:

```bash
cd ~/volleyball-schedule-monitor
./scripts/update-pi.sh
```

Run the updater as the checkout owner, not with `sudo`; it requests sudo only
for the installed code and systemd operations. It fast-forwards from GitHub,
preserves `/var/lib/volleyball-schedule-monitor` and protected environment
files, updates dependencies only when `requirements.txt` changes, refreshes
the systemd definitions, then restarts and verifies the UI and schedule timer.
`deploy/install.sh` remains the clean-machine/bootstrap installer and is only
needed again if the installed virtual environment or runtime directory is
missing.

The SQLite history schema upgrades itself transactionally when the application
opens the database. No database reset or separate migration command is needed
for normal updates; existing schedule revisions, learned team identities,
aliases, diagnostics, and parsed history are retained.

### Repairing learned team identities after an update

Team names from KVA PDFs are preserved verbatim in each schedule revision, but
the history database now keeps a separate canonical identity and learned aliases
for each season.  To backfill an existing season without sending calendar or
email notifications, stop the monitor while repairing its database:

```bash
cd ~/volleyball-schedule-monitor
./scripts/update-pi.sh
sudo systemctl stop volleyball-schedule.timer volleyball-schedule.service volleyball-ui.service

# Preview the aliases that meet the deliberately conservative confidence rules.
sudo -u volleyball env RUNTIME_DIR=/var/lib/volleyball-schedule-monitor \
  /opt/volleyball-schedule-monitor/.venv/bin/python -m src.history \
  repair-team-identities --season 2026-27 --dry-run

# Record the same safe aliases.  This only adds identity metadata; it does not
# rewrite PDF revisions or run the calendar/email pipeline.
sudo -u volleyball env RUNTIME_DIR=/var/lib/volleyball-schedule-monitor \
  /opt/volleyball-schedule-monitor/.venv/bin/python -m src.history \
  repair-team-identities --season 2026-27

# Retained PDFs are reparsed locally to fill complete historic league rosters.
# Preview first; neither form runs calendar, email, or notification work.
sudo -u volleyball env RUNTIME_DIR=/var/lib/volleyball-schedule-monitor \
  /opt/volleyball-schedule-monitor/.venv/bin/python -m src.history \
  backfill-pool-rosters --season 2026-27 --dry-run
sudo -u volleyball env RUNTIME_DIR=/var/lib/volleyball-schedule-monitor \
  /opt/volleyball-schedule-monitor/.venv/bin/python -m src.history \
  backfill-pool-rosters --season 2026-27

# Inspect the currently parsed pool against the immediately preceding logical
# league week. This is read-only and does not send notifications.
sudo -u volleyball env RUNTIME_DIR=/var/lib/volleyball-schedule-monitor \
  SETTINGS_FILE=/var/lib/volleyball-schedule-monitor/settings.json \
  /opt/volleyball-schedule-monitor/.venv/bin/python -m src.history \
  pool-movement --season 2026-27

sudo systemctl start volleyball-ui.service volleyball-schedule.timer
sudo systemctl status volleyball-ui.service volleyball-schedule.timer --no-pager
```

`install.sh` refreshes the virtual environment dependencies safely, so no
separate `pip install` step is normally needed.  To verify the Teams-page
aggregation directly from SQLite, run:

```bash
sqlite3 /var/lib/volleyball-schedule-monitor/history.sqlite3 '
SELECT c.canonical_name, COUNT(DISTINCT g.logical_id) AS weeks_together,
       MIN(g.game_date) AS first_seen
FROM parsed_game g
JOIN parsed_game_team t ON t.content_hash=g.content_hash AND t.logical_id=g.logical_id
JOIN team_alias a ON a.season=g.season AND a.alias_normalized=t.team_normalized
JOIN team_identity c ON c.season=a.season AND c.canonical_normalized=a.canonical_normalized
WHERE g.season="2026-27" AND c.canonical_name="Block Busters"
GROUP BY c.canonical_name;'
```

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
