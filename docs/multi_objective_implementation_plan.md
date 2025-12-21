# Multi-Objective ILP Optimization Implementation Plan

## Overview

Implement two-phase multi-objective optimization for the MTG Combo Cube ILP solver:
- **Phase 1**: Maximize combo count (existing behavior)
- **Phase 2**: Minimize card utilization variance while preserving optimal combo count

Multi-objective optimization will be the **default behavior** for ILP, with `--single-phase` flag to opt out.

## User Requirements

Based on design document and user preferences:
- Make multi-objective the default ILP behavior (no flag needed)
- Phase 2 failures fall back to Phase 1 with warning
- Skip warm-start optimization (defer to future)
- Output Phase 1 vs Phase 2 comparison stats to separate JSON file

## Implementation Steps

### 1. Data Model Extensions

**File**: [src/mtg_combo_cube/ilp_models.py](src/mtg_combo_cube/ilp_models.py)

Add `UtilizationStats` dataclass after `ComboData`:
```python
@dataclass
class UtilizationStats:
    """Statistics about card utilization across completable combos."""
    min_utilization: int
    max_utilization: int
    mean_utilization: float
    std_deviation: float
    total_absolute_deviation: int
    median_utilization: float
```

Extend `OptimizationResult` with optional multi-objective fields (backward compatible):
```python
@dataclass
class OptimizationResult:
    # Existing fields (unchanged)
    selected_cards: list[str]
    completable_combo_ids: list[str]
    combo_count: int
    objective_value: float
    solve_time_seconds: float
    status: str

    # New fields with defaults
    utilization_per_card: dict[str, int] | None = None
    phase1_utilization_stats: UtilizationStats | None = None
    phase2_utilization_stats: UtilizationStats | None = None
    phase1_solve_time: float | None = None
    phase2_solve_time: float | None = None
    phase2_status: str | None = None
    is_multi_objective: bool = False
```

### 2. ILP Optimizer Enhancements

**File**: [src/mtg_combo_cube/ilp_optimizer.py](src/mtg_combo_cube/ilp_optimizer.py)

#### 2.1 Precompute Card Participation Graph

Add to `__init__` after line 42:
```python
self.card_to_combos: dict[str, list[ComboData]] = self._build_participation_graph()
```

Add method after `_collect_all_cards()`:
```python
def _build_participation_graph(self) -> dict[str, list[ComboData]]:
    """Build mapping of cards to combos they participate in."""
    from collections import defaultdict

    participation: dict[str, list[ComboData]] = defaultdict(list)

    for combo in self.combos:
        for card in combo.required_cards:
            participation[card].append(combo)
        for opts in combo.requirement_options:
            for card in opts:
                if combo not in participation[card]:
                    participation[card].append(combo)

    return {card: participation.get(card, []) for card in self.all_cards}
```

#### 2.2 Add Utilization Calculation Helpers

Add methods after `_compute_weight()`:
```python
def _calculate_utilization(
    self,
    selected_cards: list[str],
    completable_combo_ids: list[str]
) -> dict[str, int]:
    """Calculate utilization for each selected card."""
    completable_set = set(completable_combo_ids)
    utilization: dict[str, int] = {}

    for card in selected_cards:
        count = sum(
            1 for combo in self.card_to_combos[card]
            if combo.id in completable_set
        )
        utilization[card] = count

    return utilization

def _compute_utilization_stats(
    self,
    utilization: dict[str, int]
) -> UtilizationStats:
    """Compute statistical summary of card utilization."""
    if not utilization:
        return UtilizationStats(0, 0, 0.0, 0.0, 0, 0.0)

    values = list(utilization.values())
    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    std_dev = variance ** 0.5
    total_abs_dev = sum(abs(x - mean) for x in values)

    sorted_values = sorted(values)
    median = (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2 if n % 2 == 0 else float(sorted_values[n // 2])

    return UtilizationStats(
        min_utilization=min(values),
        max_utilization=max(values),
        mean_utilization=mean,
        std_deviation=std_dev,
        total_absolute_deviation=total_abs_dev,
        median_utilization=median,
    )
```

#### 2.3 Enhance Existing `solve()` Method

After extracting solution (around line 158), add:
```python
# Calculate utilization for this solution
utilization = self._calculate_utilization(selected, completed)
utilization_stats = self._compute_utilization_stats(utilization)
```

Update return statement (line 165):
```python
return OptimizationResult(
    selected_cards=selected,
    completable_combo_ids=completed,
    combo_count=len(completed),
    objective_value=objective / self.WEIGHT_SCALE,
    solve_time_seconds=solve_time,
    status=status_str,
    utilization_per_card=utilization,
    phase1_utilization_stats=utilization_stats,
    phase1_solve_time=solve_time,
    is_multi_objective=False,
)
```

