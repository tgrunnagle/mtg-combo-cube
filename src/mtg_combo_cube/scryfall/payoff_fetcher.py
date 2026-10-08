"""Resolves the payoff table's Scryfall queries to card names, with a cache."""

import json
import logging
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from mtg_combo_cube.scryfall.scryfall_fetcher import ScryfallFetcher

logger = logging.getLogger(__name__)


class PayoffFetcher:
    """
    Fetches the cards matching the payoff table's Scryfall queries.

    Requests go through a ScryfallFetcher, which provides rate limiting and retries. Every
    query runs in EDHREC order over paper cards; the raw ordered names of the first result
    page are returned and cached, so the caller applies the blocklist and the card limit
    (as for the template searches) and the cache stays valid when those change. A query is
    cached under its search URL, so editing a query in the table fetches the edited query
    and leaves the rest cached.

    The table is edited by hand, so the two ways a bad query fails quietly are caught: a
    query Scryfall rejects (HTTP 4xx) and a query it only partly understands (a 200 with
    `warnings` naming the ignored terms, whose results would be a wider search). Both are
    recorded in `rejected` with the reason, left out of the results and not cached.
    """

    CACHE_FILENAME = "scryfall_payoffs.json"
    CACHE_VERSION = 1
    SEARCH_URL = "https://api.scryfall.com/cards/search"
    QUERY_SUFFIX = "game:paper"  # added to every query: no digital-only cards

    def __init__(
        self,
        fetcher: ScryfallFetcher,
        cache_dir: Path = Path("data/cache"),
        enable_read: bool = False,
        enable_write: bool = False,
    ):
        """
        Initialize the payoff fetcher.

        Args:
            fetcher: Fetcher used for the HTTP requests
            cache_dir: Directory for the cache file (default: data/cache)
            enable_read: Serve query results from the cache file when present
            enable_write: Write fetched results to the cache file
        """
        self._fetcher = fetcher
        self.cache_path = cache_dir / self.CACHE_FILENAME
        self.enable_read = enable_read
        self.enable_write = enable_write
        # Queries Scryfall rejected or only partly understood in this run: query -> reason.
        # A table error for the caller; never cached, and not asked for again.
        self.rejected: dict[str, str] = {}

    @classmethod
    def search_url(cls, query: str) -> str:
        """
        The Scryfall search URL of a payoff query: EDHREC order, one entry per card. The
        query is parenthesized before the paper filter is added, so a top-level `or` in the
        table applies to the whole query (Scryfall's implicit AND binds tighter than `or`).
        """
        params = {"q": f"({query}) {cls.QUERY_SUFFIX}", "order": "edhrec", "unique": "cards"}
        return f"{cls.SEARCH_URL}?{urlencode(params)}"

    async def fetch_queries(self, queries: Iterable[str]) -> dict[str, list[str]]:
        """
        Get the raw, ordered card names matching each query.

        Returns:
            Query -> card names (empty when no card matches). A query whose request failed
            (a network error, or HTTP 429 / 5xx after the retries) is left out with a
            warning; a query Scryfall rejected or partly ignored is left out and recorded in
            `rejected`. Empty results are not cached: a query that matches nothing is a
            table error for the caller to report, and may be fixed on Scryfall.
        """
        wanted = list(dict.fromkeys(queries))
        urls = {query: self.search_url(query) for query in wanted}
        cached: dict[str, list[str]] = {}
        if self.enable_read:
            try:
                cached = self._load_cache()
            except OSError as e:
                logger.warning(f"Cache read error for {self.cache_path.name}: {e}")
        results = {query: cached[urls[query]] for query in wanted if urls[query] in cached}

        fetched: dict[str, list[str]] = {}
        for query in wanted:
            if query in results or query in self.rejected:
                continue
            data = await self._fetcher.request_json(urls[query])
            if data is None:
                status = self._fetcher.last_status
                if status is not None and status != 429 and 400 <= status < 500:
                    self.rejected[query] = f"HTTP {status}"
                    logger.warning(f"Payoff query rejected by Scryfall (HTTP {status}): {query!r}")
                else:
                    logger.warning(f"Payoff query could not be fetched: {query!r}")
                continue
            if warnings := data.get("warnings"):
                reason = "; ".join(str(warning) for warning in warnings)
                self.rejected[query] = reason
                logger.warning(
                    f"Payoff query only partly understood by Scryfall ({reason}): {query!r}"
                )
                continue
            fetched[query] = [card["name"] for card in data.get("data", [])]

        if self.enable_write and (to_cache := {urls[q]: n for q, n in fetched.items() if n}):
            self._write_cache(to_cache)

        results.update(fetched)
        return results

    def _load_cache(self) -> dict[str, list[str]]:
        """The cached card names by search URL (see _load_entries)."""
        return {url: entry["cards"] for url, entry in self._load_entries().items()}

    def _load_entries(self) -> dict[str, dict]:
        """
        Load the cache file: search URL -> entry (`cards`, `fetched_at`). A missing or
        outdated file counts as empty, and so does a file with a malformed entry: the whole
        file is then dropped and rewritten with this run's results, as the card attribute
        cache does. A file that cannot be read at all raises OSError, so a transient lock
        does not look like an empty cache.
        """
        if not self.cache_path.exists():
            return {}
        with open(self.cache_path, encoding="utf-8") as f:
            text = f.read()
        try:
            data = json.loads(text)
            if data.get("version") != self.CACHE_VERSION:
                raise ValueError(f"unsupported cache version {data.get('version')}")
            return {url: self._parse_entry(entry) for url, entry in data["queries"].items()}
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            logger.warning(f"Cache read error for {self.cache_path.name}: {e}")
            return {}

    @staticmethod
    def _parse_entry(entry: dict) -> dict:
        """A validated cache entry; a wrong type is a ValueError (the cache is unusable)."""
        cards = entry["cards"]
        if not isinstance(cards, list) or not all(isinstance(card, str) for card in cards):
            raise ValueError(f"malformed cache entry {entry!r}")
        fetched_at = entry.get("fetched_at", "")
        if not isinstance(fetched_at, str):
            raise ValueError(f"malformed cache entry {entry!r}")
        return {"cards": list(cards), "fetched_at": fetched_at}

    def _write_cache(self, fetched: dict[str, list[str]]) -> None:
        """
        Add fetched results to the cache file, each with the time it was fetched. The
        entries already in the file are kept when the file is valid (see _load_entries); the
        file is left alone when it cannot be read, so a transient error does not erase it.
        """
        try:
            entries = self._load_entries()
        except OSError as e:
            logger.warning(f"Cache not updated: {self.cache_path.name} could not be read ({e})")
            return
        fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
        for url, cards in fetched.items():
            entries[url] = {"cards": cards, "fetched_at": fetched_at}
        temp_path = self.cache_path.with_name(f"{self.cache_path.name}.tmp")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump({"version": self.CACHE_VERSION, "queries": entries}, f)
            os.replace(temp_path, self.cache_path)
        except OSError as e:
            logger.warning(f"Cache write error for {self.cache_path.name}: {e}")
