"""Tests for the generic card table the payoff and enabler tables are built on: parsing with
each kind's keys and errors, the query result check and the resolution of the sources."""

import pytest

from mtg_combo_cube.ilp.card_table import (
    CardTable,
    CardTableCategory,
    CardTableDefinitions,
    CardTableError,
    TableKind,
    check_query_results,
    parse_card_table,
    resolve_sources,
)
from mtg_combo_cube.ilp.enablers import EnablerDefinitions, EnablerTableError
from mtg_combo_cube.ilp.payoffs import PayoffDefinitions, PayoffTableError

KINDS = [
    (PayoffDefinitions, PayoffTableError, "payoff", "min_payoffs", "max_payoffs"),
    (EnablerDefinitions, EnablerTableError, "enabler", "min_enablers", "max_enablers"),
]


class TestParse:
    @pytest.mark.parametrize(("definitions", "error", "label", "min_key", "max_key"), KINDS)
    def test_each_kind_parses_with_its_own_keys(
        self,
        definitions: type[CardTableDefinitions],
        error: type[CardTableError],
        label: str,
        min_key: str,
        max_key: str,
    ):
        table = parse_card_table(
            {
                "a": {"queries": ["q1", "q1", " q2"], "cards": ["X"], min_key: 1, max_key: 3},
                "b": {"exclude": ["Y"]},
            },
            definitions,
        )

        assert isinstance(table, definitions)
        assert table.names == ("a", "b")
        assert table.queries == ("q1", "q2")
        assert table.cards == {"X"}
        assert table.minimums == {"a": 1}
        assert table.maximums == {"a": 3}
        first, second = table
        assert isinstance(first, definitions.CATEGORY)
        assert first.minimum == 1 and first.maximum == 3
        assert second.exclude == {"Y"}
        assert table.KIND == TableKind(label, error, min_key, max_key)

    @pytest.mark.parametrize(("definitions", "error", "label", "min_key", "max_key"), KINDS)
    def test_errors_name_the_kind(
        self,
        definitions: type[CardTableDefinitions],
        error: type[CardTableError],
        label: str,
        min_key: str,
        max_key: str,
    ):
        with pytest.raises(error, match=f"the {label} table must be a mapping"):
            parse_card_table([], definitions)
        with pytest.raises(error, match=f"the {label} table is empty"):
            parse_card_table({}, definitions)
        with pytest.raises(error, match=f"{label} category 'a' must be an object"):
            parse_card_table({"a": ["q"]}, definitions)
        with pytest.raises(
            error, match=f"{label} category 'a' has no queries, cards or exclusions"
        ):
            parse_card_table({"a": {}}, definitions)
        with pytest.raises(error, match=f"{label} category 'a' must list its cards"):
            parse_card_table({"a": {"cards": [""]}}, definitions)
        with pytest.raises(error, match=f"{label} category names must be strings"):
            parse_card_table({"": {"cards": ["X"]}}, definitions)
        with pytest.raises(error, match=f"{label} category 'a' must give {min_key} as a whole"):
            parse_card_table({"a": {"cards": ["X"], min_key: -1}}, definitions)
        with pytest.raises(error, match=f"has {min_key} 3 above {max_key} 2"):
            parse_card_table({"a": {"cards": ["X"], min_key: 3, max_key: 2}}, definitions)
        with pytest.raises(error, match=f"duplicate {label} category names"):
            definitions(
                (definitions.CATEGORY("a", cards=("X",)), definitions.CATEGORY("a", cards=("Y",)))
            )

    def test_bound_keys_of_the_other_kind_are_unknown(self):
        with pytest.raises(
            EnablerTableError, match=r"unknown keys \['max_payoffs', 'min_payoffs'\]"
        ):
            parse_card_table(
                {"a": {"cards": ["X"], "min_payoffs": 1, "max_payoffs": 2}}, EnablerDefinitions
            )
        with pytest.raises(PayoffTableError, match=r"unknown keys \['min_enablers'\]"):
            parse_card_table({"a": {"cards": ["X"], "min_enablers": 1}}, PayoffDefinitions)

    def test_the_generic_classes_have_a_generic_kind(self):
        table = parse_card_table({"a": {"cards": ["X"], "min": 1}}, CardTableDefinitions)

        assert table.minimums == {"a": 1}
        with pytest.raises(CardTableError, match="card category 'a' has min 2 above max 1"):
            CardTableCategory("a", cards=("X",), minimum=2, maximum=1)


