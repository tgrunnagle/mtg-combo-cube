"""Unit tests for PayoffFetcher (mocked HTTP, no network)."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from mtg_combo_cube.scryfall.payoff_fetcher import PayoffFetcher
from tests.unit.scryfall_fakes import FakeResponse, FakeSession, make_fetcher

STORM = "keyword:storm f:commander"
X_DAMAGE = 'o:"{X}" o:"X damage"'
STORM_URL = PayoffFetcher.search_url(STORM)
X_DAMAGE_URL = PayoffFetcher.search_url(X_DAMAGE)


def make_payoff_fetcher(session: FakeSession, tmp_path: Path, **kwargs) -> PayoffFetcher:
    kwargs.setdefault("enable_read", True)
    kwargs.setdefault("enable_write", True)
    return PayoffFetcher(make_fetcher(session, tmp_path), cache_dir=tmp_path, **kwargs)


def read_cache(fetcher: PayoffFetcher) -> dict:
    with open(fetcher.cache_path, encoding="utf-8") as f:
        return json.load(f)


class TestSearchUrl:
    def test_query_runs_in_edhrec_order_over_paper_cards(self):
        parsed = urlparse(STORM_URL)

        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == PayoffFetcher.SEARCH_URL
        assert parse_qs(parsed.query) == {
            "q": [f"({STORM}) game:paper"],
            "order": ["edhrec"],
            "unique": ["cards"],
        }

    def test_paper_filter_applies_to_a_top_level_or(self):
        # Scryfall's implicit AND binds tighter than "or", so the query is parenthesized
        query = parse_qs(urlparse(PayoffFetcher.search_url("keyword:storm or t:instant")).query)

        assert query["q"] == ["(keyword:storm or t:instant) game:paper"]

    def test_different_queries_have_different_urls(self):
        assert STORM_URL != X_DAMAGE_URL
        assert PayoffFetcher.search_url(STORM) == STORM_URL


class TestPayoffFetcher:
    @pytest.mark.asyncio
    async def test_fetches_each_query_once_and_caches_it(self, tmp_path: Path):
        session = FakeSession(
            {
                STORM_URL: [FakeResponse(200, card_names=["Grapeshot", "Brain Freeze"])],
                X_DAMAGE_URL: [FakeResponse(200, card_names=["Crypt Rats"])],
            }
        )
        fetcher = make_payoff_fetcher(session, tmp_path)

        results = await fetcher.fetch_queries([STORM, X_DAMAGE, STORM])

        assert results == {STORM: ["Grapeshot", "Brain Freeze"], X_DAMAGE: ["Crypt Rats"]}
        assert session.requests == [STORM_URL, X_DAMAGE_URL]
        cache = read_cache(fetcher)
        assert cache["version"] == PayoffFetcher.CACHE_VERSION
        assert set(cache["queries"]) == {STORM_URL, X_DAMAGE_URL}
        assert cache["queries"][STORM_URL]["cards"] == ["Grapeshot", "Brain Freeze"]
        assert cache["queries"][STORM_URL]["fetched_at"].startswith("20")

    @pytest.mark.asyncio
    async def test_cached_query_makes_no_request(self, tmp_path: Path):
        await make_payoff_fetcher(
            FakeSession({STORM_URL: [FakeResponse(200, card_names=["Grapeshot"])]}), tmp_path
        ).fetch_queries([STORM])

        session = FakeSession({X_DAMAGE_URL: [FakeResponse(200, card_names=["Crypt Rats"])]})
        fetcher = make_payoff_fetcher(session, tmp_path)
        results = await fetcher.fetch_queries([STORM, X_DAMAGE])

        # Only the query missing from the cache is requested; both end up in the file
        assert results == {STORM: ["Grapeshot"], X_DAMAGE: ["Crypt Rats"]}
        assert session.requests == [X_DAMAGE_URL]
        assert set(read_cache(fetcher)["queries"]) == {STORM_URL, X_DAMAGE_URL}

    @pytest.mark.asyncio
    async def test_changed_query_is_fetched_and_the_rest_stay_cached(self, tmp_path: Path):
        await make_payoff_fetcher(
            FakeSession({STORM_URL: [FakeResponse(200, card_names=["Grapeshot"])]}), tmp_path
        ).fetch_queries([STORM])
        edited = "keyword:storm f:commander -t:creature"
        edited_url = PayoffFetcher.search_url(edited)

        session = FakeSession({edited_url: [FakeResponse(200, card_names=["Brain Freeze"])]})
        fetcher = make_payoff_fetcher(session, tmp_path)
        results = await fetcher.fetch_queries([edited])

        assert results == {edited: ["Brain Freeze"]}
        assert set(read_cache(fetcher)["queries"]) == {STORM_URL, edited_url}

    @pytest.mark.asyncio
    async def test_failed_query_is_left_out_and_not_cached(self, tmp_path: Path, caplog):
        # A server error after every retry is a transient failure, not a rejected query
        session = FakeSession(
            {
                STORM_URL: [FakeResponse(503)],
                X_DAMAGE_URL: [FakeResponse(200, card_names=["Crypt Rats"])],
            }
        )
        fetcher = make_payoff_fetcher(session, tmp_path)

        with caplog.at_level("WARNING"):
            results = await fetcher.fetch_queries([STORM, X_DAMAGE])

        assert results == {X_DAMAGE: ["Crypt Rats"]}
        assert fetcher.rejected == {}
        assert f"Payoff query could not be fetched: {STORM!r}" in caplog.text
        assert list(read_cache(fetcher)["queries"]) == [X_DAMAGE_URL]

    @pytest.mark.asyncio
    async def test_rejected_query_is_recorded_and_not_cached(self, tmp_path: Path, caplog):
        # A malformed query is a 400, which is not retried; it is not a transient failure
        session = FakeSession(
            {
                STORM_URL: [FakeResponse(400)],
                X_DAMAGE_URL: [FakeResponse(200, card_names=["Crypt Rats"])],
            }
        )
        fetcher = make_payoff_fetcher(session, tmp_path)

        with caplog.at_level("WARNING"):
            results = await fetcher.fetch_queries([STORM, X_DAMAGE])

        assert results == {X_DAMAGE: ["Crypt Rats"]}
        assert fetcher.rejected == {STORM: "HTTP 400"}
        assert f"rejected by Scryfall (HTTP 400): {STORM!r}" in caplog.text
        assert list(read_cache(fetcher)["queries"]) == [X_DAMAGE_URL]
        # It is not asked for again
        assert await fetcher.fetch_queries([STORM]) == {}
        assert session.requests.count(STORM_URL) == 1

    @pytest.mark.asyncio
    async def test_partly_understood_query_is_rejected(self, tmp_path: Path, caplog):
        # Scryfall ignores a term it does not know and says so in `warnings`; the results
        # are a wider search and must not count
        payload = {
            "object": "list",
            "data": [{"name": "Grapeshot"}, {"name": "Lightning Bolt"}],
            "warnings": ["Invalid expression “keywrd:storm” was ignored."],
        }
        session = FakeSession({STORM_URL: [FakeResponse(200, payload=payload)]})
        fetcher = make_payoff_fetcher(session, tmp_path)

        with caplog.at_level("WARNING"):
            results = await fetcher.fetch_queries([STORM])

        assert results == {}
        assert fetcher.rejected == {STORM: "Invalid expression “keywrd:storm” was ignored."}
        assert "only partly understood by Scryfall" in caplog.text
        assert not fetcher.cache_path.exists()

    @pytest.mark.asyncio
    async def test_empty_result_is_returned_but_not_cached(self, tmp_path: Path):
        # Scryfall answers a search without matches with 404; the caller reports it as a
        # table error, and a later run asks again
        session = FakeSession({STORM_URL: [FakeResponse(404)]})
        fetcher = make_payoff_fetcher(session, tmp_path)

        assert await fetcher.fetch_queries([STORM]) == {STORM: []}
        assert not fetcher.cache_path.exists()

    @pytest.mark.asyncio
    async def test_cache_not_read_when_disabled(self, tmp_path: Path):
        await make_payoff_fetcher(
            FakeSession({STORM_URL: [FakeResponse(200, card_names=["Grapeshot"])]}), tmp_path
        ).fetch_queries([STORM])

        session = FakeSession({STORM_URL: [FakeResponse(200, card_names=["Brain Freeze"])]})
        results = await make_payoff_fetcher(session, tmp_path, enable_read=False).fetch_queries(
            [STORM]
        )

        assert results == {STORM: ["Brain Freeze"]}
        assert read_cache(make_payoff_fetcher(session, tmp_path))["queries"][STORM_URL][
            "cards"
        ] == ["Brain Freeze"]

    @pytest.mark.asyncio
    async def test_cache_not_written_when_disabled(self, tmp_path: Path):
        session = FakeSession({STORM_URL: [FakeResponse(200, card_names=["Grapeshot"])]})
        fetcher = make_payoff_fetcher(session, tmp_path, enable_write=False)

        assert await fetcher.fetch_queries([STORM]) == {STORM: ["Grapeshot"]}
        assert not fetcher.cache_path.exists()

    @pytest.mark.asyncio
    async def test_unreadable_cache_is_kept_and_not_overwritten(self, tmp_path: Path, caplog):
        await make_payoff_fetcher(
            FakeSession({STORM_URL: [FakeResponse(200, card_names=["Grapeshot"])]}), tmp_path
        ).fetch_queries([STORM])
        session = FakeSession({X_DAMAGE_URL: [FakeResponse(200, card_names=["Crypt Rats"])]})
        fetcher = make_payoff_fetcher(session, tmp_path)

        def locked() -> dict:
            raise OSError("file locked")

        fetcher._load_cache = locked  # type: ignore[method-assign]
        with caplog.at_level("WARNING"):
            results = await fetcher.fetch_queries([X_DAMAGE])

        assert results == {X_DAMAGE: ["Crypt Rats"]}
        assert "Cache read error" in caplog.text
        assert list(read_cache(fetcher)["queries"]) == [STORM_URL, X_DAMAGE_URL]

    @pytest.mark.asyncio
    async def test_malformed_entry_for_another_query_is_dropped_on_write(self, tmp_path: Path):
        # A bad entry for a query this run does not ask for would otherwise survive every
        # rewrite and keep the whole file unreadable
        session = FakeSession({STORM_URL: [FakeResponse(200, card_names=["Grapeshot"])]})
        fetcher = make_payoff_fetcher(session, tmp_path)
        with open(fetcher.cache_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": PayoffFetcher.CACHE_VERSION,
                    "queries": {X_DAMAGE_URL: {"cards": "Crypt Rats"}},
                },
                f,
            )

        await fetcher.fetch_queries([STORM])

        assert list(read_cache(fetcher)["queries"]) == [STORM_URL]
        second = make_payoff_fetcher(FakeSession(), tmp_path)
        assert await second.fetch_queries([STORM]) == {STORM: ["Grapeshot"]}

    @pytest.mark.parametrize(
        "cached",
        [
            {"version": 0, "queries": {STORM_URL: {"cards": ["Grapeshot"]}}},
            {"version": PayoffFetcher.CACHE_VERSION, "queries": {STORM_URL: ["Grapeshot"]}},
            {"version": PayoffFetcher.CACHE_VERSION, "queries": {STORM_URL: {"cards": "x"}}},
            {"version": PayoffFetcher.CACHE_VERSION, "queries": {STORM_URL: {"cards": [1]}}},
            {
                "version": PayoffFetcher.CACHE_VERSION,
                "queries": {STORM_URL: {"cards": ["Grapeshot"], "fetched_at": 7}},
            },
            {"version": PayoffFetcher.CACHE_VERSION, "templates": {}},
        ],
    )
    @pytest.mark.asyncio
    async def test_outdated_or_malformed_cache_is_treated_as_empty(
        self, tmp_path: Path, cached: dict
    ):
        session = FakeSession({STORM_URL: [FakeResponse(200, card_names=["Brain Freeze"])]})
        fetcher = make_payoff_fetcher(session, tmp_path)
        with open(fetcher.cache_path, "w", encoding="utf-8") as f:
            json.dump(cached, f)

        results = await fetcher.fetch_queries([STORM])

        assert results == {STORM: ["Brain Freeze"]}
        assert session.requests == [STORM_URL]
        # The file is rewritten in the current format
        cache = read_cache(fetcher)
        assert cache["version"] == PayoffFetcher.CACHE_VERSION
        assert cache["queries"][STORM_URL]["cards"] == ["Brain Freeze"]
