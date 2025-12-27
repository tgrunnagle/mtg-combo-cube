# ILP Performance Investigation Plan

## Problem Statement

Two-phase ILP optimization for a 300-card cube with 10,000 variants takes approximately **30 minutes** to run. This investigation aims to identify the primary bottleneck and guide optimization efforts.

## Profiling Implementation

Profiling has been added to the ILP optimizer. Key metrics captured:
- Constraint counts by type
- Variable counts
- Build/solve/extract timings
- OR-Tools solver statistics (branches, conflicts, booleans)

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

However, the solver still hits the time limit. The issue is now **proving optimality** rather than finding a good solution. The solver finds a good feasible solution quickly but spends remaining time trying to prove no better solution exists.

---

## Step 3: Root Cause Analysis

The MAD (Mean Absolute Deviation) objective in Phase 2 creates a harder optimization problem:

1. **Utilization linking constraints** (1,880): Force `u[card] = combo_sum` when selected
2. **MAD deviation constraints** (3,760): 4 constraints per card for `d_plus`, `d_minus`
3. **Conditional constraints**: `only_enforce_if()` creates reified constraints

The solver struggles to prove optimality because:
- **Objective is continuous-like**: Minimizing sum of deviations has many near-optimal solutions
- **No strong bounds**: Unlike Phase 1's integer combo count, deviation can vary smoothly
- **Symmetry**: Many equivalent card swaps produce similar deviation values

---

## Step 4: Next Optimization Options

### A. Early Termination (Quick Win)

Accept "good enough" solutions instead of waiting for optimality proof:
```python
# Stop when solution is within 5% of best known bound
solver.parameters.relative_gap_limit = 0.05
```

### B. Time Budget Split

Phase 1 completes in <1s. Give Phase 2 almost all the time:
- Current: Both phases share `time_limit`
- Better: Phase 2 gets `time_limit - phase1_time`

### C. Simpler Objective (More Invasive)

Replace MAD with simpler objectives:
1. **Min-max range**: `minimize(max_util - min_util)` - 2 variables instead of 2×cards
2. **Satisficing**: Just constrain `min_util >= threshold`, no optimization
3. **Bucketed deviation**: Only penalize cards outside acceptable range

### D. Solution Callbacks

Use callbacks to accept first feasible solution that meets quality threshold:
```python
class StopOnGoodSolution(cp_model.CpSolverSolutionCallback):
    def on_solution_callback(self):
        if self.objective_value < target_deviation:
            self.stop_search()
```

---

## Implementation Status

| Optimization | Status | Impact |
|--------------|--------|--------|
| Warm-start hints | ✓ Done | 12x fewer branches |
| Early termination | Pending | - |
| Time budget split | Pending | - |
| Simpler objective | Pending | - |

---

## Files Changed

| File | Change |
|------|--------|
| `src/mtg_combo_cube/ilp/profiling.py` | **New** - ProfileResult, extract_solver_stats |
| `src/mtg_combo_cube/ilp/ilp_optimizer.py` | Profiling + warm-start hints |
| `src/mtg_combo_cube/ilp/ilp_models.py` | Added profile_data field |
| `src/mtg_combo_cube/ilp/ilp_runner.py` | Pass flag, output to stats JSON |
| `src/mtg_combo_cube/runner.py` | Pass profile parameter |
| `src/mtg_combo_cube/__main__.py` | Added --profile flag |
| `Taskfile.yml` | --profile now default for build:ilp tasks |
