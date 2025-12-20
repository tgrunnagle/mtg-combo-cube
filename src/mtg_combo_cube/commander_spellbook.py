import json
import logging
from typing import AsyncIterator
from urllib.parse import urlencode
import aiohttp

from mtg_combo_cube.models import Variant

logger = logging.getLogger(__name__)


class CommanderSpellbook:
    def __init__(self):
        self._logger = logging.getLogger()

    PAGE_SIZE = 1000
    UPDATE_LOG_INTERVAL = 1000

    def _get_variants_url(self, max_cards_in_combo: int, offset: int) -> str:
        params = {
            "limit": self.PAGE_SIZE,
            "offset": offset,
            "q": f"cards<{max_cards_in_combo + 1}", # "+-is:commander" not working,
            "orderings": "-popularity,card_count",
        }
        return "https://backend.commanderspellbook.com/variants?" + urlencode(params)

    async def get_variants(
        self, max_cards_in_combo: int, max_variants: int
    ) -> AsyncIterator[Variant]:
        next = self._get_variants_url(max_cards_in_combo, 0)
        self._logger.debug(f"Fetching variants from {next}")
        count = 0
        async with aiohttp.ClientSession() as session:
            while next:
                async with session.get(next) as response:
                    response.raise_for_status()
                    data = await response.json()
                    next = data.get("next")
                    if not data.get("results"):
                        break
                    for result in data["results"]:
                        variant = Variant.model_validate(result)
                        if any(r.must_be_commander for r in variant.requires):
                            # skip combos requiring commander
                            continue
                        if count > 0 and count % CommanderSpellbook.UPDATE_LOG_INTERVAL == 0:
                            self._logger.info(f"Found {count} combo variants...")
                        yield variant
                        count += 1
                        if count >= max_variants:
                            return

    def _get_find_my_combos_url(self, offset: int) -> str:
        params = {
            "limit": self.PAGE_SIZE,
            "offset": offset,
            "ordering": "-popularity,card_count"
        }
        return "https://backend.commanderspellbook.com/find-my-combos?" + urlencode(params)

    async def get_almost_included(
        self, existing_cards: list[str], max_variants: int
    ) -> AsyncIterator[Variant]:
        body = {
            "commanders": [],
            "main": [{"card": card, "quantity": 1} for card in existing_cards],
        }
        count = 0
        next = self._get_find_my_combos_url(0)
        async with aiohttp.ClientSession() as session:
            while next:
                logger.debug(f"Fetching almost included variants from {next}")
                async with session.post(next, json=body) as response:
                    response.raise_for_status()
                    data = await response.json()
                    next = data.get("next")
                    results = data["results"]["almostIncluded"]
                    if not results:
                        break
                    for result in results:
                        variant = Variant.model_validate(result)
                        if any(r.must_be_commander for r in variant.requires):
                            # skip combos requiring commander
                            continue
                        if count > 0 and count % CommanderSpellbook.UPDATE_LOG_INTERVAL == 0:
                            self._logger.info(f"Found {count} almost included combo variants...")
                        yield variant
                        count += 1
                        if count >= max_variants:
                            return

    async def get_included(
        self, existing_cards: list[str], max_variants: int
    ) -> AsyncIterator[Variant]:
        next = self._get_find_my_combos_url(0)
        body = {
            "commanders": [],
            "main": [{"card": card, "quantity": 1} for card in existing_cards],
        }
        count = 0
        async with aiohttp.ClientSession() as session:
            while next:
                logger.debug(f"Fetching included variants from {next}")
                async with session.post(next, json=body) as response:
                    data = await response.json()
                    next = data.get("next")
                    results = data["results"]["included"]
                    if not results:
                        break
                    for result in results:
                        variant = Variant.model_validate(result)
                        if any(r.must_be_commander for r in variant.requires):
                            # skip combos requiring commander
                            continue
                        if count > 0 and count % CommanderSpellbook.UPDATE_LOG_INTERVAL == 0:
                            self._logger.info(f"Found {count} included combo variants...")
                        yield variant
                        count += 1
                        if count >= max_variants:
                            return
