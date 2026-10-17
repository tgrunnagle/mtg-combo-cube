"""Tests for the configuration file: the blocklist, blocklist query, outcome, payoff and
enabler sections."""

from pathlib import Path

import pytest

from mtg_combo_cube.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    CubeConfig,
    load_config,
    parse_blocklist,
    parse_blocklist_queries,
    parse_config,
    parse_fixed_cards_file,
    read_card_list,
)
from mtg_combo_cube.ilp.enablers import EnablerTableError, parse_enabler_table
from mtg_combo_cube.ilp.outcomes import OutcomeCategoryError, parse_outcome_categories
from mtg_combo_cube.ilp.payoffs import PayoffTableError, parse_payoff_table

CONFIG = """\
# A comment
blocklist:
  - Sol Ring
  - "  Demonic Tutor  "
blocklist_queries:
  - t:stickers
outcome_categories:
  mana: [infinite colored mana]
  damage: {patterns: ["re:infinite damage(?! to creatures)"], min_combos: 3}
  other: []
payoffs:
  mana:
    queries: ['o:"{X}" o:"X damage"']
    exclude: [Chromatic Orrery]
enablers:
  ritual:
    queries: ['o:"Add {B}{B}{B}"']
    cards: [Dark Ritual]
    min_enablers: 1
"""


def write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


class TestLoad:
    def test_loads_every_section(self, tmp_path: Path):
        config = load_config(write(tmp_path, CONFIG))

        assert config.blocklist == frozenset({"Sol Ring", "Demonic Tutor"})
        assert config.blocklist_queries == ("t:stickers",)
        assert config.outcome_categories is not None
        assert config.outcome_categories.names == ("mana", "damage", "other")
        assert config.outcome_categories.categories[1].min_combos == 3
        assert config.payoffs is not None
        assert config.payoffs.names == ("mana",)
        assert config.payoffs.queries == ('o:"{X}" o:"X damage"',)
        assert config.enablers is not None
        assert config.enablers.names == ("ritual",)
        assert config.enablers.queries == ('o:"Add {B}{B}{B}"',)
        assert config.enablers.min_enablers == {"ritual": 1}

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Configuration file not found"):
            load_config(str(tmp_path / "missing.yaml"))

    def test_invalid_yaml(self, tmp_path: Path):
        with pytest.raises(ConfigError, match="not valid YAML"):
            load_config(write(tmp_path, "blocklist: [Sol Ring\n"))

    @pytest.mark.parametrize("text", ["", "# only a comment\n"])
    def test_empty_file_has_no_sections(self, tmp_path: Path, text: str):
        assert load_config(write(tmp_path, text)) == CubeConfig()

    def test_every_section_is_optional(self, tmp_path: Path):
        config = load_config(write(tmp_path, "blocklist: [Sol Ring]\n"))

        assert config.blocklist == frozenset({"Sol Ring"})
        assert config.blocklist_queries == ()
        assert config.outcome_categories is None
        assert config.payoffs is None
        assert config.enablers is None

    def test_table_errors_come_from_the_tables(self, tmp_path: Path):
        with pytest.raises(OutcomeCategoryError, match="invalid regex"):
            load_config(write(tmp_path, "outcome_categories: {mana: ['re:(']}\n"))
        with pytest.raises(PayoffTableError, match="no queries, cards or exclusions"):
            load_config(write(tmp_path, "outcome_categories: {mana: [x]}\npayoffs: {mana: {}}\n"))
        with pytest.raises(EnablerTableError, match="enabler category 'ritual' has unknown keys"):
            load_config(write(tmp_path, "enablers: {ritual: {min_payoffs: 1}}\n"))

    def test_enabler_table_needs_no_other_section(self, tmp_path: Path):
        config = load_config(write(tmp_path, "enablers: {ritual: {cards: [Dark Ritual]}}\n"))

        assert config.enablers is not None
        assert config.enablers.names == ("ritual",)
        config.require(enablers=True)


