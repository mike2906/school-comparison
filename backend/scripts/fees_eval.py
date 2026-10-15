"""Fees eval: run the fees agent's reader on saved pages and score it against the blind key.

The pages are what the agent's finder found (``fees-agent --save-pages evals/fees/pages``);
the key is the R6 step 3 and step 4 answer keys in ``docs/accuracy`` (judged site fees where
the judges ran, the blind key otherwise). Saved pages keep the score from drifting as sites
change, so a change in the score is a change in the prompt, schema, checks or model.

What it covers: the reader, ``check_fee`` and ``_build_rows``, and the publish gate on the
rows. Not the finder (it needs live sites), the re-read (it needs the school's stored rows)
or the keep rules. A row counts as:

- right: amount, category and period match a key fee;
- blank: amount and category match, the row has no period and the key fee has one;
- wrong: anything else, a row at an amount the key does not have included;
- duplicate: a second row on a key fee already matched.

A key fee is found when a row, or one of a row's payment options, matches its amount and
category. Amounts are compared in euro (BGN at 1.95583) within 1 %.

    uv run python scripts/fees_eval.py                       # one trial, report + baseline check
    uv run python scripts/fees_eval.py --trials 3            # repeat, scores averaged
    uv run python scripts/fees_eval.py --write-baseline      # store this run as the baseline
    MODEL_TIER_PRICING=openrouter/... uv run python scripts/fees_eval.py --no-check   # try a model

Calls the model directly (no DB, no ledger); ``--max-usd`` stops the run. Exit code 1 when the
score falls past the margins below the baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.ai.client import extract_provider_cost_usd, get_model  # noqa: E402
from app.models import School  # noqa: E402
from app.scrapers.extraction.agent_fees import check_fee  # noqa: E402
from app.scrapers.stages.fees_agent import (  # noqa: E402
    PageRead,
    _build_rows,
    _new_source_page,
    _page_for,
    reader_agent,
    reader_message,
)
from app.utils.display_gating import pricing_row_is_publishable  # noqa: E402

EVAL_DIR = BACKEND / "evals" / "fees"
PAGES_DIR = EVAL_DIR / "pages"
BASELINE = EVAL_DIR / "baseline.json"
ACCURACY = BACKEND.parent / "docs" / "accuracy"
KEY_SETS = ("step3", "step4")
BGN_PER_EUR = Decimal("1.95583")
TOLERANCE = Decimal("0.01")
# A drop past these fails the check: one trial moves by a few points on its own.
MAX_WRONG_RISE = 0.05
MAX_FOUND_DROP = 0.07


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


def _fee_amounts(fee: dict[str, Any]) -> list[Decimal]:
    return [
        value
        for value in (_euro(fee.get(name), fee.get("currency")) for name in ("amount", "amount_min", "amount_max"))
        if value is not None
    ]


def _value(field: Any) -> Any:
    return getattr(field, "value", field)


def score_school(rows: list[Any], fees: list[dict[str, Any]]) -> dict[str, Any]:
    """Each row's verdict and the key fees found, for one school."""
    verdicts: list[dict[str, Any]] = []
    found: set[str] = set()
    for row in rows:
        category, period, currency = _value(row.category), _value(row.period), row.currency
        amounts = [
            value for value in (_euro(row.amount, currency), _euro(row.amount_min, currency)) if value is not None
        ]
        at_amount = [fee for fee in fees if any(_same(a, b) for a in amounts for b in _fee_amounts(fee))]
        same_category = [fee for fee in at_amount if fee["category"] == category]
        verdict, fid = "wrong", None
        if not at_amount:
            reason = "amount not in key"
        elif not same_category:
            reason, fid = f"category {category}, key {at_amount[0]['category']}", at_amount[0]["fid"]
        else:
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
        # A fee paid in instalments is one row whose options carry the instalments, each a key fee.
        for option in (row.pricing_context or {}).get("payment_options") or []:
            for name in ("total", "instalment", "first_payment"):
                value = _euro(option.get(name), currency)
                found |= {
                    fee["fid"]
                    for fee in fees
                    if fee["category"] == category and any(_same(value, b) for b in _fee_amounts(fee))
                }
        verdicts.append(
            {
                "category": category,
                "amount": str(row.amount) if row.amount is not None else None,
                "currency": currency,
                "period": period,
                "label": (row.pricing_context or {}).get("label"),
                "verdict": verdict,
                "fid": fid,
                "reason": reason,
            }
        )
    return {"rows": verdicts, "found": sorted(found), "site_fees": len(fees)}


async def read_school(saved: dict[str, Any]) -> tuple[list[Any], float]:
    """The publishable rows one read of the saved pages gives, and the call's cost."""
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
        return [], 0.0
    result = await reader_agent().run(reader_message(school, priced, ()))
    stand_ins = {page.url: _new_source_page(school, page, None, -(i + 1)) for i, page in enumerate(priced)}
    checked = []
    for fee in result.output.fees:
        page = _page_for(fee, priced)
        if page is not None and (outcome := check_fee(fee, page.text)).kept:
            checked.append((outcome, stand_ins[page.url]))
    rows, _ = _build_rows(school, checked)
    return [row for row in rows if pricing_row_is_publishable(row)], extract_provider_cost_usd(result)


def summarize(schools: dict[int, dict[str, Any]]) -> dict[str, Any]:
    counts = {name: 0 for name in ("rows", "right", "blank", "wrong", "duplicate", "found", "site_fees")}
    for school in schools.values():
        counts["rows"] += len(school["rows"])
        for row in school["rows"]:
            counts[row["verdict"]] += 1
        counts["found"] += len(school["found"])
        counts["site_fees"] += school["site_fees"]
    judged = counts["rows"] - counts["duplicate"]
    return counts | {
        "wrong_rate": round(counts["wrong"] / judged, 4) if judged else 0.0,
        "found_rate": round(counts["found"] / counts["site_fees"], 4) if counts["site_fees"] else 0.0,
    }


async def run_trial(cases: list[dict[str, Any]], key: dict[int, list], workers: int, budget: dict) -> dict[str, Any]:
    gate = asyncio.Semaphore(workers)
    schools: dict[int, dict[str, Any]] = {}
    errors: dict[int, str] = {}

    async def one(saved: dict[str, Any]) -> None:
        school_id = saved["school_id"]
        async with gate:
            if budget["spent"] >= budget["cap"]:
                errors[school_id] = "spend cap reached"
                return
            try:
                rows, cost = await read_school(saved)
            except Exception as exc:  # one failed read is that school's, not the run's
                errors[school_id] = f"{type(exc).__name__}: {str(exc)[:200]}"
                return
        budget["spent"] += cost
        schools[school_id] = score_school(rows, key[school_id])

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
    budget = {"spent": 0.0, "cap": args.max_usd}
    trials = [asyncio.run(run_trial(cases, key, args.workers, budget)) for _ in range(args.trials)]
    rates = {
        name: round(sum(t["summary"][name] for t in trials) / len(trials), 4) for name in ("wrong_rate", "found_rate")
    }
    report = {
        "model": get_model("pricing"),
        "schools": len(cases),
        "schools_without_pages": missing,
        "trials": args.trials,
        "spent_usd": round(budget["spent"], 4),
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
