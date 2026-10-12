"""
Card tables: named categories of cards found by Scryfall queries and explicit names.

The payoff table (ilp/payoffs.py) and the enabler table (ilp/enablers.py) have the same
shape in the configuration file: a mapping of category name to `queries` (Scryfall
searches, resolved by the PayoffFetcher in EDHREC order; the first QUERY_CARD_LIMIT cards
count), explicit `cards`, `exclude` (names dropped whatever their source) and an optional
lower and upper bound for the category's Phase 2 floor, under keys named for the table
(`min_payoffs` / `max_payoffs`, `min_enablers` / `max_enablers`). This module holds what the
two share: the category and table definitions with their validation, the parsing, the check
of the query results and the resolution of each category's card set with the source of
every card. A table names its kind (TableKind: the label of its error messages, its error
class and its bound keys) on its category and definition classes.
"""

import logging
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import ClassVar, Self

logger = logging.getLogger(__name__)

QUERY_CARD_LIMIT = 25  # cards kept per query, in EDHREC order

# Where a card of a table came from
SOURCE_CARD = "card"
SOURCE_QUERY = "query"


class CardTableError(ValueError):
    """An invalid card table, or a query of it that matches no card or Scryfall rejects."""


class CardTableFetchError(RuntimeError):
    """A table's query could not be fetched from Scryfall while a floor needs it."""


@dataclass(frozen=True)
class TableKind:
    """What a table is, for its error messages and its bound keys."""

    label: str  # "payoff", "enabler": the start of every error message
    error: type[CardTableError]
    min_key: str  # the configuration key of a category's own lower bound
    max_key: str  # the configuration key of a category's own upper bound


GENERIC_KIND = TableKind("card", CardTableError, "min", "max")


def _names(
    entry: Mapping[str, object], key: str, kind: TableKind, category: str
) -> tuple[str, ...]:
    """The non-empty strings listed under `key`, or an error naming the category."""
    values = entry.get(key, [])
    if not isinstance(values, list) or not all(
        isinstance(value, str) and value.strip() for value in values
    ):
        raise kind.error(
            f"{kind.label} category {category!r} must list its {key} as non-empty strings"
        )
    return tuple(dict.fromkeys(str(value).strip() for value in values))


def _bound(entry: Mapping[str, object], key: str, kind: TableKind, category: str) -> int | None:
    """The non-negative integer under `key`, None when absent, or an error naming the category."""
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise kind.error(
            f"{kind.label} category {category!r} must give {key} as a whole number of 0 or more, "
            f"got {value!r}"
        )
    return value


@dataclass(frozen=True)
class CardTableCategory:
    """How the cards of one category are found, and the bounds of its floor."""

    KIND: ClassVar[TableKind] = GENERIC_KIND

    name: str
    queries: tuple[str, ...] = ()  # Scryfall queries
    cards: tuple[str, ...] = ()  # explicit card names
    exclude: frozenset[str] = frozenset()  # names dropped whatever their source
    minimum: int | None = None  # the category's own lower bound for its floor
    maximum: int | None = None  # the category's own upper bound for its floor

    def __post_init__(self) -> None:
        kind = self.KIND
        if not (self.queries or self.cards or self.exclude):
            raise kind.error(
                f"{kind.label} category {self.name!r} has no queries, cards or exclusions"
            )
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise kind.error(
                f"{kind.label} category {self.name!r} has {kind.min_key} {self.minimum} above "
                f"{kind.max_key} {self.maximum}"
            )


@dataclass(frozen=True)
class CardTableDefinitions:
    """A card table as loaded: the categories and how to find their cards."""

    KIND: ClassVar[TableKind] = GENERIC_KIND
    CATEGORY: ClassVar[type[CardTableCategory]] = CardTableCategory

    categories: tuple[CardTableCategory, ...]

    def __post_init__(self) -> None:
        kind = self.KIND
        if not self.categories:
            raise kind.error(f"the {kind.label} table is empty")
        names = [category.name for category in self.categories]
        if len(set(names)) != len(names):
            raise kind.error(f"duplicate {kind.label} category names in {names}")

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
    def minimums(self) -> dict[str, int]:
        """The categories' own lower bounds for their floor, where the table gives one."""
        return {c.name: c.minimum for c in self.categories if c.minimum is not None}

    @property
    def maximums(self) -> dict[str, int]:
        """The categories' own upper bounds for their floor, where the table gives one."""
        return {c.name: c.maximum for c in self.categories if c.maximum is not None}

    def __len__(self) -> int:
        return len(self.categories)

    def __iter__(self) -> Iterator[CardTableCategory]:
        return iter(self.categories)


