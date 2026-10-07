"""Fill the API caches ahead of a build, so the build makes no network requests.

Usage:
    uv run python -m mtg_combo_cube.precache -n 20000

Downloads what an ILP build with the same settings reads: the Commander Spellbook variants,
the Scryfall template searches, the payoff table's Scryfall queries and the Scryfall card
attributes (color identity, type line, mana value) of the candidate and payoff cards.

Everything is fetched again and written over what the cache holds: the variants file is
replaced, and so is every template, payoff query and card attribute entry of this
configuration. Entries that only other configurations use are left alone. With
--keep-existing, entries already in the cache are kept instead, so an incomplete run can be
finished without starting over.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

from mtg_combo_cube.blocklist import load_blocklist
from mtg_combo_cube.ilp.combo_preprocessor import ComboPreprocessor
from mtg_combo_cube.ilp.payoffs import (
    PayoffDefinitions,
    PayoffTableError,
    resolve_payoff_table,
    resolve_payoffs,
)
from mtg_combo_cube.models import Variant
from mtg_combo_cube.scryfall.card_attribute_fetcher import CardAttributeFetcher
from mtg_combo_cube.scryfall.payoff_fetcher import PayoffFetcher
from mtg_combo_cube.scryfall.scryfall_fetcher import ScryfallFetcher
from mtg_combo_cube.spellbook.api_cache import SpellbookCache
from mtg_combo_cube.spellbook.commander_spellbook import CommanderSpellbook

logger = logging.getLogger(__name__)

MAX_RETRY_WAIT_SECONDS = 300.0

Sleep = Callable[[float], Awaitable[None]]


@dataclass
class PrecacheResult:
    """What a precache run found and what it could not fetch."""

    variants: int = 0
    variants_cached: bool = False
    failed_templates: int = 0
    payoff_queries: int = 0
    failed_payoff_queries: int = 0
    empty_payoff_queries: list[str] = field(default_factory=list)  # a table error for a build
    cards: int = 0
    cards_without_attributes: int = 0
    failed_attribute_requests: int = 0

    @property
    def complete(self) -> bool:
        """
        True when the variants file is in place, no request was left failing and every
        payoff query matches a card (a build with the same table fails otherwise).
        """
        return (
            self.variants_cached
            and self.failed_templates == 0
            and self.failed_payoff_queries == 0
            and not self.empty_payoff_queries
            and self.failed_attribute_requests == 0
        )


def _is_retryable(error: Exception) -> bool:
    """Network errors and HTTP 429 / 5xx may pass with time; other HTTP errors will not."""
    if isinstance(error, aiohttp.ClientResponseError):
        return error.status == 429 or error.status >= 500
    return True


async def _run_passes(
    stage: str,
    run_pass: Callable[[], Awaitable[int]],
    max_passes: int,
    retry_wait_seconds: float,
    sleep: Sleep,
) -> int:
    """
    Repeat a stage until a pass leaves no failure, waiting twice as long before each new pass.

    The clients retry single requests on their own; a new pass covers outages that outlast
    those retries.

    Returns:
        The number of failures left by the last pass (0 when the stage completed).
    """
    failures = 0
    for attempt in range(1, max_passes + 1):
        failures = await run_pass()
        if failures == 0:
            break
        if attempt < max_passes:
            delay = min(retry_wait_seconds * 2 ** (attempt - 1), MAX_RETRY_WAIT_SECONDS)
            logger.warning(
                f"{stage}: {failures} failed; pass {attempt + 1} of {max_passes} in {delay:.0f}s"
            )
            await sleep(delay)
    return failures


async def _precache_variants(
    spellbook: CommanderSpellbook,
    cache: SpellbookCache,
    max_cards_in_combo: int,
    max_variants: int,
    keep_existing: bool,
    max_passes: int,
    retry_wait_seconds: float,
    sleep: Sleep,
) -> tuple[list[Variant], bool] | None:
    """
    Download the variants and write them to the cache file, replacing an existing one.

    Returns:
        The variants and whether they are in the cache file, or None when the download kept
        failing (an existing cache file is then left as it was).
    """
    cache_path = cache.variants_cache_path(max_cards_in_combo, max_variants)
    if keep_existing and (cached := cache.read_variants_cache(cache_path)) is not None:
        return cached, True

    variants: list[Variant] = []

    async def run_pass() -> int:
        # The listing cannot be resumed, so a pass starts over
        variants.clear()
        try:
            async for variant in spellbook.get_variants(
                max_cards_in_combo=max_cards_in_combo, max_variants=max_variants
            ):
                variants.append(variant)
        except (aiohttp.ClientError, TimeoutError) as e:
            if not _is_retryable(e):
                raise
            logger.warning(f"Commander Spellbook download failed ({type(e).__name__}: {e})")
            return 1
        return 0

    failures = await _run_passes(
        "Commander Spellbook variants", run_pass, max_passes, retry_wait_seconds, sleep
    )
    if failures:
        return None
    return variants, cache.write_variants_cache(cache_path, variants)


async def _precache_templates(
    variants: list[Variant],
    blocklist: frozenset[str],
    fetcher: ScryfallFetcher,
    max_passes: int,
    retry_wait_seconds: float,
    sleep: Sleep,
) -> tuple[list[str], int]:
    """
    Resolve the template requirements the way a build does, which fills the template cache.

    Returns:
        - Names of the candidate cards of the instance
        - Number of templates whose fetch still failed in the last pass
    """
    card_names: list[str] = []

    async def run_pass() -> int:
        # Results of earlier passes stay in the fetcher; only the failures are requested again
        fetcher.clear_failures()
        preprocessor = ComboPreprocessor(blocklist=blocklist, fetcher=fetcher)
        _, candidate_cards = await preprocessor.preprocess_variants(variants)
        card_names[:] = sorted(candidate_cards)
        return fetcher.failed_url_count

    failures = await _run_passes(
        "Scryfall templates", run_pass, max_passes, retry_wait_seconds, sleep
    )
    return card_names, failures


async def _precache_payoffs(
    definitions: PayoffDefinitions,
    blocklist: frozenset[str],
    payoff_fetcher: PayoffFetcher,
    fetcher: ScryfallFetcher,
    max_passes: int,
    retry_wait_seconds: float,
    sleep: Sleep,
) -> tuple[list[str], int, list[str]]:
    """
    Resolve the payoff table's queries the way a build does, which fills the payoff cache.

    Returns:
        - Names of the payoff cards a build adds to the candidates (the table's cards and the
          query results, after the blocklist and the per-query limit)
        - Number of queries whose fetch still failed in the last pass
        - Queries that match no card (a table error for a build)
    """
    results: dict[str, list[str]] = {}

    async def run_pass() -> int:
        fetcher.clear_failures()
        results.update(await payoff_fetcher.fetch_queries(definitions.queries))
        return fetcher.failed_url_count

    failures = await _run_passes(
        "Scryfall payoff queries", run_pass, max_passes, retry_wait_seconds, sleep
    )
    empty = [query for query, cards in results.items() if not cards]
    payoffs = resolve_payoffs(definitions, {}, results, blocklist)
    return sorted(payoffs.all_cards), failures, empty


async def _precache_attributes(
    card_names: list[str],
    attribute_fetcher: CardAttributeFetcher,
    fetcher: ScryfallFetcher,
    max_passes: int,
    retry_wait_seconds: float,
    sleep: Sleep,
) -> tuple[int, int]:
    """
    Look up the attributes of every card, which fills the card attribute cache.

    Returns:
        - Number of cards left without Scryfall data
        - Number of requests that still failed in the last pass
    """
    remaining = list(card_names)

    async def run_pass() -> int:
        fetcher.clear_failures()
        attributes = await attribute_fetcher.fetch_attributes(remaining)
        remaining[:] = [name for name in remaining if name not in attributes]
        return fetcher.failed_requests

    failures = await _run_passes(
        "Scryfall card attributes", run_pass, max_passes, retry_wait_seconds, sleep
    )
    return len(remaining), failures


async def precache(
    max_cards_in_combo: int = 4,
    max_variants: int = 20000,
    blocklist: frozenset[str] = frozenset(),
    cache_dir: Path = Path("data/cache"),
    keep_existing: bool = False,
    max_passes: int = 3,
    retry_wait_seconds: float = 30.0,
    spellbook: CommanderSpellbook | None = None,
    session: aiohttp.ClientSession | None = None,
    sleep: Sleep = asyncio.sleep,
    payoffs: PayoffDefinitions | None = None,
) -> PrecacheResult:
    """
    Fill the variants, Scryfall template, payoff query and card attribute caches for one
    build configuration.

    Args:
        max_cards_in_combo: Largest combo size to fetch (part of the variants cache key)
        max_variants: Maximum number of combo variants (part of the variants cache key)
        blocklist: Blocked card names; decides which templates and cards a build needs
        cache_dir: Directory for the cache files (default: data/cache)
        keep_existing: Keep entries already in the cache instead of fetching them again and
            writing over them
        max_passes: Passes per stage before giving up on requests that keep failing
        retry_wait_seconds: Wait before the second pass; doubles for every further pass
        spellbook: Commander Spellbook client to use; one is created when omitted
        session: HTTP session for the Scryfall requests; the fetchers create one when omitted
        sleep: Awaitable sleep function (replaceable in tests)
        payoffs: The payoff table whose queries to resolve, and whose cards to look up the
            attributes of beside the candidate cards (None: no payoff stage)
    """
    result = PrecacheResult()

    fetched = await _precache_variants(
        spellbook if spellbook is not None else CommanderSpellbook(),
        SpellbookCache(cache_dir=cache_dir),
        max_cards_in_combo,
        max_variants,
        keep_existing,
        max_passes,
        retry_wait_seconds,
        sleep,
    )
    if fetched is None:
        return result
    variants, result.variants_cached = fetched
    result.variants = len(variants)

    template_fetcher = ScryfallFetcher(
        cache_dir=cache_dir,
        enable_read=keep_existing,
        enable_write=True,
        session=session,
        sleep=sleep,
    )
    card_names, result.failed_templates = await _precache_templates(
        variants, blocklist, template_fetcher, max_passes, retry_wait_seconds, sleep
    )

    async with ScryfallFetcher(session=session, sleep=sleep) as fetcher:
        if payoffs is not None:
            payoff_fetcher = PayoffFetcher(
                fetcher, cache_dir=cache_dir, enable_read=keep_existing, enable_write=True
            )
            (
                payoff_cards,
                result.failed_payoff_queries,
                result.empty_payoff_queries,
            ) = await _precache_payoffs(
                payoffs, blocklist, payoff_fetcher, fetcher, max_passes, retry_wait_seconds, sleep
            )
            result.payoff_queries = len(payoffs.queries)
            # A build adds the payoff cards to the candidates and looks them up too
            card_names = sorted(set(card_names) | set(payoff_cards))
        result.cards = len(card_names)

        attribute_fetcher = CardAttributeFetcher(
            fetcher, cache_dir=cache_dir, enable_read=keep_existing, enable_write=True
        )
        (
            result.cards_without_attributes,
            result.failed_attribute_requests,
        ) = await _precache_attributes(
            card_names, attribute_fetcher, fetcher, max_passes, retry_wait_seconds, sleep
        )

    return result


if __name__ == "__main__":
    argparser = argparse.ArgumentParser(
        description="Download the Commander Spellbook and Scryfall data a build reads from "
        "its cache. Use the same -n, --max-cards-in-combo and --blocklist as the build."
    )
    argparser.add_argument(
        "-n",
        "--max-variants",
        type=int,
        default=20000,
        help="Maximum number of combo variants, as used for the build (default: 20000)",
    )
    argparser.add_argument(
        "--max-cards-in-combo",
        type=int,
        default=4,
        help="Largest combo size to fetch (default: 4, the size a build uses)",
    )
    argparser.add_argument(
        "--blocklist",
        type=str,
        default=None,
        help="Path to blocklist file (default: data/blocklist.txt)",
    )
    argparser.add_argument(
        "--payoffs",
        type=str,
        default=None,
        help="Path to the payoff table whose Scryfall queries to resolve (default: "
        "data/payoffs.json; skipped when that is missing)",
    )
    argparser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache"),
        help="Directory for the cache files (default: data/cache, where a build reads them)",
    )
    argparser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Keep entries already in the cache and fetch only what is missing "
        "(default: fetch everything again and write over the cached data)",
    )
    argparser.add_argument(
        "--max-passes",
        type=int,
        default=3,
        help="Passes per stage before giving up on requests that keep failing (default: 3)",
    )
    argparser.add_argument(
        "--retry-wait",
        type=float,
        default=30.0,
        help="Seconds to wait before the second pass; doubles for every further pass, "
        f"up to {MAX_RETRY_WAIT_SECONDS:.0f} (default: 30)",
    )
    argparser.add_argument("-d", "--debug", action="store_true")
    args = argparser.parse_args()
    if args.max_variants < 1 or args.max_cards_in_combo < 1 or args.max_passes < 1:
        argparser.error("--max-variants, --max-cards-in-combo and --max-passes must be at least 1")
    try:
        payoffs = resolve_payoff_table(args.payoffs, required=False)
    except (FileNotFoundError, PayoffTableError) as e:
        argparser.error(str(e))
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    result = asyncio.run(
        precache(
            max_cards_in_combo=args.max_cards_in_combo,
            max_variants=args.max_variants,
            blocklist=load_blocklist(args.blocklist),
            cache_dir=args.cache_dir,
            keep_existing=args.keep_existing,
            max_passes=args.max_passes,
            retry_wait_seconds=args.retry_wait,
            payoffs=payoffs,
        )
    )

    print(f"Cache directory: {args.cache_dir}")
    print(f"Variants: {result.variants}" + ("" if result.variants_cached else " (NOT cached)"))
    print(f"Scryfall templates still failing: {result.failed_templates}")
    if payoffs is not None:
        print(
            f"Payoff queries: {result.payoff_queries}, {result.failed_payoff_queries} still failing"
        )
        for query in result.empty_payoff_queries:
            print(f"Payoff query matches no card (fix the table): {query!r}")
    print(
        f"Card attributes: {result.cards - result.cards_without_attributes} of {result.cards} "
        f"cards, {result.failed_attribute_requests} requests still failing"
    )
    if result.empty_payoff_queries:
        print("The payoff table has queries that match no card; a build with it fails.")
        sys.exit(1)
    if not result.complete:
        print("Cache is INCOMPLETE; run again with --keep-existing to fetch what is missing.")
        sys.exit(1)
    print("Cache is complete.")
