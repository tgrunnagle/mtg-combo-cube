"""Fake HTTP session for testing Scryfall access without the network."""

import json
from pathlib import Path
from typing import cast

import aiohttp

from mtg_combo_cube.scryfall.scryfall_fetcher import ScryfallFetcher


class FakeResponse:
    """Stands in for an aiohttp response used as an async context manager."""

    def __init__(
        self,
        status: int = 200,
        card_names: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.status = status
        self.headers = headers or {}
        self._card_names = card_names or []

    async def json(self) -> dict:
        if self.status != 200:
            return {"object": "error", "status": self.status}
        return {"object": "list", "data": [{"name": name} for name in self._card_names]}

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakeSession:
    """
    Serves scripted responses per URL and records every request.

    Each URL maps to a list of responses (or exceptions to raise) used in order;
    the last one repeats. An unscripted URL fails the test.
    """

    def __init__(self, responses: dict[str, list[FakeResponse | Exception]] | None = None):
        self._responses = {url: list(items) for url, items in (responses or {}).items()}
        self.requests: list[str] = []

    def get(self, url: str) -> FakeResponse:
        self.requests.append(url)
        if url not in self._responses:
            raise AssertionError(f"Unexpected request: {url}")
        items = self._responses[url]
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, Exception):
            raise item
        return item


class SleepRecorder:
    """Replaces asyncio.sleep so tests do not wait; records the requested delays."""

    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def make_fetcher(
    session: FakeSession,
    cache_dir: Path,
    enable_read: bool = True,
    enable_write: bool = True,
    sleep: SleepRecorder | None = None,
    **kwargs,
) -> ScryfallFetcher:
    """Create a ScryfallFetcher on a fake session with no real delays."""
    kwargs.setdefault("min_interval_seconds", 0)
    return ScryfallFetcher(
        cache_dir=cache_dir,
        enable_read=enable_read,
        enable_write=enable_write,
        session=cast(aiohttp.ClientSession, session),
        sleep=sleep if sleep is not None else SleepRecorder(),
        **kwargs,
    )


def write_cache_file(cache_dir: Path, templates: dict[str, list[str]]) -> Path:
    """Write a Scryfall cache file in the format ScryfallFetcher reads."""
    cache_path = cache_dir / ScryfallFetcher.CACHE_FILENAME
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"version": ScryfallFetcher.CACHE_VERSION, "templates": templates}, f)
    return cache_path


def read_cache_file(cache_dir: Path) -> dict[str, list[str]]:
    """Read the templates stored in a Scryfall cache file."""
    with open(cache_dir / ScryfallFetcher.CACHE_FILENAME, encoding="utf-8") as f:
        return json.load(f)["templates"]
