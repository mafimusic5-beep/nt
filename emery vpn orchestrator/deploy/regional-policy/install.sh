#!/usr/bin/env bash
set -euo pipefail

[[ "$EUID" -eq 0 ]] || { echo 'Run as root on a VPN node.' >&2; exit 1; }
test -x /usr/local/bin/xray
command -v python3 >/dev/null
policy_source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

getent group emery-regional-xray >/dev/null || groupadd --system emery-regional-xray
id emery-regional-xray >/dev/null 2>&1 || useradd --system --no-create-home \
  --gid emery-regional-xray --shell /usr/sbin/nologin emery-regional-xray
install -d -m 755 -o root -g root /var/lib/emery-regional-policy /opt/emery/regional-policy
if [[ ! -d /etc/emery ]]; then
  install -d -m 750 -o root -g root /etc/emery
fi

# Retain replaced deployment files; do not touch the primary Xray configuration.
policy_backup_dir="$(mktemp -d /var/lib/emery-regional-policy/install-backup-XXXXXXXX)"
for policy_file in regional_policy.py emery-regional-xray.service emery-regional-policy-update.service emery-regional-policy-update.timer; do
  if [[ "$policy_file" == regional_policy.py ]]; then
    policy_destination="/opt/emery/regional-policy/$policy_file"
  else
    policy_destination="/etc/systemd/system/$policy_file"
  fi
  if [[ -f "$policy_destination" ]]; then
    cp -a -- "$policy_destination" "$policy_backup_dir/$policy_file"
  fi
  install -m 644 -o root -g root "$policy_source_dir/$policy_file" "$policy_destination"
done
if [[ ! -e /etc/emery/regional-policy.env ]]; then
  install -m 640 -o root -g root "$policy_source_dir/regional-policy.env.example" /etc/emery/regional-policy.env
fi
systemctl daemon-reload
systemctl enable emery-regional-xray.service
echo "Installed only. Review /etc/emery/regional-policy.env, then run:"
echo "systemctl start emery-regional-policy-update.service"
echo "systemctl enable --now emery-regional-policy-update.timer"
echo "Deployment backup: $policy_backup_dir"
