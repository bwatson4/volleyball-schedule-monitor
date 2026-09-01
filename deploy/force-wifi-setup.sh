#!/usr/bin/env bash
# Safely force or clear forced fallback mode after NetworkManager recovery.
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then echo "Run with sudo." >&2; exit 1; fi
ENV_FILE=/etc/volleyball-wifi-provision.env
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE; run install.sh first." >&2; exit 1; }
sed -i '/^WIFI_FORCE_SETUP=/d' "$ENV_FILE"
if [[ ${1:-force} == --normal ]]; then
  systemctl restart volleyball-wifi-provision.service
  echo "Forced setup mode cleared; normal boot behavior restored."
  exit 0
fi
printf 'WIFI_FORCE_SETUP=1\n' >> "$ENV_FILE"
systemctl restart volleyball-wifi-provision.service
echo "Forced setup AP requested. Run '$0 --normal' when finished."
