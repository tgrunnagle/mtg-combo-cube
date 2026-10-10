"""Tests for the outcome category table: parsing, matching and the shipped default."""

import math

import pytest

from mtg_combo_cube.config import load_config
from mtg_combo_cube.ilp.cube_evaluation import combos_per_outcome, popularity_stats
from mtg_combo_cube.ilp.ilp_models import ComboData
from mtg_combo_cube.ilp.outcomes import (
    OutcomeCategories,
    OutcomeCategory,
    OutcomeCategoryError,
    outcome_rules_requested,
    parse_outcome_categories,
)

TABLE = {
    "mana": ["infinite colored mana", "infinite colorless mana"],
    "damage": {"patterns": ["infinite damage"], "min_combos": 3},
    "lock": ["re:^lock"],
}


def categories() -> OutcomeCategories:
    return parse_outcome_categories(TABLE)


def default_table() -> OutcomeCategories:
    """The outcome category table of the shipped configuration file."""
    table = load_config().outcome_categories
    assert table is not None
    return table


class TestCategorize:
    def test_substring_match_is_case_insensitive(self):
        assert categories().categorize(["Near-infinite colored mana"]) == {"mana"}
        assert categories().categorize(["INFINITE DAMAGE to one opponent"]) == {"damage"}

    def test_overlapping_patterns_place_a_combo_in_every_matching_category(self):
        features = ["Infinite colored mana", "Infinite damage", "Infinite creature ETB"]

        assert categories().categorize(features) == {"mana", "damage"}

    def test_no_match_is_empty(self):
        assert categories().categorize(["Infinite creature ETB"]) == frozenset()
        assert categories().categorize([]) == frozenset()

    def test_regex_prefix(self):
        assert categories().categorize(["Lock"]) == {"lock"}
        # A substring pattern "lock" would match "block"; the anchored regex does not
        assert categories().categorize(["Creatures can't block"]) == frozenset()

    def test_names_keep_table_order(self):
        assert categories().names == ("mana", "damage", "lock")
        assert len(categories()) == 3


class TestMinimums:
    def test_default_applies_unless_the_table_overrides(self):
        assert categories().minimums(5) == {"mana": 5, "damage": 3, "lock": 5}

    def test_zero_default_keeps_only_the_table_minimums(self):
        assert categories().minimums(0) == {"damage": 3}

    def test_table_minimum_of_zero_disables_the_category(self):
        table = parse_outcome_categories({"a": {"patterns": ["x"], "min_combos": 0}, "b": ["y"]})

        assert table.minimums(2) == {"b": 2}


class TestCatchAll:
    def test_a_category_without_patterns_holds_the_unmatched_combos(self):
        table = parse_outcome_categories({"mana": ["infinite colored mana"], "other": []})

        assert table.catch_all == "other"
        assert table.categorize(["Infinite creature ETB"]) == {"other"}
        assert table.categorize([]) == {"other"}
        # A combo in a named category is not in the catch-all
        assert table.categorize(["Infinite colored mana", "Infinite creature ETB"]) == {"mana"}
        assert table.minimums(5) == {"mana": 5, "other": 5}

    def test_without_a_catch_all_unmatched_combos_are_in_no_category(self):
        assert categories().catch_all is None
        assert categories().categorize(["Infinite creature ETB"]) == frozenset()

    def test_one_catch_all_at_most(self):
        with pytest.raises(OutcomeCategoryError, match="more than one catch-all"):
            parse_outcome_categories({"a": [], "b": ["x"], "c": []})

    def test_default_table_has_one(self):
        table = default_table()

        assert table.catch_all == "other"
        assert table.names[-1] == "other"
        assert "triggers" in table.names


class TestParse:
    def test_patterns_must_not_be_empty_strings(self):
        with pytest.raises(OutcomeCategoryError, match="'mana' has an empty pattern"):
            OutcomeCategory("mana", ("",))

    @pytest.mark.parametrize(
        ("table", "message"),
        [
            ([], "must be a mapping of category name to patterns"),
            ({}, "table is empty"),
            ({"a": "infinite mana"}, "must list its patterns"),
            ({"a": [1]}, "must list its patterns as strings"),
            ({"a": {"patterns": ["x"], "minimum": 1}}, "unknown keys"),
            ({"a": {"patterns": ["x"], "min_combos": "3"}}, "non-integer minimum"),
            ({"a": {"patterns": ["x"], "min_combos": True}}, "non-integer minimum"),
            ({"a": {"patterns": ["x"], "min_combos": -1}}, "negative minimum"),
            ({"a": ["re:("]}, "invalid regex"),
            ({"a": ["re:"]}, "empty regex"),
            ({"a": ["re:  "]}, "empty regex"),
        ],
    )
    def test_invalid_tables_are_rejected(self, table: object, message: str):
        with pytest.raises(OutcomeCategoryError, match=message):
            parse_outcome_categories(table)

    def test_duplicate_names_are_rejected(self):
        with pytest.raises(OutcomeCategoryError, match="duplicate"):
            OutcomeCategories((OutcomeCategory("a", ("x",)), OutcomeCategory("a", ("y",))))


