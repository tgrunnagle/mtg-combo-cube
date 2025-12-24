"""Converts Variant objects to ILP-ready ComboData."""

import logging
from collections import defaultdict

import aiohttp

from mtg_combo_cube.ilp.ilp_models import CandidateCard, ComboData, RequirementOption
from mtg_combo_cube.ilp.requirement_normalizer import compute_requirement_group_key
from mtg_combo_cube.models import Variant

logger = logging.getLogger(__name__)


class ComboPreprocessor:
    """Converts Variant objects to ILP-ready ComboData."""

    REQUIREMENT_CARD_LIMIT = 10  # Max cards per template requirement

    def __init__(self, blocklist: frozenset[str] = frozenset()):
        self._scryfall_cache: dict[str, list[str]] = {}
        self._blocklist = blocklist

    async def preprocess_variants(
        self,
        variants: list[Variant],
    ) -> tuple[list[ComboData], dict[str, CandidateCard]]:
        """
        Convert variants to ComboData and build candidate cards.

        Returns:
            - List of ComboData for ILP
            - Dict of card_name -> CandidateCard with all relationships
        """
        combo_data_list: list[ComboData] = []

        # Track relationships per card
        card_combo_ids: dict[str, set[str]] = defaultdict(set)
        card_requirement_keys: dict[str, set[str]] = defaultdict(set)

        for variant in variants:
            combo_data = await self._process_single_variant(variant)
            if combo_data is None:
                continue  # Skip unresolvable combos

            combo_data_list.append(combo_data)

            # Track direct combo participation
            for card in combo_data.required_cards:
                card_combo_ids[card].add(combo_data.id)

            # Track requirement template satisfaction
            for opt in combo_data.requirement_options:
                for card in opt.cards:
                    card_requirement_keys[card].add(opt.group_key)

        # Build CandidateCard instances
        all_card_names = set(card_combo_ids.keys()) | set(card_requirement_keys.keys())
        candidate_cards = {
            name: CandidateCard(
                name=name,
                combo_ids=frozenset(card_combo_ids.get(name, set())),
                requirement_group_keys=frozenset(card_requirement_keys.get(name, set())),
            )
            for name in all_card_names
        }

        logger.info(
            f"Preprocessed {len(combo_data_list)} combos with {len(candidate_cards)} unique cards"
        )
        return combo_data_list, candidate_cards

    async def _process_single_variant(self, variant: Variant) -> ComboData | None:
        """Convert a single Variant to ComboData."""
        # Extract required cards from 'uses'
        required_cards = frozenset(use.card.name for use in variant.uses)

        # Skip combo if ANY required card is blocked
        if required_cards & self._blocklist:
            blocked = required_cards & self._blocklist
            logger.debug(f"Skipping combo {variant.id}: blocked cards {blocked}")
            return None

        # Resolve template requirements
        requirement_options: list[RequirementOption] = []
        for req in variant.requires:
            if req.template.scryfall_api is None:
                # No API means unresolvable - skip this combo
                logger.debug(f"Skipping combo {variant.id}: unresolvable requirement")
                return None

            cards = await self._resolve_template(req.template.scryfall_api)
            if not cards:
                logger.debug(f"Skipping combo {variant.id}: empty requirement options")
                return None

            group_key = compute_requirement_group_key(
                req.template.scryfall_api,
                req.template.name,
            )
            requirement_options.append(
                RequirementOption(
                    template_name=req.template.name,
                    group_key=group_key,
                    cards=frozenset(cards),
                )
            )

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
                    # Filter out blocked cards before applying limit
                    cards = [card for card in cards if card not in self._blocklist]
                    cards = cards[: self.REQUIREMENT_CARD_LIMIT]

            self._scryfall_cache[url] = cards
            return cards
        except Exception as e:
            logger.warning(f"Error fetching template: {e}")
            return []
