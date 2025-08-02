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
        self._card_popularity = {}
        self._requirement_api_popularity = {}
        self._requirement_cards_cache = {}

    REQUIRED_CARD_RANK_LIMIT = 10

    def _scale_popularity(self, popularity: int) -> float:
        return math.log(popularity)

    def count_cards(self) -> int:
        return len(self._card_popularity)

    def count_variants(self) -> int:
        return len(self._variants)

    def count_required_cards_from_cache(self) -> int:
        cards = set()
        logger.info(f"Found {len(self._requirement_cards_cache)} requirement urls")
        for _, card_names in self._requirement_cards_cache.items():
            cards.update(card_names)
        return len(cards)

    def process_variant(self, variant: Variant):
        self._variants[variant.id] = variant

        scaled_popularity = self._scale_popularity(variant.popularity)
        for use in variant.uses:
            if (sum_pop := self._card_popularity.get(use['card']['name'])) is None:
                self._card_popularity[use['card']['name']] = scaled_popularity
            else:
                self._card_popularity[use['card']['name']] = sum_pop + scaled_popularity

            for requirement in variant.requires:
                # key is the modified scryfall api url
                # order=edhrec ranks the cards by edhrec rank
                # limit=3 limits the number of cards returned to 3
                # remove the 'require legal:commander' from the url
                if requirement['template']['scryfallApi'] is None:
                    #logger.warning(f"No scryfall api for requirement\n{json.dumps(requirement, indent=2)}")
                    continue
                key = requirement['template']['scryfallApi'].replace('+legal%3Acommander', '') + f'&order=edhrec&limit={self.REQUIRED_CARD_RANK_LIMIT}'
                if (sum_pop := self._requirement_api_popularity.get(key)) is None:
                    self._requirement_api_popularity[key] = scaled_popularity
                else:
                    self._requirement_api_popularity[key] = sum_pop + scaled_popularity

    def get_top_cards(self, n: int) -> list[tuple[str, int]]:
        return sorted(self._card_popularity.items(), key=lambda item: item[1], reverse=True)[:n]

    async def get_top_required_cards(self, n: int, exclude: list[str] = []) -> list[tuple[str, int]]:
        top = sorted(self._requirement_api_popularity.items(), key=lambda item: item[1], reverse=True)[:n]
        card_counts = {}
        for api_url, req_count in top:
            card_names = await self._get_requirement_card_names(api_url)
            for card_name in card_names:
                if (count := card_counts.get(card_name)) is None:
                    card_counts[card_name] = req_count
                else:
                    card_counts[card_name] = count + req_count
        return sorted([(k, v) for k, v in card_counts.items() if k not in exclude], key=lambda item: item[1], reverse=True)[:n]

    async def _get_requirement_card_names(self, scryfall_api: str) -> list[str]:
        if (card_names := self._requirement_cards_cache.get(scryfall_api)) is None:
            async with aiohttp.ClientSession() as session:
                logger.debug(f"Fetching requirement card names for {scryfall_api}")
                async with session.get(scryfall_api) as response:
                    text = await response.text()
                    data = json.loads(text)

                    card_names = [card['name'] for card in data['data']]
                    card_names = card_names[:min(self.REQUIRED_CARD_RANK_LIMIT, len(card_names))]
            self._requirement_cards_cache[scryfall_api] = card_names
        return card_names

        