"""Unit tests for ScryfallFetcher (mocked HTTP, no network)."""

import aiohttp
import pytest

from mtg_combo_cube.scryfall.scryfall_fetcher import ScryfallFetcher
from tests.unit.scryfall_fakes import (
    FakeResponse,
    FakeSession,
    SleepRecorder,
    make_fetcher,
    read_cache_file,
    write_cache_file,
)

URL = "https://api.scryfall.com/cards/search?q=type%3Acreature&order=edhrec"
OTHER_URL = "https://api.scryfall.com/cards/search?q=type%3Aartifact&order=edhrec"


class TestScryfallFetcherCache:
    """Tests for the persistent cache."""

    @pytest.mark.asyncio
    async def test_cache_hit_makes_no_request(self, tmp_path):
        """Test that a cached URL is served without any HTTP request."""
        write_cache_file(tmp_path, {URL: ["Card A", "Card B"]})
        session = FakeSession()
        fetcher = make_fetcher(session, tmp_path)

        async with fetcher:
            names = await fetcher.fetch_card_names(URL)

        assert names == ["Card A", "Card B"]
        assert session.requests == []
        assert fetcher.network_requests == 0
        assert fetcher.cache_hits == 1

    @pytest.mark.asyncio
    async def test_cache_miss_fetches_and_writes(self, tmp_path):
        """Test that a missing URL is fetched once and written to the cache file."""
        session = FakeSession({URL: [FakeResponse(200, ["Card A", "Card B"])]})
        fetcher = make_fetcher(session, tmp_path)

        async with fetcher:
            names = await fetcher.fetch_card_names(URL)
            again = await fetcher.fetch_card_names(URL)

        assert names == ["Card A", "Card B"]
        assert again == names
        assert session.requests == [URL]
        assert read_cache_file(tmp_path) == {URL: ["Card A", "Card B"]}
        # No temp file is left behind
        assert [p.name for p in tmp_path.iterdir()] == [ScryfallFetcher.CACHE_FILENAME]

    @pytest.mark.asyncio
    async def test_written_cache_is_read_by_next_fetcher(self, tmp_path):
        """Test that a second fetcher makes no request for a URL the first one cached."""
        first_session = FakeSession({URL: [FakeResponse(200, ["Card A"])]})
        async with make_fetcher(first_session, tmp_path) as first:
            await first.fetch_card_names(URL)

        second_session = FakeSession()
        async with make_fetcher(second_session, tmp_path) as second:
            names = await second.fetch_card_names(URL)

        assert names == ["Card A"]
        assert second_session.requests == []

    @pytest.mark.asyncio
    async def test_write_keeps_existing_entries(self, tmp_path):
        """Test that writing new results keeps entries already in the cache file."""
        write_cache_file(tmp_path, {OTHER_URL: ["Old Card"]})
        session = FakeSession({URL: [FakeResponse(200, ["Card A"])]})

        # Reading disabled: the file is not used for lookups, but must not be clobbered
        async with make_fetcher(session, tmp_path, enable_read=False) as fetcher:
            await fetcher.fetch_card_names(URL)

        assert read_cache_file(tmp_path) == {OTHER_URL: ["Old Card"], URL: ["Card A"]}

    @pytest.mark.asyncio
    async def test_read_disabled_ignores_cache(self, tmp_path):
        """Test that the cache file is not used for lookups when reading is disabled."""
        write_cache_file(tmp_path, {URL: ["Stale Card"]})
        session = FakeSession({URL: [FakeResponse(200, ["Fresh Card"])]})

        async with make_fetcher(session, tmp_path, enable_read=False) as fetcher:
            names = await fetcher.fetch_card_names(URL)

        assert names == ["Fresh Card"]
        assert session.requests == [URL]
        assert read_cache_file(tmp_path) == {URL: ["Fresh Card"]}

    @pytest.mark.asyncio
    async def test_write_disabled_creates_no_file(self, tmp_path):
        """Test that nothing is written when writing is disabled."""
        session = FakeSession({URL: [FakeResponse(200, ["Card A"])]})

        async with make_fetcher(session, tmp_path, enable_write=False) as fetcher:
            await fetcher.fetch_card_names(URL)

        assert list(tmp_path.iterdir()) == []

    @pytest.mark.asyncio
    async def test_corrupt_cache_file_is_treated_as_empty(self, tmp_path):
        """Test that an unreadable cache file falls back to fetching and is then replaced."""
        (tmp_path / ScryfallFetcher.CACHE_FILENAME).write_text("{not json", encoding="utf-8")
        session = FakeSession({URL: [FakeResponse(200, ["Card A"])]})

        async with make_fetcher(session, tmp_path) as fetcher:
            names = await fetcher.fetch_card_names(URL)

        assert names == ["Card A"]
        assert read_cache_file(tmp_path) == {URL: ["Card A"]}

    @pytest.mark.asyncio
    async def test_flushes_in_batches(self, tmp_path):
        """Test that the cache file is written once a batch is full, before close."""
        urls = [f"{URL}&page={i}" for i in range(3)]
        session = FakeSession({url: [FakeResponse(200, [f"Card {url[-1]}"])] for url in urls})
        fetcher = make_fetcher(session, tmp_path, flush_every=2)

        await fetcher.fetch_card_names(urls[0])
        assert not (tmp_path / ScryfallFetcher.CACHE_FILENAME).exists()
        await fetcher.fetch_card_names(urls[1])
        assert set(read_cache_file(tmp_path)) == set(urls[:2])
        await fetcher.fetch_card_names(urls[2])
        await fetcher.close()
        assert set(read_cache_file(tmp_path)) == set(urls)

    @pytest.mark.asyncio
    async def test_not_found_is_cached_as_empty(self, tmp_path):
        """Test that a 404 (no card matches the search) is a cacheable empty result."""
        session = FakeSession({URL: [FakeResponse(404)]})
        fetcher = make_fetcher(session, tmp_path)

        async with fetcher:
            names = await fetcher.fetch_card_names(URL)

        assert names == []
        assert session.requests == [URL]
        assert fetcher.failed_url_count == 0
        assert read_cache_file(tmp_path) == {URL: []}

        # The cached empty result is a hit for the next fetcher
        next_session = FakeSession()
        async with make_fetcher(next_session, tmp_path) as next_fetcher:
            assert await next_fetcher.fetch_card_names(URL) == []
        assert next_session.requests == []


