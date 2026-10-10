"""Unit tests for the blocklist queries (mocked HTTP, no network)."""

from pathlib import Path

import pytest

from mtg_combo_cube.blocklist import (
    PAGE_SIZE,
    BlocklistFetchError,
    BlocklistQueryError,
    blocked_by_queries,
    fetch_blocklist,
)
from mtg_combo_cube.scryfall.payoff_fetcher import PayoffFetcher
from tests.unit.scryfall_fakes import FakeResponse, FakeSession, make_fetcher

STICKERS = "t:stickers"
STICKERS_URL = PayoffFetcher.search_url(STICKERS, PayoffFetcher.PAPER_ONLY)
BLOCKLIST = frozenset({"Command Tower"})


class TestBlockedByQueries:
    def test_union_of_the_query_results(self):
        blocked = blocked_by_queries(
            ["a", "b"], {"a": ["Card A", "Card B"], "b": ["Card B", "Card C"]}, {}
        )

        assert blocked == frozenset({"Card A", "Card B", "Card C"})

    def test_rejected_query_is_a_configuration_error(self):
        with pytest.raises(BlocklistQueryError, match=r"'t:nope' \(HTTP 400\)"):
            blocked_by_queries(["t:nope"], {}, {"t:nope": "HTTP 400"})

    def test_query_without_results_is_a_fetch_error(self):
        with pytest.raises(BlocklistFetchError, match="'b'"):
            blocked_by_queries(["a", "b"], {"a": ["Card A"]}, {})

    def test_query_matching_no_card_blocks_nothing(self, caplog: pytest.LogCaptureFixture):
        assert blocked_by_queries(["a"], {"a": []}, {}) == frozenset()
        assert "matches no card" in caplog.text

    def test_full_page_is_a_warning(self, caplog: pytest.LogCaptureFixture):
        cards = [f"Card {i}" for i in range(PAGE_SIZE)]

        assert len(blocked_by_queries(["a"], {"a": cards}, {})) == PAGE_SIZE
        assert f"first {PAGE_SIZE} cards" in caplog.text


class TestFetchBlocklist:
    @pytest.mark.asyncio
    async def test_without_queries_the_blocklist_is_unchanged(self, tmp_path: Path):
        session = FakeSession()

        blocked = await fetch_blocklist(BLOCKLIST, (), fetcher=make_fetcher(session, tmp_path))

        assert blocked == BLOCKLIST
        assert session.requests == []

    @pytest.mark.asyncio
    async def test_query_cards_join_the_blocklist_and_are_cached(self, tmp_path: Path):
        session = FakeSession(
            {STICKERS_URL: [FakeResponse(200, card_names=["Geek Lotus Warrior"])]}
        )

        blocked = await fetch_blocklist(
            BLOCKLIST,
            [STICKERS],
            read_cache=True,
            cache_dir=tmp_path,
            fetcher=make_fetcher(session, tmp_path),
        )
        cached_session = FakeSession()
        cached = await fetch_blocklist(
            BLOCKLIST,
            [STICKERS],
            read_cache=True,
            cache_dir=tmp_path,
            fetcher=make_fetcher(cached_session, tmp_path),
        )

        assert blocked == cached == frozenset({"Command Tower", "Geek Lotus Warrior"})
        assert session.requests == [STICKERS_URL]
        assert cached_session.requests == []

    @pytest.mark.asyncio
    async def test_rejected_query_raises(self, tmp_path: Path):
        session = FakeSession({STICKERS_URL: [FakeResponse(400)]})

        with pytest.raises(BlocklistQueryError):
            await fetch_blocklist(
                BLOCKLIST, [STICKERS], cache_dir=tmp_path, fetcher=make_fetcher(session, tmp_path)
            )

    @pytest.mark.asyncio
    async def test_failed_query_raises(self, tmp_path: Path):
        session = FakeSession({STICKERS_URL: [FakeResponse(503)]})

        with pytest.raises(BlocklistFetchError):
            await fetch_blocklist(
                BLOCKLIST,
                [STICKERS],
                cache_dir=tmp_path,
                fetcher=make_fetcher(session, tmp_path, max_attempts=1),
            )
