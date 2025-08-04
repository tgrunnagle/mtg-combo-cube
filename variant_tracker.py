from commander_spellbook import Variant
import aiohttp
import json
import math
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VariantTracker:
    def __init__(self):
        self._variants = {}
        self._card_counts = {}
        self._requirement_cards_cache = {}

    REQUIRED_CARD_RANK_LIMIT = 5

    def count_cards(self) -> int:
        return len(self._card_counts)

    def count_variants(self) -> int:
        return len(self._variants)

    async def process_variant(self, variant: Variant):
        self._variants[variant.id] = variant

        for use in variant.uses:
            if (count := self._card_counts.get(use['card']['name'])) is None:
                self._card_counts[use['card']['name']] = 1
            else:
                self._card_counts[use['card']['name']] = count + 1

            for requirement in variant.requires:
                if requirement['template']['scryfallApi'] is None:
                    #logger.warning(f"No scryfall api for requirement\n{json.dumps(requirement, indent=2)}")
                    continue
                url = requirement['template']['scryfallApi'].replace('+legal%3Acommander', '') + f'&order=edhrec'
                cards = await self._get_requirement_card_names(url)
                for card in cards:
                    if (count := self._card_counts.get(card)) is None:
                        self._card_counts[card] = 1
                    else:
                        self._card_counts[card] = count + 1

    def get_top_cards(self, n: int) -> list[tuple[str, int]]:
        return sorted(self._card_counts.items(), key=lambda item: item[1], reverse=True)[:n]

    async def _get_requirement_card_names(self, scryfall_api: str) -> list[str]:
        if (card_names := self._requirement_cards_cache.get(scryfall_api)) is None:
            async with aiohttp.ClientSession() as session:
                logger.info(f"Fetching requirement card names for {scryfall_api}")
                async with session.get(scryfall_api) as response:
                    text = await response.text()
                    data = json.loads(text)

                    card_names = [card['name'] for card in data['data']]
                    card_names = card_names[:min(self.REQUIRED_CARD_RANK_LIMIT, len(card_names))]
            self._requirement_cards_cache[scryfall_api] = card_names
        return card_names

        