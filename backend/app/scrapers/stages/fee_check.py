"""Check a fee read against the pages it came from (fees-agent, after the first read).

A second model reads the same pages and the fees the first read gave, and lists fees the
pages state that the read left out, and read fees that look wrong. The amounts it says were
missed go back to the reader as a second read: what it adds still has to pass every check
in agent_fees.py, so the checker never writes a fee itself. Wrong-looking fees are only
reported.

Why: people kept finding fee problems on the live site (Maple Bear's extras, ranges and
periods). In a trial on 15 keyed schools (2026-10-16, /mnt/project-files/fee-audit) Sonnet
flagged all 9 schools with real problems and 1 of 4 clean ones; per fee it found about 40%
of the misses, so it steers a re-read rather than scoring fees. It runs only when a school is
read, so its cost follows how often fee pages change, not the number of schools.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import os
import subprocess
from decimal import Decimal
from typing import Any, Optional, Protocol

from pydantic import BaseModel, Field

from app.ai.client import create_agent, extract_provider_cost_usd, get_model, get_model_costs, get_provider
from app.scrapers.extraction.agent_fees import AgentFee, fee_amounts, page_numbers, read_amounts
from app.services.provider_costs import execute_billable_request

logger = logging.getLogger(__name__)

TIER = "fee_check"
# schools.attributes key: the hash of the pages the last check read (internal, never published).
ATTRIBUTE = "fee_check"
ISSUE_TITLE = "Fee check: fees that look wrong"
# Thinking counts as output. A school's check wrote about 3K tokens in the trial; 16K leaves
# room for a long fee table and stays under the non-streaming request limit.
CHECK_MAX_OUTPUT_TOKENS = 16_000
CHECK_OUTPUT_TOKENS_RESERVE = 6_000
CHECK_TIMEOUT_SECONDS = 300

CHECK_PROMPT = """You check fees read from a school's web pages before a parent relies on them.
You get the pages and the numbered fees one read gave. List:
1. missed: a fee the pages clearly state (tuition, registration, food, transport, materials,
   uniforms, extended day, camps, activities...) that none of the read fees gives.
2. wrong: a read fee whose amount, period (yearly, monthly, term, one_time...), category or age
   group does not match what the pages say, or that repeats another read fee.

Rules:
- Use only what the pages say. Never guess. A blank period is not wrong when the pages state none.
- Only fees for the current or next school year, or with no year. Ignore older years.
- One read fee may cover several groups at the same price: that is not a miss.
- A printed price range ("from 200 to 550") is one fee with amount_min and amount_max.
- Discounts, instalments of a fee and payment plans are not separate fees.
- Fines, penalties, late-payment charges, duplicate documents, transcripts and replacing lost
  items are not fees: never list them as missed.
- Fees of another school or kindergarten named on the pages (another legal entity, another
  building with its own name) are out of scope.