#### 2.4 Implement Phase 2 Solver

Add new method after `solve()`:
```python
def _solve_phase2(
    self,
    target_combo_count: int,
    phase1_result: OptimizationResult,
) -> OptimizationResult:
    """
    Phase 2: Minimize utilization variance while preserving combo count.

    Key constraints:
    - Fixed combo count from Phase 1
    - Utilization variables: u[c] = sum of completed combos card c participates in
    - MAD linearization: minimize sum(d_plus[c] + d_minus[c])

    Falls back to Phase 1 result if Phase 2 fails.
    """
    # Implementation following design doc lines 142-188
    # - Build new CP-SAT model with same base constraints
    # - Add combo count constraint: sum(y[j]) == target_combo_count
    # - Add utilization constraints using only_enforce_if for x[card]
    # - Add MAD deviation constraints
    # - Minimize total deviation
    # - Return Phase 1 result if INFEASIBLE or TIMEOUT
```

#### 2.5 Add Two-Phase Orchestrator

Add public method after `_solve_phase2()`:
```python
def solve_two_phase(self) -> OptimizationResult:
    """
    Two-phase multi-objective optimization (recommended entry point).

    Returns:
        OptimizationResult with balanced utilization, or Phase 1 fallback
    """
    logger.info("Starting two-phase multi-objective optimization")

    # Phase 1: Maximize combo count
    phase1_result = self.solve()

    # Handle Phase 1 failure or edge cases
    if phase1_result.status not in ("OPTIMAL", "FEASIBLE"):
        logger.warning(f"Phase 1 failed: {phase1_result.status}")
        return phase1_result

    if phase1_result.combo_count == 0:
        logger.warning("Phase 1 found 0 combos. Skipping Phase 2.")
        return phase1_result

    # Phase 2: Balance utilization
    return self._solve_phase2(
        target_combo_count=phase1_result.combo_count,
        phase1_result=phase1_result,
    )
```

### 3. Stats File Output

**File**: [src/mtg_combo_cube/ilp_runner.py](src/mtg_combo_cube/ilp_runner.py)

Add imports at top:
```python
import json
from datetime import datetime, timezone
from pathlib import Path
```

Add function after `collect_variants()`:
```python
def write_utilization_stats(
    result: OptimizationResult,
    output_file: str,
    cube_size: int,
) -> None:
    """Write utilization statistics to JSON file."""
    # Derive stats filename: cube.txt -> cube_stats.json
    output_path = Path(output_file)
    stats_file = output_path.with_stem(f"{output_path.stem}_stats").with_suffix(".json")

    # Build JSON structure with metadata, phase1, phase2 sections
    # Include improvement metrics if multi-objective
    # Include top/bottom utilized cards

    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

    logger.info(f"Utilization statistics written to {stats_file}")
```

### 4. Entry Point Updates

#### 4.1 Update `build_cube_ilp()`

**File**: [src/mtg_combo_cube/ilp_runner.py](src/mtg_combo_cube/ilp_runner.py) (lines 30-78)

Add parameter:
```python
async def build_cube_ilp(
    cube_size: int,
    max_cards_in_combo: int = 4,
    max_variants: int = 10000,
    time_limit_seconds: int = 300,
    use_multi_objective: bool = True,  # NEW: default to two-phase
) -> tuple[list[str], int, OptimizationResult]:
```

Replace line 71:
```python
# Run optimization (two-phase by default)
if use_multi_objective:
    result = optimizer.solve_two_phase()
else:
    result = optimizer.solve()
```

Add logging after line 75:
```python
# Log utilization improvements if multi-objective
if result.is_multi_objective and result.phase2_utilization_stats:
    p1 = result.phase1_utilization_stats
    p2 = result.phase2_utilization_stats
    logger.info(
        f"Utilization: std_dev {p1.std_deviation:.1f} → {p2.std_deviation:.1f} "
        f"({100 * (1 - p2.std_deviation / p1.std_deviation):.1f}% improvement)"
    )
```

#### 4.2 Update `run_ilp()`

**File**: [src/mtg_combo_cube/ilp_runner.py](src/mtg_combo_cube/ilp_runner.py) (lines 81-101)

Add parameter:
```python
async def run_ilp(
    cube_size: int,
    output_file: str,
    time_limit_seconds: int = 300,
    max_variants: int = 10000,
    use_multi_objective: bool = True,  # NEW
):
```

Update call to `build_cube_ilp()`:
```python
cards, combo_count, result = await build_cube_ilp(
    cube_size=cube_size,
    time_limit_seconds=time_limit_seconds,
    max_variants=max_variants,
    use_multi_objective=use_multi_objective,  # NEW
)
```