class TestFixedCards:
    def test_read_card_list_skips_comments_blanks_and_repeats(self, tmp_path: Path):
        path = tmp_path / "fixed.txt"
        path.write_text(
            "# mainboard\nSol Ring\n\n  Wasteland  \nSol Ring\n# a comment\nMox Pearl\n",
            encoding="utf-8",
        )

        assert read_card_list(path) == ["Sol Ring", "Wasteland", "Mox Pearl"]
        assert read_card_list(str(path)) == ["Sol Ring", "Wasteland", "Mox Pearl"]

    def test_loaded_from_the_file_the_key_names(self, tmp_path: Path):
        fixed = tmp_path / "fixed.txt"
        fixed.write_text("Sol Ring\nSol Ring\nWasteland\n", encoding="utf-8")

        config = load_config(write(tmp_path, f"fixed_cards: {fixed.as_posix()}\n"))

        assert config.fixed_cards == frozenset({"Sol Ring", "Wasteland"})
        assert config.fixed_cards_file == fixed.as_posix()

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Fixed cards file not found"):
            load_config(write(tmp_path, "fixed_cards: nowhere.txt\n"))

    def test_override_replaces_the_configured_file(self, tmp_path: Path):
        configured = tmp_path / "configured.txt"
        configured.write_text("Sol Ring\n", encoding="utf-8")
        override = tmp_path / "override.txt"
        override.write_text("Wasteland\n", encoding="utf-8")

        config = load_config(
            write(tmp_path, f"fixed_cards: {configured.as_posix()}\n"), str(override)
        )

        assert config.fixed_cards == frozenset({"Wasteland"})
        assert config.fixed_cards_file == str(override)
        # The override works without the key too
        assert load_config(write(tmp_path, ""), str(override)).fixed_cards == {"Wasteland"}

    def test_fixed_card_on_the_blocklist_is_an_error(self, tmp_path: Path):
        fixed = tmp_path / "fixed.txt"
        fixed.write_text("Sol Ring\n", encoding="utf-8")

        with pytest.raises(ConfigError, match=r"cannot be on the blocklist: \['Sol Ring'\]"):
            load_config(
                write(tmp_path, f"fixed_cards: {fixed.as_posix()}\nblocklist: [Sol Ring]\n")
            )
        with pytest.raises(ConfigError, match="cannot be on the blocklist"):
            CubeConfig(fixed_cards=frozenset({"A"}), blocklist=frozenset({"A"}))

    @pytest.mark.parametrize("entry", ["", "  ", 1, ["a.txt"], {"path": "a.txt"}])
    def test_invalid_entries_are_rejected(self, entry: object):
        with pytest.raises(ConfigError, match="path of a card list file"):
            parse_fixed_cards_file(entry)

    def test_parse_keeps_the_path_only(self):
        config = parse_config({"fixed_cards": " data/fixed.txt "})

        assert config.fixed_cards_file == "data/fixed.txt"
        assert config.fixed_cards == frozenset()


class TestParse:
    @pytest.mark.parametrize(
        ("data", "message"),
        [
            ([], "must be a mapping"),
            ("blocklist", "must be a mapping"),
            ({"blocklist": [], "payoff": {}}, r"unknown sections \['payoff'\]"),
        ],
    )
    def test_invalid_documents_are_rejected(self, data: object, message: str):
        with pytest.raises(ConfigError, match=message):
            parse_config(data)

    def test_none_is_an_empty_configuration(self):
        assert parse_config(None) == CubeConfig()

    def test_blocklist_is_stripped_and_deduplicated(self):
        assert parse_blocklist(["Sol Ring", " Sol Ring ", "Mana Crypt"]) == frozenset(
            {"Sol Ring", "Mana Crypt"}
        )
        assert parse_blocklist([]) == frozenset()

    @pytest.mark.parametrize("entries", ["Sol Ring", ["Sol Ring", ""], ["  "], [1], {"a": 1}])
    def test_invalid_blocklists_are_rejected(self, entries: object):
        with pytest.raises(ConfigError, match="list of card names"):
            parse_blocklist(entries)

    def test_blocklist_queries_are_stripped_and_deduplicated_in_order(self):
        assert parse_blocklist_queries(["t:stickers", " t:attraction ", "t:stickers"]) == (
            "t:stickers",
            "t:attraction",
        )
        assert parse_blocklist_queries([]) == ()

    @pytest.mark.parametrize("entries", ["t:stickers", ["t:stickers", ""], [1], {"a": 1}])
    def test_invalid_blocklist_queries_are_rejected(self, entries: object):
        with pytest.raises(ConfigError, match="list of Scryfall queries"):
            parse_blocklist_queries(entries)


