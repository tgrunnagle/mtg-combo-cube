"""Tests for the payoff table: parsing, the outlet inference and the resolved payoff set."""

import json
from collections import Counter
from pathlib import Path

import pytest

from mtg_combo_cube.ilp.ilp_models import ComboData
from mtg_combo_cube.ilp.outcomes import load_outcome_categories, parse_outcome_categories
from mtg_combo_cube.ilp.payoffs import (
    DEFAULT_INFERENCE_THRESHOLD,
    DEFAULT_PAYOFFS_PATH,
    PayoffCategory,
    PayoffDefinitions,
    PayoffTableError,
    check_query_results,
    infer_payoffs,
    load_payoff_table,
    parse_payoff_table,
    resolve_payoff_definitions,
    resolve_payoff_table,
    resolve_payoffs,
)

OUTCOMES = parse_outcome_categories(
    {
        "mana": ["infinite mana"],
        "damage": ["infinite damage"],
        "tokens": ["infinite creature tokens"],
        "draw": ["infinite card draw"],
    }
)
ENGINES = ("mana", "tokens")  # the non-terminal categories: the payoff table's
TABLE = {
    "mana": {"queries": ['o:"{X}" o:"X damage"'], "exclude": ["Chromatic Orrery"]},
    "tokens": {"cards": ["Impact Tremors"], "queries": ["keyword:storm"]},
}


def combo(
    combo_id: str,
    cards: list[str],
    features: list[str],
    includes: list[int],
    popularity: int = 1,
) -> ComboData:
    return ComboData(
        combo_id,
        frozenset(cards),
        [],
        popularity,
        features=frozenset(features),
        includes=frozenset(includes),
    )


class TestParse:
    def test_long_form(self):
        definitions = parse_payoff_table(TABLE)

        assert definitions.names == ("mana", "tokens")
        assert len(definitions) == 2
        assert definitions.queries == ('o:"{X}" o:"X damage"', "keyword:storm")
        assert definitions.cards == {"Impact Tremors"}
        mana, tokens = definitions
        assert mana == PayoffCategory(
            "mana", queries=('o:"{X}" o:"X damage"',), exclude=frozenset(["Chromatic Orrery"])
        )
        assert tokens.cards == ("Impact Tremors",)

    def test_exclusions_alone_are_a_category(self):
        definitions = parse_payoff_table({"mana": {"exclude": ["Chromatic Orrery"]}})

        assert definitions.names == ("mana",)

    def test_floor_bounds_are_kept_per_category(self):
        definitions = parse_payoff_table(
            {
                "mana": {"cards": ["A"], "min_payoffs": 3, "max_payoffs": 9},
                "storm": {"cards": ["B"], "max_payoffs": 0},
                "tokens": {"cards": ["C"]},
            }
        )

        assert definitions.min_payoffs == {"mana": 3}
        assert definitions.max_payoffs == {"mana": 9, "storm": 0}
        table = resolve_payoffs(definitions, {}, {})
        assert table.min_payoffs == {"mana": 3}
        assert table.max_payoffs == {"mana": 9, "storm": 0}
        assert table.without(["A"]).min_payoffs == {"mana": 3}
        assert (
            resolve_payoffs(parse_payoff_table({"mana": {"cards": ["A"]}}), {}, {}).min_payoffs
            == {}
        )

    def test_duplicates_and_blanks_are_dropped(self):
        definitions = parse_payoff_table({"mana": {"queries": ["a", " a", "b"]}})

        assert next(iter(definitions)).queries == ("a", "b")

    @pytest.mark.parametrize(
        ("table", "message"),
        [
            ([], "must be a JSON object"),
            ({}, "is empty"),
            ({"mana": ["keyword:storm"]}, "must be an object"),
            ({"mana": {}}, "no queries, cards or exclusions"),
            ({"mana": {"query": ["x"]}}, "unknown keys"),
            ({"mana": {"queries": "keyword:storm"}}, "must list its queries"),
            ({"mana": {"cards": [""]}}, "must list its cards"),
            ({"mana": {"exclude": [1]}}, "must list its exclude"),
            ({"": {"queries": ["x"]}}, "names must be strings"),
            ({"mana": {"cards": ["A"], "min_payoffs": -1}}, "min_payoffs as a whole number"),
            ({"mana": {"cards": ["A"], "max_payoffs": True}}, "max_payoffs as a whole number"),
            ({"mana": {"cards": ["A"], "min_payoffs": 3, "max_payoffs": 2}}, "above max_payoffs"),
        ],
    )
    def test_invalid_tables_are_rejected(self, table: object, message: str):
        with pytest.raises(PayoffTableError, match=message):
            parse_payoff_table(table)

    def test_duplicate_names_are_rejected(self):
        with pytest.raises(PayoffTableError, match="duplicate"):
            PayoffDefinitions(
                (PayoffCategory("mana", cards=("A",)), PayoffCategory("mana", cards=("B",)))
            )

    def test_categories_must_be_outcome_categories(self):
        definitions = parse_payoff_table({"mana": {"cards": ["A"]}, "storm": {"cards": ["B"]}})

        with pytest.raises(PayoffTableError, match=r"\['storm'\] are not in the outcome"):
            definitions.check_categories(OUTCOMES)
        parse_payoff_table(TABLE).check_categories(OUTCOMES)


