# ILP Improvement Plan

Created 2026-10-05. Tracks the follow-up work from the October 2026 code review.
Tick items as they land. Record benchmark numbers in the tables so decisions are traceable.

## Status

| Stage | Summary | Status |
|-------|---------|--------|
| 0 | Safe-run guardrails and baseline | Done (2026-10-05) |
| 1 | Housekeeping fixes | Done (2026-10-05) |
| 2 | Refactor shared model building | Done (2026-10-05) |
| 3 | Tighten the Phase 2 formulation | Done (2026-10-05) |
| 4 | New Phase 2 objectives and benchmark | Done (2026-10-05); default is `tiered`, Goal 1 only met at the time limit |
| 5 | Cap search using Phase 1 (optional) | Skipped (2026-10-05); reasons in the Stage 5 section |
| 6 | Data layer: Scryfall cache and rate limiting | Done (2026-10-05) |
| 7 | Docs, merge, and wrap-up | Done (2026-10-05) except the commit and merge, which the owner does (see "Hand-off") |

## Outcome

Result against the goals listed under "Goals" (written 2026-10-05, at the end of Stage 7). Rung
L is 300 cards / 10,000 variants; all numbers are true values taken from the tables below.

| Goal | Result | Supporting numbers |
|------|--------|--------------------|
| 1. Balanced rung L result in under 5 minutes | **Partly met: only at the time limit** | No objective reaches its stop rule on rung L inside 300 s; every run ends FEASIBLE at the limit. The default (`tiered`) returns at 302-303 s with a 16-17.5% gap. The cube it returns is clearly more balanced than Phase 1 (std dev 41.85 -> 28.6 / 29.1 in two runs, cards above 100: 12 -> 7 / 8, maximum 348 -> 255) for 10% of the combos (2471 -> 2223). The December 2025 run needed 30 minutes for range 277 -> 257 and left a card at utilization 0 |
| 2. No selected card below the floor, whichever objective | **Met for every Phase 2 result** | Minimum utilization is 2 (the default floor) in all seven rung L runs of all five objectives, and at least 2 in every Stage 3 / Stage 4 run on S and M. The floor is a per-card constraint added by the Phase 2 driver. Not covered: a Phase 2 fallback and `--single-phase` return the Phase 1 cube, which has no floor (the rung L Phase 1 cube happens to have minimum 7) |
| 3. One copy of the model-building code | **Met** | One `_build_base_model`, one Phase 2 driver, five objectives registered in `_PHASE2_OBJECTIVES`. The pre-refactor file built the base model three times. Small remaining overlaps are listed under "Follow-ups" (h) |
| 4. No network calls on a repeat run with a warm cache | **Met for the ILP method** | Rung L warm run: `0 network requests, 68 templates read from cache` (`data/bench_v2_L_warm.log`); the Stage 7 smoke run on rung S: `0 network requests, 24 templates read from cache`. The greedy method still queries Commander Spellbook live (greedy was a non-goal) |

What did not work out: Phase 2 is still not solved at full size. The work made the reported
numbers correct (Stage 3), made the model tighter and the default result flatter (Stages 3
and 4), and made runs repeatable (Stage 6), but the remaining difficulty appears to come from
the problem definition (coverage rule plus utilization definition, "Follow-ups" (a)), not from
the choice of objective.

## Background

- Phase 1 (maximize combo count) is solved: OPTIMAL in about 5 s at 300 cards / 10,000 variants.
- Phase 2 (balance utilization) is the bottleneck. The last full run (2025-12-24, MAD objective)
  hit the 30-minute limit at FEASIBLE, reduced the utilization range only from 277 to 257, and
  left a selected card with utilization 0.
- The blue screens were most likely CPU instability under sustained all-core load, not a solver
  bug. The BIOS was updated on 2026-10-05 (version 1836, microcode 0x133). Long, many-worker
  solves should still be reintroduced gradually (Stage 0).
- `ilp_optimizer.py` builds the same base model three times (Phase 1, MAD, minmax), so every
  formulation change must currently be made in three places.

## Goals

1. Phase 2 reaches a good, balanced solution at 300 cards / 10,000 variants in **under 5 minutes**.
2. No selected card has utilization below the floor, whichever objective is used.
3. One copy of the model-building code.
4. Repeat runs make no network calls when the cache is warm.

Non-goals: changing the greedy algorithm, changing Phase 1's objective, adding a UI.

## Working agreements

- Branch: new branch off `perf_2025-12-25` (that branch is unmerged and holds the profiling,
  warm-start, gap-limit, and minmax work this plan builds on).
- Validate each stage with `task check` (lint, format, typecheck, tests).
- Benchmark ladder, always with `--read-api-cache --profile`:

  | Rung | Cube size | Variants | Time limit | Purpose |
  |------|-----------|----------|------------|---------|
  | S | 100 | 1,000 | 30 s | Fast iteration, matches the earlier investigation |
  | M | 200 | 5,000 | 120 s | Scaling check |
  | L | 300 | 10,000 | 300 s | Target configuration |

---

## Stage 0: Safe-run guardrails and baseline

Purpose: be able to run the solver at a controlled load, and capture "before" numbers.

- [x] Add `--workers` CLI flag, plumbed through `runner.py` -> `ilp_runner.py` -> `ILPOptimizer`.
      Replaces the three hardcoded `num_workers = 8`. Default 8.
- [x] Add a `WORKERS` variable to the `build:ilp*` tasks in `Taskfile.yml`.
- [x] Warm the API cache once per rung (first run fetches from Spellbook and Scryfall).
      Only the Spellbook variants are cached; Scryfall templates are refetched every run (Stage 6).
- [x] Stability check after the BIOS update: run rung S, then M, then L with `--workers 4`,
      then L with `--workers 8`. Stop and investigate hardware if any run crashes the machine.
- [x] Record the baseline (current minmax objective, default settings):

  | Rung | P1 time | P2 time | P2 status | P2 gap | Combos P1 -> P2 | Util range P1 -> P2 | Std dev P1 -> P2 |
  |------|---------|---------|-----------|--------|-----------------|---------------------|------------------|
  | S | 30.0 s (FEASIBLE, gap 343.8%) | 30.1 s | FEASIBLE | 100.0% | 223 -> 200 | 2-47 (45) -> 2-22 (20) | 6.18 -> 5.36 |
  | M | 120.3 s (FEASIBLE, gap 346.4%) | 120 s (hit limit) | TIMEOUT, no solution found; fell back to P1 | n/a | 1099 -> 1099 (fallback) | 4-220 (216) -> unchanged | 26.97 -> unchanged |
  | L | 300.4 s (FEASIBLE, gap 302.0%) | 300 s (hit limit) | TIMEOUT, no solution found; fell back to P1 | n/a | 2438 -> 2438 (fallback) | 7-330 (323) -> unchanged | 40.51 -> unchanged |
  | L (8 workers) | 4.2 s (OPTIMAL, gap 0.0%) | 301.3 s | FEASIBLE | 11.7% | 2470 -> 2223 | 7-347 (340) -> 2-146 (144) | 41.83 -> 31.43 |

  Notes on the baseline (runs of 2026-10-05, rows S/M/L use `--workers 4`):

  - The time limit applies per phase. "P1 time" shows Phase 1 status and relative gap in brackets.
    P2 gap is the solver's relative gap on the scaled range objective.
  - With 4 workers Phase 1 never proved optimality: it ran to the time limit on every rung with the
    bound stuck near its trivial value (gap 300%+). With 8 workers on rung L it was OPTIMAL in 4.2 s
    with 0 conflicts. CP-SAT's subsolver portfolio depends on the worker count, so 4 workers is not
    a like-for-like lower-load setting.
  - On rungs M and L with 4 workers Phase 2 found no solution at all inside the limit (status
    TIMEOUT, despite the warm-start hints) and the run fell back to the Phase 1 cube. The stats JSON
    then reports `single_phase` and has no `phase2` block.
  - The instances are not identical between runs: Scryfall returned HTTP 429 for 0 / 44 / 116 / 108
    template requests (S / M / L / L-8), and the affected combos were silently dropped
    (995 / 4,910 / 9,806 / 9,814 combos kept). Stage 6 fixes this; until then compare runs with care.
  - No crash or instability in any of the four runs.

  **Baseline v2 (8 workers, cached Scryfall data)**. Taken after Stage 6 on 2026-10-05; this is
  the reference for all later stages. Same settings as above except `--workers 8`:

  | Rung | P1 time | P2 time | P2 status | P2 gap | Combos P1 -> P2 | Util range P1 -> P2 | Std dev P1 -> P2 | Combos in instance |
  |------|---------|---------|-----------|--------|-----------------|---------------------|------------------|--------------------|
  | S | 0.2 s (OPTIMAL, gap 0.0%) | 30.1 s | FEASIBLE | 84.6% | 231 -> 207 | 2-50 (48) -> 3-22 (19) | 6.66 -> 5.30 | 995 |
  | M | 3.3 s (OPTIMAL, gap 0.0%) | 19.3 s | OPTIMAL (stopped at the 5% gap limit) | 4.6% | 1113 -> 1001 | 5-219 (214) -> 2-99 (97) | 27.16 -> 22.45 | 4,954 |
  | L | 5.1 s (OPTIMAL, gap 0.0%) | 300.7 s | FEASIBLE | 42.9% | 2471 -> 2223 | 7-348 (341) -> 2-213 (211) | 41.85 -> 42.08 | 9,922 |

  Notes on baseline v2 (files: `data/bench_v2_{S,M,L}.{log,txt}` and `_stats.json`):

  - Zero Scryfall failures on every rung, so these are the full instances. Combos dropped in
    preprocessing: S 5 (all `no_scryfall_api`), M 46 (4 `blocked_card`, 42 `no_scryfall_api`),
    L 78 (5 `blocked_card`, 73 `no_scryfall_api`); no `scryfall_failure` or `empty_match`.
  - The whole ladder needs only 68 distinct Scryfall templates, cached in
    `data/cache/scryfall_templates.json`. A warm start of rung L logs `0 network requests,
    68 templates read from cache` (`data/bench_v2_L_warm.log`).
  - Rung M Phase 2 stopped early at the 5% gap limit, which CP-SAT reports as OPTIMAL.
  - Rung L Phase 2 at the time limit is much worse here (range 211, std dev slightly above
    Phase 1) than in the Stage 0 8-worker run (range 144, std dev 31.43) on a 9,814-combo
    instance. One run each, so the cause (instance difference or run-to-run variation of a
    time-limited parallel search) is not established; treat single rung L Phase 2 numbers as noisy.

