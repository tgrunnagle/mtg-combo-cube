# Payoff Support Plan

Written October 2026. Make sure the cube holds the cards that turn its engines into wins:
outlets for infinite mana, storm count, tokens, life and counters. Fifth playability plan,
and the first use of the "reserved slots for non-combo playables" mechanism noted in
[combo-variety-plan.md](combo-variety-plan.md).

## Context for a new session

- Read the "Context for a new session" sections of
  [combo-grouping-plan.md](combo-grouping-plan.md), [archetype-support-plan.md](archetype-support-plan.md)
  and [combo-variety-plan.md](combo-variety-plan.md). The archetype plan describes how a
  Phase 2 hard constraint is added through the `_cube_rules` registry (`add(base)` plus
  `violations(cards)`, applied by the Phase 2 model, the repair models and the Phase 1
  check). The combo variety plan added the outcome categories this plan builds on:
  `data/outcome_categories.json`, `ilp/outcomes.py`, `ComboData.features`,
  `combos_per_outcome`, and the outcome minimum rule (`--min-outcome-combos`, default 40).
- The candidate cards (`ILPOptimizer.all_cards`, the `x` variables) are exactly the cards
  that appear in a variant's `uses` or in a template requirement's pool
  (`ComboPreprocessor.preprocess_variants`). A card in no combo is not a candidate and can
  never be selected. The utilization floor (`_add_utilization_floor`, `--min-util-floor 2`)
  forces `x[c] = 0` for any card that cannot reach the floor, so a card with no combos would
  also be excluded by it.
- Scryfall queries are resolved by `ScryfallFetcher.fetch_card_names` (disk cache
  `data/cache/scryfall_templates.json`, rate limited, retried); the preprocessor caps a
  template's pool at `REQUIREMENT_CARD_LIMIT = 10` cards. Card attributes (color identity,
  type line, mana value) come from `CardAttributeFetcher` and are fetched for every
  candidate card in `build_cube_ilp`.
- Spellbook's data model has combos that *need* a feature ("Infinite colorless mana" +
  Walking Ballista gives "Infinite damage"); the variant generator bundles such a combo with
  every engine that produces the feature. In the variant JSON this shows as `includes`
  listing more than one combo id; nothing marks which card is the outlet. The `/combos/`
  endpoint is not public (404); `/features/` is.

## Problem

### Engines without outlets

