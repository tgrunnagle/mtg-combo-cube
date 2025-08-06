from commander_spellbook import CommanderSpellbook, Variant
from variant_tracker import VariantTracker
import math
import logging
import asyncio
from collections import Counter

logger = logging.getLogger(__name__)

class CubeBuilder:
    def __init__(self, cube_size: int, spellbook: CommanderSpellbook = CommanderSpellbook()):
        self._cube_size = cube_size
        self._cube = []
        self._combos_cache = []
        self._combos_cache_lock = asyncio.Lock()
        self._variant_tracker = VariantTracker()
        self._spellbook = spellbook

    GOLDEN_RATIO = 1.1

    def _select_top_cards(self, count: int) -> list[tuple[str, int]]:
        # TODO selection algorithm to pick the best cards from the pool
        top_cards = self._variant_tracker.get_top_cards(count)
        return top_cards
        
    async def build_cube(self) -> list[str]:
        logger.info("Looking for top combos...")
        async for variant in self._spellbook.get_variants(max_cards_in_combo=3):
            await self._variant_tracker.process_variant(variant)
        logger.info(f"Found {self._variant_tracker.count_cards()} cards in {self._variant_tracker.count_variants()} combos")

        select_count = math.ceil(self._cube_size / self.GOLDEN_RATIO)
        top_cards = self._select_top_cards(select_count)
        logger.info(f"Selected {len(top_cards)} top cards")
        
        # Add cards that are almost included in the top cards
        added_cards = []
        existing_cards = [card[0] for card in top_cards]
        async for variant in self._spellbook.get_almost_included(existing_cards=existing_cards):
            for card in variant.uses:
                if card['card']['name'] not in existing_cards:
                    added_cards.append(card['card']['name'])
                if len(added_cards) + len(top_cards) == self._cube_size:
                    break
            if len(added_cards) + len(top_cards) == self._cube_size:
                break
        logger.info(f"Added {len(added_cards)} almost included cards")

        self._combos_cache = []
        self._cube = [card[0] for card in top_cards] + added_cards
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
            combos = []
            count = 0
            async for variant in self._spellbook.get_included(self._cube):
                combos.append(variant)
                count += 1
                if count % 100 == 0:
                    logger.info(f"Fetched {count} variants")
            self._combos_cache = combos
        return combos

    def get_cube(self) -> list[str]:
        return self._cube
    
    async def get_dead_cards(self) -> list[str]:
        combos = await self.get_combos()
        card_counts = Counter()
        for combo in combos:
            for card in combo.uses:
                card_counts[card['card']['name']] += 1
        return [card for card, count in card_counts.items() if count == 1]

    async def remove_dead_cards(self):
        dead_cards = await self.get_dead_cards()
        self.remove_cards(dead_cards)

    async def add_almost_included(self):
        self._combos_cache = []
        existing_cards = self._cube
        async for variant in self._spellbook.get_almost_included(existing_cards=existing_cards):
            for card in variant.uses:
                if card['card']['name'] not in existing_cards:
                    self._cube.append(card['card']['name'])
                if len(self._cube) == self._cube_size:
                    break
            if len(self._cube) == self._cube_size:
                break
        

    