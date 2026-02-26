#!/usr/bin/env bash
set -euo pipefail

# Run the recommended scraping workflow for one school:
# 1) validate URL
# 2) navigate website (with automatic challenge-aware fallbacks)
# 3) extract structured data
#
# Usage:
#   cd backend
#   scripts/run_school_workflow.sh --school-id 182

SCHOOL_ID=""
COUNTRY="bg"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --school-id)
      SCHOOL_ID="${2:-}"
      shift 2
      ;;
    --country)
      COUNTRY="${2:-bg}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$SCHOOL_ID" ]]; then
  echo "Missing required --school-id" >&2
  exit 2
fi

echo "Running workflow for school $SCHOOL_ID (country=$COUNTRY)"

uv run python -m app.scrapers.cli run --school-id "$SCHOOL_ID" --stage validate-urls --country "$COUNTRY" --sync
uv run python -m app.scrapers.cli run --school-id "$SCHOOL_ID" --stage navigate --country "$COUNTRY" --sync
uv run python -m app.scrapers.cli run --school-id "$SCHOOL_ID" --stage extract --country "$COUNTRY" --sync

echo "Workflow complete for school $SCHOOL_ID"
