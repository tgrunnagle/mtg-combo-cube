"""Tests for the outcome category table: parsing, matching and the shipped default."""

import json
import math
from pathlib import Path

import pytest

from mtg_combo_cube.ilp.cube_evaluation import combos_per_outcome, popularity_stats
from mtg_combo_cube.ilp.ilp_models import ComboData
from mtg_combo_cube.ilp.outcomes import (
    DEFAULT_OUTCOME_CATEGORIES_PATH,
    OutcomeCategories,
    OutcomeCategory,
    OutcomeCategoryError,
    load_outcome_categories,
    parse_outcome_categories,
)

TABLE = {
    "mana": ["infinite colored mana", "infinite colorless mana"],
    "damage": {"patterns": ["infinite damage"], "min_combos": 3},
    "lock": ["re:^lock"],
}


def categories() -> OutcomeCategories:
    return parse_outcome_categories(TABLE)


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


class TestParse:
    def test_every_category_needs_a_pattern(self):
        with pytest.raises(OutcomeCategoryError, match="'mana' has no patterns"):
            parse_outcome_categories({"mana": []})
        with pytest.raises(OutcomeCategoryError, match="'mana' has an empty pattern"):
            OutcomeCategory("mana", ("",))

    @pytest.mark.parametrize(
        ("table", "message"),
        [
            ([], "must be a JSON object"),
            ({}, "table is empty"),
            ({"a": "infinite mana"}, "must list its patterns"),
            ({"a": [1]}, "must list its patterns as strings"),
            ({"a": {"patterns": ["x"], "minimum": 1}}, "unknown keys"),
            ({"a": {"patterns": ["x"], "min_combos": "3"}}, "non-integer minimum"),
            ({"a": {"patterns": ["x"], "min_combos": True}}, "non-integer minimum"),
            ({"a": {"patterns": ["x"], "min_combos": -1}}, "negative minimum"),
            ({"a": ["re:("]}, "invalid regex"),
        ],
    )
    def test_invalid_tables_are_rejected(self, table: object, message: str):
        with pytest.raises(OutcomeCategoryError, match=message):
            parse_outcome_categories(table)

    def test_duplicate_names_are_rejected(self):
        with pytest.raises(OutcomeCategoryError, match="duplicate"):
            OutcomeCategories((OutcomeCategory("a", ("x",)), OutcomeCategory("a", ("y",))))


class TestLoad:
    def test_loads_a_file(self, tmp_path: Path):
        path = tmp_path / "outcomes.json"
        path.write_text(json.dumps(TABLE), encoding="utf-8")

        table = load_outcome_categories(str(path))

        assert table.names == ("mana", "damage", "lock")
        assert table.categories[1].min_combos == 3

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_outcome_categories(str(tmp_path / "missing.json"))

    def test_invalid_json(self, tmp_path: Path):
        path = tmp_path / "outcomes.json"
        path.write_text("{", encoding="utf-8")

        with pytest.raises(OutcomeCategoryError, match="not valid JSON"):
            load_outcome_categories(str(path))

    def test_default_table_ships_with_the_repository(self):
        table = load_outcome_categories()

        assert Path(DEFAULT_OUTCOME_CATEGORIES_PATH).exists()
        assert {"mana", "damage", "tokens", "draw", "mill", "lifegain", "counters"} <= set(
            table.names
        )
        assert all(category.patterns for category in table)
        # Trigger loops are not an outcome; terminal results are
        assert table.categorize(["Infinite creature ETB", "Infinite death triggers"]) == set()
        assert table.categorize(["Infinite colored mana", "Infinite creature ETB"]) == {"mana"}
        assert table.categorize(["Infinite lifegain triggers"]) == set()
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
