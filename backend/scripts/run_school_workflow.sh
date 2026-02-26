#!/usr/bin/env bash
set -euo pipefail

# Run the recommended scraping workflow for one school:
# 1) validate URL
# 2) navigate website (with automatic challenge-aware fallbacks)
# 3) extract structured data
#
# Optional: run Lite-only extractor benchmark for that school.
#
# Usage:
#   cd backend
#   scripts/run_school_workflow.sh --school-id 182
#   scripts/run_school_workflow.sh --school-id 182 --benchmark-lite

SCHOOL_ID=""
COUNTRY="bg"
BENCHMARK_LITE=0

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
    --benchmark-lite)
      BENCHMARK_LITE=1
      shift 1
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

# Recommended defaults from current benchmark:
# - Fetch with httpx first; navigator auto-fallback handles anti-bot cases.
# - Extract page text with bs4.
export NAV_FETCH_ENGINE="${NAV_FETCH_ENGINE:-httpx}"
export NAV_CONTENT_EXTRACTOR="${NAV_CONTENT_EXTRACTOR:-bs4}"

echo "Running workflow for school $SCHOOL_ID (country=$COUNTRY)"
echo "NAV_FETCH_ENGINE=$NAV_FETCH_ENGINE NAV_CONTENT_EXTRACTOR=$NAV_CONTENT_EXTRACTOR"

uv run python -m app.scrapers.cli run --school-id "$SCHOOL_ID" --stage validate-urls --country "$COUNTRY" --sync
uv run python -m app.scrapers.cli run --school-id "$SCHOOL_ID" --stage navigate --country "$COUNTRY" --sync
uv run python -m app.scrapers.cli run --school-id "$SCHOOL_ID" --stage extract --country "$COUNTRY" --sync

if [[ "$BENCHMARK_LITE" -eq 1 ]]; then
  echo "Running Lite-only benchmark for school $SCHOOL_ID"
  MODEL_TIER_CHEAP='openrouter/google/gemini-2.5-flash-lite' \
  MODEL_TIER_MEDIUM='openrouter/google/gemini-2.5-flash-lite' \
  MODEL_TIER_CAPABLE='openrouter/google/gemini-2.5-flash-lite' \
  EXTRACTION_PRIMARY_TIER='cheap' \
  EXTRACTION_ENABLE_CAPABLE_FALLBACK='false' \
  uv run python scripts/compare_content_extractors.py \
    --school-ids "$SCHOOL_ID" \
    --fetch-engines httpx,crawl4ai \
    --modes bs4,trafilatura,crawl4ai \
    --school-timeout-seconds 420 \
    --output "reports/extractor_ab_school${SCHOOL_ID}.json"
fi

echo "Workflow complete for school $SCHOOL_ID"