Done when: the baseline table is filled in and rung L completes without a crash.

## Stage 1: Housekeeping fixes

Small, independent fixes. No behavior change to the optimization.

- [x] Fix ruff E501 at `ilp_optimizer.py:885`.
- [x] Fix the 7 `ty` warnings (`phase1_utilization_stats` may be `None` in both Phase 2 methods);
      remove the `# type: ignore` on `phase1_solve_time` the same way.
- [x] Fix mixed clocks: the two early returns in `solve()` compute
      `time.time() - start_time` where `start_time` is a `perf_counter()` value.
- [x] README corrections:
  - `--method` default is `ilp` in code; README says `greedy`.
  - Quick Start uses `CARD_COUNT=300`; the Taskfile variable is `CUBE_SIZE`.
  - `task run` is documented but does not exist.
  - Complexity section says Phase 2 has `3C` integer variables; true only for MAD.
- [x] `solve_two_phase` comment says MAD is the default branch; it is not.

Done when: `task check` is fully clean.

## Stage 2: Refactor shared model building

Purpose: one place to change the formulation. Pure refactor; existing tests stay green unchanged.

- [x] `_build_base_model() -> (model, x, y, counts)`: card/combo variables, cube-size constraint,
      required-card constraints, requirement-option constraints.
- [x] `_add_combo_count_window(model, y, target)`: the tolerance / equality logic.
- [x] `_add_utilization_vars(model, x, y) -> u`: utilization variables and linking.
- [x] `_add_warm_start(model, x, y, u, phase1_result)`.
- [x] `_make_solver(gap_limit: float | None)`: time limit, workers, logging, gap.
- [x] `_extract_result(solver, x, y, ...)`: selected cards, combos, utilization, requirement and
      cross-template stats, profile data. Used by Phase 1 and every Phase 2 variant.
- [x] Phase 2 becomes one method that takes an objective strategy (`mad`, `minmax`, and the new
      ones from Stage 4) instead of one 300-line method per objective.
- [x] Add a regression test: Phase 1 and both existing Phase 2 objectives give the same combo
      count and objective value on a small fixed instance before and after the refactor.

Done when: `ilp_optimizer.py` has no duplicated constraint code and rung S results match Stage 0.

Structure after the refactor (2026-10-05), all in `ilp_optimizer.py` (1,247 -> 1,018 lines):

- `_BaseModel` (dataclass): `model`, `x`, `y`, and `counts` (the profile counts dict; every
  builder below records what it added there).
- `_build_base_model()` builds the variables and the cube-size, required-card and
  requirement-option constraints. Phase 1 (`solve`) adds `_add_combo_count_objective(base)`.
- Phase 2 is one driver, `_solve_phase2(phase1_result, profile)`, which calls in order:
  `_build_base_model`, `_add_combo_count_window`, `_add_phase2_coverage`,
  `_add_utilization_vars` (returns `u`), the objective's `add_to_model`, `_add_warm_start`.
- Shared by both phases: `_make_solver(gap_limit=None)`, `_run_solver` (solve, solver profile,
  status string), `_extract_solution` (returns a `_Solution` with cards, combos, utilization and
  all stats). Phase 2 only: `_log_phase2_improvement`, `_merge_phase2_profile`.
- To add a Phase 2 objective (corrected 2026-10-05 in Stage 7 to match the code after Stage 4):
  1. Write `_add_<name>_objective(self, base, u, phase1_result) -> None` in `ILPOptimizer`.
     The callback signature is `add_to_model(optimizer, base, u, phase1_result)`;
     `phase1_result` is the Phase 1 `OptimizationResult` (get its stats with
     `self._require_phase1_stats(phase1_result)`). Add the variables and constraints, call
     `base.model.minimize(...)` and record what was added in `base.counts`.
  2. Use `u[card]` as it is: exact, and 0 for unselected cards. Do not add a floor; the driver
     adds it for every objective (`_add_utilization_floor`).
  3. Tiebreak: minimize `primary * self._tiebreak_scale() - self._versatility_bonus(base)` so
     the bonus stays a pure tiebreak.
  4. Hint the objective's own variables from `base.hint_utilization` (the utilization of the
     warm-start cube); `_add_warm_start` hints only `x`, `y`, `z` and `u`.
  5. If the optimum can be near 0, set `base.absolute_gap_limit` (see
     `_set_overage_stop_rule`); otherwise the relative `--gap-limit` applies.
  6. Register it in the `_PHASE2_OBJECTIVES` dict as
     `_Phase2Objective(label=..., add_to_model=..., uses_util_cap=...)` and add the name to
     the `--phase2-objective` choices and help text in `__main__.py`
     (`tests/unit/test_runner.py` checks that every registered name is a CLI choice).
  7. Add it to the brute-force comparison in `tests/unit/test_ilp_phase2_objectives.py` and
     to the README objective table.
- Superseded by Stage 3 (kept for the record): at the end of Stage 2 the floor was still inside
  `_add_minmax_objective`, and an unknown `phase2_objective` value fell back to `mad`. Now the
  floor is in the driver and an unknown value raises `ValueError` in `ILPOptimizer.__init__`.
  `_solve_phase2_minmax` is gone; `_solve_phase2` no longer takes `target_combo_count` (it uses
  `phase1_result.combo_count`).
- Verification: the models are byte-identical to the pre-refactor ones (serialized CP-SAT proto
  and solver parameters compared on 220 configurations). Characterization tests are in
  `tests/unit/test_ilp_optimizer_characterization.py`. Rungs S and M
  (`data/bench_s2_{S,M}.*`): profile counts identical to baseline v2 for both phases;
  Phase 1 identical (231 / 1113 combos, OPTIMAL); Phase 2 S FEASIBLE 207 combos, range 3-22,
  std dev 5.30 (same as v2); Phase 2 M OPTIMAL at the gap limit in 45.3 s, 1001 combos,
  range 2-101, std dev 22.67 (v2: 19.3 s, 2-99, 22.45).

## Stage 3: Tighten the Phase 2 formulation

Purpose: stronger LP relaxation and smaller domains, independent of which objective is used.
Extended on 2026-10-05 with a correctness fix (Part A) and small fixes (Part C) that came out of
the Stage 2 review.

Part A: correctness (reported numbers and the Phase 2 model must match the real cube)

- [x] Finding confirmed: in Phase 2, `y[combo]` was only bounded from above, and the Phase 2
      objectives reward low utilization, so the solver could set `y = 0` for a combo the cube
      completes. Combo counts and utilization were read from `y`, so they were under-reported.