The outcome rule counts a combo for "mana" whether or not the cube holds anything to spend
the mana on, and a storm engine counts for "storm" with no storm card in the cube. In the
tracked cube (`data/current_best_cube.txt`, 1,007 distinct combos) 497 combos make infinite
mana and 179 an infinite storm count, but the cube has no Grapeshot, Tendrils of Agony,
Empty the Warrens or Brain Freeze, and its mana outlets are whichever ones happened to be
combo pieces (Walking Ballista, Thassa's Oracle, Laboratory Maniac, Aetherflux Reservoir).
A drafter who assembles an engine may have nothing to do with it.

### Two kinds of payoff

Payoffs the pool already knows about. Many outlets are combo pieces in their own right and
so are candidate cards; they get into the cube when their combos are popular, not because an
engine needs them. In the cached 20,000 variants:

| Card | Variants in the pool | In the tracked cube |
|---|---|---|
| Altar of Dementia | 447 | yes |
| Goblin Bombardment | 327 | yes |
| Walking Ballista | 148 | yes |
| Aetherflux Reservoir | 79 | yes |
| Blood Artist | 67 | yes |
| Zulaport Cutthroat | 54 | yes |
| Thassa's Oracle | 24 | yes |
| Exsanguinate | 22 | no |
| Sanguine Bond, Vito, Thorn of the Dusk Rose | 16 each | no |
| Purphoros, God of the Forge | 11 | no |
| Impact Tremors, Brain Freeze | 5 each | no |

Payoffs the pool does not know about. Spellbook lists "Infinite storm count" as an outcome
but casting a storm spell is not a combo, so Grapeshot, Tendrils of Agony, Empty the
Warrens, Craterhoof Behemoth, Finale of Devastation, Helix Pinnacle, Blue Sun's Zenith and
Mind Grind have no variants at all. They are not candidate cards, and no cube rule can
select a card that is not in the pool.

### What the data can and cannot infer

9,181 of the 20,000 variants produce a terminal result (damage, draw, mill, turns, win) and
6,636 of those bundle more than one combo, so engine-plus-outlet variants are common. But
inferring the outlet (the bundled variant's cards minus the cards of its engine
sub-variant, matched through `includes`) finds the engine in the pool for only 50 of 4,982
bundled terminal variants: the engines' own variants are mostly outside the top 20,000, and
Spellbook files the common outlet combos (Heliod + Walking Ballista) as self-contained
combos with a single `of`, so Walking Ballista is never inferred as an outlet. The
inference yields 16 outlet cards for mana, 14 for storm, 15 for tokens, 12 for counters and
4 for lifegain, several of them doubtful (Chromatic Orrery everywhere, three Chandras for
lifegain). It is a useful supplement, not a source.

## Decisions to make

| Question | Recommendation |
|----------|----------------|
| Source of payoff cards | Both. Inference from the pool (the cards a bundled variant adds to its engine sub-variant, matched through `includes`) gives the outlets Spellbook itself treats as payoffs, 4 to 16 per category today; a table `data/payoffs.json` of Scryfall queries adds whole classes of outlet Spellbook cannot see (storm spells, overrun effects, X-cost sinks) and excludes the inference's false positives. The table is queries, not card lists: a query stays current as cards are printed, is reviewable in one line, and resolves through the same cached fetcher as the template requirements. Explicit card names are allowed but should be the exception. The payoff set of a category is the union of the two, minus the exclusions. |
| Inference confidence | An inferred card counts when it is the extra card in at least two bundled variants of the category (`--payoff-inference-min 2`), which drops one-off accidents; a card that the inference assigns to every category (Chromatic Orrery) is still excluded by hand. |
| Fetching and caching | Like the card attributes: a `PayoffFetcher` modelled on `CardAttributeFetcher`, with its own versioned cache `data/cache/scryfall_payoffs.json` keyed by the query text (plus the order and limit), under the same `--read-api-cache` / `--skip-api-caching` switches, filled by `task precache`. A query is re-run only when it is missing from the cache, so with `--read-api-cache` a warm cache makes a build network-free and the resolved table is reproducible between the runs being compared. |
| Cards not in the pool | Add them as candidate cards with no combos (`CandidateCard` with empty sets), fetch their attributes, and exempt them from the utilization floor. This is the "reserved slots" mechanism; a later `data/playables.txt` of removal and fixing reuses it. |
| Rule form | A hard floor per category: at least `--min-payoffs` payoff cards of each category in the table (default 3), as one linear constraint each. The stricter "an engine counts only with an outlet present" needs a variable per combo and is left as an open question. |
| Which categories | Those the table lists. The terminal categories (damage, draw, mill, turns, win, lock) are their own payoff and need none. A category with no combos in the pool skips its floor. |
| Interaction with the outcome minimum | Independent rules. The outcome minimum keeps engines of every kind in the cube; the payoff floor keeps outlets for the non-terminal ones. |
| Slot cost | Payoff-only cards complete no combos, so each is a cube slot that scores nothing. Measure before choosing the default; keep the floor small. |

## Step 1: data and reporting

1. `src/mtg_combo_cube/ilp/payoffs.py`: load `data/payoffs.json`
   (`{"category": {"queries": ["keyword:storm f:commander", ...], "cards": [...],
   "exclude": ["Chromatic Orrery"]}}`), validate that every category names a category of the
   outcome table and has a query, a card or only exclusions. `scryfall/payoff_fetcher.py`
   (`PayoffFetcher`, modelled on `CardAttributeFetcher`) resolves the queries through
   `ScryfallFetcher`'s session (EDHREC order, limit 25 per query, blocklist applied; a query
   with no results is an error) and keeps the results in `data/cache/scryfall_payoffs.json`:
   a cache version, and per query the card names and when they were fetched. The build
   reads it under `--read-api-cache` and writes it unless `--skip-api-caching`, exactly as
   the card attributes; `precache` resolves the table after the attributes so a build with a
   warm cache needs no network. Changing a query in the table changes its cache key, so the
   edited query is fetched and the rest stay cached. Flag `--payoffs PATH`; ship the default
   file.
