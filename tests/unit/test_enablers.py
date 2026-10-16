"""Tests for the enabler table: parsing, the shipped default and the resolved enabler set."""

import pytest

from mtg_combo_cube.config import load_config
from mtg_combo_cube.ilp.enablers import (
    EnablerCategory,
    EnablerTableError,
    parse_enabler_table,
    resolve_enablers,
)

TABLE = {
    "ritual": {"queries": ["o:ritual"], "exclude": ["Reprint // Ritual"]},
    "wheel": {"cards": ["Wheel of Misfortune", "Memory Jar"], "queries": ["o:wheel"]},
    "graveyard": {"cards": ["Entomb"], "min_enablers": 1, "max_enablers": 2},
}


class TestParse:
    def test_long_form(self):
        definitions = parse_enabler_table(TABLE)

        assert definitions.names == ("ritual", "wheel", "graveyard")
        assert len(definitions) == 3
        assert definitions.queries == ("o:ritual", "o:wheel")
        assert definitions.cards == {"Wheel of Misfortune", "Memory Jar", "Entomb"}
        assert definitions.min_enablers == {"graveyard": 1}
        assert definitions.max_enablers == {"graveyard": 2}
        ritual, _, graveyard = definitions
        assert ritual == EnablerCategory(
            "ritual", queries=("o:ritual",), exclude=frozenset(["Reprint // Ritual"])
        )
        assert isinstance(graveyard, EnablerCategory)
        assert graveyard.min_enablers == 1 and graveyard.max_enablers == 2

    @pytest.mark.parametrize(
        ("table", "message"),
        [
            ({}, "the enabler table is empty"),
            ({"ritual": {}}, "enabler category 'ritual' has no queries, cards or exclusions"),
            ({"ritual": {"query": ["x"]}}, "unknown keys"),
            ({"ritual": {"cards": ["A"], "min_enablers": True}}, "min_enablers as a whole number"),
            (
                {"ritual": {"cards": ["A"], "min_enablers": 3, "max_enablers": 2}},
                "above max_enablers",
            ),
        ],
    )
    def test_invalid_tables_are_rejected(self, table: object, message: str):
        with pytest.raises(EnablerTableError, match=message):
            parse_enabler_table(table)


class TestDefaultTable:
    def test_default_table_has_the_five_classes(self):
        definitions = load_config().enablers

        assert definitions is not None
        assert definitions.names == (
            "fast_mana",
            "ritual",
            "wheel",
            "graveyard",
            "recursion",
            "top_deck",
        )
        # Fast mana and rituals come from queries; the wheels and graveyard fillers are
        # named, since a query also finds cards nobody wants in the slot
        fast_mana, ritual, wheel, graveyard, recursion, top_deck = definitions
        assert fast_mana.queries and ritual.queries
        assert not wheel.queries and not graveyard.queries
        assert wheel.require == ("Wheel of Fortune",)
        assert {"Windfall", "Memory Jar"} <= set(wheel.cards)
        assert {"Entomb", "Buried Alive", "Faithless Looting"} <= set(graveyard.cards)
        # The recursion engine is required and is the whole category, with no floor of its own
        assert recursion.require == ("Underworld Breach",)
        assert recursion.minimum == 0 and recursion.maximum == 0
        # So is Bolas's Citadel, the top-of-library engine the user wants in the cube
        assert top_deck.require == ("Bolas's Citadel",)
        assert top_deck.minimum == 0 and top_deck.maximum == 0
        assert definitions.required == {"Wheel of Fortune", "Underworld Breach", "Bolas's Citadel"}
        # The suspend rocks and the double-faced reprint are excluded, not re-queried
        assert {"Sol Talisman", "Mox Tantalite"} <= fast_mana.exclude
        assert "Blazing Firesinger // Seething Song" in ritual.exclude
        assert definitions.min_enablers == {"recursion": 0, "top_deck": 0}
        assert definitions.max_enablers == {"recursion": 0, "top_deck": 0}


class TestResolve:
    DEFINITIONS = parse_enabler_table(TABLE)
    QUERIES = {
        "o:ritual": ["Dark Ritual", "Reprint // Ritual", "Blocked", "Seething Song"],
        "o:wheel": ["Windfall", "Wheel of Fortune", "Memory Jar"],
    }

    def test_union_of_sources_minus_exclusions_and_blocklist(self):
        enablers = resolve_enablers(
            self.DEFINITIONS, self.QUERIES, blocklist=frozenset(["Blocked"])
        )

        assert enablers.names == ("ritual", "wheel", "graveyard")
        assert enablers.cards("ritual") == {"Dark Ritual", "Seething Song"}
        assert enablers.sources["ritual"] == {
            "Dark Ritual": {"query"},
            "Seething Song": {"query"},
        }
        assert enablers.cards("wheel") == {
            "Windfall",
            "Wheel of Fortune",
            "Memory Jar",
            "Wheel of Misfortune",
        }
        assert enablers.sources["wheel"]["Memory Jar"] == {"card", "query"}
        assert enablers.source_counts("wheel") == {"card": 2, "query": 3}
        assert enablers.cards("graveyard") == {"Entomb"}
        assert enablers.all_cards == (
            enablers.cards("ritual") | enablers.cards("wheel") | enablers.cards("graveyard")
        )
        assert enablers.min_enablers == {"graveyard": 1}
        assert enablers.max_enablers == {"graveyard": 2}

    def test_query_limit_and_missing_results(self):
        enablers = resolve_enablers(
            self.DEFINITIONS, {"o:ritual": self.QUERIES["o:ritual"]}, query_limit=1
        )

        assert enablers.cards("ritual") == {"Dark Ritual"}
        assert enablers.cards("wheel") == {"Wheel of Misfortune", "Memory Jar"}

    def test_stats_and_without(self):
        enablers = resolve_enablers(self.DEFINITIONS, self.QUERIES)

        stats = enablers.stats(
            ["Dark Ritual", "Windfall", "Other"], enabler_only=["Windfall", "Entomb"]
        )

        assert stats.cards_per_category == {"ritual": 1, "wheel": 1, "graveyard": 0}
        assert stats.cards == {
            "ritual": {"Dark Ritual": ["query"]},
            "wheel": {"Windfall": ["query"]},
            "graveyard": {},
        }
        assert stats.enabler_only == ["Windfall"]  # of the cube's cards only
        smaller = enablers.without(["Dark Ritual", "Entomb", "Not There"])
        assert smaller.cards("ritual") == {"Seething Song", "Blocked"}  # no blocklist here
        assert smaller.cards("graveyard") == frozenset()
        assert smaller.min_enablers == {"graveyard": 1}
