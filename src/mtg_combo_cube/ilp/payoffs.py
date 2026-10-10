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
applies to every category (see ILPOptimizer._compute_payoff_floors).

The payoff set of a category is the union of the inferred cards (infer_payoffs: the cards a
bundled Spellbook variant adds to the engine it includes, kept from the confidence
threshold on), the table's cards and the query results, minus the exclusions and the
blocklist (resolve_payoffs). The Phase 2 payoff floor asks for a minimum of them per
category; a payoff card in no combo is added to the candidate pool for that.
"""

import logging
from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from mtg_combo_cube.ilp.ilp_models import ComboData, PayoffStats
from mtg_combo_cube.ilp.outcomes import OutcomeCategories

logger = logging.getLogger(__name__)

# Bundled variants a card must be the outlet of before the inference counts it
DEFAULT_INFERENCE_THRESHOLD = 2
QUERY_CARD_LIMIT = 25  # cards kept per query, in EDHREC order

# Where a payoff card came from
SOURCE_INFERRED = "inferred"
SOURCE_CARD = "card"
SOURCE_QUERY = "query"
SOURCES = (SOURCE_INFERRED, SOURCE_CARD, SOURCE_QUERY)


class PayoffTableError(ValueError):
    """An invalid payoff table, or a query of it that matches no card or Scryfall rejects."""


class PayoffFetchError(RuntimeError):
    """A payoff query could not be fetched from Scryfall while the payoff floor needs it."""


def _names(entry: Mapping[str, object], key: str, category: str) -> tuple[str, ...]:
    """The non-empty strings listed under `key`, or an error naming the category."""
    values = entry.get(key, [])
    if not isinstance(values, list) or not all(
        isinstance(value, str) and value.strip() for value in values
    ):
        raise PayoffTableError(
            f"payoff category {category!r} must list its {key} as non-empty strings"
        )
    return tuple(dict.fromkeys(str(value).strip() for value in values))


def _bound(entry: Mapping[str, object], key: str, category: str) -> int | None:
    """The non-negative integer under `key`, None when absent, or an error naming the category."""
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PayoffTableError(
            f"payoff category {category!r} must give {key} as a whole number of 0 or more, "
            f"got {value!r}"
        )
    return value


@dataclass(frozen=True)
class PayoffCategory:
    """How the payoff cards of one outcome category are found, and the bounds of its floor."""

    name: str
    queries: tuple[str, ...] = ()  # Scryfall queries
    cards: tuple[str, ...] = ()  # explicit card names
    exclude: frozenset[str] = frozenset()  # names dropped whatever their source
    min_payoffs: int | None = None  # the category's own lower bound for the payoff floor
    max_payoffs: int | None = None  # the category's own upper bound for the payoff floor

    def __post_init__(self) -> None:
        if not (self.queries or self.cards or self.exclude):
            raise PayoffTableError(
                f"payoff category {self.name!r} has no queries, cards or exclusions"
            )
        if (
            self.min_payoffs is not None
            and self.max_payoffs is not None
            and self.min_payoffs > self.max_payoffs
        ):
            raise PayoffTableError(
                f"payoff category {self.name!r} has min_payoffs {self.min_payoffs} above "
                f"max_payoffs {self.max_payoffs}"
            )


@dataclass(frozen=True)
class PayoffDefinitions:
    """The payoff table as loaded: the categories and how to find their cards."""

    categories: tuple[PayoffCategory, ...]

    def __post_init__(self) -> None:
        if not self.categories:
            raise PayoffTableError("the payoff table is empty")
        names = [category.name for category in self.categories]
        if len(set(names)) != len(names):
            raise PayoffTableError(f"duplicate payoff category names in {names}")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(category.name for category in self.categories)

    @property
    def queries(self) -> tuple[str, ...]:
        """Every query of the table once, in table order."""
        return tuple(dict.fromkeys(q for category in self.categories for q in category.queries))

    @property
    def cards(self) -> frozenset[str]:
        """Every card the table names explicitly."""
        return frozenset(card for category in self.categories for card in category.cards)

    @property
    def min_payoffs(self) -> dict[str, int]:
        """The categories' own lower bounds for the payoff floor, where the table gives one."""
        return {c.name: c.min_payoffs for c in self.categories if c.min_payoffs is not None}

    @property
    def max_payoffs(self) -> dict[str, int]:
        """The categories' own upper bounds for the payoff floor, where the table gives one."""
        return {c.name: c.max_payoffs for c in self.categories if c.max_payoffs is not None}

    def __len__(self) -> int:
        return len(self.categories)

    def __iter__(self) -> Iterator[PayoffCategory]:
        return iter(self.categories)

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
    if not isinstance(table, dict):
        raise PayoffTableError(
            "the payoff table must be a mapping of category name to its queries, cards and exclude"
        )
    categories = []
    for name, entry in table.items():
        if not isinstance(name, str) or not name:
            raise PayoffTableError(f"payoff category names must be strings, got {name!r}")
        if not isinstance(entry, dict):
            raise PayoffTableError(
                f"payoff category {name!r} must be an object with queries, cards or exclude"
            )
        entry_dict: dict[str, object] = {str(key): value for key, value in entry.items()}
        unknown = set(entry_dict) - {"queries", "cards", "exclude", "min_payoffs", "max_payoffs"}
        if unknown:
            raise PayoffTableError(f"payoff category {name!r} has unknown keys {sorted(unknown)}")
        categories.append(
            PayoffCategory(
                name,
                queries=_names(entry_dict, "queries", name),
                cards=_names(entry_dict, "cards", name),
                exclude=frozenset(_names(entry_dict, "exclude", name)),
                min_payoffs=_bound(entry_dict, "min_payoffs", name),
                max_payoffs=_bound(entry_dict, "max_payoffs", name),
            )
        )
    return PayoffDefinitions(tuple(categories))


