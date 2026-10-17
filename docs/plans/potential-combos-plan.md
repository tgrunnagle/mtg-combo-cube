# Potential Combos Plan

Written October 2026. Use Commander Spellbook's "potential combos" (combos the cube is one
piece short of) to improve the cube: a one-card-away report after every build, the cards of
the 90 reserved slots made known to the optimizer as fixed cards outside the budget, and a
staples rule that keeps the most popular combos of the pool in the cube. Eighth playability
plan; the seventh is [balancing-plan.md](balancing-plan.md), which merges first (see "Relation
to the balancing plan").

Out of scope, by decision: rewarding potential combos themselves. A combo whose missing piece
is in nobody's pool cannot be drafted, so it is worth nothing in the cube; its value is as a
signal of what to add, which the report and the fixed cards capture. Also out of scope: the
utilization balancing itself (measured in the same session: the tiered objective removes
redundant sac outlets and leaves the hubs where they are; a separate plan).

## Context for a new session

- Read the "Context for a new session" sections of
  [archetype-support-plan.md](archetype-support-plan.md) (a Phase 2 hard constraint is one
  entry of the `_cube_rules` registry: `add(base)` plus `violations(cards)`, applied to the
  Phase 2 model, the warm-start repair models and the check of the Phase 1 cube) and
  [enabler-support-plan.md](enabler-support-plan.md) (card tables, enabler-only candidates,
  the floors; the decision that ramp and tutors live in the 90 reserved slots).
- The 400-card cube is 310 combo cards the optimizer builds plus 90 slots filled by hand:
  lands, ramp and interaction. The current list of those 90 (70 distinct names, the dual and
  fetch lands twice) is the file `ComboCubelevel2.txt`, a Cube Cobra export with a
  `# mainboard` header: removal and counterspells, the five Moxen, ten Talismans, the ten dual
  lands, five Triomes and five of the Streets of New Capenna tri-lands, the ten fetch lands
  and Wasteland.
- Spellbook's Find My Combos page (`commanderspellbook.com/find-my-combos`, the link the
  card data file carries since PR 34) lists for a deck list the combos it completes and the
  combos it is one card short of. The backend is
  `POST https://backend.commanderspellbook.com/find-my-combos/?limit=1000` with the body
  `{"main": [{"card": <name>, "quantity": 1}, ...], "commanders": []}` (a list of plain
  names is a 400); `count` is null, so follow `next` until it is. The result lists are
  `included` and `almostIncluded` (plus the commander and color variants of each), every
  entry a full variant with `uses`, `of`, `popularity` and `requires`. It searches all of
  Spellbook, not the pool: the variants with five or more cards and beyond the 20,000 most
  popular are in it too.
- Where the pieces of this plan live: the instance is loaded by `ilp_runner.load_instance`
  (variants from the cache, `ComboPreprocessor` resolving templates into
  `requirement_options`); the candidate cards are every card a variant names or a template
  pool holds, plus the payoff and enabler cards the pool lacks (`add_payoff_cards`,
  `add_enabler_cards`). The base model (`_build_base_model`) has `sum(x) == cube_size`
  over `all_cards`; `_add_utilization_floor` forces `x = 0` for a card that cannot reach the
  floor unless it is in `floor_free_cards`. `cube_evaluation.completable_combo_ids` decides
  what a card set completes, `group_popularity` gives each group its most popular variant's
  popularity, `card_inclusion` builds the per-card data. `evaluate_cube --card-data`
  reproduces a build's statistics for a cube file from the caches.

## Problem

### What the potential-combo list says about the tracked cube

Against `data/current_best_cube.txt` (310 cards, built 13 October 2026, 1,477 completed
variants in 1,037 distinct combos on the 20,000-variant pool), Find My Combos reports:

| Measure | Value |
|---|---|
| Included variants (distinct combos) | 1,961 (1,252) |
| Potential variants (distinct combos) | 13,376 (5,008) |
| Potential variants inside the pool | 4,774 |
| Included variants inside the pool | 1,451 |

Two different lists hide under "potential combos":

- **By number of variants**, the missing cards are the ones Phase 2 removed: Woe Strider (54
  potential variants), Bartolomé del Presidio (53), Yahenni (49), Umbral Collar Zealot (40),
  Selvala (40), Herd Baloth (33). Free sac outlets and other redundant pieces; the balancing
  objective's doing, and a separate question.
- **By popularity**, the missing cards are the staples the cube is one card short of. From
  the pool, with the project's own template resolution (the one-card-away computation of
  Step 1):

