# ILP Performance Investigation Plan

> **Note (2026-10-05): historical document.** It records the December 2025 investigation and is
> kept unchanged except where marked "superseded". The current state of the code, the benchmark
> numbers and the decisions are in [ilp-improvement-plan.md](ilp-improvement-plan.md). What
> changed since this was written:
>
> - The Phase 2 model at the time could leave a completed combo uncounted, so the Phase 2 combo
>   counts and utilization numbers below are under-reported and not comparable with current ones.
> - The default Phase 2 objective is now `tiered`; `minmax` and `mad` remain available, along
>   with `maxutil` and `softcap`.
> - The utilization floor is a constraint for every objective, not part of `minmax`.
> - The code shown in Steps 2 and 4 no longer exists in this form (see the superseded table
>   under "Files Changed").

## Problem Statement

Two-phase ILP optimization for a 300-card cube with 10,000 variants takes approximately **30 minutes** to run. This investigation aims to identify the primary bottleneck and guide optimization efforts.

## Profiling Implementation

Profiling has been added to the ILP optimizer. Key metrics captured:
- Constraint counts by type
- Variable counts
- Build/solve/extract timings
- OR-Tools solver statistics (branches, conflicts, booleans, objective bounds)

---

## Step 1: Small-Scale Profile Test ✓ COMPLETE

**Command:**
```bash
uv run python -m src.mtg_combo_cube -c 100 --method ilp -o data/cube_test.txt -t 300 -n 1000 --profile --read-api-cache
```

**Parameters:** 100 cards, 1,000 variants, 300s time limit

### Baseline Results (Before Warm-Start)

| Metric | Phase 1 | Phase 2 |
|--------|---------|---------|
| Constraints | 4,528 | 12,992 |
| Variables (card + combo) | 1,933 | 1,933 + 2,820 aux |
| Model build time | 0.0s | 0.2s |
| **Solver time** | **0.5s** | **302.9s** |
| Branches | 2,122 | 99,257 |
| Conflicts | 0 | 12,572 |

### Key Findings

1. **Phase 2 is the bottleneck** - 99.8% of total time
2. Phase 2 explores **46.8x more branches** than Phase 1
3. Phase 2 has 12,572 conflicts vs 0 for Phase 1
4. Model build time is negligible (<1% of total)
5. Phase 2 status was `FEASIBLE` (not OPTIMAL) - hit time limit

**Bottleneck: Phase 2 Solver** ✓

---

## Step 2: Warm-Start Optimization ✓ IMPLEMENTED

### Implementation

Added hints to Phase 2 model using Phase 1 solution:
```python
# Hint card selection variables
for card in self.all_cards:
    model.add_hint(x[card], 1 if card in phase1_cards else 0)

# Hint combo completion variables
for combo in self.combos:
    model.add_hint(y[combo.id], 1 if combo.id in phase1_combos else 0)

# Hint utilization variables
for card in self.all_cards:
    util_value = phase1_result.utilization_per_card.get(card, 0)
    model.add_hint(u[card], util_value if card in phase1_cards else 0)
```

### Results With Warm-Start

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Phase 2 Branches | 99,257 | 8,208 | **12x reduction** |
| Phase 2 Conflicts | 12,572 | 0 | **Eliminated** |
| Phase 2 Solver Time | 302.9s | 300.4s | Still hits limit |
| Phase 2 Status | FEASIBLE | FEASIBLE | No change |

### Analysis

Warm-start significantly improved search efficiency:
- **12x fewer branches** explored
- **Zero conflicts** (down from 12,572)
- Phase 2 now explores only **2.3x more branches** than Phase 1 (vs 46.8x before)

However, the solver still hits the time limit. The issue is now **proving optimality** rather than finding a good solution.

---

## Step 3: Gap Limit Analysis ✓ IMPLEMENTED

Added `--gap-limit` CLI parameter (default: 5%) for early termination.

### MAD Objective Gap

| Metric | Value |
|--------|-------|
| Objective Value | 24,745 |
| Best Bound | 13,903 |
| **Relative Gap** | **43.8%** |

The MAD objective creates weak bounds, so even 20% gap limit won't trigger early termination.

---

## Step 4: Min-Max Range Objective ✓ IMPLEMENTED

### New Objective

Replaced MAD with min-max range: `minimize(max_util - min_util)` with a minimum floor constraint.

