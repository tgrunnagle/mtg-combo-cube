# Archetype Support Plan

Written October 2026. Guarantee that every two-color pair, and every mono color, has combos to
draft. Second of four playability plans; see
[combo-grouping-plan.md](combo-grouping-plan.md) (first),
[card-mix-plan.md](card-mix-plan.md) and [combo-variety-plan.md](combo-variety-plan.md).

## Context for a new session

- Read the "Context for a new session" section of
  [combo-grouping-plan.md](combo-grouping-plan.md) for where the optimizer, preprocessor,
  evaluation, stats and tests live and how to run a full-size build.
- Phase 2 hard constraints today: cube size, combo window, coverage
  (`_add_phase2_coverage`), color balance (`_add_color_balance`), utilization floor. Every
  Phase 2 constraint that defines feasibility appears in three places, and a new one must
  too:
  1. `_solve_phase2`, on the full Phase 2 model;
  2. `_repair_model`, the small model used by `_best_constrained_cube` (which finds the
     reference cube the combo window is measured from) and `_repair_floor` (the warm start);
  3. a violation check used by `_build_warm_start` to decide whether the Phase 1 cube already
     satisfies the rules (`_coverage_violations`, `_color_violations`).
  The color balance work in [color-balance-plan.md](color-balance-plan.md) is the template:
  constructor arguments, `_add_color_balance(base)` setting `base.counts["color_balance"]`
  only when constraints are added, `_color_violations(cards)`, the flag, the stats field, and
  `tests/unit/test_ilp_color_balance.py`.
- The combo window is measured from the reference cube, the best cube found under coverage
  and color balance in 10% of the time limit (`WARM_START_MAXIMIZE_FRACTION`). New hard
  constraints lower that reference, and the floor repair gets 20%
  (`WARM_START_FLOOR_FRACTION`). At 270 cards with ratio 1.5 the default 300 s limit already
  fails and 600 s is needed, so each added rule should be measured for its cost.

## Problem

Card color balance does not mean a drafter in a color pair has combos to assemble. The
combos the current best cube completes (template-free variants, by Spellbook `identity`):

| Identity | Completed variants |
|---|---|
| Two-color pairs | WB 365, GW 345, GU 261, BG 225, RG 67, BR 67, RW 39, UR 21, WU 18, UB 10 |
| Mono | G 259, B 194, W 113, U 56, R 17 |
| Colorless | 6 |
| Three colors | 702 |
| Four or five colors | 108 |

A UB, WU or UR drafter has almost nothing, and 28% of the completed variants need three or
more colors, which are rarely assembled in a draft. The pool is not the limit: the cached
20,000 variants have 535 to 1,293 variants per pair (GU 1,293, WB 1,232, BG 1,038, GW 918,
UR 875, WU 855, BR 769, RW 698, RG 548, UB 535), 6,725 mono-colored and 493 colorless.

## Decisions to make

| Question | Recommendation |
|----------|----------------|
| What counts for a pair | A completed combo whose identity is a subset of the pair: the pair's gold combos plus both mono colors plus colorless. Mono and colorless combos count for every pair they fit. |
| Unit | Distinct combo groups if [combo-grouping-plan.md](combo-grouping-plan.md) has landed, otherwise variants. The flag values below assume groups; in variants multiply by roughly 3. |
| Hard or soft | Hard minimum per pair, like coverage and color balance. A soft version is possible later as a penalty term. |
| Mono colors too | Yes, a separate smaller minimum, since mono combos are what make a color draftable at all. |
| Wide combos | A cap on the share of completed combos with three or more colors, as a separate flag. |
| Identity source | `Variant.identity` as given by Spellbook. Caveat: it covers the `uses` cards; a template requirement filled by a colored card can add a color the identity does not show. Accept this. |

## Step 1: measure