def check_query_results(results: Mapping[str, Sequence[str]]) -> None:
    """A query that matches no card is a table error, not an empty payoff set."""
    empty = [query for query, cards in results.items() if not cards]
    if empty:
        raise PayoffTableError(
            "payoff queries match no card on Scryfall: " + ", ".join(repr(q) for q in empty)
        )


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
class PayoffTable:
    """The resolved payoff cards of each category and where each came from."""

    # category (table order) -> card (alphabetical) -> the sources (SOURCES) that named it
    sources: dict[str, dict[str, frozenset[str]]]
    # category -> card -> bundled variants the inference found it the outlet of, every
    # count (also below the threshold), so the table can be tuned from a run
    inferred: dict[str, dict[str, int]]
    inference_threshold: int
    # The table's own bounds for the payoff floor, for the categories that give one
    min_payoffs: dict[str, int] = field(default_factory=dict)
    max_payoffs: dict[str, int] = field(default_factory=dict)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.sources)

    def cards(self, name: str) -> frozenset[str]:
        """The payoff cards of a category."""
        return frozenset(self.sources[name])

    @property
    def all_cards(self) -> frozenset[str]:
        """Every payoff card of every category."""
        return frozenset(card for cards in self.sources.values() for card in cards)

    def source_counts(self, name: str) -> dict[str, int]:
        """How many of a category's cards each source named (a card can have several)."""
        return {
            source: sum(1 for sources in self.sources[name].values() if source in sources)
            for source in SOURCES
        }

    def without(self, names: Collection[str]) -> "PayoffTable":
        """The table without the named cards (a table card Scryfall does not know)."""
        dropped = set(names)
        return PayoffTable(
            sources={
                name: {card: s for card, s in by_card.items() if card not in dropped}
                for name, by_card in self.sources.items()
            },
            inferred=self.inferred,
            inference_threshold=self.inference_threshold,
            min_payoffs=self.min_payoffs,
            max_payoffs=self.max_payoffs,
        )

    def stats(
        self, selected_cards: Collection[str], payoff_only: Collection[str] = ()
    ) -> PayoffStats:
        """
        The payoff cards of each category in a cube, with the source of each, and which of
        the cube's cards are payoff-only (`payoff_only`: the cards that complete no combo).
        """
        selected = set(selected_cards)
        cards = {
            name: {
                card: sorted(sources)
                for card, sources in sorted(by_card.items())
                if card in selected
            }
            for name, by_card in self.sources.items()
        }
        return PayoffStats(
            cards_per_category={name: len(found) for name, found in cards.items()},
            cards=cards,
            payoff_only=sorted(set(payoff_only) & selected),
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
    blocked = set(blocklist)
    sources: dict[str, dict[str, frozenset[str]]] = {}
    for category in definitions:
        found: dict[str, set[str]] = {}
        seen: set[str] = set()
        counts = inferred.get(category.name, Counter())
        named: list[tuple[Iterable[str], str]] = [
            (
                (card for card, count in counts.items() if count >= inference_threshold),
                SOURCE_INFERRED,
            ),
            (category.cards, SOURCE_CARD),
        ]
        for query in category.queries:
            result = query_results.get(query, [])
            seen.update(result)
            kept = [c for c in result if c not in blocked and c not in category.exclude]
            named.append((kept[:query_limit], SOURCE_QUERY))
        for names, source in named:
            for name in names:
                seen.add(name)
                if name not in category.exclude and name not in blocked:
                    found.setdefault(name, set()).add(source)
        if unused := sorted(category.exclude - seen):
            logger.warning(
                f"Payoff table: the exclusions {unused} of {category.name!r} match no card of "
                "the category"
            )
        sources[category.name] = {
            card: frozenset(card_sources) for card, card_sources in sorted(found.items())
        }
    return PayoffTable(
        sources=sources,
        inferred={
            name: dict(
                sorted(inferred.get(name, Counter()).items(), key=lambda item: (-item[1], item[0]))
            )
            for name in definitions.names
        },
        inference_threshold=inference_threshold,
        min_payoffs=definitions.min_payoffs,
        max_payoffs=definitions.max_payoffs,
    )