class TestDefaultTable:
    def test_default_table_excludes_damage_to_creatures_only(self):
        table = default_table()

        assert table.categorize(["Infinite damage"]) == {"damage"}
        assert table.categorize(["Near-infinite damage to one opponent"]) == {"damage"}
        assert table.categorize(["Infinite damage to creatures"]) == {"other"}
        assert table.categorize(["Near-infinite damage to all creatures"]) == {"other"}
        assert table.categorize(["Infinite damage to most creatures"]) == {"other"}

    def test_default_table_ships_with_the_repository(self):
        table = default_table()

        assert {"mana", "damage", "tokens", "draw", "mill", "lifegain", "counters"} <= set(
            table.names
        )
        assert all(category.patterns for category in table if not category.is_catch_all)
        # Creature trigger loops are the "triggers" engine; a result no category names is
        # in the catch-all
        assert table.categorize(["Infinite creature ETB", "Infinite death triggers"]) == {
            "triggers"
        }
        assert table.categorize(["Infinite colored mana", "Infinite creature ETB"]) == {
            "mana",
            "triggers",
        }
        assert table.categorize(["Infinite lifegain triggers"]) == {"other"}
        assert table.categorize(["Infinite landfall triggers"]) == {"other"}
        assert table.categorize(["Infinite lifegain"]) == {"lifegain"}
        assert table.categorize(["Lock"]) == {"lock"}


def combo(
    combo_id: str, cards: list[str], features: list[str], popularity: int = 1, group: str = ""
) -> ComboData:
    return ComboData(
        combo_id,
        frozenset(cards),
        [],
        popularity,
        group_key=group,
        features=frozenset(features),
    )


COMBOS = [
    combo("m1", ["M1", "M2"], ["Infinite colored mana", "Infinite creature ETB"], 100, "mana1"),
    # The same combo with a piece swapped: its features are the union over the group
    combo("m1b", ["M1", "M3"], ["Infinite colorless mana"], 50, "mana1"),
    combo("md", ["M1", "D1"], ["Infinite colored mana", "Infinite damage"], 10),
    combo("etb", ["E1", "E2"], ["Infinite creature ETB"], 0),
    combo("none", ["N1", "N2"], [], 1000),
]


class TestRulesRequested:
    @pytest.mark.parametrize(
        ("minimum", "share", "requested"),
        [(0, 0, False), (0, 1, False), (0, 0.001, False), (1, 0, True), (0, 0.5, True)],
    )
    def test_rules_requested(self, minimum: int, share: float, requested: bool):
        assert outcome_rules_requested(minimum, share) is requested


class TestCombosPerOutcome:
    def test_counts_distinct_combos_per_category_and_the_uncategorized(self):
        stats = combos_per_outcome({"M1", "M2", "M3", "D1", "E1", "E2"}, COMBOS, categories())

        # mana1 counts once though two variants are complete; md is in two categories
        assert stats.combos_per_outcome == {"mana": 2, "damage": 1, "lock": 0}
        assert stats.uncategorized == 1  # the ETB loop
        assert stats.total == 3

    def test_group_features_are_the_union_over_the_variants(self):
        # Only m1b is complete, but the group's features include m1's colored mana
        stats = combos_per_outcome({"M1", "M3"}, COMBOS, categories())

        assert stats.combos_per_outcome["mana"] == 1

    def test_completed_ids_can_be_given(self):
        stats = combos_per_outcome(set(), COMBOS, categories(), completed_ids=["md", "none"])

        assert stats.combos_per_outcome == {"mana": 1, "damage": 1, "lock": 0}
        assert stats.uncategorized == 1
        assert stats.total == 2

    def test_a_precomputed_categorization_can_be_given(self):
        # The caller's categorization is taken as is, so it can differ from the table's
        given = {
            "mana1": frozenset(["lock"]),
            "md": frozenset(),
            "etb": frozenset(),
            "none": frozenset(),
        }

        stats = combos_per_outcome({"M1", "M2", "D1"}, COMBOS, categories(), group_categories=given)

        assert stats.combos_per_outcome == {"mana": 0, "damage": 0, "lock": 1}
        assert stats.uncategorized == 1

    def test_empty_cube(self):
        stats = combos_per_outcome(set(), COMBOS, categories())

        assert stats.combos_per_outcome == {"mana": 0, "damage": 0, "lock": 0}
        assert stats.uncategorized == 0
        assert stats.total == 0


class TestPopularityStats:
    def test_distinct_combos_at_their_most_popular_variant(self):
        # Pool groups: mana1 100, md 10, etb 0, none 1000 -> median 55
        stats = popularity_stats({"M1", "M2", "M3", "D1", "E1", "E2"}, COMBOS)

        assert stats.combo_count == 3
        assert stats.pool_median_popularity == 55
        assert stats.median_popularity == 10  # 100, 10, 0
        assert stats.below_pool_median_share == pytest.approx(2 / 3)
        assert stats.mean_log_popularity == pytest.approx((math.log1p(100) + math.log1p(10)) / 3)

    def test_empty_cube(self):
        stats = popularity_stats(set(), COMBOS)

        assert stats.combo_count == 0
        assert stats.median_popularity == 0
        assert stats.below_pool_median_share == 0
        assert stats.pool_median_popularity == 55

    def test_no_combos(self):
        assert popularity_stats(set(), []).pool_median_popularity == 0
