"""Unit tests for CardColorFetcher (mocked HTTP, no network)."""

import json

import pytest

from mtg_combo_cube.scryfall.card_color_fetcher import CardColorFetcher
from tests.unit.scryfall_fakes import FakeResponse, FakeSession, make_fetcher

URL = CardColorFetcher.COLLECTION_URL


def collection_response(cards: dict[str, list[str]]) -> FakeResponse:
    """A collection response with the given card name -> color_identity list."""
    return FakeResponse(
        200,
        payload={
            "object": "list",
            "not_found": [],
            "data": [
                {"name": name, "color_identity": identity} for name, identity in cards.items()
            ],
        },
    )


def make_color_fetcher(session: FakeSession, tmp_path, **kwargs) -> CardColorFetcher:
    kwargs.setdefault("enable_read", True)
    kwargs.setdefault("enable_write", True)
    return CardColorFetcher(make_fetcher(session, tmp_path), cache_dir=tmp_path, **kwargs)


class TestCardColorFetcher:
    @pytest.mark.asyncio
    async def test_identities_are_in_wubrg_order(self, tmp_path):
        session = FakeSession(
            {URL: [collection_response({"Card A": ["G", "W"], "Card B": [], "Card C": ["U"]})]}
        )
        fetcher = make_color_fetcher(session, tmp_path)

        identities = await fetcher.fetch_color_identities(["Card A", "Card B", "Card C"])

        assert identities == {"Card A": "WG", "Card B": "", "Card C": "U"}
        assert session.bodies == [
            {"identifiers": [{"name": "Card A"}, {"name": "Card B"}, {"name": "Card C"}]}
        ]

    @pytest.mark.asyncio
    async def test_requests_are_batched(self, tmp_path):
        names = [f"Card {i}" for i in range(CardColorFetcher.BATCH_SIZE + 5)]
        session = FakeSession(
            {
                URL: [
                    collection_response(dict.fromkeys(names[: CardColorFetcher.BATCH_SIZE], ["R"])),
                    collection_response(dict.fromkeys(names[CardColorFetcher.BATCH_SIZE :], ["B"])),
                ]
            }
        )
        fetcher = make_color_fetcher(session, tmp_path)

        identities = await fetcher.fetch_color_identities(names)

        assert [len(body["identifiers"]) for body in session.bodies] == [
            CardColorFetcher.BATCH_SIZE,
            5,
        ]
        assert identities["Card 0"] == "R"
        assert identities[names[-1]] == "B"
        assert len(identities) == len(names)

    @pytest.mark.asyncio
    async def test_multi_faced_card_is_requested_by_front_face(self, tmp_path):
        session = FakeSession(
            {URL: [collection_response({"Front // Back": ["U"], "Left // Right": ["B", "R"]})]}
        )
        fetcher = make_color_fetcher(session, tmp_path)

        identities = await fetcher.fetch_color_identities(["Front", "Left // Right"])

        assert identities == {"Front": "U", "Left // Right": "BR"}
        assert session.bodies == [{"identifiers": [{"name": "Front"}, {"name": "Left"}]}]

    @pytest.mark.asyncio
    async def test_unknown_card_is_left_out(self, tmp_path):
        session = FakeSession({URL: [collection_response({"Card A": ["W"]})]})
        fetcher = make_color_fetcher(session, tmp_path)

        identities = await fetcher.fetch_color_identities(["Card A", "No Such Card"])

        assert identities == {"Card A": "W"}

    @pytest.mark.asyncio
    async def test_failed_request_returns_no_colors(self, tmp_path):
        session = FakeSession({URL: [FakeResponse(400)]})
        fetcher = make_color_fetcher(session, tmp_path)

        assert await fetcher.fetch_color_identities(["Card A"]) == {}
        assert not fetcher.cache_path.exists()

    @pytest.mark.asyncio
    async def test_cache_is_written_and_read(self, tmp_path):
        first_session = FakeSession({URL: [collection_response({"Card A": ["W"]})]})
        await make_color_fetcher(first_session, tmp_path).fetch_color_identities(["Card A"])

        # Only the card missing from the cache is requested
        second_session = FakeSession({URL: [collection_response({"Card B": ["U"]})]})
        second = make_color_fetcher(second_session, tmp_path)
        identities = await second.fetch_color_identities(["Card A", "Card B"])

        assert identities == {"Card A": "W", "Card B": "U"}
        assert second_session.bodies == [{"identifiers": [{"name": "Card B"}]}]
        with open(second.cache_path, encoding="utf-8") as f:
            assert json.load(f) == {
                "version": CardColorFetcher.CACHE_VERSION,
                "cards": {"Card A": "W", "Card B": "U"},
            }

    @pytest.mark.asyncio
    async def test_fully_cached_makes_no_request(self, tmp_path):
        first_session = FakeSession({URL: [collection_response({"Card A": ["W"]})]})
        await make_color_fetcher(first_session, tmp_path).fetch_color_identities(["Card A"])

        session = FakeSession()
        identities = await make_color_fetcher(session, tmp_path).fetch_color_identities(["Card A"])

        assert identities == {"Card A": "W"}
        assert session.requests == []

    @pytest.mark.asyncio
    async def test_cache_not_read_when_disabled(self, tmp_path):
        first_session = FakeSession({URL: [collection_response({"Card A": ["W"]})]})
        await make_color_fetcher(first_session, tmp_path).fetch_color_identities(["Card A"])

        session = FakeSession({URL: [collection_response({"Card A": ["G"]})]})
        fetcher = make_color_fetcher(session, tmp_path, enable_read=False)

        assert await fetcher.fetch_color_identities(["Card A"]) == {"Card A": "G"}
