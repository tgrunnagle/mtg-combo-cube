# MTG Combo Cube

A tool for building Magic: The Gathering combo cubes by optimizing card selection to maximize combo potential.

## Summary

Given a target cube size (e.g., 360 cards), this tool selects cards that maximize the number of completable combos while ensuring balanced card utilization. This problem is a variant of a **weighted maximum coverage** or **set cover**, which is NP-hard. It fetches combo data from the [Commander Spellbook API](https://commanderspellbook.com/) and uses Integer Linear Programming (ILP) to find optimal solutions.

**The core optimization problem:**
- Select exactly N cards for the cube
- Maximize the number of completable combos (a combo is completable if all its required cards are in the cube)
- Balance card utilization so each card contributes to roughly the same number of combos

**Two-phase approach (default):**
1. **Phase 1** - Maximize the combo score: distinct combos, with further variants of a combo the cube already completes worth `--variant-weight` each (popularity as tiebreaker)
2. **Phase 2** - Balance card utilization while holding the combo score within a tolerance, under the *cube rules*: template coverage, color balance, archetype support (combos for every color pair and mono color, a cap on three-plus-color combos), the card mix (caps on multicolor, colorless, expensive and creature cards, a floor on instants and sorceries), outcome support (combos of every outcome category: mana, damage, tokens, draw, ...), payoff support (outlets for the engines: storm spells, X spells, aristocrats, ...) and enabler support (the cards that set combos up: fast mana, rituals, wheels, graveyard fillers)

This produces cubes where every card pulls its weight: in a two-phase result every combo card takes part in at least `--min-util-floor` completable combos (default 2), "hub" cards that many combos depend on are pushed down (while redundant pieces that complete the same combos are kept, see "Phase 2 Objectives"), every draft archetype has combos to build around, the engines have outlets and the combos have enablers. The outlets and the enablers are the exceptions to the floor: a payoff card that is in no combo (Grapeshot, Comet Storm) completes nothing and is in the cube to be drafted beside an engine, and an enabler card below the floor (Chrome Mox with one variant in the pool, Entomb with none) is in the cube to set combos up; the `payoffs` and `enablers` statistics list those cards as `payoff_only` and `enabler_only`, and the utilization statistics leave them out.

## Setup

### Prerequisites
- Python 3.13+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- [Task](https://taskfile.dev/) (optional, for running development commands)

### Installation

```bash
task install

# Or install dependencies with uv directly
uv sync --dev
```

## Usage

### Quick Start

```bash
# Build a 300-card cube using ILP (two-phase optimization, default)
task build:ilp CUBE_SIZE=300
# or
uv run python -m src.mtg_combo_cube -c 300
```

### Command-Line Options

```
-c, --cube-size        Cube size (default: 300)
-o, --output-file      Output file path (default: data/cube.txt)
-t, --time-limit       ILP solver time limit in seconds (default: 300)
-n, --max-variants     Max combo variants to fetch (default: 20000)
--single-phase         Use single-phase ILP (disables utilization balancing)
--combo-tolerance      Phase 2 combo window, on the combo score (weighted combos when --variant-weight < 1), measured from the best cube under the Phase 2 cube rules (default: 0.1 = 10%)
--gap-limit            Phase 2 early termination gap (default: 0.05 = 5%)
--phase2-objective     Phase 2 objective: essential (default), tiered, softcap, maxutil, minmax or mad
--util-cap             Cap for essential (combos the cube would lose without the card), tiered and softcap (utilization) (default: 2 x the Phase 1 median of the measure)
--warm-start           A cube list from an earlier build (its output file or <output>_reference.txt) that hints the Phase 2 reference solve
--min-util-floor       Minimum utilization floor for Phase 2, any objective (default: 2)
--max-color-ratio      Phase 2 color balance: largest color at most this many times the smallest (default: 2.0, 0 disables)
--variant-weight       Value of each further variant of a combo the cube already completes (default: 0.1; 1 counts variants, 0 counts distinct combos)
--min-pair-combos      Phase 2 archetype support: distinct combos every two-color pair must be able to assemble (default: 250, 0 disables)
--min-mono-combos      Phase 2 archetype support: distinct combos every mono color must be able to assemble (default: 150, 0 disables)
--max-wide-combo-share Phase 2 cap on the share of completed combos that need three or more colors (default: 0.25, 0 or 1 disables)
--max-multicolor-share Phase 2 card mix: at most this share of the cube may be multicolor cards (default: 0.15, 0 or 1 disables)
--max-colorless-share  Phase 2 card mix: at most this share may be colorless nonland cards (default: 0.25, 0 or 1 disables)
--max-expensive-share  Phase 2 card mix: at most this share may have mana value --expensive-mana-value or more (default: 0.2, 0 or 1 disables)
--expensive-mana-value Mana value from which a card counts as expensive (default: 5, above 0)
--max-creature-share   Phase 2 card mix: at most this share may be creatures (default: 0.6, 0 or 1 disables)
--min-spell-share      Phase 2 card mix: at least this share must be instants or sorceries (default: 0.05, 0 disables)
--mono-color-ratio     Phase 2 card mix: largest mono-colored count at most this many times the smallest (default: 0, disabled; otherwise at least 1)
--min-outcome-combos   Phase 2 outcome support: distinct combos the cube must complete of every outcome category, unless the table gives a category its own minimum (default: 40, 0 disables)
--max-outcome-share    Phase 2 cap on the share of completed combos in any one outcome category (default: 0, disabled; 0 or 1 disables)
--popularity-weight    How much a combo's Spellbook popularity adds to its value in both phases (default: 0, popularity is a tiebreak only)
--min-payoffs          Phase 2 payoff support: payoff cards (outlets) the cube must hold of every category in the payoff table (default: 2, 0 disables)
--payoff-share         Phase 2 payoff support: share of the cube the payoff floors add up to, split among the categories by their combos in the pool (default: 0.15, 0 keeps --min-payoffs alone)
--payoff-inference-min Bundled Spellbook variants a card must be the outlet of to count as an inferred payoff (default: 2)
--min-enablers         Phase 2 enabler support: enabler cards (fast mana, rituals, wheels, graveyard fillers) the cube must hold of every category in the enabler table (default: 2, 0 disables)
--enabler-share        Phase 2 enabler support: share of the cube the enabler floors add up to, split evenly among the categories (default: 0.05, 0 keeps --min-enablers alone)
--staple-combos        Phase 2 staples rule: the named cards of each of the pool's N most popular variants are a staple the cube must complete (default: 25; 0 turns the rule off)
--min-staple-combos    Phase 2 staples rule: how many of the staples the cube must complete (default: 20; 0 asks for all of them)
--fixed-cards          A card list file naming the cube's cards outside the slots the optimizer fills, in place of the configuration's fixed_cards file
--min-coverage-ratio   Min coverage ratio for requirement templates (default: 0.1)
--workers              Parallel search workers for the ILP solver (default: 8)
--profile              Enable detailed profiling of ILP optimization
--config               Path to the configuration file: fixed cards, blocklist, blocklist queries, outcome category table, payoff table and enabler table (default: config.yaml)
--skip-api-caching     Skip writing API responses to cache files
--read-api-cache       Read from cache if available, fall back to API if not
-d, --debug            Enable debug logging (includes the CP-SAT search log)
```

`-t` is applied to each phase separately.

Both methods leave out the cards of the configuration's `blocklist` and the cards matching its `blocklist_queries`, Scryfall searches resolved (and cached) before the build: the defaults block the Un-set card types (stickers, attractions, contraptions, hosts and augments). A query Scryfall rejects is a usage error; one that cannot be fetched stops the run.

Both also know the cube's **fixed cards**: the cards of the slots filled by hand (lands, ramp and interaction; the 90 of a 400-card cube built as 310 combo cards plus 90), listed in the file the configuration's `fixed_cards` key names ([data/fixed_cards.txt](data/fixed_cards.txt), a Cube Cobra export with Sol Ring added; `--fixed-cards` names another). A fixed card is in every model, so the combos it completes with the cube count for the combo score, the window and every rule that counts combos (the archetype, outcome and coverage rules, the payoff and enabler floors: the five Moxen meet most of the fast-mana floor), and no slot of the cube size goes to a card the fixed list already holds. It is not one of the cards the cube size, the card mix, the color balance, the utilization floor and statistics or the balancing objective shape. A fixed card the pool does not name (a dual land) completes nothing and is kept for the record; a fixed card on the blocklist is an error. The cube file still lists the 310; the fixed cards are reported in the stats file and the card data (see "Output Files").

The `task build:ilp*` targets pass `--profile --read-api-cache` and accept `CUBE_SIZE`, `OUTPUT`, `TIME_LIMIT`, `MAX_VARIANTS`, `WORKERS` and `CONFIG` (a configuration file other than `config.yaml`) variables, e.g. `task build:ilp CUBE_SIZE=200 TIME_LIMIT=120`. Any other flag goes after `--`, e.g. `task build:ilp CUBE_SIZE=200 MAX_VARIANTS=1000 -- --min-pair-combos 0 --min-mono-combos 0 --min-outcome-combos 0 --min-payoffs 0 --min-enablers 0` (the archetype and outcome minimums and the payoff and enabler floors are sized for the default build; see "Phase 2 Options"). Their defaults (310 cards, 20,000 variants, a 360 s time limit, 8 workers) are the top-level `vars` in `Taskfile.yml`, shared with `task precache`.

### Examples

```bash
uv run python -m src.mtg_combo_cube --help

# Two-phase ILP (balanced card utilization)
uv run python -m src.mtg_combo_cube -c 300

# Single-phase ILP (max combos only)
uv run python -m src.mtg_combo_cube -c 300 --single-phase

# Two-phase with 20% combo tolerance (trades more combos for a flatter cube)
uv run python -m src.mtg_combo_cube -c 300 --combo-tolerance 0.2

# Push the single most-used card down instead of the overall spread
uv run python -m src.mtg_combo_cube -c 300 --phase2-objective maxutil

# Penalize every card that more than 40 combos depend on
uv run python -m src.mtg_combo_cube -c 300 --util-cap 40

# Balance on variants per card instead (the objective the tracked cube was built with)
uv run python -m src.mtg_combo_cube -c 300 --phase2-objective tiered

# Hint the Phase 2 reference solve with the previous build's reference cube, so the combo
# window is measured from a reference at least as good as last time
uv run python -m src.mtg_combo_cube -c 300 --warm-start data/cube_reference.txt

# Small, fast configuration for trying things out (about 1 minute with a warm cache);
# the archetype and outcome minimums and the payoff and enabler floors are sized for a full
# build, so they are switched off here
uv run python -m src.mtg_combo_cube -c 100 -t 30 -n 1000 --read-api-cache --min-pair-combos 0 --min-mono-combos 0 --min-outcome-combos 0 --min-payoffs 0 --min-enablers 0

# Custom output file and time limit
uv run python -m src.mtg_combo_cube -c 450 -o my_cube.txt -t 1800
```

### Output Files

- **data/cube.txt**: List of selected cards (one per line)
- **data/cube_stats.json**: Utilization statistics and optimization metrics
- **data/cube_reference.txt**: The Phase 2 reference cube (the best cube found under the cube rules, which the combo window was measured from), for a later build's `--warm-start`; written whenever Phase 2 ran
- **data/cube_card_data.json**: Why each card is in the cube, one entry per card, most-used first: its `reason` (`essential`: the cube would lose a combo without it; `redundant`: in combos that other cards complete too; `payoff_only`: in no completed combo, there for the payoff floor; `enabler_only`: in no completed combo, there for the enabler floor; `no_combo`: in no combo, there for another cube rule), the variants and distinct combos it takes part in (as a named piece or by filling a template), the combos it is essential to, the templates it fills, its combos per outcome category, its payoff and enabler categories, whether Phase 2 added it, and `spellbook_urls`: Commander Spellbook searches that show its completed combos (one variant per combo, most popular first), ten per link since that is the most one Spellbook search accepts. After the cube's cards come the fixed cards that take part in a completed combo, with the reason `fixed`. The file also has a `spellbook_find_my_combos_url` that opens the whole cube (the cube's cards and the fixed cards) on Spellbook's Find My Combos page
- **data/cube_potential_combos.json**: The variants of the pool the cube is one piece short of, by missing piece (the "potential combos" of Spellbook's Find My Combos page, over the pool rather than all of Spellbook): the totals, then the fifty pieces with the highest summed popularity, each with its kind (a `candidate` card, a `payoff` or `enabler` card, or a `template` requirement no card of the cube fills), the variants it would complete, their distinct combos, how many of those the cube does not complete through another variant (`new_combo_count`), the summed and highest popularity, the most popular variant's named cards and Spellbook search links to the variants. The cube is the cube's cards and the fixed cards together, so a fixed card is never a missing piece. The file also carries the Find My Combos link for the whole cube, whose own list is the same report over all of Spellbook. This is where to look for the next card to add to the fixed slots: Tireless Provisioner and Lion's Eye Diamond head the tracked cube's list

The stats file contains:

- `metadata`: cube size, `combo_count` (completed variants), `distinct_combo_count` (completed combos, see "Combos and variants" below), the `variant_weight` and `popularity_weight` used, total solve time, Phase 1 status and `optimization_method`, which is `two_phase`, `single_phase`, or `two_phase_fallback_to_phase1` when Phase 2 ran but found no solution and the cube is the Phase 1 result.
- `fixed_cards` (with fixed cards): the list, which of them are candidate cards, the completed variants and distinct combos a fixed card takes part in, and the fixed cards of each payoff and enabler category. `staples` (with `--staple-combos`): each staple's cards and popularity, whether the cube completes it and the cards it lacks. `one_card_away`: the totals of the potential combos file and its top ten pieces, without the links.
- `phase1` / `phase2`: `combo_count`, `distinct_combo_count`, solve time, utilization statistics (min, max, mean, median, standard deviation), the same five numbers for the cards counted in distinct combos (`distinct_combos_per_card`) and in essential combos (`essential_combos_per_card`, the combos the cube would lose without the card), `concentration` (the share of the completed combos the most-used card and the five most-used cards take part in, and the card the most combos depend on with their number), `colors`, the color distribution of that phase's cube, `archetypes`, the distinct combos each draft archetype can assemble, `card_mix`, its make-up by type, color count and mana value, `outcomes`, the distinct combos per outcome category, `popularity`, how popular its distinct combos are, `payoffs`, the payoff cards (outlets) of each category it holds, and `enablers`, the enabler cards of each category it holds (all seven described below). `phase2` also records `status`, the `objective` that ran, the `max_color_ratio` applied, the archetype settings applied (`min_pair_combos`, `min_mono_combos`, `max_wide_combo_share`, each left out when disabled), `card_mix_rules` (the card mix settings in force, left out when all are disabled), `card_mix_limits` (the card counts those rules applied at this cube size) and `unknown_candidate_cards` (candidate cards without Scryfall data), the outcome settings applied (`outcome_minimums` per category and `max_outcome_share`, left out when disabled), the payoff floor applied (`min_payoffs`, `payoff_share` and `payoff_floors` per category, left out when disabled), the enabler floor applied (`min_enablers`, `enabler_share` and `enabler_floors` per category, left out when disabled), the staples rule applied (`staple_combos`, `min_staple_combos` and `staples_completed`, which `phase1` carries too, left out when disabled), the reference cube the tolerance was measured from (`reference_combo_count` variants, `reference_distinct_combo_count` combos and `reference_weighted_combo_count`, the weighted count the window holds) and, for `essential` / `tiered` / `softcap`, the `util_cap` used with `util_cap_measure` (`essential_combos` or `utilization`). With a `--popularity-weight` above 0, `metadata` and both phase blocks also carry `combo_score`, the popularity-weighted count the window then holds (the reference as `reference_combo_score`). After a fallback it holds only the status, time, objective, cap, archetype, card mix, outcome, payoff and enabler settings, reference and `fell_back_to_phase1: true`.
- `improvement`: Phase 1 to Phase 2 changes, including the variant and distinct combo counts before and after and the cards swapped.
- `largest_combo_groups`: the ten combos with the most completed variants in the final cube, each with its Spellbook combo id, variant count and the cube cards that take part.
- `payoffs`: the resolved payoff table of the run (see below): every payoff card per category with its sources, how many cards each source named, and everything the inference found with its counts, so the table can be tuned from a run.
- `enablers`: the resolved enabler table of the run (see below): every enabler card per category with its sources and how many cards each source named.
- `top_utilized_cards` / `bottom_utilized_cards` (each card with its `utilization`, `distinct_combos` and `essential_combos`), `requirement_types`, `cross_template_overlap`.
- `profiling` (with `--profile`): per-phase timings, variable and constraint counts and solver statistics.

Every combo count and utilization number is computed from the selected cards, not read from solver variables.

`colors` is based on each card's color identity, looked up on Scryfall for every candidate card (with its type line and mana value) and cached in `data/cache/scryfall_card_attributes.json`:

- `cards_per_color`: cards per color (W, U, B, R, G). A multicolor card counts once for each of its colors.
- `mono_colored`, `multicolor`, `colorless`: the cube split into exclusive groups. `unknown` counts cards without color data.
- `variance` / `std_deviation`: spread of the five `cards_per_color` counts. Zero means the colors are evenly represented.

`archetypes` counts distinct combos by the drafter who could assemble them, from each combo's Commander Spellbook color identity:

- `combos_per_archetype`: for each of the ten two-color pairs (`WU` ... `RG`), the five mono colors and `C` (colorless), the completed combos whose color identity fits within those colors. A mono-white combo counts for `W` and for every pair with white; a colorless combo counts everywhere. The identity covers a combo's named cards; a template requirement filled by a colored card can add a color it does not show.
- `combos_by_color_count`: completed combos by the number of colors they need (0 to 5). A combo with variants of different identities needs the fewest colors of its completed variants.

`card_mix` describes the cube's cards, from the same Scryfall data:

- `type_counts`: cards of each card type (Creature, Instant, Sorcery, Artifact, Enchantment, Planeswalker, Battle, Land); a card counts once for every type it has, taken from the front face of a multi-faced card.
- `multicolor`, `colorless`: the cards the multicolor and colorless caps count: nonland cards with two or more colors, and nonland cards with none (`colors` counts lands too). `unknown` counts cards without Scryfall data, which count as colorless.
- `mana_value_counts`, `mean_mana_value`, `mean_mana_value_per_color`: the nonland cards by mana value (`7` means 7 or more), their mean, and the mean of the nonland cards of each color. A double-faced card has its front face's mana value, a split card the sum; cards without Scryfall data are left out.

`outcomes` counts distinct combos by what they do, from the features each variant produces on Commander Spellbook ("Infinite colored mana", "Infinite damage", ...) and the `outcome_categories` section of [config.yaml](config.yaml):

- `combos_per_outcome`: for each category of the table (by default mana, damage, tokens, draw, mill, lifegain, counters, turns, lock, storm and win), the completed combos with a feature matching one of the category's patterns. A combo counts for every category it matches, so the counts overlap; a combo's features are those of all its variants.
- `uncategorized`: completed combos in no category, typically a trigger loop (creature ETB, death triggers) without a terminal result; `total` is the completed combos.
- The table maps a category name to a list of patterns: case-insensitive substrings, or regular expressions with a `re:` prefix. A category may instead give `{patterns: [...], min_combos: N}` to set its own minimum for `--min-outcome-combos`. A category with an empty pattern list is the catch-all for the combos no other category matches (`other` in the default table, after `triggers` took the creature ETB, LTB, death and sacrifice trigger loops), so the outcome minimum keeps those in the cube too. Without the section the build goes on without the outcome statistics, as long as both outcome rules and the payoff floor are off.

`popularity` describes the distinct combos by Spellbook usage, a combo's popularity being its most popular variant's: `median_popularity` and `mean_log_popularity` (the mean of `log(1 + popularity)`), `below_pool_median_share`, the share of the completed combos less popular than the median combo of the whole pool (`pool_median_popularity`), and `combo_count`.

`payoffs` counts the outlets in the cube: the cards that turn an engine into a win (a storm spell for an infinite storm count, an X spell for infinite mana, an aristocrat for infinite tokens). The outcome categories that are their own payoff (damage, draw, mill, turns, lock, win, and the catch-all `other`) need none; for the others the `payoffs` section of [config.yaml](config.yaml) says how to find them:

- The table maps an outcome category to `queries` (Scryfall searches, run in EDHREC order over paper cards without the Un-set ones, the first 25 unblocked cards of each; a query written as `{query: ..., limit: N}` keeps its first N instead), `cards` (names), `require` (names the cube must hold while the payoff floor is on, cards of the category like the others) and `exclude` (names dropped whatever their source). Every category must be a category of the outcome table; the default table covers mana, storm, tokens, triggers, lifegain and counters. The default mana category requires six cards and has a floor of 8 (`min_payoffs` and `max_payoffs`): four X spells as finishers (Torment of Hailfire, Comet Storm, Finale of Devastation, Exsanguinate) and two repeatable sinks that are combo pieces (Kenrith, Walking Ballista) are always in the cube, and the solver fills the other two slots from the X-spell queries and the inferred outlets. Named as plain cards the X spells never made the cube: an X spell scores nothing unless its one engine is in the cube, so the floor took combo pieces (Kenrith, the Chandras) and long-tail sinks over any of them, and among a longer list of X spells it was indifferent. The default storm category requires Brain Freeze and Tendrils of Agony (named as plain cards they lost the floor to other cards in no combo) beside a query for the storm spells that drain or mill and one for the cards that trigger whenever a spell is cast, and tokens stays queried: a named list of overrun effects cost a build seven slots of cards in no combo. A payoff table that names a category the outcome table lacks, or that has no outcome table beside it, is an error whatever the settings. Without the section the build goes on without payoffs, as long as the payoff floor is off.
- A third source is inferred from the pool: Spellbook bundles an engine with an outlet as a variant that `includes` more than one combo (infinite mana plus Walking Ballista is "Infinite damage"), so the cards such a variant adds to the engine it includes are outlets of the engine's categories. A card counts once it is the outlet of `--payoff-inference-min` bundled variants (default 2). The inference finds the outlets Spellbook knows about (Blood Artist, Suture Priest, Gravitic Punch) but not the storm spells, which are no combo; the table covers those.
- A category may also bound its own payoff floor with `min_payoffs` and `max_payoffs` (see `--payoff-share` under "Phase 2 Options").
- `cards_per_category` is the number of payoff cards of each category in the cube and `cards` lists them with their sources (`inferred`, `card`, `query`); a card can be an outlet for several categories. With the floor on, every payoff card the pool lacks is added to the candidate cards as a card that completes no combo, and every payoff card in fewer combos than `--min-util-floor` (an X spell with one variant in the pool) is exempt from that floor rather than excluded by it, so its one combo still counts; `payoff_only` lists those cards in the cube. The cube may hold at most as many of them as the floors add up to, so they are chosen for the payoff floor and nothing else. With `--min-payoffs 0` the pool is unchanged and the table serves the statistics only.

`enablers` counts the cards that set combos up: fast mana, rituals, wheels and graveyard fillers (Lion's Eye Diamond, Dark Ritual, Wheel of Fortune, Entomb). A card scores only through the combos it completes, so without a rule those cards lose their slot to pieces that complete more, or are not candidates at all; the `enablers` section of [config.yaml](config.yaml) says how to find them:

- The table maps a free-form category name (`fast_mana`, `ritual`, `wheel`, `graveyard` and `recursion` by default; not tied to the outcome table) to `queries`, `cards` and `exclude`, exactly as the payoff table does, and a category may bound its own enabler floor with `min_enablers` and `max_enablers`. There is no inference: nothing in the Spellbook data marks a card as an enabler. The default fast mana and rituals come from queries; the wheels and graveyard fillers are named, because the floor treats every card of a category alike and a query also finds cards nobody wants in the slot (Day's Undoing, Midnight Clock), which a build then used to meet the floor. Wheel of Fortune is required (the floor filled the wheel slots with the other four), and `recursion` is a category of one required card, Underworld Breach, with no floor of its own (`min_enablers` and `max_enablers` 0), so the enabler share is split among the other four. The default table leaves out ramp that is no combo piece (signets, talismans, Nature's Lore) and tutors, which the slots added by hand to the cube cover; ramp a query catches anyway (Paradise Mantle, Everflowing Chalice) stays and counts for its category like any card. Without the section the build goes on without enablers, as long as the enabler floor is off.
- `cards_per_category` is the number of enabler cards of each category in the cube and `cards` lists them with their sources (`card`, `query`). With the floor on, every enabler card the pool lacks is added to the candidate cards as a card that completes no combo, and every enabler card in fewer combos than `--min-util-floor` is exempt from that floor, so its few combos still count: `enabler_only` lists those cards in the cube (a card in both tables is a payoff card and stays `payoff_only`). The cube may hold at most as many of them as the floors add up to. With `--min-enablers 0` the pool is unchanged, the exemption is off and the table serves the statistics only.

The log prints the same combo counts ("Combos: Phase 1 4145 variants in 604 combos, ..."), color distribution, archetype counts ("Archetypes, Phase 2: pairs WU=358, WB=335, ...; mono W=219, ...; colorless 128; 3+ colors 304 of 1380 (22%)"), card mix ("Card mix, Phase 2: types Creature=180 (60%), Instant=10, Sorcery=7, Artifact=81, Enchantment=51, Planeswalker=2, Battle=0, Land=2; multicolor 35 (12%), colorless 73 (24%); mana value (nonland) mean 3.29, 0:9 1:31 2:58 3:74 4:66 5:32 6:16 7+:12, per color W=3.4, U=3.4, B=3.3, R=3.3, G=3.5"), outcomes ("Outcomes, Phase 2: mana=615, damage=174, ...; no category 97 of 1060 (9%)") popularity ("Popularity, Phase 2: median 462.5 (pool median 327.5), mean log 6.32, 42% of 1060 combos below the pool median") payoffs ("Payoffs, Phase 2: mana=12, storm=4, tokens=9, lifegain=3, counters=6") and enablers ("Enablers, Phase 2: fast_mana=4, ritual=4, wheel=4, graveyard=4") at the end of a run. If Scryfall cannot be reached, the run still completes without `colors` and `card_mix` when the payoff and enabler queries are cached or their floors are off; with a floor on (the default) and a cold query cache it stops before loading the instance, since the floor needs the queries.

[data/current_best_cube.txt](data/current_best_cube.txt), its stats file, its card data, its potential combos ([data/current_best_cube_potential_combos.json](data/current_best_cube_potential_combos.json)) and its reference cube ([data/current_best_cube_reference.txt](data/current_best_cube_reference.txt)) are a tracked example, built by the command in [data/current_best_cube_command.txt](data/current_best_cube_command.txt): a 310-card cube (the combo part of a 400-card cube whose other 90 slots are the fixed cards of [data/fixed_cards.txt](data/fixed_cards.txt)) from 20,000 variants with the default settings of 16 October 2026, the `essential` objective and the staples rule included, with the reference solve hinted by the previous tracked cube's reference (1,619 variants in 1,036 distinct combos, the fixed cards counted, 20 of the 23 staples of the 25 most popular variants; every two-color pair can assemble at least 253 of the combos and every mono color at least 152; at least 40 combos of every outcome category, `other` included, 57% of them making mana; Ashnod's Altar is in 17% of the combos and the top five cards in 32%, and the most essential card is essential to 124 of them; payoff floors of 15% of the cube, triggers 16, mana 8, tokens 8, storm 7, counters 4 and lifegain 3, the six required mana payoffs, Brain Freeze and Tendrils of Agony in the cube and 4 outlets in no combo; enabler floors of 5% of the cube, four of each category, the five fixed Moxen counting toward fast mana, the required Underworld Breach, Lotus Petal, Dark Ritual, Cabal Ritual and Wheel of Fortune among them and 6 enabler-only cards; 60% creatures, 25% colorless cards, 30 instants and sorceries). The build of 16 October with `essential` but without the fixed cards or the staples rule, the tracked cube before this one, found 1,595 variants in 1,073 distinct combos (1,604 and 1,077 with the fixed cards) and 11 of the 23 staples, with the top five cards in 33% of them and the most essential card essential to 129. The build of 13 October with `tiered` found 1,477 variants in 1,037 distinct combos with the top five cards in 36% of them, the most essential card essential to 132, 11 outlets and 6 enablers in no combo; its reference solve reached 1,151 distinct combos, the warm-started one 1,198. The build of 12 October, before the required payoffs, found 1,141 distinct combos with 7 outlets in no combo but none of the X spells or storm spells; the build of 10 October, before the enabler floor and with the warm-start preparation inside the Phase 2 time limit, found 1,019; [docs/plans/enabler-support-plan.md](docs/plans/enabler-support-plan.md) compares the last two.

### Evaluating a Cube

To score an existing cube list (true variant and distinct combo counts, the largest combo groups, utilization statistics, combos per draft archetype, combos per outcome category, popularity, payoff cards, enabler cards, color distribution and card mix) against the cached data:

```bash
uv run python -m mtg_combo_cube.ilp.evaluate_cube data/cube.txt -n 20000
```

Add `--card-data` to also write the per-card data to `data/cube_card_data.json` (without `added_in_phase2`, which only a build knows) and the potential combos to `data/cube_potential_combos.json`. The configuration's fixed cards count as in a build (`--fixed-cards` names another file); `--staple-combos N` (default 25, as a build) reports which of the pool's staples the cube completes.

Use the same `-n` (and `--config`, `--fixed-cards`, `--payoff-inference-min`, `--min-util-floor`) as the build you want to compare with. Cached data is read when present and fetched otherwise (only card attributes are written to the cache); cards that are not part of the instance are reported and count with utilization 0.

### API Caching

Four kinds of API responses are cached in `data/cache/`:

| Data | File | Used by |
|------|------|---------|
| Commander Spellbook combo variants | `variants_cards{max}_max{variants}.json` | ILP |
| Scryfall template searches (the cards that satisfy a requirement such as "Persist Creature") | `scryfall_templates.json` | ILP |
| Scryfall card attributes of the candidate cards (color identity, type line, mana value) | `scryfall_card_attributes.json` | ILP (color balance, card mix, statistics) |
| Scryfall searches of the payoff table (the outlets of each outcome category), the enabler table and the blocklist queries, with the time each was fetched | `scryfall_payoffs.json` | ILP (payoff and enabler floors, blocklist, statistics) |

**Cache behavior:**
- By default, API responses are written to `data/cache/` after fetching
- Use `--read-api-cache` to read from cache when available (falls back to live API on cache miss)
- Use `--skip-api-caching` to disable writing to cache
- Both flags cover all four caches. `scryfall_card_colors.json`, written by earlier versions, is no longer read and can be deleted. With a warm cache, an ILP run with `--read-api-cache` makes no network requests.
- A payoff or enabler query is cached under its search URL, so editing a query in the table fetches the edited query and leaves the rest cached; with `--read-api-cache` (which the `task build:ilp*` targets pass) the resolved table is the same between the runs being compared, while a plain run resolves every query live and may see Scryfall's ordering change. A query that matches no card, or that Scryfall rejects or only partly understands, is a table error, reported and not cached.
- Scryfall requests are rate-limited (about 10 per second) and retried on HTTP 429 / 5xx and network errors. Failed requests are not cached, so a later run retries them. A failed template or card attribute request degrades the instance or the statistics with a warning; a failed payoff or enabler query is an error when its floor is on, so a floor never runs against a different table than intended.

After preprocessing, the ILP method logs how many combos were left out and why, and how the Scryfall data was obtained:

```
Dropped 78 of 10000 combos (blocked_card=5, no_scryfall_api=73, scryfall_failure=0, empty_match=0); Scryfall: 0 network requests, 68 templates read from cache, 0 failed
```

- `blocked_card`: a required card is on the blocklist
- `no_scryfall_api`: a requirement has no Scryfall query
- `empty_match`: the query matches no card that is not blocked
- `scryfall_failure`: the request failed. A warning is logged because the instance is then incomplete; re-running retries it.

```bash
# First run: fetches from API and caches results
uv run python -m src.mtg_combo_cube -c 300

# Subsequent runs: use cached data for faster iteration
uv run python -m src.mtg_combo_cube -c 300 --read-api-cache

# Force fresh API fetch without caching
uv run python -m src.mtg_combo_cube -c 300 --skip-api-caching
```

#### Precaching

To download everything a build reads ahead of time, so that the build itself makes no network requests:

```bash
task precache                                   # 20,000 variants, as the task build:ilp* targets use by default
task precache MAX_VARIANTS=1000                 # a smaller configuration
task precache -- --keep-existing                # only fetch what is missing

uv run python -m mtg_combo_cube.precache -n 20000 --max-cards-in-combo 4 --config config.yaml
```

It fills the variants file, the Scryfall template searches, the blocklist's, the payoff table's and the enabler table's searches and the card attributes (of the candidate, payoff, enabler and fixed cards). Use the same `-n` (`MAX_VARIANTS`), `--max-cards-in-combo` (`MAX_CARDS_IN_COMBO`), `--config` (`CONFIG`) as the build: the first two name the variants file, and the configuration's blocklist, blocklist queries, payoff table and enabler table decide which templates, queries and cards the build asks for. `task precache` and the `task build:ilp*` targets both default to 20,000 variants.

- **Existing data is overwritten.** The variants file is replaced, and so is every template, payoff and enabler query and card attribute entry of the configuration. Entries that only other configurations use are left alone. A failed download leaves the existing variants file in place.
- `--keep-existing` keeps the entries already in the cache and fetches only what is missing, which finishes an incomplete run without starting over.
- **Retries:** each client retries single requests with backoff (see above). On top of that, a stage whose requests still failed is run again, up to `--max-passes` times (default 3), waiting `--retry-wait` seconds (default 30) before the second pass and twice as long before each further one.
- The script prints a summary and exits with status 1 when the cache is incomplete. Cards that Scryfall does not know are reported but do not count as a failure.
- `--cache-dir` writes somewhere other than `data/cache/`; a build only reads `data/cache/`.

## Optimization Method

### ILP
Integer Linear Programming using OR-Tools CP-SAT solver. Phase 1 is solved to proven optimality at the tested sizes when every variant counts (`--variant-weight 1`); with the default weight it runs to its time limit at full size and returns the best cube found. Phase 2 returns the best cube found within the gap or time limit. Two operational modes:

**Two-Phase (Default)**
- Phase 1: Maximize combo count
- Phase 2: Balance card utilization while preserving combo count (within tolerance)
- Produces balanced cubes where cards participate more evenly across combos
- Outputs detailed statistics to `{output}_stats.json` and per-card data to `{output}_card_data.json`

**Combos and variants**

Commander Spellbook lists *variants*: each is one way to assemble a *combo*, and many variants are the same combo with one piece swapped for an equivalent (the cached 20,000 variants belong to about 8,700 combos; the largest combo has 301 variants). Every count in the log and the stats file is reported both ways: `combo_count` is completed variants, `distinct_combo_count` is the combos they belong to (a variant's `of` ids on Spellbook; a variant of two combos combined counts as its own).

`--variant-weight` sets what the optimizer counts. A completed variant is worth 1 if it is the first of its combo and `--variant-weight` for each further one, so `1` counts variants and `0` counts distinct combos, with values in between rewarding extra variants less than new combos. The default is `0.1`: at full size it more than doubles the distinct combos of a variant-counting cube and shrinks the largest combo from 230 variants to 24, at the cost of about 40% of the variants. Any weight below 1 makes Phase 1 run to its time limit at full size instead of proving optimality in seconds. Phase 1 maximizes this weighted count and the Phase 2 combo window (`--combo-tolerance`) holds it, so with a weight below 1 the tolerance and `reference_weighted_combo_count` are in weighted combos, not variants. The table in [docs/plans/combo-grouping-plan.md](docs/plans/combo-grouping-plan.md) compares weights at full size.

`--popularity-weight` (default 0) makes popular combos worth more in the same score: a combo's value is multiplied by `1 + weight x log(1 + popularity) / log(1 + the pool's largest popularity)`, so with `1` the most popular combo on Spellbook is worth two combos nobody plays and the median one about 1.45. Both phases use the weighted score, so Phase 2 cannot trade popular combos for obscure ones inside the window; the stats file then reports the score as `combo_score` beside the plain counts. At 0 popularity remains a tiebreak only. Full runs at weights 0, 0.5 and 1 ([docs/plans/combo-variety-plan.md](docs/plans/combo-variety-plan.md), Step 3) moved the median popularity of the cube's combos by less than the run-to-run variation, because the cube rules and the tiebreak already favor popular combos, so the default stays 0.

**Phase 2 Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--phase2-objective` | `essential` | Objective function: `essential`, `tiered`, `softcap`, `maxutil`, `minmax` or `mad` |
| `--util-cap` | 2 x the Phase 1 median of the measure | Cap `T` for `essential` (essential combos per card: the combos the cube would lose without the card), `tiered` and `softcap` (utilization): the measure above it is penalized |
| `--warm-start` | none | A cube list from an earlier build (its output file, or the `<output>_reference.txt` written beside it) that hints the Phase 2 reference solve when it is a whole cube (the fixed cards apart); when it also satisfies the cube rules the reference is at least that cube, otherwise it is a hint only. Cards not in the pool are ignored |
| `--combo-tolerance` | `0.1` | How far Phase 2 may move from the reference combo score (10%), in weighted combos when `--variant-weight` is below 1 (see "Combos and variants"). The reference is the best cube found under the Phase 2 cube rules (coverage, color balance, archetype support, the card mix, the outcome rules, the payoff floor and the enabler floor), which scores lower than the Phase 1 cube |
| `--gap-limit` | `0.05` | Early termination when proven within 5% of optimal (0 = solve to optimality) |
| `--min-util-floor` | `2` | Minimum completable combos each selected card must participate in (all objectives, 0 disables) |
| `--max-color-ratio` | `2.0` | Color balance: no color may have more than this many times the cards of another color (all objectives, 0 disables, otherwise at least 1) |
| `--min-coverage-ratio` | `0.1` | Minimum cards per requirement template (10% of the combos using it, at most the template's card pool; 0 disables) |
| `--variant-weight` | `0.1` | Value of each further completed variant of a combo relative to its first (both phases; `1` counts every variant, `0` counts distinct combos only). The combo window is measured in the same weighted units |
| `--min-pair-combos` | `250` | Archetype support: every two-color pair must be able to assemble at least this many distinct combos (the pair's own plus mono-colored and colorless ones; 0 disables) |
| `--min-mono-combos` | `150` | Archetype support: every mono color must be able to assemble at least this many distinct combos (its own plus colorless ones; 0 disables) |
| `--max-wide-combo-share` | `0.25` | At most this share of the completed combos may need three or more colors (0 or 1 disables) |
| `--max-multicolor-share` | `0.15` | Card mix: at most this share of the cube may be multicolor cards (0 or 1 disables). Lands are left out of every card mix rule |
| `--max-colorless-share` | `0.25` | Card mix: at most this share may be colorless nonland cards; cards without Scryfall data count as colorless (0 or 1 disables) |
| `--max-expensive-share` | `0.2` | Card mix: at most this share may have a mana value of `--expensive-mana-value` (default 5, above 0) or more (0 or 1 disables) |
| `--max-creature-share` | `0.6` | Card mix: at most this share may be creatures (0 or 1 disables) |
| `--min-spell-share` | `0.05` | Card mix: at least this share must be instants or sorceries (0 disables) |
| `--mono-color-ratio` | `0` | Card mix: no color may have more than this many times the mono-colored cards of another, as `--max-color-ratio` on mono-colored cards only (0 disables, otherwise at least 1) |
| `--min-outcome-combos` | `40` | Outcome support: the cube must complete at least this many distinct combos of every category in the outcome table (mana, damage, tokens, ...), unless the table gives a category its own minimum (0 disables) |
| `--max-outcome-share` | `0` | Outcome support: at most this share of the completed combos may be in any one outcome category (0 or 1 disables). Costly: every combo in a category is linked exactly in the warm-start repair models too, so a tight cap may find no cube within the time limit |
| `--popularity-weight` | `0` | Value added to a combo for its popularity in both phases (see "Combos and variants"); 0 keeps popularity a tiebreak |
| `--min-payoffs` | `2` | Payoff support: the cube must hold at least this many payoff cards (outlets) of every category in the payoff table whose engines the pool has (0 disables the payoff floor). Payoff cards the pool lacks are added to the candidate cards |
| `--payoff-share` | `0.15` | Payoff support: the payoff floors add up to this share of the cube's cards, split among the payoff categories in proportion to their distinct combos in the pool (one card at a time to the category with the most combos per outlet, the D'Hondt method), each at least `--min-payoffs` and at most twice the even split of the total; a category's own `min_payoffs` / `max_payoffs` in the payoff table override those bounds. 0 keeps the floor at `--min-payoffs` per category. Below 1 |
| `--payoff-inference-min` | `2` | Bundled Spellbook variants (an engine plus an outlet) a card must be the outlet of before it counts as an inferred payoff of the engine's category |
| `--min-enablers` | `2` | Enabler support: the cube must hold at least this many enabler cards (fast mana, rituals, wheels, graveyard fillers) of every category in the enabler table (0 disables the enabler floor). Enabler cards the pool lacks are added to the candidate cards, and enabler cards below `--min-util-floor` are exempt from it |
| `--enabler-share` | `0.05` | Enabler support: the enabler floors add up to this share of the cube's cards, split evenly among the enabler categories, each at least `--min-enablers` and at most twice the even split of the total; a category's own `min_enablers` / `max_enablers` in the enabler table override those bounds. 0 keeps the floor at `--min-enablers` per category. Below 1 |

**Phase 2 Objectives:**

| Objective | Minimizes | Pick it when |
|-----------|-----------|--------------|
| `essential` (default) | Total number of combos per card that the cube would lose without the card, above the cap `T`, counted again above `2T` and `4T` | You want few bottleneck cards and redundancy kept: a card is penalized for the combos that depend on it alone, not for the combos it is in |
| `tiered` | Total utilization above the cap `T`, counted again above `2T` and `4T` | You want the flattest distribution of variants per card; this was the default before `essential` and the objective the tracked cube was built with |
| `softcap` | Total utilization above the cap `T` | As `tiered`, without the extra penalty for cards far above the cap |
| `maxutil` | The highest utilization of any card | The single most-used card matters most |
| `minmax` | `max_utilization - min_utilization` | As `maxutil`; this was the default before `tiered` |
| `mad` | Total absolute deviation from the Phase 1 mean utilization | Comparison with older results |

`essential` counts, for each card, the completed combos whose every completed variant names the card as a piece (a template filler never counts: another pool card could fill the slot), which is what `essential_combos` in the card data reports for named pieces. Utilization counts every variant a card is in, so a redundant piece (a fourth free sacrifice outlet, completing combos the other three complete as well) raises its own count and its partners' and is the cheapest thing for `tiered` to remove, while the hub cards whose combos are theirs alone stay. Under `essential` the redundant piece costs nothing, and the cheapest way to relieve a hub is a second card that completes the same combos. At full size (310 cards, 20,000 variants, 360 s), against the tracked `tiered` cube: variants per distinct combo 1.43 to 1.55, the share of the combos the five most-used cards touch 36% to 32%, the largest number of combos depending on one card 130 to 118, and a third free outlet kept; the distinct combo count moved with the reference solve rather than the objective (see [docs/plans/balancing-plan.md](docs/plans/balancing-plan.md)).

The older objectives, measured at 300 cards / 10,000 variants with a 300 s limit (Phase 1 for comparison: maximum 348, standard deviation 41.85, 12 cards above 100):

| Objective | Max utilization | Std deviation | Cards above 100 |
|-----------|-----------------|---------------|-----------------|
| `tiered` (two runs) | 255 | 28.6 / 29.1 | 7 / 8 |
| `softcap` | 261 | 29.8 | 8 |
| `mad` | 240 | 29.5 | 8 |
| `maxutil` | 155 | 32.6 | 18 |
| `minmax` | 155 | 32.2 | 19 |

The objectives fall into two families: `tiered`, `softcap` and `mad` give a lower spread but leave one card far out; `maxutil` and `minmax` hold the worst card at about 155 but end with a plateau of cards just under it. All of them keep 2,223-2,224 of the 2,471 Phase 1 combos (the lower edge of the 10% tolerance), and none finishes before the time limit at this size. These are one or two runs each of a time-limited parallel search; differences inside a family are within run-to-run noise. Details are in the [ILP Improvement Plan](docs/plans/ilp-improvement-plan.md).

Notes:

- The floor (`--min-util-floor`), the coverage rule (`--min-coverage-ratio`), the color balance (`--max-color-ratio`), the archetype rules (`--min-pair-combos`, `--min-mono-combos`, `--max-wide-combo-share`), the card mix rules (`--max-multicolor-share` and the other share options), the outcome rules (`--min-outcome-combos`, `--max-outcome-share`), the payoff floor (`--min-payoffs`) and the enabler floor (`--min-enablers`) are constraints of Phase 2 only and apply to every objective. Phase 1 and `--single-phase` do not enforce them.
- Color balance counts a card once for each color of its color identity, so a white-blue card counts as white and as blue. Colorless cards are not limited by it (see `--max-colorless-share`). The rule needs every color to be present; if it cannot be met within the combo tolerance, Phase 2 fails and the Phase 1 cube is returned. If card data cannot be fetched from Scryfall, a warning is logged and the run continues without the color balance and the card mix rules.
- The card mix rules are shares of the cube size, rounded to hundredths (a nonzero share that rounds to 0 is rejected): a cap allows `floor(share x cube size)` cards and the spell floor requires `ceil(share x cube size)`; the counts applied are recorded as `card_mix_limits`. Card types are those of the front face of a multi-faced card. Lands are left out of every card mix rule and of the `card_mix` multicolor and colorless counts: they are combo pieces, and colorless lands would otherwise fill the colorless cap and colored lands the multicolor cap. A card without Scryfall data counts as colorless, typeless and mana value 0 (a warning gives their number); if more than 5% of the candidate cards have no data, the rules are skipped with a warning, as when no data could be fetched at all. Before solving, a warning names any rule the whole pool cannot meet. The defaults were chosen from the measurements in [docs/plans/card-mix-plan.md](docs/plans/card-mix-plan.md): each costs at most about 4% of the weighted combo count alone (the whole set 5.6%), with the colorless cap, the expensive cap and the spell floor the most binding.
- Archetype support counts distinct combos (not variants) by their Spellbook color identity, as in the `archetypes` statistics. The minimums are absolute counts chosen for the default configuration (300 cards, 20,000 variants, see [docs/plans/archetype-support-plan.md](docs/plans/archetype-support-plan.md)); a much smaller cube or pool cannot reach them, so lower them or pass 0 there. Before solving, a warning names any archetype the whole pool has too few combos for; if the minimums cannot be met, Phase 2 fails, the Phase 1 cube is returned and the log says which archetypes that cube falls short on.
- Outcome support counts distinct combos by the features their variants produce on Spellbook, as in the `outcomes` statistics; a combo in two categories counts for both. The minimums are absolute counts like the archetype minimums, so a smaller cube or pool needs lower ones or 0. Before solving, a warning names any category the whole pool has too few combos for, and that category's minimum is lowered to what the pool has (so a mistyped pattern cannot make Phase 2 infeasible by itself); if the rules still cannot be met within the cube, Phase 2 fails, the Phase 1 cube is returned and the log says which categories fall short. The default of 40 was chosen from the measurements in [docs/plans/combo-variety-plan.md](docs/plans/combo-variety-plan.md): it is within the run-to-run noise of a build without the rule, while 60 costs about 9% of the weighted combos, 80 about 23% and a 0.5 share cap a third, which is why the cap is off by default.
- Payoff support counts the cards of the payoff table in the cube per category, as in the `payoffs` statistics. Every payoff card the pool lacks becomes a candidate card that completes no combo: it is exempt from the utilization floor (its utilization is 0 by definition) and takes a cube slot that scores nothing, so the solver prefers payoff cards that are combo pieces too. A payoff card below `--min-util-floor` in the pool (Comet Storm, with one variant) is exempt the same way instead of excluded, or it would lose its slot to a card with no variant at all. A category's `require` cards are in every Phase 2 cube while the floor is on (a cube rule of their own, `required cards`) and are exempt from `--min-util-floor`, which is how a finisher the solver would never pick on its own, scoring nothing or completing one combo, is held in the cube; a required card Scryfall does not know, or the blocklist blocks, is an error. The floors are sized from the cube: `--payoff-share` (default 0.15, about one card in seven) of the cube's cards are payoff cards in all, split among the categories in proportion to their distinct combos in the pool (an engine that is half the pool's combos is half the drafters' engines, so it gets half the outlets), one card at a time to the category with the most combos per outlet, each category at least `--min-payoffs` and at most twice the even split, or the table's own `min_payoffs` / `max_payoffs`. A category whose outcome has no combo in the pool gets no floor; a category with fewer selectable payoff cards than its floor (a blocked card, or one the card attributes do not know) has it lowered to what it has, with a warning, and the cards it cannot take go on to the other categories. The payoff floor needs the outcome table (to tell which categories have engines in the pool), and the table's categories must be outcome categories; a configuration without both sections needs `--min-payoffs 0`. The minimum of 2 was chosen from the measurements in [docs/plans/payoff-support-plan.md](docs/plans/payoff-support-plan.md), where a flat floor of 2 costs about 2% of the weighted combos and 5 about 12%; the share of 15% is a draft ratio (roughly one outlet in a pack of 15), and the plan's Results record what it costs.
- Enabler support counts the cards of the enabler table in the cube per category, as in the `enablers` statistics. Enabler cards are combo pieces the model undervalues (a ritual or a wheel appears in a variant only when it is a piece) or no candidates at all (Entomb, Mana Crypt), so the floor treats them as the payoff floor treats outlets: an enabler card below `--min-util-floor` is *enabler-only*, exempt from the utilization floor rather than excluded by it, so Chrome Mox with one variant can be selected and its variant still counts; a card in both tables stays a payoff card. Enabler-only cards are left out of the utilization statistics and the balancing objectives, and the cube may hold at most as many of them as the floors add up to. The floors are `--enabler-share` (default 0.05, 16 cards of 310) of the cube, split evenly among the categories (nothing in the pool says how much of each kind the combos want), each at least `--min-enablers` and at most twice the even split, or the table's own `min_enablers` / `max_enablers`; a category with fewer selectable cards than its floor has it lowered to what it has, with a warning. The floor needs the enabler table and nothing else; a configuration without the section needs `--min-enablers 0`. The defaults were chosen from the measurements in [docs/plans/enabler-support-plan.md](docs/plans/enabler-support-plan.md).
- The staples rule (`--staple-combos N`, on by default with N=25 and `--min-staple-combos 20`) holds the most popular combos of the pool in a Phase 2 cube, which no other rule asks for: the cube built before it, with popularity as a tiebreak only, completed 5 of the pool's 10 most popular combos and about half of the top 25, and Phase 1 none of the top 10 (the measurements are in [docs/plans/potential-combos-plan.md](docs/plans/potential-combos-plan.md)). A *staple* is the set of named cards of one of the N most popular variants; variants with the same named cards (the same combo with another template filler) are one staple, and a variant naming a superset of another's cards is its own. The cube must complete at least `--min-staple-combos` of them (0: all). The named cards of the staples are exempt from `--min-util-floor`, as the required cards are: Mindcrank is in two combos of the pool and would otherwise have to complete both. The rule is a cube rule like the others, so the reference cube and the warm-start repair satisfy it too; a minimum above the staples there are is lowered with a warning. The defaults come from the plan's Step 4: 20 of the top 25 raised the staples completed from 11 to 20 of 23 for 4.6% of the weighted combo count and 5% of the distinct combos, and made the completed combos more popular overall (the sum of their popularity up by a fifth); 40 of the top 50 cost 12% and 70 of the top 100 cost 21%, and `--popularity-weight` 0.5 to 2 without the rule changed neither the staples completed nor the popularity of the completed combos beyond the noise of a solve. The tracked cube is the 25/20 build.
- The tolerance measurements in this section were taken when `--combo-tolerance` was measured from the Phase 1 count. It is now measured from the best cube under the Phase 2 cube rules (coverage, color balance, archetype support, the card mix, the outcome rules, the payoff floor and the enabler floor), so the same value allows fewer combos than it did then.
- For `essential`, `tiered` and `softcap` the gap limit is measured against the Phase 1 cube: the solve stops once the total overage is proven within `gap-limit` x (overage of the Phase 1 cube) of optimal.
- The reference solve stops at the time limit, so the reference (and the window with it) varies between runs of the same inputs; at full size its weighted combo count moved 12% over four runs. `--warm-start` with the previous build's `<output>_reference.txt` hints the reference solve with a cube that satisfied the rules last time, so the reference is at least that cube.
- If Phase 2 finds no solution (infeasible or out of time), the Phase 1 cube is written, a warning is logged and the stats file says `two_phase_fallback_to_phase1`.

**Single-Phase** (use `--single-phase`)
- Maximizes combo count only
- Faster but may result in unbalanced card utilization

## Design Documentation

- [Architecture](docs/architecture.md) - The system as implemented: data pipeline, the two-phase ILP model and its objectives, outputs and known limitations
- [Plans and design documents](docs/plans/README.md) - Working documents from each round of development, including the [ILP Improvement Plan](docs/plans/ilp-improvement-plan.md) with benchmark results and decisions

Key concepts:
- **Variant and Combo**: A variant is one Spellbook combo listing; a combo (or combo group) is the set of variants that are the same combo with a piece swapped. Reported as `combo_count` (variants) and `distinct_combo_count` (combos). `--variant-weight` sets how much the optimizer values further variants of a combo it already completes.
- **Card Utilization**: Number of completable variants each card participates in. A card counts for a variant when it is one of its required cards or belongs to the card pool of one of its requirement templates, whether or not it is the card that satisfies the template.
- **Essential Combos**: The completed combos the cube would lose without a card; the measure the default `essential` objective balances, so a card pays for the combos that depend on it alone and a redundant piece pays nothing
- **Utilization Cap**: The `essential` (default), `tiered` and `softcap` objectives penalize their measure above a cap, by default twice the Phase 1 median
- **Utilization Floor**: Every card in a Phase 2 cube takes part in at least `--min-util-floor` completable combos
- **Color Balance**: In a Phase 2 cube no color has more than `--max-color-ratio` times the cards of another color
- **Archetype Support**: In a Phase 2 cube every two-color pair can assemble at least `--min-pair-combos` distinct combos and every mono color `--min-mono-combos`, counting the combos whose color identity fits the archetype, and at most `--max-wide-combo-share` of the completed combos need three or more colors
- **Card Mix**: In a Phase 2 cube at most `--max-multicolor-share`, `--max-colorless-share`, `--max-expensive-share` and `--max-creature-share` of the cards are multicolor, colorless, at or above `--expensive-mana-value`, or creatures, and at least `--min-spell-share` are instants or sorceries
- **Outcome Support**: A Phase 2 cube completes at least `--min-outcome-combos` distinct combos of every outcome category (what a combo does, from its Spellbook features and the outcome table in `config.yaml`) and no category holds more than `--max-outcome-share` of its combos
- **Popularity**: Spellbook's usage count per combo; a tiebreak in the Phase 1 objective, or with `--popularity-weight` a multiplier on a combo's value in both phases
- **Payoff Support**: A Phase 2 cube holds payoff cards (outlets: the cards that turn an engine's infinite mana, storm count, tokens, triggers, life or counters into a win) of every category in the payoff table of `config.yaml`, found by Scryfall queries, named cards and inference from Spellbook's bundled variants: at least `--min-payoffs` per category and `--payoff-share` of the cube in all, split by the categories' combos in the pool; payoff cards in no combo are added to the candidate pool
- **Enabler Support**: A Phase 2 cube holds enabler cards (the cards that set combos up: fast mana, rituals, wheels and graveyard fillers) of every category in the enabler table of `config.yaml`, found by Scryfall queries and named cards: at least `--min-enablers` per category and `--enabler-share` of the cube in all, split evenly; enabler cards the pool lacks are added to the candidate pool, and enabler cards below the utilization floor are exempt from it
- **Fixed Cards**: The cube's cards outside the slots the optimizer fills (the `fixed_cards` file of `config.yaml`): in every model, counted by everything that counts combos and by nothing that shapes the cube's cards
- **Staples**: A Phase 2 cube completes at least `--min-staple-combos` (20) of the staples, the sets of named cards of the pool's `--staple-combos` (25) most popular variants
- **Potential Combos**: The variants of the pool the cube is one piece short of, by missing piece, written to `<output>_potential_combos.json` after every build and by `evaluate_cube --card-data`
- **Fallback Strategy**: Phase 2 failures automatically return Phase 1 results, marked as a fallback in the log and the stats file

## Development

### Using Taskfile

```bash
# Install dependencies
task install

# Run unit tests
task test

# Run tests with coverage
task test:cov

# Run linting and formatting
task lint
task format

# Type checking
task typecheck

# Run all checks (lint, format, typecheck, tests)
task check

# Scan dependencies for High/Critical vulnerabilities (requires grype: https://github.com/anchore/grype)
task security

# Build a cube (see Quick Start)
task build:ilp
task build:ilp-single

# Download the API data for a build into data/cache (see API Caching)
task precache
```

### Manual Commands

```bash
# Testing
uv run pytest tests/unit -v
uv run pytest tests/unit --cov=src --cov-report=term-missing

# Linting
uv run ruff check --select I --fix .
uv run ruff check . --fix

# Formatting
uv run ruff format .

# Type checking
uv run ty check .
```

### Test Coverage

The project includes comprehensive unit tests for the ILP optimization logic.

Tests focus on:
- Data model validation and backward compatibility
- Utilization calculation and statistics
- Constraint satisfaction and optimization
- Single-phase and two-phase solver behavior, with every Phase 2 objective checked against brute-force enumeration of a small instance (`essential` also on a grouped instance, where it keeps a redundant outlet that `tiered` removes), the essential measure against the card data's `essential_combos`, and the warm-start cube that hints the reference solve
- Edge cases (empty combos, insufficient cards, etc.)
- Combo grouping: the weighted score, group variables and distinct combo reporting
- The Phase 2 cube rules on small hand-built instances: coverage, color balance, archetype minimums and the wide combo cap, the card mix caps and floor, the outcome minimums and share cap, and the fallback when a rule cannot be met
- The popularity weight: a weight that flips the Phase 1 choice, and the Phase 2 window in score units
- The outcome category table: parsing, substring and regex patterns, per-category minimums, and the shipped default
- The payoff table: parsing, the outlet inference on bundled variants, the union of sources with exclusions and the blocklist, the shipped default; the payoff floor on small instances, payoff-only cards under the utilization floor, the card mix and every Phase 2 objective
- The generic card table the payoff and enabler tables share, and the enabler table: parsing with its own keys and errors, the shipped default, the resolved set; the enabler floor on small instances, enabler-only cards below the utilization floor and in no combo, a card in both tables, the cap under `mad` and `minmax`
- Stats file generation and formatting, including the `colors`, `archetypes`, `card_mix`, `outcomes`, `popularity`, `payoffs` and `enablers` blocks
- Scryfall fetching (template searches, card attributes, payoff and enabler queries: cache, cache versioning, retries, rate limiting) against a fake HTTP session, and the precache script
- CLI and runner plumbing, including a check that every layer declares the same defaults

The unit tests make no network requests.

Run `task test:cov` to generate an HTML coverage report in `htmlcov/`.

## How It Works

### ILP Method
1. Fetch and preprocess combo variants
2. Build constraint satisfaction model with:
   - Cube size constraint
   - Combo completion requirements (required cards + optional requirements conditions)
   - Popularity-based tiebreaking
3. **Phase 1**: Maximize weighted combo count
4. **Phase 2** (unless `--single-phase`): Balance the cube with the combo score held within tolerance of the reference: the best cube found under the Phase 2 cube rules (coverage, color balance, archetype support, the card mix, the outcome rules, the payoff floor and the enabler floor), from an extra solve after Phase 1 with the same time limit (hinted with the Phase 1 cube, or with `--warm-start`'s cube). The Phase 2 model adds exact combo completion (a combo counts if and only if the cube completes it), one utilization variable per card, the utilization floor (payoff-only and enabler-only cards exempt), the cube rules and the chosen objective (by default `essential`, which adds one essential-combo variable per card). It is warm-started from that reference cube, repaired in a second solve if it has cards below the floor (also with the same time limit, stopping at the first cube that meets the floor).
5. Output optimized card list and statistics

### ILP Complexity

The ILP model scales as follows (where **Q** = cube size, **N** = number of combos, **C** = number of unique cards):

| Aspect | Phase 1 | Phase 2 |
|--------|---------|---------|
| Binary variables | C + N + G | C + N + G + P + H |
| Integer variables | 0 | C (utilization) + objective variables |
| Constraints | O(N × R + N) | O(N × R + N + C + P × K) |

Where **R** is the average number of optional requirements per combo, **G** the number of combo groups with two or more variants (about 1,200 at 20,000 variants; they exist when `--variant-weight` is below 1 or an archetype or outcome rule is on), **P** the number of distinct requirement card pools (about 80 at 20,000 variants) and **K** the cards per pool (at most 10). **H** are the per-archetype indicators of the groups whose variants differ in color identity (about 2,200), used by the archetype minimums and the wide combo cap. The archetype rules add 15 constraints plus the wide combo cap, the card mix caps and floor one each (plus 20 for `--mono-color-ratio`), the color balance 20, the outcome minimums one per category, the payoff floor one per category of the payoff table and the enabler floor one per category of the enabler table. **C** includes the payoff and enabler cards the pool lacks (about 100 and 45 with the default tables). Objective variables: 1 for `maxutil`, 2 for `minmax`, up to C for `softcap`, up to 3C for `tiered`, 2C for `mad`; `essential` adds C essential-combo variables, one boolean per (card, combo group) pair where the card is a piece of some but not all of the group's variants (about 10,000 at 20,000 variants) and up to 3C overage variables.

**Measured solve times** (8 workers, warm cache; see the [ILP Improvement Plan](docs/plans/ilp-improvement-plan.md)). The first three rows are from before combo grouping and the Phase 2 cube rules (`--variant-weight 1`, only coverage in Phase 2); the last is the tracked `data/current_best_cube.txt`, built with the current defaults by `task build:ilp` (360 s per phase; `essential`, the staples rule, the reference solve hinted by the previous tracked cube's reference):

| Cube size | Variants | Phase 1 | Phase 2 (`tiered`; `essential` takes the same time at full size) |
|-----------|----------|---------|--------------------|
| 100 | 1,000 | 0.2 s, optimal | runs to a 30 s limit |
| 200 | 5,000 | about 3 s, optimal | about 80 s to reach the 5% gap limit (one run) |
| 300 | 10,000 | about 5 s, optimal | runs to the 300 s limit (16-17.5% gap left) |
| 310 | 20,000, current defaults | runs to the 360 s limit (best cube found, not proven) | a reference solve to its own 360 s limit and a floor repair with the same limit of its own (it stops early at the first cube meeting the floor), then runs to the 360 s limit |

- With `--variant-weight 1` Phase 1 is solved to optimality in seconds; with the default 0.1 the group variables make it run to its time limit at full size. Phase 2 is the expensive part either way: at full size no objective reaches its stop rule, so the result is the best cube found when the time limit expires. Most of the improvement happens in the first 3 minutes.
- `--time-limit` (default 300 s; `task build:ilp` passes 360) applies to each phase separately, and the warm-start preparation before Phase 2 (the reference solve and the floor repair) gets the same limit for each of its two solves, outside the Phase 2 limit: with every cube rule on, a fifth of the limit left the reference well short of the best cube (Phase 2 holds a window measured from it), and the repair of a better reference found no cube in its fifth. A full build is therefore up to four solves of 360 s, though the repair usually stops early.
- Phase 2 timings vary noticeably between runs (the parallel search is not deterministic).
- **Keep `--workers` at 8.** CP-SAT chooses its set of search strategies by worker count, so fewer workers is not just slower: with 4 workers Phase 1 did not prove optimality within the time limit at any tested size (8 workers: about 5 s at full size), and Phase 2 found no solution at all at 200 and 300 cards. More than 8 workers has not been measured.

## License

MIT
