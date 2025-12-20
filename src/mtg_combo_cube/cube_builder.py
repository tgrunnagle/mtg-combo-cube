import asyncio
import logging
import math
from collections import Counter

from mtg_combo_cube.commander_spellbook import CommanderSpellbook
from mtg_combo_cube.models import Variant
from mtg_combo_cube.variant_tracker import VariantTracker

logger = logging.getLogger(__name__)


class CubeBuilder:
    def __init__(
        self, cube_size: int, spellbook: CommanderSpellbook = CommanderSpellbook()
    ):
        self._cube_size = cube_size
        self._cube = []
        self._combos_cache = []
        self._combos_cache_lock = asyncio.Lock()
        self._variant_tracker = VariantTracker()
        self._spellbook = spellbook

    GOLDEN_RATIO = 1.2

    def _select_top_cards(self, count: int) -> list[str]:
        # TODO selection algorithm to pick the best cards from the pool
        # return self._variant_tracker.get_top_cards_by_count(count)
        return self._variant_tracker.get_top_cards_by_popularity(count)

    async def build_cube(
        self, max_cards_in_combo: int = 4, golden_ratio: float = GOLDEN_RATIO
    ) -> list[str]:
        logger.info("Looking for top combos...")
        async for variant in self._spellbook.get_variants(
            max_cards_in_combo=max_cards_in_combo, max_variants=10000
        ):
            await self._variant_tracker.process_variant(variant)
        logger.info(
            f"Found {self._variant_tracker.count_cards()} cards in {self._variant_tracker.count_variants()} combos"
        )

        top_card_count = math.ceil(self._cube_size / golden_ratio)
        top_cards = self._select_top_cards(top_card_count)
        logger.info(f"Selected {len(top_cards)} top cards")

        # Add cards that are almost included in the top cards
        almost_included_cards = Counter()
        async for variant in self._spellbook.get_almost_included(
            existing_cards=top_cards, max_variants=2000
        ):
            for use in variant.uses:
                if use.card.name not in top_cards:
                    almost_included_cards[use.card.name] += 1
        added_cards = [
            card
            for card, _ in almost_included_cards.most_common(
                self._cube_size - len(top_cards)
            )
        ]

        logger.info(f"Added {len(added_cards)} almost included cards")

        self._combos_cache = []
        self._cube = top_cards + added_cards
        logger.info(f"Built cube of size {len(self._cube)}")
        return self._cube

    def remove_cards(self, cards: list[str]):
        self._combos_cache = []
        self._cube = [card for card in self._cube if card not in cards]

    async def get_combos(self) -> list[Variant]:
        if self._combos_cache:
            return self._combos_cache
        async with self._combos_cache_lock:
            if self._combos_cache:
                return self._combos_cache
            combos: list[Variant] = []
            async for variant in self._spellbook.get_included(self._cube, max_variants=10000):
                combos.append(variant)
            self._combos_cache = combos
        return combos

    def get_cube(self) -> list[str]:
        return self._cube

    async def get_dead_cards(self) -> list[str]:
        combos = await self.get_combos()
        card_counts = Counter()
        for combo in combos:
            for use in combo.uses:
                card_counts[use.card.name] += 1
        return [card for card, count in card_counts.items() if count == 1]

    async def remove_dead_cards(self):
        dead_cards = await self.get_dead_cards()
        self.remove_cards(dead_cards)

    async def add_almost_included(self):
        self._combos_cache = []
        existing_cards = self._cube
        async for variant in self._spellbook.get_almost_included(
            existing_cards=existing_cards, max_variants=1000
        ):
            for use in variant.uses:
                if use.card.name not in existing_cards:
                    self._cube.append(use.card.name)
                if len(self._cube) == self._cube_size:
                    break
            if len(self._cube) == self._cube_size:
                break