| Missing card | Potential variants | Distinct combos | Combos the cube lacks | Summed popularity |
|---|---|---|---|---|
| Sol Ring | 10 | 10 | 5 | 375,045 |
| Tireless Provisioner | 8 | 5 | 0 | 218,906 |
| Lion's Eye Diamond | 10 | 5 | 3 | 185,801 |
| Lotus Cobra | 4 | 4 | 0 | 182,600 |
| Jeska's Will | 1 | 1 | 1 | 89,628 |
| Restoration Angel | 6 | 6 | 2 | 81,961 |
| Ghostly Flicker | 9 | 5 | 2 | 75,993 |
| Sensei's Divining Top | 1 | 1 | 1 | 51,193 |

Three reasons the optimizer passes on them:

1. **Popularity is not in the objective.** The tracked build ran with `--popularity-weight
   0`. Of the pool's most popular combos (groups ranked by their most popular variant) the
   Phase 1 cube completes 0 of the top 10, 4 of the top 25 and 21 of the top 100; the final
   cube 5, 12 and 31, and those came in through the win outcome minimum (the Thassa's Oracle
   combos). Hullbreaker Horror plus Sol Ring, the most popular combo on Spellbook, counts the
   same as any obscure one.
2. **Group deduplication.** Springheart Nantuko's combo is complete through another landfall
   piece, so Provisioner and Lotus Cobra add no new combo and only further variants at a
   tenth each. Sol Ring adds 5 combos the cube lacks, the same marginal value as the weakest
   cards Phase 1 kept (4 to 5 combos), so it lost a tie the objective is indifferent to.
3. **The 90 reserved slots are invisible.** The model does not know the fixed cards, so it
   cannot count the combos they complete with the 310, can build no combo piece toward them,
   and may spend a slot of the 310 on a card the 90 already hold.

### The fixed cards today

Measured with the current list (70 distinct names) against the tracked cube, by the API and
locally with the pool: the fixed cards complete **no** combo with the cube (1,961 included
variants with and without them; 1,477 in the pool either way), and no potential variant has a
fixed card as its missing piece. Their footprint in the pool is small: the fetch lands are
named by 11 to 18 variants each (landfall combos), Swords to Plowshares by 8, Toxic Deluge,
Lightning Bolt and Wrath of God by one; with them present three more cards become one card
away (Ghost Town, Jumbo Cactuar, Biorhythm). So today the feature buys correctness rather
than combos:

- 15 of the fixed cards are candidate cards (the fetch lands, six Talismans, Swords, Bolt,
  Wrath, Toxic Deluge, through template pools or as named pieces), and the five Moxen are
  enabler cards of the `fast_mana` category. A build can spend one of the 310 on a card that
  is already in the 400.
- The value grows with the list: once Sol Ring (ramp, so a card for the 90 by the standing
  decision) is on it, Hullbreaker Horror plus Sol Ring is a completed combo the model knows,
  and the one-card-away report says which fixed-slot candidates would complete the most.

### The 20,000-variant pool

Two thirds of Spellbook's potential variants and a quarter of its included ones are outside
the pool (five or more cards, or below the popularity cutoff). They are the tail; the report
says how much of the site's number is in reach of the optimizer, and nothing in this plan
widens the pool.

## Decisions

Taken on 16 October 2026, before implementation.

1. **Where the fixed cards come from.** A text file in the cube file format (one name per
   line, `#` comments, duplicates collapsed, the format `read_cube_file` accepts), tracked
   as `data/fixed_cards.txt` and named by a new `fixed_cards` key of `config.yaml` (a path,
   relative to the working directory), with `--fixed-cards <file>` on the build and evaluate
   commands as an override. The Cube Cobra export is copied in as is, lands included: they
   complete nothing today, but landfall combos name fetch lands and a future template would
   be filled by them, and carrying them costs nothing. The alternative, a list of names in
   `config.yaml`, would put 90 lines of lands beside the tables.
2. **What a fixed card counts for.** It is in the cube for every rule that counts combos
   (the score and the window, the archetype and outcome counts, the wide cap, the
   utilization of the other cards, coverage), for the payoff and enabler floors and the
   required cards, and for nothing that shapes the 310: not the cube size, the card mix caps
   and floor, the color balance, the utilization floor, the utilization statistics or the
   balancing objectives. The five fixed Moxen therefore meet most of the `fast_mana` floor:
   the floor meant "the cube holds enough fast mana", and the 400 does. Lotus Petal and the
   other fast mana that are combo pieces stay in the 310 on their own merit. Should the
   first build turn the freed `fast_mana` slots into cards nobody wants, the floor on top of
   the fixed cards is a one-line change (leave fixed cards out of `payoff_cards` and
   `enabler_cards`), recorded in the results section.
