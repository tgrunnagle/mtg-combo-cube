"""
Outcome categories: what a combo does, from the features it produces on Spellbook.

A category is a name with a list of patterns; a combo belongs to every category one of its
feature names matches. Patterns are case-insensitive substrings ("infinite damage" matches
"Near-infinite damage to one opponent"), or regular expressions when prefixed with "re:"
("re:^lock" matches "Lock" but not "Creatures can't block"). The table lives in a JSON file
so it can be edited without code changes:

    {
      "mana": ["infinite colored mana", "infinite colorless mana"],
      "damage": {"patterns": ["infinite damage"], "min_combos": 40}
    }

The long form gives a category its own minimum for the Phase 2 outcome rule, in place of the
default minimum (--min-outcome-combos).
"""

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_OUTCOME_CATEGORIES_PATH = "data/outcome_categories.json"
REGEX_PREFIX = "re:"


class OutcomeCategoryError(ValueError):
    """An invalid outcome category table."""


@dataclass(frozen=True)
class OutcomeCategory:
    """A category of combo outcomes: its name, its feature patterns and its own minimum."""

    name: str
    patterns: tuple[str, ...]
    min_combos: int | None = None  # overrides the default minimum when given
    _matchers: tuple[re.Pattern[str], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.patterns:
            raise OutcomeCategoryError(f"outcome category {self.name!r} has no patterns")
        if self.min_combos is not None and self.min_combos < 0:
            raise OutcomeCategoryError(
                f"outcome category {self.name!r} has a negative minimum: {self.min_combos}"
            )
        matchers = []
        for pattern in self.patterns:
            if not isinstance(pattern, str) or not pattern:
                raise OutcomeCategoryError(
                    f"outcome category {self.name!r} has an empty pattern: {pattern!r}"
                )
            if pattern.startswith(REGEX_PREFIX):
                body = pattern[len(REGEX_PREFIX) :]
                if not body.strip():
                    raise OutcomeCategoryError(
                        f"outcome category {self.name!r} has an empty regex: {pattern!r}"
                    )
                try:
                    matchers.append(re.compile(body, re.IGNORECASE))
                except re.error as e:
                    raise OutcomeCategoryError(
                        f"outcome category {self.name!r} has an invalid regex {pattern!r}: {e}"
                    ) from e
            else:
                matchers.append(re.compile(re.escape(pattern), re.IGNORECASE))
        object.__setattr__(self, "_matchers", tuple(matchers))

    def matches(self, feature: str) -> bool:
        """Whether the feature name belongs to this category."""
        return any(matcher.search(feature) for matcher in self._matchers)


@dataclass(frozen=True)
class OutcomeCategories:
    """The outcome category table, in file order."""

    categories: tuple[OutcomeCategory, ...]

    def __post_init__(self) -> None:
        if not self.categories:
            raise OutcomeCategoryError("the outcome category table is empty")
        names = [category.name for category in self.categories]
        if len(set(names)) != len(names):
            raise OutcomeCategoryError(f"duplicate outcome category names in {names}")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(category.name for category in self.categories)

    def __len__(self) -> int:
        return len(self.categories)

    def __iter__(self) -> Iterator[OutcomeCategory]:
        return iter(self.categories)

    def categorize(self, features: Iterable[str]) -> frozenset[str]:
        """The names of every category one of the features matches."""
        features = list(features)
        return frozenset(
            category.name
            for category in self.categories
            if any(category.matches(feature) for feature in features)
        )

    def minimums(self, default: int) -> dict[str, int]:
        """
        The minimum completed combos of each category for the outcome rule: the category's
        own minimum when the table gives one, otherwise `default`. Only the categories with
        a minimum above 0.
        """
        minimums: dict[str, int] = {}
        for category in self.categories:
            minimum = default if category.min_combos is None else category.min_combos
            if minimum > 0:
                minimums[category.name] = minimum
        return minimums


def parse_outcome_categories(table: object) -> OutcomeCategories:
    """Build the category table from the parsed JSON object (see the module docstring)."""
    if not isinstance(table, dict):
        raise OutcomeCategoryError("the outcome category table must be a JSON object")
    categories = []
    for name, entry in table.items():
        if not isinstance(name, str):
            raise OutcomeCategoryError(f"outcome category names must be strings, got {name!r}")
        min_combos: int | None = None
        if isinstance(entry, dict):
            entry_dict: dict[str, object] = {str(key): value for key, value in entry.items()}
            patterns = entry_dict.get("patterns")
            minimum = entry_dict.get("min_combos")
            unknown = set(entry_dict) - {"patterns", "min_combos"}
            if unknown:
                raise OutcomeCategoryError(
                    f"outcome category {name!r} has unknown keys {sorted(unknown)}"
                )
            if minimum is not None:
                if isinstance(minimum, bool) or not isinstance(minimum, int):
                    raise OutcomeCategoryError(
                        f"outcome category {name!r} has a non-integer minimum: {minimum!r}"
                    )
                min_combos = minimum
        else:
            patterns = entry
        if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
            raise OutcomeCategoryError(
                f"outcome category {name!r} must list its patterns as strings"
            )
        categories.append(OutcomeCategory(name, tuple(str(p) for p in patterns), min_combos))
    return OutcomeCategories(tuple(categories))


def load_outcome_categories(path: str | None = None) -> OutcomeCategories:
    """
    Load the category table from a JSON file (the default table when no path is given).

    Raises FileNotFoundError when the file does not exist and OutcomeCategoryError when the
    table is invalid.
    """
    table_path = Path(path) if path else Path(DEFAULT_OUTCOME_CATEGORIES_PATH)
    if not table_path.exists():
        raise FileNotFoundError(f"Outcome categories file not found: {table_path}")
    with open(table_path, encoding="utf-8") as f:
        try:
            table = json.load(f)
        except json.JSONDecodeError as e:
            raise OutcomeCategoryError(f"{table_path} is not valid JSON: {e}") from e
    return parse_outcome_categories(table)
