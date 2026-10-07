#!/usr/bin/env bash
# Runs on the API host, installed as /usr/local/sbin/schooldecider-deploy: the only command
# CD's SSH key may run (forced command in authorized_keys). Runbook: deploy/README.md
#   deploy <commit sha> <config hash>   pull the commit's image (GHCR token on stdin),
#                                       migrate, restart the API, check /ready
#   rollback <commit sha>               back to the previous image, if the last deploy
#                                       moved the host to <commit sha>
#   publish <sha256>                    replace the data with the snapshot on stdin (a
#                                       pg_dump -Fc of the launch DB); the previous data
#                                       is restored if anything after the backup fails
# The running and previous images are recorded in deploy/release.env, which compose reads.
set -euo pipefail

IMAGE_REPO=ghcr.io/mike2906/school-comparison-api
APP_DIR="${SCHOOLDECIDER_DIR:-$HOME/schooldecider}"
RELEASE_FILE=deploy/release.env
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
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

# Dumps the database to deploy/backups/<kind>-<label>-XXXXXXXX.dump and checks the dump is
# readable, or stops: $3 names what must not start without it. Sets $backup.
take_backup() {
	local kind="$1" label="$2" blocked="$3"
	# exec requires a healthy database even after compose down or host recovery.
	if ! dc up -d --wait --wait-timeout 60 postgres; then
		die "database startup failed; $blocked not started"
	fi

	umask 077
	mkdir -p deploy/backups
	backup="$(mktemp "$APP_DIR/deploy/backups/$kind-$label-XXXXXXXX.dump")"
	if ! dc exec -T postgres pg_dump -U schools -d schools --format=custom > "$backup"; then
		rm -f "$backup"
		die "database backup failed; $blocked not started"
	fi
	if [ ! -s "$backup" ] || ! dc exec -T postgres pg_restore --list < "$backup" >/dev/null; then
		rm -f "$backup"
		die "database backup is empty or unreadable; $blocked not started"
	fi
	sha256sum "$backup" > "$backup.sha256"
	echo "deploy: verified $kind backup at $backup"

	# Keep this verified dump and the six most recent previous script-created dumps of
	# its kind. Never prune on backup failure, and leave manually named archives untouched.
	local -a older_backups
	local old_backup
	mapfile -d '' -t older_backups < <(
		find "$APP_DIR/deploy/backups" -maxdepth 1 -type f -name "$kind-*.dump" \
			! -path "$backup" -printf '%T@ %p\0' | sort -z -nr | cut -z -d' ' -f2-
	)
	for old_backup in "${older_backups[@]:6}"; do
		rm -f -- "$old_backup" "$old_backup.sha256"
	done
}

# Replaces the database contents with a custom-format dump, in one transaction: on any
# error nothing has changed.
load_dump() {
	dc exec -T postgres pg_restore -U schools -d schools --no-owner --clean --if-exists \
		--single-transaction --exit-on-error < "$1"
}

count_schools() {
	dc exec -T postgres psql -U schools -d schools -Atc 'select count(*) from schools' </dev/null 2>/dev/null || echo unknown
}

deploy() {
	local sha="${1:-}" hash="${2:-}" new current previous token=""
	[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "expected a full commit sha"
	[[ "$hash" =~ ^[0-9a-f]{64}$ ]] || die "expected a config hash"
	if [ "$hash" != "$(cat "${CONFIG_FILES[@]}" "$SELF" | sha256sum | cut -d' ' -f1)" ]; then
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

	# Keep a recoverable snapshot before any schema change. A failed/invalid dump stops
	# the release before Alembic runs; code rollback never downgrades the database.
	take_backup pre-migrate "${sha:0:12}" migration

	echo "deploy: migrating with $new"
	API_IMAGE="$new" dc run --rm -T api alembic upgrade head </dev/null

	if [ "$new" != "$current" ]; then
		write_release "$new" "$current"
	else
		# Already running: this run changes nothing, so it leaves nothing to roll back.
		write_release "$new" "$new"
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
	if [ "$previous" = "$current" ]; then
		echo "deploy: $sha was already running before this release; nothing to roll back"
		return 0
	fi
	write_release "$previous" "$previous"
	dc up -d
	wait_ready "$previous" || die "rolled back to $previous, but it is not ready"
	echo "deploy: rolled back to $previous (migrations stay applied)"
}

# Upper bound for a snapshot on stdin; a dump is about 10 MB.
MAX_SNAPSHOT_BYTES=536870912

publish() {
	local sum="${1:-}" current before after
	[[ "$sum" =~ ^[0-9a-f]{64}$ ]] || die "expected the snapshot's sha256"
	current="$(release_value API_IMAGE)"

	umask 077
	mkdir -p deploy/backups
	incoming="$(mktemp "$APP_DIR/deploy/backups/incoming-XXXXXXXX.dump")"
	trap 'rm -f "$incoming"' EXIT
	head -c "$MAX_SNAPSHOT_BYTES" > "$incoming"
	if [ "$(sha256sum "$incoming" | cut -d' ' -f1)" != "$sum" ]; then
		die "the snapshot does not match its checksum; nothing changed"
	fi

	take_backup pre-publish "$(date -u +%Y%m%d-%H%M%S)" publish
	if ! dc exec -T postgres pg_restore --list < "$incoming" >/dev/null; then
		die "the snapshot is not a readable dump; nothing changed"
	fi
	before="$(count_schools)"

	# No requests while the tables are replaced. The image stays the same: the snapshot
	# is migrated to its schema, as on first start.
	dc stop api
	if ! load_dump "$incoming"; then
		dc up -d
		die "the snapshot could not be restored; the previous data is unchanged"
	fi
	if ! dc run --rm -T api alembic upgrade head </dev/null; then
		restore_previous_data "$current" "the snapshot could not be migrated to the running image's schema"
	fi
	dc up -d
	if ! wait_ready "$current"; then
		dc logs --tail 50 api >&2 || true
		dc stop api
		restore_previous_data "$current" "the API was not ready with the snapshot"
	fi
	after="$(count_schools)"
	echo "deploy: published the snapshot; schools: $before -> $after"
}

# After a failed publish: back to the data in $backup, then stop with the reason.
restore_previous_data() {
	if ! load_dump "$backup"; then
		die "$2, and restoring $backup failed: the API is stopped, restore by hand (deploy/README.md)"
	fi
	dc up -d
	wait_ready "$1" || die "$2; the previous data is restored but the API is not ready"
	die "$2; the previous data is restored"
}

if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
	read -r -a args <<< "$SSH_ORIGINAL_COMMAND"
else
	args=("$@")
fi

cd "$APP_DIR"
exec 9> deploy/.deploy.lock
# Runs are serialized by the workflow; wait for one that was cancelled mid-way to finish.
flock -w 300 9 || die "another deploy is still running"
# First run: the image built on the host by hand is both the running one and the fallback.
[ -f "$RELEASE_FILE" ] || write_release schooldecider-api:local schooldecider-api:local

case "${args[0]:-}" in
	deploy) deploy "${args[1]:-}" "${args[2]:-}" ;;
	rollback) rollback "${args[1]:-}" ;;
	publish) publish "${args[1]:-}" ;;
	*) die "usage: deploy <commit sha> <config hash> | rollback <commit sha> | publish <sha256>" ;;
esac
