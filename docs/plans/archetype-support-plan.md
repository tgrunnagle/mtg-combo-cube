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

## Results (6 October 2026)

### Step 1: measurement

Phase 1 style model plus coverage and color balance (`_build_base_model`, `_add_cube_rules`),
300 cards, the cached 20,000 variants (19,848 after preprocessing, 8,662 groups), 120 s per
solve, 8 workers, `variant_weight` 0.1. No solve proved optimality, so the differences
between rows include solver noise of a few percent. "Lowest pair" is the pair with the
fewest fitting distinct combos (gold plus mono plus colorless); "wide" is the share of the
completed distinct combos needing three or more colors.

| Setting | Variants | Distinct combos | Weighted | Lowest pair | Lowest mono | Wide |
|---|---|---|---|---|---|---|
| Baseline (coverage + color only) | 2,119 | 1,380 | 1,453.9 | 300 | 199 | 22.0% |
| Maximize the lowest pair | 1,546 | 958 | 1,016.8 | 433 | 322 | 2.0% |
| Maximize the lowest mono | 1,192 | 824 | 860.8 | 460 | 362 | 0.6% |
| Pair >= 216, mono >= 181 | 2,042 | 1,351 | 1,420.1 | 318 | 217 | 23.9% |
| Pair >= 325, mono >= 181 | 2,039 | 1,314 | 1,386.5 | 353 | 231 | 22.5% |
| Wide cap 25% alone | 2,099 | 1,368 | 1,441.1 | 301 | 194 | 21.3% |
| Pair >= 216, mono >= 181, wide cap 25% | 2,275 | 1,354 | 1,446.1 | 324 | 209 | 23.4% |

Pool, in distinct combos with at least one fitting variant: pairs 1,500 (WG) to 2,361 (UG),
mono 732 (W) to 1,222 (U), colorless 292; 1,839 groups have only variants of three or more
colors; 526 groups have variants of differing identity.

- With combo grouping (`variant_weight` 0.1) the problem in the table at the top is mostly
  gone: the unconstrained cube already gives every pair at least 300 distinct combos and
  every mono color at least 199. That table was the variant-counting cube.
- Pair minimums up to about 300 and mono minimums up to about 200 cost nothing
  measurable. 325 per pair costs about 5% of the weighted count; the maximum (433) costs
  30%, and maximizing the lowest mono color drives the cube to mono and colorless combos.
- The 25% wide cap is not binding; the pool is 21% wide-only groups and the optimizer
  completes them at about that rate. Raising the pair minimum pushes wide combos out by
  itself (2% at the maximum).

### Decisions

- Defaults `--min-pair-combos 250`, `--min-mono-combos 150`, `--max-wide-combo-share 0.25`:
  a guarantee at about 80% of what the unconstrained cube gives, binding only when a run
  drifts, at no measured cost.
- Identity: `Variant.identity` normalized to WUBRG order; "C" becomes "". A group counts
  for an archetype through any completed variant that fits, so groups of mixed identity
  get one extra bool per archetype they only partly fit (`_fitting_group_count`).
- Step 4 (the rule registry) is done: `_cube_rules` lists coverage, color balance, the
  archetype minimums and the wide cap as `(add, violations)` pairs, applied by
  `_solve_phase2`, `_repair_model` and `_build_warm_start`.
- Group variables now exist whenever an archetype rule is enabled, also at
  `variant_weight` 1, where they play no part in the objective.

### Verification

Full runs at the defaults (300 cards, 20,000 variants, 300 s per phase, 8 workers, `tiered`,
`variant_weight` 0.1), compared with the tracked best cube from the combo grouping plan,
scored with `evaluate_cube`. "Lowest pair" and "lowest mono" are the archetypes with the
fewest fitting distinct combos. Phase 1 and Phase 2 hit their time limits in both runs.

| | Tracked cube (before) | First run, reference solve at 10% | Second run, reference solve at 20% (new tracked cube) |
|---|---|---|---|
| Phase 1 variants / combos / weighted | | 2,498 / 1,514 / 1,612.4 | 2,432 / 1,523 / 1,613.9 |
| Phase 1 cube breaks | | 11 coverage, BR 233, RG 234, R 125, wide 27% | 13 coverage, BR 229, B 149, R 143 |
| Reference variants / combos / weighted | 2,391 / 1,392 / 1,491.9 | 1,757 / 1,081 / 1,148.6 | 2,148 / 1,257 / 1,346.1 |
| Warm start preparation | 64 s | 66 s | 81 s |
| Final variants / combos | 1,913 / 1,279 | 1,414 / 991 | 1,771 / 1,149 |
| Lowest pair / lowest mono | BR 196 / B 129 | UB 252 / B 185 | UR 264 / U 182 |
| Pairs (WU WB WR WG UB UR UG BR BG RG) | 342 261 331 298 271 265 381 196 282 246 | 317 289 374 285 252 303 281 285 295 313 | 334 369 375 339 301 264 315 308 368 306 |
| Mono (W U B R G) / colorless | 172 183 129 130 169 / 78 | 206 198 185 221 199 / 138 | 226 182 223 190 213 / 125 |
| 3+ color combos | 292 (23%) | 196 (20%) | 224 (19%) |
| Utilization min-max, std | 2-259, 25.5 | 2-173, 18.8 | 2-238, 26.1 |
| Colors W U B R G | 63 65 53 47 77 | 54 46 35 59 53 | 61 49 53 52 62 |