- [x] `cube_evaluation.py`: pure functions `completable_combo_ids(selected_cards, combos)`,
      `card_utilization(...)` and `compute_utilization_stats(...)`. `_extract_solution` (both
      phases) now derives every reported number from the selected cards and logs a WARNING if
      the solver's `y` disagrees.
- [x] Evaluation entry point:
      `uv run python -m mtg_combo_cube.ilp.evaluate_cube <cube.txt> -n <variants> [--blocklist f]`
      (loads the instance from the caches through the normal preprocessing path,
      `ilp_runner.load_instance`).
- [x] True values of the baseline v2 cubes recorded (table below).
- [x] Phase 2 is exact: `y[combo] = 1` iff the cube completes the combo
      (`_add_exact_combo_linking`). Phase 1 is unchanged (it maximizes `y`).
- [x] Decision on `u[card]` recorded (below and in the decision log).

Part B: tightening

- [x] Per-card utilization bound: `u[c]` in `[0, len(card_to_combos[c])]` instead of
      `[0, len(combos)]`.
- [x] Min/max linking without the global big-M. Max side: plain `max_util >= u[c]` (no big-M at
      all, since `u[c] = 0` for unselected cards; stronger than the planned per-card `M_c`).
      Min side: `min_util <= u[c] + M * (1 - x[c])` with `M` = upper bound of `min_util`.
- [x] Bound `max_util` by `max_c len(card_to_combos[c])`; bound `min_util` by the
      `cube_size`-th largest per-card bound (the minimum over `cube_size` cards cannot exceed it).
- [x] Cards with no combos: `u[c]` is the constant 0, no linking constraints.
- [x] Utilization floor applied to **all** objectives, per card: `u[c] >= floor * x[c]`
      (`_add_utilization_floor`, called by the Phase 2 driver). Cards that can never reach the
      floor are fixed to `x[c] = 0`. `mad` used to ignore the floor.
- [x] MAD deviation variables bounded per card as well.

Part C: small fixes from the Stage 2 review

- [x] Unknown `phase2_objective` raises `ValueError` (in `ILPOptimizer.__init__`).
- [x] Phase 2 fallback leaves a trace: the result has `phase2_fell_back = True`,
      `phase2_status`, `phase2_solve_time`, total solve time and the Phase 2 profile block; the
      stats JSON says `optimization_method: two_phase_fallback_to_phase1` and has a `phase2`
      block with status and time. `is_multi_objective` stays `False` for a fallback.
