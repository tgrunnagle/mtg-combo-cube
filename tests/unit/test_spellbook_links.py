"""Tests for the Commander Spellbook links of a cube's combos."""

import pytest

from mtg_combo_cube.ilp.spellbook_links import (
    SEARCH_URL_IDS,
    find_my_combos_url,
    search_url,
    search_urls,
)


class TestSearchUrl:
    def test_lists_the_variants_by_id(self):
        assert search_url(["1580-2034-4871", "628-2034--5"]) == (
            "https://commanderspellbook.com/search/?q=sid%3A1580-2034-4871%20OR%20sid%3A628-2034--5"
        )

    def test_rejects_an_empty_or_too_long_list(self):
        with pytest.raises(ValueError, match="1 to 10 variants, not 0"):
            search_url([])
        with pytest.raises(ValueError, match="1 to 10 variants, not 11"):
            search_url([str(i) for i in range(11)])

    def test_search_urls_splits_ten_per_link_in_order(self):
        assert search_urls([]) == []
        ids = [str(i) for i in range(SEARCH_URL_IDS + 1)]
        urls = search_urls(ids)
        assert urls == [search_url(ids[:SEARCH_URL_IDS]), search_url(ids[SEARCH_URL_IDS:])]
        assert urls[1].endswith("q=sid%3A10")


class TestFindMyCombosUrl:
    def test_puts_the_card_list_in_the_url(self):
        url = find_my_combos_url(["Ashnod's Altar", "Zulaport Cutthroat"])
        assert url == (
            "https://commanderspellbook.com/find-my-combos/"
            "?decklist=Ashnod%27s%20Altar%0AZulaport%20Cutthroat"
        )
