# Pi archive snapshot

All 21 PDFs retained in `/var/lib/volleyball-schedule-monitor/pdfs` were exported
read-only on October 13, 2026 and imported by the user. `metadata.json` contains
the inventory/checksums, non-secret parser settings, revision/game/pool records
and operational candidate metadata from that export. No credentials or email
recipient configuration are included.

The imported checksums, byte counts and hash filenames were verified for every
file. The October 12 18:20 Vancouver failed candidate exactly matches
`../pdfs/publisher-135.pdf`. Tests replay every PDF in isolated SQLite/state with
calendar/email doubles and blocked networking. These fixtures must never be
replayed through the production runner or imported into a production database.