class TestQueryLimit:
    def test_a_query_may_give_its_own_limit(self):
        table = parse_card_table(
            {"a": {"queries": ["q1", {"query": " q2", "limit": 3}, "q2", {"query": "q3"}]}},
            PayoffDefinitions,
        )

        (category,) = table
        assert category.queries == ("q1", "q2", "q3")
        assert category.query_limits == {"q2": 3}
        assert table.queries == ("q1", "q2", "q3")

    @pytest.mark.parametrize(
        ("queries", "message"),
        [
            ([{"limit": 3}], "as non-empty strings or as {query: ..., limit: N}"),
            ([{"query": " ", "limit": 3}], "as non-empty strings or as {query: ..., limit: N}"),
            ([{"query": "q", "cap": 3}], "as non-empty strings or as {query: ..., limit: N}"),
            ([{"query": "q", "limit": 0}], "whole number of 1 or more, got 0"),
            ([{"query": "q", "limit": True}], "whole number of 1 or more, got True"),
            ([{"query": "q", "limit": "3"}], "whole number of 1 or more, got '3'"),
            ([{"query": "q", "limit": 2}, {"query": "q", "limit": 3}], "'q' two limits"),
        ],
    )
    def test_a_bad_query_entry_is_an_error(self, queries: list[object], message: str):
        with pytest.raises(PayoffTableError, match="payoff category 'a' ") as e:
            parse_card_table({"a": {"queries": queries}}, PayoffDefinitions)

        assert message in str(e.value)

    def test_a_limit_must_name_a_listed_query(self):
        with pytest.raises(CardTableError, match=r"'a' limits queries it does not list: \['q2'\]"):
            CardTableCategory("a", queries=("q1",), query_limits={"q2": 3})

    def test_the_limit_applies_to_its_query_alone(self):
        definitions = parse_card_table(
            {"a": {"queries": [{"query": "short", "limit": 1}, "long"]}}, EnablerDefinitions
        )

        sources = resolve_sources(
            definitions,
            {"short": ["Blocked", "S1", "S2"], "long": ["L1", "L2", "L3"]},
            blocklist=frozenset(["Blocked"]),
            query_limit=2,
        )

        assert set(sources["a"]) == {"S1", "L1", "L2"}


class TestQueryResults:
    @pytest.mark.parametrize(("definitions", "error", "label", "min_key", "max_key"), KINDS)
    def test_empty_results_are_an_error_of_the_kind(
        self,
        definitions: type[CardTableDefinitions],
        error: type[CardTableError],
        label: str,
        min_key: str,
        max_key: str,
    ):
        check_query_results({"a": ["Card"]}, definitions.KIND)
        with pytest.raises(error, match=f"{label} queries match no card on Scryfall: 'b', 'c'"):
            check_query_results({"a": ["Card"], "b": [], "c": []}, definitions.KIND)


class TestResolve:
    DEFINITIONS = parse_card_table(
        {
            "a": {"queries": ["q"], "cards": ["Named"], "exclude": ["Gone", "Not There"]},
            "b": {"cards": ["Other"]},
        },
        EnablerDefinitions,
    )

    def test_sources_are_the_union_minus_exclusions_and_blocklist(self, caplog):
        with caplog.at_level("WARNING"):
            sources = resolve_sources(
                self.DEFINITIONS,
                {"q": ["Blocked", "Gone", "Named", "Found"]},
                blocklist=frozenset(["Blocked"]),
            )

        assert sources == {
            "a": {"Found": {"query"}, "Named": {"card", "query"}},
            "b": {"Other": {"card"}},
        }
        assert "Enabler table: the exclusions ['Not There'] of 'a' match no card" in caplog.text

    def test_blocked_and_excluded_results_do_not_use_up_the_limit(self):
        sources = resolve_sources(
            self.DEFINITIONS,
            {"q": ["Blocked", "Gone", "Found", "Later"]},
            blocklist=frozenset(["Blocked"]),
            query_limit=1,
        )

        assert set(sources["a"]) == {"Found", "Named"}

    def test_extra_sources_come_first(self):
        sources = resolve_sources(
            self.DEFINITIONS,
            {"q": ["Found"]},
            extra_sources={"a": [(["Guess", "Gone", "Found"], "guessed")]},
        )

        assert sources["a"] == {
            "Found": {"guessed", "query"},
            "Guess": {"guessed"},
            "Named": {"card"},
        }

    def test_card_table_helpers(self):
        table = CardTable(
            sources={"a": {"X": frozenset(["card"]), "Y": frozenset(["card", "query"])}, "b": {}},
            minimums={"a": 1},
        )

        assert table.names == ("a", "b")
        assert table.cards("a") == {"X", "Y"}
        assert table.all_cards == {"X", "Y"}
        assert table.cards_by_category() == {"a": frozenset(["X", "Y"]), "b": frozenset()}
        assert table.source_counts("a") == {"card": 2, "query": 1}
        assert table.selected_sources(["Y", "Z"]) == {"a": {"Y": ["card", "query"]}, "b": {}}
        smaller = table.without(["X", "Z"])
        assert smaller.cards("a") == {"Y"}
        assert smaller.minimums == {"a": 1}


class TestRequire:
    @pytest.mark.parametrize(("definitions", "error", "label", "min_key", "max_key"), KINDS)
    def test_required_cards_are_cards_of_their_category(
        self,
        definitions: type[CardTableDefinitions],
        error: type[CardTableError],
        label: str,
        min_key: str,
        max_key: str,
    ):
        table = parse_card_table(
            {"a": {"queries": ["q"], "require": ["Must", " Must"]}, "b": {"cards": ["Other"]}},
            definitions,
        )

        assert table.cards == {"Must", "Other"}
        assert table.required == {"Must"}
        resolved = resolve_sources(table, {"q": ["Found", "Must"]})
        assert resolved["a"] == {"Found": {"query"}, "Must": {"card", "query"}}
        with pytest.raises(error, match=f"{label} category 'a' both requires and excludes"):
            parse_card_table({"a": {"require": ["X"], "exclude": ["X"]}}, definitions)
        with pytest.raises(error, match=f"the {label} table requires cards the blocklist"):
            resolve_sources(table, {"q": []}, blocklist=frozenset(["Must"]))

    def test_the_resolved_table_keeps_the_required_set(self):
        table = CardTable(
            sources={"a": {"X": frozenset(["card"]), "Y": frozenset(["card"])}},
            required=frozenset(["X", "Y"]),
        )

        assert table.without(["X"]).required == {"Y"}
