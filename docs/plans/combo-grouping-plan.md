# Combo Grouping Plan

Written October 2026. Make the optimizer count distinct combos instead of Commander Spellbook
variants. First of four playability plans; see also
[archetype-support-plan.md](archetype-support-plan.md),
[card-mix-plan.md](card-mix-plan.md) and
[combo-variety-plan.md](combo-variety-plan.md). This one goes first because it changes what
every combo count, utilization number and combo window means.

## Context for a new session

- The optimizer is `src/mtg_combo_cube/ilp/ilp_optimizer.py` (`ILPOptimizer`). Phase 1
  maximizes the number of completable combos (`_add_combo_count_objective`, popularity as a
  tiny tiebreaker). Phase 2 (`_solve_phase2`) balances card utilization under a combo window
  (`_combo_count_window`), coverage constraints, color balance, a utilization floor and the
  `tiered` objective. `docs/architecture.md` describes the current system.
- Variants are downloaded by `src/mtg_combo_cube/spellbook/commander_spellbook.py`, parsed
  into `Variant` (`src/mtg_combo_cube/models.py`) and converted to `ComboData`
  (`src/mtg_combo_cube/ilp/ilp_models.py`) by `src/mtg_combo_cube/ilp/combo_preprocessor.py`.
  Each `ComboData` is one variant: `id`, `required_cards`, `requirement_options`,
  `popularity`.
- Ground-truth evaluation of a cube (which combos it completes, utilization) lives in
  `src/mtg_combo_cube/ilp/cube_evaluation.py`. Stats files are written by `write_stats` in
  `src/mtg_combo_cube/ilp/ilp_runner.py`; `src/mtg_combo_cube/ilp/evaluate_cube.py` evaluates
  an existing cube file.
- Full-size runs: `uv run python -m mtg_combo_cube -c 300 --profile --read-api-cache
  --workers 8`, about 5.5 minutes with the default 20,000 variants and 300 s per phase. The
  variant cache is `data/cache/variants_cards4_max20000.json`; a fresh download takes about
  13 minutes because Spellbook rate-limits. Validate with `task check`.
- Tests follow `tests/unit/test_ilp_color_balance.py`: a hand-built list of `ComboData`,
  `build_candidate_cards` from `tests/unit/test_ilp_optimizer.py`, small cube sizes, one
  worker. Characterization tests compare profile constraint counts exactly, so only set a
  new `base.counts[...]` entry when constraints were actually added.
- The tracked best cube is `data/current_best_cube.txt` with
  `data/current_best_cube_stats.json` (300 cards, 20,000 variants, ratio 2: 3,249 combos).

## Problem

A Spellbook variant is one way to assemble a combo. Many variants are the same combo with one
piece swapped for an equivalent. The optimizer treats every variant as a separate combo, so a
card in one combo with ten interchangeable partners looks like a ten-combo card, and Phase 1
is rewarded for stacking variants of a single combo.

Measured on the cached 20,000 variants (`of` field, the combo a variant belongs to):

| | |
|---|---|
| Variants | 20,000 |
| Distinct combos (`of` ids) | 8,686 |
| Combos with exactly one variant | 7,444 |
| Combos with 10 or more variants | 317 |
| Largest combos (variants) | 301, 272, 231, 217, 210 |
| Variants that belong to more than one combo | 512 |

In the current best cube, counting only variants without template requirements (2,873 of the
3,249 completed), those variants belong to 524 distinct combos. One combo (id 6186) accounts
for 240 of them, another for 174. The 500-utilization hubs seen in every run are cards in
those big combos.

## Decisions to make

| Question | Recommendation |
|----------|----------------|
| What is a combo group | The sorted tuple of `Variant.of` ids. A variant of two combos combined is its own group. 512 of 20,000 variants have more than one `of` id. Alternative: the smallest id, which merges such variants into one of their parents. |
| Keep variants or collapse them before the solve | Keep them. Variants are what the cube actually completes and the coverage and card-level constraints need them. Grouping is applied in the objective and the counts. |
| How to value extra variants of a group | Diminishing: the first completed variant of a group is worth 1, each further one is worth `variant_weight` (new flag, default to be chosen by measurement, start at 0.1). 0 counts groups only. |
| Utilization definition | Step 2 below. Measure first; keep the variant definition until the group definition is shown to change results. |

## Step 0: report distinct combos

No solver change. Needed to measure everything else.

1. `ComboData` gets `group_key: str` (set in `ComboPreprocessor._process_single_variant` from
   `variant.of`). Existing tests build `ComboData` directly, so give it a default equal to the
   variant id.