class TestConsistency:
    OUTCOMES = parse_outcome_categories({"mana": ["infinite mana"], "damage": ["x"]})

    def test_payoff_table_needs_the_outcome_table(self):
        with pytest.raises(PayoffTableError, match="needs the outcome category table"):
            CubeConfig(payoffs=parse_payoff_table({"mana": {"cards": ["A"]}}))

    def test_payoff_categories_must_be_outcome_categories(self):
        with pytest.raises(PayoffTableError, match=r"\['storm'\] are not in the outcome"):
            CubeConfig(
                outcome_categories=self.OUTCOMES,
                payoffs=parse_payoff_table({"storm": {"cards": ["Grapeshot"]}}),
            )

    def test_fitting_tables(self):
        payoffs = parse_payoff_table({"mana": {"cards": ["A"]}})
        config = CubeConfig(outcome_categories=self.OUTCOMES, payoffs=payoffs)

        assert config.payoffs == payoffs


class TestRequire:
    def test_present_sections_pass(self):
        config = CubeConfig(
            outcome_categories=parse_outcome_categories({"mana": ["x"]}),
            payoffs=parse_payoff_table({"mana": {"cards": ["A"]}}),
            enablers=parse_enabler_table({"ritual": {"cards": ["B"]}}),
        )

        config.require(outcome_categories=True, payoffs=True, enablers=True)

    def test_nothing_required_passes_without_tables(self):
        CubeConfig().require()
        CubeConfig().require(outcome_categories=False, payoffs=False, enablers=False)

    @pytest.mark.parametrize(
        ("outcomes", "payoffs", "enablers", "message"),
        [
            (True, False, False, "no outcome_categories section"),
            (False, True, False, "no payoffs section"),
            (False, False, True, "no enablers section"),
            (True, True, False, "no outcome_categories or payoffs section"),
            (True, True, True, "no outcome_categories or payoffs or enablers section"),
        ],
    )
    def test_missing_sections_are_named(
        self, outcomes: bool, payoffs: bool, enablers: bool, message: str
    ):
        with pytest.raises(ConfigError, match=message):
            CubeConfig().require(outcome_categories=outcomes, payoffs=payoffs, enablers=enablers)


class TestDefaultConfig:
    def test_default_config_ships_with_the_repository(self):
        assert Path(DEFAULT_CONFIG_PATH).exists()
        config = load_config()

        assert {"Command Tower", "Arcane Signet", "Jeweled Lotus"} <= config.blocklist
        # The tracked fixed cards file: the reserved slots, lands twice in the export
        assert config.fixed_cards_file == "data/fixed_cards.txt"
        assert {"Mox Pearl", "Wasteland", "Wooded Foothills"} <= config.fixed_cards
        assert "Sol Ring" not in config.fixed_cards  # a required enabler of the 310 instead
        assert len(config.fixed_cards) == 70
        assert config.outcome_categories is not None
        assert config.outcome_categories.catch_all == "other"
        assert config.payoffs is not None
        assert config.enablers is not None
        config.require(outcome_categories=True, payoffs=True, enablers=True)