2. `infer_payoffs(variants, categories) -> dict[category, Counter[card]]` in the same
   module, run by the preprocessor while it has the `Variant` objects: for every variant
   whose `includes` lists more than one combo and whose features put it in a terminal
   category, find the variants in the pool whose single `of` id is one of those included
   combos, whose cards are a strict subset, and whose features are in a non-terminal
   category only; the set difference is the outlet, credited to the engine's categories.
   Keep a card for a category when its count reaches the confidence threshold. Today this
   yields mana 16, storm 14, tokens 15, counters 12, lifegain 4 before pruning (Blood Artist,
   Zulaport Cutthroat, Corpse Knight, Suture Priest, Altar of Dementia, Goblin Bombardment,
   Brain Freeze, Hardened Scales, Doubling Season among them).
3. `PayoffTable` for a category = inferred (at or above the threshold) | table cards |
   query results, minus the table's exclusions. Log, per category, how many cards came from
   each source, and write the inferred set to the stats file (`payoffs.inferred`) so the
   table can be tuned from a run.
4. Default table, one Scryfall query per class of outlet. These were run against Scryfall
   on 7 October 2026 (`unique=cards`, with `game:paper` added); the counts are the cards
   each returns, the names the top of its EDHREC ordering. Every query is kept under the
   25-card limit or ordered by EDHREC rank so the limit keeps the played ones.

   | Category | Query | Cards | Examples |
   |---|---|---|---|
   | mana | `o:"{X}" o:"X damage" -t:land` | 34 | Crypt Rats, Devil's Play, Fall of the Titans, Comet Storm |
   | mana | `o:"{X}" (o:"draw X cards" or o:"draws X cards")` | 10 | Well of Lost Dreams, Blue Sun's Zenith, Mindspring Merfolk |
   | mana | `o:"{X}" o:"X +1/+1 counters"` | 21 | Nyxborn Hydra, Zaxara, the Exemplary, Wildborn Preserver |
   | storm | `keyword:storm f:commander` | 33 | Grapeshot, Brain Freeze, Empty the Warrens, Mind's Desire |
   | tokens | `o:"whenever a creature you control dies" (o:"deals 1 damage" or o:"loses 1 life") f:commander` | 8 | Bastion of Remembrance, The Meathook Massacre |
   | tokens | `o:"creatures you control get +" o:"trample until end of turn" (t:sorcery or t:instant or t:creature) f:commander` | 27 | Overrun, End-Raze Forerunners, Kamahl, Heart of Krosa |
   | lifegain | `o:"whenever you gain life" (o:"deals that much damage" or o:"loses that much life") f:commander` | 6 | Vito, Thorn of the Dusk Rose, Sanguine Bond, Vizkopa Guildmage |
   | lifegain | `o:"pay 50 life" f:commander` | 1 | Aetherflux Reservoir |
   | counters | `o:"remove a +1/+1 counter from" (o:"deals 1 damage" or o:"draw a card") f:commander` | 18 | Walking Ballista, Triskelion, Sage of Fables |
   | counters | `o:"+1/+1 counters are put on" o:"draw" f:commander` | 6 | Benthic Biomancer, Fetid Gargantua |

   Known gaps to fill with further queries rather than names: the aristocrats whose trigger
   reads "whenever Blood Artist or another creature dies" (Blood Artist, Zulaport Cutthroat
   and Corpse Knight say "creature you control or another creature", so the first tokens
   query misses them; the inference finds all three today), ETB damage with "another
   creature enters" wording (Impact Tremors, Purphoros), Thassa's Oracle and Laboratory
   Maniac (draw outlets for infinite mana only through card draw, better left to the draw
   category), and Craterhoof Behemoth, whose text has no "trample until end of turn". A
   query that returns nothing is a 404 from Scryfall and must be reported as a table error,
   not an empty set.