- [x] `extract_solver_stats`: gap is 0.0 when objective == bound (also for objective 0),
      otherwise `|objective - bound| / max(1, |objective|)` (CP-SAT's definition). Objective and
      gap are omitted when the solver found no solution.
- Noted, left alone: coverage constraints exist only in Phase 2; the MAD target is a truncated
  Phase 1 mean; versatility bonus granularity. README still describes the floor as minmax-only
  (Stage 7).

Tests

- [x] Unit tests: completability and utilization functions; exactness regression test (old
      one-sided model under-reports, reported values stay true); minmax optimum equals a
      brute-force enumeration of the characterization instance (bounds cut nothing off); floor
      respected under `mad`; `ValueError`; fallback trace; gap computation. 143 -> 180 tests.

**Baseline v2, true values.** The baseline v2 cubes (`data/bench_v2_{S,M,L}.txt`) re-evaluated
with `evaluate_cube`. "Reported" is what the Stage 0 table shows for Phase 2:

| Rung | Combos reported -> true | Util range reported -> true | Std dev reported -> true | True mean / median |
|------|-------------------------|-----------------------------|--------------------------|--------------------|
| S | 207 -> 208 | 3-22 (19) -> 3-23 (20) | 5.30 -> 5.46 | 7.47 / 5.0 |
| M | 1001 -> 1002 | 2-99 (97) -> 2-100 (98) | 22.45 -> 22.60 | 19.60 / 11.0 |
| L | 2223 -> 2244 | 2-213 (211) -> 2-224 (222) | 42.08 -> 43.33 | 30.15 / 15.0 |

The discrepancy was real but small on S and M (one hidden combo) and larger on L (21 hidden
combos, true range 11 wider). In all three runs the reported count sat exactly on the lower edge
of the tolerance window. Phase 1 numbers were not affected (Phase 1 was OPTIMAL, and it
maximizes `y`).

- [x] Re-run the ladder with the existing minmax objective (8 workers, warm caches, all values
      TRUE values, files `data/bench_s3_{S,M,L}.*`):

  | Rung | P1 time | P2 time | P2 status | P2 gap | Branches | Combos P1 -> P2 | Util range P1 -> P2 | Std dev P1 -> P2 |
  |------|---------|---------|-----------|--------|----------|-----------------|---------------------|------------------|
  | S | 0.2 s (OPTIMAL, gap 0.0%) | 30.1 s | FEASIBLE | 12.3% | 810 | 231 -> 207 | 2-50 (48) -> 3-22 (19) | 6.66 -> 5.30 |
  | M | 3.2 s (OPTIMAL, gap 0.0%) | 81.3 s | OPTIMAL | 4.8% | 14,125 | 1113 -> 1001 | 5-219 (214) -> 2-101 (99) | 27.16 -> 22.66 |
  | L | 5.2 s (OPTIMAL, gap 0.0%) | 304.8 s | FEASIBLE | 9.5% | 570,984 | 2471 -> 2223 | 7-348 (341) -> 2-154 (152) | 41.85 -> 32.83 |

  For comparison, baseline v2 with true values: S 30.1 s, FEASIBLE, gap 84.6%, 208 combos,
  range 20, std dev 5.46. M 19.3 s, stopped at the 5% gap limit, 1002 combos, range 98, std dev
  22.60. L 300.7 s, FEASIBLE, gap 42.9%, 2244 combos, range 222, std dev 43.33.

  Notes on the Stage 3 ladder (one run per rung, 2026-10-05):

  - Every number was re-checked with `evaluate_cube` on the written cube and matches. No run
    logged a "disagree" warning, so the solver's `y` equals the truth on all rungs.
  - S: same quality as before by true values (range 19 vs 20, std dev 5.30 vs 5.46) and a much
    stronger bound (gap 12.3% vs 84.6%). Range 19 is the proven optimum for this instance: two
    earlier runs of the same model (the "linear" column below) finished OPTIMAL in about 25 s.
    This run did not finish the proof inside 30 s, so the proof time sits near the limit.
  - M: slower than baseline v2. It reached the 5% gap limit in 81.3 s (v2: 19.3 s; the Stage 2
    re-run: 45.3 s) with range 99 (v2 true: 98). The other two M runs of the exact model took
    69.2 s and more than 120 s (comparison table below), so this is not a one-off. The exact
    model is larger and the old model could reach its gap target partly by hiding combos.
  - L: clearly better. True range 222 -> 152, std dev 43.33 -> 32.83, gap 42.9% -> 9.5%, still
    FEASIBLE at the 300 s limit. Single run of a time-limited parallel search; baseline v2 L
    was itself an unusually bad run (see the Stage 0 notes).
  - P2 gap is the solver's relative gap on the scaled range objective. P2 time includes model
    build (0.1 / 0.3 / 0.7 s).
  - Phase 2 combo counts are again exactly the lower edge of the tolerance window
    (207 / 1001 / 2223), now as true counts.


Exact-linking formulation (decision): for each distinct option pool one boolean `z` with
`z <= sum(x over pool)` and `z >= x[c]` per pool card (single-card pools use `x[c]` itself; `z`
is shared by all combos with the same card set: 24 / 48 variables on rungs S / M), then
`y >= sum(required x) + sum(z) - (k - 1)`. Written as linear constraints. The clause form
(`add_bool_or` / `add_implication`) was compared on the same model, default minmax, 8 workers:

| Rung | Linear | Clause |
|------|--------|--------|
| S (2 runs each, 30 s limit) | OPTIMAL in 25.3 s and 25.1 s (range 19 proven) | FEASIBLE at 30 s both times, gap 42.1% and 15.8% (same range 19) |
| M (1 run each) | FEASIBLE at 120 s, gap 8.6%, range 4-104 (100) | stopped at the 5% gap limit in 69.2 s, range 2-100 (98) |

Linear was chosen because it proved optimality on S in both comparison runs; the M result
points the other way, but it is one run each of a time-limited parallel search, and the later
ladder runs of the linear form (S not proven in 30 s, M at the gap limit in 81 s) show how large
the run-to-run spread is. The two forms describe the same constraints, so any difference is
search behavior, not model strength. The evidence for linear over clause is weak; revisit only
if Stage 4 needs it. Files: `data/bench_s3_cmp_*`.

`u[card]` (decision): kept as a variable with the meaning "utilization if selected, else 0".
With exact `y` it is a plain equality `u[c] == sum(y)` for cards that are only ever required
cards (`y <= x` already forces the sum to 0 when the card is not selected). Cards that appear in
an option pool still need the two enforced constraints, because such a combo can be complete
through another card of the pool. Keeping "0 if unselected" is what lets `max_util >= u[c]` work
without a big-M.

Done when: the table is filled in and gap and time are no worse than Stage 0 on every rung.
Status against that criterion: gap is better on every rung (12.3% vs 84.6%, 4.8% vs 4.6% at the
same 5% stop, 9.5% vs 42.9%). Time is equal on S and L (both at the limit) and worse on M
(81 s vs 19 s). The M regression is accepted as the price of a correct model; the old M time was
measured on a model that could hide combos. Goal 1 (rung L under 5 minutes) is not met yet;
that is Stage 4.

Notes for Stage 4:

- Phase 2 driver order: `_build_base_model`, `_add_combo_count_window`, `_add_phase2_coverage`,
  `_add_exact_combo_linking`, `_add_utilization_vars`, `_add_utilization_floor`, the
  objective's `add_to_model`, `_add_warm_start`. `_BaseModel.option_satisfied` holds the `z`
  variables.
- The floor lives in the driver (`_add_utilization_floor`), not in any objective. `maxutil`
  therefore needs no `min_util` and no floor logic of its own.
- `u[c]` is exact and 0 for unselected cards, with upper bound `len(card_to_combos[c])`.
  `maxutil` is just `max_util >= u[c]` for every card plus `minimize(max_util)`. `softcap` can
  use `over[c] >= u[c] - T` for every card without conditioning on `x[c]` (an unselected card
  has `u = 0`, so `over = 0`); bound `over[c]` by `max(0, len(card_to_combos[c]) - T)` and skip
  cards whose bound is not above `T`.
- Because `y` is exact, the lower edge of the combo window is now a real cost: Phase 2 can no
  longer hide combos, so it gives up real combos to balance. Expect the solver to sit at the
  window's lower edge; the `--combo-tolerance` review in Stage 4 matters more than before.
- The window's upper edge (`combo_sum <= ceil(target * (1 + tol))`) is now also real. It cannot
  bind when Phase 1 was OPTIMAL, but it would cut off better cubes after a FEASIBLE Phase 1.
- Objectives set their own hints for their auxiliary variables (see `_add_minmax_objective`);
  `_add_warm_start` hints `x`, `y`, `z` and `u` with true Phase 1 values.
- All reported numbers come from `cube_evaluation`; a "disagree" WARNING in a log means the
  model is not exact any more and should be treated as a bug.
- Use `evaluate_cube` to compare cubes across stages; never compare against the "reported"
  Stage 0 Phase 2 numbers.

## Stage 4: New Phase 2 objectives and benchmark

Purpose: replace the range objective, which is dominated by a handful of hub cards
(one card sat at 257 combos while the median was 17).

Candidates, each added as a `--phase2-objective` choice:

- [x] **`maxutil`**: minimize `max_util` only. The floor from Stage 3 handles the low end, so
      `min_util` and its linking constraints disappear. Pure min-max with a tight bound.
- [x] **`softcap`**: minimize `sum_c over[c]` where `over[c] >= u[c] - T` for selected cards and
      `over[c] >= 0`. `T` comes from a new `--util-cap` flag; default derived from Phase 1
      (`2 x median`). Penalizes every over-used card, not just the worst one.
- [x] **`tiered`** (added in this stage, one extra variant): `softcap` with a penalty that grows
      with the distance from the cap. Minimizes the total utilization above `T`, plus the total
      above `2T`, plus the total above `4T`, so a unit of utilization costs 1 / 2 / 3 in the
      three bands. A convex piecewise-linear stand-in for the squared deviation.
- [x] Keep `minmax` and `mad` available for comparison.
- [x] Benchmark on the ladder (tables below).
- [x] **Decision:** `tiered` is the new default objective (details below).
- [x] `--combo-tolerance` revisited: keep 0.1 (sweep below).

Done when: a default is chosen and it meets Goal 1. Status: a default is chosen. Goal 1 is met
only in the weak sense (see "Goal 1" below).

### What was built (2026-10-05)

- `maxutil`, `softcap`, `tiered` registered in `_PHASE2_OBJECTIVES`; CLI choices extended;
  `--util-cap` (int, optional) plumbed `__main__` -> `runner.run` -> `run_ilp` ->
  `build_cube_ilp` -> `ILPOptimizer(util_cap=...)`. A negative cap raises `ValueError`.
- Default cap rule (`_resolve_util_cap`): `T = max(1, ceil(2 x Phase 1 median utilization))`.
  The median is the typical card and is not pulled up by the hubs (on rung L the Phase 1 median
  is 15, the mean 27.4), and "more than twice the typical card" is also how the result tables
  count over-used cards. The cap comes from Phase 1, not from the Phase 2 cube, so it is a fixed
  number for the solve. `T` used: 10 / 20 / 30 on rungs S / M / L. It is logged
  (`Phase 2: utilization cap T = 30 (2 x Phase 1 median)`) and written to the result
  (`OptimizationResult.phase2_util_cap`, with `phase2_objective`) and to the stats JSON
  (`phase2.objective`, `phase2.util_cap`), also for a fallback.
- Tiebreak: `_tiebreak_scale()` = `max(WEIGHT_SCALE, largest possible versatility bonus + 1)`,
  used by `minmax`, `maxutil`, `softcap`, `tiered`. One unit of the primary objective always
  outweighs the whole bonus, so the bonus is a pure tiebreak. On the real instances the scale
  is `WEIGHT_SCALE` (10,000), so `minmax` models are unchanged. `mad` is left as it was (its
  primary term is scaled by 100 only; noted in Stage 3).
- Stop rule for `softcap` / `tiered` (`_set_overage_stop_rule`): their optimum can be near 0,
  where a relative gap is useless, and the objective is negative once only the bonus is left.
  The gap limit is therefore measured against the Phase 1 cube: CP-SAT's `absolute_gap_limit`
  is set so the solve stops when the overage is proven within `gap_limit x (overage of the
  Phase 1 cube)` of its optimum, without proving the tiebreak. The relative limit stays on but
  can never be the tighter one. `--gap-limit 0` still means "solve to optimality". The gap
  column below is the solver's relative gap; for these objectives the stop rule is the
  absolute one (rung L `tiered`: stop at a proven gap of 293 overage units).
- Objective signature changed: `add_to_model(optimizer, base, u, phase1_result)` (was
  `phase1_stats`), because the overage hints need per-card values.
- Warm start (`_build_warm_start`, `_repair_warm_start`). Finding from the CP-SAT log
  (`-d`): the Phase 1 cube is **not** a feasible hint. It satisfies the floor on all rungs
  but breaks coverage constraints (1 / 4 / 10 on S / M / L), CP-SAT reports "The solution hint
  is complete, but it is infeasible", and on rung M the first Phase 2 solution appeared after
  11.5 s. Fix: when the Phase 1 cube breaks coverage or the floor, a feasible cube is searched
  in the small Phase 1 model (coverage constraints, lower edge of the combo window, and the
  floor written as `floor * x[c] <= sum(y of c's combos)`, which is valid for one-sided `y`),
  hinted with the Phase 1 cube, stopping at the first solution. Time allowance: 10% of the
  time limit; it counts against the Phase 2 limit. Measured: 0.1 s / 1.3 s / 3.7-12.3 s on
  S / M / L; afterwards CP-SAT logs "The solution hint is complete and is feasible" (checked
  on M and L). All hints (x, y, z, u, objective variables) come from the warm-start cube.
  The Phase 2 profile gets a `warm_start_repair` timing when a repair ran.
- Tests: 180 -> 288. Every new objective is checked against a brute-force enumeration of the
  characterization instance (6 size / floor / tolerance cases; 3 caps for `softcap`, 2 for
  `tiered`), plus the derived cap, `--util-cap` plumbing (CLI, runner, optimizer, stats JSON),
  tiebreak scale, stop rule and warm-start repair (`tests/unit/test_ilp_phase2_objectives.py`,
  `tests/unit/test_runner.py`).

### Results

All numbers are true values (from the cube), one run each unless noted, 8 workers, warm
caches, default tolerance 0.1 and floor 2. "Cards > 2x / 4x median" uses the median of the
cube itself. "Cards swapped" = cards in the final cube that are not in the Phase 1 cube.
Files: `data/bench_s4_<rung>_<objective>.*`.

Rung L (300 cards, 9,922 combos, 300 s). Phase 1: 2471 combos, range 7-348, std dev 41.85,
median 15, 60 / 27 cards above 2x / 4x median, 12 cards above 100.

  | Rung | Objective | P2 time | Status | Gap | Combos kept | Util range | Std dev | Median | Cards > 2x / 4x median | Cards > 100 | Cards swapped |
  |------|-----------|---------|--------|-----|-------------|------------|---------|--------|------------------------|-------------|---------------|
  | L | minmax (Stage 3 run) | 304.8 s | FEASIBLE | 9.5% | 2223 | 2-154 | 32.83 | 16 | 60 / 25 | 19 | 61 |
  | L | minmax (Stage 4 code) | 303.2 s | FEASIBLE | 10.0% | 2224 | 2-155 | 32.20 | 16 | 62 / 27 | 19 | 65 |
  | L | mad | 300.7 s | FEASIBLE | 21.9% | 2223 | 2-240 | 29.46 | 17 | 59 / 20 | 8 | 59 |
  | L | maxutil | 301.3 s | FEASIBLE | 11.0% | 2223 | 2-155 | 32.59 | 16 | 62 / 26 | 18 | 63 |
  | L | softcap (T = 30) | 300.4 s | FEASIBLE | 20.5% | 2223 | 2-261 | 29.81 | 16 | 63 / 24 | 8 | 48 |
  | L | tiered (T = 30), run 1 | 302.1 s | FEASIBLE | 17.5% | 2223 | 2-255 | 28.57 | 16 | 67 / 22 | 7 | 65 |
  | L | tiered (T = 30), run 2 | 303.1 s | FEASIBLE | 16.0% | 2223 | 2-255 | 29.06 | 16 | 69 / 23 | 8 | 51 |

Rungs S and M (iteration runs; not all with the final code):

  | Rung | Objective | P2 time | Status | Combos kept | Util range | Std dev | Cards > 2x / 4x median | Cards swapped |
  |------|-----------|---------|--------|-------------|------------|---------|------------------------|---------------|
  | S | minmax (Stage 3 run) | 30.1 s | FEASIBLE | 207 of 231 | 3-22 | 5.30 | 21 / 7 | 31 |
  | S | maxutil | 30.1 s | FEASIBLE | 207 | 2-22 | 5.26 | 19 / 7 | 19 |
  | S | softcap (T = 10) | 30.1 s | FEASIBLE | 207 | 2-23 | 4.54 | 17 / 2 | 23 |
  | S | tiered (T = 10) | 30.1 s | FEASIBLE | 207 | 2-24 | 4.62 | 19 / 2 | 19 |
  | M | minmax (Stage 3 run) | 81.3 s | stopped at gap limit | 1001 of 1113 | 2-101 | 22.66 | 40 / 25 | 36 |
  | M | maxutil | 52.8 s | stopped at gap limit | 1001 | 2-100 | 22.68 | 39 / 24 | 35 |
  | M | softcap (T = 20) | 50.4 s | stopped at gap limit | 1001 | 2-126 | 22.38 | 33 / 20 | 57 |
  | M | tiered (T = 20) | 81.4 s | stopped at gap limit | 1001 | 2-126 | 21.39 | 38 / 23 | 61 |

Notes:

- The S and M runs of `maxutil` and the S run of `softcap` predate the warm-start repair.
  `softcap` on M was run three times: 88.0 s without the repair (std dev 22.16), 102.6 s with
  a first, slower repair version (22.39), 50.4 s with the final one (22.38). The spread shows
  how noisy M timings are; the repair is not shown to make M faster, only to make the hint
  feasible.
- No objective reaches its stop rule on rung L inside 300 s. Every run ends FEASIBLE at the
  limit and sits on the lower edge of the tolerance window (2223 or 2224 combos).
- Two families. `minmax` and `maxutil` push the maximum down to about 155 but end with a
  plateau of cards just under it (18-19 cards above 100) and std dev 32-33. `mad`, `softcap`
  and `tiered` leave one card far out (Kodama of the East Tree, 240-261) but have 7-8 cards
  above 100 and std dev 28.6-29.8. The same ordering of std dev holds on S and M.
- `maxutil` is not better than `minmax` on any rung (same maximum, same std dev within noise,
  same gap), so dropping `min_util` did not help the search.
- Run-to-run spread on L: the two `tiered` runs differ by 0.5 in std dev, the two `minmax`
  runs (different code) by 0.6. The 3-4 points between the families are outside that; the
  differences inside the overage family (28.6-29.8) are not.
- `tiered` run 2, objective over time (overage units, from the solver log): warm start
  10,398; 15 s 6,648; 60 s 5,439; 120 s 4,449; 180 s 3,958; 240 s 3,806; 300 s 3,731; best
  bound 3,133. Most of the improvement happens in the first 3 minutes.
- No "disagree" warning in any log: the model stayed exact.

### Decision: default objective

`tiered` is the default (`--phase2-objective`, `ILPOptimizer`, `run_ilp`, `build_cube_ilp`,
`runner.run`). By the criteria in order:

1. Finishes rung L inside 300 s: no objective does; all are compared at the limit.
2. Combo count within tolerance: all, identical (2223-2224).
3. Lowest std dev: `tiered` (28.57 and 29.06 in two runs) against 32.20-32.83 for `minmax`.
   It is also the best on M (21.39 vs 22.66) and it reaches its stop rule there.
4. Lowest max utilization: `minmax` / `maxutil` (155 vs 255).

So `tiered` wins on the third criterion and loses on the fourth. It is not a win on every
number: its worst card is 100 higher. The distribution is what decided it: 7-8 cards above 100
instead of 19, lower mean (26.0 vs 27.7), lower std dev on every rung. `mad` and `softcap`
are within noise of `tiered` on L; `tiered` was preferred over `mad` because its tiebreak is
clean, it has a usable bound and stop rule (it terminates on M), and it was best on M. Use
`--phase2-objective maxutil` or `minmax` when the single worst card matters most.

### Tolerance sweep

`tiered`, rung L, 300 s limit, one run each (0.1: the two runs above).

  | `--combo-tolerance` | Window | Combos kept | P2 status | Util range | Std dev | Median | Cards > 2x / 4x median | Cards > 100 | Cards swapped |
  |---------------------|--------|-------------|-----------|------------|---------|--------|------------------------|-------------|---------------|
  | 0 | 2471 | 2471 (Phase 1 cube) | INFEASIBLE after 34 s, fell back | 7-348 | 41.85 | 15 | 60 / 27 | 12 | 0 |
  | 0.05 | 2347-2595 | 2347 | FEASIBLE at 304 s, gap 13.8% | 2-284 | 50.11 | 15.5 | 66 / 33 | 20 | 25 |
  | 0.1 | 2223-2719 | 2223 | FEASIBLE at 302 s, gap 16-17.5% | 2-255 | 28.57 / 29.06 | 16 | 67 / 22, 69 / 23 | 7 / 8 | 65 / 51 |
  | 0.2 | 1976-2966 | 1976 | FEASIBLE at 300 s, gap 63.7% | 2-165 | 20.05 | 15 | 60 / 11 | 5 | 125 |

- Tolerance 0 is infeasible with the default coverage and floor constraints: no 300-card cube
  with 2471 combos satisfies them (proven by both the repair model and Phase 2). The run falls
  back to the Phase 1 cube, which breaks 10 coverage constraints.
- Tolerance 0.05 is worse than Phase 1 by std dev. The coverage constraints force all 10
  "Persist Creature" cards into the cube; with 214 persist combos still complete, 9 of the 10
  most-used cards are persist creatures at 214-267. The window is too tight to drop those combos.
- From 0.1 to 0.2 the cube gives up another 247 combos (10% of Phase 1) for std dev 29 -> 20
  and maximum 255 -> 165. The gap at 0.2 is large (63.7%), so 300 s is far from converged there.
- Recommendation: keep the default at 0.1. Below it balancing does not work with the current
  coverage rule; 0.2 is a real trade (20% of the combos for a much flatter cube) that should
  stay a user choice. Default not changed.

### Goal 1

Not met as "Phase 2 finishes": on rung L no objective reaches its stop rule in 300 s. Met in
the weaker sense that the default returns at the 300 s limit with a cube that is clearly more
balanced than Phase 1 (std dev 41.85 -> 28.6-29.1, cards above 100: 12 -> 7-8, maximum
348 -> 255, floor respected) for 10% of the combos, with a 16-17.5% gap left. Against the
Stage 3 result (std dev 32.83) that is an improvement of about 4 points; it is not a
change of kind.

### Finding: what the remaining hubs are

Read from the stats files of the rung L runs:

- Coverage needs `ceil(0.1 x combos using the template)` cards per template, capped at the pool
  size. Template pools hold at most 10 cards
  (`ComboPreprocessor.REQUIREMENT_CARD_LIMIT`), so for "Persist Creature" (326 combos) the
  constraint demands the whole pool: all 10 persist creatures are in every Phase 2 cube.
- Utilization counts a card for every completed combo whose pool contains it (open question 2).
  Each of those 10 cards is therefore counted once per completed persist combo, whichever
  creature the combo actually uses.
- `minmax` / `maxutil` keep 115 / 117 persist combos and the persist creatures sit on the
  plateau (137-154). `tiered` / `mad` cut them to 59 combos (persist creatures at or below 92). At
  tolerance 0.05, 214 persist combos remain and the persist creatures are the hubs.
- The single outlier of the overage family (Kodama of the East Tree, 240-284) is a different
  case: a card required by its combos.

So a large part of what Phase 2 is fighting on rung L is produced by the coverage rule and the
utilization definition together, not by the combo graph. Inference, not tested: changing either
would change the picture more than another objective would.

## Stage 5: Cap search using Phase 1 (optional)

Only if Stage 4 does not meet Goal 1. Phase 1 solves in seconds, so balance can be handled as
hard constraints inside a Phase-1-shaped model and searched from outside:

- [ ] Add an optional hard cap `u[c] <= cap` and the floor to the Phase 1 model.
- [ ] Binary-search the smallest `cap` whose max-combo solution stays within `--combo-tolerance`
      of the uncapped optimum. Each probe is one short solve, warm-started from the previous one.
- [ ] Expose as `--phase2-objective capsearch`; add it to the Stage 4 benchmark table.

Decision after Stage 4: [ ] needed / [x] skipped (2026-10-05). None of the three items above
was built. Reasons, as recorded by Stage 4:

- A hard cap `u[c] <= cap` is not valid in the Phase-1-shaped model: `y` is one-sided there, so
  the solver could meet the cap by not counting combos the cube completes (the Stage 3 bug). A
  capped model needs the exact linking and is then Phase 2 sized, which removes the reason for
  the cap search (cheap probes).
- A cap search minimizes the maximum, and `maxutil` already does that directly: on rung L its
  bound says the maximum cannot go below 138 at tolerance 0.1 and the best found is 155.
- What limits the result is the plateau, not the maximum. The coverage rule forces all 10
  "Persist Creature" pool cards into every Phase 2 cube, and each of them is counted once per
  completed persist combo, whichever creature the combo uses (Stage 4, "Finding"). A cap search
  does not change that.

Goal 1 is therefore closed as "partly met" (see "Outcome"), not by Stage 5. The alternatives
below are carried over to "Follow-ups".

Recommendation from Stage 4 (2026-10-05), not implemented:

- Goal 1 is not met in the strict sense, so Stage 5 is still open. The cap search as written
  above is not what I would build:
  - A cap search minimizes the maximum. `maxutil` already does that directly, and its bound on
    rung L says the maximum cannot go below 138 at tolerance 0.1 (best found: 155). A cap
    search can at most close that 138-155 interval, and the maximum is the one number the
    range family already handles; the std dev problem (the plateau) stays.
  - A hard cap `u[c] <= cap` is not valid in the Phase-1-shaped model: `y` is one-sided there,
    so the solver could meet the cap by not counting combos the cube completes (the Stage 3
    bug). The floor can be written on one-sided `y` (Stage 4 does that in the warm-start
    repair), the cap cannot. A capped model needs the exact linking and is then Phase 2 sized.
- What I would try instead, in this order:
  1. Decide open question 2 and review the coverage rule (see "Finding" in Stage 4): either
     limit the coverage requirement to a share of the pool (so a 10-card pool is not forced in
     whole), or count a pool card only for combos where no other selected card of the pool
     satisfies the template, or weight pool membership by `1 / (selected pool cards)`. This is
     a modelling decision for the owner, not a solver change, and it decides what "balanced"
     means before more solver work is spent on it.
  2. If the definition stays: a cap search in the exact model is still possible as a sequence
     of feasibility solves (`u[c] <= cap`, maximize combos, warm-started from the previous
     probe), but expect each probe to cost like a Phase 2 solve, so only a few probes fit in
     5 minutes. A cheaper variant of the same idea is `tiered` with a hard cap on top
     (`--util-cap` semantics plus `u[c] <= k x T`), which would remove the single outlier.
  3. Time, not model: the `tiered` trajectory flattens after about 3 minutes with a 16% gap.
     If the result at 300 s is acceptable to the owner, Goal 1 can be closed as "good cube at
     the limit" without Stage 5.

## Stage 6: Data layer

Purpose: repeatable offline runs and polite API use. Independent of Stages 2-5; can be done
at any point after Stage 1.

- [x] Persist Scryfall template results to `data/cache/` (keyed by the prepared URL), honoring
      `--read-api-cache` and `--skip-api-caching`.
- [x] One shared `aiohttp.ClientSession` in `ComboPreprocessor` instead of one per request.
- [x] Rate-limit Scryfall calls (about 100 ms between requests) and send a `User-Agent`.
- [x] Share the Scryfall fetch between `ComboPreprocessor` and `greedy/variant_tracker.py`,
      which currently has its own copy.
- [x] Distinguish "Scryfall error" from "no matching cards": today both silently drop the combo.
      Log a count of combos dropped per reason.
- [x] Unit tests with mocked HTTP for cache hit, cache miss, and error paths.

Done when: a second run of rung L makes zero network requests.

## Stage 7: Docs, merge, wrap-up

- [x] Update `README.md` (new flags, new default objective, corrected complexity table).
- [x] Append results to `docs/plans/ilp-performance-investigation.md` or link this plan from it.
      Done as a dated note at the top of that file and of `docs/plans/multi_objective_design_doc.md`,
      with the misleading parts (old method names, "minmax is the default") marked superseded.
- [x] Replace `data/current_best_cube*.{txt,json}` with a fresh rung L result.
- [ ] Open a PR to `main` covering `perf_2025-12-25` plus this work (or two PRs, perf branch
      first). Left to the owner: commits must be GPG-signed. See "Hand-off".

What was done (2026-10-05):

- README brought in line with the code: option list (`--util-cap`, the five
  `--phase2-objective` choices with `tiered` as default, floor for every objective), an
  objective table with the rung L numbers and when to pick which, both API caches and what
  `--read-api-cache` / `--skip-api-caching` cover, the dropped-combo summary line, the
  `evaluate_cube` entry point, the stats JSON contents, measured solve times in place of the
  old estimates, and the note that fewer than 8 workers badly hurts the solver.
- New best cube: `data/current_best_cube.txt` and `data/current_best_cube_stats.json` are
  copies of `data/bench_s4_L_tiered.{txt,_stats.json}` (Stage 4, `tiered` run 1, the one of
  the two default-objective rung L runs with the lower std dev). 300 cards, 2223 combos,
  utilization 2-255, mean 25.98, median 16, std dev 28.57, Phase 2 FEASIBLE at 302 s.
  Re-checked with `evaluate_cube ... -n 10000` after copying: 2223 combos, min 2, max 255,
  std dev 28.57, as recorded. For comparison, the replaced December 2025 cube scores 2016
  combos, utilization 0-223, std dev 26.90 on today's instance, and one of its cards (Dockside
  Extortionist) is no longer in the instance; its stored stats (2185 combos) were measured on
  the December instance with the model that could under-report.
