"""Board-level menu buckets and the price-history column contract.

Scraped category labels differ by chain. ``resolve_bucket`` collapses them into
the buckets the charts use. Drinks are two buckets — alcoholic and
non-alcoholic — because a single "Drinks" average mixes a £3 coke with a £30
bottle of wine.

A category is trusted when it is a real section heading ("Soft drinks",
"RED WINE"). Some scrapers write the previous dish into the category column
instead. ``assign_buckets`` detects that and classifies the dish name.
"""

from __future__ import annotations

import re

import pandas as pd

ALCOHOLIC_DRINKS = "Drinks (alcoholic)"
NON_ALCOHOLIC_DRINKS = "Drinks (non-alcoholic)"
# The old single drinks bucket. Stored history still uses this until it is
# reclassified; new rows never write it.
LEGACY_DRINKS_BUCKET = "Drinks"

# Food courses, then the two drink courses, then desserts. Charts that sort
# alphabetically still keep the two drink labels next to each other.
BUCKETS = (
    "Starters",
    "Mains",
    "Sides",
    NON_ALCOHOLIC_DRINKS,
    ALCOHOLIC_DRINKS,
    "Desserts",
)

# Column contract for the tidy history frame (also used for empty results).
HISTORY_COLUMNS = ["date", "source", "item", "dish_id", "bucket", "price"]

# Food section headings only. Drink headings are detected separately so the
# dish name can split alcoholic from non-alcoholic.
CATEGORY_TO_BUCKET: dict[str, str] = {
    # Nando's
    "Starters": "Starters",
    "PERi-PERi Chicken": "Mains",
    "Burgers, Pittas, Wraps": "Mains",
    "Salads & Bowls": "Mains",
    "Sharing Platters": "Mains",
    "Veggie": "Mains",
    "Nandinos (Kids)": "Mains",
    "Sides": "Sides",
    "Dips & Extras": "Sides",
    "Desserts": "Desserts",
    # Pizza Express
    "Starters & Sides": "Starters",
    "Dough Balls": "Starters",
    "Meal Deals": "Mains",
    "🏆 Matchday Winners 🏆": "Mains",
    "PIZZA": "Mains",
    "ROMANA PIZZA": "Mains",
    "CLASSIC PIZZA": "Mains",
    "LEGGERA": "Mains",
    "GLUTEN-FREE": "Mains",
    "Pasta": "Mains",
    "Salads": "Mains",
    "Mains": "Mains",
    "Kids": "Mains",
    "Dips": "Sides",
    "Pizza": "Mains",
    "Gluten-Free": "Mains",
    # Banana Tree / Giggling Squid (K10)
    "Small Plates": "Starters",
    "Curries": "Mains",
    "Noodles & Rice": "Mains",
    "Chef's Specials": "Mains",
    "Bao Buns": "Mains",
    "Dessert": "Desserts",
    # Five Guys / Wagamama
    "Burgers": "Mains",
    "Beef Dogs": "Mains",
    "Sandwiches": "Mains",
    "Fries": "Sides",
    "Ramen": "Mains",
    "Curry": "Mains",
    "Teppan": "Mains",
    "Donburi": "Mains",
    "Salads & Sides": "Sides",
    # Prezzo
    "Antipasti": "Starters",
    "Mains & Grill": "Mains",
    "Dolci": "Desserts",
    # Zizzi
    "Pizza & Calzone": "Mains",
    "Sides & Salads": "Sides",
    # Pho
    "Classics": "Mains",
    "Noodles": "Mains",
    "Rice": "Mains",
    # Rosa's Thai
    "Stir Fries": "Mains",
}

_EXACT_FOOD = {label.casefold(): bucket for label, bucket in CATEGORY_TO_BUCKET.items()}

# A heading that mentions a drink course. "soft" alone is too wide ("soft shell").
_DRINK_SECTION_HINTS = (
    "drink",
    "beer",
    "cider",
    "wine",
    "cocktail",
    "coffee",
    "tea",
    "juice",
    "mocktail",
    "spirit",
    "liqueur",
    "fizz",
    "milkshake",
    "lemonade",
    "softs",
)

# These phrases mean "no alcohol" even when the name also says beer or IPA.
_ZERO_ALCOHOL_PHRASES = (
    "alcohol-free",
    "alcohol free",
    "non-alcoholic",
    "non alcoholic",
    "ginger ale",
    "abv 0",
    "0.0",
    "0,0",
    "crodino",
)

