# Balancing Plan

Written October 2026. Replace the measure the Phase 2 balancing objective works on: penalize
cards the cube's combos *depend on* (bottlenecks) rather than cards that are *in* many
combos, so that redundant pieces (a fourth free sacrifice outlet, a second altar) are kept
rather than removed. Seventh playability plan.

Out of scope: the Phase 2 cube rules (coverage, colors, archetypes, card mix, outcomes,
payoffs, enablers), the combo window, the Phase 1 objective, and the popularity questions
raised by the potential-combos investigation (a separate plan).

## Context for a new session

- Phase 2 (`ILPOptimizer._solve_phase2`, `docs/architecture.md` "Phase 2: balance
  utilization") builds the base model plus the combo window, the cube rules, exact combo
  linking, the utilization variables `u[c]` (`_add_utilization_vars`: the number of
  completed variants card c is a named piece of or could fill a template of, 0 when
  unselected), the utilization floor (`--min-util-floor 2`), the objective and a warm start.
- The objectives are methods registered in `ILPOptimizer._PHASE2_OBJECTIVES` with the
  signature `add_to_model(optimizer, base, u, phase1_result)`. The default `tiered`
  minimizes the total of `u` above a cap T, again above 2T and above 4T
  (`_add_overage_vars`, `_add_overage_objective`); T is `--util-cap` or twice the Phase 1
  median of `u` (`_resolve_util_cap`). The stop rule measures the gap against the Phase 1
  cube's overage (`_set_overage_stop_rule`). A versatility bonus is a pure tiebreak
  (`_tiebreak_scale`).
- Groups: variants of one Spellbook combo share a `group_key`; `g[k]` is 1 iff a variant of
  group k is complete (`_add_group_vars`, exact both ways), and exists whenever
  `--variant-weight` is below 1 or an archetype or outcome rule is on. The combo score counts
  a group's first variant as 1 and each further one as `--variant-weight` (0.1 by default).
- `cube_evaluation.card_inclusion` already reports, per card of a finished cube, the
  distinct combos it is in (`distinct_combo_count`) and the combos the cube would lose
  without it (`essential_combos`, via `_survives_without`). The stats file reports
  utilization in variants (`top_utilized_cards`, the utilization statistics).
- Benchmark ladder, always with `--read-api-cache --profile --workers 8`, warm caches
  (`data/cache/variants_cards4_max{1000,5000,20000}.json`; the smaller two are the first
  variants of the 20,000 file, which the API returns in the same order):

  | Rung | Cube size | Variants | Time limit | Rules |
  |------|-----------|----------|------------|-------|
  | S | 100 | 1,000 | 30 s | archetype, outcome, payoff and enabler minimums off (see `Taskfile.yml`) |
  | M | 200 | 5,000 | 120 s | same |
  | L | 310 | 20,000 | 360 s | all defaults; the tracked cube is the `tiered` baseline |

## Problem

### What the tiered objective did to the tracked cube

The tracked cube (`data/current_best_cube.txt`, built 13 October 2026) went through Phase 2
with `tiered` at T = 34. Phase 2 swapped 126 cards, cut the variants from 2,531 to 1,477
and the distinct combos from 1,584 to 1,037 (27 points of that is the cube rules, measured
by the reference cube; 10 points the window). Counting named cards only against the
20,000-variant pool:

| Measure | Phase 1 cube | Final cube |
|---|---|---|
| Combos touching the top card (Ashnod's Altar) | 16% | 17% |
| Combos touching the top five cards | 35% | 36% |
| Ashnod's Altar: variants / distinct combos / combos only it completes | 291 / 254 / 183 | 197 / 177 / 130 |
| Phyrexian Altar: same | 214 / 184 / 121 | 127 / 118 / 78 |
| Median variants per card | 18 | 11 |
| Cards above twice the median (variants) | 49 | 50 |

The hubs the objective was written for (ilp-improvement-plan.md, Stage 4: "one card sat at
257 combos while the median was 17") are still there, at the same share of the cube. What
left instead were the cards cheapest to drop inside the window. The cards Phase 2 removed
averaged 17 variants in the Phase 1 cube; the cards it kept 32. The free sacrifice outlets
are the clearest case:

| Card (removed) | Variants | Distinct combos | Combos only it completes |
|---|---|---|---|
| Woe Strider | 64 | 53 | 6 |
| Bartolomé del Presidio | 55 | 45 | 0 |
| Umbral Collar Zealot | 59 | 41 | 0 |
| Yahenni, Undying Partisan | 52 | 42 | 1 |

A fourth free outlet completes 45 combos that Viscera Seer, Carrion Feeder and the altars
already complete. In the combo score it is worth a tenth per variant; in the objective every
one of its variants adds a unit of `u` to a persist creature or a drain piece that is already
above T. The objective therefore sees it as pure overage at almost no cost in the window, and
removes it. The Spellbook "potential combos" list for the cube, ordered by count, is this
list of removed cards.

### Why

`u` counts variants, and a redundant variant of a combo the card is already in counts the
same as a new combo. Three consequences:

1. **Redundancy is penalized twice.** The redundant outlet's own `u` is high (every variant
   it is interchangeable in), and it raises `u` for every partner.
2. **Removing a hub is never the cheap move.** Ashnod's Altar's combos are mostly its alone
   (183 of 254), so removing it costs the window 183 combos; removing four redundant outlets
   costs 7. The objective takes the second.
3. **The remedy for a hub is not available.** The way to make a cube less dependent on
   Ashnod's Altar is a second card that completes the same combos. Under `u` that second card
   is itself a hub and is penalized.

### What balance is for

In a draft cube the thing to avoid is a bottleneck: a card so many combos need that whoever
drafts it owns the archetype, and whoever does not has dead pieces. Redundancy, several
interchangeable pieces for the same combos, is the cure, not the disease: it lets two
drafters assemble the same combo and makes each piece less of a pick. `card_inclusion`
already measures the right thing per card: `essential_combos`, the combos the cube would
lose without the card.

## Candidates

All three keep Phase 2 as it is (window, rules, floor, warm start) and change only what the
objective measures. Sizes are for the 20,000-variant pool: 8,596 groups, of which 1,229 have
two or more variants, with 10,876 (named card, group) pairs among those.

1. **`grouptiered`: utilization in distinct combos.** `ug[c]` = number of completed groups
   card c takes part in. For a group where the card is in several variants,
   `w[c,k] >= y[j]` for each (while c is selected) and `w[c,k] <= sum y[j]`; a group with one
   such variant uses its `y`. `ug[c] = sum`, 0 when unselected, as `u`. Tiers above twice the
   Phase 1 median of `ug`. A redundant variant no longer raises partners' counts; the
   redundant card itself still counts its combos.
2. **`essential`: tiers on the combos that need the card.** `e[c,k] >= g[k] - sum(y[j] for
   the variants of k that do not name c)`, so `e[c,k]` is forced to 1 only when the group is
   complete and every complete variant names c; a group whose variants all name c
   contributes `g[k]`, a single-variant group its `y`. `e[c] = sum`. Template fillers never
   count (another pool card could fill the slot). Tiers above twice the Phase 1 median of
   `e`. A lower bound is enough when the objective minimizes `e`. Redundant outlets score
   zero; the altars score what they are essential to; the solver's cheapest remedy for an
   altar is a second outlet covering the same combos.
3. **`essentialcap`: balance as a rule.** The same `e`, but `e[c] <= cap` as a hard
   constraint, and the objective is the Phase 1 one (maximize the combo score). Exact: `e`
   is a lower bound, so the cap binds the true value. This is the shape a `_cube_rules`
   entry would take; if it wins, the reference solve already is Phase 2 and the window
   becomes unnecessary.

A fourth, a cap on the share of completed combos a card touches (`ug[c] <= a x sum g`), is
left out: it would stop the altars by dropping their combos, not by adding cover, and S-rung
numbers for `essentialcap` below show what a cap alone does.

## Decisions to make

- Which measure becomes the default: `essential` is the hypothesis; `grouptiered` is the
  conservative fallback (same semantics as today, redundancy no longer taxed); `essentialcap`
  decides whether balance should be a rule rather than an objective.
- The default cap for the new measure: twice the Phase 1 median, as for `u` (tracked Phase 1
  cube: median essential 10.5, median distinct 12, median variants 18), or a share of the
  cube's combos.
- Whether utilization statistics and `--util-cap` switch to the new measure, or the new
  measure is reported beside them.

## Step 1: the measures and the objectives

- `_group_counts_of(cards, combo_ids)` and `_essential_counts_of(cards, combo_ids)`: the
  two measures computed from a cube, for the cap, the stop rule and the statistics; the
  second must agree with `card_inclusion`'s `essential_combos` for named pieces.
- `_add_group_participation_vars(base)` and `_add_essential_vars(base)` as described, with
  hints from the warm-start cube (`base.hint_cards`, `base.hint_combo_ids`, set beside
  `hint_utilization`).
- `_add_overage_vars` takes the hint values as a parameter so the tiers work on any measure.
- Register `grouptiered`, `essential` and `essentialcap`; extend the CLI choices; the cap is
  logged with its source and written to the stats file as `util_cap` with the measure it
  applies to.
- Prototype: the three objectives as above are implemented on this branch for the
  measurements below (uncommitted at the time of writing; the plan decides what stays).

## Step 2: reporting

- `UtilizationStats` gains the same five numbers for distinct combos and for essential
  combos per card, both phases; `top_utilized_cards` lists the three counts.
- A concentration block in the stats file: the share of completed combos the top card and
  the top five cards touch, and the largest essential count; these are the numbers the
  decision is made on.
- README and architecture: the objective table, the `--util-cap` row, the Phase 2 section.

## Step 3: measure and choose the default

Run the ladder for the four objectives (S and M with the rules off, L with the defaults;
the tracked cube is L `tiered`). Decide on: distinct combos kept, variants per distinct combo
(redundancy), top-card and top-five share, largest essential count, cards above twice the
median on each measure, free outlets kept, Phase 2 time and status.

## Tests

- Brute-force checks of `grouptiered`, `essential` and `essentialcap` against enumeration on
  the characterization instance (`test_ilp_phase2_objectives.py`), as for the existing
  objectives.
- `_essential_counts_of` against `card_inclusion` on a hand-built instance with a group of
  two variants sharing one piece (essential to the shared piece only), a single-variant
  group, and a template-filled variant (the filler is not essential).
- The hard cap: an instance where a hub is essential to more combos than the cap and a
  second piece can cover them; the solution adds the cover rather than dropping the hub.
- CLI and stats plumbing for the new choices and the cap source.

## Verification

- `task check`.
- The L run of the chosen objective: concentration numbers against the tracked cube; the
  removed-outlets table above re-done for the new cube.

## Results

### S rung (100 cards, 1,000 variants, 30 s; rules off)

Phase 1: 170 variants, 144 combos. All four Phase 2 solves OPTIMAL in 2 to 6 s. Named
cards only, against the 1,000-variant pool.

| Objective | Variants | Distinct | Variants per combo | Top card share | Top 5 share | Max essential | Cards > 2x median essential | Outlets kept |
|---|---|---|---|---|---|---|---|---|
| tiered (T = 6) | 138 | 124 | 1.11 | 7% | 31% | 8 | 2 | the two altars |
| grouptiered (cap 6) | 142 | 126 | 1.13 | 6% | 24% | 6 | 0 | the two altars |
| essential (cap 6) | 161 | 124 | 1.30 | 9% | 23% | 6 | 0 | the two altars and Viscera Seer |
| essentialcap (cap 6) | 158 | 126 | 1.25 | 13% | 25% | 6 | 18 | Ashnod's Altar and Viscera Seer |

Reading: `essential` keeps 17% more variants than `tiered` for the same distinct combos (it
is the only one that keeps a third free outlet), and no card is above twice the median
essential count. `essentialcap` meets the cap but lets Ashnod's Altar take 13% of the
combos: a cap bounds dependence, not presence, and with the score maximized the solver
packs combos onto the hub. The S instance is too small for the hubs to matter; the M and L
rungs decide.

### M rung (200 cards, 5,000 variants, 120 s; rules off)

Phase 1: 771 variants, 514 combos (Ashnod's Altar in 117 of them, 23%, essential to 78).
Reference cube (the rules cost 5 coverage, 1 multicolor, 1 creature and 1 spell-floor
violations): 485 to 487 combos, weighted 509.5; window 458 to 561 weighted combos. Every
Phase 2 solve ran to its time limit (FEASIBLE, 60 to 120 s of warm-start preparation
outside it). Named cards only, against the 5,000-variant pool.

| Objective | Cap | Variants | Distinct | Variants per combo | Top card share | Top 5 share | Ashnod's Altar: combos / essential | Max essential | Cards > 2x median essential | Free outlets kept | Phase 2 time |
|---|---|---|---|---|---|---|---|---|---|---|---|
| tiered | T = 14 | 585 | 446 | 1.31 | 16% | 28% | 69 / 45 | 45 | 17 | 2 + the altars | 243 s |
| grouptiered | 12 | 613 | 445 | 1.38 | 15% | 24% | 65 / 41 | 41 | 20 | 2 + the altars | 182 s |
| essential | 10 | 647 | 440 | 1.47 | 16% | 27% | 70 / 35 | 35 | 14 | 2 + the altars | 207 s |
| essentialcap | 10 | INFEASIBLE, fell back to Phase 1 | | | | | | | | | 83 s |

Reading:

- **The window, not the measure, sets the distinct combo count.** All three tiered
  variants sit at the floor of the window (440 to 446 combos for a floor of 458 weighted).
  Phase 2 gives away the whole tolerance whatever it measures; the measure only decides
  which combos go.
- **The measure decides what is kept.** `essential` keeps 62 more variants than `tiered`
  (1.47 per combo against 1.31) for the same distinct combos, and brings the combos that
  depend on Ashnod's Altar alone from 45 to 35 while the altar stays in 70: the cover came
  from Phyrexian Altar, Viscera Seer and Carrion Feeder, which all gained combos. The
  number of cards above twice the median essential count went 17 to 14. `grouptiered` is
  between the two.
- **The hub share is a property of the pool.** Ashnod's Altar is in 9% of the pool's
  combos (814 of 8,596 at 20,000 variants; Phyrexian Altar 7%), and a cube that maximizes
  combos over-samples that dense cluster to 23% (Phase 1 here, 16% in the tracked Phase 1
  cube). Every objective brought it to 15 to 16%, because that is what the window's 10%
  buys when the altar's combos are the ones given up. Pushing the share lower is a matter
  of the tolerance, or of excluding altar combos, not of the balancing measure.
- **A hard essential cap does not fit inside the window.** At the default cap (10, twice
  the Phase 1 median) the model is proven infeasible in 83 s: the altar is essential to 78
  Phase 1 combos and cannot be covered or dropped that far within 10% of the reference. A
  cap would need either a much looser value (the L rung tries 90, half the tracked Phase 1
  altar's 183) or no window, which makes it a Phase 1 rule rather than a Phase 2 objective.
- The time limit was reached by every objective, so the differences are between
  time-limited solutions, not optima; the ordering matched the S rung, where every solve
  was optimal.

### L rung (310 cards, 20,000 variants, 360 s; all defaults)

Four runs with the same inputs (the tracked cube is the `tiered` run). Phase 1 and the
reference solve both ran to the 360 s limit every time (FEASIBLE), and their results moved
from run to run; the window floor is 90% of the reference, so it moved with them. Named
cards only, against the 20,000-variant pool.

| Run | Phase 1 variants / combos | Reference variants / combos / weighted | Window floor (weighted) |
|---|---|---|---|
| tiered (tracked cube) | 2,531 / 1,584 | 1,658 / 1,151 / 1,201.7 | 1,081.5 |
| grouptiered | 2,571 / 1,548 | 1,772 / 1,232 / 1,286.0 | 1,157 |
| essential | 2,492 / 1,561 | 1,601 / 1,096 / 1,146.5 | 1,031 |
| essentialcap | 2,563 / 1,579 | 1,787 / 1,066 / 1,138.1 | 1,024 |

| Objective | Cap | Phase 2 status | Variants | Distinct | Weighted | Variants per combo | Top card share | Top 5 share | Ashnod's Altar: combos / essential | Max essential | Cards > 2x median essential | Free outlets kept | Phase 2 time (with warm-start preparation) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tiered (tracked) | default | FEASIBLE | 1,470 | 1,029 | 1,081.0 | 1.43 | 17% | 36% | 177 / 130 | 130 | 46 | Viscera Seer, Carrion Feeder | 765 s |
| grouptiered | 24 | TIMEOUT, no solution, fell back to Phase 1 | | | | | | | | | | | 1,083 s |
| essential | 20 | FEASIBLE | 1,505 | 968 | 1,031.2 | 1.55 | 17% | 32% | 163 / 118 | 118 | 38 | Woe Strider, Viscera Seer, Carrion Feeder | 738 s |
| essentialcap | 90 (`--util-cap`) | TIMEOUT, no solution, fell back to Phase 1 | | | | | | | | | | | 741 s |

Reading:

- **`essential` is the only new objective that solves at full size**, and it moves the
  concentration numbers the right way: top-five share 36% to 32%, largest essential count
  130 to 118, cards above twice the median essential count 46 to 38, variants per distinct
  combo 1.43 to 1.55, cards in no named combo 29 to 24, and a third free outlet kept (Woe
  Strider). Ashnod's Altar still touches 17% of the combos, as at M: that share is the pool's.
- **The distinct combo deficit (968 against 1,029) is the reference solve, not the
  objective.** The solve ended at 1,031.2 weighted combos, on its floor of 1,031; the tracked
  run's floor was 1,081.5. The reference solve's weighted score ranged 1,138 to 1,286 over
  four runs of the same inputs (a 12% spread) because it stops at the time limit, so the
  floor every Phase 2 objective is measured against is noisier than the differences between
  the objectives. Fixing this comes before any further comparison at L.
- **The swaps are the cards the potential-combos investigation named.** Against the tracked
  cube, the `essential` cube adds Sol Ring, Lion's Eye Diamond, Lotus Cobra, Tireless
  Provisioner, Ghostly Flicker, Woe Strider, Herd Baloth, Selvala, Heart of the Wilds and
  Suture Priest, the cards the `tiered` objective had pushed out for being in many variants.
  96 cards differ in all; a good part of that is run-to-run noise, since the Phase 1 cubes
  differ too.
- **`grouptiered` has no solution at L.** The warm-start repair found no cube (19 cards
  below the utilization floor; at M it succeeded), so the solve started without a hint, and
  with 10,224 extra participation variables it found nothing in 360 s (690,967 branches).
  It was never better than `essential` at S or M. Drop it.
- **`essentialcap` has no solution at L even at 90.** The repaired warm start (986 combos,
  1,054.5 weighted) breaks the cap (the altar is essential to 118 to 130 combos in every
  cube found), so the hint is infeasible and the solver found nothing in 360 s (681,067
  branches). A hard cap would have to sit above the hub's natural level, which makes it
  pointless, or run without the window and much longer, which makes it a Phase 1 rule. Drop
  it; the question of balance as a rule is answered for now.
- The `essential` solve made 1,122 branches and 1 conflict: the whole 360 s went into
  improving the repaired warm start (1,020 combos, 1,071.5 weighted) by local search, giving
  up 40 weighted combos down to the floor in exchange for essentiality. The hint matters;
  the solve never proves anything at this size.

## Recommendation

1. **Make `essential` the default Phase 2 objective**; keep `tiered` as a choice (it is the
   objective the tracked cube was built with). Remove `grouptiered` and `essentialcap` from
   the prototype before committing. The cap stays twice the Phase 1 median of the measure
   (20 at L; the tiers are then 20, 40 and 80, and the altars pay the top tier).
2. **Stabilize the reference solve before the L comparison is repeated.** The floor should
   not move 12% between runs of the same inputs. Cheapest: warm-start the reference solve
   from the previous output cube when one exists (`-o` target, or a `--warm-start` file);
   the tracked cube satisfies every rule, so it is a feasible hint and the reference can only
   improve on it. Alternatively give the reference solve its own, longer limit
   (`--reference-time`). Then re-run L `tiered` and `essential` from the same reference and
   compare distinct combos on equal floors.
3. **Reporting (Step 2) as planned**: distinct and essential counts per card beside the
   variant count in the utilization statistics and `top_utilized_cards`, and the
   concentration block (top-card share, top-five share, largest essential count) in the
   stats file, so that what the objective now minimizes is what the stats show.
4. **Leave the utilization floor in variants** for now. The warm-start repair already fails
   at L when 19 cards are below the floor; a stricter floor would make it fail more often.
   Revisit with the floor measured in distinct combos once the reference solve is stable.
5. **Template fillers stay out of the essential measure.** The top essential cards at L are
   all named pieces (the altars, Ghave, Altar of Dementia, Blasting Station); no single-card
   pool shows up.

## Open questions

- Should the utilization floor move to the new measure too (a card must be in two distinct
  combos, rather than two variants)? Today two variants of one combo satisfy it. Deferred
  (recommendation 4).
- `essential` ignores template fillers. A pool of one card in the cube makes that card
  essential to the combo; `card_inclusion` counts it, the model does not. Closed for now
  (recommendation 5): no single-card pool is among the top essential cards at L.
- Balance as a rule (`essentialcap`) is dropped; if the Phase 2 objective is ever freed for
  popularity or the potential-combos ideas, it will be by a different route (a third phase,
  or the reference solve carrying the balance).
- How much of the 96-card difference between the tracked cube and the `essential` cube is
  the objective and how much is solver noise? Answerable only once the reference solve is
  stable (recommendation 2).