3. **The staples rule's unit.** Variants, as Spellbook ranks them and as the user reads the
   list; the rest of the optimizer counts groups. A *staple* is the set of named cards of a
   variant among the N most popular variants of the pool (the two Springheart Nantuko plus
   Tireless Provisioner variants, which differ only by template, are one staple), satisfied
   when any variant with those named cards is completed; the rule asks for at least K of the
   N staples. The group alternative would never ask for Provisioner or Lotus Cobra, because
   their groups are complete through other pieces.
4. **Hard rule, not a weight.** The soft version exists, `--popularity-weight`: a log scale
   that makes the most popular combo worth `1 + w` combos and a median one about
   `1 + 0.45 w`, too flat to carry Sol Ring on its own. The plan builds the rule; Step 4
   measures the weight beside it for the record.
5. **The report's home.** The stats file gets a short `one_card_away` block (the totals and
   the top ten pieces with their counts, no links), the build log and `evaluate_cube` print
   the same ten, and the full report with its Spellbook links goes to its own file,
   `<output>_potential_combos.json`, beside the card data file: fifty rows with a search URL
   of up to ten variant ids each is too long for the stats file. The card data file is
   untouched; its entries are the cube's cards.
6. **Which cards go where.** Sol Ring is ramp by the standing decision and goes on the fixed
   list, not in the 310. Lion's Eye Diamond and Jeska's Will are enablers and combo pieces
   and belong in the 310; the staples rule is the way to them. Sensei's Divining Top needs
   Mystic Forge or Foundry Inspector as well, a three-card staple the rule asks for as one,
   and gets no special handling.
7. **No Spellbook call from the tool.** Spellbook's `almostIncluded` is the ground truth for
   the whole site and the local report covers the pool. The report states the difference
   (the pool's share of the site's potential variants) and the Find My Combos link already
   shows the site's list, so no `--from-spellbook` option is built.

## Relation to the balancing plan

Checked on 16 October 2026 against [balancing-plan.md](balancing-plan.md), which replaces
the Phase 2 objective's measure (`essential`: the combos only the card completes, tiers
above twice the Phase 1 median) and recommends warm-starting the reference solve from the
previous output cube, and merges to `main` before this plan. The two plans touch different
things (that one the objective and the utilization statistics, this one the base model, a
cube rule, the configuration and the outputs), so the conflicts are textual: the result
fields, `_extract_solution`, the stats writer. Three points of design follow from it:

1. **Fixed cards are exempt cards.** The optimizer already has one set of cards the
   utilization floor, the utilization statistics and every balancing objective leave alone
   (`exempt_cards`, today the payoff-only and enabler-only cards), and decision 2 asks
   exactly that of a fixed card. So the fixed candidates join `exempt_cards` (after being
   left out of `payoff_only_cards` and `enabler_only_cards`, whose caps they must not count
   against), and any objective written to the existing pattern, `essential` included, skips
   them without knowing about them. A fixed card still gives cover: a variant naming it
   counts in the sum that makes another card's combo non-essential.
2. **Step 4 waits for the merge.** The balancing plan measured a 12% spread in the reference
   solve's weighted score between runs of the same inputs, more than the few percent the
   staples rule is expected to cost; and the default objective changes. The measurements
   are made once the balancing plan's objective and reference warm start are on `main`, from
   one reference, with the staples rule and the fixed cards on top. Steps 1 to 3 do not
   depend on it and are built now.
3. **Both plans fix the same cards.** The balancing plan's `essential` cube at full size
   brought in Sol Ring, Lion's Eye Diamond, Lotus Cobra, Tireless Provisioner and Ghostly
   Flicker on its own. Step 4 therefore also asks how much the staples rule still adds under
   the new objective; if the answer is nothing, the rule stays available and off.

## Step 1: the one-card-away report

`cube_evaluation.one_card_away(selected_cards, combos, fixed_cards=())` returns, per missing
piece, the variants of the pool that the cube would complete with that one piece added: all
required cards but one selected and every requirement option filled (the piece is the card),
or every required card selected and exactly one option unfilled (the piece is the template,
reported as `template:<display name>`). Per piece: the variants, the distinct combos, the
combos the cube does not complete through another variant (`new_combos`), the summed and the
highest popularity, the most popular variant's cards as an example, and whether the piece is
a candidate card, a payoff or enabler card, or in no pool at all. Sorted by summed
popularity, the top 50 kept.

