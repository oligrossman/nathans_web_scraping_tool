"""Constrained vocabulary and schemas for dish-level menu enrichment."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Cuisine(str, Enum):
    JAPANESE = "Japanese"
    PAN_ASIAN = "PanAsian"
    THAI = "Thai"
    CHINESE = "Chinese"
    INDIAN = "Indian"
    ITALIAN = "Italian"
    BRITISH = "British"
    AMERICAN = "American"
    MEXICAN = "Mexican"
    MEDITERRANEAN = "Mediterranean"
    MIDDLE_EASTERN = "MiddleEastern"
    OTHER = "Other"


class DishType(str, Enum):
    """The kind of dish, independent of course or restaurant."""

    NOODLES = "Noodles"
    RICE = "Rice"
    CURRY = "Curry"
    SOUP = "Soup"
    PIZZA = "Pizza"
    PASTA = "Pasta"
    BURGER = "Burger"
    WRAP = "Wrap"
    SALAD = "Salad"
    GRILL = "Grill"
    STIR_FRY = "StirFry"
    DUMPLINGS = "Dumplings"
    SUSHI = "Sushi"
    SMALL_PLATE = "SmallPlate"
    DESSERT = "Dessert"
    DRINK = "Drink"
    SIDE = "Side"
    OTHER = "Other"


class ChannelVerdict(str, Enum):
    """Whether a cheaper delivery price is a fair comparison of the joined dish."""

    MISHAP = "mishap"
    JUSTIFIED = "justified"
    UNCERTAIN = "uncertain"


class GroupingDimension(str, Enum):
    """Analytics grouping axis selectable in the UI."""

    BUCKET = "bucket"
    CUISINE = "cuisine"
    DISH_TYPE = "dish_type"

    @property
    def label(self) -> str:
        return {
            GroupingDimension.BUCKET: "Course (bucket)",
            GroupingDimension.CUISINE: "Cuisine",
            GroupingDimension.DISH_TYPE: "Dish type",
        }[self]


UNMAPPED_LABEL = "Unmapped"


@dataclass(slots=True)
class DishMapping:
    """Resolved attributes for one menu item.

    ``dish_id`` is unique per (restaurant, dish); ``source`` is the restaurant.
    """

    dish_id: str
    source: str
    name: str
    bucket: str
    cuisine: str
    dish_type: str
    is_vegetarian: bool
    is_vegan: bool
    is_override: bool = False

    @classmethod
    def from_llm_payload(
        cls,
        *,
        dish_id: str,
        source: str,
        name: str,
        bucket: str,
        payload: dict[str, Any],
    ) -> DishMapping:
        return cls(
            dish_id=dish_id,
            source=source,
            name=name,
            bucket=bucket,
            cuisine=str(payload["cuisine"]),
            dish_type=str(payload["dish_type"]),
            is_vegetarian=bool(payload["is_vegetarian"]),
            is_vegan=bool(payload["is_vegan"]),
        )


@dataclass(slots=True)
class MarketDefinition:
    """Filter definition for a custom market benchmark."""

    name: str
    cuisines: list[str] = field(default_factory=list)
    dish_types: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    buckets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "cuisines": self.cuisines,
            "dish_types": self.dish_types,
            "companies": self.companies,
            "buckets": self.buckets,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MarketDefinition:
        return cls(
            name=str(data["name"]),
            cuisines=list(data.get("cuisines") or []),
            dish_types=list(data.get("dish_types") or []),
            companies=list(data.get("companies") or []),
            buckets=list(data.get("buckets") or []),
        )


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    return [member.value for member in enum_cls]


DISH_CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cuisine": {"type": "string", "enum": _enum_values(Cuisine)},
        "dish_type": {"type": "string", "enum": _enum_values(DishType)},
        "is_vegetarian": {"type": "boolean"},
        "is_vegan": {"type": "boolean"},
    },
    "required": [
        "cuisine",
        "dish_type",
        "is_vegetarian",
        "is_vegan",
    ],
    "additionalProperties": False,
}

OPENAI_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "dish_classification",
        "strict": True,
        "schema": DISH_CLASSIFICATION_SCHEMA,
    },
}


CHANNEL_CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": _enum_values(ChannelVerdict)},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}


OPENAI_CHANNEL_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "channel_smell_check",
        "strict": True,
        "schema": CHANNEL_CHECK_SCHEMA,
    },
}
