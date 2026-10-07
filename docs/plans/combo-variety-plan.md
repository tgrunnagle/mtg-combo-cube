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

## Results (7 October 2026)

Implemented on branch `combo-variety`. `ComboData` carries `features` (the names of the
variant's Spellbook `produces` features) and `bracket_tag`; `ilp/outcomes.py` loads the
category table `data/outcome_categories.json` (case-insensitive substring patterns, or a
regex with a `re:` prefix; a category may give `{"patterns": [...], "min_combos": N}` to set
its own minimum); `combos_per_outcome` and `popularity_stats` in `cube_evaluation.py` feed
`outcomes` and `popularity` blocks per phase in the stats file, a log line each and
`evaluate_cube`. The outcome rules are two more entries of `_cube_rules`: "outcome minimum"
(`--min-outcome-combos`, the table's own minimum where given) and "outcome share cap"
(`--max-outcome-share`), counted in distinct combos (groups) over the group indicators, the
cap written like the wide combo cap (`(den - num) * inside <= num * outside`, with the
variants of every group in a category linked exactly in the one-sided repair models).
`--popularity-weight w` scales each group's score weights (both `g` and `y`) by
`1 + w x log(1 + popularity) / log(1 + max popularity)`, in the Phase 1 objective, the Phase
2 window and `_combo_score`, reported as `combo_score`.

Decisions taken against the plan's recommendations:

- A combo is placed in its categories by the union of its variants' features, so that the
  model (which decides membership before the solve), the violation checks and the statistics
  agree. Variants of one combo produce the same features in nearly every case.
- The default table has eleven categories: the plan's ten plus "win" ("win the game", "loses
  the game"), which is as terminal as an outcome gets. "draw" matches "infinite card draw"
  only, not "infinite draw triggers" (a trigger, not a result); "lifegain" excludes
  "lifegain triggers" with a regex; "lock" is anchored (`re:^lock`) so it does not match
  "block".
- The bracket tag is stored but not used: Spellbook's letters (E 12,980, S 4,266, R 1,534,
  O 607, P 516, C 97 in the pool) are not documented well enough to turn into a rule.

### Step 1: the baseline

`evaluate_cube` on the tracked `data/current_best_cube.txt` (300 cards, built by the card mix
plan; 1,444 variants in 1,060 distinct combos):

| Category | Pool (8,662 combos) | Tracked cube (1,060) | Share of the cube |
|---|---|---|---|
| mana | 3,466 | 615 | 58% |
| tokens | 1,677 | 278 | 26% |
| damage | 1,675 | 174 | 16% |
| counters | 899 | 170 | 16% |
| storm | 1,641 | 170 | 16% |
| draw | 804 | 95 | 9% |
| mill | 569 | 79 | 7% |
| lifegain | 690 | 52 | 5% |
| lock | 901 | 43 | 4% |
| turns | 570 | 33 | 3% |
| win | 647 | 4 | 0.4% |
| no category | 967 | 97 | 9% |

Counted in distinct combos the picture is less lopsided than the variant counts in the
Problem section suggested (a combo counts for every category it is in, so the shares add to
more than 100%), but the cube is still mostly mana combos, and it has almost no win-the-game,
extra-turn or lock combos although the pool has 570 to 900 of each. One in eleven completed
combos is a trigger loop with no terminal result in the table.

Popularity: the tracked cube's distinct combos have a median popularity of 462.5 against a
pool median of 327.5 (the pool being the 20,000 most popular variants already), with a mean
log popularity of 6.32; 42% of its combos are below the pool median.

### Step 2: combo cost of the outcome rules

Phase 1 style solve under the Phase 2 cube rules in force (coverage, color balance 2.0,
archetype minimums 250 / 150, wide cap 0.25, the card mix defaults) plus the rule under test;
300 cards, 20,000 variants, 120 s, 8 workers, no warm start. Weighted combos at
`--variant-weight 0.1`. These unhinted 120 s solves are noisy: the baseline found no solution
at all in two of three attempts and 1,274.5 weighted in the third, while the same rule set
measured 1,369.4 in the card mix plan's session; differences under about 8% are noise.

| Rule | Weighted combos | Distinct | Smallest category | Cube under the rule |
|---|---|---|---|---|
| None (this session, 1 of 3 solves) | 1,274.5 | 1,178 | win 2 | mana 58%, turns 16, lock 18 |
| None (card mix plan's session) | 1,369.4 | 1,297 | | |
| Minimum 40 every category | 1,349.2 | 1,292 | win 40 | mana 56%, turns 42, lock 45 |
| Minimum 50 | 1,203.8 | 1,144 | turns 50 | mana 54% |
| Minimum 60 | 1,249.6 | 1,195 | turns 60 | mana 53% |
| Minimum 80 | 1,051.3 | 999 | draw 80 | mana 51%, turns 100, lock 104 |
| Share cap 0.5 | 914.7 | 869 | win 2 | mana 49% |
| Share cap 0.4 | no solution in 120 s | | | |

Decisions:

- `--min-outcome-combos 40` by default, no per-category override in the shipped table: it
  brings every category to at least 40 distinct combos (3% of the cube) at a cost inside the
  measurement noise, where 60 costs about 9% and 80 about 23% against the card mix plan's
  baseline. The pool has at least 569 combos in every category, so the minimum is far from
  what the pool allows.
- `--max-outcome-share 0` (off): the cap is the expensive form of the rule. Half the pool's
  combos make mana, so holding mana to 50% of the cube costs a third of the combos, and 40%
  found no cube at all in the time given. Mana combos are also the ones other categories
  build on (infinite mana plus an outlet), so a hard cap on them is the wrong tool; the
  minimums raise the other categories instead.

### Step 3: popularity weight

One full two-phase run each (`task build:ilp`: 300 cards, 20,000 variants, 360 s per phase, 8
workers, the outcome minimum of 40 in force) at `--popularity-weight` 0, 0.5 and 1. The
popularity statistics are over the Phase 2 cube's distinct combos; the pool median is 327.5.

| | w = 0 | w = 0.5 | w = 1 |
|---|---|---|---|
| Phase 1 variants / distinct / weighted | 2,504 / 1,525 / 1,622.9 | 2,544 / 1,489 / 1,594.5 | 2,535 / 1,512 / 1,614.3 |
| Phase 1 median popularity | 368 | 408 | 380 |
| Reference under the cube rules (distinct / weighted / score) | 1,099 / 1,157.5 | 1,085 / 1,126.3 / 1,410.8 | 1,172 / 1,224.7 / 1,843.9 |
| Phase 2 variants / distinct / weighted | 1,352 / 1,007 / 1,041.5 | 1,284 / 982 / 1,012.2 | 1,413 / 1,065 / 1,099.8 |
| Phase 2 median popularity | 507 | 494 | 536 |
| Phase 2 mean log popularity | 6.46 | 6.44 | 6.47 |
| Share below the pool median | 40% | 42% | 40% |
| Utilization min / max / std dev | 2 / 158 / 17.3 | 2 / 142 / 14.5 | 2 / 167 / 16.8 |
| Lowest pair / lowest mono | RG 252 / R 156 | WB 250 / B 161 | WG 259 / W 163 |
| Mana combos (share of distinct) | 497 (49%) | 527 (54%) | 622 (58%) |

The weight does what it should inside the model (Phase 1's median popularity rises from 368
to 380 or 408, and the Phase 2 window is measured in score units) but the Phase 2 cube's
popularity hardly moves: the three medians lie within the 10% that separates two runs at
`w = 0` (the previous tracked cube had 462.5, this run 507), the mean log popularity is
6.44 to 6.47 everywhere, and 40 to 42% of the combos are below the pool median in every run.
The reason is that the Phase 2 cube is already popular: the cube rules and the coverage
constraints pull in the hub cards and the staple combos, and the pool is the 20,000 most
popular variants to begin with. `w = 1` finished with the most distinct combos, but the
spread of the three Phase 1 results (1,489 to 1,525 from the same settings) says that is
run-to-run variation, not the weight.

Decision: `--popularity-weight 0` stays the default. The flag is kept for a build that wants
to lean on Spellbook's usage counts, and its score is reported beside the plain count.

### Verification

The `w = 0` run above is the verification run at the chosen defaults (`--min-outcome-combos
40`, `--max-outcome-share 0`, `--popularity-weight 0`). It replaced the tracked
`data/current_best_cube.txt`.

| | Tracked cube before | This run |
|---|---|---|
| Phase 1 variants / distinct / weighted | 2,468 / 1,502 / 1,598.6 | 2,504 / 1,525 / 1,622.9 |
| Reference under the cube rules (weighted) | 1,220.2 (1,132 distinct) | 1,157.5 (1,099 distinct; 124 cards swapped) |
| Phase 2 variants / distinct / weighted | 1,444 / 1,060 / 1,098.4 | 1,352 / 1,007 / 1,041.5 |
| Utilization min / max / std dev | 2 / 193 / 18.9 | 2 / 158 / 17.3 |
| Outcomes: mana / tokens / damage | 615 (58%) / 278 / 174 | 497 (49%) / 244 / 176 |
| Outcomes: counters / storm / draw / mill | 170 / 170 / 95 / 79 | 148 / 179 / 82 / 80 |
| Outcomes: lifegain / lock / turns / win | 52 / 43 / 33 / 4 | 53 / 56 / 53 / 40 |
| Combos in no category | 97 (9%) | 82 (8%) |
| Median popularity / below pool median | 462.5 / 42% | 507 / 40% |
| Lowest pair / lowest mono | BR 280 / R 186 | RG 252 / R 156 |
| Combos needing 3+ colors | 13% | 12% |
| Creatures / instants and sorceries | 180 (60%) / 17 | 176 (59%) / 19 |
| Multicolor / colorless (nonland) | 35 (12%) / 73 (24%) | 32 (11%) / 75 (25%) |
| Phase 2 time (repair / solve) | 360 s (93 s / 266 s) | 360 s (98 s / 262 s) |

The Phase 1 cube broke the outcome minimum on "win" only (4 of 40), plus the usual coverage,
archetype and card mix rules; the reference solve repaired all of them in 98 s. The cube has
5% fewer distinct combos than the previous tracked cube, inside the run-to-run variation
measured above, and every category now has at least 40 combos: ten times the win-the-game
combos, and the lock and extra-turn combos up by a third and two thirds. Mana combos fell
from 58% to 49% of the cube without a cap on them. Phase 2 still runs to its time limit.

### Open questions, updated

- The payoff question stands: an ETB loop counts for nothing here unless one of its
  variants' features names a terminal result, and a mana combo counts for "mana" whether or
  not the cube holds an outlet for it. A payoff table would be the next step.
- The outcome minimum is an absolute count like the archetype minimums; a share of the
  completed combos per category would scale with the cube size.
- The share cap is implemented but off. If a build wants it, the measurements say 0.5 is the
  lowest value that finds a cube at all in 120 s, and it costs a third of the combos.
- `bracket_tag` is on `ComboData` and unused; Spellbook's bracket letters would need
  documenting before a rule could be built on them.
- Table tuning left for later, so the measurements above stay comparable: "Infinite Treasure
  tokens" (542 variants) and "Infinite combat phases" (511) are uncategorized though they
  are mana and a win in practice, and "mana" also matches the dozen "infinite X mana for
  opponents" features.
- A category the pool has too few combos for is lowered to what the pool has (with a
  warning) rather than making Phase 2 infeasible, as Step 2.4 asked; the archetype minimums
  do not do this.
