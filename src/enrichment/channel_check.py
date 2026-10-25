"""Ask the dish classifier whether a cheaper delivery price is a fair comparison.

The comparison code only detects the smell (delivery below restaurant) and
attaches the other cards of that dish. This module is a second task for the
same OpenAI structured-output path as dish classification. It does not contain
a rule that canned drinks are fine or that gluten-free prices are not. Those
examples live in the prompt, and the model judges each pair from the evidence.

The Streamlit app never calls this. Run ``python -m scripts.assess_channel_smells``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from src.delivery_compare import SmellCase, evidence_text
from src.enrichment.llm import (
    _MAX_RETRIES,
    _model_name,
    _require_api_key,
    _require_async_openai,
)
from src.enrichment.schema import OPENAI_CHANNEL_FORMAT, ChannelVerdict

_SYSTEM_PROMPT = """You assess one joined restaurant-versus-delivery pair whose delivery menu price is lower.
The names already match. Decide whether that lower price is a fair comparison of the same product, or a join that has picked up a different specification.

Return one verdict:
- mishap: the delivery price is the price of a different specification of this dish. Use this when another card (for example gluten-free, leggera, or a named size) is listed separately and its price equals the delivery price, while the delivery card keeps the parent name. That pair is not a delivery discount.
- justified: the lower delivery price is a fair comparison. Use this when the names or descriptions show a real format difference, such as a can on delivery and a bottle in the restaurant, or a smaller glass against a bottle. Also use this when nothing in the evidence points at a different specification: a genuinely cheaper delivery menu price is a real gap, not a bad join.
- uncertain: the evidence is not enough to choose between those two.

Judge only from the cards and descriptions in the user message. Do not invent a can, a bottle, or a dietary variant that is not written there.
Write the reason as one sentence a pricing analyst can read next to the chart.
"""


@dataclass(slots=True)
class SmellAssessment:
    """Outcome of assessing one cheaper pair (verdict or error, never both)."""

    case: SmellCase
    verdict: str | None = None
    reason: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.verdict is not None


def parse_channel_verdict(content: str | None) -> tuple[str, str]:
    """Read a structured classifier response into ``(verdict, reason)``."""
    if not content:
        raise RuntimeError("Empty classifier response")
    payload = json.loads(content)
    verdict = payload.get("verdict")
    allowed = {member.value for member in ChannelVerdict}
    if verdict not in allowed:
        raise RuntimeError(f"Unexpected verdict {verdict!r}")
    reason = str(payload.get("reason", "")).strip()
    if not reason:
        raise RuntimeError("Classifier returned an empty reason")
    return str(verdict), reason


def build_messages(case: SmellCase) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": evidence_text(case)},
    ]


async def _assess_one(
    client,
    case: SmellCase,
    *,
    model: str,
    semaphore: asyncio.Semaphore,
) -> SmellAssessment:
    async with semaphore:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=build_messages(case),
                response_format=OPENAI_CHANNEL_FORMAT,
            )
            verdict, reason = parse_channel_verdict(response.choices[0].message.content)
            return SmellAssessment(case=case, verdict=verdict, reason=reason)
        except Exception as exc:  # one failed pair must not abort the batch
            return SmellAssessment(case=case, error=str(exc))


async def assess_smells_async(
    cases: Iterable[SmellCase],
    *,
    concurrency: int = 8,
    model: str | None = None,
    on_result: Callable[[SmellAssessment], None] | None = None,
) -> list[SmellAssessment]:
    """Assess many cheaper pairs concurrently. Failures are returned, not raised."""
    cases = list(cases)
    if not cases:
        return []

    api_key = _require_api_key()
    model = model or _model_name()
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async_client_cls = _require_async_openai()

    async with async_client_cls(api_key=api_key, max_retries=_MAX_RETRIES) as client:
        tasks = [
            asyncio.create_task(
                _assess_one(client, case, model=model, semaphore=semaphore)
            )
            for case in cases
        ]
        results: list[SmellAssessment] = []
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if on_result is not None:
                on_result(result)
            results.append(result)
    return results