The first run lost 22% of the distinct combos. The reference repair, hinted with the Phase 1
cube, found only 1,081 combos in its 30 s. Rerunning that repair on the same Phase 1 cube
(`_best_constrained_cube` with other time budgets and rule subsets):

| Repair | Variants / combos / weighted | Lowest pair / mono | Wide | Cards swapped |
|---|---|---|---|---|
| Defaults, 30 s | 1,631 / 1,081 / 1,136.0 | 327 / 273 | 21% | 139 |
| Defaults, 60 s | 2,304 / 1,369 / 1,462.5 | 270 / 162 | 24% | 43 |
| Defaults, 120 s | 2,252 / 1,378 / 1,465.4 | 263 / 155 | 22% | 41 |
| No wide cap, 30 s | 2,337 / 1,368 / 1,464.9 | 260 / 155 | 24% | 41 |
| No minimums, 30 s | 2,418 / 1,390 / 1,492.8 | 217 / 123 | 25% | 26 |
| No archetype rules, 30 s | 2,408 / 1,401 / 1,501.7 | 204 / 107 | 26% | 23 |

Code review then found that the wide cap was one-sided in the repair models: `y` is only
bounded from above there, so the solver could meet the cap by leaving `y` at 0 for wide
combos the cube still completes, and the "defaults" rows above are partly that. The fix
links the variants of every wide-only group exactly in the cap (`_add_exact_combo_linking`
on a subset), and the repair results are now checked against the true completions. The same
Phase 1 cube, repaired with the exact cap:

| Repair (exact cap) | Variants / combos / weighted | Lowest pair / mono | Wide | Cards swapped |
|---|---|---|---|---|
| Defaults, 30 s (two runs) | 1,831 / 1,098 / 1,171.3 and 2,126 / 1,214 / 1,305.2 | 324 / 226 and 307 / 192 | 9%, 13% | 120, 87 |
| Defaults, 60 s | 2,274 / 1,390 / 1,478.4 | 253 / 154 | 23% | 38 |
| Defaults, 120 s | 2,374 / 1,374 / 1,474.0 | 261 / 157 | 23% | 39 |
| Defaults, 240 s | 2,176 / 1,363 / 1,444.3 | 304 / 188 | 22% | 68 |
| No wide cap, 60 s | 2,310 / 1,384 / 1,476.6 | 255 / 152 | 24% | 38 |
| No minimums, 60 s | 2,102 / 1,334 / 1,410.8 | 297 / 182 | 21% | 88 |
| No archetype rules, 60 s | 2,386 / 1,405 / 1,503.1 | 196 / 105 | 28% | 23 |

- With 30 s the exact cap still leaves the repair short (it swaps 90 to 120 cards and
  overshoots to 9-13% wide); with 60 s it costs nothing against no cap. The archetype
  minimums cost about 2% of the weighted count, and they bind: without them the lowest pair
  falls to about 200 and the lowest mono to about 105.
- **Change:** `WARM_START_MAXIMIZE_FRACTION` is 0.2 (60 s at the default time limit). The
  warm start preparation then takes about 80 s of the Phase 2 limit.
- The two full runs in the table above were made before the cap fix. Their final cubes are
  valid, because the Phase 2 model links every variant exactly, and the second run's cube
  meets every rule (wide 19%); only the reference it was measured from may have been a
  little high.
- The second run keeps 10% fewer distinct combos than the tracked cube (1,149 against
  1,279) for a lowest pair 35% higher and a lowest mono 41% higher. Part of the gap is the
  reference solve: on the first run's Phase 1 cube the 60 s repair reached 1,369 combos,
  on the second run's 1,257, so run-to-run variation of about 100 combos sits on top of the
  measured 2.5% cost. Utilization is unchanged (std 26.1 against 25.5, maximum 238 against
  259).
- Every pair ends well above the minimum (lowest 264 against 250) because the minimums
  shape the reference cube and Phase 2 keeps the shape; the mono minimum is the one that
  binds in the repair (B 149, R 143 in the Phase 1 cube).
- `data/current_best_cube.txt` and its stats file are replaced by the second run.

## Open questions

- Whether a minimum per pair should scale with cube size (a share of the window rather than
  a count). *Still open, and more pressing now that the defaults are non-zero:* a 100-card
  run from 1,000 variants cannot reach 250 combos per pair and falls back to Phase 1 unless
  the minimums are lowered or disabled. A share of the completed distinct combos per pair
  (the wide cap's form, `den * fitting >= num * total`) would scale with everything.
- Three-color combos are a large part of the pool (3,377 of 20,000) and of what the
  optimizer likes. A cap on them will cost combos; the measurement in Step 1 should include
  the cap.
- Draft balance might be better expressed as "every card has a partner in at least two
  pairs", which is a per-card rule rather than a per-pair one. Deferred.