def parse_card_table[D: CardTableDefinitions](table: object, definitions: type[D]) -> D:
    """
    Build the definitions of a table from its parsed configuration section (module
    docstring). `definitions` is the table's definition class, which names its kind and
    its category class.
    """
    kind = definitions.KIND
    if not isinstance(table, dict):
        raise kind.error(
            f"the {kind.label} table must be a mapping of category name to its queries, cards "
            "and exclude"
        )
    known = {"queries", "cards", "exclude", kind.min_key, kind.max_key}
    categories = []
    for name, entry in table.items():
        if not isinstance(name, str) or not name:
            raise kind.error(f"{kind.label} category names must be strings, got {name!r}")
        if not isinstance(entry, dict):
            raise kind.error(
                f"{kind.label} category {name!r} must be an object with queries, cards or exclude"
            )
        entry_dict: dict[str, object] = {str(key): value for key, value in entry.items()}
        unknown = set(entry_dict) - known
        if unknown:
            raise kind.error(f"{kind.label} category {name!r} has unknown keys {sorted(unknown)}")
        categories.append(
            definitions.CATEGORY(
                name,
                queries=_names(entry_dict, "queries", kind, name),
                cards=_names(entry_dict, "cards", kind, name),
                exclude=frozenset(_names(entry_dict, "exclude", kind, name)),
                minimum=_bound(entry_dict, kind.min_key, kind, name),
                maximum=_bound(entry_dict, kind.max_key, kind, name),
            )
        )
    return definitions(tuple(categories))


def check_query_results(results: Mapping[str, Sequence[str]], kind: TableKind) -> None:
    """A query that matches no card is a table error, not an empty card set."""
    empty = [query for query, cards in results.items() if not cards]
    if empty:
        raise kind.error(
            f"{kind.label} queries match no card on Scryfall: " + ", ".join(repr(q) for q in empty)
        )


@dataclass(frozen=True)
class CardTable:
    """The resolved cards of each category of a table and where each came from."""

    # The sources a card of this table can have, in reporting order
    SOURCES: ClassVar[tuple[str, ...]] = (SOURCE_CARD, SOURCE_QUERY)

    # category (table order) -> card (alphabetical) -> the sources (SOURCES) that named it
    sources: dict[str, dict[str, frozenset[str]]]
    # The table's own bounds for the floor, for the categories that give one
    minimums: dict[str, int] = field(default_factory=dict)
    maximums: dict[str, int] = field(default_factory=dict)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.sources)

    def cards(self, name: str) -> frozenset[str]:
        """The cards of a category."""
        return frozenset(self.sources[name])

    @property
    def all_cards(self) -> frozenset[str]:
        """Every card of every category."""
        return frozenset(card for cards in self.sources.values() for card in cards)

    def cards_by_category(self) -> dict[str, frozenset[str]]:
        """Category -> its cards, for the card inclusion statistics."""
        return {name: self.cards(name) for name in self.names}

    def source_counts(self, name: str) -> dict[str, int]:
        """How many of a category's cards each source named (a card can have several)."""
        return {
            source: sum(1 for sources in self.sources[name].values() if source in sources)
            for source in self.SOURCES
        }

    def without(self, names: Collection[str]) -> Self:
        """The table without the named cards (a table card Scryfall does not know)."""
        dropped = set(names)
        return replace(
            self,
            sources={
                name: {card: s for card, s in by_card.items() if card not in dropped}
                for name, by_card in self.sources.items()
            },
        )

    def selected_sources(self, selected_cards: Collection[str]) -> dict[str, dict[str, list[str]]]:
        """The cards of each category in a cube, each with its sources (for the statistics)."""
        selected = set(selected_cards)
        return {
            name: {
                card: sorted(sources)
                for card, sources in sorted(by_card.items())
                if card in selected
            }
            for name, by_card in self.sources.items()
        }


def resolve_sources(
    definitions: CardTableDefinitions,
    query_results: Mapping[str, Sequence[str]],
    blocklist: Collection[str] = frozenset(),
    query_limit: int = QUERY_CARD_LIMIT,
    extra_sources: Mapping[str, Sequence[tuple[Iterable[str], str]]] | None = None,
) -> dict[str, dict[str, frozenset[str]]]:
    """
    The card set of each category with the source of every card (CardTable.sources): the
    table's cards and the first `query_limit` cards of each query that are neither blocked
    nor excluded (a query missing from `query_results` adds nothing), plus any
    `extra_sources` of the category (name lists with their source label, named before the
    table's cards), minus the exclusions and the blocklist. An exclusion that matches no
    card of its category is a warning (a typo excludes nothing).
    """
    kind = definitions.KIND
    blocked = set(blocklist)
    sources: dict[str, dict[str, frozenset[str]]] = {}
    for category in definitions:
        found: dict[str, set[str]] = {}
        seen: set[str] = set()
        named: list[tuple[Iterable[str], str]] = [
            *(extra_sources or {}).get(category.name, ()),
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
                f"{kind.label.capitalize()} table: the exclusions {unused} of "
                f"{category.name!r} match no card of the category"
            )
        sources[category.name] = {
            card: frozenset(card_sources) for card, card_sources in sorted(found.items())
        }
    return sources
