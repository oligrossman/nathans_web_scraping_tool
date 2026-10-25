"""Smell checks stay in the averages until the classifier judges them."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.delivery_compare import (
    JUSTIFIED,
    MISHAP,
    UNASSESSED,
    SmellCase,
    apply_assessments,
    collect_smell_cases,
    company_summary,
    compare_menus,
    evidence_text,
    make_check_id,
)
from src.enrichment.channel_check import _SYSTEM_PROMPT, parse_channel_verdict
from src.enrichment.schema import CHANNEL_CHECK_SCHEMA, ChannelVerdict
from src.enrichment.store import load_channel_checks, upsert_channel_check


def _menus() -> tuple[pd.DataFrame, pd.DataFrame]:
    restaurant = pd.DataFrame(
        {
            "name": [
                "Pollo ad Astra",
                "Gluten-Free Pollo ad Astra",
                "Leggera Pollo ad Astra",
                "Coca-Cola",
                "Margherita",
            ],
            "price": [18.45, 16.50, 17.45, 3.95, 13.95],
            "category": ["Pizza", "Pizza", "Pizza", "Drinks", "Pizza"],
            "description": [
                "",
                "Gluten-free base",
                "",
                "750ml bottle",
                "",
            ],
        }
    )
    delivery = pd.DataFrame(
        {
            "name": ["Pollo ad Astra", "Coca-Cola", "Margherita"],
            "price": [16.50, 2.55, 13.95],
            "description": ["", "330ml can", ""],
        }
    )
    return restaurant, delivery


def test_cheaper_price_is_unassessed_until_the_classifier_speaks() -> None:
    restaurant, delivery = _menus()
    frame, smells = compare_menus("Pizza Express", restaurant, delivery)
    pollo = frame.loc[frame["item"] == "Pollo ad Astra"].iloc[0]
    coke = frame.loc[frame["item"] == "Coca-Cola"].iloc[0]
    margherita = frame.loc[frame["item"] == "Margherita"].iloc[0]

    assert pollo["assessment"] == UNASSESSED
    assert bool(pollo["comparable"]) is True
    assert coke["assessment"] == UNASSESSED
    assert margherita["assessment"] == "like_for_like"
    assert margherita["check_id"] == ""

    by_item = {case.item: case for case in smells}
    assert "Gluten-Free Pollo ad Astra" in by_item["Pollo ad Astra"].price_ties[0]
    assert by_item["Coca-Cola"].price_ties == ()
    assert "750ml bottle" in evidence_text(by_item["Coca-Cola"])
    assert "330ml can" in evidence_text(by_item["Coca-Cola"])
    assert "Gluten-Free Pollo ad Astra" in evidence_text(by_item["Pollo ad Astra"])

    summary = company_summary(frame)
    assert int(summary.loc[summary["company"] == "Pizza Express", "items"].iloc[0]) == 3


def test_mishap_leaves_the_average_and_a_justified_drink_stays() -> None:
    restaurant, delivery = _menus()
    frame, smells = compare_menus("Pizza Express", restaurant, delivery)
    by_item = {case.item: case for case in smells}
    checks = pd.DataFrame(
        [
            {
                "check_id": by_item["Pollo ad Astra"].check_id,
                "verdict": MISHAP,
                "reason": "Delivery price equals the gluten-free card.",
            },
            {
                "check_id": by_item["Coca-Cola"].check_id,
                "verdict": JUSTIFIED,
                "reason": "Delivery is a 330ml can and the restaurant card is a 750ml bottle.",
            },
        ]
    )
    judged = apply_assessments(frame, checks)
    pollo = judged.loc[judged["item"] == "Pollo ad Astra"].iloc[0]
    coke = judged.loc[judged["item"] == "Coca-Cola"].iloc[0]
    assert pollo["assessment"] == MISHAP
    assert bool(pollo["comparable"]) is False
    assert coke["assessment"] == JUSTIFIED
    assert bool(coke["comparable"]) is True

    summary = company_summary(judged)
    row = summary.loc[summary["company"] == "Pizza Express"].iloc[0]
    assert int(row["items"]) == 2
    assert row["delivery"] == pytest.approx((2.55 + 13.95) / 2, abs=0.01)


def test_check_id_changes_when_the_price_changes() -> None:
    first = make_check_id("Pizza Express", "Pollo ad Astra", 18.45, 16.50)
    same = make_check_id("Pizza Express", "Pollo ad Astra", 18.4500001, 16.5)
    moved = make_check_id("Pizza Express", "Pollo ad Astra", 18.45, 17.00)
    assert first == same
    assert first != moved


def test_pollo_ad_astra_ties_the_gluten_free_card() -> None:
    cases = collect_smell_cases()
    pollo = next(
        case
        for case in cases
        if case.company == "Pizza Express" and case.item == "Pollo ad Astra"
    )
    assert pollo.restaurant == 18.45
    assert pollo.delivery == 16.50
    assert any("Gluten-Free Pollo ad Astra" in tie for tie in pollo.price_ties)


def test_appletiser_has_no_tied_variant_card() -> None:
    cases = collect_smell_cases()
    appletiser = next(
        case for case in cases if case.company == "Zizzi" and case.item == "Appletiser"
    )
    assert appletiser.price_ties == ()
    text = evidence_text(appletiser).lower()
    assert "can" not in text
    assert "bottle" not in text


def test_prompt_asks_the_classifier_to_separate_format_from_specification() -> None:
    assert "mishap" in _SYSTEM_PROMPT
    assert "justified" in _SYSTEM_PROMPT
    assert "can on delivery" in _SYSTEM_PROMPT
    assert "Do not invent" in _SYSTEM_PROMPT


def test_parse_channel_verdict_rejects_an_unknown_label() -> None:
    verdict, reason = parse_channel_verdict(
        '{"verdict": "justified", "reason": "Delivery is the can."}'
    )
    assert verdict == ChannelVerdict.JUSTIFIED.value
    assert reason == "Delivery is the can."
    with pytest.raises(RuntimeError, match="Unexpected verdict"):
        parse_channel_verdict('{"verdict": "discount", "reason": "cheaper"}')
    assert set(CHANNEL_CHECK_SCHEMA["properties"]) == {"verdict", "reason"}
    assert CHANNEL_CHECK_SCHEMA["additionalProperties"] is False


def test_verdicts_round_trip_through_the_mappings_db(tmp_path: Path) -> None:
    db_path = tmp_path / "mappings.db"
    case = SmellCase(
        check_id="abc123",
        company="Zizzi",
        item="Appletiser",
        bucket="Drinks (non-alcoholic)",
        restaurant=4.25,
        delivery=2.95,
        as_of="2026-07-08",
        restaurant_description="",
        delivery_description="",
        restaurant_siblings=(),
        delivery_siblings=(),
        price_ties=(),
    )
    upsert_channel_check(
        check_id=case.check_id,
        company=case.company,
        item=case.item,
        restaurant=case.restaurant,
        delivery=case.delivery,
        verdict=JUSTIFIED,
        reason="Same named drink, and no other card explains the price.",
        model="test-model",
        db_path=db_path,
    )
    stored = load_channel_checks(db_path)
    assert stored.loc[0, "verdict"] == JUSTIFIED
    with pytest.raises(ValueError, match="Unknown channel verdict"):
        upsert_channel_check(
            check_id="nope",
            company="Zizzi",
            item="Appletiser",
            restaurant=4.25,
            delivery=2.95,
            verdict="discount",
            reason="no",
            model="test-model",
            db_path=db_path,
        )
