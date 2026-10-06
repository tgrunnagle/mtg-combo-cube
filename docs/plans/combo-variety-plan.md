# Combo Variety Plan

Written October 2026. Vary what the cube's combos do, and prefer combos people play. Fourth
of four playability plans; see [combo-grouping-plan.md](combo-grouping-plan.md) (first),
[archetype-support-plan.md](archetype-support-plan.md) and
[card-mix-plan.md](card-mix-plan.md).

## Context for a new session

- Read the "Context for a new session" sections of
  [combo-grouping-plan.md](combo-grouping-plan.md) and
  [archetype-support-plan.md](archetype-support-plan.md). The archetype plan describes how a
  Phase 2 hard constraint is added in three places and the registry refactor; the outcome
  constraints below are the same kind of rule as the per-pair minimums, summed over a
  different subset of combos.
- `Variant.produces` (`src/mtg_combo_cube/models.py`) is a list of `Produces(feature=
  Feature(id, name, status, uncountable), quantity)`. `Variant.popularity` is Spellbook's
  usage count. `Variant.bracket_tag` is a single letter. None of these reach `ComboData`
  except `popularity`.
- Popularity is already in Phase 1: `_compute_weight` gives combo `j` weight
  `1 + TIEBREAK_EPSILON * log1p(popularity)` with `TIEBREAK_EPSILON = 0.001`, scaled by
  `WEIGHT_SCALE = 10000`. Popularity ranges from 0 to 356,332 (median 257, 90th percentile
  2,886), so the tiebreak adds 0.006 to 0.013 per combo: a pure tiebreak. Phase 2 ignores
  popularity; its combo window counts `y` unweighted.
- The 20,000 cached variants appear to be ordered by popularity descending (the first has
  the maximum), so the pool is already the most popular 20,000 of Spellbook's variants.

## Problem

### What the combos do

`produces` lists every feature a variant yields, and features overlap heavily: a creature
loop produces "Infinite creature ETB", "Infinite creature LTB" and "Infinite death triggers"
at once. There are 815 distinct feature names in the pool. The most common, in the pool and
in the current best cube (template-free completed variants, 2,873):

| Feature | Pool (20,000) | Cube (2,873) |
|---|---|---|
| Infinite creature ETB | 11,110 | 1,925 |
| Infinite creature LTB | 9,361 | 1,314 |
| Infinite death triggers | 7,176 | 1,283 |
| Infinite creature sacrifice triggers | 6,677 | 1,226 |
| Infinite landfall triggers | 2,225 | 939 |
| Infinite creature tokens | 2,997 | 739 |
| Infinite storm count | 3,183 | not in top 12 |
| Infinite colored mana | 2,375 | 338 |
| Infinite colorless mana | 2,083 | 292 |
| Infinite card draw | 1,557 | not in top 12 |
| Infinite damage | 1,373 | 242 |
| Lock | 1,346 | not in top 12 |

Two thirds of the cube's combos are creature ETB loops. Many of those need a separate payoff
to win, and the cube does not check that one is there.

### Who plays them

The cube completes whatever maximizes the count. Nothing prefers a known combo over an
obscure one of the same size, except the tiebreak.

### Bracket tags

`bracketTag` values in the pool: E 12,987, S 4,258, R 1,530, O 607, P 521, C 97. Spellbook
documents what the letters mean; check the API docs before using them, since they may encode
power level (precon-appropriate against ruthless) which is a playability signal for a cube
meant to be drafted.

## Decisions to make

| Question | Recommendation |
|----------|----------------|
| How to classify outcomes | A small table of outcome categories, each a list of feature-name patterns (case-insensitive substring or regex). A combo belongs to every category one of its features matches. Keep the table in `data/outcome_categories.json` so it can be edited without code changes. |
| Starting categories | mana ("infinite colored mana", "infinite colorless mana", "infinite mana"), damage ("infinite damage", "infinite life loss"), tokens ("infinite creature tokens", "infinite tokens"), draw ("infinite card draw", "infinite draw"), mill ("infinite mill"), lifegain ("infinite lifegain"), counters ("+1/+1 counters"), turns ("infinite turns"), lock ("lock"), storm ("infinite storm count"). Trigger loops without a terminal result (ETB, LTB, death, sacrifice) are not a category. |
| Rule form | Minimum completed combos per category, hard, with one flag for a default minimum and the table allowed to override per category. Optionally a maximum share for any single category. |
| Unit | Groups if the grouping plan has landed, otherwise variants. |
| Popularity | Report first. Then an opt-in `--popularity-weight` in Phase 1, and the same weighted sum in the Phase 2 window so the two phases agree. |

