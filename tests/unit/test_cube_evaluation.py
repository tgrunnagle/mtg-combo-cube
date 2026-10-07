"""Unit tests for the ground-truth cube evaluation functions."""

import pytest

from mtg_combo_cube.ilp.cube_evaluation import (
    card_utilization,
    completable_combo_ids,
    completable_group_keys,
    completed_group_sizes,
    compute_color_stats,
    compute_utilization_stats,
    largest_combo_groups,
    weighted_combo_count,
)
from mtg_combo_cube.ilp.ilp_models import ComboData, RequirementOption
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from tests.unit.test_ilp_optimizer import build_candidate_cards


def make_combo(
    combo_id: str,
    required: list[str],
    options: list[list[str]] | None = None,
    group: str = "",
) -> ComboData:
    return ComboData(
        id=combo_id,
        required_cards=frozenset(required),
        requirement_options=[
            RequirementOption(f"Template {i}", f"key:{sorted(cards)}", frozenset(cards))
            for i, cards in enumerate(options or [])
        ],
        popularity=1,
        group_key=group,
    )


GROUPED = [
    make_combo("h1", ["H", "P1"], group="big"),
    make_combo("h2", ["H", "P2"], group="big"),
    make_combo("h3", ["H"], [["S1", "S2"]], group="big"),
    make_combo("ab", ["A", "B"]),
    make_combo("cd", ["C", "D"]),
]


class TestComboGroups:
    """Test completable_group_keys, combo_group_sizes and largest_combo_groups."""

    def test_group_keys_in_order_of_first_completed_variant(self):
        assert completable_group_keys({"H", "P1", "P2", "A", "B"}, GROUPED) == ["big", "ab"]
        assert completable_group_keys({"A", "B", "H", "S2"}, GROUPED) == ["big", "ab"]
        assert completable_group_keys({"C", "D"}, GROUPED) == ["cd"]
        assert completable_group_keys(set(), GROUPED) == []

    def test_group_key_defaults_to_the_variant_id(self):
        assert GROUPED[3].group_key == "ab"
        assert GROUPED[0].group_key == "big"

    def test_group_sizes_count_completed_variants(self):
        assert completed_group_sizes(["h1", "h2", "h3", "ab"], GROUPED) == {"big": 3, "ab": 1}
        assert completed_group_sizes({"h1"}, GROUPED) == {"big": 1}
        assert completed_group_sizes([], GROUPED) == {}
        # Order of first completed variant, not of the ids given
        assert list(completed_group_sizes(["cd", "h2"], GROUPED)) == ["big", "cd"]

    def test_weighted_combo_count(self):
        sizes = {"big": 3, "ab": 1}

        assert weighted_combo_count(sizes, 1) == 4
        assert weighted_combo_count(sizes, 0) == 2
        assert weighted_combo_count(sizes, 0.5) == 3
        assert weighted_combo_count(sizes, 0.1) == pytest.approx(2.2)
        assert weighted_combo_count({}, 0.1) == 0

    def test_largest_groups_rank_by_variants_then_key(self):
        groups = largest_combo_groups(["H", "P1", "P2", "S2", "A", "B", "C", "D"], GROUPED)

        assert [(g.group_key, g.variant_count) for g in groups] == [
            ("big", 3),
            ("ab", 1),
            ("cd", 1),
        ]
        # Selected pool cards count, unselected ones (S1) do not
        assert groups[0].cards == ["H", "P1", "P2", "S2"]
        assert groups[1].cards == ["A", "B"]

    def test_largest_groups_limit(self):
        groups = largest_combo_groups(["H", "P1", "A", "B", "C", "D"], GROUPED, limit=2)

        # All three groups have one variant: ties are ordered by key
        assert [g.group_key for g in groups] == ["ab", "big"]

    def test_largest_groups_accepts_completed_ids(self):
        cards = ["H", "P1", "P2", "A", "B"]
        with_ids = largest_combo_groups(cards, GROUPED, completed_ids=["h1", "h2", "ab"])

        assert with_ids == largest_combo_groups(cards, GROUPED)


