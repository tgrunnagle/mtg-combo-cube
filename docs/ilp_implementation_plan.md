# ILP-Based Cube Optimization Implementation Plan

## Overview

Add Integer Linear Programming (ILP) optimization to maximize completable combos within a fixed cube size. Uses OR-Tools CP-SAT solver. Keeps existing greedy algorithm as default.

## Files to Create

### 1. `src/mtg_combo_cube/ilp_models.py`
Data structures for ILP optimization:
- `ComboData` - preprocessed combo (id, required_cards, requirement_options, popularity)
- `OptimizationResult` - solution (selected_cards, combo_ids, stats)

### 2. `src/mtg_combo_cube/combo_preprocessor.py`
Convert Variants to ComboData:
- Extract required cards from `uses` field
- Resolve template requirements via Scryfall API (limit 10 cards per template)
- Cache Scryfall responses (follow pattern from `variant_tracker.py:59-68`)
- Skip combos with unresolvable requirements

### 3. `src/mtg_combo_cube/ilp_optimizer.py`
Core ILP solver:
- Decision variables: `x[card]` (in cube?), `y[combo]` (completable?)
- Objective: Maximize `sum((1 + 0.001 * log(1 + popularity)) * y[combo])`
- Constraints:
  - `sum(x[card]) = cube_size`
  - `y[combo] <= x[card]` for each required card
  - `y[combo] <= sum(x[options])` for each template requirement
- Use CP-SAT with parallel workers and time limit

### 4. `src/mtg_combo_cube/ilp_runner.py`
High-level async interface:
- `build_cube_ilp()` - fetch variants, preprocess, optimize
- `run_ilp()` - entry point matching `run.run()` signature

## Files to Modify

### 1. `pyproject.toml`
Add dependencies:
```toml
dependencies = [
    "aiohttp>=3.13.2",
    "pydantic>=2.12.5",
    "ortools>=9.10",  # NEW
]
```

### 2. `src/mtg_combo_cube/__main__.py`
Add CLI arguments:
- `-m/--method` - "greedy" (default) or "ilp"
- `-t/--time-limit` - solver time limit in seconds (default 300)

Route to `run_ilp()` when `--method ilp` is specified.

## Implementation Order

| Step | Task | File(s) |
|------|------|---------|
| 1 | Add ortools dependency | `pyproject.toml` |
| 2 | Create ILP data models | `ilp_models.py` |
| 3 | Create combo preprocessor | `combo_preprocessor.py` |
| 4 | Create ILP optimizer | `ilp_optimizer.py` |
| 5 | Create runner/integration layer | `ilp_runner.py` |
| 6 | Add CLI arguments | `__main__.py` |

## Key Design Decisions

1. **Separate preprocessing from optimization** - Async API calls in preprocessor, sync solving in optimizer
2. **Use CP-SAT over MILP** - Free, fast for binary problems, provides feasible solutions on timeout
3. **Integer weights** - Scale floats by 10000 for CP-SAT compatibility
4. **Graceful degradation** - Skip unresolvable combos, return best solution on timeout
5. **10 cards per template** - Higher than current 5 for better optimization coverage

## CLI Usage

```bash
# Existing greedy approach (default)
uv run python -m src.mtg_combo_cube -c 300

# New ILP approach
uv run python -m src.mtg_combo_cube -c 300 --method ilp --time-limit 300
```

## Success Criteria

- ILP produces equal or more combos than greedy
- No dead cards (cards in 0 completable combos)
- Solve time < 5 minutes for 300 cards / 10K combos
- Deterministic results (same input = same output)