5. `build_cube_ilp`: after the instance is loaded, add every payoff card that is not a
   candidate as a `CandidateCard` with empty `combo_ids` and `requirement_group_keys`, fetch
   attributes for all candidates as today, and pass `payoffs: PayoffTable` to the optimizer.
   Log how many payoff cards were added to the pool and how many were already in it.
6. `cube_evaluation.payoffs_per_category(selected_cards, payoffs)`; a `payoffs` block per
   phase in the stats file (count and the cards, by category, with the source of each), a
   log line each, and `evaluate_cube` output. Run on the tracked cube for the baseline.
7. Raise the inference's reach: only 50 of 4,982 bundled terminal variants find their
   engine sub-variant inside the top 20,000. `precache` could fetch the missing engine
   variants by combo id (`/variants/?q=...` searches by combo id) into a side cache the
   inference reads; measure how many more outlets that finds before deciding whether it is
   worth the download.

## Step 2: the payoff floor

1. Constructor argument `min_payoffs: int = 0` and `payoffs: PayoffTable | None = None`.
2. `_add_payoff_floor(base)`: for each category of the table whose outcome has at least one
   combo in the pool, `sum(x[c] for c in payoffs[category]) >= min_payoffs`; a category with
   fewer payoff cards than the floor is lowered to what it has, with a warning, as the
   outcome minimum does. `_payoff_violations(cards)`. Registered in `_cube_rules`.
3. `_add_utilization_floor`: exempt payoff-only cards (candidates with no combos) from the
   floor and from the `x[c] = 0` exclusion, so they can be selected. The `u` variable of
   such a card is the constant 0 already. Check that no Phase 2 objective breaks: `minmax`
   bounds `min_util` over selected cards and would see 0, so either exclude payoff-only
   cards from the `min_util` linking or accept that `minmax` is not the default.
4. Flags `--min-payoffs`, `--payoffs`, plumbed like `--min-outcome-combos`, recorded in the
   stats `phase2` block with the floor applied per category.
5. Measure the cost as in the other plans (Phase 1 style solve with the cube rules in force
   plus the floor, 120 s) at floors of 2, 3 and 5, and the number of payoff-only cards each
   pulls in. Choose the default from that.

## Step 3: reserved slots (if the mechanism holds up)

Once payoff-only candidates work, `data/playables.txt` (one card per line, `#` comments)
can use the same path: every listed card becomes a candidate and is fixed in (`x[c] == 1`),
exempt from the utilization floor, counted by the color balance and card mix rules. Flag
`--playables PATH`, default none. This is a note for scoping; it can be its own plan.

## Tests

Small instances: `infer_payoffs` on hand-built variants (a bundled variant, its engine
sub-variant, a card below the threshold, a terminal engine that must not count); the union
with the table and the exclusions; a payoff-only card is added to the pool and can be
selected only when the floor asks for it; the floor pulls an outlet into a cube of engines; a category with no
combos in the pool gets no floor; a table naming an unknown category is rejected; a floor
above the payoff count is lowered with a warning; payoff-only cards survive the utilization
floor and count for the card mix caps; `payoffs_per_category` and the stats block. Plumbing
tests in `tests/unit/test_runner.py`; the table loaded from a temporary path with a fake
Scryfall session (see `tests/unit/scryfall_fakes.py`); `PayoffFetcher` against the fake
session as `test_card_attribute_fetcher.py` does (cache hit and miss, cache version, a
changed query re-fetched, a failed query not cached, read-only and write-disabled modes);
`precache` fills the payoff cache.

## Verification

