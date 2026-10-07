# Card Mix Plan

Written October 2026. Constrain the make-up of the cube by card type, mana value and
multicolor or colorless count. Third of four playability plans; see
[combo-grouping-plan.md](combo-grouping-plan.md) (first),
[archetype-support-plan.md](archetype-support-plan.md) and
[combo-variety-plan.md](combo-variety-plan.md).

## Context for a new session

- Read the "Context for a new session" sections of
  [combo-grouping-plan.md](combo-grouping-plan.md) and
  [archetype-support-plan.md](archetype-support-plan.md). The second one explains the three
  places every Phase 2 hard constraint must appear (`_solve_phase2`, `_repair_model`, a
  violations check for `_build_warm_start`) and proposes a registry refactor; if that refactor
  has not been done, do it here first.
- Card color identities come from `src/mtg_combo_cube/scryfall/card_color_fetcher.py`
  (`CardColorFetcher`): Scryfall `POST /cards/collection`, 75 names per batch, multi-faced
  cards requested by front-face name and matched back by face, cached in
  `data/cache/scryfall_card_colors.json` (`CACHE_VERSION = 1`, `{"version": 1, "cards":
  {name: "WUBRG letters"}}`). `fetch_color_identities` in `ilp_runner.py` wraps it and
  `build_cube_ilp` calls it for every candidate card (about 45 requests, cached). The
  response objects also carry `type_line`, `cmc` and `mana_cost`, which are currently thrown
  away.
- `Variant.uses[].card.type_line` (`src/mtg_combo_cube/models.py`) has the type line of every
  card used directly by a variant, but cards that only appear in template requirement pools
  come from Scryfall searches and have no type data in the project. The collection lookup
  covers both.
- Color statistics: `compute_color_stats` in `cube_evaluation.py`, `ColorStats` in
  `ilp_models.py`, `format_color_stats` and `_colors_block` in `ilp_runner.py`.

## Problem

The optimizer only knows combos, so the cube's shape is whatever maximizes them:

| | Current best cube (300 cards) | Candidate pool (cards used directly by variants) |
|---|---|---|
| Creatures | 191 (64%) | 2,589 of 4,291 |
| Enchantments | 57 | 628 |
| Artifacts | 33 | 685 |
| Lands | 19 | 135 |
| Instants | 5 | 242 |
| Sorceries | 3 | 243 |
| Planeswalkers | 1 | 82 |
| Multicolor cards | 47 to 58 across recent runs | |
| Colorless cards | 33 to 54 across recent runs | |

Mana values are not measured yet. Eight instants and sorceries in 300 cards means almost no
interaction; the pool has 485.

## Decisions to make

| Question | Recommendation |
|----------|----------------|
| Which rules first | Multicolor cap and colorless cap: no new data, and both were open questions in the color plan. Then mana value, then type shares. |
| Form | Share caps as hard constraints with a flag each, 0 disables. The color balance ratio form (`den * a <= num * b`) is reused for a mono-colored balance if wanted. |
| Multicolor cap | `--max-multicolor-share`, start at 0.15 (45 of 300). Gold cards are harder to cast and 20% of the cube is gold today. |
| Colorless cap | `--max-colorless-share`, start at 0.15. Colorless cards fit every deck, which makes them free for the optimizer; the question is whether 50 artifacts is what a drafter wants. |
| Mana value | `--max-expensive-share` with `--expensive-mana-value` (default 5): at most this share of cards has mana value at or above the threshold. Per-color curves are a later refinement. |
| Types | `--max-creature-share` (start at 0.6) and `--min-spell-share` for instants and sorceries together (start at 0.05). A minimum on interaction cannot be met by combos alone and may need the reserved slots idea in [combo-variety-plan.md](combo-variety-plan.md), so measure before setting a default. |
| Lands | Leave alone. The 19 lands are combo pieces. |

## Step 1: card attributes from Scryfall

