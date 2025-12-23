# Plan: Add Requirement Type Metrics to ILP Output

## Summary
For the final selected cube, compute and output metrics per requirement type (template):
- **combo_count**: How many completable combos use this requirement
- **card_count**: How many cards in the cube satisfy this requirement
- **coverage_ratio**: `card_count / combo_count`
- **cards**: List of cube cards satisfying the requirement

Also compute aggregate statistics:
- **mean_coverage_ratio**: Average coverage ratio across all requirement types
- **std_dev_coverage_ratio**: Standard deviation of coverage ratios

## Key Insight
Currently, `ComboData.requirement_options` is a `list[frozenset[str]]` that maps to the original `Variant.requires[i].template`. The template name identifies the requirement type (e.g., "Sacrifice outlet", "Mana dork"). This mapping is **lost** during preprocessing - we need to preserve it.

## Files to Modify

| File | Change |
|------|--------|
| [ilp_models.py](src/mtg_combo_cube/ilp/ilp_models.py) | Add `RequirementOption`, `RequirementTypeStats`, `RequirementCoverageStats` dataclasses; update `ComboData` and `OptimizationResult` |
| [combo_preprocessor.py](src/mtg_combo_cube/ilp/combo_preprocessor.py) | Preserve template names when creating `RequirementOption` |
| [ilp_optimizer.py](src/mtg_combo_cube/ilp/ilp_optimizer.py) | Add methods to compute requirement stats; update code accessing `requirement_options` |
| [ilp_runner.py](src/mtg_combo_cube/ilp/ilp_runner.py) | Output requirement stats to JSON |

## Implementation Steps

### 1. Update `ilp_models.py`

Add new dataclasses:

```python
@dataclass
class RequirementOption:
    """A single template requirement with its matching cards."""
    template_name: str
    cards: frozenset[str]

@dataclass
class RequirementTypeStats:
    """Statistics for a single requirement type."""
    template_name: str
    combo_count: int
    card_count: int
    cards: list[str]
    coverage_ratio: float

@dataclass
class RequirementCoverageStats:
    """Aggregate statistics for coverage ratios across all requirement types."""
    mean_coverage_ratio: float
    std_dev_coverage_ratio: float
```

Update `ComboData`:
```python
@dataclass
class ComboData:
    id: str
    required_cards: frozenset[str]
    requirement_options: list[RequirementOption]  # Changed from list[frozenset[str]]
    popularity: int
```

Update `OptimizationResult`:
```python
# Add new fields:
requirement_type_stats: list[RequirementTypeStats] | None = None
requirement_coverage_stats: RequirementCoverageStats | None = None
```

### 2. Update `combo_preprocessor.py`

Preserve template names when creating `RequirementOption`:

```python
requirement_options: list[RequirementOption] = []
for req in variant.requires:
    if req.template.scryfall_api is None:
        return None
    cards = await self._resolve_template(req.template.scryfall_api)
    if not cards:
        return None
    requirement_options.append(RequirementOption(
        template_name=req.template.name,
        cards=frozenset(cards),
    ))
```

### 3. Update `ilp_optimizer.py`

Update all code that accesses `combo.requirement_options` to use `opt.cards`:
- `_collect_all_cards()` - iterate `opt.cards` instead of `opts`
- `_build_participation_graph()` - iterate `opt.cards`
- `solve()` constraints - use `opt.cards`
- `_solve_phase2()` constraints - use `opt.cards`

Add method to calculate requirement type stats:

```python
def _calculate_requirement_stats(
    self,
    selected_cards: set[str],
    completable_combo_ids: set[str],
) -> list[RequirementTypeStats]:
    """Calculate stats for each requirement type in completable combos."""
    template_stats: dict[str, dict] = {}

    for combo in self.combos:
        if combo.id not in completable_combo_ids:
            continue
        for opt in combo.requirement_options:
            name = opt.template_name
            if name not in template_stats:
                template_stats[name] = {"combos": set(), "cards": set()}
            template_stats[name]["combos"].add(combo.id)
            satisfying = opt.cards & selected_cards
            template_stats[name]["cards"].update(satisfying)

    return [
        RequirementTypeStats(
            template_name=name,
            combo_count=len(data["combos"]),
            card_count=len(data["cards"]),
            cards=sorted(data["cards"]),
            coverage_ratio=len(data["cards"]) / len(data["combos"]) if data["combos"] else 0.0,
        )
        for name, data in sorted(template_stats.items())
    ]
```

Add method to compute aggregate coverage stats:

```python
def _compute_coverage_stats(
    self,
    requirement_stats: list[RequirementTypeStats],
) -> RequirementCoverageStats:
    """Compute mean and std dev of coverage ratios."""
    if not requirement_stats:
        return RequirementCoverageStats(mean_coverage_ratio=0.0, std_dev_coverage_ratio=0.0)

    ratios = [r.coverage_ratio for r in requirement_stats]
    mean = sum(ratios) / len(ratios)
    variance = sum((r - mean) ** 2 for r in ratios) / len(ratios)
    std_dev = variance ** 0.5

    return RequirementCoverageStats(
        mean_coverage_ratio=mean,
        std_dev_coverage_ratio=std_dev,
    )
```

Call these methods in `solve()` and `_solve_phase2()`, add results to `OptimizationResult`.

### 4. Update `ilp_runner.py`

Add requirement stats to the JSON output in `write_utilization_stats()`:

```python
if result.requirement_type_stats:
    stats["requirement_types"] = {
        "summary": {
            "mean_coverage_ratio": result.requirement_coverage_stats.mean_coverage_ratio,
            "std_dev_coverage_ratio": result.requirement_coverage_stats.std_dev_coverage_ratio,
        } if result.requirement_coverage_stats else None,
        "by_type": [
            {
                "template_name": r.template_name,
                "combo_count": r.combo_count,
                "card_count": r.card_count,
                "coverage_ratio": r.coverage_ratio,
                "cards": r.cards,
            }
            for r in sorted(result.requirement_type_stats, key=lambda x: -x.combo_count)
        ],
    }
```

## Output Format (in cube_stats.json)

```json
{
  "metadata": { ... },
  "phase1": { ... },
  "phase2": { ... },
  "requirement_types": {
    "summary": {
      "mean_coverage_ratio": 0.35,
      "std_dev_coverage_ratio": 0.18
    },
    "by_type": [
      {
        "template_name": "Sacrifice outlet",
        "combo_count": 42,
        "card_count": 8,
        "coverage_ratio": 0.19,
        "cards": ["Viscera Seer", "Carrion Feeder", ...]
      },
      {
        "template_name": "Mana dork",
        "combo_count": 15,
        "card_count": 5,
        "coverage_ratio": 0.33,
        "cards": ["Birds of Paradise", "Llanowar Elves", ...]
      }
    ]
  }
}
```

## Notes
- The `coverage_ratio` represents how many unique cards cover each combo using that requirement. A ratio of 0.19 means on average ~5 combos share each card satisfying that requirement.
- Stats are only computed for completable combos in the final cube (not all input combos).
- The `by_type` list is sorted by `combo_count` descending (most common requirement types first).
