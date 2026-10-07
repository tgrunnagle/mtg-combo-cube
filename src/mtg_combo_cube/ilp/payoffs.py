"""
Payoff cards: the outlets that turn an engine into a win.

The outcome rules keep engines of every kind in the cube (infinite mana, an infinite storm
count, infinite tokens, life or counters), but an engine is only as good as what the cube
lets a drafter do with it. A payoff table names, per non-terminal outcome category, how to
find its outlets:

    {
      "mana": {"queries": ["o:\\"{X}\\" o:\\"X damage\\" -t:land"],
               "exclude": ["Chromatic Orrery"]},
      "storm": {"queries": ["keyword:storm f:commander"]},
      "tokens": {"cards": ["Impact Tremors"]}
    }

Every category must be a category of the outcome table; the categories the payoff table
leaves out (damage, draw, mill, ...) are terminal and are their own payoff. A category gives
Scryfall `queries` (resolved by the PayoffFetcher in EDHREC order; the first QUERY_CARD_LIMIT
cards count), explicit `cards`, and `exclude`, names dropped from the category whatever
their source. A category needs at least one of the three.

The payoff set of a category is the union of the inferred cards (infer_payoffs: the cards a
bundled Spellbook variant adds to the engine it includes, kept from the confidence
threshold on), the table's cards and the query results, minus the exclusions and the
blocklist (resolve_payoffs). The Phase 2 payoff floor asks for a minimum of them per
category; a payoff card in no combo is added to the candidate pool for that.
"""

import json
import logging
from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from mtg_combo_cube.ilp.ilp_models import ComboData, PayoffStats
from mtg_combo_cube.ilp.outcomes import OutcomeCategories

logger = logging.getLogger(__name__)

DEFAULT_PAYOFFS_PATH = "data/payoffs.json"
# Bundled variants a card must be the outlet of before the inference counts it
DEFAULT_INFERENCE_THRESHOLD = 2
QUERY_CARD_LIMIT = 25  # cards kept per query, in EDHREC order

# Where a payoff card came from
SOURCE_INFERRED = "inferred"
SOURCE_CARD = "card"
SOURCE_QUERY = "query"
SOURCES = (SOURCE_INFERRED, SOURCE_CARD, SOURCE_QUERY)


class PayoffTableError(ValueError):
    """An invalid payoff table, or a query of it that matches no card."""


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