- Whole-change review. Fixed: the `mad` objective's log/profile label was "Phase 2" while the
  other four were "Phase 2 (<name>)", now "Phase 2 (mad)"; the `--gap-limit` help text now
  says how the limit is measured for `softcap` / `tiered`; added
  `test_defaults_agree_on_every_layer` (`tests/unit/test_runner.py`), which compares the
  default values of the CLI, `runner.run`, `run_ilp`, `build_cube_ilp` and
  `ILPOptimizer.__init__`. Checked and found in order: defaults agree on all layers
  (objective `tiered`, tolerance 0.1, coverage 0.1, gap 0.05, floor 2, workers 8, time limit
  300, variants 10,000); no scratch or comparison scripts in the tree (there is no `scripts/`
  directory; the clause-form comparison code from Stage 3 is gone); no debug logging left on
  (the CP-SAT log is tied to `-d`); no test without an assertion; `.gitignore` already
  ignores everything under `data/` except the blocklist and the best cube, so the benchmark
  and cache files need no new rule. Larger items are in "Follow-ups" (h).
- Smoke tests on the final code (rung S):
  - `uv run python -m src.mtg_combo_cube -c 100 --method ilp -o data/smoke_S.txt -t 30 -n 1000
    --profile --read-api-cache`: 31 s wall time, no WARNING or ERROR lines, 0 network
    requests (24 templates from cache), Phase 1 OPTIMAL 231 combos, warm start repaired in
    0.1 s, Phase 2 `tiered` (T = 10) FEASIBLE at the 30 s limit, 207 combos, utilization
    2-50 -> 2-24, std dev 6.66 -> 4.70. Cube and stats files written.
  - `uv run python -m src.mtg_combo_cube -c 100 --method greedy -o data/smoke_greedy.txt
    -n 1000 --read-api-cache`: 37 s, no WARNING or ERROR lines, 100 cards, 211 combos. This
    run used the live Commander Spellbook API (greedy has no Spellbook cache).
