"""Looks up card attributes (color identity, type line, mana value) on Scryfall, with a cache."""

import json
import logging
import os
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

from mtg_combo_cube.models import CardAttributes
from mtg_combo_cube.scryfall.scryfall_fetcher import ScryfallFetcher

logger = logging.getLogger(__name__)


class CardAttributeFetcher:
    """
    Fetches the attributes of cards by name.

    Requests go through a ScryfallFetcher, which provides rate limiting and retries.
    """

    CACHE_FILENAME = "scryfall_card_attributes.json"
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
        Initialize the attribute fetcher.

        Args:
            fetcher: Fetcher used for the HTTP requests
            cache_dir: Directory for the cache file (default: data/cache)
            enable_read: Serve attributes from the cache file when present
            enable_write: Write fetched attributes to the cache file
        """
        self._fetcher = fetcher
        self.cache_path = cache_dir / self.CACHE_FILENAME
        self.enable_read = enable_read
        self.enable_write = enable_write

    async def fetch_attributes(self, names: Iterable[str]) -> dict[str, CardAttributes]:
        """
        Get the attributes of each named card.

        Returns:
            Card name -> attributes. Cards Scryfall does not know, and cards in a batch
            whose request failed, are left out.
        """
        wanted = list(dict.fromkeys(names))
        cached = self._load_cache() if self.enable_read else {}
        attributes = {name: cached[name] for name in wanted if name in cached}
        missing = [name for name in wanted if name not in attributes]

        fetched: dict[str, CardAttributes] = {}
        for start in range(0, len(missing), self.BATCH_SIZE):
            fetched.update(await self._fetch_batch(missing[start : start + self.BATCH_SIZE]))

        if fetched and self.enable_write:
            self._write_cache(fetched)

        attributes.update(fetched)
        not_found = len(wanted) - len(attributes)
        if not_found:
            logger.warning(f"No Scryfall data for {not_found} of {len(wanted)} cards")
        return attributes

    async def _fetch_batch(self, names: list[str]) -> dict[str, CardAttributes]:
        """Fetch one batch of at most BATCH_SIZE cards."""
        # Scryfall names a multi-faced card "Front // Back" but finds it by a face name only
        requested = {name.split(" // ")[0].lower(): name for name in names}
        body = {"identifiers": [{"name": name.split(" // ")[0]} for name in names]}
        data = await self._fetcher.request_json(self.COLLECTION_URL, json_body=body)
        if data is None:
            return {}

        result: dict[str, CardAttributes] = {}
        for card in data.get("data", []):
            attributes = CardAttributes(
                color_identity="".join(
                    c for c in self.COLOR_ORDER if c in card.get("color_identity", [])
                ),
                type_line=card.get("type_line", ""),
                mana_value=float(card.get("cmc", 0.0)),
            )
            for face_name in card["name"].split(" // "):
                if (name := requested.get(face_name.lower())) is not None:
                    result[name] = attributes
        return result

    def _load_cache(self) -> dict[str, CardAttributes]:
        """Load the cache file. A missing, unreadable or outdated file counts as empty."""
        if not self.cache_path.exists():
            return {}
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != self.CACHE_VERSION:
                raise ValueError(f"unsupported cache version {data.get('version')}")
            return {name: self._parse_entry(entry) for name, entry in data["cards"].items()}
        except Exception as e:
            logger.warning(f"Cache read error for {self.cache_path.name}: {e}")
            return {}

    @staticmethod
    def _parse_entry(entry: dict) -> CardAttributes:
        """A cache entry as attributes; a wrong type is a ValueError (the cache is unusable)."""
        identity, type_line = entry["color_identity"], entry["type_line"]
        if not isinstance(identity, str) or not isinstance(type_line, str):
            raise ValueError(f"malformed cache entry {entry!r}")
        return CardAttributes(
            color_identity=identity, type_line=type_line, mana_value=float(entry["mana_value"])
        )

    def _write_cache(self, fetched: dict[str, CardAttributes]) -> None:
        """Add fetched attributes to the cache file, keeping entries already in the file."""
        cards = self._load_cache()
        cards.update(fetched)
        temp_path = self.cache_path.with_name(f"{self.cache_path.name}.tmp")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as f:
                entries = {name: asdict(attributes) for name, attributes in cards.items()}
                json.dump({"version": self.CACHE_VERSION, "cards": entries}, f)
            os.replace(temp_path, self.cache_path)
        except OSError as e:
            logger.warning(f"Cache write error for {self.cache_path.name}: {e}")