The cube evaluated is the selected cards *and* the fixed cards (decision 2: a fixed card
counts for everything that counts combos, and the Find My Combos link the report carries is
for the 400 too). A variant short of only a fixed card is complete, not potential, so a
fixed card is never a missing piece; and a variant that the fixed cards bring within one
card (Ghost Town, Jumbo Cactuar and Biorhythm with today's list) is reported, which the
310 alone would miss. This replaces the "fixed" classification the first draft of this plan
had; the fixed cards' own contribution is the `fixed_cards` block of Step 2.

Two outputs (decision 5). The stats file gets a short `one_card_away` block: the pool's
totals (variants and pieces one card away, how many of the pieces are fixed cards) and the
top ten pieces with their counts. The full report goes to `<output>_potential_combos.json`,
written by `write_potential_combos` next to `write_card_data` and by `evaluate_cube
--card-data`: the same totals, the fifty rows, and per row the Spellbook search links
(`spellbook_links.search_urls` over the row's variant ids, ten per link) so a row can be
opened on the site, plus the Find My Combos link for the 310 and the fixed cards together.
`log_phase_summary` and `evaluate_cube` print the top ten.

The same function, run against the Phase 1 cube and the final cube, also tells how much the
balancing objective threw away in reach: that is the "by number of variants" list above.

Cost: a pass over the combos per call, the same as `completable_combo_ids`.

## Step 2: fixed cards

1. **Configuration.** `fixed_cards` in `config.yaml` names the file; `load_config` reads it
   into `CubeConfig.fixed_cards: frozenset[str]` through `read_cube_file` (moved to a place
   both the runner and `evaluate_cube` import from, with its comment and duplicate handling).
   A fixed card that is in the blocklist is a `ConfigError`. `--fixed-cards` on the build
   and evaluate commands overrides the file; `precache` looks the fixed cards up on Scryfall
   with the candidates so their attributes are cached.
2. **The instance.** `build_cube_ilp` passes `fixed_cards` to the optimizer after the payoff
   and enabler cards are added; a fixed card that is not a candidate (a dual land) is
   recorded and otherwise ignored, with the count logged. `ILPOptimizer` gets
   `fixed_cards: frozenset[str]`, kept as `self.fixed_cards` (the fixed candidates) and
   removed from the sets that shape the 310: `multicolor_cards` and the other card mix sets,
   the color balance counts, `payoff_only_cards` and `enabler_only_cards` (a fixed Mox is
   neither), the utilization floor and statistics, the balancing objectives and the
   versatility bonus. They join `payoff_cards` and `enabler_cards` of their categories and
   satisfy `required_cards` (decision 2).
3. **The base model.** `x[f]` exists for every fixed candidate and is fixed to 1; the cube
   size constraint sums over `all_cards` minus the fixed cards. Nothing else in the model
   changes: the `y` constraints see `x[f] = 1`, so the combos the fixed cards complete count
   in the score, the window, the group indicators and the utilization of the other cards;
   the coverage rule counts them for the templates they fill.
4. **Checks on card sets.** Everything that evaluates a card set outside the model (the
   `violations(cards)` of each rule, `_combo_score`, the warm-start checks, the utilization
   and inclusion statistics) is given the selected cards plus the fixed cards where the model
   counts them, and the selected cards alone where it does not (card mix, color balance,
   utilization floor). The cleanest shape: `OptimizationResult.selected_cards` stays the
   310, a new `fixed_cards` field carries the fixed candidates, and the evaluation helpers
   take `fixed_cards` explicitly.
5. **Output.** The cube file is the 310. The stats file gets a `fixed_cards` block: the
   list, how many are candidates, the completed variants and combos they take part in, and
   their payoff and enabler categories; the `payoffs` and `enablers` blocks show the fixed
   cards among the category's cards. The card data file gets an entry per fixed card that
   takes part in a completed combo, with the reason `fixed` and the usual combo lists and
   Spellbook links, after the 310. `evaluate_cube` applies the same list, so a build and its
   evaluation agree.
6. **Docs.** README (configuration and the output files), architecture.md (base model, the
   cube rules paragraph on what counts fixed cards, the output table), the configuration
   docstring in `config.py`.

## Step 3: the staples rule

1. **Staples.** `staples(combos, count)` in `cube_evaluation`: the N most popular variants of
   the pool (ties by id), collapsed by their set of required cards, each staple with its
   variants' ids, its popularity (the highest) and its named cards. A variant whose named
   cards are a superset of another staple's is still its own staple (Brain Freeze plus Breach
   plus Lotus Petal, and the same with Lion's Eye Diamond, are two).
2. **The rule.** `--staple-combos N` (default 0, off) and `--min-staple-combos K` (default
   0: with N above 0 and K at 0, K is N, every staple required). One
   `_CubeRule("staples", _add_staple_minimum, _staple_violations)`. For a staple whose
   variants are a whole group the term is the group indicator; otherwise a bool
   `s <= sum(y of its variants)`, the `_fitting_group_count` construction (under-countable,
   so the minimum is exact). The constraint is `sum(s) >= K`. `violations(cards)` is the
   shortfall against the staples the card set completes. The pool check warns when K is
   above N or N above the pool.
3. **The utilization floor.** A staple's pieces may be in fewer than `--min-util-floor`
   combos of the pool (Mindcrank 2, Bloodchief Ascension 3) and would need every one of them
   completed to be selectable. With the rule on, the named cards of the N staples are added
   to `floor_free_cards`, as the required cards are, for the same reason (in the cube by
   decree). They stay in the utilization statistics and the objectives.
4. **Reporting.** The `phase2` block records N, K and the staples completed; the stats file
   lists the staples with their status and missing pieces (a short list, N rows), which is
   also the answer to "which popular combos does the cube not have".
5. **Docs.** README, architecture.md (the cube rules list: item 16, before the objective),
   the CLI help.

## Step 4: measure and choose the defaults

After the balancing plan has merged (see "Relation to the balancing plan"): with its default
objective and its reference warm start, every build below from the same reference. All on
the full pool with the tracked settings (`task build:ilp`, 310 cards, 360 s per
phase, 8 workers, the fixed-card file in place), reporting the weighted and distinct combo
counts, the Phase 2 status, the staples completed, the cards that enter and leave against
`data/current_best_cube.txt`, and the top of the one-card-away report:

1. Baseline with the fixed cards alone (Step 2): what the `fast_mana` slots and the fixed
   fetch lands change.
2. The staples rule at N=25 K=20, N=50 K=40 and N=100 K=70; expect each forced two-card
   staple to cost its two slots against about four combos each, so N=100 is about 10% of
   the cube.
3. `--popularity-weight 0.5`, `1` and `2` without the rule, for the soft comparison: the
   staples completed and the cost in distinct combos (the user asked for a slight bias
   toward popular combos to be considered beside the rule, hence the 0.5).
4. The user's call on which staples to leave out (the rule has no exclude; a staple nobody
   wants goes to the blocklist by its pieces, or K drops by one).

Choose the defaults from the table: the rule is on by default only if a setting completes a
clear majority of the top 25 for a few percent of the weighted combo count.

## Tests

- `test_cube_evaluation.py`: `one_card_away` on a hand-built instance: a card missing from a
  completed group's other variant counts as a variant but not a new combo; a template piece;
  a fixed card's entry marked; sort order and the top-50 cut. `staples` collapsing variants
  with the same named cards and keeping a superset as its own staple.
- `test_config.py` and `test_evaluate_cube.py`: the `fixed_cards` key, a missing file, a
  fixed card in the blocklist, the CLI override, duplicates and comments in the file.
- A new `test_ilp_fixed_cards.py`: a fixed card completes a combo that counts in the score
  and the window without a slot; a fixed card is not counted by the card mix caps, the color
  balance or the utilization floor; a fixed Mox counts for its enabler floor; the Phase 1 and
  Phase 2 cubes return the 310 without the fixed cards; the violation checks agree with the
  model (a cube that satisfies a rule only through a fixed card is not repaired).
- A new `test_ilp_staples.py`: the rule pulls a popular two-card combo into a cube of denser
  combos; a staple of two template variants is one; K above N warns; the floor exemption of
  a staple's pieces; the violation count; the rule off has no violations; the Phase 1 style
  repair model cannot meet the minimum with an uncompleted staple (one-sided `y`).
- `test_ilp_runner.py`: the stats blocks `one_card_away`, `fixed_cards` and the staples
  list; the potential combos file (rows, links, the Find My Combos link over the 310 plus
  the fixed cards); the card data entries of fixed cards.

## Verification

- `task check` green.
- `evaluate_cube --card-data data/current_best_cube.txt` with the fixed-card file: the
  completed counts unchanged (1,477 / 1,037), the `one_card_away` block and the potential
  combos file headed by Sol Ring with 10 variants and 5 new combos, matching the table
  above, and the first search link of that row returning those ten variants on Spellbook.
- The full builds of Step 4, with the stats, the cube files and the Find My Combos links
  recorded in a results section of this document, and `data/current_best_cube*` updated if a
  setting is adopted.

## Open questions

The questions raised while planning (the lands in the fixed file, where Sol Ring belongs,
a Spellbook call from the tool) are decisions 1, 6 and 7. Item 4 of Step 4 (which staples
to leave out) was settled on 16 October: the user asked for every staple of the top 25 and
for Bolas's Citadel, alone among the cards that left, to be required; see the end of the
Results.

## Results

### Steps 1 to 3 (16 October 2026)

Built as planned, with the changes the balancing cross-check asked for (fixed cards as
exempt cards; the one-card-away report over the cube and the fixed cards together).
`data/fixed_cards.txt` is the Cube Cobra export (90 lines, 70 names) plus Sol Ring
(decision 6), 71 fixed cards, 21 of them candidates. `task check` is green (952 tests).

`evaluate_cube --card-data --staple-combos 25` on the tracked cube with the fixed cards:

| Measure | Without fixed cards (13 October) | With the fixed cards |
|---|---|---|
| Completed variants / distinct combos / weighted | 1,477 / 1,037 / 1,081.0 | 1,487 / 1,042 / 1,086.5 |
| Variants a fixed card takes part in / distinct combos | | 13 / 12 (Sol Ring 10 as a piece and 3 as a template filler, Talisman of Dominance 1) |
| Variants one piece short / distinct pieces | | 4,832 / 1,578 |
| Staples of the 25 most popular variants completed | | 10 of 23 |

Sol Ring on the fixed list adds exactly the 10 variants and 5 combos the Problem table
predicted. The report's head is then the table minus Sol Ring, in the same order: Tireless
Provisioner (8 variants, 0 new combos), Lion's Eye Diamond (10, 3), Lotus Cobra (4, 0),
Teferi, Time Raveler (5, 5: Displacer Kitten plus Sol Ring plus Teferi, in reach only with
Sol Ring fixed), Jeska's Will (1, 1), Restoration Angel (6, 2), Ghostly Flicker (9, 2),
Tidespout Tyrant (4, 1, also through Sol Ring), Bastion of Remembrance (7, 1), Selvala (15,
5). The first search link of each of the first four rows returns exactly its variants from
Spellbook's backend. The 13 staples the cube lacks are the two-card combos of the Problem
section (Exquisite Blood pairs, Dramatic Reversal plus Isochron Scepter, Dualcaster plus
Twinflame, Curiosity plus Niv-Mizzet) and the enabler staples (Lion's Eye Diamond twice,
Jeska's Will, Tireless Provisioner, Lotus Cobra).

The card data file lists the two fixed cards in a completed combo after the 310 with the
reason `fixed`; its Find My Combos link covers the 310 and the 71 fixed cards (8.7 KB).

### Step 4

Run after the balancing plan merged (16 October 2026, `main` at 8001411: the `essential`
objective, `--warm-start`). One change was made for it: a warm-start cube that breaks a
rule (the staples rule, which the previous reference was built without) now still hints the
reference solve instead of being replaced by the Phase 1 cube, and only cannot be the
reference itself, so every build below starts its reference solve from the same cube,
`data/current_best_cube_reference.txt`. The tracked cube with the fixed cards, by
`evaluate_cube`: 1,604 variants / 1,077 distinct / 1,129.7 weighted, 11 of the 23 staples
of the top 25, the report headed by Jeska's Will, Mana Vault (11 variants, 2 new),
Forsaken Miner, Restoration Angel, Ghostly Flicker (14, 6).

All seven builds ran on 16 October 2026 from `bbd910e` with the tracked settings (310 cards,
360 s per solve, 20,000 variants, 8 workers, the fixed-card file in place) and
`--warm-start data/current_best_cube_reference.txt`, about 19 minutes each; Phase 2 ended
`FEASIBLE` in every one, the reference solve, the floor repair and the Phase 2 solve each
running to their 360 s. The staples columns count the staples of the pool's 25, 50 and 100
most popular variants (23, 48 and 97 staples) that the cube and the fixed cards complete,
whatever the build's own N; the popularity columns describe the completed variants, of a
pool of 19,848 whose median popularity is 257. The last column counts the cards that enter
(as many leave) against the tracked cube of the morning, the `essential` build without the
fixed cards.

| Build | Variants / distinct / weighted | Reference weighted | Staples of the top 25 / 50 / 100 | Top-five share, most essential card | Completed variants among the pool's top 100 / 500 | Sum of popularity | Cards changed |
|---|---|---|---|---|---|---|---|
| Tracked cube, evaluated with the fixed cards | 1,604 / 1,077 / 1,129.7 | | 11 / - / - | 33%, 129 | 32 / 94 | 4.92 M | |
| 1. Fixed cards alone | 1,665 / 1,090 / 1,147.5 | 1,274.6 | 11 / 21 / 33 | 37%, 147 | 33 / 101 | 5.17 M | 47 |
| 2. Staples 25, minimum 20 | 1,619 / 1,036 / 1,094.3 | 1,216.1 | 20 / 31 / 42 | 32%, 124 | 43 / 113 | 6.26 M | 80 |
| 2. Staples 50, minimum 40 | 1,420 / 964 / 1,009.6 | 1,121.8 | 22 / 40 / 56 | 39%, 135 | 58 / 121 | 6.69 M | 96 |
| 2. Staples 100, minimum 70 | 1,303 / 860 / 904.3 | 1,005.3 | 21 / 37 / 70 | 30%, 92 | 72 / 154 | 7.25 M | 101 |
| 3. Popularity weight 0.5 | 1,618 / 1,092 / 1,144.6 | 1,275.5 | 8 / 16 / 29 | 35%, 143 | 29 / 95 | 4.69 M | 58 |
| 3. Popularity weight 1 | 1,605 / 1,092 / 1,143.3 | 1,278.6 | 11 / 20 / 32 | 31%, 132 | 32 / 99 | 5.07 M | 59 |
| 3. Popularity weight 2 | 1,670 / 1,075 / 1,134.5 | 1,278.3 | 8 / 17 / 30 | 35%, 133 | 30 / 102 | 5.07 M | 52 |

**The fixed cards alone** (build 1) move 47 cards and gain 61 variants and 13 distinct combos
on the tracked cube: the fixed Moxen meet most of the `fast_mana` floor as decision 2
expected, and the slots that frees go to combo pieces, not to cards nobody wants, so the
fallback of decision 2 (floors on top of the fixed cards) stays unbuilt. The staples do not
move (11 of 23) and the report keeps its head: Mana Vault (12 variants, 3 new combos),
Restoration Angel (6, 0), Forsaken Miner (7, 5), Jeska's Will (2, 2), Ghostly Flicker (15,
6).

**The staples rule** (build 2) costs what its reference solve costs: the reference weighted
count falls 4.6%, 12% and 21% for N=25, 50 and 100, and the Phase 2 cube, which sits at the
90% floor of its reference in every build, falls with it (1,094.3, 1,009.6 and 904.3 against
1,147.5). So the price is paid in Phase 1's optimum, not in the balancing, and it is close to
the plan's estimate: 20 of 25 gains 9 staples for 54 distinct combos, 6 per staple. At 20 of
25 the cube completes 20 of the 23 staples (the three left out are Dramatic Reversal plus
Isochron Scepter, Lotus Cobra plus Springheart Nantuko and Curiosity plus Niv-Mizzet, Parun),
and the popularity of what it completes rises all the way down: 31 instead of 21 of the top
50 staples, 42 instead of 33 of the top 100, 43 instead of 33 completed variants among the
pool's 100 most popular, the sum of their popularity up by 21%, the median from 531 to 577.
It also balances the cube a little (the top five cards in 32% of the combos instead of 37%,
the most essential card essential to 124 instead of 147), because the staples are two-card
combos that spread the combos over more cards. 40 of 50 and 70 of 100 cost 12% and 21% of
the weighted count for 2 and 1 more of the top 25; they are too expensive for a default,
though 70 of 100 gives the most balanced cube of the seven (30%, 92).

**The popularity weight** (build 3) does not bias the cube toward popular combos at any of
the three values: the completed variants among the pool's top 100 are 29, 32 and 30 against
33 without it, the sum of their popularity 4.69, 5.07 and 5.07 million against 5.17, the
staples of the top 25 8, 11 and 8 against 11, all within the spread between two solves of
the same inputs. The scale is too flat for the solver to feel (decision 4: the most popular
combo is worth `1 + w` combos and a median one about `1 + 0.45 w`, so at w=2 one top combo
is worth 1.6 median ones) while Phase 1 runs to its time limit at full size without reaching
the fine structure of its objective, and the 10% window on the popularity-weighted score lets
the plain weighted count fall a little further (to 89.7%, 89.4% and 88.8% of the reference)
without buying popular combos with the room. `--popularity-weight` stays 0.

**Defaults.** The staples rule is on by default at `--staple-combos 25 --min-staple-combos
20` on every layer, the `ILPOptimizer` constructor included (the tests turn it off in
their settings, as they do the other rules), and the `evaluate_cube` report defaults to
the same 25; the 20 of 25 completing
a clear majority of the top 25 for 4.6% of the weighted combo count. The 25/20 build became the
tracked cube (`data/current_best_cube*`, now with `data/current_best_cube_potential_combos.json`
tracked too) until the build below replaced it. Against the tracked cube of the morning it changes 80 cards: in come the
staple pieces Jeska's Will, Mana Vault, Tireless Provisioner, Springheart Nantuko, Exquisite
Blood, Sanguine Bond, Vito, Thorn of the Dusk Rose, Bloodthirsty Conqueror, Enduring
Tenacity, Dualcaster Mage and Twinflame, with Teferi, Time Raveler, Thran Dynamo and Sliver Queen among the
others; out go Doomsday, Laboratory Maniac, Jace, Wielder of
Mysteries, Hermit Druid, Devoted Druid, Heliod, Sun-Crowned, Kitchen Finks, Timetwister, Time
Warp, Walk the Aeons, Gilded Lotus, Bolas's Citadel, Zulaport Cutthroat and the enablers
Entomb, Buried Alive, Seething Song and Desperate Ritual (the floors are met by Lively Dirge,
Tormenting Voice, Infernal Plunge, Rite of Flame and Magus of the Wheel instead; the floor
has no preference among a category's cards). The one-card-away report of the new cube is
headed by Lotus Cobra (4 variants, 0 new combos: Springheart Nantuko is in), Forsaken Miner
(11, 5), Marauding Blight-Priest (2, 0), Zulaport Cutthroat (4, 0), Ghostly Flicker (8, 3),
Restoration Angel (12, 1), Tidespout Tyrant (6, 2) and Rhythm of the Wild (16). Which of the
three missing staples to require, and whether any card that left should be a required card,
is the user's call (Open questions).

**The user's call (16 October 2026, later).** Every staple of the top 25, and of the cards
that left, Bolas's Citadel alone is to be required. So `--min-staple-combos` defaults to 25
(the minimum is lowered to the staples there are, 23, with a warning; 0 still asks for all of
them) and `config.yaml` gets a sixth enabler category, `top_deck`, holding Bolas's Citadel as
its one required card with no floor of its own, the pattern of the `recursion` category
(Citadel is a lifegain payoff card too and stays one). The build, from the 25/20 build's
reference as the hint (which breaks the new required card and three staples, so it hints but
is not the reference), ended `FEASIBLE` with 1,511 variants / 1,027 distinct / 1,075.4
weighted against the 25/20 build's 1,619 / 1,036 / 1,094.3: another 1.7% of the weighted
count and 9 distinct combos for the three staples and the Citadel, the reference falling the
same 1.7% (1,195.4 against 1,216.1). All 23 staples are completed (Phase 1 completes 4) and
Bolas's Citadel is in. The balance gives a little back (the top five cards in 34% of the
combos, the most essential card essential to 131). It changes 51 cards against the 25/20
build: in come Dramatic Reversal and Isochron Scepter, Lotus Cobra, Curiosity and Niv-Mizzet,
Parun, Bolas's Citadel, and back come Zulaport Cutthroat, Devoted Druid, Timetwister and Time
Warp; out go Altar of the Brood, Suture Priest, Peregrine Drake, Thran Dynamo, Grim Monolith,
Umbral Collar Zealot and Poison-Tip Archer among others. The report is headed by Forsaken
Miner (9 variants, 5 new combos), Marauding Blight-Priest (2, 0), Restoration Angel (6, 1),
Rhythm of the Wild (16, 4), Nissa, Resurgent Animist (4, 0) and Peregrine Drake (12, 6). This
build is the tracked cube (`data/current_best_cube*`). It was replaced the same day, after this plan merged, by
the build with Sensei's Divining Top required in the `top_deck` category too (1,496 /
1,021 / 1,068.5, 40 cards changed, the Top in and all 23 staples still completed).