- `task check`: clean; see "Hand-off" for the final numbers.

## Open questions

The three questions from the start of the plan, with what was learned (2026-10-05):

- Is balancing still the right secondary goal, or would a hand-chosen per-card cap
  (for example "no card in more than 60 combos") be enough? If so, Stage 5's hard cap without the
  search is the whole feature.
  - **Still open (owner's call).** Learned: a hard cap has to live in the exact (Phase 2
    sized) model, not in Phase 1, so it is not the cheap feature this question assumed. A cap
    of 60 would be infeasible at tolerance 0.1 on rung L: the `maxutil` bound shows no cube
    there has a maximum below 138. `--util-cap` now gives the soft version (`softcap` /
    `tiered` penalize utilization above the cap).
- Utilization currently counts a card for every completed combo whose template pool contains it,
  even when a different card is the one satisfying that template. Keep that definition?
  - **Still open, and now the most important one.** See "Follow-ups" (a).
- Should `--workers` default below 8 until the machine has proven stable?
  - **Answered: no, keep 8.** With 4 workers Phase 1 could not prove optimality on any rung
    inside the time limit (8 workers: OPTIMAL in about 5 s on rung L) and Phase 2 found no
    solution on M and L. All runs of 2026-10-05 at 8 workers completed without instability.

## Follow-ups

