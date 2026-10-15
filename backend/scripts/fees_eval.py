"""Fees eval: run the fees agent's reader on saved pages and score it against the blind key.

The pages are what the agent's finder found (``fees-agent --save-pages evals/fees/pages``);
the key is the R6 step 3 and step 4 answer keys in ``docs/accuracy`` (judged site fees where
the judges ran, the blind key otherwise), plus the targeted keys in ``docs/accuracy/cases``. Saved pages keep the score from drifting as sites
change, so a change in the score is a change in the prompt, schema, checks or model.

What it covers: the reader, ``check_fee`` and ``_build_rows``, and the publish gate on the
rows. Not the finder (it needs live sites), the re-read (it needs the school's stored rows)
or the keep rules. A row counts as:

- right: amount, category and period match a key fee;
- blank: amount and category match, the row has no period and the key fee has one;
- wrong: anything else, a row at an amount the key does not have included;
- duplicate: a second row on a key fee already matched. It counts against the wrong rate:
  a fee published twice is an error a parent sees.

A key fee is found when a row, or one of a row's payment options, matches its amount and
category. Amounts are compared in euro (BGN at 1.95583) within 1 %.

    uv run python scripts/fees_eval.py                       # one trial, report + baseline check
    uv run python scripts/fees_eval.py --trials 3            # repeat, scores averaged
    uv run python scripts/fees_eval.py --write-baseline      # store this run as the baseline (use --trials 5)
    MODEL_TIER_PRICING=openrouter/... uv run python scripts/fees_eval.py --no-check   # try a model

Calls the model directly (no DB, no ledger); ``--max-usd`` stops the run. Exit code 1 when the
score falls past the margins below the baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.ai.client import extract_provider_cost_usd, get_model, get_model_costs  # noqa: E402
from app.models import School  # noqa: E402
from app.scrapers.extraction.agent_fees import check_fee  # noqa: E402
from app.scrapers.stages.fees_agent import (  # noqa: E402
    READ_OUTPUT_TOKENS_RESERVE,
    PageRead,
    Spend,
    _build_rows,
    _new_source_page,
    _page_for,
    reader_agent,
    reader_message,
)
from app.utils import display_gating  # noqa: E402
from app.utils.display_gating import pricing_row_is_publishable  # noqa: E402

EVAL_DIR = BACKEND / "evals" / "fees"
PAGES_DIR = EVAL_DIR / "pages"
BASELINE = EVAL_DIR / "baseline.json"
ACCURACY = BACKEND.parent / "docs" / "accuracy"
# step3 and step4 are blind keys; cases holds keys written for one school's known problem (596's extras).
KEY_SETS = ("step3", "step4", "cases")
BGN_PER_EUR = Decimal("1.95583")
TOLERANCE = Decimal("0.01")
READ_TIMEOUT_SECONDS = 240  # as the stage's read
# Noise, measured 2026-10-15 over 16 trials of the same pages: one trial's wrong rate ran 6 to
# 19 % and its found rate 35 to 55 % (OpenRouter routes one model to several providers). CI
# averages 3 trials against a 6-trial baseline, so these margins are about two standard
# deviations of that difference: the check catches a large regression, not a small one.
MAX_WRONG_RISE = 0.06
MAX_FOUND_DROP = 0.09


def load_key() -> dict[int, list[dict[str, Any]]]:
    """Each keyed school's site fees; blocked sites are left out."""
    key: dict[int, list[dict[str, Any]]] = {}
    for name in KEY_SETS:
        for path in sorted((ACCURACY / name / "key").glob("*.json")):
            blind = json.loads(path.read_text(encoding="utf-8"))
            if blind.get("blocked"):
                continue
            judged = ACCURACY / name / "adj" / path.name
            fees = (
                json.loads(judged.read_text(encoding="utf-8"))["site_fees"] if judged.exists() else blind["site_fees"]
            )
            key[blind["school_id"]] = fees
    return key


