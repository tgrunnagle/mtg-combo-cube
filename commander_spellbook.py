import aiohttp
import json
from typing import AsyncIterator, Dict, Any

class Variant:
    def __init__(self, server_data: Dict[str, Any]):
        self.id = server_data['id']
        self.of = server_data['of']
        self.uses = server_data['uses']
        self.produces = server_data['produces']
        self.popularity = server_data['popularity']
        self.requires = server_data['requires']
        self._server_data = server_data

    def __dict__(self):
        return self._server_data

class CommanderSpellbook:
    def __init__(self):
        pass

    PAGE_SIZE = 100

    def _get_variants_url(self, offset: int) -> str:
        return f"https://backend.commanderspellbook.com/variants?ordering=-popularity%2Cidentity_count%2Ccard_count%2C-created&limit={self.PAGE_SIZE}&offset={offset}"

    async def get_variants(self, max_cards=3, max_pages=1) -> AsyncIterator[Variant]:
        async with aiohttp.ClientSession() as session:
            offset = 0
            url = self._get_variants_url(offset)
            while offset < max_pages * self.PAGE_SIZE:
                async with session.get(url) as response:
                    text = await response.text()
                    data = json.loads(text)
                    next = data['next']
                    for variant in data['results']:
                        yield Variant(variant)

                if next is None:
                    break
                offset += self.PAGE_SIZE
                url = self._get_variants_url(offset)

