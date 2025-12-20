import aiohttp
import json
from typing import AsyncIterator, Dict, Any
import logging

from urllib.parse import urlencode

logger = logging.getLogger(__name__)


class Variant:
    def __init__(self, server_data: Dict[str, Any]):
        self.id = server_data["id"]
        self.of = server_data["of"]
        self.uses = server_data["uses"]
        self.produces = server_data["produces"]
        self.popularity = server_data["popularity"]
        self.requires = server_data["requires"]
        self._server_data = server_data

    def __dict__(self):
        return self._server_data


class CommanderSpellbook:
    def __init__(self):
        pass

    PAGE_SIZE = 1000

    def _get_variants_url(self, max_cards_in_combo: int, offset: int) -> str:
        params = {
            "limit": self.PAGE_SIZE,
            "offset": offset,
            "q": f"cards<{max_cards_in_combo + 1}+-is:commander",
            "orderings": "-popularity,card_count",
        }
        return "https://backend.commanderspellbook.com/variants?" + urlencode(params)

    async def get_variants(
        self, max_cards_in_combo=3, max_variants=5000
    ) -> AsyncIterator[Variant]:
        async with aiohttp.ClientSession() as session:
            url = self._get_variants_url(max_cards_in_combo, 0)
            count = 0
            while count < max_variants:
                logger.debug(f"Fetching variants from {url}")
                async with session.get(url) as response:
                    text = await response.text()
                    data = json.loads(text)
                    next = data.get("next")
                    for variant in data["results"]:
                        yield Variant(variant)
                        count += 1
                        if count >= max_variants:
                            break

                if next is None:
                    break
                url = next

    def _get_find_my_combos_url(self, offset: int) -> str:
        params = {
            "limit": self.PAGE_SIZE,
            "offset": offset,
            "ordering": "-popularity,card_count"
        }
        return "https://backend.commanderspellbook.com/find-my-combos?" + urlencode(params)

    async def get_almost_included(
        self, existing_cards: list[str], max_variants: int = 5000
    ) -> AsyncIterator[str]:
        async with aiohttp.ClientSession() as session:
            url = self._get_find_my_combos_url(0)
            body = {
                "commanders": [],
                "main": [{"card": card, "quantity": 1} for card in existing_cards],
            }
            count = 0
            while count < max_variants:
                logger.debug(f"Fetching almost included variants from {url}")
                async with session.post(url, json=body) as response:
                    text = await response.text()
                    data = json.loads(text)
                    next = data.get("next")
                    almost_included = data.get("results", {}).get("almostIncluded", [])
                    if not almost_included:
                        break
                    for variant in almost_included:
                        yield Variant(variant)
                        count += 1
                        if count >= max_variants:
                            break

                if next is None:
                    break
                url = next

    async def get_included(
        self, existing_cards: list[str], max_variants: int = 5000
    ) -> AsyncIterator[Variant]:
        async with aiohttp.ClientSession() as session:
            url = self._get_find_my_combos_url(0)
            body = {
                "commanders": [],
                "main": [{"card": card, "quantity": 1} for card in existing_cards],
            }
            count = 0
            next = None
            async with session.post(url, json=body) as response:
                logger.debug(f"Fetching included variants from {url}")
                text = await response.text()
                data = json.loads(text)
                next = data.get("next")
                included = data.get("results", {}).get("included", [])
                for variant in included:
                    yield Variant(variant)
                    count += 1
                    if count >= max_variants:
                        break

            while next is not None:
                async with session.post(next, json=body) as response:
                    logger.debug(f"Fetching included variants from {next}")
                    text = await response.text()
                    data = json.loads(text)
                    next = data.get("next")
                    included = data.get("results", {}).get("included", [])
                    if not included:
                        break
                    for variant in included:
                        yield Variant(variant)
                        count += 1
                        if count >= max_variants:
                            break