1. Generalize `CardColorFetcher` into `CardAttributeFetcher` returning
   `dict[str, CardAttributes]` with `color_identity: str`, `type_line: str`, `mana_value:
   float`, and `types: frozenset[str]` derived from the type line (the part before the em
   dash, split on spaces, so "Legendary Artifact Creature" gives both Artifact and
   Creature). Keep `fetch_color_identities` working, or replace its callers in
   `build_cube_ilp`, `evaluate_cube` and the tests (`tests/unit/test_card_color_fetcher.py`,
   `tests/unit/scryfall_fakes.py`, fake fetchers in `tests/unit/test_runner.py`).
2. New cache file `data/cache/scryfall_card_attributes.json`, version 1, so the color cache
   can be deleted; or bump `CACHE_VERSION` to 2 in the same file with the new fields. A
   cache without the new fields is treated as empty.
3. `ILPOptimizer` takes `card_attributes` instead of (or alongside) `card_colors`.
   `_cards_of_color` and `_color_violations` read the identity from it.
4. Reporting first: `compute_card_mix_stats(cards, attributes) -> CardMixStats` (type
   counts, multicolor, colorless, mana value histogram and mean, per color), a `card_mix`
   block per phase in the stats file, a log line, and `evaluate_cube` output. Run it on
   `data/current_best_cube.txt` to get the real baseline for mana values.

## Step 2: share caps

1. One helper, `_add_share_cap(base, name, cards, max_share)`: `sum(x[c] for c in cards) <=
   floor(max_share * cube_size)`, sets `base.counts[name]` when added, and one
   `_share_violations(cards, group, max_share)` for the warm start check. Share floors
   (`min_spell_share`) are the same with the inequality reversed and a ceiling.
2. Constructor arguments and flags for multicolor, colorless, expensive and type shares,
   plumbed like `--max-color-ratio` (`__main__.py`, `runner.py`, `run_ilp`,
   `build_cube_ilp`, stats `phase2` block, `tests/unit/test_runner.py`).
3. Added in `_solve_phase2` and `_repair_model`; violations counted in `_build_warm_start`.
   Cards without attribute data count as colorless, typeless and mana value 0, and a warning
   gives their number.
4. Optional mono-colored balance: `--mono-color-ratio` applying the `_add_color_balance`
   form to mono-colored counts (`ColorStats.mono_colored` already reports them).

## Step 3: measure and choose defaults

For each cap, a Phase 1 style solve with coverage, color balance and the cap, 120 s at 300
cards and 20,000 variants, as in the color plan's measurements table. Record the combo cost
of each cap alone and all together, then a full two-phase run with the chosen defaults.
Watch Phase 2: it already uses its whole 300 s at 20,000 variants, and each hard cap lowers
the reference count and narrows the window.

## Tests

Small instances with hand-written attributes (`CardAttributes` per card): a cap excludes a
card the unconstrained cube takes; 0 disables; missing attributes count as colorless and
typeless; violations counts. Fetcher tests extend `test_card_color_fetcher.py` with the new
fields and cache version handling.

## Verification

Full run at the chosen defaults against the tracked best cube: card mix, combos, utilization,
colors, Phase 2 time. Record the results here; update `README.md`, `docs/architecture.md`
(Scryfall section, cache files, Phase 2 constraint list, stats file) and the memory note.

## Open questions

- Whether type shares belong in the optimizer at all, or whether a reserved block of
  non-combo playables (removal, counterspells, fixing) chosen outside the ILP is the better
  tool for interaction. See the reserved-slots note in
  [combo-variety-plan.md](combo-variety-plan.md).
- A per-color mana curve (mean mana value per color within a band) is a natural next step
  once mana values are cached.
- The mono-colored balance may conflict with the total color balance at tight ratios;
  measure before giving it a default other than 0.

## Results (7 October 2026)

