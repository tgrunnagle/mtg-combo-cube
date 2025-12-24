"""Unit tests for ComboPreprocessor blocklist functionality."""

from unittest.mock import MagicMock

import pytest

from mtg_combo_cube.ilp.combo_preprocessor import ComboPreprocessor


def make_mock_variant(
    variant_id: str,
    card_names: list[str],
    requirements: list[tuple[str, str | None]] | None = None,
    popularity: int = 100,
):
    """Helper to create a mock Variant for testing.

    Args:
        variant_id: Unique ID for the variant
        card_names: List of card names in the combo
        requirements: List of (template_name, scryfall_api) tuples for optional requirements
        popularity: Popularity score
    """
    variant = MagicMock()
    variant.id = variant_id
    variant.popularity = popularity

    # Create mock CardUse objects
    uses = []
    for name in card_names:
        card_use = MagicMock()
        card_use.card.name = name
        uses.append(card_use)
    variant.uses = uses

    # Create mock Requirement objects
    requires = []
    if requirements:
        for template_name, scryfall_api in requirements:
            req = MagicMock()
            req.template.name = template_name
            req.template.scryfall_api = scryfall_api
            requires.append(req)
    variant.requires = requires

    return variant


class TestComboPreprocessorBlocklist:
    """Tests for blocklist filtering in ComboPreprocessor."""

    @pytest.mark.asyncio
    async def test_blocks_combo_with_blocked_card(self):
        """Test that combos with any blocked card are skipped."""
        blocklist = frozenset({"Blocked Card"})
        preprocessor = ComboPreprocessor(blocklist=blocklist)

        # Create a variant with one blocked card
        variant = make_mock_variant("combo1", ["Card A", "Blocked Card", "Card C"])

        combo_data_list, candidate_cards = await preprocessor.preprocess_variants([variant])

        # Combo should be skipped
        assert len(combo_data_list) == 0
        assert len(candidate_cards) == 0

    @pytest.mark.asyncio
    async def test_allows_combo_without_blocked_cards(self):
        """Test that combos without blocked cards are allowed."""
        blocklist = frozenset({"Unrelated Card"})
        preprocessor = ComboPreprocessor(blocklist=blocklist)

        variant = make_mock_variant("combo1", ["Card A", "Card B", "Card C"])

        combo_data_list, candidate_cards = await preprocessor.preprocess_variants([variant])

        assert len(combo_data_list) == 1
        assert combo_data_list[0].id == "combo1"
        assert combo_data_list[0].required_cards == frozenset({"Card A", "Card B", "Card C"})

    @pytest.mark.asyncio
    async def test_empty_blocklist_allows_all(self):
        """Test that empty blocklist allows all combos."""
        preprocessor = ComboPreprocessor(blocklist=frozenset())

        variant = make_mock_variant("combo1", ["Card A", "Card B"])

        combo_data_list, candidate_cards = await preprocessor.preprocess_variants([variant])

        assert len(combo_data_list) == 1

    @pytest.mark.asyncio
    async def test_blocks_multiple_combos_with_blocked_card(self):
        """Test that multiple combos with blocked cards are all skipped."""
        blocklist = frozenset({"Shared Card"})
        preprocessor = ComboPreprocessor(blocklist=blocklist)

        variants = [
            make_mock_variant("combo1", ["Shared Card", "Card A"]),
            make_mock_variant("combo2", ["Shared Card", "Card B"]),
            make_mock_variant("combo3", ["Card C", "Card D"]),  # No blocked card
        ]

        combo_data_list, candidate_cards = await preprocessor.preprocess_variants(variants)

        # Only combo3 should remain
        assert len(combo_data_list) == 1
        assert combo_data_list[0].id == "combo3"

    @pytest.mark.asyncio
    async def test_blocks_combo_if_any_card_blocked(self):
        """Test that combo is blocked if ANY of its cards are blocked, not just all."""
        blocklist = frozenset({"Blocked Card"})
        preprocessor = ComboPreprocessor(blocklist=blocklist)

        # Combo has 3 cards, only 1 is blocked
        variant = make_mock_variant("combo1", ["Card A", "Blocked Card", "Card C"])

        combo_data_list, candidate_cards = await preprocessor.preprocess_variants([variant])

        # Combo should be skipped even though only 1/3 cards are blocked
        assert len(combo_data_list) == 0

    @pytest.mark.asyncio
    async def test_candidate_cards_only_from_valid_combos(self):
        """Test that candidate cards are only built from non-blocked combos."""
        blocklist = frozenset({"Blocked Card"})
        preprocessor = ComboPreprocessor(blocklist=blocklist)

        variants = [
            make_mock_variant("combo1", ["Blocked Card", "Card A"]),  # Blocked
            make_mock_variant("combo2", ["Card B", "Card C"]),  # Valid
        ]

        combo_data_list, candidate_cards = await preprocessor.preprocess_variants(variants)

        # Only Card B and Card C should be candidates
        assert set(candidate_cards.keys()) == {"Card B", "Card C"}
        assert "Blocked Card" not in candidate_cards
        assert "Card A" not in candidate_cards


class TestComboPreprocessorNoBlocklist:
    """Tests for ComboPreprocessor without blocklist (default behavior)."""

    @pytest.mark.asyncio
    async def test_default_no_blocklist(self):
        """Test that default preprocessor has no blocklist."""
        preprocessor = ComboPreprocessor()

        variant = make_mock_variant("combo1", ["Card A", "Card B"])

        combo_data_list, candidate_cards = await preprocessor.preprocess_variants([variant])

        assert len(combo_data_list) == 1
