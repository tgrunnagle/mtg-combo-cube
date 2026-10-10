"""
The cube configuration: one YAML file with the blocklist, the outcome category table and the
payoff table.

    blocklist:
      - Command Tower
    outcome_categories:
      mana: ["infinite colored mana", "infinite colorless mana"]
      damage: {patterns: ["infinite damage"], min_combos: 40}
      other: []
    payoffs:
      mana:
        queries: ['o:"{X}" o:"X damage" -t:land']
        exclude: [Chromatic Orrery]

Every section is optional: without a blocklist no card is blocked, and without an outcome or
payoff table the run goes on without the statistics and rules that need it (a rule that is
on needs its table; see CubeConfig.require). The sections are described in
ilp/outcomes.py and ilp/payoffs.py. Every payoff category must be a category of the outcome
table, so a payoff table needs the outcome table beside it.

The default file is config.yaml in the working directory, which is the repository root for
every build.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from mtg_combo_cube.ilp.outcomes import OutcomeCategories, parse_outcome_categories
from mtg_combo_cube.ilp.payoffs import PayoffDefinitions, PayoffTableError, parse_payoff_table

DEFAULT_CONFIG_PATH = "config.yaml"
SECTIONS = ("blocklist", "outcome_categories", "payoffs")


class ConfigError(ValueError):
    """An invalid configuration file, or one without a section a run needs."""


@dataclass(frozen=True)
class CubeConfig:
    """The configuration of a run; a payoff table is checked against the outcome table."""

    blocklist: frozenset[str] = frozenset()
    outcome_categories: OutcomeCategories | None = None
    payoffs: PayoffDefinitions | None = None

    def __post_init__(self) -> None:
        if self.payoffs is None:
            return
        if self.outcome_categories is None:
            raise PayoffTableError(
                "the payoff table needs the outcome category table to place its categories"
            )
        self.payoffs.check_categories(self.outcome_categories)

    def require(self, *, outcome_categories: bool = False, payoffs: bool = False) -> None:
        """Raise ConfigError when a section a run needs is missing."""
        missing = [
            name
            for name, needed, present in (
                ("outcome_categories", outcome_categories, self.outcome_categories),
                ("payoffs", payoffs, self.payoffs),
            )
            if needed and present is None
        ]
        if missing:
            raise ConfigError(
                f"the configuration has no {' or '.join(missing)} section, which the rules "
                "that are on need (--min-outcome-combos, --max-outcome-share, --min-payoffs)"
            )


def parse_blocklist(entries: object) -> frozenset[str]:
    """The blocked card names from the parsed `blocklist` section (a list of names)."""
    if not isinstance(entries, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in entries
    ):
        raise ConfigError("the blocklist must be a list of card names")
    return frozenset(str(entry).strip() for entry in entries)


def parse_config(data: object) -> CubeConfig:
    """Build the configuration from the parsed YAML document (see the module docstring)."""
    if data is None:
        return CubeConfig()
    if not isinstance(data, dict):
        raise ConfigError("the configuration must be a mapping of section name to section")
    sections: dict[str, object] = {str(key): value for key, value in data.items()}
    unknown = sorted(set(sections) - set(SECTIONS))
    if unknown:
        raise ConfigError(
            f"the configuration has unknown sections {unknown} (expected {', '.join(SECTIONS)})"
        )
    blocklist = sections.get("blocklist")
    outcomes = sections.get("outcome_categories")
    payoffs = sections.get("payoffs")
    return CubeConfig(
        blocklist=frozenset() if blocklist is None else parse_blocklist(blocklist),
        outcome_categories=None if outcomes is None else parse_outcome_categories(outcomes),
        payoffs=None if payoffs is None else parse_payoff_table(payoffs),
    )


def load_config(path: str | None = None) -> CubeConfig:
    """
    Load the configuration from a YAML file (config.yaml in the working directory when no
    path is given).

    Raises FileNotFoundError when the file does not exist, ConfigError when it is not valid
    YAML or has unknown sections, and OutcomeCategoryError or PayoffTableError when a table
    is invalid.
    """
    config_path = Path(path) if path else Path(DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"{config_path} is not valid YAML: {e}") from e
    return parse_config(data)
