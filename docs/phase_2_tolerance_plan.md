# Plan: Configurable Combo Tolerance for ILP Phase 2

## Summary
Add a `--combo-tolerance` CLI argument (default 10%) that allows Phase 2 of the ILP solver to deviate from Phase 1's combo count. This enables trading a small number of combos for better utilization balance.

## Files to Modify

| File | Change |
|------|--------|
| [__main__.py](src/mtg_combo_cube/__main__.py) | Add `--combo-tolerance` CLI argument |
| [runner.py](src/mtg_combo_cube/runner.py) | Pass `combo_tolerance` parameter through |
| [ilp_runner.py](src/mtg_combo_cube/ilp/ilp_runner.py) | Pass to `build_cube_ilp()` and `ILPOptimizer` |
| [ilp_optimizer.py](src/mtg_combo_cube/ilp/ilp_optimizer.py) | Store tolerance, modify Phase 2 constraint |

## Implementation Steps

### 1. Add CLI argument (`__main__.py`)
Add after line 44 (after `--single-phase`):
```python
argparser.add_argument(
    "--combo-tolerance",
    type=float,
    default=0.1,
    help="Tolerance for combo count deviation in phase 2 (default: 0.1 = 10%%). "
         "Set to 0 for strict equality constraint.",
)
```
Pass `combo_tolerance=args.combo_tolerance` to `run()` call.

### 2. Update `runner.py`
Add `combo_tolerance: float = 0.1` parameter to `run()` signature and pass to `run_ilp()`.

### 3. Update `ilp_runner.py`
- Add `combo_tolerance: float = 0.1` to `run_ilp()` and `build_cube_ilp()` signatures
- Pass to `ILPOptimizer` constructor

### 4. Core change in `ilp_optimizer.py`

**Constructor**: Add `combo_tolerance: float = 0.1` parameter and store as `self.combo_tolerance`.

**`_solve_phase2()` method** - Replace line 295:
```python
# Current:
model.add(sum(y[combo.id] for combo in self.combos) == target_combo_count)

# New:
combo_sum = sum(y[combo.id] for combo in self.combos)

if self.combo_tolerance > 0:
    min_combo_count = math.floor(target_combo_count * (1 - self.combo_tolerance))
    max_combo_count = math.ceil(target_combo_count * (1 + self.combo_tolerance))

    logger.info(
        f"Phase 2 combo tolerance: {self.combo_tolerance:.1%} "
        f"(range: {min_combo_count}-{max_combo_count})"
    )

    model.add(combo_sum >= min_combo_count)
    model.add(combo_sum <= max_combo_count)
else:
    # No tolerance - use exact equality (current behavior)
    model.add(combo_sum == target_combo_count)
```

## Usage Examples
```bash
# Default (10% tolerance)
uv run python -m mtg_combo_cube -m ilp

# Strict equality (current behavior)
uv run python -m mtg_combo_cube -m ilp --combo-tolerance 0

# 25% tolerance
uv run python -m mtg_combo_cube -m ilp --combo-tolerance 0.25
```

## Notes
- Only affects two-phase optimization (single-phase is unaffected)
- When tolerance is 0, behavior is identical to current implementation
- `math` module already imported in `ilp_optimizer.py`
