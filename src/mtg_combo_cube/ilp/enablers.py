"""
Enabler cards: the cards that set a combo up.

A combo scores only through the variants it completes, so the fast mana, rituals, wheels
and graveyard fillers a drafter assembles combos with (Lion's Eye Diamond, Dark Ritual,
Wheel of Fortune, Entomb) are invisible to the model unless they are pieces themselves. An
enabler table names, per free-form category, how to find them. It is the `enablers` section
of the configuration file (see config.py):

    enablers:
      fast_mana:
        queries: ['t:artifact mv=0 o:/\\{T\\}: Add/ -t:land']
        exclude: [Sol Talisman]
      ritual: {queries: ['(t:instant or t:sorcery) mv<=3 o:/^Add (\\{[WUBRGC]\\}){2,}/']}
      wheel: {cards: [Wheel of Misfortune, Memory Jar]}

A category gives Scryfall `queries` (resolved by the PayoffFetcher in EDHREC order; the first
QUERY_CARD_LIMIT cards count), explicit `cards`, and `exclude`, names dropped from the
category whatever their source; it needs at least one of the three. It may also bound its
Phase 2 enabler floor with `min_enablers` and `max_enablers`, in place of the range the
optimizer applies to every category (see ILPOptimizer._compute_enabler_floors). The
categories are not tied to the outcome table. Nothing in the Spellbook data marks a card as
an enabler, so there is no inference: the table is the only source. The table's shape and
its parsing are the generic card table of ilp/card_table.py, shared with the payoff table.

The enabler set of a category is the table's cards and the query results, minus the
exclusions and the blocklist (resolve_enablers). The Phase 2 enabler floor asks for a
minimum of them per category; an enabler card the pool lacks is added to the candidate pool
for that, and an enabler card in fewer combos than the utilization floor is exempt from it
(an *enabler-only* card), so its few combos still score.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar

from mtg_combo_cube.ilp.card_table import (
    QUERY_CARD_LIMIT,
    CardTable,
    CardTableCategory,
    CardTableDefinitions,
    CardTableError,
    CardTableFetchError,
    TableKind,
    parse_card_table,
    resolve_sources,
)
from mtg_combo_cube.ilp.ilp_models import EnablerStats


class EnablerTableError(CardTableError):
    """An invalid enabler table, or a query of it that matches no card or Scryfall rejects."""


class EnablerFetchError(CardTableFetchError):
    """An enabler query could not be fetched from Scryfall while the enabler floor needs it."""


ENABLER_KIND = TableKind("enabler", EnablerTableError, "min_enablers", "max_enablers")


@dataclass(frozen=True)
class EnablerCategory(CardTableCategory):
    """How the enabler cards of one category are found, and the bounds of its floor."""

    KIND: ClassVar[TableKind] = ENABLER_KIND

    @property
    def min_enablers(self) -> int | None:
        return self.minimum

    @property
    def max_enablers(self) -> int | None:
        return self.maximum


@dataclass(frozen=True)
class EnablerDefinitions(CardTableDefinitions):
    """The enabler table as loaded: the categories and how to find their cards."""

    KIND: ClassVar[TableKind] = ENABLER_KIND
    CATEGORY: ClassVar[type[CardTableCategory]] = EnablerCategory

    @property
    def min_enablers(self) -> dict[str, int]:
        """The categories' own lower bounds for the enabler floor, where the table gives one."""
        return self.minimums

    @property
    def max_enablers(self) -> dict[str, int]:
        """The categories' own upper bounds for the enabler floor, where the table gives one."""
        return self.maximums


def parse_enabler_table(table: object) -> EnablerDefinitions:
    """Build the enabler definitions from the parsed configuration section (module docstring)."""
    return parse_card_table(table, EnablerDefinitions)


@dataclass(frozen=True)
class EnablerTable(CardTable):
    """The resolved enabler cards of each category and where each came from."""

    @property
    def min_enablers(self) -> dict[str, int]:
        """The table's own lower bounds for the enabler floor, for the categories that give one."""
        return self.minimums

    @property
    def max_enablers(self) -> dict[str, int]:
        """The table's own upper bounds for the enabler floor, for the categories that give one."""
        return self.maximums

    def stats(
        self, selected_cards: Collection[str], enabler_only: Collection[str] = ()
    ) -> EnablerStats:
        """
        The enabler cards of each category in a cube, with the source of each, and which of
        the cube's cards are enabler-only (`enabler_only`: the enabler cards in fewer combos
        of the pool than the utilization floor).
        """
        cards = self.selected_sources(selected_cards)
        return EnablerStats(
            cards_per_category={name: len(found) for name, found in cards.items()},
            cards=cards,
            enabler_only=sorted(set(enabler_only) & set(selected_cards)),
        )


def resolve_enablers(
    definitions: EnablerDefinitions,
    query_results: Mapping[str, Sequence[str]],
    blocklist: Collection[str] = frozenset(),
    query_limit: int = QUERY_CARD_LIMIT,
) -> EnablerTable:
    """
    The enabler set of each category: the table's cards and the first `query_limit` cards
    of each query that are neither blocked nor excluded (a query missing from
    `query_results` adds nothing), minus the exclusions and the blocklist. An exclusion
    that matches no card of its category is a warning (a typo excludes nothing).
    """
    return EnablerTable(
        sources=resolve_sources(definitions, query_results, blocklist, query_limit),
        minimums=definitions.minimums,
        maximums=definitions.maximums,
    )