2. `cube_evaluation.py`: `completable_group_keys(selected_cards, combos)` and
   `combo_group_sizes(selected_cards, combos) -> dict[str, int]` (variants completed per
   group).
3. `OptimizationResult`: `distinct_combo_count` and `phase1_distinct_combo_count`.
4. `write_stats`: `distinct_combo_count` in each phase block, `largest_combo_groups` (top 10
   groups by completed variants, with their cards) at the top level. `log_phase_summary`
   prints "Combos: Phase 1 X variants in G groups". `evaluate_cube` prints the same.
5. Run the evaluation on `data/current_best_cube.txt` to get the real baseline (the 524
   above ignores template combos).

## Step 1: group-aware Phase 1 objective

1. In `_build_base_model` or a new `_add_group_vars(base)`: for each group `k` with more than
   one variant, `g[k]` is a bool with `g[k] <= sum(y[j] for j in k)` and `g[k] >= y[j]` for
   each `j`. Groups with one variant use `y[j]` directly, so the model grows by the number
   of multi-variant groups (about 1,240 at 20,000 variants) plus one constraint per variant
   in them.
2. `_add_combo_count_objective`: maximize
   `sum(W * g[k]) + sum(variant_weight * W * y[j]) + popularity tiebreak`, with `W =
   WEIGHT_SCALE`. With `variant_weight = 1` this reduces to the current objective, so the
   default-agreement tests keep passing if the default is 1; pick the default after
   measuring.
3. Flag `--variant-weight` through `__main__.py`, `runner.py`, `ilp_runner.run_ilp` and
   `build_cube_ilp`, following `--max-color-ratio` for the plumbing and its tests in
   `tests/unit/test_runner.py`.
4. Phase 2 window: `_combo_count_window` and `_add_combo_count_window` count `y`. With a
   group-aware Phase 1 the window should hold the same quantity Phase 1 maximized, so the
   window is written over the weighted sum `sum(g) + variant_weight * sum(y)`. The reference
   count from `_build_warm_start` (the best cube under coverage and color) uses the same
   expression; `_best_constrained_cube` and `_repair_floor` build on `_repair_model`, which
   calls `_build_base_model`, so the group variables must be in the base model or added in
   `_repair_model` too.
5. Tests: a small instance with one group of several variants and several single-variant
   groups where variant counting and group counting choose different cubes.

## Step 2: group-based utilization (decide after Step 1)

Utilization is the number of completed variants a card is in, and the floor, cap and tiered
objective use it. Under grouping, a card in 240 variants of one combo has utilization 240 and
is penalized as a hub even though it is one combo piece.

Option A: leave utilization in variants. Hubs in big groups stay penalized, which still pushes
the cube away from stacking variants. Cheapest.

Option B: utilization in groups: `u[c] = number of groups with a completed variant containing
c`. Needs a bool per (card, group) pair, `z[c,k] <= sum(y[j] for j in k containing c)` and
`z[c,k] >= y[j]`, then `u[c] = sum(z[c,k])` with the same `only_enforce_if(x[c])` handling as
`_add_utilization_vars` for pool cards. Roughly the same size as the existing linking (about
70,000 card-variant pairs at 20,000 variants), so Phase 2 would grow by maybe a third. Add
behind `--utilization-unit {variants,combos}` only if Step 1 results show hub cards still
dominating.

## Implementation notes (October 2026)

Steps 0 and 1 are implemented; Step 2 is decided below.

- The score formula differs from the one sketched in Step 1. "First variant worth 1, each
  further one worth `v`" is `(1 - v) * g[k] + v * sum(y[j] for j in k)` per group, not
  `g[k] + v * sum(y)`, so that `v = 1` is exactly the old objective and the characterization
  tests keep passing unchanged. Group variables are only created when `v < 1`.
- The combo window is written over the score. The window edges are whole combos
  (`floor`/`ceil` of the reference in combo units, times `WEIGHT_SCALE`), so with `v = 1` it is
  the old window on the variant count. `_build_warm_start` now returns the reference cube and
  the stats file records its variants, distinct combos and weighted count.
- Step 0 baseline, `evaluate_cube` on `data/current_best_cube.txt`: 3,248 variants in 552
  distinct combos (the tracked stats file says 3,249; the cached template pools have moved
  by one variant since it was written). Largest groups: 6186 with 240 variants (33 cards),
  30580 with 190, 1474 with 151, 26943 with 119, 29790 with 106. The five largest groups hold
  806 of the 3,248 variants.

## Verification

Full run at the defaults, compared with the tracked best cube:

- distinct combos completed (baseline from Step 0), total variants, and the largest group's
  share;