Full run at the chosen defaults against the tracked cube: payoffs per category, combos,
utilization, colors, card mix, outcomes, Phase 2 time. Record the results here; update
`README.md` (the API caching table gets a fourth row for `scryfall_payoffs.json`, the cache
behavior notes, the Phase 2 options), `docs/architecture.md` (data pipeline, candidate pool,
Phase 2 rule list, cache files, stats file) and the memory note.

## Results (7 October 2026)

Implemented on branch `payoff-support`. `ComboData` carries `includes` (the Spellbook combo
ids a variant includes); `ilp/payoffs.py` loads the table `data/payoffs.json`
(`queries`, `cards`, `exclude` per outcome category, every category checked against the
outcome table), infers outlets from the pool (`infer_payoffs`) and resolves the union
(`resolve_payoffs` -> `PayoffTable`, each card with its sources);
`scryfall/payoff_fetcher.py` (`PayoffFetcher`) resolves the queries in EDHREC order over
paper cards into `data/cache/scryfall_payoffs.json` (keyed by the search URL, with the time
each was fetched; the blocklist and the 25-card limit are applied afterwards, as for the
templates), under the usual two cache flags and filled by `task precache`. `build_cube_ilp`
reads both tables and resolves the queries before the instance is loaded, adds every payoff
card the pool lacks as a `CandidateCard` with no combos, and passes the table to the
optimizer. The "payoff floor" is one more entry of `_cube_rules` (`--min-payoffs`, one
linear constraint over `x` per category whose outcome has combos in the pool, lowered with a
warning where a category has fewer cards); payoff-only cards are exempt from the utilization
floor, the warm-start floor checks and the `minmax` and `mad` objectives. The stats file has
a `payoffs` block per phase (counts and cards with sources), `min_payoffs` and
`payoff_floors` in `phase2`, and a top-level `payoffs` block with the resolved table and
everything the inference found; `evaluate_cube` reports the same.

Decisions taken against the plan's recommendations:

- The engine of a bundled variant is matched on `includes`, not on `of`: a variant whose
  `includes` are a strict subset of the bundled variant's (so an engine that itself bundles a
  smaller combo still counts). Matching on `includes` of exactly one combo found 29 bundled
  variants an engine; the subset form finds 53, the same as matching on a single `of`, and
  comes close to the plan's counts (mana 16, storm 17 against 14, tokens 15, counters 12,
  lifegain 4; the Problem section counted 50 of 4,982 bundled terminal variants against 53
  of 5,449 here, the pool having been re-fetched in between). The outlet is what a bundle
  adds beyond every engine it includes, so a bundle of two engines credits neither engine's
  cards to the other; on this pool that changes no count.
- The storm query is `keyword:storm f:commander (o:damage or o:"create" or o:mills or
  o:"loses" or o:"draw" or o:"copy target")` (12 cards: Grapeshot, Brain Freeze, Empty the
  Warrens, Tendrils of Agony, Ignite Memories; Mind's Desire, which exiles and casts, is not
  among them) rather than every storm card: with the plain query the floor was met with Flusterstorm, Wing Shards and
  Radstorm, the cheapest storm cards, none of them an outlet.
