"""
Payoff cards: the outlets that turn an engine into a win.

The outcome rules keep engines of every kind in the cube (infinite mana, an infinite storm
count, infinite tokens, life or counters), but an engine is only as good as what the cube
lets a drafter do with it. A payoff table names, per non-terminal outcome category, how to
find its outlets. It is the `payoffs` section of the configuration file (see config.py):

    payoffs:
      mana:
        queries: ['o:"{X}" o:"X damage" -t:land']
        exclude: [Chromatic Orrery]
      storm: {cards: [Grapeshot]}
      tokens: {cards: [Impact Tremors]}

Every category must be a category of the outcome table; the categories the payoff table
leaves out (damage, draw, mill, ...) are terminal and are their own payoff. A category gives
Scryfall `queries` (resolved by the PayoffFetcher in EDHREC order; the first QUERY_CARD_LIMIT
cards count), explicit `cards`, and `exclude`, names dropped from the category whatever
their source. A category needs at least one of the three. It may also bound its Phase 2
payoff floor with `min_payoffs` and `max_payoffs`, in place of the range the optimizer
applies to every category (see ILPOptimizer._compute_payoff_floors). The table's shape and
its parsing are the generic card table of ilp/card_table.py, shared with the enabler table.

The payoff set of a category is the union of the inferred cards (infer_payoffs: the cards a
bundled Spellbook variant adds to the engine it includes, kept from the confidence
threshold on), the table's cards and the query results, minus the exclusions and the
blocklist (resolve_payoffs). The Phase 2 payoff floor asks for a minimum of them per
category; a payoff card in no combo is added to the candidate pool for that.
"""

import logging
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from mtg_combo_cube.ilp.card_table import (
    QUERY_CARD_LIMIT,
    SOURCE_CARD,
    SOURCE_QUERY,
    CardTable,
    CardTableCategory,
    CardTableDefinitions,
    CardTableError,
    CardTableFetchError,
    TableKind,
    parse_card_table,
    resolve_sources,
)
from mtg_combo_cube.ilp.card_table import check_query_results as _check_query_results
from mtg_combo_cube.ilp.ilp_models import ComboData, PayoffStats
from mtg_combo_cube.ilp.outcomes import OutcomeCategories

logger = logging.getLogger(__name__)

# Bundled variants a card must be the outlet of before the inference counts it
DEFAULT_INFERENCE_THRESHOLD = 2

# Where a payoff card came from
SOURCE_INFERRED = "inferred"
SOURCES = (SOURCE_INFERRED, SOURCE_CARD, SOURCE_QUERY)


class PayoffTableError(CardTableError):
    """An invalid payoff table, or a query of it that matches no card or Scryfall rejects."""


class PayoffFetchError(CardTableFetchError):
    """A payoff query could not be fetched from Scryfall while the payoff floor needs it."""


PAYOFF_KIND = TableKind("payoff", PayoffTableError, "min_payoffs", "max_payoffs")


@dataclass(frozen=True)
class PayoffCategory(CardTableCategory):
    """How the payoff cards of one outcome category are found, and the bounds of its floor."""

    KIND: ClassVar[TableKind] = PAYOFF_KIND

    @property
    def min_payoffs(self) -> int | None:
        return self.minimum

    @property
    def max_payoffs(self) -> int | None:
        return self.maximum


@dataclass(frozen=True)
class PayoffDefinitions(CardTableDefinitions):
    """The payoff table as loaded: the categories and how to find their cards."""

    KIND: ClassVar[TableKind] = PAYOFF_KIND
    CATEGORY: ClassVar[type[CardTableCategory]] = PayoffCategory

    @property
    def min_payoffs(self) -> dict[str, int]:
        """The categories' own lower bounds for the payoff floor, where the table gives one."""
        return self.minimums

    @property
    def max_payoffs(self) -> dict[str, int]:
        """The categories' own upper bounds for the payoff floor, where the table gives one."""
        return self.maximums

    def check_categories(self, outcome_categories: OutcomeCategories) -> None:
        """Every payoff category must be a category of the outcome table."""
        unknown = [name for name in self.names if name not in outcome_categories.names]
        if unknown:
            raise PayoffTableError(
                f"payoff categories {unknown} are not in the outcome category table "
                f"({', '.join(outcome_categories.names)})"
            )


def parse_payoff_table(table: object) -> PayoffDefinitions:
    """Build the payoff definitions from the parsed configuration section (module docstring)."""
    return parse_card_table(table, PayoffDefinitions)


def check_query_results(results: Mapping[str, Sequence[str]]) -> None:
    """A query that matches no card is a table error, not an empty payoff set."""
    _check_query_results(results, PAYOFF_KIND)