Implemented on branch `card-mix`. `CardColorFetcher` became `CardAttributeFetcher`
(`CardAttributes`: color identity, type line, mana value; cache
`data/cache/scryfall_card_attributes.json`, version 1, the color cache is no longer read),
`compute_card_mix_stats` reports the mix per phase (`card_mix` in the stats file, a log line,
`evaluate_cube`), and the rules are a `CardMixRules` object (`--max-multicolor-share`,
`--max-colorless-share`, `--max-expensive-share` with `--expensive-mana-value`,
`--max-creature-share`, `--min-spell-share`, `--mono-color-ratio`) registered as six entries of
`_cube_rules` through one share-cap and one share-floor helper. The optimizer takes
`card_attributes` instead of `card_colors`; the color balance reads the identity from it.

### Step 1: the real baseline

`evaluate_cube` on the tracked `data/current_best_cube.txt` (300 cards, built before this plan):

| | Tracked cube | Share | Candidate pool (4,555 cards) |
|---|---|---|---|
| Creatures | 189 | 63% | 2,705 (59%) |
| Instants + sorceries | 10 | 3% | 527 |
| Artifacts | 104 | 35% | 748 |
| Enchantments | 40 | 13% | 651 |
| Lands | 1 | | 163 |
| Multicolor | 49 | 16% | 988 (22%) |
| Colorless | 98 | 33% | 643 (14%) |
| Mana value 5 or more (nonland) | 76 | 25% | 1,175 (27%) |
| Mean mana value (nonland) | 3.46 | | 3.62 |

The colorless share is far above the 15% the plan guessed: the optimizer takes artifacts
because they fit every archetype. Half the cube's cards are at mana value 3 or 4.

### Step 3: combo cost of each cap

Phase 1 style solve under the Phase 2 cube rules in force (coverage, color balance 2.0,
archetype minimums 250 / 150, wide cap 0.25) plus the rule under test; 300 cards, 20,000
variants, 120 s, 8 workers. Weighted combos at `--variant-weight 0.1`; the baseline is
1,450.9 weighted (1,383 distinct). Time-limited solves: differences under 2% are noise.

| Rule | Weighted combos | Cost | Cube under the rule |
|---|---|---|---|
| None (baseline) | 1,450.9 | | creatures 63%, spells 5%, multicolor 14%, colorless 29%, mv 5+ 23% |
| Multicolor cap 0.15 | 1,442.8 | -0.6% | already met by the baseline |
| Colorless cap 0.15 | 1,328.9 | -8.4% | artifacts 47, enchantments 56 |
| Colorless cap 0.25 | 1,401.0 | -3.4% | artifacts 79 |
| Expensive cap 0.10 (mv 5+) | 1,361.9 | -6.1% | mean mv 3.03 |
| Expensive cap 0.15 | 1,414.4 | -2.5% | mean mv 3.14 |
| Expensive cap 0.20 | 1,427.5 | -1.6% | mean mv 3.35 |
| Creature cap 0.60 | 1,436.3 | -1.0% | creatures 179 |
| Spell floor 0.05 | 1,396.4 | -3.8% | 15 instants and sorceries |
| Spell floor 0.10 | 1,367.2 | -5.8% | 30 instants and sorceries |
| Mono color ratio 2 | 1,449.6 | -0.1% | already met (mono counts 26 to 44) |
| All at the plan's starting values (0.15 / 0.15 / 0.10 / 0.60 / 0.05) | 1,086.7 | -25% | lowest pair 253, lowest mono 152: the archetype minimums bind too |
| Moderate set (0.15 / 0.25 / 0.20 / 0.60 / 0.05) | 1,369.4 | -5.6% | creatures 60%, spells 5%, multicolor 11%, colorless 25%, mv 5+ 20% |
| Moderate set without the spell floor | 1,385.1 | -4.5% | spells 3% |

Decisions:

- Defaults: `--max-multicolor-share 0.15`, `--max-colorless-share 0.25`,
  `--max-expensive-share 0.2` at `--expensive-mana-value 5`, `--max-creature-share 0.6`,
  `--min-spell-share 0.05`, `--mono-color-ratio 0`. The plan's starting values cost a quarter
  of the combos together; the colorless cap at 0.15 and the expensive cap at 0.10 are the
  expensive ones, and each was loosened to the level that costs about 3% alone.
- The spell floor stays at 0.05 (15 cards): it is met by combo pieces, so it costs 1.1% on top
  of the other rules, but it is a floor on interaction only in name (see the open questions).