class TestLoad:
    def test_load_from_path(self, tmp_path: Path):
        path = tmp_path / "payoffs.json"
        path.write_text(json.dumps(TABLE), encoding="utf-8")

        assert load_payoff_table(str(path)).names == ("mana", "tokens")

    def test_missing_and_invalid_files(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Payoff table not found"):
            load_payoff_table(str(tmp_path / "missing.json"))
        broken = tmp_path / "broken.json"
        broken.write_text("{", encoding="utf-8")
        with pytest.raises(PayoffTableError, match="not valid JSON"):
            load_payoff_table(str(broken))

    def test_given_path_must_exist_even_when_not_required(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            resolve_payoff_table(str(tmp_path / "missing.json"), required=False)

    def test_missing_default_is_an_error_only_when_required(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)

        assert resolve_payoff_table(None, required=False) is None
        with pytest.raises(FileNotFoundError):
            resolve_payoff_table(None, required=True)

    def test_default_table_is_present_and_names_outcome_categories(self):
        definitions = load_payoff_table()

        assert Path(DEFAULT_PAYOFFS_PATH).exists()
        assert definitions.names == ("mana", "storm", "tokens", "triggers", "lifegain", "counters")
        definitions.check_categories(load_outcome_categories())
        assert resolve_payoff_table(None, required=True) == definitions
        # The terminal categories are their own payoff and are not in the table
        for name in ("damage", "draw", "mill", "turns", "lock", "win", "other"):
            assert name not in definitions.names

    def test_empty_query_results_are_a_table_error(self):
        check_query_results({"a": ["Card"], "b": ["Card"]})
        with pytest.raises(PayoffTableError, match="match no card on Scryfall: 'b', 'c'"):
            check_query_results({"a": ["Card"], "b": [], "c": []})


class TestResolveDefinitions:
    FITTING = {"mana": {"cards": ["A"]}}
    UNFITTING = {"storm": {"cards": ["B"]}}

    @staticmethod
    def write(tmp_path: Path, table: dict, name: str = "payoffs.json") -> str:
        path = tmp_path / name
        path.write_text(json.dumps(table), encoding="utf-8")
        return str(path)

    @pytest.mark.parametrize("required", [False, True])
    def test_a_fitting_table_is_returned_and_logged(self, tmp_path: Path, caplog, required: bool):
        path = self.write(tmp_path, self.FITTING)

        with caplog.at_level("INFO"):
            definitions = resolve_payoff_definitions(path, OUTCOMES, required=required)

        assert definitions is not None
        assert definitions.names == ("mana",)
        assert "Loaded payoff table with 1 categories: mana" in caplog.text

    def test_missing_default_is_skipped_unless_required(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        monkeypatch.chdir(tmp_path)

        with caplog.at_level("INFO"):
            assert resolve_payoff_definitions(None, OUTCOMES, required=False) is None
        assert "No payoff table at data/payoffs.json" in caplog.text
        with pytest.raises(FileNotFoundError):
            resolve_payoff_definitions(None, OUTCOMES, required=True)

    def test_missing_given_path_is_always_an_error(self, tmp_path: Path):
        for required in (False, True):
            with pytest.raises(FileNotFoundError):
                resolve_payoff_definitions(str(tmp_path / "no.json"), OUTCOMES, required=required)

    def test_default_that_does_not_fit_is_skipped_unless_required(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        self.write(tmp_path / "data", self.UNFITTING)

        with caplog.at_level("WARNING"):
            assert resolve_payoff_definitions(None, OUTCOMES, required=False) is None
        assert "does not fit the outcome table" in caplog.text
        assert "['storm'] are not in the outcome category table" in caplog.text
        with pytest.raises(PayoffTableError, match="not in the outcome category table"):
            resolve_payoff_definitions(None, OUTCOMES, required=True)

    def test_given_path_that_does_not_fit_is_always_an_error(self, tmp_path: Path):
        path = self.write(tmp_path, self.UNFITTING)

        for required in (False, True):
            with pytest.raises(PayoffTableError, match="not in the outcome category table"):
                resolve_payoff_definitions(path, OUTCOMES, required=required)

    def test_without_an_outcome_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        # The default is skipped with a warning when the floor is off; a given path, or the
        # floor being on, needs the outcome table
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        self.write(tmp_path / "data", self.FITTING)
        path = self.write(tmp_path, self.FITTING, "given.json")

        with caplog.at_level("WARNING"):
            assert resolve_payoff_definitions(None, None, required=False) is None
        assert "needs the outcome category table" in caplog.text
        for given, required in ((None, True), (path, False), (path, True)):
            with pytest.raises(PayoffTableError, match="needs the outcome category table"):
                resolve_payoff_definitions(given, None, required=required)

    def test_log_off_is_silent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog):
        monkeypatch.chdir(tmp_path)

        with caplog.at_level("INFO"):
            assert resolve_payoff_definitions(None, OUTCOMES, required=False, log=False) is None
        assert caplog.text == ""

    def test_empty_query_results_are_a_table_error_helper(self):
        # check_query_results is what fetch_payoff_queries uses when the floor is on
        with pytest.raises(PayoffTableError, match="match no card on Scryfall: 'b'"):
            check_query_results({"a": ["Card"], "b": []})


class TestInference:
    # A mana engine (combo 10), bundled with Walking Ballista into a damage combo (combo 20
    # includes 10) in two variants, and once with Comet Storm
    ENGINE = combo("e1", ["Rock", "Untapper"], ["Infinite mana"], [10])
    POOL = [
        ENGINE,
        combo("b1", ["Rock", "Untapper", "Walking Ballista"], ["Infinite damage"], [20, 10]),
        combo("b2", ["Rock", "Untapper", "Walking Ballista"], ["Infinite damage"], [21, 10]),
        combo("b3", ["Rock", "Untapper", "Comet Storm"], ["Infinite damage"], [22, 10]),
    ]

    def test_outlet_is_the_card_the_bundled_variant_adds(self):
        inferred = infer_payoffs(self.POOL, OUTCOMES, ENGINES)

        assert inferred == {
            "mana": Counter({"Walking Ballista": 2, "Comet Storm": 1}),
            "tokens": Counter(),
        }

    def test_a_bundle_whose_result_is_only_in_the_catch_all_adds_no_outlet(self):
        # With a catch-all, an unmatched result ("Infinite creature ETB") is categorized,
        # but it is no payoff: the bundle still credits nothing
        table = parse_outcome_categories(
            {"mana": ["infinite mana"], "damage": ["infinite damage"], "other": []}
        )
        pool = [
            self.ENGINE,
            combo("b1", ["Rock", "Untapper", "Blinker"], ["Infinite creature ETB"], [20, 10]),
            combo("b2", ["Rock", "Untapper", "Blinker"], ["Infinite creature ETB"], [21, 10]),
            combo("b3", ["Rock", "Untapper", "Comet Storm"], ["Infinite damage"], [22, 10]),
        ]

        assert infer_payoffs(pool, table, ("mana",)) == {"mana": Counter({"Comet Storm": 1})}

    def test_engine_must_be_non_terminal_and_a_strict_subset(self):
        # A terminal engine (it already deals damage) is no engine; an engine with the same
        # cards adds no outlet; an engine in no category is not counted
        pool = [
            combo("t", ["Rock", "Untapper"], ["Infinite damage"], [10]),
            combo("same", ["Rock", "Untapper", "Walking Ballista"], ["Infinite mana"], [11]),
            combo("none", ["Rock"], [], [12]),
            combo(
                "b", ["Rock", "Untapper", "Walking Ballista"], ["Infinite damage"], [20, 10, 11, 12]
            ),
        ]

        assert infer_payoffs(pool, OUTCOMES, ENGINES) == {"mana": Counter(), "tokens": Counter()}

    def test_engine_includes_must_be_a_strict_subset_of_the_bundle(self):
        # The engine shares combo 10 with the bundle but also includes combo 99, which the
        # bundle does not: it is a different bundle, not this one's engine
        pool = [
            combo("other", ["Rock", "Untapper"], ["Infinite mana"], [10, 99]),
            combo("b", ["Rock", "Untapper", "Walking Ballista"], ["Infinite damage"], [20, 10]),
        ]

        assert infer_payoffs(pool, OUTCOMES, ENGINES) == {"mana": Counter(), "tokens": Counter()}

    def test_engine_with_a_terminal_outcome_beside_an_engine_one_is_no_engine(self):
        # An engine that already deals damage is terminal, whatever else it does
        pool = [
            combo("mixed", ["Rock", "Untapper"], ["Infinite mana", "Infinite damage"], [10]),
            combo("b", ["Rock", "Untapper", "Walking Ballista"], ["Infinite damage"], [20, 10]),
        ]

        assert infer_payoffs(pool, OUTCOMES, ENGINES)["mana"] == Counter()

    def test_bundled_variant_must_have_a_terminal_result(self):
        # Engine plus a card that only makes more mana is not an outlet
        pool = [
            self.ENGINE,
            combo("b", ["Rock", "Untapper", "Doubler"], ["Infinite mana"], [20, 10]),
        ]

        assert infer_payoffs(pool, OUTCOMES, ENGINES)["mana"] == Counter()

    def test_engine_may_bundle_a_smaller_combo_itself(self):
        # A token engine that includes a mana sub-combo, bundled with Blood Artist: the
        # outlet is what the bundle adds beyond every engine it includes, so the mana
        # sub-combo does not claim the token maker as a mana outlet
        pool = [
            combo("mana", ["Rock", "Untapper"], ["Infinite mana"], [10]),
            combo("tokens", ["Rock", "Untapper", "Maker"], ["Infinite creature tokens"], [30, 10]),
            combo(
                "b",
                ["Rock", "Untapper", "Maker", "Blood Artist"],
                ["Infinite damage"],
                [40, 30, 10],
            ),
        ]

        inferred = infer_payoffs(pool, OUTCOMES, ENGINES)

        assert inferred["tokens"] == Counter({"Blood Artist": 1})
        assert inferred["mana"] == Counter({"Blood Artist": 1})

    def test_two_engines_in_one_bundle_are_not_each_others_outlets(self):
        # A mana engine and a token engine bundled with one outlet: each engine's cards are
        # not credited to the other's category
        pool = [
            combo("mana", ["Rock", "Untapper"], ["Infinite mana"], [10]),
            combo("tokens", ["Maker", "Flicker"], ["Infinite creature tokens"], [30]),
            combo(
                "b",
                ["Rock", "Untapper", "Maker", "Flicker", "Blood Artist"],
                ["Infinite damage"],
                [40, 30, 10],
            ),
        ]

        inferred = infer_payoffs(pool, OUTCOMES, ENGINES)

        assert inferred == {
            "mana": Counter({"Blood Artist": 1}),
            "tokens": Counter({"Blood Artist": 1}),
        }

    def test_a_card_counts_once_per_bundled_variant(self):
        # Two engine variants of the same combo both match the bundled variant
        pool = [
            self.ENGINE,
            combo("e2", ["Rock", "Untapper"], ["Infinite mana"], [10]),
            combo("b", ["Rock", "Untapper", "Walking Ballista"], ["Infinite damage"], [20, 10]),
        ]

        assert infer_payoffs(pool, OUTCOMES, ENGINES)["mana"] == Counter({"Walking Ballista": 1})

    def test_engine_in_both_categories_credits_both(self):
        pool = [
            combo("e", ["A", "B"], ["Infinite mana", "Infinite creature tokens"], [10]),
            combo("b", ["A", "B", "Outlet"], ["Infinite damage"], [20, 10]),
        ]

        inferred = infer_payoffs(pool, OUTCOMES, ENGINES)

        assert inferred["mana"] == Counter({"Outlet": 1})
        assert inferred["tokens"] == Counter({"Outlet": 1})


class TestResolve:
    DEFINITIONS = parse_payoff_table(TABLE)
    INFERRED = {
        "mana": Counter({"Walking Ballista": 3, "Chromatic Orrery": 4, "Comet Storm": 1}),
        "tokens": Counter({"Blood Artist": 2}),
    }
    QUERIES = {
        'o:"{X}" o:"X damage"': ["Crypt Rats", "Walking Ballista", "Blocked", "Comet Storm"],
        "keyword:storm": ["Grapeshot", "Brain Freeze"],
    }

    def test_union_of_sources_minus_exclusions_and_blocklist(self):
        payoffs = resolve_payoffs(
            self.DEFINITIONS, self.INFERRED, self.QUERIES, blocklist=frozenset(["Blocked"])
        )

        assert payoffs.names == ("mana", "tokens")
        assert payoffs.inference_threshold == DEFAULT_INFERENCE_THRESHOLD
        # Comet Storm is below the threshold as an inference but a query result
        assert payoffs.cards("mana") == {"Walking Ballista", "Crypt Rats", "Comet Storm"}
        assert payoffs.sources["mana"] == {
            "Comet Storm": {"query"},
            "Crypt Rats": {"query"},
            "Walking Ballista": {"inferred", "query"},
        }
        assert payoffs.cards("tokens") == {
            "Blood Artist",
            "Impact Tremors",
            "Grapeshot",
            "Brain Freeze",
        }
        assert payoffs.sources["tokens"]["Impact Tremors"] == {"card"}
        assert payoffs.all_cards == payoffs.cards("mana") | payoffs.cards("tokens")
        assert payoffs.source_counts("mana") == {"inferred": 1, "card": 0, "query": 3}
        assert payoffs.source_counts("tokens") == {"inferred": 1, "card": 1, "query": 2}

    def test_inferred_counts_are_kept_for_tuning(self):
        payoffs = resolve_payoffs(self.DEFINITIONS, self.INFERRED, self.QUERIES)

        # Every count, excluded cards too, most credited first
        assert list(payoffs.inferred["mana"].items()) == [
            ("Chromatic Orrery", 4),
            ("Walking Ballista", 3),
            ("Comet Storm", 1),
        ]
        assert payoffs.inferred["tokens"] == {"Blood Artist": 2}

    def test_threshold_and_query_limit(self):
        payoffs = resolve_payoffs(
            self.DEFINITIONS, self.INFERRED, self.QUERIES, inference_threshold=1, query_limit=1
        )

        assert payoffs.cards("mana") == {"Walking Ballista", "Comet Storm", "Crypt Rats"}
        assert payoffs.sources["mana"]["Comet Storm"] == {"inferred"}
        assert payoffs.cards("tokens") == {"Blood Artist", "Impact Tremors", "Grapeshot"}

    def test_excluded_query_results_do_not_use_up_the_limit(self, caplog):
        definitions = parse_payoff_table(
            {"mana": {"queries": ["q"], "exclude": ["Chromatic Orrery", "No Such Card"]}}
        )

        with caplog.at_level("WARNING"):
            payoffs = resolve_payoffs(
                definitions, {}, {"q": ["Chromatic Orrery", "Crypt Rats"]}, query_limit=1
            )

        assert payoffs.cards("mana") == {"Crypt Rats"}
        # An exclusion that matches nothing is a warning (a typo excludes nothing)
        assert "the exclusions ['No Such Card'] of 'mana' match no card" in caplog.text
        assert "Chromatic Orrery" not in caplog.text

    def test_blocked_query_results_do_not_use_up_the_limit(self):
        payoffs = resolve_payoffs(
            self.DEFINITIONS,
            {},
            {'o:"{X}" o:"X damage"': ["Blocked", "Crypt Rats"]},
            blocklist=frozenset(["Blocked"]),
            query_limit=1,
        )

        assert payoffs.cards("mana") == {"Crypt Rats"}

    def test_missing_query_results_add_nothing(self):
        payoffs = resolve_payoffs(self.DEFINITIONS, {}, {})

        assert payoffs.cards("mana") == frozenset()
        assert payoffs.cards("tokens") == {"Impact Tremors"}
        assert payoffs.inferred == {"mana": {}, "tokens": {}}

    def test_stats_list_the_cube_cards_with_their_sources(self):
        payoffs = resolve_payoffs(self.DEFINITIONS, self.INFERRED, self.QUERIES)

        stats = payoffs.stats(
            ["Walking Ballista", "Grapeshot", "Other"], payoff_only=["Grapeshot", "Crypt Rats"]
        )

        assert stats.cards_per_category == {"mana": 1, "tokens": 1}
        assert stats.cards == {
            "mana": {"Walking Ballista": ["inferred", "query"]},
            "tokens": {"Grapeshot": ["query"]},
        }
        assert stats.payoff_only == ["Grapeshot"]  # of the cube's cards only
        assert payoffs.stats([]).cards_per_category == {"mana": 0, "tokens": 0}
        assert payoffs.stats([]).payoff_only == []

    def test_without_drops_cards_from_every_category(self):
        payoffs = resolve_payoffs(self.DEFINITIONS, self.INFERRED, self.QUERIES)

        smaller = payoffs.without(["Walking Ballista", "Grapeshot", "Not There"])

        assert smaller.cards("mana") == payoffs.cards("mana") - {"Walking Ballista"}
        assert smaller.cards("tokens") == payoffs.cards("tokens") - {"Grapeshot"}
        assert smaller.inferred == payoffs.inferred