Unresolved items found along the way. None of them blocks the merge.

Owner decisions:

- (a) **Utilization definition and coverage rule (open question 2). Probably the biggest
  lever.** Utilization counts a card for every completed combo whose template pool contains it.
  The coverage rule (Phase 2 only) demands `ceil(0.1 x combos using the template)` cards per
  template, capped at the pool size, and pools hold at most 10 cards. For "Persist Creature"
  (326 combos) that is the whole pool, so all 10 persist creatures are in every Phase 2 cube
  and each is counted once per completed persist combo, whichever creature the combo uses.
  That produces the plateau under `minmax` / `maxutil` and the hubs at tolerance 0.05. Options
  noted in Stage 5: limit the coverage requirement to a share of the pool; count a pool card
  only when no other selected card of the pool satisfies the template; weight pool membership
  by `1 / (selected pool cards)`. That either would change the picture more than another
  objective is an inference from the stats files; it has not been tested.
- (c) **Default objective.** The default was changed from `minmax` to `tiered`. On rung L that
  lowers the std dev (Phase 1 41.85 -> about 29, against about 32-33 for `minmax`) but leaves
  the single worst card higher (255 against 155). Reverting is a change of four default strings
  (`__main__.py`, `runner.run`, `run_ilp` / `build_cube_ilp`, `ILPOptimizer.__init__`; the
  test `test_defaults_agree_on_every_layer` keeps them in step) plus the README.
- Goal 1: accept "good cube at the 300 s limit" or spend more on it (Stage 5 list: a few
  cap probes in the exact model, or `tiered` with a hard cap on top to remove the outlier).
- `--combo-tolerance 0.2` gives std dev 20 for 20% of the combos (one run, far from
  converged); the default stays 0.1.

Technical:

- (b) Coverage constraints exist only in Phase 2. The Phase 1 cube breaks 1 / 4 / 10 of them
  on S / M / L, tolerance 0 is infeasible at full size (the run falls back to the Phase 1
  cube), and the upper edge of the combo window would cut off better cubes after a FEASIBLE
  Phase 1. Adding coverage (and the one-sided floor) to Phase 1 would make the two phases
  agree; not tried.
- (d) The profile reports `branches: 0` for Phase 2 in 10 of the 12 benchmark runs in which
  the warm start was repaired (all rungs; the exceptions are `mad` on L and the infeasible
  tolerance 0 run), in the Stage 7 smoke run, and so also in the stats of the new best cube.
  It is never 0 in the 16 Stage 3 / Stage 4 / baseline v2 runs without a repair. The objective
  clearly improves during those solves, so the search is running. Not investigated: the
  correlation with the feasible hint is a fact, the cause is unknown. Do not use the branch
  count to compare Phase 2 runs until this is understood.
- (e) The `mad` target is a truncated Phase 1 mean (`int(mean * 100)`), fixed before Phase 2,
  although Phase 2 changes the mean (27.4 -> about 26 on rung L). Its primary term is scaled
  by 100 only, so its versatility tiebreak is not guaranteed to be a pure tiebreak.
- (f) Evidence is thin: every rung L number is from one or two runs, 8-worker runs are not
  deterministic, and the observed spread is about 0.5 std dev on L and tens of seconds on M (50-103 s for three
  `softcap` runs). The
  linear-over-clause choice in Stage 3 rests on two S runs each and one M run that pointed the
  other way.
- (g) Stability: all solver runs of 2026-10-05 (BIOS 1836, microcode 0x133) completed without
  instability, including about a dozen 300 s runs at 8 workers. Nothing longer than about
  5 minutes per phase was run.
- (h) Code left as it is, on purpose (small, but beyond "safe" for a wrap-up):
  - `ILPOptimizer._calculate_utilization` and `_compute_utilization_stats` duplicate
    `cube_evaluation.card_utilization` / `compute_utilization_stats` (a test asserts the first
    pair agree); `_coverage_violations` restates the rule of `_add_coverage_constraints`.
  - When the warm start is repaired, the coverage constraints are built twice, so the
    "Cannot meet minimum coverage for 'Persist Creature'" warning and the "Added N coverage
    constraints" line appear twice per run. Expected, not a second problem.
  - `ProfileResult.log_summary` prints "Constraints: N total", but N also contains the
    variable and hint counts.
  - Greedy ignores `-n` (it always requests 10,000 variants; `task build:greedy` passes `-n`
    without effect) and has no Spellbook cache. `run_greedy` / `build_cube` default
    `enable_cache_write` to `False` while the ILP functions default to `True`; the CLI always
    passes the value, so behavior is the same.
  - `profiling.timed_section` is unused (it predates this work).
  - `task build:ilp` defaults to `TIME_LIMIT=360` while the CLI default is 300 (set in an
    earlier commit by the owner; documented in the README).
  - `docs/plans/ilp_design_doc.md` has no "historical" note; its class sketch is the original plan,
    not the current code.

## Decision log

