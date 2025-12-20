import logging

import aiohttp

from mtg_combo_cube.models import Variant

logger = logging.getLogger(__name__)


class VariantTracker:
    def __init__(self):
        self._variants: dict[str, Variant] = {}
        self._card_counts: dict[str, int] = {}
        self._requirement_cards_cache: dict[str, list[str]] = {}

    REQUIRED_CARD_RANK_LIMIT = 5

    def count_cards(self) -> int:
        return len(self._card_counts)

    def count_variants(self) -> int:
        return len(self._variants)

    async def process_variant(self, variant: Variant):
        self._variants[variant.id] = variant

        for use in variant.uses:
            card_name = use.card.name
            if (count := self._card_counts.get(card_name)) is None:
                self._card_counts[card_name] = 1
            else:
                self._card_counts[card_name] = count + 1

        for requirement in variant.requires:
            if requirement.template.scryfall_api is None:
                continue
            url = (
                requirement.template.scryfall_api.replace("+legal%3Acommander", "")
                + "&order=edhrec"
            )
            cards = await self._get_requirement_card_names(url)
            for card in cards:
                if (count := self._card_counts.get(card)) is None:
                    self._card_counts[card] = 1
                else:
                    self._card_counts[card] = count + 1

    def get_top_cards_by_count(self, n: int) -> list[str]:
        return [
            count[0]
            for count in sorted(
                self._card_counts.items(), key=lambda item: item[1], reverse=True
            )[:n]
        ]

    def get_top_cards_by_popularity(self, n: int) -> list[str]:
        # TODO this does not handle required cards
        variants_by_popularity: list[Variant] = sorted(
            self._variants.values(), key=lambda v: v.popularity, reverse=True
        )
        cards: set[str] = set()
        for variant in variants_by_popularity:
            for card in variant.uses:
                cards.add(card.card.name)
                if len(cards) == n:
                    return list(cards)
        return list(cards)

    async def _get_requirement_card_names(self, scryfall_api: str) -> list[str]:
        if (card_names := self._requirement_cards_cache.get(scryfall_api)) is None:
            async with aiohttp.ClientSession() as session:
                logger.debug(f"Fetching requirement card names for {scryfall_api}")
                async with session.get(scryfall_api) as response:
                    data = await response.json()
                    card_names = [card["name"] for card in data["data"]]
                    card_names = card_names[: self.REQUIRED_CARD_RANK_LIMIT]
            self._requirement_cards_cache[scryfall_api] = card_names
        return card_names
