from commander_spellbook import CommanderSpellbook
from variant_tracker import VariantTracker
import math

class CubeBuilder:
    def __init__(self):
        pass

    POOL_MULTIPLIER = 2.0

    async def _select_top_cards(self, variant_tracker: VariantTracker, count: int) -> list[tuple[str, int]]:
        # TODO selection algorithm to pick the best cards from the pool
        top_cards = variant_tracker.get_top_cards(count)
        return top_cards

    async def _select_required_cards(self, variant_tracker: VariantTracker, count: int, top_cards: list[tuple[str, int]]) -> list[tuple[str, int]]:
        # TODO selection algorithm to pick the best cards from the pool
        req_cards = await variant_tracker.get_top_required_cards(count, exclude=[card[0] for card in top_cards])
        return req_cards
        

    async def build_cube(self, spellbook: CommanderSpellbook, cube_size: int, ratio_top_cards: float) -> list[str]:
        count_top_cards = math.ceil(cube_size * ratio_top_cards)
        variant_tracker = VariantTracker()
        async for variant in spellbook.get_variants(max_cards_in_combo=3, max_pages=100):
            variant_tracker.process_variant(variant)
            count_cards = variant_tracker.count_cards()
            if count_cards >= count_top_cards * self.POOL_MULTIPLIER:
                break

        top_cards = await self._select_top_cards(variant_tracker, count_top_cards)
        required_cards = await self._select_required_cards(variant_tracker, cube_size - count_top_cards, top_cards)
        return [
            card[0] for card in 
            # sort by popularity
            sorted([card for card in top_cards + required_cards], key=lambda card: card[1], reverse=True)
        ]