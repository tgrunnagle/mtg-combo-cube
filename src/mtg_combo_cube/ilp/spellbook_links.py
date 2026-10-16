"""
Links to Commander Spellbook pages that show a cube's combos.

A search link lists variants by id (`sid:<id> OR sid:<id> ...`). Spellbook's search accepts
at most 20 query nodes and an "OR" counts as one, so one search can list ten ids; a longer
list takes several links. The Find My Combos page takes a whole deck list in its URL and
shows every combo the list completes.
"""

from collections.abc import Collection, Sequence
from urllib.parse import quote, urlencode

SPELLBOOK_URL = "https://commanderspellbook.com"
SEARCH_URL_IDS = 10  # variant ids per search link, the most one Spellbook search accepts


def search_url(variant_ids: Sequence[str]) -> str:
    """A Spellbook search showing the given variants (1 to SEARCH_URL_IDS of them)."""
    if not 0 < len(variant_ids) <= SEARCH_URL_IDS:
        raise ValueError(
            f"a search link shows 1 to {SEARCH_URL_IDS} variants, not {len(variant_ids)}"
        )
    query = " OR ".join(f"sid:{variant_id}" for variant_id in variant_ids)
    return f"{SPELLBOOK_URL}/search/?{urlencode({'q': query}, quote_via=quote)}"


def search_urls(variant_ids: Sequence[str]) -> list[str]:
    """Spellbook searches showing the given variants, SEARCH_URL_IDS per link, in order."""
    return [
        search_url(variant_ids[start : start + SEARCH_URL_IDS])
        for start in range(0, len(variant_ids), SEARCH_URL_IDS)
    ]


def find_my_combos_url(cards: Collection[str]) -> str:
    """The Spellbook Find My Combos page for a card list: every combo the list completes."""
    decklist = "\n".join(cards)
    return f"{SPELLBOOK_URL}/find-my-combos/?{urlencode({'decklist': decklist}, quote_via=quote)}"
