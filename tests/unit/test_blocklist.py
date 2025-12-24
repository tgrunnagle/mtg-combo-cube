"""Unit tests for blocklist functionality."""

import tempfile
from pathlib import Path

import pytest

from mtg_combo_cube.blocklist import DEFAULT_BLOCKLIST_PATH, load_blocklist


class TestLoadBlocklist:
    """Tests for load_blocklist function."""

    def test_load_blocklist_with_cards(self):
        """Test loading a blocklist with card names."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("Sol Ring\n")
            f.write("Mana Crypt\n")
            f.write("  Demonic Tutor  \n")  # Test whitespace stripping
            temp_path = f.name

        try:
            blocklist = load_blocklist(temp_path)
            assert blocklist == frozenset({"Sol Ring", "Mana Crypt", "Demonic Tutor"})
        finally:
            Path(temp_path).unlink()

    def test_load_blocklist_ignores_empty_lines(self):
        """Test that empty lines are ignored."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("Sol Ring\n")
            f.write("\n")
            f.write("   \n")
            f.write("Mana Crypt\n")
            temp_path = f.name

        try:
            blocklist = load_blocklist(temp_path)
            assert blocklist == frozenset({"Sol Ring", "Mana Crypt"})
        finally:
            Path(temp_path).unlink()

    def test_load_blocklist_ignores_comments(self):
        """Test that lines starting with # are ignored."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("# This is a comment\n")
            f.write("Sol Ring\n")
            f.write("  # Indented comment\n")
            f.write("Mana Crypt\n")
            temp_path = f.name

        try:
            blocklist = load_blocklist(temp_path)
            assert blocklist == frozenset({"Sol Ring", "Mana Crypt"})
        finally:
            Path(temp_path).unlink()

    def test_load_blocklist_empty_file(self):
        """Test loading an empty blocklist file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("")
            temp_path = f.name

        try:
            blocklist = load_blocklist(temp_path)
            assert blocklist == frozenset()
        finally:
            Path(temp_path).unlink()

    def test_load_blocklist_only_comments(self):
        """Test loading a blocklist with only comments."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("# Comment 1\n")
            f.write("# Comment 2\n")
            temp_path = f.name

        try:
            blocklist = load_blocklist(temp_path)
            assert blocklist == frozenset()
        finally:
            Path(temp_path).unlink()

    def test_load_blocklist_missing_explicit_path_raises(self):
        """Test that missing explicit path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_blocklist("/nonexistent/path/blocklist.txt")
        assert "Blocklist file not found" in str(exc_info.value)

    def test_load_blocklist_returns_frozenset(self):
        """Test that load_blocklist returns a frozenset (immutable)."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("Sol Ring\n")
            temp_path = f.name

        try:
            blocklist = load_blocklist(temp_path)
            assert isinstance(blocklist, frozenset)
        finally:
            Path(temp_path).unlink()

    def test_default_blocklist_path_constant(self):
        """Test that DEFAULT_BLOCKLIST_PATH is set correctly."""
        assert DEFAULT_BLOCKLIST_PATH == "data/blocklist.txt"