## Step 1: data and reporting

1. `ComboData` gets `features: frozenset[str]` (feature names from `variant.produces`),
   `bracket_tag: str` and keeps `popularity`. Defaults for tests: empty set, "".
2. `src/mtg_combo_cube/ilp/outcomes.py`: load `data/outcome_categories.json`
   (`{"category": ["pattern", ...]}`), `categorize(features) -> frozenset[str]`, and a
   check that every category has at least one pattern. Ship a default file and allow
   `--outcome-categories PATH`.
3. `cube_evaluation.py`: `combos_per_outcome(selected_cards, combos, categories)`,
   `popularity_stats(selected_cards, combos)` (median, mean log popularity, share of
   completed combos below the pool median).
4. Stats file: an `outcomes` block and a `popularity` block per phase; a log line each;
   `evaluate_cube` output. Run on `data/current_best_cube.txt` for the baseline, including
   how many completed combos have no terminal category at all.

## Step 2: outcome minimums

1. Constructor arguments `min_outcome_combos: int = 0` (applies to every category in the
   table unless the table gives a category its own minimum) and `max_outcome_share: float =
   0`.
2. `_add_outcome_minimums(base)`: for each category, `sum(y over combos in it) >= minimum`;
   `_add_outcome_share_cap(base)`: `sum(y in category) <= share * sum(y)` as an integer
   inequality. `_outcome_violations(cards)` for the warm start check. All three places, as in
   the archetype plan.
3. Flags `--min-outcome-combos`, `--max-outcome-share`, `--outcome-categories`, plumbed like
   `--max-color-ratio`, recorded in the stats `phase2` block.
4. Measure the cost as in the color plan (Phase 1 style solve with coverage, color and the
   minimums, 120 s) before choosing defaults. A category with too few combos in the pool
   should warn before the solve rather than make Phase 2 infeasible.

## Step 3: popularity weight

1. `--popularity-weight w` (default 0, the current tiebreak): combo weight becomes
   `1 + w * log1p(popularity) / log1p(max_popularity)` plus the tiebreak, so with `w = 1`
   the most popular combo is worth two combos of zero popularity and the median one about
   1.4.
2. The Phase 2 combo window and the reference count use the same weighted sum, otherwise
   Phase 2 can trade popular combos for obscure ones at no cost. `_combo_count_window`
   then works in weight units; the stats file reports both the weighted value and the plain
   count.
3. Full runs at `w` of 0, 0.5 and 1 comparing the popularity statistics from Step 1 against
   the combo count and the balance measures. Record here; change the default only if a
   setting improves median popularity without a large combo cost.

## Reserved slots for non-combo playables (note, not in scope)

A drafted combo cube still needs removal, counterspells, tutors and fixing, which no combo
objective will select. The simplest design is a curated list (`data/playables.txt`) of cards
that are always included: `build_cube_ilp` passes them to the optimizer as fixed selections
(`x[c] == 1`) so the color balance and card mix rules account for them, and the solver fills
the remaining slots. This belongs in its own plan once the four above are in; it is noted
here because the outcome categories and the card mix shares will both look different once
30 non-combo cards are in the cube.

## Tests

Small instances with features set on `ComboData`: a minimum pulls a damage combo into a cube
of mana combos; the share cap; 0 disables; infeasible minimum falls back; `categorize` with
overlapping patterns and no match. Popularity weight: a two-combo instance where the
weight flips the choice; the window in weight units. Plumbing tests in
`tests/unit/test_runner.py`; the categories file is loaded in a test with a temporary path.

## Verification

Full run at the chosen defaults against the tracked best cube: outcome counts, popularity
median, combos, utilization, colors, Phase 2 time. Record the results here and update
`README.md` and `docs/architecture.md`.

## Open questions

- Whether a combo should count toward an outcome only if the cube also contains a payoff for
  it (an ETB loop with a Blood Artist). That needs a payoff table and is a deeper model.
- `easyPrerequisites` and `notablePrerequisites` are free text; 9,420 of 20,000 variants have
  a notable prerequisite. A cap on combos with notable prerequisites is possible but the
  text would need reading to see whether "notable" means "hard".
- `manaValueNeeded` (0 for 12,464 variants, 5 or more for 2,303) measures mana to go off
  once assembled and could join the card mix mana curve work.
