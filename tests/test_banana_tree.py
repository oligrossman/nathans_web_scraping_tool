"""Unit tests for Banana Tree menu extraction (no network / browser needed)."""

from src.scrapers.banana_tree import BananaTreeScraper, _combine_name


def test_combine_name_prefixes_bare_protein_variant() -> None:
    assert _combine_name("Thai Massaman Curry", "Beef") == "Thai Massaman Curry - Beef"


def test_combine_name_keeps_descriptive_variant_when_it_contains_dish() -> None:
    assert _combine_name("Noodles", "Chicken Singapore Noodles") == (
        "Chicken Singapore Noodles"
    )


def test_combine_name_falls_back_to_available_side() -> None:
    assert _combine_name(None, "Fries") == "Fries"
    assert _combine_name("Sides", None) == "Sides"


def test_build_items_flattens_byo_variants_and_dedupes() -> None:
    data = {
        "standalone": [
            {"name": "Prawn Cracker Basket", "price": "3.95", "desc": None},
            # Masonry layout renders the menu twice; the duplicate must collapse.
            {"name": "Prawn Cracker Basket", "price": "3.95", "desc": None},
        ],
        "byo": [
            {
                "dish": "Thai Massaman Curry",
                "desc": "A rich, mild curry.",
                "variants": [
                    {"name": "Beef", "price": "18.65"},
                    {"name": "Chicken", "price": "17.95"},
                    # Included side with no price should be dropped.
                    {"name": "Steamed Jasmine Rice", "price": None},
                ],
            }
        ],
    }

    items = BananaTreeScraper._build_items(data)
    named = {(item.name, item.price) for item in items}

    assert ("Prawn Cracker Basket", 3.95) in named
    assert ("Thai Massaman Curry - Beef", 18.65) in named
    assert ("Thai Massaman Curry - Chicken", 17.95) in named
    assert len(items) == 3  # one standalone (deduped) + two priced variants
