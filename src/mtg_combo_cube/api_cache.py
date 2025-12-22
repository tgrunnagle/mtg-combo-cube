"""Cache utilities for Commander Spellbook API responses."""

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from mtg_combo_cube.commander_spellbook import CommanderSpellbook
from mtg_combo_cube.models import Variant

logger = logging.getLogger(__name__)


class SpellbookCache:
    """Manages file-based caching for Commander Spellbook get_variants API responses."""

    def __init__(
        self,
        cache_dir: Path = Path("data/cache"),
        enable_write: bool = True,
        enable_read: bool = True,
    ):
        """
        Initialize cache manager.

        Args:
            cache_dir: Directory for cache files (default: data/cache)
            enable_write: Whether to write to cache (default: True)
            enable_read: Whether to read from cache (default: True)
        """
        self.cache_dir = cache_dir
        self.enable_write = enable_write
        self.enable_read = enable_read

        if enable_write:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_variants_cache_path(self, max_cards_in_combo: int, max_variants: int) -> Path:
        """Get cache file path for get_variants() results."""
        filename = f"variants_cards{max_cards_in_combo}_max{max_variants}.json"
        return self.cache_dir / filename

    def read_variants_cache(self, cache_path: Path) -> list[Variant] | None:
        """
        Read variants from cache file.

        Returns:
            List of Variant objects if cache exists and is valid, None otherwise.
        """
        if not cache_path.exists():
            logger.debug(f"Cache miss: {cache_path.name}")
            return None

        try:
            logger.info(f"Reading from cache: {cache_path.name}")
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)

            variants = [Variant.model_validate(v) for v in data]
            logger.info(f"Loaded {len(variants)} variants from cache")
            return variants
        except Exception as e:
            logger.warning(f"Cache read error for {cache_path.name}: {e}")
            return None

    def write_variants_cache(self, cache_path: Path, variants: list[Variant]) -> None:
        """Write variants to cache file."""
        try:
            logger.info(f"Writing {len(variants)} variants to cache: {cache_path.name}")
            data = [v.model_dump(by_alias=True) for v in variants]
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Cache written: {cache_path.name}")
        except Exception as e:
            logger.warning(f"Cache write error for {cache_path.name}: {e}")

    async def get_variants_cached(
        self,
        spellbook: CommanderSpellbook,
        max_cards_in_combo: int,
        max_variants: int,
    ) -> AsyncIterator[Variant]:
        """
        Get variants with caching support.

        If enable_read is True, tries to read from cache first.
        If cache miss or enable_read is False, fetches from API.
        If enable_write is True, writes API results to cache.
        """
        cache_path = self._get_variants_cache_path(max_cards_in_combo, max_variants)

        # Try reading from cache if enabled
        if self.enable_read:
            cached = self.read_variants_cache(cache_path)
            if cached is not None:
                for variant in cached:
                    yield variant
                return

        # Fetch from API and collect for caching
        variants = []
        async for variant in spellbook.get_variants(max_cards_in_combo, max_variants):
            variants.append(variant)
            yield variant

        if self.enable_write:
            # Write to cache if enabled
            self.write_variants_cache(cache_path, variants)
