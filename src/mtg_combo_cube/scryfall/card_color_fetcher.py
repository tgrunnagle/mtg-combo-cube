"""Looks up card color identities on Scryfall, with a persistent cache."""

import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path

from mtg_combo_cube.scryfall.scryfall_fetcher import ScryfallFetcher

logger = logging.getLogger(__name__)


class CardColorFetcher:
    """
    Fetches the color identity of cards by name.

    A color identity is a string of WUBRG letters in that order, empty for a colorless card.
    Requests go through a ScryfallFetcher, which provides rate limiting and retries.
    """

    CACHE_FILENAME = "scryfall_card_colors.json"
    CACHE_VERSION = 1
    COLLECTION_URL = "https://api.scryfall.com/cards/collection"
    BATCH_SIZE = 75  # Scryfall limit of identifiers per collection request
    COLOR_ORDER = "WUBRG"

    def __init__(
        self,
        fetcher: ScryfallFetcher,
        cache_dir: Path = Path("data/cache"),
        enable_read: bool = False,
        enable_write: bool = False,
    ):
        """
        Initialize the color fetcher.

        Args:
            fetcher: Fetcher used for the HTTP requests
            cache_dir: Directory for the cache file (default: data/cache)
            enable_read: Serve colors from the cache file when present
            enable_write: Write fetched colors to the cache file
        """
        self._fetcher = fetcher
        self.cache_path = cache_dir / self.CACHE_FILENAME
        self.enable_read = enable_read
        self.enable_write = enable_write

    async def fetch_color_identities(self, names: Iterable[str]) -> dict[str, str]:
        """
        Get the color identity of each named card.

        Returns:
            Card name -> color identity. Cards Scryfall does not know, and cards in a batch
            whose request failed, are left out.
        """
        wanted = list(dict.fromkeys(names))
        cached = self._load_cache() if self.enable_read else {}
        identities = {name: cached[name] for name in wanted if name in cached}
        missing = [name for name in wanted if name not in identities]

        fetched: dict[str, str] = {}
        for start in range(0, len(missing), self.BATCH_SIZE):
            fetched.update(await self._fetch_batch(missing[start : start + self.BATCH_SIZE]))

        if fetched and self.enable_write:
            self._write_cache(fetched)

        identities.update(fetched)
        not_found = len(wanted) - len(identities)
        if not_found:
            logger.warning(f"No color data for {not_found} of {len(wanted)} cards")
        return identities

    async def _fetch_batch(self, names: list[str]) -> dict[str, str]:
        """Fetch one batch of at most BATCH_SIZE cards."""
        # Scryfall names a multi-faced card "Front // Back" but finds it by a face name only
        requested = {name.split(" // ")[0].lower(): name for name in names}
        body = {"identifiers": [{"name": name.split(" // ")[0]} for name in names]}
        data = await self._fetcher.request_json(self.COLLECTION_URL, json_body=body)
        if data is None:
            return {}

        result: dict[str, str] = {}
        for card in data.get("data", []):
            identity = "".join(c for c in self.COLOR_ORDER if c in card.get("color_identity", []))
            for face_name in card["name"].split(" // "):
                if (name := requested.get(face_name.lower())) is not None:
                    result[name] = identity
        return result

    def _load_cache(self) -> dict[str, str]:
        """Load the cache file. A missing or unreadable file counts as empty."""
        if not self.cache_path.exists():
            return {}
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != self.CACHE_VERSION:
                raise ValueError(f"unsupported cache version {data.get('version')}")
            return dict(data["cards"])
        except Exception as e:
            logger.warning(f"Cache read error for {self.cache_path.name}: {e}")
            return {}

    def _write_cache(self, fetched: dict[str, str]) -> None:
        """Add fetched colors to the cache file, keeping entries already in the file."""
        cards = self._load_cache()
        cards.update(fetched)
        temp_path = self.cache_path.with_name(f"{self.cache_path.name}.tmp")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump({"version": self.CACHE_VERSION, "cards": cards}, f)
            os.replace(temp_path, self.cache_path)
        except OSError as e:
            logger.warning(f"Cache write error for {self.cache_path.name}: {e}")