class TestScryfallFetcherRetries:
    """Tests for retry, backoff, and failure handling."""

    @pytest.mark.asyncio
    async def test_rate_limited_then_ok_retries_and_succeeds(self, tmp_path):
        """Test that a 429 followed by a 200 is retried and the result is cached."""
        session = FakeSession({URL: [FakeResponse(429), FakeResponse(200, ["Card A"])]})
        sleep = SleepRecorder()
        fetcher = make_fetcher(session, tmp_path, sleep=sleep, backoff_seconds=2.0)

        async with fetcher:
            names = await fetcher.fetch_card_names(URL)

        assert names == ["Card A"]
        assert session.requests == [URL, URL]
        assert fetcher.network_requests == 2
        assert fetcher.failed_url_count == 0
        assert sleep.delays == [2.0]
        assert read_cache_file(tmp_path) == {URL: ["Card A"]}

    @pytest.mark.asyncio
    async def test_retry_after_header_is_respected(self, tmp_path):
        """Test that Retry-After sets the delay, bounded by the maximum backoff."""
        session = FakeSession(
            {
                URL: [
                    FakeResponse(429, headers={"Retry-After": "7"}),
                    FakeResponse(503, headers={"Retry-After": "600"}),
                    FakeResponse(200, ["Card A"]),
                ]
            }
        )
        sleep = SleepRecorder()
        fetcher = make_fetcher(
            session, tmp_path, sleep=sleep, backoff_seconds=1.0, max_backoff_seconds=30.0
        )

        async with fetcher:
            names = await fetcher.fetch_card_names(URL)

        assert names == ["Card A"]
        assert sleep.delays == [7.0, 30.0]

    @pytest.mark.asyncio
    async def test_backoff_doubles_between_attempts(self, tmp_path):
        """Test that the retry delay doubles on every attempt."""
        session = FakeSession({URL: [FakeResponse(500)]})
        sleep = SleepRecorder()
        fetcher = make_fetcher(session, tmp_path, sleep=sleep, backoff_seconds=1.0, max_attempts=4)

        async with fetcher:
            await fetcher.fetch_card_names(URL)

        assert sleep.delays == [1.0, 2.0, 4.0]

    @pytest.mark.asyncio
    async def test_persistent_failure_is_not_cached(self, tmp_path):
        """Test that a URL that keeps failing returns None, is counted, and is not cached."""
        session = FakeSession({URL: [FakeResponse(429)]})
        fetcher = make_fetcher(session, tmp_path, max_attempts=3)

        async with fetcher:
            names = await fetcher.fetch_card_names(URL)
            again = await fetcher.fetch_card_names(URL)

        assert names is None
        assert again is None
        # Bounded attempts, and the failed URL is not retried again within the run
        assert session.requests == [URL] * 3
        assert fetcher.failed_url_count == 1
        assert not (tmp_path / ScryfallFetcher.CACHE_FILENAME).exists()

        # A later run retries it
        next_session = FakeSession({URL: [FakeResponse(200, ["Card A"])]})
        async with make_fetcher(next_session, tmp_path) as next_fetcher:
            assert await next_fetcher.fetch_card_names(URL) == ["Card A"]

    @pytest.mark.asyncio
    async def test_failure_does_not_remove_other_results(self, tmp_path):
        """Test that only successful URLs are written when another URL fails."""
        session = FakeSession(
            {URL: [FakeResponse(200, ["Card A"])], OTHER_URL: [FakeResponse(500)]}
        )
        fetcher = make_fetcher(session, tmp_path, max_attempts=2)

        async with fetcher:
            assert await fetcher.fetch_card_names(URL) == ["Card A"]
            assert await fetcher.fetch_card_names(OTHER_URL) is None

        assert read_cache_file(tmp_path) == {URL: ["Card A"]}

    @pytest.mark.asyncio
    async def test_network_error_is_retried(self, tmp_path):
        """Test that a connection error is retried like a server error."""
        session = FakeSession(
            {URL: [aiohttp.ClientConnectionError("boom"), FakeResponse(200, ["Card A"])]}
        )
        fetcher = make_fetcher(session, tmp_path)

        async with fetcher:
            names = await fetcher.fetch_card_names(URL)

        assert names == ["Card A"]
        assert session.requests == [URL, URL]

    @pytest.mark.asyncio
    async def test_persistent_network_error_is_a_failure(self, tmp_path):
        """Test that repeated timeouts end as an uncached failure."""
        session = FakeSession({URL: [TimeoutError()]})
        fetcher = make_fetcher(session, tmp_path, max_attempts=2)

        async with fetcher:
            names = await fetcher.fetch_card_names(URL)

        assert names is None
        assert fetcher.failed_url_count == 1
        assert not (tmp_path / ScryfallFetcher.CACHE_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_client_error_is_not_retried(self, tmp_path):
        """Test that a 4xx other than 404 / 429 fails without retrying."""
        session = FakeSession({URL: [FakeResponse(400)]})
        fetcher = make_fetcher(session, tmp_path)

        async with fetcher:
            names = await fetcher.fetch_card_names(URL)

        assert names is None
        assert session.requests == [URL]
        assert fetcher.failed_url_count == 1


class TestScryfallFetcherRateLimit:
    """Tests for the delay between requests."""

    @pytest.mark.asyncio
    async def test_waits_between_requests(self, tmp_path):
        """Test that a second request waits for the minimum interval."""
        session = FakeSession(
            {URL: [FakeResponse(200, ["Card A"])], OTHER_URL: [FakeResponse(200, ["Card B"])]}
        )
        sleep = SleepRecorder()
        fetcher = make_fetcher(session, tmp_path, sleep=sleep, min_interval_seconds=60)

        async with fetcher:
            await fetcher.fetch_card_names(URL)
            assert sleep.delays == []
            await fetcher.fetch_card_names(OTHER_URL)

        assert len(sleep.delays) == 1
        assert 0 < sleep.delays[0] <= 60

    @pytest.mark.asyncio
    async def test_no_wait_for_cache_hits(self, tmp_path):
        """Test that cache hits are not rate limited."""
        write_cache_file(tmp_path, {URL: ["Card A"], OTHER_URL: ["Card B"]})
        sleep = SleepRecorder()
        fetcher = make_fetcher(FakeSession(), tmp_path, sleep=sleep, min_interval_seconds=60)

        async with fetcher:
            await fetcher.fetch_card_names(URL)
            await fetcher.fetch_card_names(OTHER_URL)

        assert sleep.delays == []