Before adding constraints, find what the pool allows. In the Phase 1 model
(`_build_base_model` plus `_add_phase2_coverage` and `_add_color_balance`), add a variable
`m` and `sum(y over combos fitting pair P) >= m` for all ten pairs, maximize `m` with a
120 s limit at 300 cards and 20,000 variants. Repeat for mono colors. Record the best `m` and
the combo count of that cube, to pick defaults that cost little. The scratchpad experiments
for the color plan were written as a short script constructing `ILPOptimizer` from
`load_instance` in `ilp_runner.py` and calling the private model builders; do the same.

## Step 2: data

1. `ComboData` gets `color_identity: str` (WUBRG letters, empty for colorless), set in
   `ComboPreprocessor._process_single_variant` from `variant.identity` ("C" means colorless
   and should become ""). Default "" for tests that build `ComboData` directly.
2. `cube_evaluation.py`: `combos_per_archetype(selected_cards, combos) -> dict[str, int]`
   over the ten pairs, the five mono colors and "C", and `combos_by_identity_size`.
3. `OptimizationResult` and `write_stats`: an `archetypes` block per phase with those counts;
   `log_phase_summary` prints the pair counts on one line; `evaluate_cube` prints them.

## Step 3: constraints

1. Constructor arguments `min_pair_combos: int = 0`, `min_mono_combos: int = 0`,
   `max_wide_combo_share: float = 0` (0 disables each). Defaults set from Step 1.
2. `_add_archetype_minimums(base)`: for each pair `P`, `sum(base.y[j] for j fitting P) >=
   min_pair_combos`; same for mono colors with `min_mono_combos`. In group units (plan 1) the
   sum is over group variables. Sets `base.counts["archetype_minimum"]` when added.
3. `_add_wide_combo_cap(base)`: `sum(y over combos with 3+ colors) <= share * sum(y)` written
   as an integer inequality (`den * wide <= num * total` with a `Fraction`, as
   `_add_color_balance` does for the ratio).
4. `_archetype_violations(cards)` for `_build_warm_start`, and both constraints added in
   `_repair_model` and `_solve_phase2`. Logged next to the coverage and color lines.
5. Flags `--min-pair-combos`, `--min-mono-combos`, `--max-wide-combo-share`, plumbed through
   `__main__.py`, `runner.py`, `run_ilp`, `build_cube_ilp`, with the stats `phase2` block
   recording the values applied.
6. Infeasible settings fall back to the Phase 1 cube like every other Phase 2 failure; the
   log must say which minimum could not be met. A cheap pre-check: if the pool has fewer
   fitting combos than the minimum for some pair, warn before solving.

## Step 4: refactor the constraint plumbing (optional, recommended)

This plan and [card-mix-plan.md](card-mix-plan.md) each add two or three rules to the three
places listed above. A small registry would stop the duplication: a list of Phase 2 cube
rules, each with `add(base)` and `violations(cards)`, iterated by `_solve_phase2`,
`_repair_model` and `_build_warm_start`. Do it in whichever of the two plans is implemented
first, keeping the existing method names as the implementations.

## Tests

Small instance: one two-card combo per pair plus a white triangle (reuse the shape of
`tests/unit/test_ilp_color_balance.py`), with identities set on the `ComboData`. Cases: the
minimum forces a combo into an otherwise ignored pair; the wide cap excludes a three-color
combo; 0 disables; infeasible minimum falls back; `_archetype_violations` counts. Plumbing
tests in `tests/unit/test_runner.py` and stats tests in `tests/unit/test_ilp_runner.py`.

## Verification

Full run at the chosen defaults against the tracked best cube: combos per pair (the table
above is the baseline), total combos, utilization, colors, Phase 2 time. Record the results
here and update `README.md` and `docs/architecture.md` (Phase 2 constraint list, flags,
stats file).

## Open questions

- Whether a minimum per pair should scale with cube size (a share of the window rather than
  a count).
- Three-color combos are a large part of the pool (3,377 of 20,000) and of what the
  optimizer likes. A cap on them will cost combos; the measurement in Step 1 should include
  the cap.
- Draft balance might be better expressed as "every card has a partner in at least two
  pairs", which is a per-card rule rather than a per-pair one. Deferred.