- Copy a short line from the page as evidence for each finding.
Empty lists are the right answer when the read matches the pages."""


class MissedFee(BaseModel):
    category: str = Field(description="tuition, registration, food, transport, materials, uniforms, ...")
    amount: Optional[float] = Field(description="The amount as printed; null for a range")
    amount_min: Optional[float] = Field(description="A printed range's low end, else null")
    amount_max: Optional[float] = Field(description="A printed range's high end, else null")
    currency: Optional[str] = Field(description="EUR or BGN, as printed")
    period: Optional[str] = Field(description="As the page states it, else null")
    who: Optional[str] = Field(description="The age group, grade or plan the page gives, else null")
    evidence: str = Field(description="The page line that states the fee, copied")
    page: str = Field(description="The URL of the page, copied from its PAGE line")


class WrongFee(BaseModel):
    fee: int = Field(description="The read fee's number")
    problem: str = Field(description="What is wrong and what the page says instead")
    evidence: str = Field(description="The page line that shows it, copied")


class FeeCheck(BaseModel):
    missed: list[MissedFee]
    wrong: list[WrongFee]


class _Spend(Protocol):  # fees_agent.Spend
    spent_usd: float

    def allows(self, estimate_usd: float) -> bool: ...

    def hold(self, estimate_usd: float): ...


def _fee_line(index: int, fee: AgentFee) -> str:
    if fee.amount is not None:
        amount = f"{fee.amount:g}"
    else:
        amount = f"{fee.amount_min:g}–{fee.amount_max:g}" if fee.amount_min is not None else "no amount"
    parts = [
        f"{index}. {fee.category.value}",
        f"{amount} {fee.currency}",
        f"period {fee.period.value if fee.period else 'blank'}",
        f"year {fee.academic_year or 'not stated'}",
    ]
    if fee.age_group:
        parts.append(f"group {fee.age_group}")
    if fee.plan_name:
        parts.append(f"plan {fee.plan_name}")
    parts.append(f"page {fee.source_url}")
    return " | ".join(parts)


def check_message(school_name: str, pages_text: str, fees: list[AgentFee]) -> str:
    """The checker's whole input: the pages the reader saw, then the read's numbered fees."""
    listed = "\n".join(_fee_line(index, fee) for index, fee in enumerate(fees)) or "(no fees)"
    return f"Institution: {school_name}\n\n{pages_text}\n\n=== FEES THE READ GAVE\n{listed}"


def checker_agent():
    settings: dict[str, Any] = {"max_tokens": CHECK_MAX_OUTPUT_TOKENS}
    if get_provider(TIER) == "anthropic":
        settings["anthropic_effort"] = "medium"  # high (Sonnet's default) thinks longer for no gain here
    # No retry: a failed check leaves the read as it was, which is what happened before checks.
    return create_agent(TIER, CHECK_PROMPT, FeeCheck, retries=0, model_settings=settings)


def worst_case_usd(input_chars: int) -> float:
    """One check's most expensive outcome: input at 1.5 characters a token, full output."""
    in_price, out_price = get_model_costs(TIER)
    return (input_chars / 1.5 * in_price + CHECK_MAX_OUTPUT_TOKENS * out_price) / 1_000_000


async def check_fees(
    school_id: int, school_name: str, pages_text: str, fees: list[AgentFee], spend: _Spend, *, stage: str
) -> FeeCheck:
    """One checker call over the pages and the read's fees, paid from ``spend``."""
    from app.scrapers.stages.fees_agent import SpendCapReached

    message = check_message(school_name, pages_text, fees)
    in_price, out_price = get_model_costs(TIER)
    estimate = (len(message) / 3 * in_price + CHECK_OUTPUT_TOKENS_RESERVE * out_price) / 1_000_000
    if not spend.allows(estimate):
        raise SpendCapReached(f"spent ${spend.spent_usd:.4f}")
    agent = checker_agent()
    with spend.hold(estimate):
        result = await execute_billable_request(
            lambda: agent.run(message),
            model=get_model(TIER),
            school_id=school_id,
            stage=stage,
            timeout_seconds=CHECK_TIMEOUT_SECONDS,
        )
    spend.spent_usd += extract_provider_cost_usd(result)
    return result.output


def amounts_to_reread(check: FeeCheck, pages_text: str, fees: list[AgentFee]) -> list[Decimal]:
    """The amounts the check says were missed that the pages print and the read did not give.

    An amount the pages don't print is the checker's own arithmetic or a slip: dropped.
    """
    shown = page_numbers(pages_text)
    read = {amount for fee in fees for amount in read_amounts(fee)}
    return sorted({amount for finding in check.missed for amount in fee_amounts(finding) if amount in shown} - read)


def check_report(check: FeeCheck, fees: list[AgentFee]) -> dict[str, Any]:
    """The run report's record of one check."""
    wrong = []
    for finding in check.wrong:
        fee = fees[finding.fee] if 0 <= finding.fee < len(fees) else None
        wrong.append(
            {
                "fee": finding.fee,
                "category": fee.category.value if fee else None,
                "amounts": [format(amount, "f") for amount in fee_amounts(fee)] if fee else None,
                "problem": finding.problem,
                "evidence": finding.evidence,
            }
        )
    return {"missed": [finding.model_dump() for finding in check.missed], "wrong": wrong}


