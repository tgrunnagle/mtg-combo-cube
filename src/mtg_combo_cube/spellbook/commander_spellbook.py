import asyncio
import logging
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import aiohttp

from mtg_combo_cube.models import Variant

logger = logging.getLogger(__name__)


class CommanderSpellbook:
    def __init__(self):
        self._logger = logging.getLogger()

    PAGE_SIZE = 1000
    UPDATE_LOG_INTERVAL = 1000
    MAX_ATTEMPTS = 12  # a rate-limit block can last several minutes
    BACKOFF_SECONDS = 2.0
    MAX_BACKOFF_SECONDS = 60.0
    PAGE_INTERVAL_SECONDS = 0.5  # pause between pages; the API rate-limits bursts

    async def _get_json(self, session: aiohttp.ClientSession, url: str) -> dict:
        """GET one page, retrying on HTTP 429 and 5xx with doubling backoff (or Retry-After)."""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            async with session.get(url) as response:
                retryable = response.status == 429 or response.status >= 500
                if not retryable or attempt == self.MAX_ATTEMPTS:
                    response.raise_for_status()
                    return await response.json()
                delay = self.BACKOFF_SECONDS * 2 ** (attempt - 1)
                try:
                    delay = max(delay, float(response.headers.get("Retry-After", 0)))
                except ValueError:
                    pass  # HTTP-date form; keep the exponential backoff
                delay = min(delay, self.MAX_BACKOFF_SECONDS)
                self._logger.warning(
                    f"Spellbook HTTP {response.status}; retry {attempt} in {delay:.0f}s"
                )
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    def _get_variants_url(self, max_cards_in_combo: int, offset: int) -> str:
        params = {
            "limit": self.PAGE_SIZE,
            "offset": offset,
            "q": f"cards<{max_cards_in_combo + 1}",  # "+-is:commander" not working,
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
                data = await self._get_json(session, next)
                next = data.get("next")
                if not data.get("results"):
                    break
                if next:
                    await asyncio.sleep(self.PAGE_INTERVAL_SECONDS)
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
