#!/usr/bin/env bash
set -Eeuo pipefail

: "${NODE_IP:?NODE_IP is required}"

assignment_port_start="${ASSIGNMENT_PORT_START:-20000}"
assignment_port_end="${ASSIGNMENT_PORT_END:-20199}"
gate_port="${GATE_PORT:-8447}"

[[ "$NODE_IP" =~ ^[0-9]+(\.[0-9]+){3}$ ]]
[[ "$assignment_port_start" =~ ^[0-9]+$ ]]
[[ "$assignment_port_end" =~ ^[0-9]+$ ]]
[[ "$gate_port" =~ ^[0-9]+$ ]]
(( assignment_port_start >= 1024 && assignment_port_end <= 65535 ))
(( assignment_port_start <= assignment_port_end ))
hostname -I | tr ' ' '\n' | grep -Fxq "$NODE_IP"

for required_command in iptables iptables-save systemctl ss; do
  command -v "$required_command" >/dev/null
done
systemctl is-active --quiet emery-control-tunnel.service
systemctl is-active --quiet emery-device-gate.service
ss -lntH "sport = :$gate_port" | grep -q LISTEN

xray_unit=''
for candidate in xray.service xray-manual.service; do
  if systemctl is-active --quiet "$candidate"; then
    xray_unit="$candidate"
    break
  fi
done
[[ -n "$xray_unit" ]]

backup="/root/emery-assignment-firewall-backup-$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$backup"
iptables-save > "$backup/iptables.v4"
if command -v ip6tables-save >/dev/null; then
  ip6tables-save > "$backup/iptables.v6"
fi

unit_file='/etc/systemd/system/emery-assignment-firewall.service'
if [[ -e "$unit_file" ]]; then
  cp -a "$unit_file" "$backup/"
fi

cat > "$unit_file" <<UNIT
[Unit]
Description=Keep Emery per-device VLESS assignment ports private
After=network.target
Before=xray.service xray-manual.service emery-device-gate.service

[Service]
Type=oneshot
ExecStart=/bin/sh -ec '/usr/sbin/iptables -C INPUT -i lo -p tcp --dport $assignment_port_start:$assignment_port_end -m comment --comment EMERY_ASSIGNMENT_LOOPBACK -j ACCEPT 2>/dev/null || /usr/sbin/iptables -I INPUT 1 -i lo -p tcp --dport $assignment_port_start:$assignment_port_end -m comment --comment EMERY_ASSIGNMENT_LOOPBACK -j ACCEPT'
ExecStart=/bin/sh -ec '/usr/sbin/iptables -C INPUT -p tcp --dport $assignment_port_start:$assignment_port_end -m comment --comment EMERY_ASSIGNMENT_PRIVATE -j DROP 2>/dev/null || /usr/sbin/iptables -I INPUT 2 -p tcp --dport $assignment_port_start:$assignment_port_end -m comment --comment EMERY_ASSIGNMENT_PRIVATE -j DROP'
ExecStart=/bin/sh -ec 'if [ -x /usr/sbin/ip6tables ]; then /usr/sbin/ip6tables -C INPUT -i lo -p tcp --dport $assignment_port_start:$assignment_port_end -m comment --comment EMERY_ASSIGNMENT_LOOPBACK -j ACCEPT 2>/dev/null || /usr/sbin/ip6tables -I INPUT 1 -i lo -p tcp --dport $assignment_port_start:$assignment_port_end -m comment --comment EMERY_ASSIGNMENT_LOOPBACK -j ACCEPT; /usr/sbin/ip6tables -C INPUT -p tcp --dport $assignment_port_start:$assignment_port_end -m comment --comment EMERY_ASSIGNMENT_PRIVATE -j DROP 2>/dev/null || /usr/sbin/ip6tables -I INPUT 2 -p tcp --dport $assignment_port_start:$assignment_port_end -m comment --comment EMERY_ASSIGNMENT_PRIVATE -j DROP; fi'
ExecStop=/bin/sh -ec '/usr/sbin/iptables -D INPUT -p tcp --dport $assignment_port_start:$assignment_port_end -m comment --comment EMERY_ASSIGNMENT_PRIVATE -j DROP 2>/dev/null || true; /usr/sbin/iptables -D INPUT -i lo -p tcp --dport $assignment_port_start:$assignment_port_end -m comment --comment EMERY_ASSIGNMENT_LOOPBACK -j ACCEPT 2>/dev/null || true'
ExecStop=/bin/sh -ec 'if [ -x /usr/sbin/ip6tables ]; then /usr/sbin/ip6tables -D INPUT -p tcp --dport $assignment_port_start:$assignment_port_end -m comment --comment EMERY_ASSIGNMENT_PRIVATE -j DROP 2>/dev/null || true; /usr/sbin/ip6tables -D INPUT -i lo -p tcp --dport $assignment_port_start:$assignment_port_end -m comment --comment EMERY_ASSIGNMENT_LOOPBACK -j ACCEPT 2>/dev/null || true; fi'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT

chmod 644 "$unit_file"
systemctl daemon-reload
systemctl enable emery-assignment-firewall.service >/dev/null
systemctl restart emery-assignment-firewall.service

systemctl is-active --quiet emery-assignment-firewall.service
iptables -C INPUT -i lo -p tcp --dport "$assignment_port_start:$assignment_port_end" -m comment --comment EMERY_ASSIGNMENT_LOOPBACK -j ACCEPT
iptables -C INPUT -p tcp --dport "$assignment_port_start:$assignment_port_end" -m comment --comment EMERY_ASSIGNMENT_PRIVATE -j DROP
if command -v ip6tables >/dev/null; then
  ip6tables -C INPUT -i lo -p tcp --dport "$assignment_port_start:$assignment_port_end" -m comment --comment EMERY_ASSIGNMENT_LOOPBACK -j ACCEPT
  ip6tables -C INPUT -p tcp --dport "$assignment_port_start:$assignment_port_end" -m comment --comment EMERY_ASSIGNMENT_PRIVATE -j DROP
fi
systemctl is-active --quiet "$xray_unit"
systemctl is-active --quiet emery-control-tunnel.service
systemctl is-active --quiet emery-device-gate.service
ss -lntH "sport = :$gate_port" | grep -q LISTEN

echo "NODE_FIREWALL_READY: public TCP $assignment_port_start-$assignment_port_end blocked; loopback allowed; gate $gate_port and $xray_unit active; backup=$backup"
