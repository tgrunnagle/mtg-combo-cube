"""Unit tests for the ground-truth cube evaluation functions."""

import pytest

from mtg_combo_cube.ilp.cube_evaluation import (
    ARCHETYPES,
    COLOR_PAIRS,
    card_utilization,
    combos_by_color_count,
    combos_per_archetype,
    completable_combo_ids,
    completable_group_keys,
    completed_group_sizes,
    compute_archetype_stats,
    compute_card_mix_stats,
    compute_color_stats,
    compute_utilization_stats,
    fits_archetype,
    largest_combo_groups,
    weighted_combo_count,
)
from mtg_combo_cube.ilp.ilp_models import ComboData, RequirementOption
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.scryfall.card_attribute_fetcher import CardAttributes
from tests.unit.test_ilp_optimizer import build_candidate_cards


def make_combo(
    combo_id: str,
    required: list[str],
    options: list[list[str]] | None = None,
    group: str = "",
    identity: str | None = "",
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
        color_identity=identity,
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


COLORED = [
    make_combo("w", ["W1", "W2"], identity="W"),
    make_combo("wu", ["W1", "U1"], identity="WU"),
    make_combo("c", ["C1", "C2"]),
    make_combo("wub", ["W1", "U1", "B1"], identity="WUB"),
    # One combo with a white variant and a white-blue variant
    make_combo("m1", ["M", "W2"], group="m", identity="W"),
    make_combo("m2", ["M", "U1"], group="m", identity="WU"),
]
EVERY_COLORED_CARD = ["W1", "W2", "U1", "B1", "C1", "C2", "M"]


class TestArchetypes:
    """Test fits_archetype, combos_per_archetype, combos_by_color_count."""

    def test_archetype_order(self):
        assert ARCHETYPES == (
            "WU", "WB", "WR", "WG", "UB", "UR", "UG", "BR", "BG", "RG",
            "W", "U", "B", "R", "G", "C",
        )  # fmt: skip

    def test_fits_archetype(self):
        assert fits_archetype("W", "WU")
        assert fits_archetype("", "WU")
        assert fits_archetype("WU", "WU")
        assert not fits_archetype("WB", "WU")
        assert fits_archetype("", "C")
        assert not fits_archetype("W", "C")
        assert fits_archetype("W", "W")
        assert not fits_archetype("WU", "W")

    def test_combos_per_archetype_counts_groups_that_fit(self):
        counts = combos_per_archetype(EVERY_COLORED_CARD, COLORED)

        # w, c and m fit mono white (m through its white variant); wu, wub do not
        assert counts["W"] == 3
        assert counts["WU"] == 4  # w, wu, c, m
        assert counts["WB"] == 3  # w, c, m
        assert counts["UB"] == 1  # c
        assert counts["U"] == 1
        assert counts["C"] == 1
        assert all(counts[pair] >= 1 for pair in COLOR_PAIRS)

    def test_mixed_group_counts_only_through_a_completed_fitting_variant(self):
        # Only the white-blue variant of m is complete
        counts = combos_per_archetype(["M", "U1"], COLORED)

        assert counts["WU"] == 1
        assert counts["W"] == 0
        assert counts["U"] == 0

    def test_combos_by_color_count_uses_the_fewest_colors_of_a_group(self):
        assert combos_by_color_count(EVERY_COLORED_CARD, COLORED) == {
            0: 1,  # c
            1: 2,  # w, m
            2: 1,  # wu
            3: 1,  # wub
            4: 0,
            5: 0,
        }
        assert combos_by_color_count(["M", "U1"], COLORED)[2] == 1

    def test_compute_archetype_stats(self):
        stats = compute_archetype_stats(EVERY_COLORED_CARD, COLORED)

        assert stats is not None
        assert stats.combos_per_archetype == combos_per_archetype(EVERY_COLORED_CARD, COLORED)
        assert stats.wide_combo_count == 1

    def test_accepts_completed_ids(self):
        completed = completable_combo_ids(EVERY_COLORED_CARD, COLORED)

        assert compute_archetype_stats(
            EVERY_COLORED_CARD, COLORED, completed
        ) == compute_archetype_stats(EVERY_COLORED_CARD, COLORED)
        assert combos_per_archetype(EVERY_COLORED_CARD, COLORED, ["c"])["C"] == 1

    def test_empty_cube(self):
        stats = compute_archetype_stats([], COLORED)

        assert stats is not None
        assert set(stats.combos_per_archetype.values()) == {0}
        assert stats.wide_combo_count == 0

    def test_unknown_identity_gives_no_stats(self):
        combos = [*COLORED, make_combo("x", ["X1", "X2"], identity=None)]

        assert combos[-1].color_identity is None
        assert compute_archetype_stats(EVERY_COLORED_CARD, combos) is None
        with pytest.raises(ValueError, match="no color identity"):
            combos_per_archetype(["X1", "X2"], combos)


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
        attributes = {name: CardAttributes(identity) for name, identity in identities.items()}

        stats = compute_color_stats([*identities, "Mystery"], attributes)

        assert stats.cards_per_color == {"W": 3, "U": 3, "B": 1, "R": 1, "G": 1}
        assert stats.mono_colored == {"W": 1, "U": 1, "B": 0, "R": 0, "G": 0}
        assert stats.multicolor == 2
        assert stats.colorless == 1
        assert stats.unknown == 1
        # Counts 3, 3, 1, 1, 1: mean 1.8, squared deviations 2 x 1.44 + 3 x 0.64 = 4.8
        assert stats.variance == pytest.approx(0.96)
        assert stats.std_deviation == pytest.approx(0.96**0.5)

    def test_even_distribution_has_zero_variance(self):
        attributes = {color: CardAttributes(color) for color in "WUBRG"}

        stats = compute_color_stats(list(attributes), attributes)

        assert stats.variance == 0.0
        assert stats.std_deviation == 0.0

    def test_empty(self):
        stats = compute_color_stats([], {})

        assert sum(stats.cards_per_color.values()) == 0
        assert stats.variance == 0.0


class TestComputeCardMixStats:
    """Test compute_card_mix_stats."""

    ATTRIBUTES = {
        "Elf": CardAttributes("G", "Creature \u2014 Elf", 1),
        "Golem": CardAttributes("", "Artifact Creature \u2014 Golem", 7),
        "Bolt": CardAttributes("R", "Instant", 1),
        "Wrath": CardAttributes("W", "Sorcery", 4),
        "Giant": CardAttributes("R", "Creature \u2014 Giant // Instant \u2014 Adventure", 3),
        "Gold": CardAttributes("WU", "Legendary Enchantment", 9.5),
        "Forest": CardAttributes("G", "Basic Land \u2014 Forest", 0),
    }

    def test_counts(self):
        stats = compute_card_mix_stats([*self.ATTRIBUTES, "Mystery"], self.ATTRIBUTES)

        assert stats.card_count == 8
        # A card counts once per type; supertypes and the back face are not types
        assert stats.type_counts == {
            "Creature": 3,
            "Instant": 1,
            "Sorcery": 1,
            "Artifact": 1,
            "Enchantment": 1,
            "Planeswalker": 0,
            "Battle": 0,
            "Land": 1,
        }
        assert stats.multicolor == 1
        assert stats.colorless == 2  # the golem and the unknown card
        assert stats.unknown == 1
        # Nonland mana values: 1, 7, 1, 4, 3, 9.5 and the unknown card's 0
        assert stats.mana_value_counts == {0: 1, 1: 2, 2: 0, 3: 1, 4: 1, 5: 0, 6: 0, 7: 2}
        assert stats.mean_mana_value == pytest.approx(25.5 / 7)
        assert stats.mean_mana_value_per_color == {
            "W": pytest.approx(6.75),
            "U": 9.5,
            "B": 0.0,
            "R": 2.0,
            "G": 1.0,  # the forest is a land
        }

    def test_empty(self):
        stats = compute_card_mix_stats([], {})

        assert stats.card_count == 0
        assert sum(stats.type_counts.values()) == 0
        assert stats.mean_mana_value == 0.0
