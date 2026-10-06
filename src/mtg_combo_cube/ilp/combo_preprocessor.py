"""Converts Variant objects to ILP-ready ComboData."""

import logging
from collections import Counter, defaultdict
from enum import StrEnum

from mtg_combo_cube.ilp.ilp_models import CandidateCard, ComboData, RequirementOption
from mtg_combo_cube.ilp.requirement_normalizer import (
    compute_requirement_group_key,
    prepare_scryfall_url,
)
from mtg_combo_cube.models import Variant
from mtg_combo_cube.scryfall.scryfall_fetcher import ScryfallFetcher

logger = logging.getLogger(__name__)


class DropReason(StrEnum):
    """Why a combo was left out of the ILP instance."""

    BLOCKED_CARD = "blocked_card"
    NO_SCRYFALL_API = "no_scryfall_api"
    SCRYFALL_FAILURE = "scryfall_failure"
    EMPTY_MATCH = "empty_match"


class ComboPreprocessor:
    """Converts Variant objects to ILP-ready ComboData."""

    REQUIREMENT_CARD_LIMIT = 10  # Max cards per template requirement

    def __init__(
        self,
        blocklist: frozenset[str] = frozenset(),
        fetcher: ScryfallFetcher | None = None,
    ):
        self._blocklist = blocklist
        # Without an explicit fetcher, results are not cached on disk
        self._fetcher = fetcher if fetcher is not None else ScryfallFetcher()
        self.drop_counts: Counter[DropReason] = Counter()

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
        self.drop_counts = Counter()

        # Track relationships per card
        card_combo_ids: dict[str, set[str]] = defaultdict(set)
        card_requirement_keys: dict[str, set[str]] = defaultdict(set)

        # One shared HTTP session for the whole pass; the cache is flushed on exit
        async with self._fetcher:
            for variant in variants:
                combo_data = await self._process_single_variant(variant)
                if isinstance(combo_data, DropReason):
                    self.drop_counts[combo_data] += 1
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

        self._log_summary(len(variants))
        logger.info(
            f"Preprocessed {len(combo_data_list)} combos with {len(candidate_cards)} unique cards"
        )
        return combo_data_list, candidate_cards

    def _log_summary(self, variant_count: int) -> None:
        """Log dropped combos per reason and how the Scryfall data was obtained."""
        by_reason = ", ".join(f"{reason.value}={self.drop_counts[reason]}" for reason in DropReason)
        logger.info(
            f"Dropped {self.drop_counts.total()} of {variant_count} combos ({by_reason}); "
            f"Scryfall: {self._fetcher.network_requests} network requests, "
            f"{self._fetcher.cache_hits} templates read from cache, "
            f"{self._fetcher.failed_url_count} failed"
        )
        if self._fetcher.failed_url_count:
            logger.warning(
                f"Scryfall fetch failed for {self._fetcher.failed_url_count} templates; "
                f"{self.drop_counts[DropReason.SCRYFALL_FAILURE]} combos were dropped. "
                "The instance is incomplete; failures are not cached, so re-run to retry them."
            )

    async def _process_single_variant(self, variant: Variant) -> ComboData | DropReason:
        """Convert a single Variant to ComboData, or give the reason it is dropped."""
        # Extract required cards from 'uses'
        required_cards = frozenset(use.card.name for use in variant.uses)

        # Skip combo if ANY required card is blocked
        if required_cards & self._blocklist:
            blocked = required_cards & self._blocklist
            logger.debug(f"Skipping combo {variant.id}: blocked cards {blocked}")
            return DropReason.BLOCKED_CARD

        # Resolve template requirements
        requirement_options: list[RequirementOption] = []
        for req in variant.requires:
            if req.template.scryfall_api is None:
                # No API means unresolvable - skip this combo
                logger.debug(f"Skipping combo {variant.id}: unresolvable requirement")
                return DropReason.NO_SCRYFALL_API

            cards = await self._resolve_template(req.template.scryfall_api)
            if cards is None:
                logger.debug(f"Skipping combo {variant.id}: Scryfall fetch failed")
                return DropReason.SCRYFALL_FAILURE
            if not cards:
                logger.debug(f"Skipping combo {variant.id}: empty requirement options")
                return DropReason.EMPTY_MATCH

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
            group_key=self._group_key(variant),
        )

    @staticmethod
    def _group_key(variant: Variant) -> str:
        """
        The combo group of a variant: its sorted Spellbook combo ids ('of') joined with "+".

        A variant of two combos combined is its own group. Without 'of' ids the variant is
        a group of its own (ComboData defaults the key to the variant id).
        """
        return "+".join(str(combo_id) for combo_id in sorted(ref.id for ref in variant.of))

    async def _resolve_template(self, scryfall_api: str) -> list[str] | None:
        """Get cards matching a Scryfall template query, or None if the fetch failed."""
        url = prepare_scryfall_url(scryfall_api)
        cards = await self._fetcher.fetch_card_names(url)
        if cards is None:
            return None
        # Filter out blocked cards before applying limit
        cards = [card for card in cards if card not in self._blocklist]
        return cards[: self.REQUIREMENT_CARD_LIMIT]
