#!/usr/bin/env bash
# Host firewall. IPv4: the published Caddy ports take 443 only from Cloudflare and 80 from
# nobody; SSH stays open (keys only). IPv6: nothing new comes in, so sshd is not reachable
# there; Caddy's ports are bound to 0.0.0.0 in docker-compose.prod.yml, so they are never
# published over IPv6 (Docker's forwarding is not covered by the IPv6 INPUT rules). Also drops IPv4 TCP
# packets carrying IP options, which nothing legitimate sends and which are the trigger of
# CVE-2026-98432 (a kernel bug reachable through any open TCP port).
# Docker publishes ports through its own iptables rules, ahead of ufw, so the port rules go
# in Docker's DOCKER-USER chain. Idempotent; schooldecider-firewall.service (installed by
# deploy/bootstrap.sh) runs it at boot, before Docker starts, and whenever Docker restarts.
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

# The hardening below is not what keeps the API private (the rules above are), so a failure
# here is logged and the unit still succeeds: Docker requires this unit, and a missing
# nft or IPv6 must not take the API down. Check the journal for "firewall: WARNING".

# IPv4 TCP with IP options (header longer than 20 bytes): dropped before routing, so before
# any socket, published port or container sees them. Own nft table, replaced on every run.
if ! nft -f - <<'NFT'
table ip schooldecider_ipopts
delete table ip schooldecider_ipopts
table ip schooldecider_ipopts {
	chain prerouting {
		type filter hook prerouting priority -300; policy accept;
		ip protocol tcp ip hdrlength != 5 counter drop
	}
}
NFT
then
	echo "firewall: WARNING: could not install the IP-options drop" >&2
fi

# IPv6: only replies, loopback and ICMPv6 (neighbour discovery needs it) come in.
# DROP is added last, so a failure part-way leaves IPv6 as open as before, never half-closed
# for replies.
CHAIN6=SCHOOLDECIDER-IN6
ipv6_rules() {
	# Chained with &&: set -e does not apply inside an `if` condition.
	{ ip6tables -w -N "$CHAIN6" 2>/dev/null || ip6tables -w -F "$CHAIN6"; } \
		&& ip6tables -w -A "$CHAIN6" -i lo -j RETURN \
		&& ip6tables -w -A "$CHAIN6" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN \
		&& ip6tables -w -A "$CHAIN6" -p ipv6-icmp -j RETURN \
		&& ip6tables -w -A "$CHAIN6" -j DROP \
		&& { ip6tables -w -C INPUT -j "$CHAIN6" 2>/dev/null || ip6tables -w -I INPUT -j "$CHAIN6"; }
}
if [ -d /proc/sys/net/ipv6 ] && ! ipv6_rules; then
	echo "firewall: WARNING: could not close IPv6 inbound" >&2
fi

echo "firewall: IPv4 TCP with IP options dropped; IPv6 inbound closed except replies and ICMPv6"
