#!/usr/bin/env bash
# One-time setup of the API host (Ubuntu 26.04), safe to run again. From your machine:
#   scp deploy/bootstrap.sh deploy/firewall.sh deploy/cloudflare-proxies.caddy ubuntu@<ip>:
#   ssh ubuntu@<ip> sudo bash bootstrap.sh
# Installs Docker, turns off SSH password logins, enables unattended security updates
# (Ubuntu's and Docker's, with a night-time reboot when one needs it), refreshes the Caddy
# and Postgres images weekly and installs the firewall (deploy/firewall.sh) as a service.
# Runbook: deploy/README.md
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
	echo "run with sudo" >&2
	exit 1
fi
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
login_user="${SUDO_USER:-ubuntu}"
if ! [ -s "$(getent passwd "$login_user" | cut -d: -f6)/.ssh/authorized_keys" ]; then
	echo "$login_user has no SSH key installed; not disabling password logins" >&2
	exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q ca-certificates curl iptables nftables unattended-upgrades

# Docker Engine and the compose plugin, from Docker's apt repository.
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
	> /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
usermod -aG docker "$login_user"

# Unattended upgrades: Ubuntu's security pocket (the default) plus Docker's repository,
# which the default leaves out. Without a reboot a new kernel or libc is installed but not
# running (the host ran a month-old kernel until 2026-11), so reboot at night when one is
# needed. The API is down for about a minute; Cloudflare serves cached answers meanwhile.
cat > /etc/apt/apt.conf.d/52schooldecider-upgrades <<'CONF'
Unattended-Upgrade::Origins-Pattern {
	"origin=Docker,label=Docker CE,codename=${distro_codename}";
};
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-WithUsers "true";
Unattended-Upgrade::Automatic-Reboot-Time "02:30";
CONF
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF

# SSH: keys only, one login user (CD uses the same one).
cat > /etc/ssh/sshd_config.d/00-schooldecider.conf <<CONF
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers $login_user
MaxAuthTries 3
X11Forwarding no
CONF
sshd -t
systemctl try-reload-or-restart ssh

# Firewall: applied at boot before Docker starts, and again with every Docker restart.
install -m 0755 -d /etc/schooldecider
install -m 0644 "$src/cloudflare-proxies.caddy" /etc/schooldecider/cloudflare-proxies.caddy
install -m 0755 "$src/firewall.sh" /usr/local/sbin/schooldecider-firewall
cat > /etc/systemd/system/schooldecider-firewall.service <<'UNIT'
[Unit]
Description=Limit the published API ports to Cloudflare
Wants=network-online.target
After=network-online.target
Before=docker.service
PartOf=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/schooldecider-firewall

# RequiredBy: if the rules cannot be applied, Docker does not start (closed, not open).
[Install]
WantedBy=multi-user.target
RequiredBy=docker.service
UNIT

# Caddy and Postgres use moving tags (caddy:2-alpine, postgres:16-alpine) that CD never
# pulls: fetch their patch releases weekly. Same major versions, so no data migration;
# Postgres restarts only when its image changed.
app_dir="$(getent passwd "$login_user" | cut -d: -f6)/schooldecider"
cat > /etc/systemd/system/schooldecider-image-refresh.service <<UNIT
[Unit]
Description=Pull patch releases of the Caddy and Postgres images
Wants=network-online.target docker.service
After=network-online.target docker.service

[Service]
Type=oneshot
User=$login_user
WorkingDirectory=$app_dir
ExecStart=/usr/bin/docker compose --env-file deploy/.env --env-file deploy/release.env -f docker-compose.prod.yml pull caddy postgres
ExecStart=/usr/bin/docker compose --env-file deploy/.env --env-file deploy/release.env -f docker-compose.prod.yml up -d --no-deps caddy postgres
ExecStart=/usr/bin/docker image prune -f
UNIT
cat > /etc/systemd/system/schooldecider-image-refresh.timer <<'UNIT'
[Unit]
Description=Weekly refresh of the Caddy and Postgres images

[Timer]
OnCalendar=Sun 03:30
RandomizedDelaySec=15m
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl reenable schooldecider-firewall.service
systemctl restart schooldecider-firewall.service
systemctl enable --now schooldecider-image-refresh.timer

echo "bootstrap done; log in again for the docker group to apply to $login_user"
