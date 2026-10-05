"""Unit tests for requirement normalization."""

from mtg_combo_cube.ilp.requirement_normalizer import (
    compute_requirement_group_key,
    normalize_scryfall_url,
    normalize_template_name,
    prepare_scryfall_url,
)


class TestNormalizeScryfallUrl:
    """Test Scryfall URL normalization."""

    def test_basic_query(self):
        """Test basic query normalization."""
        url = "https://api.scryfall.com/cards/search?q=type:creature+keyword:haste"
        result = normalize_scryfall_url(url)
        assert "q=" in result
        assert "keyword:haste" in result
        assert "type:creature" in result

    def test_removes_order_param(self):
        """Test that order parameter is removed."""
        url1 = "https://api.scryfall.com/cards/search?q=type:creature&order=edhrec"
        url2 = "https://api.scryfall.com/cards/search?q=type:creature&order=name"
        assert normalize_scryfall_url(url1) == normalize_scryfall_url(url2)

    def test_removes_format_param(self):
        """Test that format parameter is removed."""
        url1 = "https://api.scryfall.com/cards/search?q=type:creature&format=json"
        url2 = "https://api.scryfall.com/cards/search?q=type:creature"
        assert normalize_scryfall_url(url1) == normalize_scryfall_url(url2)

    def test_sorts_query_terms(self):
        """Test that query terms are sorted for canonical representation."""
        url1 = "https://api.scryfall.com/cards/search?q=keyword:haste+type:creature"
        url2 = "https://api.scryfall.com/cards/search?q=type:creature+keyword:haste"
        assert normalize_scryfall_url(url1) == normalize_scryfall_url(url2)

    def test_removes_legal_commander(self):
        """Test that legal:commander filter is removed."""
        url1 = "https://api.scryfall.com/cards/search?q=type:creature+legal:commander"
        url2 = "https://api.scryfall.com/cards/search?q=type:creature"
        assert normalize_scryfall_url(url1) == normalize_scryfall_url(url2)

    def test_handles_url_encoded_spaces(self):
        """Test handling of URL-encoded spaces."""
        url1 = "https://api.scryfall.com/cards/search?q=type:creature+keyword:persist"
        url2 = "https://api.scryfall.com/cards/search?q=type:creature%20keyword:persist"
        assert normalize_scryfall_url(url1) == normalize_scryfall_url(url2)

    def test_empty_query(self):
        """Test handling of empty query string."""
        url = "https://api.scryfall.com/cards/search"
        result = normalize_scryfall_url(url)
        assert result == ""

    def test_preserves_non_noise_params(self):
        """Test that non-noise parameters are preserved."""
        url = "https://api.scryfall.com/cards/search?q=type:creature&custom=value"
        result = normalize_scryfall_url(url)
        assert "custom=value" in result


class TestNormalizeTemplateName:
    """Test template name normalization."""

    def test_lowercase(self):
        """Test lowercase conversion."""
        assert normalize_template_name("Persist Creature") == "persist creature"

    def test_removes_special_chars(self):
        """Test removal of special characters."""
        assert normalize_template_name("Creature (Green)") == "creature green"
        assert normalize_template_name('Card with "quotes"') == "card with quotes"

    def test_normalizes_whitespace(self):
        """Test whitespace normalization."""
        assert normalize_template_name("  Persist   Creature  ") == "persist creature"

    def test_removes_punctuation(self):
        """Test removal of punctuation."""
        assert normalize_template_name("Sac-outlet!") == "sacoutlet"

    def test_preserves_numbers(self):
        """Test that numbers are preserved."""
        assert normalize_template_name("2-card combo") == "2card combo"

    def test_empty_string(self):
        """Test handling of empty string."""
        assert normalize_template_name("") == ""


class TestComputeRequirementGroupKey:
    """Test group key computation."""

    def test_prefers_scryfall_url(self):
        """Test that Scryfall URL is preferred over name."""
        key = compute_requirement_group_key(
            "https://api.scryfall.com/cards/search?q=type:creature",
            "Some Creature",
        )
        assert key.startswith("scryfall:")

    def test_falls_back_to_name(self):
        """Test fallback to name when no URL is provided."""
        key = compute_requirement_group_key(None, "Some Creature")
        assert key.startswith("name:")
        assert "some creature" in key

    def test_same_query_different_names_produce_same_key(self):
        """Test that same query with different names produces same key."""
        key1 = compute_requirement_group_key(
            "https://api.scryfall.com/cards/search?q=keyword:persist",
            "Persist Creature",
        )
        key2 = compute_requirement_group_key(
            "https://api.scryfall.com/cards/search?q=keyword:persist",
            "Green Persist Creature",
        )
        assert key1 == key2

    def test_different_queries_produce_different_keys(self):
        """Test that different queries produce different keys."""
        key1 = compute_requirement_group_key(
            "https://api.scryfall.com/cards/search?q=keyword:persist",
            "Persist Creature",
        )
        key2 = compute_requirement_group_key(
            "https://api.scryfall.com/cards/search?q=keyword:undying",
            "Undying Creature",
        )
        assert key1 != key2

    def test_name_fallback_different_names_produce_different_keys(self):
        """Test that different names produce different keys when no URL."""
        key1 = compute_requirement_group_key(None, "Sac outlet")
        key2 = compute_requirement_group_key(None, "Mana dork")
        assert key1 != key2

    def test_name_fallback_similar_names_produce_same_key(self):
        """Test that similar normalized names produce same key."""
        key1 = compute_requirement_group_key(None, "Sac Outlet")
        key2 = compute_requirement_group_key(None, "sac outlet")
        assert key1 == key2


class TestPrepareScryfallUrl:
    """Test Scryfall URL preparation for fetching."""

    def test_adds_order_edhrec_when_missing(self):
        """Test that order=edhrec is added when not present."""
        url = "https://api.scryfall.com/cards/search?q=type:creature"
        result = prepare_scryfall_url(url)
        assert "order=edhrec" in result

    def test_preserves_order_edhrec_when_present(self):
        """Test that order=edhrec is preserved when already present."""
        url = "https://api.scryfall.com/cards/search?q=type:creature&order=edhrec"
        result = prepare_scryfall_url(url)
        assert result.count("order=edhrec") == 1

    def test_replaces_other_order_with_edhrec(self):
        """Test that other order values are replaced with edhrec."""
        url = "https://api.scryfall.com/cards/search?q=type:creature&order=name"
        result = prepare_scryfall_url(url)
        assert "order=edhrec" in result
        assert "order=name" not in result

    def test_removes_legal_commander_encoded(self):
        """Test that +legal%3Acommander is removed."""
        url = "https://api.scryfall.com/cards/search?q=type:creature+legal%3Acommander"
        result = prepare_scryfall_url(url)
        assert "legal%3Acommander" not in result
        assert "+legal%3Acommander" not in result

    def test_removes_legal_commander_standalone(self):
        """Test that standalone legal%3Acommander is removed."""
        url = "https://api.scryfall.com/cards/search?q=legal%3Acommander+type:creature"
        result = prepare_scryfall_url(url)
        assert "legal%3Acommander" not in result

    def test_preserves_query_params(self):
        """Test that query parameters are preserved."""
        url = "https://api.scryfall.com/cards/search?q=keyword:persist"
        result = prepare_scryfall_url(url)
        assert "q=" in result
        assert "persist" in result

    def test_returns_valid_url(self):
        """Test that result is a valid URL."""
        url = "https://api.scryfall.com/cards/search?q=type:creature"
        result = prepare_scryfall_url(url)
        assert result.startswith("https://api.scryfall.com")
