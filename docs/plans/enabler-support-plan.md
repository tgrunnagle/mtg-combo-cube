# Enabler Support Plan

Written October 2026. Make sure the cube holds the cards that set combos up: fast mana,
rituals, wheels and graveyard fillers (Lion's Eye Diamond, Lotus Petal, Dark Ritual, Seething
Song, Wheel of Fortune, Entomb). Sixth playability plan, and the second use of the "reserved
slots for non-combo playables" mechanism that [payoff-support-plan.md](payoff-support-plan.md)
introduced.

Out of scope, by decision: ramp (signets, talismans, Nature's Lore and the like) and tutors.
Both are covered by the cards added by hand to the 90 reserved slots of the 400-card cube.

## Context for a new session

- Read the "Context for a new session" sections of
  [archetype-support-plan.md](archetype-support-plan.md) (how a Phase 2 hard constraint is
  added through the `_cube_rules` registry: `add(base)` plus `violations(cards)`) and
  [payoff-support-plan.md](payoff-support-plan.md) (the payoff table, the `PayoffFetcher`,
  payoff-only candidates, the payoff floor). This plan reuses all of it; the implementation
  is mostly a generalization of `ilp/payoffs.py` and its plumbing in `ilp/ilp_runner.py`.
- The payoff machinery as it stands: the `payoffs` section of `config.yaml` gives, per
  outcome category, Scryfall `queries`, explicit `cards`, `exclude` and optional
  `min_payoffs` / `max_payoffs`. `parse_payoff_table` validates it; `PayoffFetcher`
  (`scryfall/payoff_fetcher.py`) resolves every query in EDHREC order over paper cards into
  `data/cache/scryfall_payoffs.json`; `resolve_payoffs` keeps the first `QUERY_CARD_LIMIT`
  (25) unblocked cards of each query, unions them with the table's cards and the inferred
  outlets, and drops the exclusions. `add_payoff_cards` adds every payoff card the pool lacks
  as a `CandidateCard` with no combos (a *payoff-only* card); `drop_unknown_payoff_cards`
  removes the ones Scryfall does not know. In the optimizer, `payoff_only_cards` are exempt
  from the utilization floor and the objectives, `payoff_cards` per category are the
  selectable ones (payoff-only, or in at least `min_utilization_floor` combos),
  `_compute_payoff_floors` splits `--payoff-share` of the cube among the categories by their
  combos in the pool (`_apportion`, D'Hondt, each at least `--min-payoffs` and at most twice
  the even split), `_add_payoff_floor` adds one constraint per category plus a cap on
  payoff-only cards (the floors' sum), and `_payoff_violations` checks a cube. The stats file
  has a `payoffs` block per phase and a top-level resolved table; the card data file gives
  each card its `payoff_categories` and the reason `payoff_only`.
- The utilization floor (`_add_utilization_floor`, `--min-util-floor 2`) forces `x[c] = 0`
  for any card in fewer combos than the floor, payoff-only cards excepted. A card with one
  variant in the pool can therefore never be selected today.
- Spellbook records which zone each piece of a variant must start in (`zoneLocations` of a
  `CardUse`: B battlefield, H hand, G graveyard, L library, E exile, C command zone). Nothing
  in the data says that a card helps assemble a combo; a ritual or a wheel appears in a
  variant only when it is itself a piece.

## Problem

### The optimizer only sees enablers as weak combo pieces

Lion's Eye Diamond and Wheel of Fortune are candidate cards, but their value to a drafter
(mana and cards to assemble any combo) is invisible to the model: a card scores only through
the variants it completes. On the cached 20,000 variants and the tracked 310-card cube
(`data/current_best_cube.txt`), the variants each card appears in, and the variants it would
complete if added to the cube on its own:

| Card | Variants in the pool | Completed if added alone | In the cube |
|---|---|---|---|
| Lion's Eye Diamond | 36 | 7 | no |
| Mana Vault | 45 | 8 | no |
| Windfall | 36 | 1 | no |
| Wheel of Fortune | 7 | 1 | no |
| Necropotence | 37 | 0 | no |
| Dark Ritual | 18 | 0 | no |
| Seething Song | 17 | 0 | no |
| Lotus Petal | 54 | in the cube | yes |
| Entomb, Buried Alive, Mana Crypt, Timetwister, Faithless Looting | 0 | not a candidate | no |

Two populations, as with payoffs: enablers the pool knows (LED, the rituals, Windfall) lose
their slot to pieces that complete more, and enablers it does not know (Entomb, Mana Crypt,
the looting spells) are not candidates at all. In between, a card with one variant (Chrome
Mox, Mox Diamond, Reforge the Soul) is excluded outright by the utilization floor.

The tracked cube holds four cards of the kind: Lotus Petal, Sol Ring, Mishra's Bauble and
Underworld Breach, every one of them there as a combo piece.

### What the pool asks for

The starting zones of the pieces say which enablers the combos need. Counted over the
20,000 cached variants (a variant counts once per zone):

| Zone a piece must start in | Variants |
|---|---|
| battlefield | 19,693 |
| hand | 6,830 |
| graveyard | 1,339 |
| exile | 678 |
| library | 143 |

Hand pieces want cards (wheels); battlefield pieces want mana to cast them (fast mana and
rituals); graveyard pieces want Entomb, Buried Alive and looting. These numbers are the
basis for a demand-weighted split of the floor later (see "Open questions"); the first
version splits evenly.

### The candidate pool

Scryfall queries for the four classes, run on 12 October 2026 in EDHREC order with the
payoff fetcher's `game:paper -is:funny` suffix (`unique=cards`). "In the pool" counts the
cards that are already candidates (in at least one cached variant):

| Category | Query | Cards | In the pool | Top of the EDHREC order |
|---|---|---|---|---|
| fast_mana | `t:artifact mv<=1 o:/sacrifice ~: add/ -t:land -t:creature -o:"draw a card"` | 5 | 3 | Lotus Petal, Lion's Eye Diamond, Lotus Bloom, Black Lotus, Jeweled Lotus (blocked) |
| fast_mana | `t:artifact mv=0 o:/\{T\}: Add/ -t:land` | 15 | 6 | Chrome Mox, Mox Amber, Mox Opal, Mox Diamond, Everflowing Chalice, Paradise Mantle, Sol Talisman, Mox Jasper, Mox Tantalite, Mana Crypt, the five Moxen |
| ritual | `(t:instant or t:sorcery) mv<=3 o:/^Add (\{[WUBRGC]\}){2,}/` | 9 | 7 | Dark Ritual, Seething Song, Cabal Ritual, Culling the Weak, Rite of Flame, Pyretic Ritual, Desperate Ritual, Infernal Plunge |
| wheel | `o:/each player discards (their\|his or her) hand(, then)?( and)? draws/ -t:planeswalker` | 11 | 5 | Windfall, Wheel of Fortune, Magus of the Wheel, Reforge the Soul, Jace's Archivist, Dragon Mage, Whispering Madness, Wheel of Fate |
| wheel | `o:/shuffles? (their\|his or her\|your) hand and graveyard into (their\|his or her\|your) library/ o:/draws? seven/ -t:planeswalker -t:creature mv<=6` | 9 | 1 | Midnight Clock, Echo of Eons, Timetwister, Day's Undoing, Time Reversal, Time Spiral |
| graveyard | `o:/search your library for [^\n]* put (it\|them\|that card\|those cards) into your graveyard/ mv<=3` | 7 | 2 | Entomb, Buried Alive, Goblin Engineer, Unmarked Grave, Lively Dirge |
| graveyard | `(t:instant or t:sorcery) mv<=2 o:/draw (two\|three) cards(, then\|\. Then) discard/` | 9 | 1 | Faithless Looting, Three Steps Ahead, Izzet Charm, Chart a Course, Careful Study |
| graveyard | `(t:instant or t:sorcery) mv<=2 o:/as an additional cost to cast this spell, discard (a\|two) cards?/ o:/draw/` | 6 | 0 | Thrill of Possibility, Cathartic Reunion, Tormenting Voice, Wild Guess |

With the explicit cards below, the draft table resolves to about 70 distinct cards, 25 of
them already in the pool. Every query is under the 25-card limit, so the limit never
decides; the EDHREC ordering matters only for reading the table.

Known imperfections of the queries, handled by `exclude` rather than tighter regexes:
Sol Talisman and Mox Tantalite (suspend) and Mox Jasper (needs a dragon) in fast_mana; the
double-faced reprints that `unique=cards` lists beside the original (`Blazing Firesinger //
Seething Song`, `Naktamun Lorespinner // Wheel of Fortune`). Wheel of Misfortune ("each
player secretly chooses a number") and Memory Jar (exiles the hand rather than discarding
it) match no query and are explicit `cards`. Black Lotus and the five Moxen match the
fast_mana queries; whether to block them is a budget question for `blocklist`, not for the
table (Jeweled Lotus is blocked already).

Ramp cards the queries catch anyway stay in the table when they are combo pieces: Paradise
Mantle (69 variants, in the tracked cube) and Everflowing Chalice (16 variants) count for
fast_mana like any other card. The floor is met by whichever fast mana the combos want, and
the cube does not lose a combo piece to a scope rule. If a category fills with such cards
instead of the targets, Step 3.3 catches it and `max_enablers` or `exclude` is the lever.

## Decisions to make

| Question | Recommendation |
|----------|----------------|
| Source of enabler cards | A table only, no inference. The payoff inference exists because Spellbook bundles an engine with its outlet; nothing in the data marks an enabler, so there is nothing to infer. The table is queries with `exclude`, explicit `cards` for the wordings a query cannot catch, exactly as the payoff table. |
| Where the table lives | An `enablers` section of `config.yaml` beside `payoffs`, same keys (`queries`, `cards`, `exclude`, `min_enablers`, `max_enablers`). Categories are free-form names (`fast_mana`, `ritual`, `wheel`, `graveyard`), not tied to the outcome table. |
| Code reuse | Extract the category-table parsing and resolution that `ilp/payoffs.py` and the payoff table share into a generic card table (`ilp/card_table.py`: `CardTableCategory`, `parse_card_table`, `resolve_card_table` -> `CardTable` with sources and `min` / `max` bounds); `payoffs.py` keeps the outcome check and the inference on top of it, `ilp/enablers.py` is the thin enabler-specific layer. `PayoffFetcher` already takes a `label` and resolves any query; the blocklist uses it the same way. One cache file. |
| Rule form | A hard floor per category, one linear constraint each, registered in `_cube_rules` as "enabler floor": `sum(x[c] for c in enabler_cards[category]) >= floor`. Same shape as the payoff floor, same warm-start repair path. |
| How much of the cube | `--enabler-share` default 0.05 of the cube (16 cards of 310), `--min-enablers` default 2 per category. Split evenly among the categories by `_apportion` with equal weights, each at least the minimum and at most twice the even split (so 4 each, capped at 8, on the default table), lowered to what a category can select, with the same warnings as the payoff floor. |
| Cards already in the pool | Count toward the floor, as combo-piece payoffs do. This is the point: LED, Lotus Petal, Dark Ritual and Windfall satisfy the floor and complete combos at the same time, so the solver prefers them to Lotus Bloom or Day's Undoing without any special scoring. |
| Cards the pool lacks | Added as `CandidateCard`s with no combos, exactly as payoff-only cards (`add_enabler_cards`, `drop_unknown_enabler_cards`); attributes fetched with the rest; they count for the color balance and card mix rules. |
| Enabler cards below the utilization floor | New behavior, the one place this plan differs from payoffs. An enabler card in fewer combos than `--min-util-floor` is *enabler-only*: exempt from the utilization floor (so Chrome Mox with one variant can be selected and its variant still scores), left out of the utilization statistics as payoff-only cards are, and capped in number by the floors' sum (`_enabler_only_cap`) so `mad` and `minmax` cannot fill the cube with them. An enabler card at or above the floor is an ordinary combo piece that also counts for its category. Payoff-only cards keep their stricter definition (no combos); a card in both tables is handled by membership in either set. |
| Where the slots come from | The share is of the built cube, as the payoff share is, so by default 16 of the 310 combo slots. The alternative, building 326 so the enablers come out of the 90 reserved slots, is a `-c` change and no code; the verification step runs both so the combo cost of each is known. |
| Reporting | An `enablers` block per phase in the stats file (count and cards per category, with the source of each, `enabler_only` listed), `min_enablers` and `enabler_floors` in `phase2`, a top-level resolved `enablers` table; `enabler_categories` and the reason `enabler_only` in the card data file; `evaluate_cube` reports the same. |

## Step 1: the card table and the configuration

1. `ilp/card_table.py`: move from `ilp/payoffs.py` the category dataclass (name, queries,
   cards, exclude, lower and upper bound), `_names` / `_bound`, the table parsing with its
   validation (a category needs a query, a card or an exclusion; bounds are whole numbers
   with `min <= max`; unknown keys are an error), `check_query_results` and the resolution
   of a category's card set (query results after the blocklist and exclusions, first
   `QUERY_CARD_LIMIT` per query, the explicit cards, each card with its sources, a warning
   for an exclusion that matches nothing). The bound keys are parameters (`min_payoffs` /
   `max_payoffs` for one table, `min_enablers` / `max_enablers` for the other) so the
   configuration stays readable. `payoffs.py` keeps `PayoffDefinitions.check_categories`,
   `infer_payoffs`, `PayoffTable` (the inferred source and counts) and its stats; every
   existing payoff test passes unchanged.
2. `ilp/enablers.py`: `parse_enabler_table` -> `EnablerDefinitions`, `resolve_enablers`
   -> `EnablerTable` (sources per category, `min_enablers` / `max_enablers`, `cards(name)`,
   `all_cards`, `source_counts`, `without`, `stats(selected, enabler_only)` ->
   `EnablerStats`), built on the generic table. Two sources (`card`, `query`).
3. `config.py`: the `enablers` section, `CubeConfig.enablers`, `require(enablers=True)`
   for a run with the floor on, `SECTIONS` extended. No dependency on the outcome table.
4. `config.yaml`: the default table from the query table above, with the exclusions and
   explicit cards named there, and a comment per category saying what it is for and what is
   deliberately left out (ramp that is not a combo piece, tutors).
5. `ilp_runner.py`: `fetch_enabler_queries` (the payoff fetcher with label "Enabler";
   rejected queries are a table error, unfetchable ones stop a run with the floor on),
   `build_enabler_table`, `add_enabler_cards`, `drop_unknown_enabler_cards`; `build_cube_ilp`
   resolves the table after the payoff table, adds the missing cards when the floor is on,
   passes the table to the optimizer and returns it for the stats. `precache.py` resolves
   the enabler queries with the payoff ones and fetches the attributes of the enabler cards
   a build adds. `evaluate_cube.py` resolves the table from the cache and reports it.

## Step 2: the enabler floor

1. `ILPOptimizer`: constructor arguments `enablers: EnablerTable | None`, `min_enablers`
   (default `DEFAULT_MIN_ENABLERS = 2`) and `enabler_share` (default
   `DEFAULT_ENABLER_SHARE = 0.05`), validated as the payoff ones. `enabler_only_cards`: the
   enabler cards in fewer combos than `min_utilization_floor` (payoff-only cards that are
   also enabler cards stay payoff-only). `enabler_cards` per category: the table's cards
   that are candidates (all of them, since enabler-only cards are selectable). Floors from
   `_compute_enabler_floors`: `_apportion(total, equal weights, lower, upper)` with the same
   capping and lowering as `_compute_payoff_floors`, logged the same way;
   `_check_enabler_pool` warns about a category the pool cannot fill.
2. `_add_utilization_floor`: skip enabler-only cards with the payoff-only ones. The
   utilization variable of an enabler-only card is still linked to its combos (it may be 0
   or 1); the `mad`, `minmax` and `tiered` objectives and the utilization statistics leave
   enabler-only cards out as they leave payoff-only cards out (`_payoff_only_cap` becomes a
   per-kind cap; the `minmax` lower bound and the deviation terms skip both sets).
3. `_add_enabler_floor(base)`: one constraint per category with a floor, and the cap on
   enabler-only cards (the floors' sum; with no floor every enabler-only card is fixed to
   0). `_enabler_violations(cards)`, `_describe_enabler_shortfalls` for the repair log.
   Registered in `_cube_rules` after the payoff floor.
4. `OptimizationResult`: `phase1_enabler_stats`, `phase2_enabler_stats`,
   `phase2_min_enablers`, `phase2_enabler_share`, `phase2_enabler_floors`; `_enabler_info`
   as `_payoff_info` (off without a table; a warning when the caller asked for a floor the
   configuration cannot give).
5. Flags `--min-enablers` and `--enabler-share` in `__main__.py`, validated like the payoff
   flags; plumbed through `run_ilp` and `build_cube_ilp`. `--min-enablers 0` turns the floor
   and the enabler-only candidates off, so the small test configurations in the README gain
   `--min-enablers 0`.
6. `cube_evaluation.py`: `enabler_categories` per card and the reason `enabler_only` for an
   enabler card in no completed combo (a payoff card in no combo stays `payoff_only`; a card
   in both tables with no combo is `payoff_only`); `write_card_data`, the card data summary
   and the README's description of the file updated. Stats blocks as decided above.

## Step 3: measure and choose the defaults

As the payoff plan measured: Phase 1 style solves under every Phase 2 cube rule plus the
enabler floor, 310 cards, 20,000 variants, 120 s, 8 workers, against the same solve without
the floor.

1. The combo cost of `--enabler-share` 0.03, 0.05 and 0.08 at `--min-enablers` 2, and how
   many enabler-only cards each pulls in. Expected: small, since 25 of the 70 enabler cards
   are combo pieces and the floor is 16 cards; record the distinct combos lost.
2. The same at `-c 326` (the enablers taken from the reserved slots) for the 0.05 share.
3. Which cards the floor chooses per category, against the target list (LED, Lotus Petal,
   Dark Ritual, Seething Song, Wheel of Fortune, Entomb). If a category fills with cards
   nobody wants (Lotus Bloom and Day's Undoing over LED and Windfall), that is a table
   problem, fixed with `exclude` or a `max_enablers`, not a model problem.
4. Choose the default share from the measurements; 0.05 is the starting point.

## Tests

Small instances, in the style of the payoff tests: the generic card table parses and
validates both key sets and rejects the same errors as before (the payoff tests move to the
generic module where they test the shared part); an enabler table with an unknown key, an
empty category or a bad bound is rejected; a query matching no card is an error;
`resolve_enablers` applies the blocklist, the exclusions and the 25-card limit; an enabler
card the pool lacks is added as a candidate and can be selected only when the floor asks for
it; an enabler card with one combo is selectable under a utilization floor of 2 and its
combo scores; an enabler card with two combos is an ordinary combo piece that still counts
for its category; the floor pulls a ritual into a cube that has none; a category with
fewer selectable cards than its floor is lowered with a warning; the enabler-only cap holds
under `mad` and `minmax`; enabler-only cards count for the color balance and the card mix
caps; a card in both tables is payoff-only; the stats blocks, the card data reasons and
`evaluate_cube`. Plumbing tests in `tests/unit/test_runner.py` (tests calling `run_ilp` must
fake `fetch_enabler_queries` as they fake `fetch_payoff_queries`); `precache` fills the
enabler queries.

## Verification

Full run at the chosen defaults against the tracked cube: enablers per category (and which
of the target cards made it), combos, utilization, colors, card mix, outcomes, payoffs,
Phase 2 time. Record the results here; update `README.md` (the configuration file
description, the Phase 2 options, the output files, the tracked cube paragraph),
`docs/architecture.md` (data pipeline, candidate pool, Phase 2 rule list, stats file),
`docs/plans/README.md` and the memory note. Replace the tracked cube and
`data/current_best_cube_command.txt` if the run is adopted.

## Open questions

- **Demand-weighted floors.** Split the share by what the pool asks for instead of evenly:
  fast mana and rituals by the mana value of the pieces, wheels by the variants with a piece
  in hand (6,830), graveyard fillers by the variants with a piece in the graveyard (1,339).
  A `weight` key per category in the table would do it without touching the model. Worth it
  only once the even split has been seen to put cards in the wrong category.
- **Color of the enablers.** Rituals are black and red, wheels red and blue, graveyard
  fillers black and red again. The color balance rule counts them, so the floor may push
  the cube's colors; the verification run will show whether the ratio needs room.
- **Should an enabler count only beside the combos it serves?** A ritual is worth more in a
  cube of expensive battlefield pieces, Entomb only beside graveyard combos. The stricter
  form needs a variable per combo and is left open, as the matching payoff question was.
- **Other enabler classes.** Reanimation spells (Reanimate has no variant; Animate Dead and
  Necromancy are pieces), cheat-into-play (Sneak Attack, 15 variants) and cantrips were
  sized during planning and left out to keep the floor small. Each is one more category in
  the table when wanted.
