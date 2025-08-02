from commander_spellbook import Variant
import aiohttp
import json
import math

class VariantTracker:
    def __init__(self):
        self._card_counts = {}
        self._requirement_api_counts = {}
        self._requirement_cards_cache = {}

    REQUIRED_CARD_RANK_LIMIT = 3

    def _scale_popularity(self, popularity: int) -> float:
        return math.log(popularity)

    def process_variant(self, variant: Variant):
        scaled_popularity = self._scale_popularity(variant.popularity)
        for use in variant.uses:
            if (count := self._card_counts.get(use['card']['name'])) is None:
                self._card_counts[use['card']['name']] = scaled_popularity
            else:
                self._card_counts[use['card']['name']] = count + scaled_popularity

            for requirement in variant.requires:
                # key is the modified scryfall api url
                # order=edhrec ranks the cards by edhrec rank
                # limit=3 limits the number of cards returned to 3
                # remove the 'require legal:commander' from the url
                key = requirement['template']['scryfallApi'].replace('+legal%3Acommander', '') + f'&order=edhrec&limit={self.REQUIRED_CARD_RANK_LIMIT}'
                if (count := self._requirement_api_counts.get(key)) is None:
                    self._requirement_api_counts[key] = scaled_popularity
                else:
                    self._requirement_api_counts[key] = count + scaled_popularity

    def get_top_cards(self, n: int) -> list[tuple[str, int]]:
        return sorted(self._card_counts.items(), key=lambda item: item[1], reverse=True)[:n]

    async def get_top_required_cards(self, n: int) -> list[tuple[str, int]]:
        top = sorted(self._requirement_api_counts.items(), key=lambda item: item[1], reverse=True)[:n]
        card_counts = {}
        for id, req_count in top:
            card_names = await self._get_requirement_card_names(id)
            for card_name in card_names:
                if (count := card_counts.get(card_name)) is None:
                    card_counts[card_name] = req_count
                else:
                    card_counts[card_name] = count + req_count
        return sorted(card_counts.items(), key=lambda item: item[1], reverse=True)[:n]

    async def _get_requirement_card_names(self, scryfall_api: str) -> list[str]:
        if (card_names := self._requirement_cards_cache.get(scryfall_api)) is None:
            async with aiohttp.ClientSession() as session:
                print("Fetching requirement card names for", scryfall_api)
                async with session.get(scryfall_api) as response:
                    text = await response.text()
                    data = json.loads(text)

                    card_names = [card['name'] for card in data['data']]
                    card_names = card_names[:min(self.REQUIRED_CARD_RANK_LIMIT, len(card_names))]
            self._requirement_cards_cache[scryfall_api] = card_names
        return card_names

        