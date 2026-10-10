"""
The blocked cards of a run: the configuration's blocklist and the cards matching its blocklist
queries (Scryfall searches, such as `t:stickers` for every sticker sheet).

The queries are resolved before the instance is loaded, through the payoff query cache (one
entry per search URL), so every place that applies the blocklist (the combos, the template
searches, the payoff table) leaves their cards out too. A query is resolved to the first
result page, PAGE_SIZE cards, with a warning when it may have more.
"""

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from mtg_combo_cube.config import ConfigError
from mtg_combo_cube.scryfall.payoff_fetcher import PayoffFetcher
from mtg_combo_cube.scryfall.scryfall_fetcher import ScryfallFetcher

logger = logging.getLogger(__name__)

PAGE_SIZE = 175  # cards per Scryfall search result page


class BlocklistQueryError(ConfigError):
    """A blocklist query Scryfall rejects or only partly understands: fix the configuration."""


class BlocklistFetchError(RuntimeError):
    """A blocklist query could not be fetched (a transient problem); the run cannot go on."""


def blocked_by_queries(
    queries: Sequence[str],
    results: Mapping[str, Sequence[str]],
    rejected: Mapping[str, str],
) -> frozenset[str]:
    """
    The cards the resolved queries block. A rejected query is a BlocklistQueryError and a
    query without results a BlocklistFetchError: a missing query would let its cards into
    the cube. A query that matches no card is a warning (it blocks nothing).
    """
    if rejected:
        raise BlocklistQueryError(
            "blocklist queries rejected by Scryfall (fix the configuration): "
            + "; ".join(f"{query!r} ({reason})" for query, reason in rejected.items())
        )
    missing = [query for query in queries if query not in results]
    if missing:
        raise BlocklistFetchError(
            "blocklist queries could not be fetched from Scryfall (run again): "
            + ", ".join(repr(query) for query in missing)
        )
    blocked: set[str] = set()
    for query in queries:
        cards = results[query]
        if not cards:
            logger.warning(f"Blocklist query matches no card on Scryfall: {query!r}")
        elif len(cards) >= PAGE_SIZE:
            logger.warning(
                f"Blocklist query {query!r} blocks the first {PAGE_SIZE} cards it matches only; "
                "narrow it, or split it into several queries"
            )
        blocked.update(cards)
    return frozenset(blocked)


async def fetch_blocklist(
    blocklist: frozenset[str],
    queries: Sequence[str],
    enable_cache_write: bool = True,
    read_cache: bool = False,
    cache_dir: Path = Path("data/cache"),
    fetcher: ScryfallFetcher | None = None,
) -> frozenset[str]:
    """
    The blocked card names of a run: the blocklist and the cards of the blocklist queries.

    Args:
        blocklist: The blocked card names of the configuration
        queries: The blocklist queries of the configuration
        enable_cache_write: Write fetched query results to the cache file
        read_cache: Serve query results from the cache file when present
        cache_dir: Directory for the cache file (default: data/cache)
        fetcher: Fetcher for the HTTP requests; one is created (and closed) when omitted

    Raises BlocklistQueryError for a query Scryfall rejects, BlocklistFetchError for one
    that could not be fetched.
    """
    if not queries:
        return blocklist
    if fetcher is None:
        async with ScryfallFetcher() as owned:
            return await fetch_blocklist(
                blocklist, queries, enable_cache_write, read_cache, cache_dir, fetcher=owned
            )
    query_fetcher = PayoffFetcher(
        fetcher,
        cache_dir=cache_dir,
        enable_read=read_cache,
        enable_write=enable_cache_write,
        label="Blocklist",
    )
    results = await query_fetcher.fetch_queries(queries)
    blocked = blocked_by_queries(queries, results, query_fetcher.rejected)
    logger.info(
        f"Blocklist queries block {len(blocked - blocklist)} more cards ({len(queries)} queries)"
    )
    return blocklist | blocked
