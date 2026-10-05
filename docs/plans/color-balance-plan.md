# Color Balance Plan

Written October 2026. Adds a color balance constraint to Phase 2 and, as a second step, raises
the default variant pool from 10,000 to 20,000.

## Goal

The optimizer ignores card colors, and the cubes it builds are lopsided: the current best cube
has 93 green cards and 19 red ones. A cube should not have one color more than twice the size
of another.

## Decisions

| Question | Decision |
|----------|----------|
| Constraint or objective term | Hard constraint. |
| Rule | For every pair of colors, the card count of one is at most `ratio` times the count of the other. Default ratio 2. |
| What a card counts toward | Each color in its color identity. A multicolor card counts once per color. Colorless cards are unconstrained. |
| Where it applies | Phase 2 and its warm-start repair. Phase 1 stays a pure combo maximum, so "combos given up in Phase 2" keeps its meaning. |
| Order of work | Color constraint at 10,000 variants first. Then make Phase 2 work at 20,000 variants and change the default. |

## Measurements behind the decisions

300 cards, 8 workers. "With limit" rows are a Phase 1 style model plus coverage and the color
rule, solved for 120 s and not proven optimal, so the true cost is at most what is shown.

| | 10,000 variants | 20,000 variants |
|---|---|---|
| Candidate cards | 3,352 | 4,556 |
| Candidates per color (W / U / B / R / G) | 662 / 858 / 763 / 738 / 820 | 931 / 1,176 / 1,045 / 1,012 / 1,085 |
| Phase 1 combos | 2,471 | 4,145 |
| Combos with coverage only | not measured | 3,886 |
| Combos with coverage and ratio 2 | 2,334 | 3,781 |
| Combos with coverage and ratio 1.5 | 2,293 | not measured |
| Colors at ratio 2 (W / U / B / R / G) | 66 / 42 / 84 / 42 / 84 | 73 / 44 / 88 / 44 / 88 |

- The candidate pool is not short of any color. Red is scarce in the cube because its cards
  complete fewer combos.
- At 10,000 variants, ratio 2 costs about 5.5% of the Phase 1 combos, inside the 10% window
  Phase 2 already allows.
- At 20,000 variants the color rule costs about 2.7% on top of coverage.
- A full two-phase run at 20,000 variants and 300 s per phase fails today. Phase 1 is optimal
  in 10 s, the warm-start repair times out after 31 s, and Phase 2 finds no solution.
- Downloading 20,000 variants takes about 13 minutes. Commander Spellbook rate-limits after
  roughly 100 to 170 pages and blocks for several minutes.

## Step 1: color constraint (10,000 variants)

1. **Colors before the solve.** `build_cube_ilp` looks up the color identity of every candidate
   card through `CardColorFetcher` (about 45 cached requests) and passes the result to the
   optimizer. The same data is reused for the color statistics, replacing the lookup after the
   solve.
2. **Constraint.** `ILPOptimizer` takes `card_colors` and `max_color_ratio`. A new
   `_add_color_balance` adds, for each ordered pair of colors `(a, b)`,
   `count[a] <= ratio * count[b]`, with the ratio written as an integer fraction. It is called
   from `_solve_phase2` and `_repair_warm_start`.
3. **Warm start.** `_build_warm_start` treats a Phase 1 cube that breaks the color rule like one
   that breaks coverage: it triggers the repair.
4. **Flag.** `--max-color-ratio`, default 2.0, 0 disables. Values between 0 and 1 are rejected.
5. **Degraded cases.** No color data: warn and run without the constraint. A card without
   color data counts as colorless.
6. **Reporting.** The `phase2` block of the stats file records the ratio that was applied.
7. **Tests.** Optimizer tests on small instances (constraint holds, disabled at 0, repair
   triggered), plumbing tests for the flag, and the existing default-agreement test.
8. **Docs.** README and `docs/architecture.md`.
9. **Verification.** A full 300-card, 10,000-variant run. Success: the color rule holds in the
   result and the combo count stays inside the Phase 2 window.

### Step 1 result

Done. Full run at 300 cards, 10,000 variants, 8 workers, 300 s per phase, `tiered` objective:

| | Phase 1 | Phase 2, no color rule | Phase 2, ratio 2 |
|---|---|---|---|
| Combos | 2,471 | 2,223 | 2,223 |
| W / U / B / R / G | 66 / 39 / 97 / 24 / 110 | 69 / 58 / 71 / 20 / 94 | 74 / 45 / 74 / 37 / 74 |
| Largest color / smallest color | 4.6 | 4.7 | 2.0 |
| Std dev across colors | 32.8 | 24.2 | 16.4 |
| Utilization std dev | 41.8 | 29.4 | 33.0 |
| Highest utilization | 348 | 255 | 263 |

- The rule holds exactly at its limit: 74 against 37.
- The combo count is unchanged, because both Phase 2 runs end at the bottom of the 10% window.
- Utilization balance is somewhat worse with the rule (std dev 33.0 against 29.4). One run
  each, and 8-worker runs vary, so the size of that cost is not established.
- The Phase 1 cube broke 10 coverage and 5 color constraints. The warm-start repair found a
  cube with 2,289 combos in 17 s.

## Step 2: 20,000 variants

1. Find out why the warm-start repair fails at this size. The utilization floor in the repair
   model is the first suspect; a coverage-only model of the same size solves in 11 s.
2. Fix it, then check that Phase 2 returns a solution within the default time limit.
3. Change the `--max-variants` default to 20,000 on every layer and refresh the tracked best
   cube.

Already done as groundwork: the Spellbook client retries on HTTP 429 and 5xx for up to about
seven minutes and pauses half a second between pages.

## Open questions

- Should mono-colored counts also be balanced? The rule as decided allows a color to be made up
  mostly of multicolor cards.
- Should colorless cards have a cap? They are unconstrained and took 54 slots in the ratio 2
  measurement.
- A softer objective term instead of, or on top of, the hard rule. Deferred until the hard rule
  has been used.
