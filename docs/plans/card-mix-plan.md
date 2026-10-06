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