# A drink word that should not survive a soft-drink name ("rose lemonade").
_SOFT_ITEM_HINTS = (
    "juice",
    "lemonade",
    "coke",
    "cola",
    "fanta",
    "sprite",
    "appletiser",
    "kombucha",
    "tonic",
    "squash",
    "smoothie",
    "mocktail",
    "milkshake",
    "soda",
    "cioccolata",
    "hot chocolate",
    "acqua panna",
    "san pellegrino",
    "latte",
    "espresso",
    "cappuccino",
    "americano",
    "macchiato",
    "flat white",
    "mocha",
    "coffee",
    "shake",
)

_MEAL_HINTS = (
    "burger",
    "hot dog",
    "cheese dog",
    "bacon dog",
    "grilled cheese",
    "sandwich",
    "pizza",
    "pasta",
    "ramen",
    "curry",
    "calzone",
    "noodle",
    "gyoza",
    "bao",
)

_DESSERT_HINTS = (
    "brownie",
    "cheesecake",
    "gelato",
    "ice cream",
    "sundae",
    "mousse",
    "blondie",
    "cookie",
    "mochi",
    "doughnut",
    "donut",
    "churros",
    "tiramisu",
    "panna cotta",
    "affogato",
    "cake",
)

_FOOD_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("starter", "Starters"),
    ("antipasti", "Starters"),
    ("small plate", "Starters"),
    ("aperitivo", "Starters"),
    ("dough ball", "Starters"),
    ("side", "Sides"),
    ("fries", "Sides"),
    ("dessert", "Desserts"),
    ("dolci", "Desserts"),
    ("pizza", "Mains"),
    ("pasta", "Mains"),
    ("burger", "Mains"),
    ("ramen", "Mains"),
    ("curry", "Mains"),
    ("noodle", "Mains"),
    ("calzone", "Mains"),
    ("al forno", "Mains"),
    ("grill", "Mains"),
    ("main", "Mains"),
)

