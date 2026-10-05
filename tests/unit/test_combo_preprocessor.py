"""Unit tests for ComboPreprocessor blocklist functionality."""

import logging
from unittest.mock import MagicMock

import pytest

from mtg_combo_cube.ilp.combo_preprocessor import ComboPreprocessor, DropReason
from mtg_combo_cube.ilp.requirement_normalizer import prepare_scryfall_url
from tests.unit.scryfall_fakes import (
    FakeResponse,
    FakeSession,
    make_fetcher,
    read_cache_file,
    write_cache_file,
)


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


CREATURE_API = "https://api.scryfall.com/cards/search?q=type%3Acreature+legal%3Acommander"
CREATURE_URL = prepare_scryfall_url(CREATURE_API)
ARTIFACT_API = "https://api.scryfall.com/cards/search?q=type%3Aartifact+legal%3Acommander"
ARTIFACT_URL = prepare_scryfall_url(ARTIFACT_API)


class TestComboPreprocessorScryfall:
    """Tests for template resolution through the shared Scryfall fetcher (mocked HTTP)."""

    @pytest.mark.asyncio
    async def test_warm_cache_makes_no_request(self, tmp_path):
        """Test that preprocessing with a complete cache makes no HTTP request."""
        write_cache_file(tmp_path, {CREATURE_URL: ["Creature A", "Creature B"]})
        session = FakeSession()
        preprocessor = ComboPreprocessor(fetcher=make_fetcher(session, tmp_path))

        variants = [
            make_mock_variant("combo1", ["Card A"], [("Creature", CREATURE_API)]),
            make_mock_variant("combo2", ["Card B"], [("Creature", CREATURE_API)]),
        ]
        combo_data_list, candidate_cards = await preprocessor.preprocess_variants(variants)

        assert session.requests == []
        assert len(combo_data_list) == 2
        assert combo_data_list[0].requirement_options[0].cards == frozenset(
            {"Creature A", "Creature B"}
        )
        assert set(candidate_cards) == {"Card A", "Card B", "Creature A", "Creature B"}

    @pytest.mark.asyncio
    async def test_cold_cache_fetches_each_template_once_and_writes(self, tmp_path):
        """Test that a template shared by several combos is fetched once and cached."""
        session = FakeSession({CREATURE_URL: [FakeResponse(200, ["Creature A"])]})
        preprocessor = ComboPreprocessor(fetcher=make_fetcher(session, tmp_path))

        variants = [
            make_mock_variant("combo1", ["Card A"], [("Creature", CREATURE_API)]),
            make_mock_variant("combo2", ["Card B"], [("Creature", CREATURE_API)]),
        ]
        combo_data_list, _ = await preprocessor.preprocess_variants(variants)

        assert len(combo_data_list) == 2
        assert session.requests == [CREATURE_URL]
        assert read_cache_file(tmp_path) == {CREATURE_URL: ["Creature A"]}

    @pytest.mark.asyncio
    async def test_blocklist_and_limit_applied_after_cache_read(self, tmp_path):
        """Test that the cache holds raw names; blocklist and limit apply on read."""
        raw_names = ["Blocked Card"] + [f"Creature {i}" for i in range(15)]
        write_cache_file(tmp_path, {CREATURE_URL: raw_names})
        session = FakeSession()
        preprocessor = ComboPreprocessor(
            blocklist=frozenset({"Blocked Card"}),
            fetcher=make_fetcher(session, tmp_path),
        )

        variant = make_mock_variant("combo1", ["Card A"], [("Creature", CREATURE_API)])
        combo_data_list, _ = await preprocessor.preprocess_variants([variant])

        # The blocked card is removed first, then the first 10 remaining cards are kept
        assert combo_data_list[0].requirement_options[0].cards == frozenset(
            f"Creature {i}" for i in range(ComboPreprocessor.REQUIREMENT_CARD_LIMIT)
        )
        assert session.requests == []
        # The cache file still holds the raw, unfiltered list
        assert read_cache_file(tmp_path) == {CREATURE_URL: raw_names}

    @pytest.mark.asyncio
    async def test_fetched_result_is_cached_raw(self, tmp_path):
        """Test that a fetched result is cached before the blocklist and limit are applied."""
        raw_names = ["Blocked Card"] + [f"Creature {i}" for i in range(15)]
        session = FakeSession({CREATURE_URL: [FakeResponse(200, raw_names)]})
        preprocessor = ComboPreprocessor(
            blocklist=frozenset({"Blocked Card"}),
            fetcher=make_fetcher(session, tmp_path),
        )

        variant = make_mock_variant("combo1", ["Card A"], [("Creature", CREATURE_API)])
        combo_data_list, _ = await preprocessor.preprocess_variants([variant])

        assert len(combo_data_list[0].requirement_options[0].cards) == 10
        assert read_cache_file(tmp_path) == {CREATURE_URL: raw_names}

    @pytest.mark.asyncio
    async def test_scryfall_failure_is_counted_and_not_cached(self, tmp_path, caplog):
        """Test that a persistent Scryfall error drops the combo loudly and is not cached."""
        session = FakeSession(
            {
                CREATURE_URL: [FakeResponse(429)],
                ARTIFACT_URL: [FakeResponse(200, ["Artifact A"])],
            }
        )
        fetcher = make_fetcher(session, tmp_path, max_attempts=2)
        preprocessor = ComboPreprocessor(fetcher=fetcher)

        variants = [
            make_mock_variant("combo1", ["Card A"], [("Creature", CREATURE_API)]),
            make_mock_variant("combo2", ["Card B"], [("Creature", CREATURE_API)]),
            make_mock_variant("combo3", ["Card C"], [("Artifact", ARTIFACT_API)]),
        ]
        with caplog.at_level(logging.INFO, logger="mtg_combo_cube.ilp.combo_preprocessor"):
            combo_data_list, _ = await preprocessor.preprocess_variants(variants)

        assert [combo.id for combo in combo_data_list] == ["combo3"]
        assert preprocessor.drop_counts[DropReason.SCRYFALL_FAILURE] == 2
        assert preprocessor.drop_counts.total() == 2
        # Retried up to the attempt limit once, not once per combo
        assert session.requests.count(CREATURE_URL) == 2
        assert read_cache_file(tmp_path) == {ARTIFACT_URL: ["Artifact A"]}

        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("failed for 1 templates" in m and "2 combos" in m for m in warnings)

    @pytest.mark.asyncio
    async def test_drop_reasons_are_counted_and_logged(self, tmp_path, caplog):
        """Test that each drop reason is counted separately and summarized at INFO."""
        session = FakeSession(
            {
                CREATURE_URL: [FakeResponse(404)],
                ARTIFACT_URL: [FakeResponse(200, ["Blocked Card"])],
            }
        )
        preprocessor = ComboPreprocessor(
            blocklist=frozenset({"Blocked Card"}),
            fetcher=make_fetcher(session, tmp_path),
        )

        variants = [
            make_mock_variant("kept", ["Card A"]),
            make_mock_variant("blocked", ["Blocked Card", "Card B"]),
            make_mock_variant("no_api", ["Card C"], [("Something", None)]),
            make_mock_variant("no_match", ["Card D"], [("Creature", CREATURE_API)]),
            make_mock_variant("all_blocked", ["Card E"], [("Artifact", ARTIFACT_API)]),
        ]
        with caplog.at_level(logging.INFO, logger="mtg_combo_cube.ilp.combo_preprocessor"):
            combo_data_list, _ = await preprocessor.preprocess_variants(variants)

        assert [combo.id for combo in combo_data_list] == ["kept"]
        assert preprocessor.drop_counts == {
            DropReason.BLOCKED_CARD: 1,
            DropReason.NO_SCRYFALL_API: 1,
            DropReason.EMPTY_MATCH: 2,
        }
        # A 404 is a legitimate empty result, cached like any other
        assert read_cache_file(tmp_path) == {CREATURE_URL: [], ARTIFACT_URL: ["Blocked Card"]}

        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        summary = next(m for m in infos if m.startswith("Dropped"))
        assert "Dropped 4 of 5 combos" in summary
        assert "blocked_card=1" in summary
        assert "no_scryfall_api=1" in summary
        assert "scryfall_failure=0" in summary
        assert "empty_match=2" in summary
        assert "2 network requests" in summary
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
