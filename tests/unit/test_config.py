"""Tests for the configuration file: the blocklist, outcome and payoff sections."""

from pathlib import Path

import pytest

from mtg_combo_cube.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    CubeConfig,
    load_config,
    parse_blocklist,
    parse_config,
)
from mtg_combo_cube.ilp.outcomes import OutcomeCategoryError, parse_outcome_categories
from mtg_combo_cube.ilp.payoffs import PayoffTableError, parse_payoff_table

CONFIG = """\
# A comment
blocklist:
  - Sol Ring
  - "  Demonic Tutor  "
outcome_categories:
  mana: [infinite colored mana]
  damage: {patterns: ["re:infinite damage(?! to creatures)"], min_combos: 3}
  other: []
payoffs:
  mana:
    queries: ['o:"{X}" o:"X damage"']
    exclude: [Chromatic Orrery]
"""


def write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


class TestLoad:
    def test_loads_every_section(self, tmp_path: Path):
        config = load_config(write(tmp_path, CONFIG))

        assert config.blocklist == frozenset({"Sol Ring", "Demonic Tutor"})
        assert config.outcome_categories is not None
        assert config.outcome_categories.names == ("mana", "damage", "other")
        assert config.outcome_categories.categories[1].min_combos == 3
        assert config.payoffs is not None
        assert config.payoffs.names == ("mana",)
        assert config.payoffs.queries == ('o:"{X}" o:"X damage"',)

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
        assert config.outcome_categories is None
        assert config.payoffs is None

    def test_table_errors_come_from_the_tables(self, tmp_path: Path):
        with pytest.raises(OutcomeCategoryError, match="invalid regex"):
            load_config(write(tmp_path, "outcome_categories: {mana: ['re:(']}\n"))
        with pytest.raises(PayoffTableError, match="no queries, cards or exclusions"):
            load_config(write(tmp_path, "outcome_categories: {mana: [x]}\npayoffs: {mana: {}}\n"))


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
        )

        config.require(outcome_categories=True, payoffs=True)

    def test_nothing_required_passes_without_tables(self):
        CubeConfig().require()
        CubeConfig().require(outcome_categories=False, payoffs=False)

    @pytest.mark.parametrize(
        ("outcomes", "payoffs", "message"),
        [
            (True, False, "no outcome_categories section"),
            (False, True, "no payoffs section"),
            (True, True, "no outcome_categories or payoffs section"),
        ],
    )
    def test_missing_sections_are_named(self, outcomes: bool, payoffs: bool, message: str):
        with pytest.raises(ConfigError, match=message):
            CubeConfig().require(outcome_categories=outcomes, payoffs=payoffs)


class TestDefaultConfig:
    def test_default_config_ships_with_the_repository(self):
        assert Path(DEFAULT_CONFIG_PATH).exists()
        config = load_config()

        assert {"Command Tower", "Arcane Signet", "Jeweled Lotus"} <= config.blocklist
        assert config.outcome_categories is not None
        assert config.outcome_categories.catch_all == "other"
        assert config.payoffs is not None
        config.require(outcome_categories=True, payoffs=True)
