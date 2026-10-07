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
        cached: dict[str, CardAttributes] = {}
        if self.enable_read:
            try:
                cached = self._load_cache()
            except OSError as e:
                logger.warning(f"Cache read error for {self.cache_path.name}: {e}")
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
            card_name = card.get("name")
            if not isinstance(card_name, str):
                logger.warning("Skipping a Scryfall record without a name")
                continue
            attributes = self._parse_record(card)
            for face_name in card_name.split(" // "):
                if (name := requested.get(face_name.lower())) is not None:
                    result[name] = attributes
        return result

    @classmethod
    def _parse_record(cls, card: dict) -> CardAttributes:
        """
        The attributes in a Scryfall card record. Null or missing fields degrade to the
        unknown-card values; a layout that keeps the type line on its faces only
        (`reversible_card`) joins the faces' type lines.
        """
        type_line = card.get("type_line") or " // ".join(
            face.get("type_line") or "" for face in card.get("card_faces") or []
        )
        return CardAttributes(
            color_identity="".join(
                c for c in cls.COLOR_ORDER if c in (card.get("color_identity") or [])
            ),
            type_line=type_line,
            mana_value=float(card.get("cmc") or 0.0),
        )

    def _load_cache(self) -> dict[str, CardAttributes]:
        """
        Load the cache file. A missing, outdated or malformed file counts as empty (it will
        be rewritten). A file that cannot be read at all raises OSError, so the caller can
        decide: a transient lock must not look like an empty cache.
        """
        if not self.cache_path.exists():
            return {}
        with open(self.cache_path, encoding="utf-8") as f:
            text = f.read()
        try:
            data = json.loads(text)
            if data.get("version") != self.CACHE_VERSION:
                raise ValueError(f"unsupported cache version {data.get('version')}")
            return {name: self._parse_entry(entry) for name, entry in data["cards"].items()}
        except (ValueError, KeyError, TypeError, AttributeError) as e:
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
        """
        Add fetched attributes to the cache file, keeping entries already in the file. The
        file is left alone when it cannot be read, so a transient error does not erase it.
        """
        try:
            cards = self._load_cache()
        except OSError as e:
            logger.warning(f"Cache not updated: {self.cache_path.name} could not be read ({e})")
            return
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