- Two of the known gaps are filled with Scryfall regex queries: the aristocrats
  (`o:/whenever (~ or another|another|a) creature( you control)? dies/ ...`: Blood Artist,
  Zulaport Cutthroat, Syr Konrad, 28 cards) and the ETB triggers (`... enters/ ...`: Impact
  Tremors, Purphoros, Corpse Knight, Warleader's Call, 20 cards), plus Craterhoof's wording
  (`o:"gain trample and get +X/+X"`, 6 cards). All in the tokens category.
- Chromatic Orrery is excluded from every category, not only mana: the inference assigns it
  to mana (4 bundled variants), storm (3) and tokens (3).
- The default floor is 2, not 3 (see Step 2).
- The default payoff table is opportunistic when the floor is off: a run with
  `--min-payoffs 0` and a custom `--outcome-categories` the table does not fit skips payoffs
  with a warning instead of failing, so the small test configurations keep working. With the
  floor on, a table that is missing or does not fit is an error, as is a query that matches
  no card.

### Step 1: the inference and the table

On the cached 20,000 variants (19,848 after preprocessing; 13,326 include more than one
combo, 5,449 of them with a terminal result), the inference finds an engine in the pool for
53 bundled variants. Cards found per category, and what the thresholds keep:

| Category | Cards found | Kept at 1 | Kept at 2 (default) | Kept at 3 | The cards kept at 2 |
|---|---|---|---|---|---|
| mana | 16 | 16 | 5 | 1 | Chromatic Orrery (excluded), Chandra x3, Suture Priest |
| storm | 17 | 17 | 3 | 1 | Chromatic Orrery (excluded), Korvold, Urza, Lord High Artificer |
| tokens | 15 | 15 | 2 | 1 | Chromatic Orrery (excluded), Suture Priest |
| lifegain | 4 | 4 | 3 | 0 | Chandra x3 |
| counters | 12 | 12 | 1 | 1 | Gravitic Punch (9 bundled variants) |

At threshold 1 the inference has the aristocrats and the altars (Blood Artist, Zulaport
Cutthroat, Corpse Knight, Altar of Dementia, Goblin Bombardment, Brain Freeze, Hardened
Scales, Doubling Season) but also Lightning Bolt, Sunscorched Desert and Firemind's Foresight;
at 2 it is down to a handful, most of them doubtful (three Chandras for lifegain and mana).
The threshold stays at 2 and the table does the work; the counts are written to the stats
file (`payoffs.inferred`) for tuning. Step 1.7 (fetching the missing engine variants by combo
id to widen the inference) was deferred: 53 of the 5,449 bundled terminal variants find their
engine in the pool, so the download would have to cover most of the other 5,400 engines, and
the table already covers every category at the chosen floor; it stays an open question.

The resolved default table (queries resolved on 7 October 2026, the tightened storm query)
has 177 distinct payoff cards, 99 of them in no combo of the pool: mana 57 (4 inferred, 53
from the queries), storm 14 (2 inferred, 12 from the query), tokens 76, lifegain 10, counters
25; a card can be in several categories. 4,654 candidate cards after adding them, all with
Scryfall data.

### Step 2: combo cost of the payoff floor

Two measurements, both Phase 1 style solves under every Phase 2 cube rule (the defaults) plus
the payoff floor, 300 cards, 20,000 variants, 120 s, 8 workers, weighted combos at
`--variant-weight 0.1`. Unhinted, as the outcome rule measurements were, the floor is hard
to even satisfy: floor 0 found 1,354.9 weighted combos, floor 2 1,173.8, and floors 3 and 5
found no solution in 120 s. A build never solves this model unhinted, so the second
measurement hints the reference solve with the tracked run's Phase 1 cube (rebuilt from the
stats file's card changes), as `_best_constrained_cube` does:

| Floor | Weighted combos | Distinct | Payoff cards in the cube (mana / storm / tokens / lifegain / counters) | Payoff-only cards |
|---|---|---|---|---|
| 0 | 1,289.2 | 1,231 | 1 / 1 / 7 / 0 / 3 | 0 |
| 2 | 1,264.0 (-2%) | 1,206 | 2 / 2 / 7 / 2 / 2 | 1 |
| 3 | 1,186.8 (-8%) | 1,134 | 3 / 3 / 5 / 3 / 4 | 1 |
| 5 | 1,137.4 (-12%) | 1,080 | 5 / 5 / 6 / 5 / 5 | 2 |

Single runs of a time-limited search; differences under about 8% are noise (the combo variety
plan's measurements). Without a floor the cube has no lifegain outlet and one each for mana
and storm; the floor fills those first with outlets that are combo pieces (Suture Priest,
Walking Ballista, Vito, Sanguine Bond, Aetherflux Reservoir) and takes payoff-only slots
only where it must (one storm spell at 2 and 3, two at 5), so the slot cost stays below the
floor itself. Decision: `--min-payoffs 2` by default, inside the noise, where 3 costs about
what an outcome minimum of 60 did and 5 half as much again. These runs used the plain storm
query; the tightened one leaves fewer, better storm cards to choose from.

### Step 3: reserved slots

Not implemented; the payoff-only candidate mechanism (`add_payoff_cards`, the floor
exemption, `payoff_only_cards`) is what `data/playables.txt` would reuse.

### Verification

One full run at the defaults (`task build:ilp`: 300 cards, 20,000 variants, 360 s per phase,
8 workers, `--min-payoffs 2`), now the tracked `data/current_best_cube.txt`, against the
previous tracked cube (the combo variety plan's run):

| | Previous tracked cube | This run |
|---|---|---|
| Phase 1 variants / distinct / weighted | 2,504 / 1,525 / 1,622.9 | 2,409 / 1,491 / 1,582.8 |
| Reference under the cube rules (distinct / weighted) | 1,099 / 1,157.5 | 1,089 / 1,140.0 |
| Phase 2 variants / distinct / weighted | 1,352 / 1,007 / 1,041.5 | 1,323 / 993 / 1,026.0 |
| Payoff cards (mana / storm / tokens / lifegain / counters) | 1 / 1 / 7 / 0 / 3 (none required) | 2 / 2 / 5 / 2 / 2 |
| Payoff-only cards in the cube | 0 | 0 |
| Utilization min / max / std dev | 2 / 158 / 17.3 | 2 / 187 / 18.0 |
| Lowest pair / lowest mono | RG 252 / R 156 | BR 268 / B 178 |
| Mana combos (share of distinct) | 497 (49%) | 530 (53%) |
| Smallest outcome category | win 40 | win 40 |
| Phase 2 time | 360 s (reference solve and repair about 90 s) | 360 s (90.5 s) |

The floor costs 1.5% of the weighted combos against the previous run, inside the run-to-run
noise, and was met entirely with combo pieces: Azor, the Lawbringer and Suture Priest for
mana, Brain Freeze and Urza, Lord High Artificer for storm, Aetherflux Reservoir and Vizkopa
Guildmage for lifegain, Walking Ballista and Ulasht for counters, and for tokens Blood
Artist, Zulaport Cutthroat, Suture Priest, Thornbite Staff and Blasting Station. No payoff-only card (a storm spell, an X spell) made the cube:
the pool has enough outlets that are also combo pieces to meet a floor of 2, so the
reserved-slot mechanism is exercised by the tests and the measurements (one or two
payoff-only cards at floors 2 to 5) rather than by the tracked cube. Phase 1 had one outlet
for mana and storm and none for lifegain; the previous tracked cube had the same holes.

### Step 4 (10 October 2026): floors sized from the cube, `triggers` and `other`

The flat floor of 2 per category is 4% of a 300-card cube; a draft format wants outlets at
15 to 20% of the cards, and a flat number cannot follow the pool, where mana is half the
engines and lifegain a twentieth. `--payoff-share` (default 0.15) now sizes the floors from
the cube: the floors add up to the share of the cube's cards, split among the payoff
categories in proportion to their distinct combos in the pool by the D'Hondt method, each
category at least `--min-payoffs` and at most twice the even split, or the table's own
`min_payoffs` / `max_payoffs`. Two outcome categories were added at the same time:
`triggers` (infinite creature ETB, LTB, death and sacrifice trigger loops, which were most
of the uncategorized combos and are the aristocrat engine; its payoffs are the aristocrat
queries) and the catch-all `other` (an empty pattern list; every combo no named category
matches), so the outcome minimum keeps the leftover combos in the cube too. The storm and
lifegain queries were widened (storm 14 to 39 cards, lifegain 10 to 20).

One full run at 310 cards (a 400-card cube with 90 slots reserved for lands, ramp and
interaction) and otherwise the defaults (`task build:ilp CUBE_SIZE=310`), against the
tracked 300-card cube of Step 2:

| | Tracked cube (300, floor 2) | 310 cards, share 0.15 |
|---|---|---|
| Phase 1 variants / distinct / weighted | 2,409 / 1,491 / 1,582.8 | 2,626 / 1,557 / 1,663.9 |
| Reference under the cube rules (distinct / weighted) | 1,089 / 1,140.0 | 948 / 985.0 |
| Phase 2 variants / distinct / weighted | 1,323 / 993 / 1,026.0 | 1,116 / 861 / 886.5 |
| Payoff floors (mana / storm / tokens / triggers / lifegain / counters) | 2 / 2 / 2 / - / 2 / 2 | 13 / 6 / 6 / 16 / 2 / 3 |
| Payoff cards in the cube, per category | 2 / 2 / 5 / - / 2 / 2 | 13 / 6 / 14 / 16 / 2 / 3 |
| Distinct payoff cards (share of the cube) | 11 (4%) | 38 (12%) |
| Payoff-only cards in the cube | 0 | 16 |
| Utilization min / max / std dev | 2 / 187 / 18.0 | 2 / 92 / 12.2 |
| Lowest pair / lowest mono | BR 268 / B 178 | RG 251 / G 150 |
| Largest outcome categories | mana 530 (53%) | triggers 432, mana 366 (43%) |
| `other` combos | (87 uncategorized, no rule) | 41 |
| Phase 2 time | 360 s | 360 s |

What the run says:

- The split follows the pool: `triggers` is the pool's largest category (4,616 distinct
  combos against 3,466 mana; many combos list a trigger loop beside their main result) and
  took the even-split cap of 16, mana 13, storm and tokens 6, counters 3, lifegain 2.
- The 54 floor slots are filled by 38 distinct cards, because every aristocrat (Blood
  Artist, Zulaport Cutthroat, Suture Priest, ...) counts for tokens and triggers both and
  Suture Priest for mana too. The share is a floor on slots, not on distinct cards; if the
  distinct count should reach 15%, the tokens and triggers payoffs want different queries
  (overrun effects alone for tokens), or the share goes up.
- The cost is 13% of the distinct combos against the tracked cube (993 to 861) at ten more
  cards, where the flat floor of 5 (25 slots) was measured at 12%; the reference solve
  shows the same (1,140.0 to 985.0 weighted). The Phase 1 cube broke the floors for mana
  (2 of 13), storm (1 of 6), triggers (7 of 16) and lifegain (0 of 2), so without the rule
  those outlets are not in the cube.
- Sixteen payoff-only cards made the cube (5%): eight X spells for mana (Fall of the Titans,
  Street Spasm, Lantern Flare, Wren's Run Hydra, ...), Chatterstorm, Tendrils of Agony and
  Amphibian Downpour for storm, four aristocrats and The Meathook Massacre, Dyadrine for
  counters. The mana floor of 13 is the one that reaches past the combo pieces into the
  query's EDHREC tail; a curated `cards` list for mana, or `max_payoffs`, would raise its
  quality.
- Utilization is far flatter (std dev 18.0 to 12.2, max 187 to 92): the payoff slots and
  the two new minimums leave the objective less room to stack a hub card.

## Open questions

- The conditional form ("a mana combo counts toward the outcome minimum only if a mana
  outlet is in the cube") is the precise version of this rule. It needs
  `counted[j] <= y[j]` and `counted[j] <= sum(x over the outlets)` per combo, about 8,700
  extra variables; worth trying once the floor is in and measured.
- How far the inference can go: with the engine sub-variants fetched by combo id (Step
  1.7) it may cover most outlets Spellbook knows, leaving the table to the storm spells and
  overrun effects Spellbook has no combo for. Walking Ballista will still need the table,
  because Spellbook files its combos as self-contained.
- Resolved: the confidence threshold (the Results list what 1, 2 and 3 keep; 2 stays) and
  the utilization of payoff-only cards (0 by definition; the README says so, the utilization
  statistics leave them out and the `payoffs` statistics list them as `payoff_only`).
- Mana-value and color of the outlets: a storm payoff pulls the cube toward red and blue
  instants; the card mix rules and the color balance apply to them, which may be enough.