- The multicolor cap and the mono-colored balance are not binding at the default
  configuration; the multicolor cap is kept as a guard, the mono balance is off.
- Lands are left out of every card mix rule and of the `card_mix` multicolor and colorless
  counts ("Lands: leave alone" above): colorless lands would otherwise fill the colorless cap
  and colored lands such as Kessig Wolf Run the multicolor cap. The measurements above counted
  lands (the cubes had 0 to 9), which changes the figures by at most 3 cards.
- Cards without Scryfall data count as colorless, typeless and mana value 0; when more than 5%
  of the candidates lack data (a failed Scryfall batch), the rules are skipped with a warning
  rather than applied to a skewed pool. The stats file records the count.

### Verification

One full two-phase run at the defaults (`task build:ilp`: 300 cards, 20,000 variants, 360 s
per phase, 8 workers) against the tracked cube built before this plan (same settings, the
archetype rules already in force). The run replaced the tracked `data/current_best_cube.txt`;
its `card_mix` blocks were recomputed after lands left the colorless count (the cube's two
lands, Lotus Field and Maze of Ith, are colorless), which is the only figure that changed.

| | Tracked cube before | This run |
|---|---|---|
| Phase 1 variants / distinct / weighted | 2,432 / 1,523 / 1,613.9 | 2,468 / 1,502 / 1,598.6 |
| Reference under the cube rules (weighted) | 1,346.1 (1,257 distinct) | 1,220.2 (1,132 distinct; 108 cards swapped, 72 s) |
| Phase 2 variants / distinct / weighted | 1,771 / 1,149 / 1,211.2 | 1,444 / 1,060 / 1,098.4 |
| Utilization min / max / std dev | 2 / 238 / 26.1 | 2 / 193 / 18.9 |
| Creatures | 189 (63%) | 180 (60%) |
| Instants + sorceries | 10 | 17 |
| Artifacts / enchantments | 104 / 40 | 81 / 51 |
| Multicolor / colorless (nonland) | 49 (16%) / 98 (33%) | 35 (12%) / 73 (24%) |
| Mana value 5+ (nonland) / mean | 76 (25%) / 3.46 | 60 (20%) / 3.29 |
| Cards per color W/U/B/R/G | 61/49/53/52/62 | 51/58/43/58/65 |
| Lowest pair / lowest mono | UR 264 / U 182 | BR 280 / R 186 |
| Combos needing 3+ colors | 19% | 13% |
| Phase 2 time (repair / solve) | 300 s | 360 s (93 s / 266 s) |

The Phase 1 cube broke every card mix rule plus coverage, two archetype minimums and the
wide cap; the reference solve repaired it in 72 s and the floor repair took another 21 s. The
cube loses 8% of its distinct combos against the previous tracked cube, in line with the 5.6%
measured for the rule set plus run-to-run variation, and comes out flatter (the utilization
objective had more room because the window is measured from a lower reference). Phase 2 still
runs to its time limit.

### Open questions, updated

- Interaction: the spell floor is met with combo pieces (the instants and sorceries that take
  part in combos), not with removal or counterspells. A reserved block of non-combo playables
  (see [combo-variety-plan.md](combo-variety-plan.md)) remains the better tool for that; the
  floor only stops the cube from having none.
- A per-color mana curve is now measurable (`mean_mana_value_per_color` in `card_mix`, all
  between 3.3 and 3.5 in the verification run) and could reuse the share-cap helper per color.
- The mono-colored balance (`--mono-color-ratio`) is implemented but off: at the default
  configuration the mono counts already sit within a factor of 1.5.
- `ilp_optimizer.py` now carries four rule families (coverage, color balance, archetype
  support, card mix) and is about 2,400 lines. The card mix methods only need the cube size,
  the candidate cards, their attributes and `base.x`, and `_CubeRule` already decouples a rule
  from the solve flow, so before the per-color curve or the combo-variety work the rule
  families should move into their own module that returns `list[_CubeRule]` plus its pool
  check and result info.