def _euro(amount: Any, currency: Any) -> Decimal | None:
    if amount is None:
        return None
    value = Decimal(str(amount))
    return value / BGN_PER_EUR if str(currency or "EUR").upper() == "BGN" else value


def _same(a: Decimal | None, b: Decimal | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= TOLERANCE * max(abs(a), abs(b))


def _value(field: Any) -> Any:
    return getattr(field, "value", field)


def _row_matches(row: Any, fee: dict[str, Any]) -> bool:
    """Same amount; a range only matches the same range, either way round."""
    if row.amount is not None:
        return _same(_euro(row.amount, row.currency), _euro(fee.get("amount"), fee.get("currency")))
    return _same(_euro(row.amount_min, row.currency), _euro(fee.get("amount_min"), fee.get("currency"))) and _same(
        _euro(row.amount_max, row.currency), _euro(fee.get("amount_max"), fee.get("currency"))
    )


def score_school(rows: list[Any], fees: list[dict[str, Any]]) -> dict[str, Any]:
    """Each row's verdict and the key fees found, for one school."""
    verdicts: list[dict[str, Any]] = []
    found: set[str] = set()
    for row in rows:
        category, period, currency = _value(row.category), _value(row.period), row.currency
        at_amount = [fee for fee in fees if _row_matches(row, fee)]
        same_category = [fee for fee in at_amount if fee["category"] == category]
        verdict, fid = "wrong", None
        if not at_amount:
            reason = "amount not in key"
        elif not same_category:
            reason, fid = f"category {category}, key {at_amount[0]['category']}", at_amount[0]["fid"]
        else:
            # A site often prints one amount for several groups (505: one price, five grades),
            # each a key fee: a row takes a fee no earlier row has.
            same_category.sort(key=lambda fee: fee["fid"] in found)
            match = next((fee for fee in same_category if fee.get("period") == period), None)
            if match is not None:
                verdict, reason, fid = "right", "", match["fid"]
            elif period is None:
                verdict, reason, fid = "blank", "no period", same_category[0]["fid"]
            else:
                reason, fid = f"period {period}, key {same_category[0].get('period')}", same_category[0]["fid"]
        if verdict in ("right", "blank"):
            if fid in found:
                verdict, reason = "duplicate", f"second row on {fid}"
            found.add(fid)
        verdicts.append(
            {
                "category": category,
                "amount": str(row.amount) if row.amount is not None else None,
                "amount_range": [str(row.amount_min), str(row.amount_max)] if row.amount is None else None,
                "currency": currency,
                "period": period,
                "age_group": getattr(row, "age_group", None),
                "plan_name": getattr(row, "plan_name", None),
                "label": (row.pricing_context or {}).get("label"),
                "checks": (row.pricing_context or {}).get("checks") or [],
                "verdict": verdict,
                "fid": fid,
                "reason": reason,
            }
        )
    # A fee paid in instalments is one row whose options carry the instalments, each a key fee.
    # Counted after every row has its verdict, so a row of its own on an instalment is judged
    # whichever order the reader listed them in.
    for row in rows:
        for option in (row.pricing_context or {}).get("payment_options") or []:
            for name in ("total", "instalment", "first_payment"):
                value = _euro(option.get(name), row.currency)
                found |= {
                    fee["fid"]
                    for fee in fees
                    if fee["category"] == _value(row.category)
                    and _same(value, _euro(fee.get("amount"), fee.get("currency")))
                }
    return {"rows": verdicts, "found": sorted(found), "site_fees": len(fees)}


async def read_school(saved: dict[str, Any], spend: Spend, unpublished: list | None = None) -> list[Any]:
    """The publishable rows one read of the saved pages gives; the call is paid from ``spend``.

    ``unpublished`` collects the fees the read gave that the checks or the gate kept off the
    site, with the reason: whether a missing fee was never read or was dropped."""
    school = School(
        id=saved["school_id"],
        name_i18n=saved.get("name_i18n") or {},
        school_type=saved.get("school_type"),
        education_level=saved.get("education_level"),
        website_url=saved.get("website_url"),
        country_code="bg",
        attributes={},
    )
    pages = [PageRead(**page) for page in saved["pages"]]
    priced = [page for page in pages if page.prices]
    if not priced:
        return []
    message = reader_message(school, priced, ())
    in_price, out_price = get_model_costs("pricing")
    estimate = (len(message) / 3 * in_price + READ_OUTPUT_TOKENS_RESERVE * out_price) / 1_000_000
    with spend.hold(estimate):  # counted while in flight, so 8 workers cannot all pass the cap
        result = await asyncio.wait_for(reader_agent().run(message), READ_TIMEOUT_SECONDS)
    # OpenRouter sometimes reports no cost: the estimate stands in, so the cap still stops the run.
    spend.spent_usd += extract_provider_cost_usd(result) or estimate
    stand_ins = {page.url: _new_source_page(school, page, None, -(i + 1)) for i, page in enumerate(priced)}
    checked = []
    notes = unpublished if unpublished is not None else []
    for fee in result.output.fees:
        page = _page_for(fee, priced)
        outcome = check_fee(fee, page.text) if page is not None else None
        if outcome is not None and outcome.kept:
            checked.append((outcome, stand_ins[page.url]))
        else:
            notes.append(_note(fee, outcome.checks if outcome else ["no page at its source_url"]))
    rows, _ = _build_rows(school, checked)
    published = [row for row in rows if pricing_row_is_publishable(row)]
    for row in rows:
        if row not in published:
            notes.append(
                {
                    "category": _value(row.category),
                    "amounts": [str(v) for v in (row.amount, row.amount_min, row.amount_max) if v is not None],
                    "checks": (row.pricing_context or {}).get("checks") or ["publish gate"],
                }
            )
    return published


def _note(fee: Any, checks: list[str]) -> dict[str, Any]:
    amounts = (fee.amount, fee.amount_min, fee.amount_max)
    return {"category": _value(fee.category), "amounts": [str(v) for v in amounts if v is not None], "checks": checks}


def summarize(schools: dict[int, dict[str, Any]]) -> dict[str, Any]:
    counts = {name: 0 for name in ("rows", "right", "blank", "wrong", "duplicate", "found", "site_fees")}
    for school in schools.values():
        counts["rows"] += len(school["rows"])
        for row in school["rows"]:
            counts[row["verdict"]] += 1
        counts["found"] += len(school["found"])
        counts["site_fees"] += school["site_fees"]
    return counts | {
        "wrong_rate": round((counts["wrong"] + counts["duplicate"]) / counts["rows"], 4) if counts["rows"] else 0.0,
        "found_rate": round(counts["found"] / counts["site_fees"], 4) if counts["site_fees"] else 0.0,
    }


async def run_trial(cases: list[dict[str, Any]], key: dict[int, list], workers: int, spend: Spend) -> dict[str, Any]:
    gate = asyncio.Semaphore(workers)
    schools: dict[int, dict[str, Any]] = {}
    errors: dict[int, str] = {}

    async def one(saved: dict[str, Any]) -> None:
        school_id = saved["school_id"]
        unpublished: list[dict[str, Any]] = []
        async with gate:
            try:
                try:
                    rows = await read_school(saved, spend, unpublished)
                except TimeoutError:  # a slow provider: one more try, as the stage's retry
                    unpublished.clear()
                    rows = await read_school(saved, spend, unpublished)
            except Exception as exc:  # one failed read is that school's, not the run's
                errors[school_id] = f"{type(exc).__name__}: {str(exc)[:200]}"
                return
        schools[school_id] = score_school(rows, key[school_id]) | {"unpublished": unpublished}

    await asyncio.gather(*(one(saved) for saved in cases))
    return {"summary": summarize(schools), "schools": dict(sorted(schools.items())), "errors": errors}


def check(summary: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Why the score fails against the baseline; empty when it passes."""
    failures = []
    if summary["wrong_rate"] > baseline["wrong_rate"] + MAX_WRONG_RISE:
        failures.append(f"wrong {summary['wrong_rate']:.0%} against {baseline['wrong_rate']:.0%} in the baseline")
    if summary["found_rate"] < baseline["found_rate"] - MAX_FOUND_DROP:
        failures.append(f"found {summary['found_rate']:.0%} against {baseline['found_rate']:.0%} in the baseline")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-usd", type=float, default=0.50)
    parser.add_argument("--school-id", type=int, action="append", help="Only these schools (repeatable)")
    parser.add_argument("--out", type=Path, help="Write the full report here")
    parser.add_argument("--write-baseline", action="store_true", help="Store this run's averages as the baseline")
    parser.add_argument("--no-check", action="store_true", help="Report only; do not compare with the baseline")
    args = parser.parse_args()

    key = load_key()
    cases = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(PAGES_DIR.glob("*.json"))
        if int(path.stem) in key and (not args.school_id or int(path.stem) in args.school_id)
    ]
    missing = sorted(set(key) - {case["school_id"] for case in cases})
    if not cases:
        print(f"No saved pages in {PAGES_DIR} for the keyed schools.")
        return 2
    if missing and not args.school_id:
        print(f"Keyed schools without saved pages: {missing}; the score is not comparable.")
        return 2
    # The publish gate drops an offer once its end date passes: judge it on the day the pages
    # were saved, so the score does not move with the calendar.
    saved_on = max(datetime.date.fromisoformat(case["found_at"]) for case in cases)
    display_gating._sofia_today = lambda: saved_on
    spend = Spend(args.max_usd)

    async def all_trials() -> list[dict[str, Any]]:  # one event loop: the provider's client is shared
        return [await run_trial(cases, key, args.workers, spend) for _ in range(args.trials)]

    trials = asyncio.run(all_trials())
    rates = {
        name: round(sum(t["summary"][name] for t in trials) / len(trials), 4) for name in ("wrong_rate", "found_rate")
    }
    report = {
        "model": get_model("pricing"),
        "schools": len(cases),
        "schools_without_pages": missing,
        "trials": args.trials,
        "spent_usd": round(spend.spent_usd, 4),
        **rates,
        "per_trial": trials,
    }
    print(f"Model {report['model']}, {len(cases)} schools, {args.trials} trial(s), ${report['spent_usd']:.4f}")
    for i, trial in enumerate(trials, 1):
        s = trial["summary"]
        print(
            f"  trial {i}: rows {s['rows']}, right {s['right']}, blank {s['blank']}, wrong {s['wrong']} "
            f"({s['wrong_rate']:.0%}), duplicates {s['duplicate']}, found {s['found']}/{s['site_fees']} "
            f"({s['found_rate']:.0%}), errors {len(trial['errors'])}"
        )
        for school_id, error in trial["errors"].items():
            print(f"    {school_id}: {error}")
    print(f"Average: wrong {rates['wrong_rate']:.0%}, found {rates['found_rate']:.0%}")
    found_by_school = ", ".join(
        f"{school_id} {len(school['found'])}/{school['site_fees']}"
        for school_id, school in trials[-1]["schools"].items()
        if school["site_fees"] or school["rows"]
    )
    print(f"Found per school (last trial): {found_by_school}")
    wrong_rows = [
        (school_id, row)
        for school_id, school in trials[-1]["schools"].items()
        for row in school["rows"]
        if row["verdict"] == "wrong"
    ]
    for school_id, row in wrong_rows:
        print(
            f"  wrong at {school_id}: {row['category']} {row['amount']} {row['currency']} {row['period']} ({row['reason']})"
        )
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if any(trial["errors"] for trial in trials):
        print("Some schools were not read; the score is not comparable.")
        return 2
    if args.school_id:  # a subset is not comparable with the baseline
        return 0
    if args.write_baseline:
        BASELINE.write_text(
            json.dumps({"model": report["model"], "trials": args.trials, **rates}, indent=1) + "\n", encoding="utf-8"
        )
        print(f"Baseline written: {BASELINE}")
        return 0
    if args.no_check or not BASELINE.exists():
        return 0
    failures = check(rates, json.loads(BASELINE.read_text(encoding="utf-8")))
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