class TestCompletableComboIds:
    """Test completable_combo_ids."""

    def test_required_cards_must_all_be_selected(self):
        combos = [make_combo("ab", ["A", "B"]), make_combo("bc", ["B", "C"])]

        assert completable_combo_ids({"A", "B"}, combos) == ["ab"]
        assert completable_combo_ids({"A", "C"}, combos) == []
        assert completable_combo_ids({"A", "B", "C"}, combos) == ["ab", "bc"]

    def test_every_option_needs_one_selected_card(self):
        combos = [make_combo("c", ["A"], [["S1", "S2"], ["M1", "M2"]])]

        assert completable_combo_ids({"A"}, combos) == []
        assert completable_combo_ids({"A", "S1"}, combos) == []
        assert completable_combo_ids({"A", "S2", "M1"}, combos) == ["c"]
        assert completable_combo_ids({"S1", "S2", "M1", "M2"}, combos) == []

    def test_one_card_can_satisfy_several_options(self):
        combos = [make_combo("c", ["A"], [["S1", "X"], ["X", "M1"]])]

        assert completable_combo_ids({"A", "X"}, combos) == ["c"]

    def test_option_without_cards_is_never_satisfied(self):
        combos = [make_combo("c", ["A"], [[]])]

        assert completable_combo_ids({"A"}, combos) == []

    def test_options_only_combo(self):
        combos = [make_combo("c", [], [["S1", "S2"]])]

        assert completable_combo_ids({"S2"}, combos) == ["c"]
        assert completable_combo_ids(set(), combos) == []

    def test_accepts_any_collection_and_keeps_combo_order(self):
        combos = [make_combo("z", ["A"]), make_combo("a", ["B"]), make_combo("m", ["C"])]

        assert completable_combo_ids(["C", "A", "A"], combos) == ["z", "m"]
        assert completable_combo_ids(frozenset(["B", "A"]), combos) == ["z", "a"]
        assert completable_combo_ids([], combos) == []


class TestCardUtilization:
    """Test card_utilization."""

    def test_counts_completed_combos_only(self):
        combos = [
            make_combo("ab", ["A", "B"]),
            make_combo("bc", ["B", "C"]),
            make_combo("ad", ["A", "D"]),  # D is not selected
        ]

        assert card_utilization(["A", "B", "C"], combos) == {"A": 1, "B": 2, "C": 1}

    def test_counts_every_selected_card_of_an_option_pool(self):
        # Both sac outlets count for the combo, even though one would be enough
        combos = [make_combo("c", ["A"], [["S1", "S2", "S3"]])]

        assert card_utilization(["A", "S1", "S2"], combos) == {"A": 1, "S1": 1, "S2": 1}

    def test_card_in_required_and_pool_counts_once(self):
        combos = [make_combo("c", ["A"], [["A", "S1"], ["A", "S2"]])]

        assert card_utilization(["A"], combos) == {"A": 1}

    def test_unused_and_unknown_cards_have_zero_utilization(self):
        combos = [make_combo("ab", ["A", "B"])]

        assert card_utilization(["A", "Not In Any Combo"], combos) == {
            "A": 0,
            "Not In Any Combo": 0,
        }

    def test_matches_optimizer_utilization(self):
        combos = [
            make_combo("c1", ["A", "B"]),
            make_combo("c2", ["B"], [["S1", "S2"]]),
            make_combo("c3", ["C"], [["S2", "M1"], ["S1", "S2"]]),
            make_combo("c4", ["A", "D"]),
        ]
        optimizer = ILPOptimizer(
            combos=combos, candidate_cards=build_candidate_cards(combos), cube_size=4
        )
        selected = ["A", "B", "C", "S2"]
        completed = completable_combo_ids(selected, combos)

        assert completed == ["c1", "c2", "c3"]
        assert card_utilization(selected, combos) == optimizer._calculate_utilization(
            selected, completed
        )


class TestComputeUtilizationStats:
    """Test compute_utilization_stats."""

    def test_empty(self):
        stats = compute_utilization_stats({})

        assert (stats.min_utilization, stats.max_utilization) == (0, 0)
        assert stats.mean_utilization == 0.0

    def test_values(self):
        stats = compute_utilization_stats({"A": 1, "B": 3, "C": 5, "D": 7})

        assert (stats.min_utilization, stats.max_utilization) == (1, 7)
        assert stats.mean_utilization == 4.0
        assert stats.median_utilization == 4.0
        assert stats.total_absolute_deviation == 8


class TestComputeColorStats:
    """Test compute_color_stats."""

    def test_counts_and_categories(self):
        identities = {"Mono W": "W", "Mono U": "U", "Azorius": "WU", "Rock": "", "Five": "WUBRG"}

        stats = compute_color_stats([*identities, "Mystery"], identities)

        assert stats.cards_per_color == {"W": 3, "U": 3, "B": 1, "R": 1, "G": 1}
        assert stats.mono_colored == {"W": 1, "U": 1, "B": 0, "R": 0, "G": 0}
        assert stats.multicolor == 2
        assert stats.colorless == 1
        assert stats.unknown == 1
        # Counts 3, 3, 1, 1, 1: mean 1.8, squared deviations 2 x 1.44 + 3 x 0.64 = 4.8
        assert stats.variance == pytest.approx(0.96)
        assert stats.std_deviation == pytest.approx(0.96**0.5)

    def test_even_distribution_has_zero_variance(self):
        identities = {color: color for color in "WUBRG"}

        stats = compute_color_stats(list(identities), identities)

        assert stats.variance == 0.0
        assert stats.std_deviation == 0.0

    def test_empty(self):
        stats = compute_color_stats([], {})

        assert sum(stats.cards_per_color.values()) == 0
        assert stats.variance == 0.0