Add stats file writing after line 100:
```python
with open(output_file, "w", encoding="utf-8") as f:
    f.write("\n".join(cards))

# Write utilization stats
write_utilization_stats(result, output_file, cube_size)
```

#### 4.3 Update CLI

**File**: [src/mtg_combo_cube/__main__.py](src/mtg_combo_cube/__main__.py) (lines 8-46)

Add argument after line 33:
```python
argparser.add_argument(
    "--single-phase",
    action="store_true",
    help="Use single-phase ILP (max combos only). Default: two-phase (balanced utilization)"
)
```

Update `run_ilp()` call around line 39:
```python
if args.method == "ilp":
    asyncio.run(run_ilp(
        args.cube_size,
        args.output_file,
        time_limit_seconds=args.time_limit,
        max_variants=args.max_variants,
        use_multi_objective=not args.single_phase,  # NEW
    ))
```

## Critical Files to Modify

1. [src/mtg_combo_cube/ilp_models.py](src/mtg_combo_cube/ilp_models.py) - Data structures
2. [src/mtg_combo_cube/ilp_optimizer.py](src/mtg_combo_cube/ilp_optimizer.py) - Core optimization logic
3. [src/mtg_combo_cube/ilp_runner.py](src/mtg_combo_cube/ilp_runner.py) - Orchestration and stats output
4. [src/mtg_combo_cube/__main__.py](src/mtg_combo_cube/__main__.py) - CLI interface

## Implementation Order

1. **Data models** (ilp_models.py) - Foundation for all other work
2. **Participation graph** (ilp_optimizer.py) - Precompute card-to-combo mapping
3. **Utilization helpers** (ilp_optimizer.py) - Calculation and stats methods
4. **Enhance Phase 1** (ilp_optimizer.py) - Populate utilization in existing solve()
5. **Phase 2 solver** (ilp_optimizer.py) - Core multi-objective algorithm
6. **Two-phase orchestrator** (ilp_optimizer.py) - Public API with fallback logic
7. **Stats file writer** (ilp_runner.py) - JSON output generation
8. **Entry point updates** (ilp_runner.py) - Wire up multi-objective flag
9. **CLI updates** (__main__.py) - Add --single-phase flag
10. **Integration testing** - Full pipeline validation

## Key Design Decisions

### Utilization Definition
Card utilization = number of **completable** combos the card participates in (either in required_cards or any requirement_options set).

### MAD Linearization
Phase 2 minimizes Mean Absolute Deviation:
- Target mean computed from Phase 1 total utilization
- Deviation variables: d_plus[c] and d_minus[c] for each card
- Constraints: `u[c] - mean ≤ d_plus[c]` and `mean - u[c] ≤ d_minus[c]` (only when x[c]=1)
- Objective: minimize sum(d_plus[c] + d_minus[c])

### Conditional Constraints
Using CP-SAT's `only_enforce_if` for clean handling:
```python
model.add(u[card] == combo_sum).only_enforce_if(x[card])
model.add(u[card] == 0).only_enforce_if(x[card].Not())
```

### Fallback Strategy
Phase 2 failures (INFEASIBLE or TIMEOUT) return Phase 1 solution with warning. User still gets optimal combo count.

## Edge Cases Handled

1. **Cards with zero participation**: Participation graph returns empty list, utilization set to 0
2. **Phase 2 infeasible**: Fall back to Phase 1 result
3. **Phase 2 timeout**: Fall back to Phase 1 result
4. **Zero combos from Phase 1**: Skip Phase 2, return Phase 1 result
5. **Phase 1 failure**: Return immediately, no Phase 2 attempt

## Success Metrics

After implementation:
- Combo count preserved between Phase 1 and Phase 2
- Std deviation reduced by >30% (expected)
- MAD reduced by >40% (expected)
- Phase 2 solve time <2x Phase 1 (expected)
- Total solve time <10 minutes for cube_size=300
- Stats JSON file generated with before/after comparison

## Testing Strategy

1. **Unit tests**: Participation graph, utilization calculation, stats computation
2. **Integration tests**: Two-phase solver, fallback scenarios, stats file writing
3. **Manual validation**: Compare --single-phase vs default two-phase output
4. **Performance validation**: Benchmark solve times on realistic datasets

## Usage Examples

```bash
# Multi-objective (default)
uv run python -m src.mtg_combo_cube -c 300 --method ilp

# Single-phase for comparison
uv run python -m src.mtg_combo_cube -c 300 --method ilp --single-phase

# Output files
# - cube.txt: Selected cards (one per line)
# - cube_stats.json: Utilization comparison and improvement metrics
```
