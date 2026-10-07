"""Unit tests for CardAttributeFetcher and CardAttributes (mocked HTTP, no network)."""

import json

import pytest

from mtg_combo_cube.scryfall.card_attribute_fetcher import (
    UNKNOWN_CARD,
    CardAttributeFetcher,
    CardAttributes,
)
from tests.unit.scryfall_fakes import FakeResponse, FakeSession, make_fetcher

URL = CardAttributeFetcher.COLLECTION_URL


def card_entry(
    name: str, identity: list[str], type_line: str = "Creature", cmc: float = 2.0
) -> dict:
    return {"name": name, "color_identity": identity, "type_line": type_line, "cmc": cmc}


def collection_response(cards: dict[str, list[str]] | list[dict]) -> FakeResponse:
    """A collection response: card name -> color_identity list, or full card entries."""
    data = (
        cards
        if isinstance(cards, list)
        else [card_entry(name, identity) for name, identity in cards.items()]
    )
    return FakeResponse(200, payload={"object": "list", "not_found": [], "data": data})


def make_attribute_fetcher(session: FakeSession, tmp_path, **kwargs) -> CardAttributeFetcher:
    kwargs.setdefault("enable_read", True)
    kwargs.setdefault("enable_write", True)
    return CardAttributeFetcher(make_fetcher(session, tmp_path), cache_dir=tmp_path, **kwargs)


async def fetch(session: FakeSession, tmp_path, names: list[str], **kwargs) -> dict:
    return await make_attribute_fetcher(session, tmp_path, **kwargs).fetch_attributes(names)


class TestCardAttributes:
    def test_types_are_the_words_before_the_em_dash(self):
        attributes = CardAttributes("W", "Legendary Artifact Creature — Golem", 3)

        assert attributes.types == {"Legendary", "Artifact", "Creature"}

    def test_type_line_without_subtypes(self):
        assert CardAttributes("", "Artifact").types == {"Artifact"}
        assert CardAttributes("", "Instant").types == {"Instant"}

    def test_types_come_from_the_front_face(self):
        giant = CardAttributes("R", "Creature — Giant // Instant — Adventure", 3)
        awakening = CardAttributes("R", "Instant // Land", 3)

        assert giant.types == {"Creature"}
        assert awakening.types == {"Instant"}

    def test_color_count_properties(self):
        assert CardAttributes("WU").is_multicolor
        assert not CardAttributes("W").is_multicolor
        assert CardAttributes("").is_colorless
        assert not CardAttributes("W").is_colorless

    def test_unknown_card_is_colorless_typeless_and_free(self):
        assert UNKNOWN_CARD.is_colorless
        assert UNKNOWN_CARD.types == frozenset()
        assert UNKNOWN_CARD.mana_value == 0


