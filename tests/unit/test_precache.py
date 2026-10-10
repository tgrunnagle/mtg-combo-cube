"""Unit tests for the precache script (fake Spellbook client and HTTP session, no network)."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import aiohttp
import pytest

from mtg_combo_cube.blocklist import BlocklistQueryError
from mtg_combo_cube.ilp.payoffs import parse_payoff_table
from mtg_combo_cube.ilp.requirement_normalizer import prepare_scryfall_url
from mtg_combo_cube.models import Variant
from mtg_combo_cube.precache import PrecacheResult, precache
from mtg_combo_cube.scryfall.card_attribute_fetcher import CardAttributeFetcher
from mtg_combo_cube.scryfall.payoff_fetcher import PayoffFetcher
from mtg_combo_cube.spellbook.api_cache import SpellbookCache
from mtg_combo_cube.spellbook.commander_spellbook import CommanderSpellbook
from tests.unit.scryfall_fakes import FakeResponse, FakeSession, SleepRecorder, read_cache_file

CREATURE_API = "https://api.scryfall.com/cards/search?q=type%3Acreature"
CREATURE_URL = prepare_scryfall_url(CREATURE_API)
COLLECTION_URL = CardAttributeFetcher.COLLECTION_URL
STORM_QUERY = "keyword:storm f:commander"
STORM_URL = PayoffFetcher.search_url(STORM_QUERY)
PAYOFFS = parse_payoff_table({"storm": {"queries": [STORM_QUERY], "cards": ["Aetherflux"]}})
SCRYFALL_MAX_ATTEMPTS = 5  # ScryfallFetcher default: attempts before a request counts as failed

CARD_STATES = {
    "zoneLocations": ["B"],
    "exileCardState": "",
    "mustBeCommander": False,
    "libraryCardState": "",
    "graveyardCardState": "",
    "battlefieldCardState": "",
}


def make_variant(
    variant_id: str, card_names: list[str], template_api: str | None = None
) -> Variant:
    """A complete Variant (it has to survive the cache round trip), optionally with a template."""
    uses = [
        {
            "card": {
                "id": index,
                "name": name,
                "spoiler": False,
                "oracleId": f"oracle-{name}",
                "typeLine": "Creature",
                **{
                    f"imageUri{side}{size}": None
                    for side in ("Back", "Front")
                    for size in ("Png", "Large", "Small", "Normal", "ArtCrop")
                },
            },
            "quantity": 1,
            **CARD_STATES,
        }
        for index, name in enumerate(card_names)
    ]
    requires = (
        [
            {
                "quantity": 1,
                "template": {"id": 1, "name": "Creature", "scryfallApi": template_api},
                **CARD_STATES,
            }
        ]
        if template_api
        else []
    )
    return Variant.model_validate(
        {
            "id": variant_id,
            "of": [],
            "uses": uses,
            "notes": "",
            "prices": {"tcgplayer": "0", "cardmarket": "0", "cardkingdom": "0"},
            "status": "OK",
            "spoiler": False,
            "identity": "C",
            "includes": [],
            "produces": [],
            "requires": requires,
            "legalities": {
                "brawl": True,
                "predh": True,
                "legacy": True,
                "modern": True,
                "pauper": True,
                "pioneer": True,
                "vintage": True,
                "standard": True,
                "commander": True,
                "premodern": True,
                "oathbreaker": True,
                "pauperCommander": True,
                "pauperCommanderMain": True,
            },
            "popularity": 10,
            "bracketTag": "C",
            "description": "",
            "manaNeeded": "",
            "variantCount": 1,
            "manaValueNeeded": 0,
            "easyPrerequisites": "",
            "notablePrerequisites": "",
        }
    )


class FakeSpellbook:
    """Yields scripted variants; each queued error fails one get_variants call first."""

    def __init__(self, variants: list[Variant], errors: list[Exception] | None = None):
        self._variants = variants
        self._errors = list(errors or [])
        self.calls = 0

    async def get_variants(
        self, max_cards_in_combo: int, max_variants: int
    ) -> AsyncIterator[Variant]:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        for variant in self._variants[:max_variants]:
            yield variant


def colors_response(cards: dict[str, list[str]]) -> FakeResponse:
    return FakeResponse(
        200,
        payload={
            "object": "list",
            "data": [{"name": name, "color_identity": ci} for name, ci in cards.items()],
        },
    )


VARIANTS = [
    make_variant("combo1", ["Card A", "Card B"]),
    make_variant("combo2", ["Card A"], template_api=CREATURE_API),
]
ALL_COLORS = colors_response({"Card A": ["W"], "Card B": ["U"], "Creature X": ["G"]})


def healthy_session() -> FakeSession:
    return FakeSession(
        {
            CREATURE_URL: [FakeResponse(200, card_names=["Creature X"])],
            COLLECTION_URL: [ALL_COLORS],
        }
    )


async def run_precache(
    tmp_path: Path,
    spellbook: FakeSpellbook,
    session: FakeSession,
    sleep: SleepRecorder | None = None,
    **kwargs,
) -> PrecacheResult:
    return await precache(
        max_variants=100,
        cache_dir=tmp_path,
        spellbook=cast(CommanderSpellbook, spellbook),
        session=cast(aiohttp.ClientSession, session),
        sleep=sleep if sleep is not None else SleepRecorder(),
        **kwargs,
    )


def read_colors(tmp_path: Path) -> dict[str, str]:
    """The color identities in the card attribute cache."""
    with open(tmp_path / CardAttributeFetcher.CACHE_FILENAME, encoding="utf-8") as f:
        return {name: entry["color_identity"] for name, entry in json.load(f)["cards"].items()}


class TestVariantsCacheWrite:
    def test_write_replaces_an_existing_file(self, tmp_path):
        cache = SpellbookCache(cache_dir=tmp_path)
        path = cache.variants_cache_path(4, 100)

        assert cache.write_variants_cache(path, VARIANTS)
        assert cache.write_variants_cache(path, VARIANTS[:1])

        cached = cache.read_variants_cache(path)
        assert cached is not None
        assert [variant.id for variant in cached] == ["combo1"]
        assert [p.name for p in tmp_path.iterdir()] == [path.name]

    def test_failed_write_leaves_the_existing_file(self, tmp_path, monkeypatch):
        cache = SpellbookCache(cache_dir=tmp_path)
        path = cache.variants_cache_path(4, 100)
        cache.write_variants_cache(path, VARIANTS)

        def fail_after_partial_write(data, f, **kwargs):
            f.write("[{")
            raise OSError("disk full")

        monkeypatch.setattr(json, "dump", fail_after_partial_write)
        written = cache.write_variants_cache(path, VARIANTS[:1])
        monkeypatch.undo()

        assert not written
        cached = cache.read_variants_cache(path)
        assert cached is not None
        assert [variant.id for variant in cached] == ["combo1", "combo2"]


class TestPrecache:
    @pytest.mark.asyncio
    async def test_fills_all_three_caches(self, tmp_path):
        result = await run_precache(tmp_path, FakeSpellbook(VARIANTS), healthy_session())

        assert result == PrecacheResult(variants=2, variants_cached=True, cards=3)
        assert result.complete
        with open(tmp_path / "variants_cards4_max100.json", encoding="utf-8") as f:
            assert [v["id"] for v in json.load(f)] == ["combo1", "combo2"]
        assert read_cache_file(tmp_path) == {CREATURE_URL: ["Creature X"]}
        assert read_colors(tmp_path) == {"Card A": "W", "Card B": "U", "Creature X": "G"}

    @pytest.mark.asyncio
    async def test_cache_file_name_follows_the_key_arguments(self, tmp_path):
        await precache(
            max_cards_in_combo=3,
            max_variants=1,
            cache_dir=tmp_path,
            spellbook=cast(CommanderSpellbook, FakeSpellbook(VARIANTS)),
            session=cast(aiohttp.ClientSession, healthy_session()),
            sleep=SleepRecorder(),
        )

        with open(tmp_path / "variants_cards3_max1.json", encoding="utf-8") as f:
            assert [v["id"] for v in json.load(f)] == ["combo1"]

    @pytest.mark.asyncio
    async def test_keep_existing_makes_no_request_for_cached_data(self, tmp_path):
        await run_precache(tmp_path, FakeSpellbook(VARIANTS), healthy_session())

        spellbook = FakeSpellbook(VARIANTS)
        session = FakeSession()  # any request fails the test
        result = await run_precache(tmp_path, spellbook, session, keep_existing=True)

        assert result.complete
        assert spellbook.calls == 0
        assert session.requests == []

    @pytest.mark.asyncio
    async def test_existing_cache_is_overwritten(self, tmp_path):
        await run_precache(tmp_path, FakeSpellbook(VARIANTS), healthy_session())

        spellbook = FakeSpellbook([VARIANTS[1], make_variant("combo3", ["Card C"])])
        session = FakeSession(
            {
                CREATURE_URL: [FakeResponse(200, card_names=["Creature Y"])],
                COLLECTION_URL: [
                    colors_response({"Card A": ["B"], "Card C": [], "Creature Y": ["R"]})
                ],
            }
        )
        result = await run_precache(tmp_path, spellbook, session)

        assert result.complete
        assert spellbook.calls == 1
        with open(tmp_path / "variants_cards4_max100.json", encoding="utf-8") as f:
            assert [v["id"] for v in json.load(f)] == ["combo2", "combo3"]
        assert read_cache_file(tmp_path) == {CREATURE_URL: ["Creature Y"]}
        assert read_colors(tmp_path) == {
            "Card A": "B",
            "Card C": "",
            "Creature Y": "R",
            # Entries this run did not ask for are kept
            "Card B": "U",
            "Creature X": "G",
        }

    @pytest.mark.asyncio
    async def test_failed_download_leaves_the_existing_variants_file(self, tmp_path):
        await run_precache(tmp_path, FakeSpellbook(VARIANTS), healthy_session())

        spellbook = FakeSpellbook(VARIANTS[:1], errors=[aiohttp.ClientConnectionError("down")])
        result = await run_precache(tmp_path, spellbook, FakeSession(), max_passes=1)

        assert not result.complete
        with open(tmp_path / "variants_cards4_max100.json", encoding="utf-8") as f:
            assert [v["id"] for v in json.load(f)] == ["combo1", "combo2"]

    @pytest.mark.asyncio
    async def test_failed_variants_write_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(SpellbookCache, "write_variants_cache", lambda *args: False)

        result = await run_precache(tmp_path, FakeSpellbook(VARIANTS), healthy_session())

        assert result.variants == 2
        assert not result.variants_cached
        assert not result.complete

    @pytest.mark.asyncio
    async def test_blocklist_decides_what_is_fetched(self, tmp_path):
        session = FakeSession(
            {
                CREATURE_URL: [FakeResponse(200, card_names=["Creature X"])],
                COLLECTION_URL: [colors_response({"Card A": ["W"], "Creature X": ["G"]})],
            }
        )

        result = await run_precache(
            tmp_path, FakeSpellbook(VARIANTS), session, blocklist=frozenset({"Card B"})
        )

        # combo1 needs the blocked card, so only combo2's template and cards are looked up
        assert result.complete
        assert result.cards == 2
        assert session.requests == [CREATURE_URL, COLLECTION_URL]
        assert session.bodies == [{"identifiers": [{"name": "Card A"}, {"name": "Creature X"}]}]

    @pytest.mark.asyncio
    async def test_blocklist_queries_are_resolved_and_block_their_cards(self, tmp_path):
        stickers_url = PayoffFetcher.search_url("t:stickers")
        session = FakeSession(
            {
                stickers_url: [FakeResponse(200, card_names=["Card B"])],
                CREATURE_URL: [FakeResponse(200, card_names=["Creature X"])],
                COLLECTION_URL: [colors_response({"Card A": ["W"], "Creature X": ["G"]})],
            }
        )

        result = await run_precache(
            tmp_path, FakeSpellbook(VARIANTS), session, blocklist_queries=["t:stickers"]
        )

        # The query blocks Card B, so combo1 is dropped and Card B is not looked up
        assert result.complete
        assert result.cards == 2
        assert session.requests == [stickers_url, CREATURE_URL, COLLECTION_URL]
        with open(tmp_path / PayoffFetcher.CACHE_FILENAME, encoding="utf-8") as f:
            assert json.load(f)["queries"][stickers_url]["cards"] == ["Card B"]

    @pytest.mark.asyncio
    async def test_rejected_blocklist_query_stops_the_run(self, tmp_path):
        session = FakeSession({PayoffFetcher.search_url("t:nonsense"): [FakeResponse(400)]})

        with pytest.raises(BlocklistQueryError, match="t:nonsense"):
            await run_precache(
                tmp_path, FakeSpellbook(VARIANTS), session, blocklist_queries=["t:nonsense"]
            )

    @pytest.mark.asyncio
    async def test_spellbook_network_error_is_retried_with_growing_wait(self, tmp_path):
        errors: list[Exception] = [aiohttp.ClientConnectionError("down"), TimeoutError()]
        spellbook = FakeSpellbook(VARIANTS, errors=errors)
        sleep = SleepRecorder()

        result = await run_precache(tmp_path, spellbook, healthy_session(), sleep=sleep)

        assert result.complete
        assert spellbook.calls == 3
        assert [delay for delay in sleep.delays if delay >= 30] == [30.0, 60.0]

    @pytest.mark.asyncio
    async def test_spellbook_failure_on_every_pass_stops_the_run(self, tmp_path):
        spellbook = FakeSpellbook(VARIANTS, errors=[aiohttp.ClientConnectionError("down")] * 2)
        session = FakeSession()

        result = await run_precache(tmp_path, spellbook, session, max_passes=2)

        assert not result.complete
        assert result == PrecacheResult()
        assert spellbook.calls == 2
        assert session.requests == []
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.asyncio
    async def test_spellbook_client_error_is_not_retried(self, tmp_path):
        error = aiohttp.ClientResponseError(MagicMock(), (), status=400)
        spellbook = FakeSpellbook(VARIANTS, errors=[error])

        with pytest.raises(aiohttp.ClientResponseError):
            await run_precache(tmp_path, spellbook, FakeSession())

        assert spellbook.calls == 1

    @pytest.mark.asyncio
    async def test_failed_template_is_fetched_in_a_later_pass(self, tmp_path):
        session = FakeSession(
            {
                CREATURE_URL: [
                    *[FakeResponse(503)] * SCRYFALL_MAX_ATTEMPTS,
                    FakeResponse(200, card_names=["Creature X"]),
                ],
                COLLECTION_URL: [ALL_COLORS],
            }
        )
        sleep = SleepRecorder()

        result = await run_precache(tmp_path, FakeSpellbook(VARIANTS), session, sleep=sleep)

        assert result.complete
        assert result.cards == 3
        assert session.requests.count(CREATURE_URL) == SCRYFALL_MAX_ATTEMPTS + 1
        assert 30.0 in sleep.delays
        assert read_cache_file(tmp_path) == {CREATURE_URL: ["Creature X"]}

    @pytest.mark.asyncio
    async def test_template_failing_on_every_pass_is_reported(self, tmp_path):
        session = FakeSession(
            {
                CREATURE_URL: [FakeResponse(503)],
                COLLECTION_URL: [colors_response({"Card A": ["W"], "Card B": ["U"]})],
            }
        )

        result = await run_precache(tmp_path, FakeSpellbook(VARIANTS), session, max_passes=2)

        assert not result.complete
        assert result.failed_templates == 1
        assert session.requests.count(CREATURE_URL) == 2 * SCRYFALL_MAX_ATTEMPTS
        # What could be fetched is still cached
        assert read_colors(tmp_path) == {"Card A": "W", "Card B": "U"}

    @pytest.mark.asyncio
    async def test_failed_color_request_is_fetched_in_a_later_pass(self, tmp_path):
        session = FakeSession(
            {
                CREATURE_URL: [FakeResponse(200, card_names=["Creature X"])],
                COLLECTION_URL: [*[FakeResponse(503)] * SCRYFALL_MAX_ATTEMPTS, ALL_COLORS],
            }
        )

        result = await run_precache(tmp_path, FakeSpellbook(VARIANTS), session)

        assert result.complete
        assert result.cards_without_attributes == 0
        assert read_colors(tmp_path) == {"Card A": "W", "Card B": "U", "Creature X": "G"}

    @pytest.mark.asyncio
    async def test_color_request_failing_on_every_pass_is_reported(self, tmp_path):
        session = FakeSession(
            {
                CREATURE_URL: [FakeResponse(200, card_names=["Creature X"])],
                COLLECTION_URL: [FakeResponse(503)],
            }
        )

        result = await run_precache(tmp_path, FakeSpellbook(VARIANTS), session, max_passes=2)

        assert not result.complete
        assert result.failed_attribute_requests == 1
        assert result.cards_without_attributes == 3

    @pytest.mark.asyncio
    async def test_payoff_queries_are_resolved_and_their_cards_looked_up(self, tmp_path):
        session = FakeSession(
            {
                CREATURE_URL: [FakeResponse(200, card_names=["Creature X"])],
                STORM_URL: [FakeResponse(200, card_names=["Grapeshot", "Card B"])],
                COLLECTION_URL: [
                    colors_response(
                        {
                            "Card A": ["W"],
                            "Card B": ["U"],
                            "Creature X": ["G"],
                            "Grapeshot": ["R"],
                            "Aetherflux": [],
                        }
                    )
                ],
            }
        )

        result = await run_precache(tmp_path, FakeSpellbook(VARIANTS), session, payoffs=PAYOFFS)

        assert result.complete
        assert result.payoff_queries == 1
        assert result.failed_payoff_queries == 0
        assert result.bad_payoff_queries == {}
        # The payoff cards (query results and table cards) join the candidates
        assert result.cards == 5
        assert session.bodies == [
            {
                "identifiers": [
                    {"name": name}
                    for name in ["Aetherflux", "Card A", "Card B", "Creature X", "Grapeshot"]
                ]
            }
        ]
        with open(tmp_path / PayoffFetcher.CACHE_FILENAME, encoding="utf-8") as f:
            cache = json.load(f)
        assert cache["queries"][STORM_URL]["cards"] == ["Grapeshot", "Card B"]
        assert read_colors(tmp_path)["Grapeshot"] == "R"

    @pytest.mark.asyncio
    async def test_failed_payoff_query_is_fetched_in_a_later_pass(self, tmp_path):
        session = FakeSession(
            {
                CREATURE_URL: [FakeResponse(200, card_names=["Creature X"])],
                STORM_URL: [
                    *[FakeResponse(503)] * SCRYFALL_MAX_ATTEMPTS,
                    FakeResponse(200, card_names=["Grapeshot"]),
                ],
                COLLECTION_URL: [ALL_COLORS],
            }
        )
        sleep = SleepRecorder()

        result = await run_precache(
            tmp_path, FakeSpellbook(VARIANTS), session, sleep=sleep, payoffs=PAYOFFS
        )

        assert result.complete
        assert session.requests.count(STORM_URL) == SCRYFALL_MAX_ATTEMPTS + 1
        assert 30.0 in sleep.delays

    @pytest.mark.asyncio
    async def test_payoff_query_failing_on_every_pass_is_reported(self, tmp_path):
        session = FakeSession(
            {
                CREATURE_URL: [FakeResponse(200, card_names=["Creature X"])],
                STORM_URL: [FakeResponse(503)],
                COLLECTION_URL: [ALL_COLORS],
            }
        )

        result = await run_precache(
            tmp_path, FakeSpellbook(VARIANTS), session, max_passes=2, payoffs=PAYOFFS
        )

        assert not result.complete
        assert result.failed_payoff_queries == 1
        assert not (tmp_path / PayoffFetcher.CACHE_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_payoff_query_matching_no_card_is_reported(self, tmp_path):
        session = FakeSession(
            {
                CREATURE_URL: [FakeResponse(200, card_names=["Creature X"])],
                STORM_URL: [FakeResponse(404)],
                COLLECTION_URL: [ALL_COLORS],
            }
        )

        result = await run_precache(tmp_path, FakeSpellbook(VARIANTS), session, payoffs=PAYOFFS)

        # Nothing failed, but a build with this table would, so the run is not complete
        assert not result.complete
        assert result.failed_payoff_queries == 0
        assert result.bad_payoff_queries == {STORM_QUERY: "matches no card"}
        assert session.requests.count(STORM_URL) == 1

    @pytest.mark.asyncio
    async def test_rejected_payoff_query_is_a_table_error_not_a_failure(self, tmp_path):
        # Scryfall rejects the query (400): no retry passes, reported like an empty query
        session = FakeSession(
            {
                CREATURE_URL: [FakeResponse(200, card_names=["Creature X"])],
                STORM_URL: [FakeResponse(400)],
                COLLECTION_URL: [ALL_COLORS],
            }
        )
        sleep = SleepRecorder()

        result = await run_precache(
            tmp_path, FakeSpellbook(VARIANTS), session, sleep=sleep, payoffs=PAYOFFS
        )

        assert not result.complete
        assert result.failed_payoff_queries == 0
        assert result.bad_payoff_queries == {STORM_QUERY: "rejected by Scryfall (HTTP 400)"}
        assert session.requests.count(STORM_URL) == 1
        assert not [delay for delay in sleep.delays if delay >= 30]

    @pytest.mark.asyncio
    async def test_card_unknown_to_scryfall_is_not_a_failure(self, tmp_path):
        session = FakeSession(
            {
                CREATURE_URL: [FakeResponse(200, card_names=["Creature X"])],
                COLLECTION_URL: [colors_response({"Card A": ["W"], "Card B": ["U"]})],
            }
        )

        result = await run_precache(tmp_path, FakeSpellbook(VARIANTS), session)

        # One pass only: asking again would not make Scryfall know the card
        assert result.complete
        assert result.cards_without_attributes == 1
        assert session.requests.count(COLLECTION_URL) == 1