def pages_hash(pages_text: str) -> str:
    return hashlib.sha256(pages_text.encode()).hexdigest()


def fee_signature(fee: AgentFee) -> str:
    amounts = "/".join(format(amount, "f") for amount in fee_amounts(fee))
    period = fee.period.value if fee.period else ""
    return f"{fee.category.value}|{amounts}|{fee.currency}|{period}|{fee.age_group or ''}|{fee.plan_name or ''}"


def already_checked(attributes: Optional[dict], digest: str, fees: list[AgentFee]) -> bool:
    """True when the last check read these same pages and every fee this read gives, and its
    findings reached GitHub: checking again would find the same.

    The check costs about ten times the read, and most schools' fee pages change once a year.
    A fee the last check did not see (the reader varies from run to run) is checked.
    """
    block = (attributes or {}).get(ATTRIBUTE) or {}
    return (
        block.get("pages_hash") == digest
        and block.get("reported", True)
        and {fee_signature(fee) for fee in fees} <= set(block.get("fees") or [])
    )


def checked(attributes: Optional[dict], digest: str, fees: list[AgentFee], *, reported: bool) -> dict[str, Any]:
    """``attributes`` with this check recorded; ``reported`` is False while its wrong fees wait for GitHub."""
    block = {
        "pages_hash": digest,
        "fees": sorted({fee_signature(fee) for fee in fees}),
        "checked_on": datetime.date.today().isoformat(),
        "reported": reported,
    }
    return {**(attributes or {}), ATTRIBUTE: block}


def mark_reported(attributes: Optional[dict]) -> dict[str, Any]:
    block = (attributes or {}).get(ATTRIBUTE)
    return {**(attributes or {}), ATTRIBUTE: {**block, "reported": True}} if block else dict(attributes or {})


def wrong_fees_body(results: list[dict[str, Any]]) -> Optional[str]:
    """The issue text listing every fee the run's checks thought wrong; None when there are none."""
    lines = []
    for result in results:
        for finding in (result.get("check") or {}).get("wrong") or []:
            amounts = "/".join(finding.get("amounts") or []) or "?"
            lines.append(
                f"- School {result['school_id']}: {finding.get('category') or 'fee'} {amounts} "
                f"(read fee {finding['fee']}). {finding['problem']}\n  > {finding['evidence']}"
            )
    if not lines:
        return None
    return (
        "The fee check (backend/app/scrapers/stages/fee_check.py) thinks these fees, as read and "
        "possibly published, do not match the school's pages. It does not change them. A school is "
        "checked again only when its fee pages change.\n\n" + "\n".join(lines)
    )


def open_wrong_fees_issue(results: list[dict[str, Any]], *, run_id: Any = None) -> Optional[str]:
    """Report the run's wrong-looking fees on GitHub: a new issue, or a comment on the open one.

    Best effort with the gh CLI, like the PC's other jobs; returns what it did, for the report.
    """
    body = wrong_fees_body(results)
    if body is None:
        return None
    if run_id is not None:
        body = f"fees-agent run {run_id}, {datetime.date.today().isoformat()}.\n\n{body}"
    repo = os.environ.get("PUBLISH_REPO", "mike2906/school-comparison")
    try:
        found = subprocess.run(
            ["gh", "issue", "list", "-R", repo, "--state", "open", "--search", f'in:title "{ISSUE_TITLE}"',
             "--json", "number", "-q", ".[0].number"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout.strip()  # fmt: skip
        if found:
            subprocess.run(
                ["gh", "issue", "comment", found, "-R", repo, "--body", body],
                capture_output=True,
                timeout=60,
                check=True,
            )
            return f"commented on issue {found}"
        subprocess.run(
            ["gh", "issue", "create", "-R", repo, "--title", ISSUE_TITLE, "--body", body],
            capture_output=True, timeout=60, check=True,
        )  # fmt: skip
        return "opened an issue"
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("fees-agent could not report wrong fees on GitHub: %s", exc)
        return f"not reported: {str(exc)[:200]}"