# Strong alcohol signals. "gin" and "tea" and "ale" are matched as whole words
# so "ginger", "steak" and "kale" are not drinks.
_ALCOHOL_PATTERN = re.compile(
    r"\b("
    r"beer|beers|cider|wine|wines|cocktail|cocktails|prosecco|champagne|cava|"
    r"spirits?|liqueurs?|liquors?|whisk(?:y|ey)|vodka|gin|rum|tequila|"
    r"aperol|spritz|martini|mojito|daiquiri|margarita|negroni|baileys|"
    r"merlot|malbec|shiraz|pinot|sauvignon|chardonnay|riesling|"
    r"gavi|picpoul|montepulciano|primitivo|zinfandel|trebbiano|catarratto|"
    r"soave|chianti|tempranillo|cabernet|syrah|blush|"
    r"lager|ipa|pils|pilsner|stout|perry|"
    r"pale ale|amber ale|ale|"
    r"peroni|asahi|beavertown|hawkstone|sxollie|"
    r"ros[eé]|g&t"
    r")\b",
    re.IGNORECASE,
)
_SOFT_WORD = re.compile(
    r"\b(tea|water|shake|coffee|soda|juice|lemonade)\b",
    re.IGNORECASE,
)
_ZERO_WORD = re.compile(r"\bzero\b", re.IGNORECASE)
_ABV = re.compile(r"abv\s*(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)


def _clean(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _has_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    """True when a phrase appears, as a whole word when it is a single word.

    ``"latte" in "platter"`` is true and wrong. A single word has to sit on its
    own. A multi-word phrase ("ginger ale", "ice cream") is specific enough to
    match as text.
    """
    folded = text.casefold()
    for phrase in phrases:
        if " " in phrase or any(mark in phrase for mark in "-&%/."):
            if phrase in folded:
                return True
            continue
        if re.search(rf"\b{re.escape(phrase)}\b", folded):
            return True
    return False


def _zero_alcohol(text: str) -> bool:
    if _has_phrase(text, _ZERO_ALCOHOL_PHRASES) or _ZERO_WORD.search(text):
        return True
    match = _ABV.search(text)
    return match is not None and float(match.group(1)) == 0.0


def _strong_alcohol(text: str) -> bool:
    """True when the name itself is an alcoholic drink.

    A soft word such as lemonade blocks a weak token ("rose") but not a
    listed alcohol word that sits beside it ("vodka" with juice). An explicit
    zero-alcohol marker is handled by the caller before this runs.
    """
    if not text:
        return False
    if _SOFT_WORD.search(text) or _has_phrase(text, _SOFT_ITEM_HINTS):
        if _ALCOHOL_PATTERN.search(text) and not _has_phrase(
            text, ("lemonade", "juice", "soda", "coke", "cola", "squash")
        ):
            return True
        # "rose lemonade" is soft. "vodka with juice" is not.
        if re.search(
            r"\b(vodka|gin|rum|wine|beer|cider|prosecco|cocktail|spirits?|liqueurs?|whisky|whiskey|tequila)\b",
            text,
            re.IGNORECASE,
        ):
            return True
        return False
    if _ABV.search(text) and float(_ABV.search(text).group(1)) > 0:
        return True
    return _ALCOHOL_PATTERN.search(text) is not None


def _soft_drink(text: str) -> bool:
    if not text:
        return False
    return _has_phrase(text, _SOFT_ITEM_HINTS) or _SOFT_WORD.search(text) is not None


def _is_meal_name(name: str) -> bool:
    return _has_phrase(name, _MEAL_HINTS)


def _is_dessert_name(name: str) -> bool:
    return _has_phrase(name, _DESSERT_HINTS)


# A dish can mention tea or water and still be food ("tea-stained egg",
# "waterfall beef"). Those words only make a drink when the name is not a plate.
_FOOD_CONTEXT = (
    "egg",
    "chicken",
    "beef",
    "prawn",
    "pork",
    "duck",
    "tofu",
    "noodle",
    "rice",
    "salad",
    "soup",
    "gyoza",
    "steak",
)


def _is_drink_name(name: str) -> bool:
    if not name:
        return False
    if _has_phrase(name, _FOOD_CONTEXT) and not _strong_alcohol(name):
        return False
    return _zero_alcohol(name) or _strong_alcohol(name) or _soft_drink(name)


def _section_kind(category: str) -> str | None:
    """Return a course, ``DRINKS``, ``MIXED``, or None when this is not a heading.

    None means the text is a place name or a dish, not a section. The caller
    then classifies the dish name instead of the category.
    """
    if not category:
        return None
    exact = _EXACT_FOOD.get(category.casefold())
    if exact is not None:
        return exact
    folded = category.casefold()
    has_dessert = "dessert" in folded or "dolci" in folded
    has_drink = any(hint in folded for hint in _DRINK_SECTION_HINTS)
    if has_dessert and has_drink:
        return "MIXED"
    if has_drink:
        return "DRINKS"
    if has_dessert:
        return "Desserts"
    for keyword, bucket in _FOOD_KEYWORDS:
        if keyword in folded:
            return bucket
    return None


def _pure_soft_section(category: str) -> bool:
    """A heading that is only soft drinks, not "beer + alcohol-free"."""
    folded = category.casefold()
    soft = any(
        hint in folded
        for hint in (
            "soft",
            "mocktail",
            "juice",
            "coffee",
            "tea",
            "lemonade",
            "milkshake",
            "smoothie",
        )
    )
    alcohol = any(
        hint in folded
        for hint in ("beer", "wine", "cocktail", "spirit", "cider", "fizz", "liqueur")
    )
    return soft and not alcohol


def _alcohol_section(category: str) -> bool:
    folded = category.casefold()
    return any(
        hint in folded
        for hint in (
            "beer",
            "cider",
            "wine",
            "cocktail",
            "spirit",
            "liqueur",
            "fizz",
            "prosecco",
            "champagne",
        )
    )


def split_drink(category: object, item: object | None = None) -> str:
    """Choose the alcoholic or non-alcoholic drinks bucket.

    Explicit zero-alcohol wording on the dish ("0.0", "alcohol free") wins
    over a beer heading. A soft-drinks heading then wins over a playful
    cocktail name. Otherwise the dish name, then the heading, decide.
    """
    cat = _clean(category)
    name = _clean(item)
    if _zero_alcohol(name):
        return NON_ALCOHOLIC_DRINKS
    if _strong_alcohol(name):
        return ALCOHOLIC_DRINKS
    if _pure_soft_section(cat) or (_soft_drink(name) and not _alcohol_section(cat)):
        return NON_ALCOHOLIC_DRINKS
    if _alcohol_section(cat) or _strong_alcohol(cat):
        return ALCOHOLIC_DRINKS
    if _soft_drink(name) or _soft_drink(cat) or _pure_soft_section(cat):
        return NON_ALCOHOLIC_DRINKS
    # A generic "Drinks" heading with no alcohol signal (water, a mixer).
    return NON_ALCOHOLIC_DRINKS


def resolve_bucket(category: object, item: object | None = None) -> str:
    """Map a menu heading, and optionally the dish name, to a board bucket.

    Pass the dish name whenever you have it. A heading such as "Drinks" or
    "kids drinks + desserts" is not enough on its own: the name decides
    alcoholic versus non-alcoholic, and pulls a brownie or a kids meal back
    out of a mixed heading.
    """
    cat = _clean(category)
    name = _clean(item)
    section = _section_kind(cat)

    if section == "MIXED":
        if _is_dessert_name(name) and not _has_phrase(name, _FOOD_CONTEXT):
            return "Desserts"
        if _is_meal_name(name) and not _strong_alcohol(name):
            return "Mains"
        return split_drink(cat, name)

    if section == "DRINKS":
        # "Pizza Pale" and "Pasta Beer" are drinks. A kids burger is not.
        if (
            _is_dessert_name(name)
            and not _strong_alcohol(name)
            and not _has_phrase(name, _FOOD_CONTEXT)
        ):
            return "Desserts"
        if _is_meal_name(name) and not _strong_alcohol(name) and not _zero_alcohol(name):
            return "Mains"
        return split_drink(cat, name)

    if section in {"Starters", "Sides"} and _is_drink_name(name) and not _is_meal_name(name):
        # An aperitivo list can hold one spritz next to the olives.
        return split_drink(cat, name)

    if section is not None:
        return section

    if _is_dessert_name(name) and not _has_phrase(name, _FOOD_CONTEXT):
        return "Desserts"
    if _is_fries_dish(name):
        return "Sides"
    if _is_drink_name(name) and not _is_meal_name(name):
        return split_drink("", name)
    return "Mains"


def _is_fries_dish(name: str) -> bool:
    """True when the dish is the fries, not a main that is served with fries."""
    folded = _clean(name).casefold()
    if not folded or " with " in folded or " and " in folded:
        return False
    return folded == "fries" or folded.endswith(" fries") or folded.startswith("fries")


def assign_buckets(
    menu: pd.DataFrame,
    *,
    category_col: str = "category",
    item_col: str = "name",
) -> pd.Series:
    """Bucket every row of a scraped menu.

    A category value that appears once and is also a dish name on the same
    menu is a shifted column (the previous item leaked into the heading).
    Those rows are classified from the dish name. A heading used by many
    dishes ("Pizza", "Drinks") is a real section and is trusted.
    """
    if menu.empty:
        return pd.Series(dtype="object")

    if item_col in menu.columns:
        items = menu[item_col]
    else:
        items = pd.Series([None] * len(menu), index=menu.index)
    if category_col in menu.columns:
        categories = menu[category_col]
    else:
        categories = pd.Series([None] * len(menu), index=menu.index)

    known_items = {_clean(value).casefold() for value in items.tolist() if _clean(value)}
    folded = [_clean(value).casefold() for value in categories.tolist()]
    counts: dict[str, int] = {}
    for value in folded:
        if value:
            counts[value] = counts.get(value, 0) + 1

    buckets: list[str] = []
    for category, item, fold in zip(categories.tolist(), items.tolist(), folded):
        shifted = bool(fold) and fold in known_items and counts.get(fold, 0) == 1
        if shifted:
            buckets.append(resolve_bucket(None, item))
        else:
            buckets.append(resolve_bucket(category, item))
    return pd.Series(buckets, index=menu.index, dtype="object")


def keep_legacy_drink(item: object, classified: str) -> str:
    """Keep a vague old Drinks row as a soft drink.

    ``"Pistachio"`` was filed under Drinks and the name alone does not say
    shake, so the new rules would call it a main. A kids burger that was
    mis-filed under Drinks is a meal and is allowed to leave.
    """
    if classified != "Mains":
        return classified
    name = _clean(item)
    if _is_meal_name(name) or (_is_dessert_name(name) and not _has_phrase(name, _FOOD_CONTEXT)):
        return classified
    return NON_ALCOHOLIC_DRINKS


def migrate_bucket_list(buckets: list[str]) -> list[str]:
    """Replace a saved "Drinks" filter with the two drink buckets."""
    migrated: list[str] = []
    for bucket in buckets:
        replacements = (
            [NON_ALCOHOLIC_DRINKS, ALCOHOLIC_DRINKS]
            if bucket == LEGACY_DRINKS_BUCKET
            else [bucket]
        )
        for name in replacements:
            if name not in migrated:
                migrated.append(name)
    return migrated
