"""Converts Variant objects to ILP-ready ComboData."""

import logging

import aiohttp

from mtg_combo_cube.ilp_models import ComboData
from mtg_combo_cube.models import Variant

logger = logging.getLogger(__name__)


class ComboPreprocessor:
    """Converts Variant objects to ILP-ready ComboData."""

    REQUIREMENT_CARD_LIMIT = 10  # Max cards per template requirement

    def __init__(self):
        self._scryfall_cache: dict[str, list[str]] = {}

    async def preprocess_variants(
        self,
        variants: list[Variant],
    ) -> tuple[list[ComboData], set[str]]:
        """
        Convert variants to ComboData and collect all card names.

        Returns:
            - List of ComboData for ILP
            - Set of all unique card names in the universe
        """
        combo_data_list: list[ComboData] = []
        all_cards: set[str] = set()

        for variant in variants:
            combo_data = await self._process_single_variant(variant)
            if combo_data is None:
                continue  # Skip unresolvable combos

            combo_data_list.append(combo_data)
            all_cards.update(combo_data.required_cards)
            for opts in combo_data.requirement_options:
                all_cards.update(opts)

        logger.info(
            f"Preprocessed {len(combo_data_list)} combos with {len(all_cards)} unique cards"
        )
        return combo_data_list, all_cards

    async def _process_single_variant(self, variant: Variant) -> ComboData | None:
        """Convert a single Variant to ComboData."""
        # Extract required cards from 'uses'
        required_cards = frozenset(use.card.name for use in variant.uses)

        # Resolve template requirements
        requirement_options: list[frozenset[str]] = []
        for req in variant.requires:
            if req.template.scryfall_api is None:
                # No API means unresolvable - skip this combo
                logger.debug(f"Skipping combo {variant.id}: unresolvable requirement")
                return None

            cards = await self._resolve_template(req.template.scryfall_api)
            if not cards:
                logger.debug(f"Skipping combo {variant.id}: empty requirement options")
                return None
            requirement_options.append(frozenset(cards))

        return ComboData(
            id=variant.id,
            required_cards=required_cards,
            requirement_options=requirement_options,
            popularity=variant.popularity or 0,
        )

    async def _resolve_template(self, scryfall_api: str) -> list[str]:
        """Fetch cards matching a Scryfall template query."""
        # Normalize URL for caching (same pattern as variant_tracker.py)
        url = scryfall_api.replace("+legal%3Acommander", "") + "&order=edhrec"

        if url in self._scryfall_cache:
            return self._scryfall_cache[url]

        try:
            async with aiohttp.ClientSession() as session:
                logger.debug(f"Fetching template cards: {url}")
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.warning(f"Scryfall API error: {response.status}")
                        return []
                    data = await response.json()
                    cards = [card["name"] for card in data.get("data", [])]
                    cards = cards[: self.REQUIREMENT_CARD_LIMIT]

            self._scryfall_cache[url] = cards
            return cards
        except Exception as e:
            logger.warning(f"Error fetching template: {e}")
            return []