def infer_payoffs(
    combos: list[ComboData],
    categories: OutcomeCategories,
    engine_categories: Collection[str],
) -> dict[str, Counter[str]]:
    """
    Infer outlet cards from the pool: for each engine category, how many bundled variants
    each card is the outlet of.

    Spellbook bundles an engine with every outlet that turns its result into a win, as a
    variant whose `includes` lists more than one combo. For every such variant with a
    terminal result (a category outside `engine_categories`), an engine is a variant in the
    pool whose `includes` are a strict subset of the bundled variant's (the engine may
    itself bundle a smaller combo), whose cards are a strict subset and whose categories are
    engine categories only. The catch-all category, when the table has one, is no terminal
    result: a bundle whose result matches no named category adds no known payoff. The
    outlet is what the bundled variant adds beyond every engine
    it bundles (a variant of two engines plus an outlet credits neither engine's cards to
    the other), credited to each engine's categories. A card is counted once per bundled
    variant.
    """
    engines = frozenset(engine_categories)
    unknown = frozenset([categories.catch_all]) if categories.catch_all is not None else frozenset()
    variant_categories = {combo.id: categories.categorize(combo.features) for combo in combos}
    by_included: dict[int, list[ComboData]] = {}
    for combo in combos:
        for included in combo.includes:
            by_included.setdefault(included, []).append(combo)

    credits: dict[str, dict[str, set[str]]] = {name: {} for name in engine_categories}
    for bundled in combos:
        if len(bundled.includes) < 2 or not variant_categories[bundled.id] - engines - unknown:
            continue
        candidates = {
            engine.id: engine
            for included in bundled.includes
            for engine in by_included.get(included, [])
            if engine.includes < bundled.includes
        }
        found = [
            engine
            for engine in candidates.values()
            if variant_categories[engine.id]
            and variant_categories[engine.id] <= engines
            and engine.required_cards < bundled.required_cards
        ]
        if not found:
            continue
        outlet = bundled.required_cards.difference(*(engine.required_cards for engine in found))
        for engine in found:
            for name in variant_categories[engine.id]:
                for card in outlet:
                    credits[name].setdefault(card, set()).add(bundled.id)
    return {
        name: Counter({card: len(ids) for card, ids in cards.items()})
        for name, cards in credits.items()
    }


@dataclass(frozen=True)
class PayoffTable(CardTable):
    """The resolved payoff cards of each category and where each came from."""

    SOURCES: ClassVar[tuple[str, ...]] = SOURCES

    # category -> card -> bundled variants the inference found it the outlet of, every
    # count (also below the threshold), so the table can be tuned from a run
    inferred: dict[str, dict[str, int]] = field(default_factory=dict)
    inference_threshold: int = DEFAULT_INFERENCE_THRESHOLD

    @property
    def min_payoffs(self) -> dict[str, int]:
        """The table's own lower bounds for the payoff floor, for the categories that give one."""
        return self.minimums

    @property
    def max_payoffs(self) -> dict[str, int]:
        """The table's own upper bounds for the payoff floor, for the categories that give one."""
        return self.maximums

    def stats(
        self, selected_cards: Collection[str], payoff_only: Collection[str] = ()
    ) -> PayoffStats:
        """
        The payoff cards of each category in a cube, with the source of each, and which of
        the cube's cards are payoff-only (`payoff_only`: the cards that complete no combo).
        """
        cards = self.selected_sources(selected_cards)
        return PayoffStats(
            cards_per_category={name: len(found) for name, found in cards.items()},
            cards=cards,
            payoff_only=sorted(set(payoff_only) & set(selected_cards)),
        )


def resolve_payoffs(
    definitions: PayoffDefinitions,
    inferred: Mapping[str, Counter[str]],
    query_results: Mapping[str, Sequence[str]],
    blocklist: Collection[str] = frozenset(),
    inference_threshold: int = DEFAULT_INFERENCE_THRESHOLD,
    query_limit: int = QUERY_CARD_LIMIT,
) -> PayoffTable:
    """
    The payoff set of each category: inferred cards at or above the threshold, the table's
    cards and the first `query_limit` cards of each query that are neither blocked nor
    excluded (a query missing from `query_results` adds nothing), minus the exclusions and
    the blocklist. An exclusion that matches no card of its category is a warning (a typo
    excludes nothing).
    """
    above_threshold = {
        name: [
            (
                [
                    card
                    for card, count in inferred.get(name, Counter()).items()
                    if count >= inference_threshold
                ],
                SOURCE_INFERRED,
            )
        ]
        for name in definitions.names
    }
    return PayoffTable(
        sources=resolve_sources(
            definitions, query_results, blocklist, query_limit, extra_sources=above_threshold
        ),
        minimums=definitions.minimums,
        maximums=definitions.maximums,
        inferred={
            name: dict(
                sorted(inferred.get(name, Counter()).items(), key=lambda item: (-item[1], item[0]))
            )
            for name in definitions.names
        },
        inference_threshold=inference_threshold,
    )
