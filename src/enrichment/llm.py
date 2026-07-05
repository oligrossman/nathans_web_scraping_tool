"""OpenAI-backed dish classification (offline batch only).

Two entry points share the same prompt and parsing:

- ``classify_dish``           — synchronous, one dish at a time.
- ``classify_dishes_async``   — asyncio batch with a shared client and a
                                concurrency cap, for classifying many dishes.

Classification is network (I/O) bound, so the async path uses ``AsyncOpenAI``
with a semaphore rather than threads or processes.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .schema import OPENAI_RESPONSE_FORMAT, DishMapping

DEFAULT_MODEL = "gpt-5-nano"
# The SDK retries transient errors (incl. 429s) with exponential backoff.
_MAX_RETRIES = 5

_SYSTEM_PROMPT = """You classify UK restaurant menu items into a fixed taxonomy.
Use only the allowed enum values in the response schema.

Guidance:
- cuisine: the dominant culinary style of the dish (not the restaurant brand).
- dish_type: the kind of dish it is (e.g. Noodles, Rice, Curry, Soup, Pizza,
  Pasta, Burger, Wrap, Salad, Grill, StirFry, Dumplings, Sushi, SmallPlate,
  Dessert, Drink, Side). Use Side for generic accompaniments, Drink for
  beverages, and Other only when nothing else fits.
- is_vegetarian / is_vegan: infer from the dish name and description when given.
"""


@dataclass(slots=True)
class DishRequest:
    """One dish to classify (unique per restaurant via ``dish_id``)."""

    dish_id: str
    source: str
    name: str
    bucket: str
    description: str | None = None


@dataclass(slots=True)
class DishResult:
    """Outcome of classifying one dish (mapping or error, never both)."""

    request: DishRequest
    mapping: DishMapping | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.mapping is not None


def _model_name() -> str:
    return os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)


def _require_api_key() -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Export it before running enrichment."
        )
    return api_key


def _require_openai():
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ImportError(
            "The openai package is required for enrichment. "
            "Install with: pip install openai"
        ) from exc
    return OpenAI


def _require_async_openai():
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ImportError(
            "The openai package is required for enrichment. "
            "Install with: pip install openai"
        ) from exc
    return AsyncOpenAI


def _build_messages(name: str, bucket: str, description: str | None) -> list[dict[str, str]]:
    user_lines = [
        f"Dish name: {name}",
        f"Menu bucket (course): {bucket}",
    ]
    if description:
        user_lines.append(f"Description: {description}")
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_lines)},
    ]


def _parse_mapping(
    content: str | None,
    *,
    dish_id: str,
    source: str,
    name: str,
    bucket: str,
) -> DishMapping:
    if not content:
        raise RuntimeError(f"Empty LLM response for dish {name!r}")
    payload: dict[str, Any] = json.loads(content)
    return DishMapping.from_llm_payload(
        dish_id=dish_id,
        source=source,
        name=name,
        bucket=bucket,
        payload=payload,
    )


def classify_dish(
    *,
    dish_id: str,
    source: str,
    name: str,
    bucket: str,
    description: str | None = None,
) -> DishMapping:
    """Classify one menu item via OpenAI structured outputs (synchronous)."""
    api_key = _require_api_key()
    client = _require_openai()(api_key=api_key, max_retries=_MAX_RETRIES)

    response = client.chat.completions.create(
        model=_model_name(),
        messages=_build_messages(name, bucket, description),
        response_format=OPENAI_RESPONSE_FORMAT,
    )
    return _parse_mapping(
        response.choices[0].message.content,
        dish_id=dish_id,
        source=source,
        name=name,
        bucket=bucket,
    )


async def _classify_one_async(
    client,
    request: DishRequest,
    *,
    model: str,
    semaphore: asyncio.Semaphore,
) -> DishResult:
    """Classify one dish, isolating failures so a batch never aborts."""
    async with semaphore:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=_build_messages(
                    request.name, request.bucket, request.description
                ),
                response_format=OPENAI_RESPONSE_FORMAT,
            )
            mapping = _parse_mapping(
                response.choices[0].message.content,
                dish_id=request.dish_id,
                source=request.source,
                name=request.name,
                bucket=request.bucket,
            )
            return DishResult(request=request, mapping=mapping)
        except Exception as exc:  # surfaced per-dish instead of failing the batch
            return DishResult(request=request, error=str(exc))


async def classify_dishes_async(
    requests: Iterable[DishRequest],
    *,
    concurrency: int = 8,
    model: str | None = None,
    on_result: Callable[[DishResult], None] | None = None,
) -> list[DishResult]:
    """Classify many dishes concurrently with a shared client and a semaphore.

    ``on_result`` is invoked as each dish completes (in arbitrary order), which
    lets callers stream results to storage for resumability. Returns every
    result, successful or failed.
    """
    requests = list(requests)
    if not requests:
        return []

    api_key = _require_api_key()
    model = model or _model_name()
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async_client_cls = _require_async_openai()

    async with async_client_cls(api_key=api_key, max_retries=_MAX_RETRIES) as client:
        tasks = [
            asyncio.create_task(
                _classify_one_async(
                    client, request, model=model, semaphore=semaphore
                )
            )
            for request in requests
        ]
        results: list[DishResult] = []
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if on_result is not None:
                on_result(result)
            results.append(result)
    return results
