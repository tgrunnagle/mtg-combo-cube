"""
The cube configuration: one YAML file with the fixed cards, the blocklist, the blocklist
queries, the outcome category table, the payoff table and the enabler table.

    fixed_cards: data/fixed_cards.txt
    blocklist:
      - Command Tower
    blocklist_queries:
      - t:stickers
    outcome_categories:
      mana: ["infinite colored mana", "infinite colorless mana"]
      damage: {patterns: ["infinite damage"], min_combos: 40}
      other: []
    payoffs:
      mana:
        queries: ['o:"{X}" o:"X damage" -t:land']
        exclude: [Chromatic Orrery]
    enablers:
      fast_mana:
        queries: ['t:artifact mv<=1 o:/sacrifice ~: add/ -t:land -t:creature']

`fixed_cards` names a file in the cube file format (one card per line, blank lines and `#`
comments skipped, a card listed twice read once; see read_card_list), relative to the
working directory: the cards of the cube outside the slots the optimizer fills (the slots
filled by hand with lands, ramp and interaction). A fixed card is in the cube for every rule
that counts combos and takes no slot of the cube size; a fixed card on the blocklist is an
error. A blocklist query is a Scryfall search whose matching cards are blocked too (resolved
before the instance is loaded, see blocklist.py). Every section is optional: without fixed
cards nothing is fixed, without a blocklist and blocklist queries no card is blocked, and
without an outcome, payoff or enabler table the run goes on without the statistics and rules
that need it (a rule that is on needs its table; see CubeConfig.require). The sections are
described in ilp/outcomes.py, ilp/payoffs.py and ilp/enablers.py. Every payoff category must
be a category of the outcome table, so a payoff table needs the outcome table beside it; the
enabler categories are free-form names.

The default file is config.yaml in the working directory, which is the repository root for
every build.
"""

from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from mtg_combo_cube.ilp.enablers import EnablerDefinitions, parse_enabler_table
from mtg_combo_cube.ilp.outcomes import OutcomeCategories, parse_outcome_categories
from mtg_combo_cube.ilp.payoffs import PayoffDefinitions, PayoffTableError, parse_payoff_table

DEFAULT_CONFIG_PATH = "config.yaml"
SECTIONS = (
    "fixed_cards",
    "blocklist",
    "blocklist_queries",
    "outcome_categories",
    "payoffs",
    "enablers",
)


class ConfigError(ValueError):
    """An invalid configuration file, or one without a section a run needs."""


def read_card_list(path: str | Path) -> list[str]:
    """
    The card names of a list file: one per line, blank lines and `#` comment lines skipped,
    a name listed more than once (a Cube Cobra export lists a card twice when the cube holds
    two) returned once, in the order of first appearance. Raises FileNotFoundError.
    """
    with open(path, encoding="utf-8") as f:
        lines = [line.strip() for line in f]
    return list(dict.fromkeys(line for line in lines if line and not line.startswith("#")))


@dataclass(frozen=True)
class CubeConfig:
    """
    The configuration of a run; a payoff table is checked against the outcome table, and
    the fixed cards against the blocklist.
    """

    fixed_cards: frozenset[str] = frozenset()
    fixed_cards_file: str | None = None  # the file the fixed cards were read from
    blocklist: frozenset[str] = frozenset()
    blocklist_queries: tuple[str, ...] = ()
    outcome_categories: OutcomeCategories | None = None
    payoffs: PayoffDefinitions | None = None
    enablers: EnablerDefinitions | None = None

    def __post_init__(self) -> None:
        if blocked := sorted(self.fixed_cards & self.blocklist):
            raise ConfigError(
                f"fixed cards cannot be on the blocklist: {blocked} (fix the blocklist or the "
                f"fixed cards file{f' {self.fixed_cards_file}' if self.fixed_cards_file else ''})"
            )
        if self.payoffs is None:
            return
        if self.outcome_categories is None:
            raise PayoffTableError(
                "the payoff table needs the outcome category table to place its categories"
            )
        self.payoffs.check_categories(self.outcome_categories)

    def require(
        self, *, outcome_categories: bool = False, payoffs: bool = False, enablers: bool = False
    ) -> None:
        """Raise ConfigError when a section a run needs is missing."""
        missing = [
            name
            for name, needed, present in (
                ("outcome_categories", outcome_categories, self.outcome_categories),
                ("payoffs", payoffs, self.payoffs),
                ("enablers", enablers, self.enablers),
            )
            if needed and present is None
        ]
        if missing:
            raise ConfigError(
                f"the configuration has no {' or '.join(missing)} section, which the rules "
                "that are on need (--min-outcome-combos, --max-outcome-share, --min-payoffs, "
                "--min-enablers)"
            )


def parse_fixed_cards_file(entry: object) -> str:
    """The path of the fixed cards file from the parsed `fixed_cards` entry (a string)."""
    if not isinstance(entry, str) or not entry.strip():
        raise ConfigError("fixed_cards must be the path of a card list file")
    return entry.strip()


def parse_blocklist(entries: object) -> frozenset[str]:
    """The blocked card names from the parsed `blocklist` section (a list of names)."""
    if not isinstance(entries, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in entries
    ):
        raise ConfigError("the blocklist must be a list of card names")
    return frozenset(str(entry).strip() for entry in entries)


def parse_blocklist_queries(entries: object) -> tuple[str, ...]:
    """The Scryfall queries from the parsed `blocklist_queries` section (a list of queries)."""
    if not isinstance(entries, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in entries
    ):
        raise ConfigError("the blocklist queries must be a list of Scryfall queries")
    return tuple(dict.fromkeys(str(entry).strip() for entry in entries))


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
    fixed_cards_file = sections.get("fixed_cards")
    blocklist = sections.get("blocklist")
    blocklist_queries = sections.get("blocklist_queries")
    outcomes = sections.get("outcome_categories")
    payoffs = sections.get("payoffs")
    enablers = sections.get("enablers")
    return CubeConfig(
        fixed_cards_file=(
            None if fixed_cards_file is None else parse_fixed_cards_file(fixed_cards_file)
        ),
        blocklist=frozenset() if blocklist is None else parse_blocklist(blocklist),
        blocklist_queries=(
            () if blocklist_queries is None else parse_blocklist_queries(blocklist_queries)
        ),
        outcome_categories=None if outcomes is None else parse_outcome_categories(outcomes),
        payoffs=None if payoffs is None else parse_payoff_table(payoffs),
        enablers=None if enablers is None else parse_enabler_table(enablers),
    )


def load_config(path: str | None = None, fixed_cards_file: str | None = None) -> CubeConfig:
    """
    Load the configuration from a YAML file (config.yaml in the working directory when no
    path is given) and read its fixed cards file, or `fixed_cards_file` in its place (the
    --fixed-cards override).

    Raises FileNotFoundError when the file or the fixed cards file does not exist,
    ConfigError when it is not valid YAML, has unknown sections or fixes a blocked card, and
    OutcomeCategoryError, PayoffTableError or EnablerTableError when a table is invalid.
    """
    config_path = Path(path) if path else Path(DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"{config_path} is not valid YAML: {e}") from e
    config = parse_config(data)
    file = fixed_cards_file or config.fixed_cards_file
    if file is None:
        return config
    if not Path(file).exists():
        raise FileNotFoundError(f"Fixed cards file not found: {file}")
    return replace(config, fixed_cards=frozenset(read_card_list(file)), fixed_cards_file=file)