class TestCardAttributeFetcher:
    @pytest.mark.asyncio
    async def test_attributes_are_parsed(self, tmp_path):
        session = FakeSession(
            {
                URL: [
                    collection_response(
                        [
                            card_entry("Card A", ["G", "W"], "Enchantment Creature — Elf", 4),
                            card_entry("Card B", [], "Artifact", 1),
                            card_entry("Card C", ["U"], "Instant", 0.5),
                        ]
                    )
                ]
            }
        )

        attributes = await fetch(session, tmp_path, ["Card A", "Card B", "Card C"])

        # Identities are in WUBRG order, mana values are floats
        assert attributes == {
            "Card A": CardAttributes("WG", "Enchantment Creature — Elf", 4.0),
            "Card B": CardAttributes("", "Artifact", 1.0),
            "Card C": CardAttributes("U", "Instant", 0.5),
        }
        assert isinstance(attributes["Card A"].mana_value, float)
        assert session.bodies == [
            {"identifiers": [{"name": "Card A"}, {"name": "Card B"}, {"name": "Card C"}]}
        ]

    @pytest.mark.asyncio
    async def test_missing_fields_default(self, tmp_path):
        session = FakeSession(
            {URL: [collection_response([{"name": "Card A", "color_identity": ["R"]}])]}
        )

        attributes = await fetch(session, tmp_path, ["Card A"])

        assert attributes == {"Card A": CardAttributes("R", "", 0.0)}

    @pytest.mark.asyncio
    async def test_requests_are_batched(self, tmp_path):
        batch = CardAttributeFetcher.BATCH_SIZE
        names = [f"Card {i}" for i in range(batch + 5)]
        session = FakeSession(
            {
                URL: [
                    collection_response(dict.fromkeys(names[:batch], ["R"])),
                    collection_response(dict.fromkeys(names[batch:], ["B"])),
                ]
            }
        )

        attributes = await fetch(session, tmp_path, names)

        assert [len(body["identifiers"]) for body in session.bodies] == [batch, 5]
        assert attributes["Card 0"].color_identity == "R"
        assert attributes[names[-1]].color_identity == "B"
        assert len(attributes) == len(names)

    @pytest.mark.asyncio
    async def test_multi_faced_card_is_requested_by_front_face(self, tmp_path):
        session = FakeSession(
            {
                URL: [
                    collection_response(
                        [
                            card_entry("Front // Back", ["U"], "Creature // Creature", 1),
                            card_entry("Left // Right", ["B", "R"], "Instant // Instant", 4),
                        ]
                    )
                ]
            }
        )

        attributes = await fetch(session, tmp_path, ["Front", "Left // Right"])

        assert attributes == {
            "Front": CardAttributes("U", "Creature // Creature", 1.0),
            "Left // Right": CardAttributes("BR", "Instant // Instant", 4.0),
        }
        assert session.bodies == [{"identifiers": [{"name": "Front"}, {"name": "Left"}]}]

    @pytest.mark.asyncio
    async def test_unknown_card_is_left_out(self, tmp_path):
        session = FakeSession({URL: [collection_response({"Card A": ["W"]})]})

        attributes = await fetch(session, tmp_path, ["Card A", "No Such Card"])

        assert list(attributes) == ["Card A"]

    @pytest.mark.asyncio
    async def test_failed_request_returns_nothing(self, tmp_path):
        session = FakeSession({URL: [FakeResponse(400)]})
        fetcher = make_attribute_fetcher(session, tmp_path)

        assert await fetcher.fetch_attributes(["Card A"]) == {}
        assert not fetcher.cache_path.exists()

    @pytest.mark.asyncio
    async def test_cache_is_written_and_read(self, tmp_path):
        first_session = FakeSession(
            {URL: [collection_response([card_entry("Card A", ["W"], "Sorcery", 3)])]}
        )
        await fetch(first_session, tmp_path, ["Card A"])

        # Only the card missing from the cache is requested
        second_session = FakeSession({URL: [collection_response({"Card B": ["U"]})]})
        second = make_attribute_fetcher(second_session, tmp_path)
        attributes = await second.fetch_attributes(["Card A", "Card B"])

        assert attributes == {
            "Card A": CardAttributes("W", "Sorcery", 3.0),
            "Card B": CardAttributes("U", "Creature", 2.0),
        }
        assert second_session.bodies == [{"identifiers": [{"name": "Card B"}]}]
        with open(second.cache_path, encoding="utf-8") as f:
            assert json.load(f) == {
                "version": CardAttributeFetcher.CACHE_VERSION,
                "cards": {
                    "Card A": {"color_identity": "W", "type_line": "Sorcery", "mana_value": 3.0},
                    "Card B": {"color_identity": "U", "type_line": "Creature", "mana_value": 2.0},
                },
            }

    @pytest.mark.asyncio
    async def test_fully_cached_makes_no_request(self, tmp_path):
        first_session = FakeSession({URL: [collection_response({"Card A": ["W"]})]})
        await fetch(first_session, tmp_path, ["Card A"])

        session = FakeSession()
        attributes = await fetch(session, tmp_path, ["Card A"])

        assert attributes == {"Card A": CardAttributes("W", "Creature", 2.0)}
        assert session.requests == []

    @pytest.mark.asyncio
    async def test_cache_not_read_when_disabled(self, tmp_path):
        first_session = FakeSession({URL: [collection_response({"Card A": ["W"]})]})
        await fetch(first_session, tmp_path, ["Card A"])

        session = FakeSession({URL: [collection_response({"Card A": ["G"]})]})
        attributes = await fetch(session, tmp_path, ["Card A"], enable_read=False)

        assert attributes["Card A"].color_identity == "G"

    @pytest.mark.parametrize(
        "cached",
        [
            {"version": 0, "cards": {"Card A": "W"}},  # the old color-only cache
            {"version": CardAttributeFetcher.CACHE_VERSION, "cards": {"Card A": {"typo": 1}}},
            {"version": CardAttributeFetcher.CACHE_VERSION, "cards": {"Card A": "W"}},
        ],
    )
    @pytest.mark.asyncio
    async def test_outdated_or_malformed_cache_is_treated_as_empty(self, tmp_path, cached):
        fetcher_session = FakeSession({URL: [collection_response({"Card A": ["G"]})]})
        fetcher = make_attribute_fetcher(fetcher_session, tmp_path)
        with open(fetcher.cache_path, "w", encoding="utf-8") as f:
            json.dump(cached, f)

        attributes = await fetcher.fetch_attributes(["Card A"])

        assert attributes["Card A"].color_identity == "G"
        assert len(fetcher_session.bodies) == 1
        # The file is rewritten in the current format
        with open(fetcher.cache_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["version"] == CardAttributeFetcher.CACHE_VERSION
        assert data["cards"] == {
            "Card A": {"color_identity": "G", "type_line": "Creature", "mana_value": 2.0}
        }
