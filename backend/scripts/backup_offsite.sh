#!/usr/bin/env bash
# Copy the launch database and backend/reports/ to a private Cloudflare R2 bucket.
#
# Both exist only on the machine that runs the pipeline; production holds a published
# snapshot of this database and nothing else. Run after each data publish:
#
#     backend/scripts/backup_offsite.sh
#
# Credentials come from ~/.config/schooldecider/backup.env (mode 600), an R2 API token
# limited to Object Read & Write on the one bucket:
#
#     export R2_ACCOUNT_ID=...
#     export R2_ACCESS_KEY_ID=...
#     export R2_SECRET_ACCESS_KEY=...
#
# Optional: R2_BUCKET (default schooldecider-backups), R2_ENDPOINT (a bucket in the EU
# jurisdiction uses https://<account id>.eu.r2.cloudflarestorage.com), BACKUP_DATABASE
# (default sofia_schools), BACKUP_DRY_RUN=1 to build and check the archives without
# uploading. Nothing is pruned: a dump is about 10 MB and R2's free tier holds 10 GB.
# Setup and the restore check: deploy/README.md, Off-host backup.
set -euo pipefail

die() {
	echo "backup: $*" >&2
	exit 1
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
env_file="${BACKUP_ENV_FILE:-$HOME/.config/schooldecider/backup.env}"
database="${BACKUP_DATABASE:-sofia_schools}"
container="${BACKUP_DB_CONTAINER:-sofia_schools_db}"
dry_run="${BACKUP_DRY_RUN:-}"

if [ -z "$dry_run" ]; then
	[ -r "$env_file" ] || die "$env_file not found (see deploy/README.md, Off-host backup)"
	# shellcheck disable=SC1090
	. "$env_file"
	: "${R2_ACCOUNT_ID:?}" "${R2_ACCESS_KEY_ID:?}" "${R2_SECRET_ACCESS_KEY:?}"
fi
bucket="${R2_BUCKET:-schooldecider-backups}"
endpoint="${R2_ENDPOINT:-https://${R2_ACCOUNT_ID:-}.r2.cloudflarestorage.com}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
stamp="$(date -u +%Y%m%d-%H%M%S)"
dump="$work/$database-$stamp.dump"
reports="$work/reports-$stamp.tar.gz"

docker exec "$container" pg_dump -U postgres -d "$database" --format=custom > "$dump" \
	|| die "pg_dump of $database failed"
[ -s "$dump" ] && docker exec -i "$container" pg_restore --list < "$dump" > /dev/null \
	|| die "the dump is empty or unreadable"

# Older database dumps kept under reports/ are left out: this run's dump supersedes them.
tar -czf "$reports" -C "$repo_root/backend" --exclude='*.dump' reports \
	|| die "could not archive backend/reports"

for file in "$dump" "$reports"; do
	name="$(basename "$file")"
	size="$(stat -c %s "$file")"
	if [ -n "$dry_run" ]; then
		echo "backup: dry run, would upload $name ($size bytes)"
		continue
	fi
	url="$endpoint/$bucket/$name"
	curl --fail --silent --show-error --aws-sigv4 "aws:amz:auto:s3" \
		--user "$R2_ACCESS_KEY_ID:$R2_SECRET_ACCESS_KEY" --upload-file "$file" "$url" \
		|| die "upload of $name failed"
	stored="$(curl --fail --silent --show-error --head --aws-sigv4 "aws:amz:auto:s3" \
		--user "$R2_ACCESS_KEY_ID:$R2_SECRET_ACCESS_KEY" "$url" \
		| tr -d '\r' | awk 'tolower($1) == "content-length:" { print $2 }')"
	[ "$stored" = "$size" ] || die "$name: the bucket holds ${stored:-nothing}, expected $size bytes"
	echo "backup: stored $name ($size bytes) in $bucket"
done