@dataclass(frozen=True)
class PayoffCategory:
    """How the payoff cards of one outcome category are found."""

    name: str
    queries: tuple[str, ...] = ()  # Scryfall queries
    cards: tuple[str, ...] = ()  # explicit card names
    exclude: frozenset[str] = frozenset()  # names dropped whatever their source

    def __post_init__(self) -> None:
        if not (self.queries or self.cards or self.exclude):
            raise PayoffTableError(
                f"payoff category {self.name!r} has no queries, cards or exclusions"
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
    """Build the payoff definitions from the parsed JSON object (see the module docstring)."""
    if not isinstance(table, dict):
        raise PayoffTableError("the payoff table must be a JSON object")
    categories = []
    for name, entry in table.items():
        if not isinstance(name, str) or not name:
            raise PayoffTableError(f"payoff category names must be strings, got {name!r}")
        if not isinstance(entry, dict):
            raise PayoffTableError(
                f"payoff category {name!r} must be an object with queries, cards or exclude"
            )
        entry_dict: dict[str, object] = {str(key): value for key, value in entry.items()}
        unknown = set(entry_dict) - {"queries", "cards", "exclude"}
        if unknown:
            raise PayoffTableError(f"payoff category {name!r} has unknown keys {sorted(unknown)}")
        categories.append(
            PayoffCategory(
                name,
                queries=_names(entry_dict, "queries", name),
                cards=_names(entry_dict, "cards", name),
                exclude=frozenset(_names(entry_dict, "exclude", name)),
            )
        )
    return PayoffDefinitions(tuple(categories))


def load_payoff_table(path: str | None = None) -> PayoffDefinitions:
    """
    Load the payoff table from a JSON file (the default table when no path is given).

    Raises FileNotFoundError when the file does not exist and PayoffTableError when the
    table is invalid.
    """
    table_path = Path(path) if path else Path(DEFAULT_PAYOFFS_PATH)
    if not table_path.exists():
        raise FileNotFoundError(f"Payoff table not found: {table_path}")
    with open(table_path, encoding="utf-8") as f:
        try:
            table = json.load(f)
        except json.JSONDecodeError as e:
            raise PayoffTableError(f"{table_path} is not valid JSON: {e}") from e
    return parse_payoff_table(table)


def resolve_payoff_table(path: str | None, *, required: bool = True) -> PayoffDefinitions | None:
    """
    The payoff table a run should use: the file at `path`, or the default table.

    A path that was given must exist. Without one, the default table is read from the
    working directory, as every `data/` path is; when it is missing there, that is an error
    only when the table is `required` (the payoff floor is on), otherwise None and the run
    goes on without payoffs.
    """
    if path is None and not required and not Path(DEFAULT_PAYOFFS_PATH).exists():
        return None
    return load_payoff_table(path)


def resolve_payoff_definitions(
    path: str | None,
    outcome_categories: OutcomeCategories | None,
    *,
    required: bool,
    log: bool = True,
) -> PayoffDefinitions | None:
    """
    The payoff table a run uses, checked against the outcome table, or None when there is
    none to use.

    The table is `required` when the payoff floor is on: it must then exist (the default in
    the working directory, or the given path) and name outcome categories only, and the
    outcome table must be given; each failure raises (FileNotFoundError or
    PayoffTableError). With the floor off the default table is used when it fits: when it
    is missing, or names categories the outcome table does not have, or there is no outcome
    table to place its categories in, payoffs are skipped. A table given by path must always
    exist and fit. What happens is logged unless `log` is off (a check before the run).
    """
    definitions = resolve_payoff_table(path, required=required)
    given = path is not None
    if definitions is None:
        if log:
            logger.info(
                f"No payoff table at {DEFAULT_PAYOFFS_PATH} (run from the repository root or "
                "pass --payoffs); payoffs are skipped"
            )
        return None
    if outcome_categories is None:
        if required or given:
            raise PayoffTableError(
                "the payoff table needs the outcome category table to place its categories"
            )
        if log:
            logger.warning(
                "The default payoff table needs the outcome category table to place its "
                "categories; payoffs are skipped"
            )
        return None
    try:
        definitions.check_categories(outcome_categories)
    except PayoffTableError as e:
        if required or given:
            raise
        if log:
            logger.warning(
                f"The default payoff table does not fit the outcome table ({e}); payoffs "
                "are skipped"
            )
        return None
    if log:
        logger.info(
            f"Loaded payoff table with {len(definitions)} categories: "
            f"{', '.join(definitions.names)}"
        )
    return definitions


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
    terminal result (a category outside `engine_categories`), the engine is a variant in
    the pool whose `includes` are a strict subset of the bundled variant's (the engine may
    itself bundle a smaller combo), whose cards are a strict subset and whose categories are
    engine categories only; the cards the bundled variant adds are the outlet, credited to
    each of the engine's categories. A card is counted once per bundled variant.
    """
    engines = frozenset(engine_categories)
    variant_categories = {combo.id: categories.categorize(combo.features) for combo in combos}
    by_included: dict[int, list[ComboData]] = {}
    for combo in combos:
        for included in combo.includes:
            by_included.setdefault(included, []).append(combo)

    credits: dict[str, dict[str, set[str]]] = {name: {} for name in engine_categories}
    for bundled in combos:
        if len(bundled.includes) < 2 or not variant_categories[bundled.id] - engines:
            continue
        candidates = {
            engine.id: engine
            for included in bundled.includes
            for engine in by_included.get(included, [])
            if engine.includes < bundled.includes
        }
        for engine in candidates.values():
            engine_outcomes = variant_categories[engine.id]
            if not engine_outcomes or not engine_outcomes <= engines:
                continue
            if not engine.required_cards < bundled.required_cards:
                continue
            for name in engine_outcomes:
                for card in bundled.required_cards - engine.required_cards:
                    credits[name].setdefault(card, set()).add(bundled.id)
    return {
        name: Counter({card: len(ids) for card, ids in cards.items()})
        for name, cards in credits.items()
    }


@dataclass(frozen=True)
class PayoffTable:
    """The resolved payoff cards of each category and where each came from."""

    # category -> card -> the sources (SOURCES) that named it, in table order
    sources: dict[str, dict[str, frozenset[str]]]
    # category -> card -> bundled variants the inference found it the outlet of, every
    # count (also below the threshold), so the table can be tuned from a run
    inferred: dict[str, dict[str, int]]
    inference_threshold: int

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

    def stats(self, selected_cards: Collection[str]) -> PayoffStats:
        """The payoff cards of each category in a cube, with the source of each."""
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
            cards_per_category={name: len(found) for name, found in cards.items()}, cards=cards
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
    cards and the first `query_limit` unblocked cards of each query (a query missing from
    `query_results` adds nothing), minus the exclusions and the blocklist.
    """
    blocked = set(blocklist)
    sources: dict[str, dict[str, frozenset[str]]] = {}
    for category in definitions:
        found: dict[str, set[str]] = {}
        counts = inferred.get(category.name, Counter())
        named: list[tuple[Iterable[str], str]] = [
            (
                (card for card, count in counts.items() if count >= inference_threshold),
                SOURCE_INFERRED,
            ),
            (category.cards, SOURCE_CARD),
        ]
        for query in category.queries:
            unblocked = [card for card in query_results.get(query, []) if card not in blocked]
            named.append((unblocked[:query_limit], SOURCE_QUERY))
        for names, source in named:
            for name in names:
                if name not in category.exclude and name not in blocked:
                    found.setdefault(name, set()).add(source)
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
    )