| Date | Decision | Reason |
|------|----------|--------|
| 2026-10-05 | Plan created; continue from `perf_2025-12-25` | That branch holds the unmerged profiling and minmax work |
| 2026-10-05 | Stage 0 baseline taken; benchmark later stages with `--workers 8`, not 4 | Phase 1 was FEASIBLE at the time limit with 4 workers on every rung but OPTIMAL in 4 s with 8 (CP-SAT's subsolver portfolio depends on worker count); Phase 2 found no solution on M and L with 4 workers. All four runs were stable after the BIOS update |
| 2026-10-05 | Stage 6 moved ahead of Stages 2-5 so benchmarks run on a fixed instance | Stage 0 runs lost 44-116 template requests to Scryfall HTTP 429, silently dropping combos, so every run solved a slightly different instance |
| 2026-10-05 | Stage 3: all reported numbers are computed from the selected cards (`cube_evaluation.py`), never from solver `y` | Phase 2's one-sided `y` let the solver hide completed combos; baseline v2 under-reported 1 / 1 / 21 combos and 1 / 1 / 11 of range on S / M / L |
| 2026-10-05 | Stage 3: Phase 2 `y` made exact with shared option variables `z`, linear form; Phase 1 left one-sided | Needed for correct utilization. Linear proved rung S optimal in 2 of 2 comparison runs, clause in 0 of 2; rung M (one run each) favored clause, so the evidence is weak. Phase 1 maximizes `y`, so it does not need the lower bound |
| 2026-10-05 | Stage 3: keep `u[c]` as a variable that is 0 for unselected cards; plain equality for required-only cards, enforced pair for option-pool cards | "0 if unselected" removes the big-M on the max side (`max_util >= u[c]`) and lets Stage 4 objectives use `u` without conditioning on `x` |
| 2026-10-05 | Stage 3: floor is a per-card constraint added by the Phase 2 driver for every objective; an infeasible floor falls back to Phase 1 with `phase2_fell_back` set | `mad` ignored the floor and could select dead cards; a silent fallback was indistinguishable from a single-phase run |
| 2026-10-05 | Stage 3: accept the slower rung M (81 s vs 19 s to the 5% gap) | The old time came from a model that could hide combos; rung L improved (true range 222 -> 152, gap 42.9% -> 9.5%) |
| 2026-10-05 | Stage 4: default Phase 2 objective is `tiered` (softcap counted again above 2T and 4T), `T` = 2 x Phase 1 median | Rung L at 300 s: std dev 28.6 / 29.1 (two runs) vs 32.2-32.8 for `minmax`, 7-8 cards above 100 vs 19; best on M too and it reaches its stop rule there. Its worst card is higher (255 vs 155), so `minmax` / `maxutil` stay for users who care about the maximum. `mad` and `softcap` are within noise of `tiered` on L |
| 2026-10-05 | Stage 4: `softcap` / `tiered` stop on an absolute gap of `gap_limit` x the Phase 1 cube's overage | Their optimum can be near 0 and the objective turns negative when only the tiebreak is left, so a relative gap is not a usable stop rule |
| 2026-10-05 | Stage 4: Phase 2 warm start is repaired in the Phase 1 model when the Phase 1 cube breaks coverage or the floor | The Phase 1 cube broke 1 / 4 / 10 coverage constraints on S / M / L and CP-SAT rejected the hint as infeasible; after the repair (0.1-12 s) the hint is accepted on M and L |
| 2026-10-05 | Stage 4: keep `--combo-tolerance` at 0.1 | Rung L with `tiered`: 0 is infeasible with coverage and floor (falls back to Phase 1), 0.05 ends less balanced than Phase 1 (std dev 50.1), 0.2 gives std dev 20.1 but costs 20% of the combos |
| 2026-10-05 | Stage 4: Stage 5 cap search not recommended as written | The cap is not valid in the Phase-1-shaped model (one-sided `y`), and `maxutil` already bounds the maximum at 138-155; the remaining hubs come largely from the coverage rule forcing whole 10-card pools plus the pool-membership utilization definition (open question 2), which should be decided first |
| 2026-10-05 | Stage 5 (cap search) skipped | A hard cap is not valid in the Phase-1-shaped model (one-sided `y` could hide completed combos), and in the exact model each probe costs like a Phase 2 solve; `maxutil` already pins the maximum (bound 138, best found 155 on rung L); the plateau comes from the coverage rule forcing all 10 "Persist Creature" pool cards in, each counted once per completed persist combo |
| 2026-10-05 | Stage 7: `data/current_best_cube*` replaced by the Stage 4 `tiered` run 1 on rung L (`data/bench_s4_L_tiered.*`) | The lower std dev of the two default-objective rung L runs (28.57 vs 29.06); re-scored with `evaluate_cube`: 2223 combos, utilization 2-255 |
| 2026-10-05 | Open question 3 answered: `--workers` stays at 8 | 4 workers cannot prove Phase 1 optimal and finds no Phase 2 solution on M and L; no instability at 8 workers in any run of the day |

## Hand-off

State on 2026-10-05, end of Stage 7. Nothing is committed: `ilp-improvements` points at the
same commit as `perf_2025-12-25` (`5572075`) and all work of this plan is in the working tree.
An early version of this file is staged (`AM` in `git status`); `git add` replaces it.

Checks on the final tree: `task check` clean (ruff, ruff format, ty with zero diagnostics,
289 tests pass; 288 before Stage 7). Smoke tests: see Stage 7.

Merging: `ilp-improvements` contains all of `perf_2025-12-25` (6 commits ahead of `main`,
0 behind), so it can merge straight to `main`; no separate PR for the perf branch is needed.

Changed and new files, by theme (the stage that mainly owns each file):

| Theme | Files |
|-------|-------|
| Stage 0: worker count | `Taskfile.yml`; `--workers` in `src/mtg_combo_cube/__main__.py`, `runner.py`, `ilp/ilp_runner.py`, `ilp/ilp_optimizer.py` |
| Stage 1: housekeeping | `ilp/ilp_optimizer.py` (lint, typing, clocks); `README.md` corrections |
| Stage 6: Scryfall data layer | new `src/mtg_combo_cube/scryfall/` (`__init__.py`, `scryfall_fetcher.py`); `ilp/combo_preprocessor.py`, `greedy/variant_tracker.py`, `greedy/greedy_runner.py`; cache flags to greedy in `runner.py`; fetcher wiring in `ilp/ilp_runner.py`; tests: new `tests/unit/scryfall_fakes.py`, `test_scryfall_fetcher.py`, `test_variant_tracker.py`, changed `test_combo_preprocessor.py` |
| Stage 2: refactor | `ilp/ilp_optimizer.py`; new `tests/unit/test_ilp_optimizer_characterization.py` |
| Stage 3: exact Phase 2 and true numbers | new `ilp/cube_evaluation.py`, `ilp/evaluate_cube.py`; `ilp/ilp_optimizer.py`, `ilp/ilp_models.py` (`phase2_fell_back`), `ilp/profiling.py` (gap), `ilp/ilp_runner.py` (`load_instance`, fallback in the stats file); tests: new `test_cube_evaluation.py`, `test_profiling.py`, changed `test_ilp_optimizer.py`, `test_ilp_runner.py` |
| Stage 4: objectives | `ilp/ilp_optimizer.py`, `ilp/ilp_models.py` (`phase2_objective`, `phase2_util_cap`), `__main__.py`, `runner.py`, `ilp/ilp_runner.py`; tests: new `test_ilp_phase2_objectives.py`, `test_runner.py` |
| Stage 7: docs and wrap-up | `README.md`, `docs/plans/ilp-improvement-plan.md` (new), `docs/plans/ilp-performance-investigation.md`, `docs/plans/multi_objective_design_doc.md`, `data/current_best_cube.txt`, `data/current_best_cube_stats.json`; small edits in `ilp/ilp_optimizer.py` (label), `__main__.py` (help text), `tests/unit/test_runner.py` |

Not for commit (ignored by `.gitignore`): `data/bench_*`, `data/smoke_*`, `data/cache/`.

Suggested commits, in this order, one per stage:

| # | Stage | Message |
|---|-------|---------|
| 1 | 0 | Add `--workers` option and WORKERS task variable |
| 2 | 1 | Fix lint and type warnings, mixed clocks, and README errors |
| 3 | 6 | Add shared Scryfall fetcher with disk cache, rate limiting and retries |
| 4 | 2 | Refactor ILP optimizer into one shared model builder and one Phase 2 driver |
| 5 | 3 | Make Phase 2 combo linking exact and report true cube statistics |
| 6 | 4 | Add maxutil, softcap and tiered Phase 2 objectives; default to tiered |
| 7 | 7 | Update docs and best cube for the ILP improvement work |

Caveat on the split: the stages were not committed as they landed, so the working tree holds
only the final state. `ilp_optimizer.py`, `ilp_runner.py`, `__main__.py`, `runner.py`,
`ilp_models.py` and `README.md` were each changed by several stages; splitting them needs
hunk-level staging (`git add -p`), and the intermediate commits produced that way have not
been run through `task check`. Files owned by one stage (the `scryfall/` package, the
evaluation modules, most test files) can be staged whole. If a clean per-stage history is not
worth that effort, one commit for the whole change, or two (code and tests; docs and data), is
the safe alternative: the final tree is the only state that was verified.

Before merging, the owner may want to look at: the default objective ("Follow-ups" (c)), the
replaced best cube (Stage 7), and open question 2 ("Follow-ups" (a)), which is a decision for
after the merge rather than a blocker.
