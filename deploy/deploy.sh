#!/usr/bin/env bash
# Runs on the API host, installed as /usr/local/sbin/schooldecider-deploy: the only command
# CD's SSH key may run (forced command in authorized_keys). Runbook: deploy/README.md
#   deploy <commit sha> <config hash>   pull the commit's image (GHCR token on stdin),
#                                       migrate, restart the API, check /ready
#   rollback <commit sha>               back to the previous image, if <commit sha> is running
# The running and previous images are recorded in deploy/release.env, which compose reads.
set -euo pipefail

IMAGE_REPO=ghcr.io/mike2906/school-comparison-api
APP_DIR="${SCHOOLDECIDER_DIR:-$HOME/schooldecider}"
RELEASE_FILE=deploy/release.env
# Files CD does not change. A commit that changes one is refused until the host is synced.
CONFIG_FILES=(docker-compose.prod.yml deploy/Caddyfile deploy/cloudflare-proxies.caddy)

die() {
	echo "deploy: $*" >&2
	exit 1
}

dc() {
	docker compose --env-file deploy/.env --env-file "$RELEASE_FILE" -f docker-compose.prod.yml "$@"
}

release_value() {
	sed -n "s/^$1=//p" "$RELEASE_FILE" | tail -n 1
}

write_release() {
	printf 'API_IMAGE=%s\nPREVIOUS_API_IMAGE=%s\n' "$1" "$2" > "$RELEASE_FILE.tmp"
	mv "$RELEASE_FILE.tmp" "$RELEASE_FILE"
}

# True once the API container runs image $1 and answers /ready (up to a minute).
wait_ready() {
	local container
	for _ in $(seq 30); do
		container="$(dc ps -q api 2>/dev/null || true)"
		if [ -n "$container" ] \
			&& [ "$(docker inspect --format '{{.Config.Image}}' "$container" 2>/dev/null)" = "$1" ] \
			&& dc exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=5)" </dev/null >/dev/null 2>&1; then
			return 0
		fi
		sleep "${READY_POLL_SECONDS:-2}"
	done
	return 1
}

# Removes pulled release images except the running one and the rollback target.
remove_old_images() {
	local ref
	docker image ls --format '{{.Repository}}:{{.Tag}}' "$IMAGE_REPO" | while IFS= read -r ref; do
		if [ "$ref" != "$1" ] && [ "$ref" != "$2" ]; then
			docker image rm "$ref" >/dev/null || true
		fi
	done
}

deploy() {
	local sha="${1:-}" hash="${2:-}" new current previous token=""
	[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "expected a full commit sha"
	[[ "$hash" =~ ^[0-9a-f]{64}$ ]] || die "expected a config hash"
	if [ "$hash" != "$(cat "${CONFIG_FILES[@]}" "$0" | sha256sum | cut -d' ' -f1)" ]; then
		die "the compose file, Caddy files or this script differ from the commit; sync the host first (deploy/README.md)"
	fi

	new="$IMAGE_REPO:$sha"
	current="$(release_value API_IMAGE)"
	previous="$(release_value PREVIOUS_API_IMAGE)"

	if ! docker image inspect "$new" >/dev/null 2>&1; then
		IFS= read -r token || true
		[ -n "$token" ] || die "no registry token on stdin"
		# A throwaway Docker config: the token is never stored on the host.
		docker_config="$(mktemp -d)"
		trap 'rm -rf "$docker_config"' EXIT
		printf '%s' "$token" | docker --config "$docker_config" login ghcr.io -u x-access-token --password-stdin >/dev/null
		docker --config "$docker_config" pull -q "$new"
		rm -rf "$docker_config"
	fi

	echo "deploy: migrating with $new"
	API_IMAGE="$new" dc run --rm -T api alembic upgrade head </dev/null

	if [ "$new" != "$current" ]; then
		write_release "$new" "$current"
	fi
	dc up -d
	if ! wait_ready "$new"; then
		dc logs --tail 50 api >&2 || true
		if [ "$new" != "$current" ]; then
			write_release "$current" "$previous"
			dc up -d
			wait_ready "$current" || die "$new was not ready, and neither is $current after rolling back"
			die "$new was not ready; rolled back to $current (migrations stay applied)"
		fi
		die "$new is not ready"
	fi

	remove_old_images "$new" "$(release_value PREVIOUS_API_IMAGE)"
	echo "deploy: running $new"
}

rollback() {
	local sha="${1:-}" current previous
	[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "expected a full commit sha"
	current="$(release_value API_IMAGE)"
	previous="$(release_value PREVIOUS_API_IMAGE)"
	if [ "$current" != "$IMAGE_REPO:$sha" ]; then
		echo "deploy: running $current, not $sha; nothing to roll back"
		return 0
	fi
	[ "$previous" != "$current" ] || die "no previous image recorded"
	write_release "$previous" "$previous"
	dc up -d
	wait_ready "$previous" || die "rolled back to $previous, but it is not ready"
	echo "deploy: rolled back to $previous (migrations stay applied)"
}

if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
	read -r -a args <<< "$SSH_ORIGINAL_COMMAND"
else
	args=("$@")
fi

cd "$APP_DIR"
exec 9> deploy/.deploy.lock
flock -n 9 || die "another deploy is running"
# First run: the image built on the host by hand is both the running one and the fallback.
[ -f "$RELEASE_FILE" ] || write_release schooldecider-api:local schooldecider-api:local

case "${args[0]:-}" in
	deploy) deploy "${args[1]:-}" "${args[2]:-}" ;;
	rollback) rollback "${args[1]:-}" ;;
	*) die "usage: deploy <commit sha> <config hash> | rollback <commit sha>" ;;
esac
