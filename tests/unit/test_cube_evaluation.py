"""Unit tests for the ground-truth cube evaluation functions."""

import pytest

from mtg_combo_cube.ilp.cube_evaluation import (
    ARCHETYPES,
    COLOR_PAIRS,
    card_inclusion,
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
    fixed_card_stats,
    largest_combo_groups,
    one_card_away,
    participating_variants,
    staple_stats,
    staples,
    weighted_combo_count,
)
from mtg_combo_cube.ilp.ilp_models import (
    ENABLER_ONLY,
    ESSENTIAL,
    FIXED,
    NO_COMBO,
    PAYOFF_ONLY,
    PIECE_CANDIDATE,
    PIECE_ENABLER,
    PIECE_PAYOFF,
    PIECE_TEMPLATE,
    PIECE_UNKNOWN,
    REDUNDANT,
    ComboData,
    RequirementOption,
)
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.models import CardAttributes
from tests.unit.test_ilp_optimizer import build_candidate_cards


def make_combo(
    combo_id: str,
    required: list[str],
    options: list[list[str]] | None = None,
    group: str = "",
    identity: str | None = "",
    popularity: int = 1,
) -> ComboData:
    return ComboData(
        id=combo_id,
        required_cards=frozenset(required),
        requirement_options=[
            RequirementOption(f"Template {i}", f"key:{sorted(cards)}", frozenset(cards))
            for i, cards in enumerate(options or [])
        ],
        popularity=popularity,
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


class TestCardInclusion:
    """Test card_inclusion: why each card is in a cube."""

    COMBOS = [
        # Group "big": two variants; H is in both, P1 and S1/S2 only in one each
        make_combo("h1", ["H", "P1"], group="big"),
        make_combo("h2", ["H"], [["S1", "S2"]], group="big"),
        make_combo("ab", ["A", "B"]),
        make_combo("x", ["A", "Z"]),  # not completed: Z is not in the cube
    ]
    CUBE = ["H", "P1", "S1", "S2", "A", "B", "F", "Pay"]

    def by_card(self, **kwargs):
        stats = card_inclusion(self.CUBE, self.COMBOS, **kwargs)
        return {card.card: card for card in stats}

    def test_counts_match_utilization(self):
        stats = self.by_card()
        utilization = card_utilization(self.CUBE, self.COMBOS)
        assert {card: s.combo_count for card, s in stats.items()} == utilization

    def test_piece_and_template_counts(self):
        stats = self.by_card()
        assert (stats["H"].piece_combo_count, stats["H"].template_combo_count) == (2, 0)
        assert (stats["S1"].piece_combo_count, stats["S1"].template_combo_count) == (0, 1)
        assert stats["S1"].templates == ["Template 0"]
        assert stats["H"].templates == []
        assert stats["H"].distinct_combo_count == 1

    def test_essential_combos(self):
        stats = self.by_card()
        # Every variant of "big" needs H; P1 and the S cards each have a variant without them
        assert stats["H"].essential_combos == ["big"]
        assert stats["P1"].essential_combos == []
        assert stats["S1"].essential_combos == []
        assert stats["A"].essential_combos == ["ab"]

    def test_template_card_is_essential_when_it_is_the_only_filler(self):
        combos = [make_combo("t", ["H"], [["S1", "S2"]])]
        stats = {s.card: s for s in card_inclusion(["H", "S1"], combos)}
        assert stats["S1"].essential_combos == ["t"]

    def test_combo_variants_one_per_combo(self):
        stats = self.by_card()
        # H is in both variants of "big", equally popular: the first id represents the combo
        assert stats["H"].combo_variants == ["h1"]
        assert stats["P1"].combo_variants == ["h1"]
        assert stats["S1"].combo_variants == ["h2"]
        assert stats["A"].combo_variants == ["ab"]  # "x" is not completed
        assert stats["F"].combo_variants == []

    def test_combo_variants_are_the_most_popular_and_ordered_by_popularity(self):
        combos = [
            make_combo("h1", ["H", "P1"], group="big", popularity=5),
            make_combo("h2", ["H"], [["S1"]], group="big", popularity=9),
            make_combo("hb", ["H", "B"], popularity=7),
            make_combo("s1b", ["S1", "B"], popularity=1),
        ]
        stats = {s.card: s for s in card_inclusion(["H", "P1", "S1", "B"], combos)}
        assert stats["H"].combo_variants == ["h2", "hb"]
        assert stats["S1"].combo_variants == ["h2", "s1b"]
        assert stats["B"].combo_variants == ["hb", "s1b"]
        assert stats["P1"].combo_variants == ["h1"]  # the only variant of "big" it is in

    def test_reasons(self):
        stats = self.by_card(payoff_cards={"mana": ["Pay", "A", "Elsewhere"]})
        assert stats["H"].reason == ESSENTIAL
        assert stats["P1"].reason == REDUNDANT
        assert stats["Pay"].reason == PAYOFF_ONLY
        assert stats["F"].reason == NO_COMBO
        assert stats["A"].payoff_categories == ["mana"]
        assert stats["F"].payoff_categories == []
        assert stats["A"].enabler_categories == []

    def test_enabler_reasons(self):
        # An enabler in no completed combo is enabler-only; one that is a payoff card too
        # is payoff-only; a combo piece keeps its combo reason and its categories
        stats = self.by_card(
            payoff_cards={"mana": ["Pay"]},
            enabler_cards={"ritual": ["F", "Pay", "H"], "wheel": ["F"]},
        )
        assert stats["F"].reason == ENABLER_ONLY
        assert stats["F"].enabler_categories == ["ritual", "wheel"]
        assert stats["Pay"].reason == PAYOFF_ONLY
        assert stats["Pay"].enabler_categories == ["ritual"]
        assert stats["H"].reason == ESSENTIAL
        assert stats["H"].enabler_categories == ["ritual"]
        assert stats["A"].enabler_categories == []

    def test_outcomes_count_distinct_combos(self):
        categories = {"big": frozenset({"mana", "damage"}), "ab": frozenset({"mana"})}
        stats = self.by_card(group_categories=categories)
        assert stats["H"].outcomes == {"damage": 1, "mana": 1}
        assert stats["A"].outcomes == {"mana": 1}
        assert stats["F"].outcomes == {}

    def test_most_used_first(self):
        stats = card_inclusion(self.CUBE, self.COMBOS)
        assert [s.card for s in stats][:1] == ["H"]
        assert [s.combo_count for s in stats] == sorted(
            (s.combo_count for s in stats), reverse=True
        )

    def test_accepts_completed_ids(self):
        completed = completable_combo_ids(self.CUBE, self.COMBOS)
        assert card_inclusion(self.CUBE, self.COMBOS, completed) == card_inclusion(
            self.CUBE, self.COMBOS
        )

    def test_fixed_cards_come_last_with_their_own_reason(self):
        # Z is fixed: it completes "x" with A, which is then no longer essential to A alone
        # in a sense but still is (x needs A); S2 is fixed and in no completed combo once
        # S1 fills the template, so it is still reported (it takes part in h2's pool)
        stats = card_inclusion(
            ["H", "P1", "S1", "A", "B", "F"], self.COMBOS, fixed_cards=["Z", "Rock"]
        )

        assert [s.card for s in stats][-1] == "Z"
        assert stats[-1].reason == FIXED
        assert stats[-1].combo_count == 1
        assert stats[-1].essential_combos == ["x"]
        assert "Rock" not in {s.card for s in stats}  # fixed, in no combo: not reported
        by_card = {s.card: s for s in stats}
        assert by_card["A"].distinct_combo_count == 2  # ab and, with Z, x
        assert by_card["A"].reason == ESSENTIAL
        assert [s.card for s in stats if s.reason != FIXED] == [
            s.card
            for s in card_inclusion(["H", "P1", "S1", "A", "B", "F", "Z"], self.COMBOS)
            if s.card != "Z"
        ]


class TestParticipatingVariants:
    COMBOS = [
        make_combo("h1", ["H", "P1"], group="big"),
        make_combo("h2", ["H"], [["S1", "S2"]], group="big"),
        make_combo("ab", ["A", "B"]),
    ]

    def test_required_cards_and_option_pool_cards_take_part(self):
        completed = ["h1", "h2", "ab"]

        assert [c.id for c in participating_variants(["P1"], self.COMBOS, completed)] == ["h1"]
        # S2 is in h2's pool whether or not it fills it
        assert [c.id for c in participating_variants(["S2"], self.COMBOS, completed)] == ["h2"]
        assert [c.id for c in participating_variants(["H", "A"], self.COMBOS, completed)] == [
            "h1",
            "h2",
            "ab",
        ]
        assert participating_variants(["A"], self.COMBOS, ["h1"]) == []  # ab not completed
        assert participating_variants([], self.COMBOS, completed) == []


class TestFixedCardStats:
    COMBOS = TestParticipatingVariants.COMBOS

    def test_counts_and_categories(self):
        stats = fixed_card_stats(
            ["H", "Island", "Mox"],
            ["H", "P1", "A", "B", "Mox"],
            self.COMBOS,
            ["h1", "h2", "ab"],
            payoff_cards={"mana": ["A", "H"], "storm": ["X"]},
            enabler_cards={"fast_mana": ["Mox"], "ritual": []},
        )

        assert stats.cards == ["H", "Island", "Mox"]
        assert stats.candidate_cards == ["H", "Mox"]
        assert (stats.combo_count, stats.distinct_combo_count) == (2, 1)
        assert stats.payoff_categories == {"mana": ["H"]}
        assert stats.enabler_categories == {"fast_mana": ["Mox"]}

    def test_without_tables(self):
        stats = fixed_card_stats(["Island"], ["H"], self.COMBOS, [])

        assert stats.candidate_cards == []
        assert (stats.combo_count, stats.distinct_combo_count) == (0, 0)
        assert stats.payoff_categories == {} and stats.enabler_categories == {}


class TestOneCardAway:
    COMBOS = [
        # Group "big": h1 is complete, h2 needs P2; the group is complete, so P2 adds no combo
        make_combo("h1", ["H", "P1"], group="big", popularity=10),
        make_combo("h2", ["H", "P2"], group="big", popularity=30),
        # Two variants needing Sol: one named, one through a template Sol fills
        make_combo("s1", ["A", "Sol"], popularity=100),
        make_combo("s2", ["B"], [["Sol", "Petal"]], popularity=50),
        # A complete variant with an unfilled template: the template is the piece
        make_combo("t", ["A", "B"], [["Z1", "Z2"]], popularity=20),
        # Two pieces missing: not one card away
        make_combo("far", ["A", "X", "Y"], popularity=1000),
        # Missing a card and a template: not one card away
        make_combo("far2", ["A", "X"], [["Z1"]], popularity=1000),
        # Complete through the fixed card F: not potential, and F is no piece
        make_combo("f", ["A", "F"], popularity=400),
        make_combo("g", ["F", "G"], popularity=5),
    ]
    CUBE = ["H", "P1", "A", "B"]

    def test_pieces_and_counts(self):
        report = one_card_away(
            self.CUBE,
            self.COMBOS,
            fixed_cards=["F"],
            candidate_cards=["H", "P1", "P2", "A", "B", "Sol", "Petal", "G"],
            payoff_cards=["G"],
            enabler_cards=["Sol", "Petal"],
        )

        # h2, s1, s2, t and g (with F fixed) are one piece short; f is complete through F
        assert (report.variant_count, report.piece_count) == (5, 5)
        assert [
            (p.piece, p.kind, p.variant_count, p.distinct_combo_count) for p in report.pieces
        ] == [
            ("Sol", PIECE_ENABLER, 1, 1),  # s1 names it; s2's template is a template piece
            ("template:Template 0", PIECE_TEMPLATE, 1, 1),  # s2: Sol or Petal
            ("P2", PIECE_CANDIDATE, 1, 1),
            ("template:Template 0", PIECE_TEMPLATE, 1, 1),  # t: Z1 or Z2, another template
            ("G", PIECE_PAYOFF, 1, 1),
        ]
        sol, template_s, p2, template_t, g = report.pieces
        assert (sol.popularity, sol.max_popularity, sol.new_combo_count) == (100, 100, 1)
        assert sol.example_cards == ["A", "Sol"]
        assert sol.variant_ids == ["s1"]
        assert (template_s.popularity, template_s.variant_ids) == (50, ["s2"])
        assert (template_t.popularity, template_t.example_cards) == (20, ["A", "B"])
        # P2's variant is a second variant of a complete combo: no new combo
        assert (p2.popularity, p2.new_combo_count) == (30, 0)
        assert (g.popularity, g.new_combo_count, g.example_cards) == (5, 1, ["F", "G"])

    def test_fixed_card_brings_variants_within_one_card(self):
        with_fixed = one_card_away(self.CUBE, self.COMBOS, fixed_cards=["F"], payoff_cards=["G"])
        without = one_card_away(self.CUBE, self.COMBOS)

        pieces = {p.piece: p for p in with_fixed.pieces}
        assert pieces["G"].kind == PIECE_PAYOFF
        assert pieces["G"].new_combo_count == 1
        assert "F" not in pieces
        # Without the fixed card F is the piece of f and g needs two cards
        plain = {p.piece: p for p in without.pieces}
        assert plain["F"].variant_count == 1
        assert "G" not in plain
        assert without.variant_count == with_fixed.variant_count

    def test_kinds_and_sorting(self):
        report = one_card_away(
            self.CUBE, self.COMBOS, candidate_cards=["H", "P1", "A", "B"], payoff_cards=["Sol"]
        )

        kinds = {p.piece: p.kind for p in report.pieces}
        assert kinds["Sol"] == PIECE_PAYOFF  # the payoff table wins over the enabler table
        assert kinds["F"] == PIECE_UNKNOWN  # not a candidate
        assert kinds["P2"] == PIECE_UNKNOWN
        # Summed popularity descending, ties by name
        assert [p.popularity for p in report.pieces] == sorted(
            (p.popularity for p in report.pieces), reverse=True
        )
        # Without candidate cards every named piece is a candidate
        assert {p.kind for p in one_card_away(self.CUBE, self.COMBOS).pieces} == {
            PIECE_CANDIDATE,
            PIECE_TEMPLATE,
        }

    def test_limit(self):
        report = one_card_away(self.CUBE, self.COMBOS, limit=2)

        assert report.piece_count == 5  # F, Sol, P2 and the two templates
        assert [p.piece for p in report.pieces] == ["F", "Sol"]

    def test_empty_cube(self):
        report = one_card_away([], [make_combo("ab", ["A", "B"]), make_combo("c", ["C"])])

        # A single-card combo is one card away from an empty cube; a two-card one is not
        assert (report.variant_count, report.piece_count) == (1, 1)
        assert report.pieces[0].piece == "C"


class TestStaples:
    COMBOS = [
        make_combo("s", ["S1", "S2"], popularity=1000),
        make_combo("t1", ["T1", "T2"], [["Z1"]], group="t", popularity=500),
        make_combo("t2", ["T1", "T2"], [["Z2"]], group="t", popularity=400),
        make_combo("u", ["S1", "S2", "U"], popularity=300),
        make_combo("only", [], [["A", "B"]], popularity=5000),
        make_combo("d", ["D1", "D2"], popularity=1),
    ]

    def test_collapse_by_named_cards(self):
        found = staples(self.COMBOS, 4)

        assert [(s.cards, s.popularity, s.variant_ids) for s in found] == [
            (["S1", "S2"], 1000, ["s"]),
            (["T1", "T2"], 500, ["t1", "t2"]),
            (["S1", "S2", "U"], 300, ["u"]),
        ]

    def test_count_and_ties(self):
        assert [s.cards for s in staples(self.COMBOS, 1)] == [["S1", "S2"]]
        assert staples(self.COMBOS, 0) == []
        tied = [make_combo("b", ["B"], popularity=1), make_combo("a", ["A"], popularity=1)]
        assert [s.cards for s in staples(tied, 1)] == [["A"]]  # ties by id

    def test_status(self):
        found = staples(self.COMBOS, 4)
        cube = ["S1", "S2", "T1", "T2", "Z2"]

        status = staple_stats(found, cube, completable_combo_ids(cube, self.COMBOS))

        assert [(s.cards, s.completed, s.missing_cards) for s in status] == [
            (["S1", "S2"], True, []),
            (["T1", "T2"], True, []),  # through t2
            (["S1", "S2", "U"], False, ["U"]),
        ]
        assert status[1].popularity == 500
        # The named cards present but the template unfilled: not completed, nothing missing
        partial = staple_stats(
            found, ["T1", "T2"], completable_combo_ids(["T1", "T2"], self.COMBOS)
        )
        assert (partial[1].completed, partial[1].missing_cards) == (False, [])


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
        assert stats.colorless == 2  # the golem and the unknown card, not the forest
        assert stats.unknown == 1
        # Nonland mana values: 1, 7, 1, 4, 3, 9.5; the unknown card is left out
        assert stats.mana_value_counts == {0: 0, 1: 2, 2: 0, 3: 1, 4: 1, 5: 0, 6: 0, 7: 2}
        assert stats.mean_mana_value == pytest.approx(25.5 / 6)
        assert stats.mean_mana_value_per_color == {
            "W": pytest.approx(6.75),
            "U": 9.5,
            "B": 0.0,
            "R": 2.0,
            "G": 1.0,  # the forest is a land
        }

    def test_lands_count_as_types_only(self):
        attributes = {
            "Wolf Run": CardAttributes("RG", "Land", 0),
            "Coffers": CardAttributes("B", "Land", 0),
            "Maze": CardAttributes("", "Land", 0),
        }

        stats = compute_card_mix_stats(list(attributes), attributes)

        assert stats.type_counts["Land"] == 3
        assert stats.multicolor == 0
        assert stats.colorless == 0
        assert stats.mean_mana_value == 0.0

    def test_empty(self):
        stats = compute_card_mix_stats([], {})

        assert stats.card_count == 0
        assert sum(stats.type_counts.values()) == 0
        assert stats.mean_mana_value == 0.0