- utilization range and std dev (baseline 2 to 326, 54.1);
- colors (baseline W 78, U 50, B 77, R 50, G 100);
- Phase 1 solve time (baseline 10 to 22 s, optimal) and whether Phase 2 still finishes with a
  solution in 300 s.

Try `variant_weight` at 1, 0.25, 0.1 and 0. Record the table here and pick the default.

### Results (6 October 2026)

Full runs, 300 cards, 20,000 variants, 300 s per phase, 8 workers, otherwise defaults. Phase 2
hit its time limit in every run; the final cube is the best found. "Final" is the Phase 2
cube; "largest" is its biggest combo group.

| `v` | Phase 1 variants / combos (time, gap) | Reference variants / combos / weighted | Final variants / combos | Largest group (share) | Top 5 groups | Utilization min-max, median, std | Colors W U B R G |
|---|---|---|---|---|---|---|---|
| 1 | 4,145 / 604 (10 s, optimal) | 3,622 / 534 / 3,622 | 3,259 / 562 | 230 (7%) | 754 | 2-349, 25, 48.1 | 79 46 83 46 92 |
| 0.25 | 2,964 / 1,410 (305 s, 13.6%) | 2,822 / 1,300 / 1,680.5 | 2,390 / 1,220 | 60 (3%) | 221 | 2-256, 18, 38.0 | 74 62 67 41 74 |
| 0.1 | 2,476 / 1,496 (301 s, 8.3%) | 2,391 / 1,392 / 1,491.9 | 1,913 / 1,279 | 24 (1%) | 113 | 2-259, 15, 25.5 | 63 65 53 47 77 |
| 0 | 2,187 / 1,554 (301 s, 4.8%) | 2,090 / 1,441 / 1,441 | 1,736 / 1,296 | 21 (1%) | 82 | 2-209, 13, 20.2 | 66 79 41 48 72 |

Most used cards of the final cube: at `v = 1` Kodama of the East Tree 349, Pitiless Plunderer
326, Ashnod's Altar 215; at `v = 0.1` Ashnod's Altar 259, Phyrexian Altar 183, Goblin
Bombardment 112; at `v = 0` Ashnod's Altar 209, Phyrexian Altar 137, Karmic Guide 80.

Observations:

- Counting variants (`v = 1`) reproduces the tracked best cube: 562 distinct combos, the
  largest holding 230 of 3,259 variants, and the hub cards are the pieces of the big groups.
- Any weight below 1 changes the cube completely. `v = 0.1` has 2.3 times the distinct
  combos of `v = 1` and its largest group is 24 variants; the utilization standard deviation
  halves. Going on to `v = 0` adds only 17 combos while dropping another 177 variants.
- The remaining hub cards at low weights are sacrifice outlets in template pools (Ashnod's
  Altar, Phyrexian Altar), counted once per completed variant whose pool contains them. That
  is the utilization definition limitation, not the grouping one.
- Phase 1 is no longer solved to optimality: with 1,226 group variables (13,638 linking
  constraints) it runs to the 300 s limit at every weight below 1, with a 5-14% gap. The
  Phase 1 solution is still an upper bound on what Phase 2 can keep, so the cube quality
  rests on the warm-start repair and Phase 2 as before.
- Every Phase 2 cube sits on the lower edge of its combo window (90% of the weighted
  reference), as it did before grouping.

### Decisions

- **Default `--variant-weight` is 0.1.** It takes nearly all of the distinct-combo gain
  while keeping some redundancy inside a combo (1,913 variants against 1,736 at 0).
- **Step 2 is not done (Option A).** With the default, the largest group is 24 variants and
  the over-used cards are template-pool cards across many distinct combos, which a
  group-based utilization would count the same way. Revisit if the utilization definition
  is changed.
- `data/current_best_cube.txt` and its stats file are replaced by the `v = 0.1` run, so the
  tracked example matches the default settings again.
- Follow-up: a Phase 1 gap or time setting now matters (`-t` applies to both phases). A
  Phase 1 gap limit of a few percent would free time without changing the cube much.

## Risks and notes

- Phase 1 is currently solved to optimality in seconds; the group variables may make that
  harder. A gap limit for Phase 1 does not exist today.
- The combo window in weighted units changes the meaning of `--combo-tolerance` and the
  `reference_combo_count` in the stats file; document both in `README.md` and
  `docs/architecture.md`.
- Spellbook `includes` lists combos a variant contains as sub-combos (6,951 variants include
  two). It is not the grouping key; `of` is.
- Plans 2 and 4 count combos per color pair and per outcome. If this plan lands first, those
  counts should be in groups, not variants.