```python
# Only 2 auxiliary variables instead of 2×cards
max_util = model.new_int_var(0, max_combos, "max_util")
min_util = model.new_int_var(0, max_combos, "min_util")

# Link to card utilizations
for card in cards:
    model.add(max_util >= u[card]).only_enforce_if(x[card])
    model.add(min_util <= u[card]).only_enforce_if(x[card])

# Floor constraint
model.add(min_util >= 2)

# Objective
model.minimize(max_util - min_util)
```

### Comparison Results (10s time limit)

| Metric | MAD | Min-Max | Comparison |
|--------|-----|---------|------------|
| Phase 2 Branches | 8,103 | 552 | **15x fewer** |
| Std Dev Improvement | 29.1% | 28.2% | Similar |
| Range Improvement | 23.3% | 53.3% | **2.3x better** |
| Relative Gap | 43.8% | 23.0% | **Much tighter** |
| Final Range | 1-24 | 2-16 | Better extremes |

### Key Insights

1. **Min-max is much more efficient**: 15x fewer branches for similar std_dev improvement
2. **Better range compression**: 53% vs 23% range reduction
3. **Tighter bounds**: 23% gap vs 43.8% gap - gap limit can actually trigger
4. **Trade-off**: MAD optimizes all cards equally; min-max focuses on extremes

### When to Use Each

*Superseded: `minmax` is no longer the default. See the objective comparison in
[ilp-improvement-plan.md](ilp-improvement-plan.md), Stage 4, and the README.*

- **Min-Max (default at the time)**: Fast results with good extremes, tighter bounds for gap-limit
- **MAD (`--phase2-objective mad`)**: When you care about overall distribution smoothness and have time to spare

---

## Implementation Status

| Optimization | Status | Impact |
|--------------|--------|--------|
| Warm-start hints | ✓ Done | 12x fewer branches, 0 conflicts |
| Gap limit CLI | ✓ Done | Works better with minmax (23% gap) |
| Min-max objective | ✓ Done | 15x fewer branches, 2.3x better range |

---

## CLI Options Added

*Superseded: as added in December 2025. Current options and defaults are in the README
(`--phase2-objective` now defaults to `tiered`; the floor applies to every objective).*

```
--phase2-objective    Phase 2 objective: 'minmax' (default) or 'mad'
--min-util-floor      Minimum utilization floor for minmax (default: 2)
--gap-limit           Early termination gap (default: 0.05 = 5%)
--profile             Enable profiling output
```

---

## Files Changed

*Superseded: `_solve_phase2_minmax()` was removed in the October 2026 refactor. Phase 2 is now
one driver, `_solve_phase2()`, with the objectives registered in `_PHASE2_OBJECTIVES`
(`ilp_optimizer.py`); see [ilp-improvement-plan.md](ilp-improvement-plan.md), Stage 2.*

| File | Change (December 2025) |
|------|--------|
| `src/mtg_combo_cube/ilp/profiling.py` | ProfileResult, extract_solver_stats (with bounds) |
| `src/mtg_combo_cube/ilp/ilp_optimizer.py` | Added `_solve_phase2_minmax()`, warm-start, gap_limit |
| `src/mtg_combo_cube/ilp/ilp_models.py` | Added profile_data field |
| `src/mtg_combo_cube/ilp/ilp_runner.py` | Pass all new flags |
| `src/mtg_combo_cube/runner.py` | Pass all new parameters |
| `src/mtg_combo_cube/__main__.py` | Added --phase2-objective, --min-util-floor, --gap-limit, --profile |
| `Taskfile.yml` | --profile now default for build:ilp tasks |
| `README.md` | Documented new options |

---

## Recommendations

*Superseded: see "Measured solve times" and "Phase 2 Objectives" in the README. `mad` is no
longer the recommendation for production runs.*

For **fast iteration** during development:
```bash
uv run python -m src.mtg_combo_cube -c 100 --method ilp -t 30 -n 1000 --phase2-objective minmax --read-api-cache
```

For **production quality** with more time:
```bash
uv run python -m src.mtg_combo_cube -c 300 --method ilp -t 600 -n 10000 --phase2-objective mad
```

The min-max objective provides a good balance of speed and quality for most use cases. Use MAD when you need the smoothest possible utilization distribution and have time to spare.
