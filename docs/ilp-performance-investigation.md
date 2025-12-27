# ILP Performance Investigation Plan

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

However, the solver still hits the time limit. The issue is now **proving optimality** rather than finding a good solution. The solver finds a good feasible solution quickly but spends remaining time trying to prove no better solution exists.

---

## Step 3: Early Termination via Gap Limit ✓ IMPLEMENTED

### Implementation

Added `--gap-limit` CLI parameter (default: 5%) to enable early termination:
```python
if self.gap_limit > 0:
    solver.parameters.relative_gap_limit = self.gap_limit
```

### Results

Gap limit doesn't help because **the solver's bound is too loose**:

| Metric | Value |
|--------|-------|
| Objective Value | 24,745 |
| Best Bound | 13,903 |
| **Relative Gap** | **43.8%** |

The solver can't prove tight bounds for the MAD objective, so even a 20% gap limit won't trigger early termination.

### Root Cause

The MAD (Mean Absolute Deviation) objective creates a fundamentally hard problem for CP-SAT's bounding:
1. **Large objective scale**: Deviation values are scaled by 100 for integer arithmetic
2. **Weak LP relaxation**: The reified constraints (`only_enforce_if`) don't provide strong bounds
3. **Symmetry**: Many card swaps produce similar deviation values, creating a flat objective landscape

---

## Step 4: Solution Comparison

### Actual Quality (Not What Gap Suggests)

Despite the 43.8% gap, the solution quality is excellent:

| Metric | Phase 1 | Phase 2 | Improvement |
|--------|---------|---------|-------------|
| Std Dev | 5.64 | 3.66-3.73 | ~35% better |
| Util Range | 2-32 | 2-23 | 30% smaller |
| Combos | 221 | 198 | -10% (within tolerance) |

The gap is a **bound quality issue**, not a solution quality issue. The solver finds good solutions quickly but can't prove they're optimal.

---

## Step 5: Recommended Next Steps

### A. Satisficing Approach (Recommended)

Since solution quality is already good, accept solutions faster:

1. **Solution callback**: Stop after finding first feasible solution
   ```python
   class FirstSolutionCallback(cp_model.CpSolverSolutionCallback):
       def on_solution_callback(self):
           self.stop_search()
   ```

2. **Quality threshold**: Stop when std_dev improvement exceeds target (e.g., 20%)

### B. Simpler Objective (Alternative)

Replace MAD with objectives that have better bounds:

1. **Min-max range**: `minimize(max_util - min_util)` - only 2 auxiliary variables
2. **Bound-only**: `minimize(max_util)` with `min_util >= threshold` constraint
3. **Quadratic approximation**: Use sum of squared deviations (can be linearized)

### C. Iterative Refinement

Run multiple short Phase 2 attempts with progressively tighter constraints:
1. First pass: Quick feasible solution (10s)
2. If time remains: Add constraint to beat current objective, re-solve

---

## Implementation Status

| Optimization | Status | Impact |
|--------------|--------|--------|
| Warm-start hints | ✓ Done | 12x fewer branches, 0 conflicts |
| Gap limit CLI | ✓ Done | Works, but gap is 43.8% (too loose) |
| Solution callback | Pending | Expected: immediate termination |
| Simpler objective | Pending | Expected: faster bounds |

---

## Files Changed

| File | Change |
|------|--------|
| `src/mtg_combo_cube/ilp/profiling.py` | ProfileResult, extract_solver_stats (with bounds) |
| `src/mtg_combo_cube/ilp/ilp_optimizer.py` | Profiling + warm-start + gap_limit |
| `src/mtg_combo_cube/ilp/ilp_models.py` | Added profile_data field |
| `src/mtg_combo_cube/ilp/ilp_runner.py` | Pass flags, output to stats JSON |
| `src/mtg_combo_cube/runner.py` | Pass profile and gap_limit parameters |
| `src/mtg_combo_cube/__main__.py` | Added --profile and --gap-limit flags |
| `Taskfile.yml` | --profile now default for build:ilp tasks |
| `README.md` | Documented --gap-limit and --profile options |
