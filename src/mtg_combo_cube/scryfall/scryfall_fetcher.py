"""Shared Scryfall template fetcher with a persistent cache, rate limiting, and retries."""

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import TracebackType
from typing import Self

import aiohttp

logger = logging.getLogger(__name__)


class ScryfallFetcher:
    """
    Fetches the card names matching a Scryfall search URL.

    Results are the raw, ordered card names of the first result page. Callers apply their own
    blocklist and limit, so the cache stays valid when those change.

    Use as an async context manager (or call close()) so the shared HTTP session is closed and
    pending cache entries are written.
    """

    CACHE_FILENAME = "scryfall_templates.json"
    CACHE_VERSION = 1
    USER_AGENT = "mtg-combo-cube/0.1.0 (+https://github.com/tgrunnagle/mtg-combo-cube)"
    REQUEST_TIMEOUT_SECONDS = 30

    def __init__(
        self,
        cache_dir: Path = Path("data/cache"),
        enable_read: bool = False,
        enable_write: bool = False,
        min_interval_seconds: float = 0.1,
        max_attempts: int = 5,
        backoff_seconds: float = 2.0,
        max_backoff_seconds: float = 60.0,
        flush_every: int = 50,
        session: aiohttp.ClientSession | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        """
        Initialize the fetcher.

        Args:
            cache_dir: Directory for the cache file (default: data/cache)
            enable_read: Serve results from the cache file when present
            enable_write: Write fetched results to the cache file
            min_interval_seconds: Minimum delay between two Scryfall requests
            max_attempts: Attempts per URL before giving up on 429 / 5xx / network errors
            backoff_seconds: First retry delay; doubles on every further attempt
            max_backoff_seconds: Upper bound for a retry delay (also caps Retry-After)
            flush_every: Write the cache file after this many new results
            session: HTTP session to use; one is created on first request when omitted
            sleep: Awaitable sleep function (replaceable in tests)
        """
        self.cache_path = cache_dir / self.CACHE_FILENAME
        self.enable_read = enable_read
        self.enable_write = enable_write
        self._min_interval_seconds = min_interval_seconds
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._flush_every = flush_every
        self._session = session
        self._owns_session = False
        self._sleep = sleep

        # Results usable in this run: url -> raw ordered card names
        self._results: dict[str, list[str]] = {}
        # Contents of the cache file, loaded lazily
        self._disk: dict[str, list[str]] | None = None
        self._unflushed: dict[str, list[str]] = {}
        self._failed_urls: set[str] = set()
        self._last_request_time: float | None = None

        self.network_requests = 0
        self.cache_hits = 0

    @property
    def failed_url_count(self) -> int:
        """Number of distinct URLs that could not be fetched in this run."""
        return len(self._failed_urls)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Write pending cache entries and close the session if this fetcher created it."""
        self.flush()
        if self._session is not None and self._owns_session:
            await self._session.close()
            self._session = None
            self._owns_session = False

    async def fetch_card_names(self, url: str) -> list[str] | None:
        """
        Get the raw, ordered card names matching a prepared Scryfall search URL.

        Returns:
            The card names (empty when no card matches), or None when the fetch failed.
        """
        if (names := self._results.get(url)) is not None:
            return names

        if self.enable_read and (names := self._load_disk().get(url)) is not None:
            self.cache_hits += 1
            self._results[url] = names
            return names

        if url in self._failed_urls:
            return None

        names = await self._fetch_with_retries(url)
        if names is None:
            self._failed_urls.add(url)
            return None

        self._results[url] = names
        if self.enable_write:
            self._unflushed[url] = names
            if len(self._unflushed) >= self._flush_every:
                self.flush()
        return names

    def flush(self) -> None:
        """Write pending results to the cache file, keeping entries already in the file."""
        if not self.enable_write or not self._unflushed:
            return

        templates = self._load_disk()
        templates.update(self._unflushed)
        temp_path = self.cache_path.with_name(f"{self.cache_path.name}.tmp")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump({"version": self.CACHE_VERSION, "templates": templates}, f)
            os.replace(temp_path, self.cache_path)
            logger.debug(f"Scryfall cache written: {len(templates)} templates")
            self._unflushed = {}
        except OSError as e:
            logger.warning(f"Cache write error for {self.cache_path.name}: {e}")

    def _load_disk(self) -> dict[str, list[str]]:
        """Load the cache file once. A missing or unreadable file counts as empty."""
        if self._disk is not None:
            return self._disk

        self._disk = {}
        if self.cache_path.exists():
            try:
                with open(self.cache_path, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("version") != self.CACHE_VERSION:
                    raise ValueError(f"unsupported cache version {data.get('version')}")
                self._disk = {url: list(names) for url, names in data["templates"].items()}
                logger.info(
                    f"Loaded {len(self._disk)} Scryfall templates from cache: "
                    f"{self.cache_path.name}"
                )
            except Exception as e:
                logger.warning(f"Cache read error for {self.cache_path.name}: {e}")
        return self._disk

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": self.USER_AGENT, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT_SECONDS),
            )
            self._owns_session = True
        return self._session

    async def _throttle(self) -> None:
        """Wait until the minimum interval since the previous request has passed."""
        if self._last_request_time is not None:
            remaining = self._min_interval_seconds - (time.monotonic() - self._last_request_time)
            if remaining > 0:
                await self._sleep(remaining)
        self._last_request_time = time.monotonic()

    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        delay = self._backoff_seconds * 2 ** (attempt - 1)
        if retry_after is not None:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass  # HTTP-date form; fall back to exponential backoff
        return min(delay, self._max_backoff_seconds)

    async def _fetch_with_retries(self, url: str) -> list[str] | None:
        """Fetch one URL. Returns None after a non-retryable error or the last failed attempt."""
        data = await self.request_json(url)
        if data is None:
            return None
        return [card["name"] for card in data.get("data", [])]

    async def request_json(self, url: str, json_body: dict | None = None) -> dict | None:
        """
        Request one URL with throttling and retries: a GET, or a POST when json_body is given.

        Returns:
            The response JSON (an empty dict for 404), or None after a non-retryable error
            or the last failed attempt.
        """
        session = self._get_session()
        problem = ""
        for attempt in range(1, self._max_attempts + 1):
            retry_after: str | None = None
            await self._throttle()
            self.network_requests += 1
            logger.debug(f"Scryfall request: {url}")
            try:
                request = (
                    session.get(url) if json_body is None else session.post(url, json=json_body)
                )
                async with request as response:
                    status = response.status
                    if status == 200:
                        return await response.json()
                    if status == 404:
                        # Scryfall answers a search without matches with 404
                        return {}
                    if status != 429 and status < 500:
                        logger.warning(f"Scryfall API error {status} (not retried): {url}")
                        return None
                    problem = f"HTTP {status}"
                    retry_after = response.headers.get("Retry-After")
            except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                problem = f"{type(e).__name__}: {e}"

            if attempt < self._max_attempts:
                delay = self._retry_delay(attempt, retry_after)
                logger.debug(
                    f"Scryfall {problem}; retry {attempt}/{self._max_attempts - 1} in {delay:.1f}s"
                )
                await self._sleep(delay)

        logger.warning(
            f"Scryfall fetch failed after {self._max_attempts} attempts ({problem}): {url}"
        )
        return None
