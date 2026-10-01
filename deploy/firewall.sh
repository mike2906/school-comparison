#!/usr/bin/env bash
# Host firewall for the published Caddy ports: 443 only from Cloudflare, 80 from nobody.
# Docker publishes ports through its own iptables rules, ahead of ufw, so the rules go
# in Docker's DOCKER-USER chain. Idempotent; schooldecider-firewall.service (installed by
# deploy/bootstrap.sh) runs it at boot, before Docker starts, and whenever Docker restarts. IPv4 only: Caddy's
# ports are bound to 0.0.0.0 in docker-compose.prod.yml, so nothing is published over IPv6.
set -euo pipefail

RANGES_FILE="${1:-/etc/schooldecider/cloudflare-proxies.caddy}"
CHAIN=SCHOOLDECIDER-ORIGIN

mapfile -t ranges < <(grep -v '^#' "$RANGES_FILE" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}')
if [ "${#ranges[@]}" -lt 10 ]; then
	echo "firewall: only ${#ranges[@]} Cloudflare ranges in $RANGES_FILE, refusing to apply" >&2
	exit 1
fi
iface="$(ip -4 route show default | awk '{print $5; exit}')"
if [ -z "$iface" ]; then
	echo "firewall: no default IPv4 route" >&2
	exit 1
fi

# DROP goes in first and the ranges are inserted above it, so a failure part-way leaves
# the port closed rather than open.
iptables -w -N "$CHAIN" 2>/dev/null || iptables -w -F "$CHAIN"
iptables -w -A "$CHAIN" -j DROP
for range in "${ranges[@]}"; do
	iptables -w -I "$CHAIN" 1 -s "$range" -j RETURN
done

# DOCKER-USER sees packets after DNAT, so match the port the client connected to.
# New inbound connections only: replies to the containers' own outbound traffic pass.
# Docker keeps an existing DOCKER-USER chain, so the rules can be in place before it starts.
iptables -w -N DOCKER-USER 2>/dev/null || true
insert_once() {
	iptables -w -C DOCKER-USER "$@" 2>/dev/null || iptables -w -I DOCKER-USER "$@"
}
insert_once -i "$iface" -p tcp -m conntrack --ctorigdstport 443 --ctdir ORIGINAL -j "$CHAIN"
insert_once -i "$iface" -p tcp -m conntrack --ctorigdstport 80 --ctdir ORIGINAL -j DROP

echo "firewall: 443 limited to ${#ranges[@]} Cloudflare ranges on $iface; 80 closed"
