"""Bounded English-identity LLM adjudication pilot (read-only, never publishes).

Population: the known-good identities the deterministic resolver currently misses
plus every known-bad trap in the fixed benchmark. Evidence comes only from cached
already-valid official pages. The script performs no database write, no refresh,
no provider discovery call, no OCR, and no geocode attempt; accepted cases are
recommendations for manual promotion, never publications.

    uv run python scripts/pilot_identity_adjudication.py            # dry run, 0 LLM calls
    uv run python scripts/pilot_identity_adjudication.py --live     # one call per case
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.database import async_session_maker
from app.services.identity_adjudication import (
    MIN_ACCEPT_CONFIDENCE,
    MIN_SUPPORTING_SOURCE_URLS,
    IdentityAdjudicationVerdict,
    adjudicate_case,
    apply_guards,
    build_cases,
    build_user_prompt,
    case_row,
)
from app.services.identity_resolver_benchmark import (
    evaluate_identity_resolver_benchmark,
    load_identity_resolver_benchmark,
)


REPORT_ROOT = Path(__file__).resolve().parents[1] / "reports" / "p2-14f"


async def _pilot_population(db) -> list[dict[str, Any]]:
    """Missed known-good identities first, then every known-bad trap."""
    benchmark = load_identity_resolver_benchmark()
    result = await evaluate_identity_resolver_benchmark(db, benchmark=benchmark)
    missed_ids = {row["id"] for row in result["known_good"]["missed"]}

    population = [
        {
            "school_id": int(row["id"]),
            "candidate": {"en": row["english_name"]},
            "expected": "accept",
            "group": "known_good_missed",
            "label": row["english_name"],
        }
        for row in benchmark["known_good"]
        if int(row["id"]) in missed_ids
    ]
    population.extend(
        {
            "school_id": int(row["id"]),
            "candidate": row["candidate"],
            "expected": "reject",
            "group": "known_bad",
            "label": row["candidate"].get("en") or row["candidate"].get("bg"),
            "trap": row["reason"],
        }
        for row in benchmark["known_bad"]
    )
    return population


def _replay_row(case, recorded: dict[str, Any] | None) -> dict[str, Any]:
    """Re-score a recorded model verdict under the current guards, no LLM call.

    Guard changes must be re-validated against the same model output rather than
    by paying for another run.
    """
    row = case_row(case, decision="rejected")
    if not recorded or not recorded.get("verdict"):
        row["reason_code"] = case.blocked_reason or (recorded or {}).get("reason_code")
        row["replayed"] = False
        return row

    verdict = IdentityAdjudicationVerdict(
        verdict=recorded["verdict"],
        reason_code=recorded["reason_code"],
        confidence=recorded["confidence"],
        quoted_evidence=recorded.get("quoted_evidence") or [],
    )
    decision, failures = apply_guards(case, verdict)
    row.update(
        {
            "verdict": verdict.verdict,
            "reason_code": verdict.reason_code,
            "confidence": verdict.confidence,
            "quoted_evidence": list(verdict.quoted_evidence),
            "guard_failures": failures,
            "decision": decision,
            "replayed": True,
            "recorded_decision": recorded.get("decision"),
            "recorded_token_cost_usd": recorded.get("token_cost_usd", 0.0),
        }
    )
    return row


def _scoreboard(rows: list[dict[str, Any]]) -> dict[str, Any]:
    good = [row for row in rows if row["expected"] == "accept"]
    bad = [row for row in rows if row["expected"] == "reject"]
    recovered = [row for row in good if row["decision"] == "recommend_manual_promotion"]
    held = [row for row in good if row["decision"] == "hold_insufficient_provenance"]
    leaked = [row for row in bad if row["decision"] == "recommend_manual_promotion"]
    model_leaked = [row for row in bad if row["verdict"] == "accept"]
    return {
        "known_good_missed": {
            "total": len(good),
            "recovered": len(recovered),
            "recovered_ids": [row["school_id"] for row in recovered],
            "held_for_provenance": [row["school_id"] for row in held],
            "still_rejected": [
                row["school_id"] for row in good if row["decision"] == "rejected"
            ],
        },
        "known_bad": {
            "total": len(bad),
            "leaked_after_guards": len(leaked),
            "leaked_ids": [row["school_id"] for row in leaked],
            "model_accepts_before_guards": [row["school_id"] for row in model_leaked],
        },
        "llm_calls": sum(1 for row in rows if row["llm_called"]),
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "token_cost_usd": round(sum(row["token_cost_usd"] for row in rows), 6),
    }


def _write_acceptance(path: Path, payload: dict[str, Any]) -> None:
    score = payload["scoreboard"]
    good = score["known_good_missed"]
    bad = score["known_bad"]
    lines = [
        "# Bounded English-identity LLM adjudication pilot",
        "",
        f"- Mode: `{payload['mode']}`",
        f"- Generated: {payload['generated_at']}",
        f"- Cases: {payload['cases']} "
        f"({good['total']} missed known-good, {bad['total']} known-bad traps)",
        f"- LLM calls: {score['llm_calls']} "
        f"(input {score['input_tokens']} / output {score['output_tokens']} tokens, "
        f"${score['token_cost_usd']:.6f})",
        "",
        "## Result",
        "",
        f"- Recovered known-good identities: {good['recovered']}/{good['total']} "
        f"{good['recovered_ids']}",
        f"- Held for insufficient provenance: {good['held_for_provenance']}",
        f"- Still rejected: {good['still_rejected']}",
        f"- Known-bad leaked after guards: {bad['leaked_after_guards']}/{bad['total']} "
        f"{bad['leaked_ids']}",
        f"- Known-bad accepted by the model before guards: "
        f"{bad['model_accepts_before_guards']}",
        "",
        "## Boundary",
        "",
        "- No database write, publication, refresh, provider discovery call, OCR, or",
        "  geocode attempt was performed; evidence came only from cached valid pages.",
        "- Accepts are recommendations for manual promotion through",
        "  `app.services.identity_curation`, which still requires "
        f"{MIN_SUPPORTING_SOURCE_URLS} same-domain source URLs and a named reviewer.",
        f"- Accept floor: confidence >= {MIN_ACCEPT_CONFIDENCE}; deterministic guards can",
        "  only reject, never accept.",
        "",
        "## Cases",
        "",
        "| School | Candidate | Expected | Verdict | Reason | Conf | Decision | Guards |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    caught = [
        row
        for row in payload["rows"]
        if row["verdict"] == "accept" and row["decision"] != "recommend_manual_promotion"
    ]
    for row in payload["rows"]:
        lines.append(
            "| {school_id} | {candidate} | {expected} | {verdict} | {reason} | {conf} | "
            "{decision} | {guards} |".format(
                school_id=row["school_id"],
                candidate=(row["candidate_en"] or row["label"] or "—"),
                expected=row["expected"],
                verdict=row["verdict"] or "—",
                reason=row["reason_code"] or "—",
                conf="—" if row["confidence"] is None else f"{row['confidence']:.2f}",
                decision=row["decision"],
                guards=", ".join(row["guard_failures"]) or "—",
            )
        )

    if caught:
        lines.extend(
            [
                "",
                "## Accepts stopped by deterministic guards",
                "",
                "Each row is a label the model was willing to publish. A row with a",
                "single guard means that guard is the only defense in front of it.",
                "",
            ]
        )
        for row in caught:
            failures = row["guard_failures"]
            sole = " (sole defense)" if len(failures) == 1 else ""
            lines.append(
                f"- School {row['school_id']} — `{row['candidate_en']}` "
                f"(expected {row['expected']}, level {row['education_level']}, "
                f"{len(row['supporting_source_urls'])} supporting URLs): "
                f"{', '.join(failures) or 'none'}{sole}"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    # `--live` is the only mode that spends money, so it must never combine with
    # a mode documented as making no LLM call: a silent precedence rule would
    # turn an intended replay into fresh paid, nondeterministic requests.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--live",
        action="store_true",
        help="perform one bounded LLM call per case (default: assemble evidence only)",
    )
    parser.add_argument(
        "--prefilter-rejects",
        action="store_true",
        help="skip the LLM call when deterministic guards already reject the candidate",
    )
    parser.add_argument("--limit", type=int, default=0, help="adjudicate at most N cases")
    mode.add_argument(
        "--from-json",
        type=Path,
        help="re-render acceptance.md from a recorded run without any LLM call",
    )
    mode.add_argument(
        "--replay-json",
        type=Path,
        help="re-score a recorded run's model verdicts under the current guards "
        "(no LLM call); use after changing a guard",
    )
    args = parser.parse_args()

    if args.from_json:
        payload = json.loads(args.from_json.read_text(encoding="utf-8"))
        _write_acceptance(args.from_json.parent / "acceptance.md", payload)
        print(f"Re-rendered {args.from_json.parent / 'acceptance.md'} (0 LLM calls)")
        return

    recorded_rows: dict[int, dict[str, Any]] = {}
    if args.replay_json:
        recorded_payload = json.loads(args.replay_json.read_text(encoding="utf-8"))
        recorded_rows = {int(row["school_id"]): row for row in recorded_payload["rows"]}

    async with async_session_maker() as db:
        population = await _pilot_population(db)
        if args.limit > 0:
            population = population[: args.limit]
        cases = await build_cases(db, population)

        rows: list[dict[str, Any]] = []
        for request, case in zip(population, cases):
            if args.live:
                row = await adjudicate_case(
                    case, prefilter_rejects=args.prefilter_rejects
                )
            elif args.replay_json:
                row = _replay_row(case, recorded_rows.get(case.school_id))
            else:
                row = case_row(case, decision="not_adjudicated_dry_run")
                row["reason_code"] = case.blocked_reason
                row["prompt_chars"] = len(build_user_prompt(case))
            row.update(
                {
                    "expected": request["expected"],
                    "group": request["group"],
                    "label": request["label"],
                    "trap": request.get("trap"),
                }
            )
            rows.append(row)

        # The pilot must leave the session clean: assembling evidence may not
        # stage any change, and nothing is ever committed.
        pending = [obj for obj in (db.new | db.dirty | db.deleted)]
        if pending:
            raise RuntimeError(f"Pilot staged unexpected database changes: {pending}")

    payload = {
        "schema_version": 1,
        "mode": (
            "live_bounded_adjudication"
            if args.live
            else "replay_recorded_verdicts_no_llm"
            if args.replay_json
            else "dry_run_evidence_only"
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases": len(rows),
        "rows": rows,
        "scoreboard": _scoreboard(rows),
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPORT_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "adjudication.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_acceptance(out_dir / "acceptance.md", payload)
    print(json.dumps(payload["scoreboard"], ensure_ascii=False, indent=2))
    print(f"\nEvidence: {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
