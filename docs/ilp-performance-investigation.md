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

### Results

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

## Step 2: Root Cause Analysis

The MAD (Mean Absolute Deviation) objective in Phase 2 creates a much harder optimization problem:

1. **Utilization linking constraints** (1,880): Force `u[card] = combo_sum` when selected
2. **MAD deviation constraints** (3,760): 4 constraints per card for `d_plus`, `d_minus`
3. **Conditional constraints**: `only_enforce_if()` creates reified constraints

The solver struggles because:
- **Objective is continuous-like**: Minimizing sum of deviations has many near-optimal solutions
- **No strong bounds**: Unlike Phase 1's integer combo count, deviation can vary smoothly
- **Branching difficulty**: 99K branches with 12K conflicts indicates poor search guidance

---

## Step 3: Optimization Strategies

### A. Warm-Start from Phase 1 (Recommended First)

Provide Phase 1 solution as a hint to Phase 2:
```python
# After Phase 1, before Phase 2 solve:
for card in phase1_selected:
    solver.add_hint(x[card], 1)
for combo in phase1_completed:
    solver.add_hint(y[combo.id], 1)
```

**Expected impact:** Reduces branches by starting closer to optimal.

### B. Time Budget Split

Currently both phases share time limit. Consider:
- Phase 1: 10% of time (fast, usually optimal quickly)
- Phase 2: 90% of time (needs more exploration)

### C. Simplify Objective

Replace MAD with simpler objectives:
1. **Min-max utilization**: `minimize(max_util - min_util)` - fewer variables
2. **Variance proxy**: Single auxiliary variable instead of per-card deviations
3. **Quantile-based**: Only constrain bottom 10% utilization

### D. Constraint Reduction

The 3,760 MAD constraints dominate. Options:
- Only add deviation constraints for "borderline" cards (high variance candidates)
- Use lazy constraint generation

---

## Step 4: Implementation Priority

1. **Warm-start** - Low effort, potentially high impact
2. **Time split** - Simple configuration change
3. **Simpler objective** - Medium effort, guarantees faster solve
4. **Lazy constraints** - Higher complexity

---

## Next Steps

- [ ] Implement warm-start from Phase 1
- [ ] Re-run test to measure improvement
- [ ] If still slow, try simpler objective function

---

## Files Changed (Profiling Implementation)

| File | Change |
|------|--------|
| `src/mtg_combo_cube/ilp/profiling.py` | **New** - ProfileResult, extract_solver_stats |
| `src/mtg_combo_cube/ilp/ilp_optimizer.py` | Instrumented solve() and _solve_phase2() |
| `src/mtg_combo_cube/ilp/ilp_models.py` | Added profile_data field |
| `src/mtg_combo_cube/ilp/ilp_runner.py` | Pass flag, output to stats JSON |
| `src/mtg_combo_cube/runner.py` | Pass profile parameter |
| `src/mtg_combo_cube/__main__.py` | Added --profile flag |
| `Taskfile.yml` | --profile now default for build:ilp tasks |
