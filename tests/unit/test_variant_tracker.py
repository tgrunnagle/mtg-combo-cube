"""Unit tests for VariantTracker's use of the shared Scryfall fetcher (mocked HTTP)."""

import pytest

from mtg_combo_cube.greedy.variant_tracker import VariantTracker
from mtg_combo_cube.ilp.requirement_normalizer import prepare_scryfall_url
from tests.unit.scryfall_fakes import FakeResponse, FakeSession, make_fetcher, write_cache_file
from tests.unit.test_combo_preprocessor import make_mock_variant

CREATURE_API = "https://api.scryfall.com/cards/search?q=type%3Acreature+legal%3Acommander"
CREATURE_URL = prepare_scryfall_url(CREATURE_API)


class TestVariantTrackerScryfall:
    """Tests for requirement card lookup in VariantTracker."""

    @pytest.mark.asyncio
    async def test_counts_top_requirement_cards(self, tmp_path):
        """Test that the tracker fetches a template once and counts its top-ranked cards."""
        raw_names = ["Blocked Card"] + [f"Creature {i}" for i in range(8)]
        session = FakeSession({CREATURE_URL: [FakeResponse(200, raw_names)]})
        tracker = VariantTracker(
            blocklist=frozenset({"Blocked Card"}),
            fetcher=make_fetcher(session, tmp_path),
        )

        await tracker.process_variant(
            make_mock_variant("combo1", ["Card A"], [("Creature", CREATURE_API)])
        )
        await tracker.process_variant(
            make_mock_variant("combo2", ["Card A"], [("Creature", CREATURE_API)])
        )

        assert session.requests == [CREATURE_URL]
        assert tracker.count_variants() == 2
        # Card A plus the first REQUIRED_CARD_RANK_LIMIT unblocked template cards
        assert tracker.count_cards() == 1 + VariantTracker.REQUIRED_CARD_RANK_LIMIT
        top_cards = tracker.get_top_cards_by_count(10)
        assert set(top_cards) == {"Card A"} | {f"Creature {i}" for i in range(5)}

    @pytest.mark.asyncio
    async def test_reads_shared_cache(self, tmp_path):
        """Test that the tracker uses the same cache file as the ILP preprocessor."""
        write_cache_file(tmp_path, {CREATURE_URL: ["Creature A"]})
        session = FakeSession()
        tracker = VariantTracker(fetcher=make_fetcher(session, tmp_path))

        await tracker.process_variant(
            make_mock_variant("combo1", ["Card A"], [("Creature", CREATURE_API)])
        )

        assert session.requests == []
        assert tracker.count_variants() == 1

    @pytest.mark.asyncio
    async def test_skips_combo_when_fetch_fails(self, tmp_path):
        """Test that a combo is skipped when its template cannot be fetched."""
        session = FakeSession({CREATURE_URL: [FakeResponse(500)]})
        tracker = VariantTracker(fetcher=make_fetcher(session, tmp_path, max_attempts=2))

        await tracker.process_variant(
            make_mock_variant("combo1", ["Card A"], [("Creature", CREATURE_API)])
        )

        assert tracker.count_variants() == 0
        assert tracker.count_cards() == 0
