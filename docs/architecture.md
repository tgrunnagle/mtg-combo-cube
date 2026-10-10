# Architecture

This document describes the system as it is implemented today. For how it got here, including
benchmark results and the reasoning behind decisions, see the documents in [plans/](plans/).
For setup, flags and usage, see the [project README](../README.md).

## Purpose

Given a cube size N, pick N Magic: The Gathering cards so that as many combos as possible can be
assembled from the cube, and so that the cards are used by a comparable number of combos rather
than a few hub cards carrying everything. Combo data comes from the
[Commander Spellbook](https://commanderspellbook.com/) API. Template requirements such as
"a creature with persist" are resolved to concrete cards through the
[Scryfall](https://scryfall.com/) API.

Two builders exist:

- **ILP** (default): an exact model solved with the OR-Tools CP-SAT solver, in two phases.
- **Greedy**: the original heuristic. Fast, no optimality guarantee, kept for comparison.

## System overview

```mermaid
flowchart TD
    CLI["__main__.py<br/>argument parsing"] --> RUN["runner.py<br/>load blocklist, pick method"]

    RUN -->|ilp| ILPR["ilp/ilp_runner.py"]
    RUN -->|greedy| GR["greedy/greedy_runner.py"]

    ILPR --> LOAD["load_instance"]
    LOAD --> SB["spellbook/<br/>CommanderSpellbook + SpellbookCache"]
    LOAD --> PRE["ilp/combo_preprocessor.py<br/>Variant -> ComboData"]
    PRE --> SF["scryfall/scryfall_fetcher.py<br/>ScryfallFetcher"]

    ILPR --> CAF["scryfall/card_attribute_fetcher.py<br/>CardAttributeFetcher"]
    CAF --> SF
    ILPR --> PAY["ilp/payoffs.py<br/>payoff table + inference"]
    PAY --> PF["scryfall/payoff_fetcher.py<br/>PayoffFetcher"]
    PF --> SF

    ILPR --> OPT["ilp/ilp_optimizer.py<br/>ILPOptimizer"]
    OPT --> P1["Phase 1<br/>maximize combo score"]
    P1 --> P2["Phase 2<br/>balance utilization<br/>under the cube rules"]
    OPT --> EVAL["ilp/cube_evaluation.py<br/>ground-truth scoring"]

    ILPR --> OUT["cube .txt + _stats.json"]

    GR --> SB
    GR --> VT["greedy/variant_tracker.py"]
    VT --> SF
    GR --> OUTG["cube .txt"]

    SB -.-> API1[("Commander Spellbook API")]
    SF -.-> API2[("Scryfall API")]
    SB -.-> CACHE[("data/cache/")]
    SF -.-> CACHE
```

## Package layout

All code lives under `src/mtg_combo_cube/`.

| Module | Responsibility |
|--------|----------------|
| `__main__.py` | CLI. Parses flags and calls `runner.run`. |
| `runner.py` | Loads the blocklist and dispatches to the ILP or greedy runner. |
| `models.py` | Pydantic models for Commander Spellbook responses (`Variant`, `CardUse`, `Requirement`, `Template`, ...) and `CardAttributes`, a card's Scryfall data (color identity, type line, mana value). |
| `blocklist.py` | Reads `data/blocklist.txt`: one card name per line, `#` comments allowed. |
| `precache.py` | Command-line entry point that fills the caches ahead of a build. |
| `spellbook/commander_spellbook.py` | Async client for the Spellbook API: paged variant listing and the "find my combos" endpoint. |
| `spellbook/api_cache.py` | `SpellbookCache`: file cache for the variant listing. |
| `scryfall/scryfall_fetcher.py` | `ScryfallFetcher`: template lookups with disk cache, rate limiting and retries. Shared by both builders. |
| `scryfall/card_attribute_fetcher.py` | `CardAttributeFetcher`: the `CardAttributes` of named cards, with its own disk cache. Used by the color balance and card mix rules and the color and card mix statistics. |
| `scryfall/payoff_fetcher.py` | `PayoffFetcher`: the cards matching the payoff table's Scryfall queries, with its own disk cache. |
| `ilp/requirement_normalizer.py` | Canonical keys for template requirements and URL preparation for Scryfall. |
| `ilp/combo_preprocessor.py` | Turns variants into the ILP instance (`ComboData`, `CandidateCard`). |
| `ilp/ilp_models.py` | Dataclasses for the instance, statistics and `OptimizationResult`. |
| `ilp/ilp_optimizer.py` | `ILPOptimizer`: builds and solves the CP-SAT models. |
| `ilp/cube_evaluation.py` | Pure functions that score a set of cards: completed variants and distinct combos, utilization, combos per draft archetype, combos per outcome category, popularity, color distribution, card mix. |
| `ilp/outcomes.py` | The outcome category table (`data/outcome_categories.json`): category names with feature-name patterns, an optional catch-all category without patterns, and `categorize`. |
| `ilp/payoffs.py` | The payoff table (`data/payoffs.json`): per outcome category the queries, cards and exclusions that find its outlets; the outlet inference from bundled variants; the resolved `PayoffTable`. |
| `ilp/evaluate_cube.py` | Command-line entry point that scores an existing cube file. |
| `ilp/profiling.py` | Timing, variable and constraint counts, solver statistics. |
| `ilp/ilp_runner.py` | Orchestrates an ILP build and writes the outputs. |
| `greedy/greedy_runner.py` | The greedy builder. |
| `greedy/variant_tracker.py` | Card frequency and popularity tallies for the greedy builder. |

## Data pipeline

### 1. Fetch variants

`CommanderSpellbook.get_variants` pages through the Spellbook API, most popular first, keeping
combos of at most four cards and skipping any that need a specific commander. It stops after
`--max-variants` combos. `SpellbookCache` stores the result in
`data/cache/variants_cards{K}_max{N}.json`.

The API rate-limits bursts and blocks for several minutes, so the client pauses half a second
between pages and retries HTTP 429 and 5xx responses with doubling backoff for up to about
seven minutes. A first download of 20,000 variants takes about 13 minutes.

### 2. Preprocess into an ILP instance

`ComboPreprocessor.preprocess_variants` converts each variant into a `ComboData`:

- `required_cards`: the specific cards the combo names.
- `requirement_options`: one entry per template requirement. Each holds the pool of cards that
  can satisfy it, and a `group_key` that identifies the same template across combos.
- `group_key`: the combo the variant is one way of assembling, from the variant's `of` ids
  (sorted and joined with `+`, so a variant of two combos combined is a group of its own).
  Variants sharing a key are the same combo with a piece swapped. At 20,000 variants there
  are about 8,700 groups; 7,400 have a single variant and the largest has 301.
- `color_identity`: the variant's Spellbook color identity as WUBRG letters in that order
  (empty for colorless). It covers the named cards only; a template requirement filled by a
  colored card can add a color. About 530 of the 8,700 groups have variants of differing
  identity.
- `features`: the names of the features the variant produces on Spellbook ("Infinite
  colored mana", "Infinite creature ETB", ...; 814 distinct names at 20,000 variants), and
  `bracket_tag`, its Spellbook bracket letter. The features place a combo in its outcome
  categories; a group's features are the union over its variants.
- `includes`: the Spellbook combo ids the variant includes. A variant that bundles an engine
  with an outlet (infinite mana plus Walking Ballista) includes the engine's combo beside
  its own; two thirds of the 20,000 variants include more than one combo. The payoff
  inference reads this.

A template's pool is the first ten cards Scryfall returns for the template's search, ordered by
EDHREC rank, after removing blocklisted cards.

A combo is left out of the instance for one of four reasons, counted in `DropReason` and logged
in one summary line:

| Reason | Meaning |
|--------|---------|
| `blocked_card` | A required card is on the blocklist. |
| `no_scryfall_api` | A requirement has no Scryfall search, so it cannot be resolved to cards. |
| `scryfall_failure` | The Scryfall lookup failed after retries. Logged as a warning, since it changes the instance. |
| `empty_match` | The search matched no usable card. |

The preprocessor also returns the card universe as `CandidateCard` objects, each recording the
combos that require the card and the template groups it can satisfy.

### 3. Scryfall access

`ScryfallFetcher` returns the raw ordered card names for a search URL. Callers apply their own
blocklist and limit afterwards, so the cache stays valid when those change.

- **Cache:** one file, `data/cache/scryfall_templates.json`, keyed by the prepared URL. Written
  to a temporary file and then swapped in, flushed every 50 new results and on close.
- **Politeness:** one shared HTTP session, 100 ms between requests, an identifying `User-Agent`.
- **Retries:** up to five attempts on HTTP 429, 5xx and network errors, with doubling backoff
  that respects `Retry-After`.
- **Outcomes:** a 404 means "no cards match" and is cached as an empty result. A failure is
  never cached.

`CardAttributeFetcher` looks up every candidate card before the solve, in batches of 75 names
through the Scryfall collection endpoint, and keeps three things per card as a
`CardAttributes`: the color identity (WUBRG letters), the type line and the mana value
(Scryfall's `cmc`: the front face's for a double-faced card, the sum for a split card). A
card's types are the words of its front face's type line before the em dash. It sends its
requests through a `ScryfallFetcher`, so the same politeness and retry rules apply, and caches
results in `data/cache/scryfall_card_attributes.json` (version 1; an older or malformed file
is treated as empty). The attributes feed the Phase 2 color balance and card mix rules and the
color and card mix statistics. After a failed lookup the run continues without them.

### Payoff cards

`ilp/payoffs.py` turns the payoff table `data/payoffs.json` into the `PayoffTable` of an
instance: for each non-terminal outcome category (mana, storm, tokens, lifegain, counters by
default; every name must be a category of the outcome table), the outlets that turn its
engines into a win, each with its sources:

- **Inferred** (`infer_payoffs`): for every variant that includes more than one combo and has
  a terminal result (a category outside the payoff table), the engine is a variant of the pool
  whose `includes` and cards are strict subsets and whose categories are all in the payoff
  table; the cards the bundled variant adds are the outlet, credited to the engine's
  categories. A card counts once per bundled variant, and is kept from
  `--payoff-inference-min` (default 2) on. Spellbook files most outlet combos as
  self-contained, so this finds a handful of cards per category (Suture Priest, Gravitic
  Punch) and is a supplement to the table.
- **Cards**: names the table gives.
- **Queries**: Scryfall searches the table gives, resolved by `PayoffFetcher` in EDHREC order
  over paper cards (`unique=cards`); the first 25 unblocked cards of each count, so the cache
  holds the raw first page as the template cache does. A query that matches no card is a
  table error (`PayoffTableError`), reported and not cached. The cache is
  `data/cache/scryfall_payoffs.json`, keyed by the search URL with the time each was fetched,
  under the same two flags as the other caches.

The exclusions and the blocklist are removed from the union. `build_cube_ilp` reads both
tables before the instance is loaded (a bad table fails fast), resolves the queries, runs the
inference on the preprocessed combos, and adds every payoff card the pool lacks to the
candidate cards as a `CandidateCard` with no combos: a *payoff-only* card, selectable for the
payoff floor and nothing else. With the floor off, a default table that is missing or does not
fit a custom outcome table is skipped with a warning; with it on, both are errors.

### Cache flags

All caches follow the same two flags. Reads happen only with `--read-api-cache`. Writes happen
unless `--skip-api-caching` is given. With a warm cache, an ILP run makes no network requests.
The greedy builder uses the Scryfall cache but always calls the Spellbook API live.

### Precaching

`precache.py` (`task precache`) fills all four caches for one configuration without solving
anything. It runs the same steps as a build: fetch the variants, preprocess them with the
blocklist, resolve the payoff table's queries, look up the attributes of the candidate and
payoff cards. Its arguments are the values that decide what a build reads: `--max-variants`
and `--max-cards-in-combo` name the variants file, `--blocklist` decides which templates and
cards are looked up, and `--payoffs` which queries. A payoff query that matches no card is
reported as a table error.

By default it reads nothing from the cache, so the variants file and every entry of the
configuration are fetched again and overwritten; `--keep-existing` reads the cache and fetches
only what is missing. The clients retry single requests themselves. On top of that, a stage
that still has failed requests is run again, up to `--max-passes` times with a doubling wait,
and only the failures are requested again. The script exits with status 1 when a request kept
failing, so the cache is known to be incomplete.

## The ILP optimizer

`ILPOptimizer` takes the instance, the cube size and the tuning parameters. Its entry points are
`solve()` for Phase 1 alone (`--single-phase`) and `solve_two_phase()` for the default flow.

### Base model

Shared by every phase, built by `_build_base_model`:

```
x[c] in {0,1}     card c is in the cube
y[j] in {0,1}     variant j is counted as complete
g[k] in {0,1}     combo group k has a complete variant

sum_c x[c] = N                                  cube size
y[j] <= x[c]            for each required card c of variant j
y[j] <= sum x[c]        over the pool of each template requirement of variant j
g[k] <= sum_{j in k} y[j],  g[k] >= y[j]        for each group k of two or more variants
```

The card universe is every card a variant names or a template pool holds, plus the payoff
cards the pool lacks (payoff-only cards, see "Payoff cards"), which appear in no `y`
constraint. The `y` constraints only stop `y[j]` from being 1 when the cube is missing
something. That is sufficient whenever `y` is being maximized. `g` is exact in both directions; the group
variables exist when `--variant-weight` is below 1 (the score needs them) or an archetype or
outcome rule is on (the rules count groups), and are left out otherwise.

### Combo score

The quantity Phase 1 maximizes and the Phase 2 window holds. With `v = --variant-weight`:

```
score = sum_k [ (1 - v) * g[k] + v * sum_{j in k} y[j] ]
```

The first completed variant of a group is worth 1 and each further one `v`, so with `v = 1`
the score is the variant count and with `v = 0` the number of distinct combos. Groups of one
variant contribute `y[j]` directly. The score is scaled by `WEIGHT_SCALE` (10,000) to stay
integer. `_combo_score` computes the same number from a set of completed variant ids, for the
reference cube and the warm-start checks.

With `w = --popularity-weight` above 0 (default 0), each group's term is multiplied by its
popularity factor `1 + w * log(1 + p_k) / log(1 + max_p)`, where `p_k` is the popularity of
the group's most popular variant and `max_p` the largest in the pool, so the most popular
combo is worth `1 + w` combos of zero popularity and the median one about `1 + 0.45 w`. The
factor is applied to both the `g` and the `y` weights of a group, rounded to integers in
`WEIGHT_SCALE` units, and the same factors are used by `_combo_score`, so the Phase 2 window
holds the popularity-weighted score and cannot trade popular combos for obscure ones. The
stats file reports this score as `combo_score` beside the plain `weighted_combo_count`.

### Phase 1: maximize combo count

```
maximize  WEIGHT_SCALE * score + tiebreak        t(p) = 0.001 * log(1 + p)
tiebreak = sum_{j not grouped} t(popularity[j]) * y[j] + sum_{k grouped} t(max popularity in k) * g[k]
```

With `--popularity-weight` 0, popularity is a tiebreak only, added once per counted item:
per variant for groups without a `g` variable, per group otherwise. A further variant of a
grouped combo then earns exactly `v`, so with `v = 0` the objective counts distinct combos,
and with `v = 1` this is the popularity-weighted variant count. With a weight above 0 the
score's weights carry the popularity factor (see "Combo score") and the tiebreak still
applies on top of it, so a tie in the weighted score goes to the more popular cube. Weights
are scaled to integers for CP-SAT.

### Phase 2: balance utilization

Phase 2 builds a fresh model: the base model plus the following, in this order.

1. **Combo count window.** The combo score must stay within `--combo-tolerance` (10% by
   default) of the reference score: the best cube found under the cube rules below (see
   "Reference cube and warm start"). The window edges are whole combos: `floor` and `ceil` of
   the reference in combo units, times `WEIGHT_SCALE`. With `--variant-weight` below 1 the
   tolerance is in weighted combos, so a cube may trade variants of a completed combo for
   new combos inside the window.
2. **Coverage constraints.** For each template group used by at least 10 combos, the cube must
   contain at least `--min-coverage-ratio` x (combos using it) cards from the group's pool,
   capped at the pool size.
3. **Exact combo linking.** `y[j]` is forced to 1 whenever the cube completes combo j. Each
   distinct option pool gets one boolean `z` meaning "at least one card of this pool is
   selected", shared by every combo using that pool. Then `y[j] >= (satisfied parts) - (k - 1)`
   over the k required cards and pools of the combo. Without this, an objective that rewards low
   utilization could switch off combos the cube actually completes.
4. **Utilization variables.** `u[c]` is the number of completed combos card c takes part in, or
   0 when c is not selected. A card takes part in a combo if it is a required card or appears in
   one of the combo's template pools. `u[c]` is bounded by the number of combos c appears in.
5. **Utilization floor.** Every selected card must have `u[c] >= --min-util-floor` (default 2).
   A card that can never reach the floor is excluded outright. Payoff-only cards are exempt:
   they complete no combo by definition (`u` is the constant 0) and are in the pool as
   outlets, so the floor, the warm-start floor checks and the `minmax` and `mad` objectives
   leave them out.
6. **Color balance.** For every ordered pair of colors, `count[a] <= ratio * count[b]`, with
   `--max-color-ratio` (default 2) written as an integer fraction. `count` is the number of
   selected cards whose color identity includes the color, so a multicolor card counts once
   per color. Colorless cards are unconstrained here (the colorless cap below limits them).
   The rule is skipped when the ratio is 0 or no card data could be fetched.
7. **Archetype minimums.** For each of the ten two-color pairs, the number of completed
   combos (groups) whose color identity fits within the pair is at least
   `--min-pair-combos` (default 250); for each mono color, at least `--min-mono-combos`
   (default 150). Mono-colored and colorless combos count for every archetype they fit
   in. A group whose variants all fit contributes its `g` (or the `y` of a single variant);
   a group where only some variants fit gets a bool `h <= sum(y of the fitting variants)`,
   so the count can only be under-estimated and the minimum is exact.
8. **Wide combo cap.** At most `--max-wide-combo-share` (default 0.25) of the completed
   combos may need three or more colors, written as
   `(den - num) * total <= den * narrow` over the group indicators, where `narrow` counts
   the groups with a completed variant of at most two colors (the same `h` construction).
   In the one-sided repair models every wide variant is also linked exactly, so the solver
   cannot meet the cap by leaving `y` at 0 for a wide combo the cube completes. Both
   archetype rules are skipped at 0 (the cap also at 1), and when the combos carry no color
   identities.
9. **Card mix caps and floor.** For each of multicolor, colorless, expensive
   (`--expensive-mana-value` or more, default 5) and creature cards, the selected cards with
   that attribute number at most `floor(share x cube size)`, with `--max-multicolor-share`
   (default 0.15), `--max-colorless-share` (0.25), `--max-expensive-share` (0.2) and
   `--max-creature-share` (0.6); and at least `ceil(--min-spell-share x cube size)` (0.05)
   selected cards are instants or sorceries. Each is one linear constraint over the
   candidate cards that count for it, decided from the Scryfall attributes before the solve.
   Lands are left out of every card mix rule, and a card without attributes counts as
   colorless, typeless and mana value 0. Shares are rounded to hundredths; a share of 0
   skips a rule (a cap of 1 too), and so does missing card data: none at all, or more than
   5% of the candidates without it. Before the solve, a warning names any rule the pool
   cannot meet (too few candidates for the floor, or too few cards outside a cap to fill
   the cube).
10. **Mono-colored balance.** `--mono-color-ratio` (default 0, off) applies the color balance
    form to the cards whose identity is exactly one color.
11. **Outcome minimums.** For each category of the outcome table (what the combos do:
    mana, damage, tokens, triggers, draw, mill, lifegain, counters, turns, lock, storm, win,
    and the catch-all `other`, a category without patterns that holds every combo no other
    category matches), the number of completed combos (groups) in it is at least
    `--min-outcome-combos`, or the category's own `min_combos` when the table gives one. A group is in every category one
    of its variants' features matches, decided before the solve (`outcome_groups`), so the
    count is a sum of group indicators: under-countable, hence the minimum is exact. 0
    disables a category's minimum, and a minimum above the number of combos the pool has
    in the category is lowered to that number with a warning (every such combo must then
    be completed). The archetype minimums are instead kept and fall back: they are two
    numbers for a pool known to be large, so a shortfall there is a configuration error,
    whereas one outcome minimum applies to every category of an editable table, and a
    small category would otherwise make every Phase 2 infeasible.
12. **Outcome share cap.** At most `--max-outcome-share` (default 0, off) of the completed
    combos may be in any one category, written per category as
    `(den - num) * inside <= num * outside` over the group indicators. As for the wide combo
    cap, every variant of a group in a category is linked exactly so the one-sided repair
    models cannot leave `y` at 0 for a completed combo inside; under-counting `outside`
    only tightens the cap. Both outcome rules are skipped without a table.
13. **Payoff floor.** For each category of the payoff table whose outcome has at least one
    combo in the pool, at least its floor of the category's payoff cards are selected: one
    linear constraint over `x`, exact in every model. The floors are computed once in the
    constructor (`_compute_payoff_floors`): every category gets `--min-payoffs` (default 2)
    or the table's own `min_payoffs`, and with `--payoff-share` (default 0.15) the floors
    add up to that share of the cube, the cards above the minimums going one at a time to
    the category with the most distinct pool combos per outlet (`_apportion`, the D'Hondt
    method) up to twice the even split or the table's `max_payoffs`. Only selectable cards
    count (payoff-only cards, or combo pieces at or above the utilization floor); a category
    with fewer of them than its floor has it lowered to what it has, with a warning, as the
    outcome minimum is, and the apportionment passes those cards on. Skipped at
    `--min-payoffs 0`, without a payoff table, and without an outcome table (which says
    whether a category has engines in the pool).
14. **The objective**, chosen with `--phase2-objective`.
15. **Warm start.** The model is hinted with a starting cube.

Coverage, color balance, the archetype minimums, the wide combo cap, the card mix rules, the
outcome rules and the payoff floor are the *cube rules* (`_cube_rules`): each is a pair of `add(base)` and
`violations(cards)`, and the list is applied by the Phase 2 model, by the warm-start repair
models and by the check that decides whether the Phase 1 cube needs repairing. A new hard
constraint on the cube is one entry in that list; the card mix rules are built from one
share-cap helper and one share-floor helper (`_card_mix_rules`).

#### Objectives

Each objective is a method registered in `ILPOptimizer._PHASE2_OBJECTIVES`. It receives the
model and the utilization variables, adds what it needs, and sets the objective. Adding an
objective means writing one `_add_<name>_objective` method, registering it, and adding the name
to the CLI choices.

| Name | Minimizes | Notes |
|------|-----------|-------|
| `tiered` (default) | Utilization above T, plus above 2T, plus above 4T | Convex penalty: one card far above the cap costs more than several slightly above it. |
| `softcap` | Total utilization above a cap T | Penalizes every over-used card equally per unit. |
| `maxutil` | The highest utilization of any selected card | The floor handles the low end. |
| `minmax` | Highest minus lowest utilization | Two auxiliary variables. |
| `mad` | Total absolute deviation from the Phase 1 mean | Two deviation variables per card. |

The cap T is `--util-cap` when given, otherwise twice the Phase 1 median utilization.

Every objective subtracts a small versatility bonus for cards that satisfy more template groups.
For all but `mad`, the main term is scaled so that the bonus can never outweigh one unit of it.

#### Stopping

Each phase has its own `--time-limit`. Phase 2 also stops early once the solution is proven
within `--gap-limit` of optimal (5% by default). For `softcap` and `tiered` the optimum can be
near zero, where a relative gap is meaningless, so the limit is measured as a fraction of the
Phase 1 cube's overage instead.

#### Reference cube and warm start

Phase 1 ignores the cube rules, and at full size they are expensive: coverage and color
balance alone cost close to 10% of the combos, and with the archetype rules, the card mix,
the outcome minimum and the payoff floor the reference cube scores nearly 30% below the Phase
1 cube (1,140.0 against 1,582.8 weighted combos in the tracked run). Measuring the combo window from the
Phase 1 count would then leave no feasible cube. `_build_warm_start` therefore prepares two things before the Phase 2 model
is built, both in the small Phase 1 model with the cube rules added:

1. **Reference cube** (`_best_constrained_cube`). If the Phase 1 cube breaks a cube rule, the
   combo score is maximized under all of them, hinted with the Phase 1 cube, for at most 20%
   of the Phase 2 time limit. The combo score of the result is the reference the window is
   measured from. It is the best cube found in that time, not a proven maximum. If the
   Phase 1 cube already satisfies every rule, it is the reference.
2. **Floor repair** (`_repair_floor`). If the reference cube has cards below the utilization
   floor, the lower edge of the window and the floor as a penalty are added, the total
   shortfall is minimized, and the search stops at the first cube with none. This takes at
   most 20% of the time limit.

The floor is a penalty in step 2 because as a hard constraint it makes even a first solution
hard to find at larger pool sizes. Both steps count against the Phase 2 time limit. The Phase 2
model is hinted with the repaired cube, or with the reference cube if the repair found none.

#### Fallback

If Phase 2 finds no solution, the Phase 1 result is returned with `phase2_fell_back` set, along
with the Phase 2 status, time and profile. The stats file then reports
`two_phase_fallback_to_phase1`. Two warnings help place the cause. Before the solve, a pool
check names any archetype or outcome category the whole pool has too few combos for and any
card mix rule the pool cannot meet (too few instants and sorceries for the floor, too few
cards outside a cap). After a failure, the fallback warning says whether a cube satisfying
the rules was found at all: if not, it lists the rules the Phase 1 cube still breaks (with
the archetypes and outcome categories below their minimum and the payoff categories below
their floor); if so, the cause lies in the combo window, the utilization floor or the time
limit.

### Ground-truth reporting

No reported number is read from the solver's `y` or `g` variables. After each solve,
`_extract_solution` takes the selected cards and calls `cube_evaluation` to recompute which
variants are complete, how many distinct combos they belong to (`completable_group_keys`), the
groups with the most completed variants (`largest_combo_groups`), the distinct combos each
draft archetype can assemble (`compute_archetype_stats`), the distinct combos per outcome
category (`combos_per_outcome`, with the number in no category), how popular the distinct
combos are (`popularity_stats`: median, mean log popularity, share below the pool median),
which payoff cards of each category it holds (`PayoffTable.stats`) and each card's
utilization.
If the solver's view disagrees, a warning is logged. The color distribution and the card mix
(`compute_color_stats`, `compute_card_mix_stats`) are computed by the runner from the selected
cards and their Scryfall attributes when it writes the log and the stats file. The same
functions back the `evaluate_cube` command, so a cube file scored later gives the same numbers
as the run that produced it.

### Solver configuration

CP-SAT runs with `--workers` parallel search workers, 8 by default. CP-SAT chooses its search
strategies based on the worker count, and with fewer than 8 it fails to prove Phase 1 optimal on
this problem. `--debug` turns on the solver's search log. `--profile` records variable and
constraint counts, timings and solver statistics per phase.

## The greedy builder

`GreedyRunner` works in steps:

1. Tally cards across the most popular variants with `VariantTracker`, resolving templates to
   the top five Scryfall matches.
2. Take the top `cube_size / ratio` cards by popularity (`--ratio`, default 1.2).
3. Ask Spellbook which combos are almost complete given those cards, and add the most frequent
   missing cards.
4. Remove dead cards, meaning cards that appear in only one combo of the cube.
5. Refill to the cube size from the almost-complete combos.

It ignores the ILP-only flags, including `--max-variants`.

## Outputs

| File | Written by | Contents |
|------|------------|----------|
| `<output>.txt` | Both builders | The cube, one card name per line. |
| `<output>_stats.json` | ILP only | Metadata, Phase 1 and Phase 2 variant and distinct combo counts, utilization statistics, color distribution, combos per draft archetype, card mix, combos per outcome category, popularity and payoff cards, the Phase 2 settings in force, improvement and card changes, the largest combo groups, most and least used cards, per-template coverage, cross-template overlap, the resolved payoff table, and profiling data when `--profile` is set. |

The stats file's `optimization_method` is `single_phase`, `two_phase` or
`two_phase_fallback_to_phase1`. Its `phase2` block records the objective, the cap T used and
the reference cube the window was measured from (variants, distinct combos and the weighted
count). `combo_count` is always completed variants and `distinct_combo_count` the combos they
belong to; `metadata.variant_weight` records the weight the run used.

Each phase block has a `colors` entry computed from card color identities: cards per color
(a multicolor card counts once per color), the split into mono-colored, multicolor and
colorless cards, and the variance and standard deviation of the five per-color counts. It
also has an `archetypes` entry: `combos_per_archetype`, the distinct combos whose color
identity fits each of the ten color pairs, the five mono colors and `C` (colorless), and
`combos_by_color_count`, the distinct combos by the number of colors they need. A `card_mix`
entry gives the cards per type, the multicolor and colorless counts (nonland cards, as the
two caps count them), and the mana value histogram and means (overall and per color) of the
nonland cards. An `outcomes` entry gives `combos_per_outcome`, the distinct combos in each
category of the outcome table, with `uncategorized` (completed combos in no category) and
`total`; a `popularity` entry gives the median and mean log popularity of the distinct combos
and the share below the pool median. The `phase2` block records the archetype settings that
were applied, the card mix settings in force as `card_mix_rules` with the card counts they
applied as `card_mix_limits`, the number of candidate cards without Scryfall data, and the
outcome settings applied (`outcome_minimums` per category and `max_outcome_share`) and the
payoff floor applied (`min_payoffs` and `payoff_floors` per category). A `payoffs` entry per
phase gives `cards_per_category`, the payoff cards of each category in the cube, and `cards`,
each with its sources; a top-level `payoffs` block holds the resolved table (every card per
category with its sources and the counts per source) and everything the inference found with
its counts, so the table can be tuned from a run.
`metadata.popularity_weight` records the weight; with a weight above 0 each block also carries
the popularity-weighted `combo_score` (the `phase2` block also `reference_combo_score`).

`data/current_best_cube.txt` and its stats file are the tracked reference result: the default
settings (`--variant-weight 0.1`, the archetype minimums, the card mix rules, the outcome
minimum of 40 and the payoff floor of 2) at 300 cards and 20,000 variants with 360 s per phase. Everything else under
`data/` is ignored by git, including `data/cache/`.

## Testing

Unit tests live in `tests/unit/` and run with `task test`. `task check` adds lint, format and
type checking.

- **Optimizer:** behavior tests, characterization tests on a fixed small instance solved to
  optimality with one worker, and brute-force checks of each Phase 2 objective's optimum.
- **Cube rules:** one module each for combo grouping, color balance, archetype support, the
  card mix, the outcome rules, the popularity weight and the payoff floor, on hand-built
  instances where Phase 1 breaks the rule and Phase 2 must repair it (or where the weight
  flips the Phase 1 choice); they also cover the violation counters, the pool checks, the
  fallback messages and payoff-only cards under the utilization floor and every objective.
- **Evaluation:** the completion, grouping, utilization, archetype, outcome, popularity, color
  and card mix functions, the outcome table's parsing and matching, and the payoff table's
  parsing, inference and resolution.
- **Data layer:** the Scryfall fetchers (templates, card attributes, payoff queries) against a
  fake HTTP session (cache hit and miss, cache versions, retries, failures, 404), the
  preprocessor, the requirement normalizer, the blocklist and the precache script.
- **Wiring:** a test that defaults agree across the CLI, both runners and the optimizer, and
  that every option reaches the optimizer and the stats file.

No test touches the network.

## Known limitations

These are open design questions rather than defects. Details are in
[plans/ilp-improvement-plan.md](plans/ilp-improvement-plan.md) under "Follow-ups".

- **Utilization definition.** A card is counted for every completed variant whose template pool
  contains it, even when a different card fills that slot. Combined with the coverage
  constraints, this gives every card in a popular pool a high utilization that no objective can
  lower. Utilization is also counted in variants, not distinct combos, so a card in many
  variants of one combo looks like a hub (see
  [plans/combo-grouping-plan.md](plans/combo-grouping-plan.md), Step 2).
- **Color balance is all or nothing.** The rule requires every color to be present and is a
  hard constraint, and so is the optional mono-colored balance (`--mono-color-ratio`). See
  [plans/color-balance-plan.md](plans/color-balance-plan.md).
- **The card mix is coarse.** The caps and the spell floor are hard shares of the whole cube:
  there is no per-color mana curve, the instants and sorceries the floor brings in are combo
  pieces rather than chosen interaction, and a multi-faced card is typed by its front face.
  See [plans/card-mix-plan.md](plans/card-mix-plan.md).
- **Archetype minimums are absolute counts.** `--min-pair-combos` and `--min-mono-combos`
  do not scale with the cube size or the pool, so the defaults fit the default
  configuration only and a smaller run must lower them or pass 0. A share of the completed
  combos per pair would scale; see
  [plans/archetype-support-plan.md](plans/archetype-support-plan.md).
- **The reference count is not a proven maximum.** It comes from a time-limited solve, so it
  varies a little between runs, and with it the combo window. A combo tolerance of 0 can
  still be infeasible because of the utilization floor.
- **Phase 2 does not finish early at full size.** At the default configuration (300 cards,
  20,000 variants) it runs to its time limit and returns the best cube found, and the
  reference solve and warm-start repair take about a quarter of that time.
- **Phase 1 is not proven optimal with grouping.** With `--variant-weight` below 1 the group
  variables make Phase 1 run to its time limit at 300 cards and 20,000 variants (an 8% gap at
  the default 0.1 after 300 s). There is no separate Phase 1 gap or time setting.
- **Run-to-run variation.** A time-limited parallel search does not return the same cube twice.
- **Template pools are truncated.** Only the first ten Scryfall matches are considered.
- **Payoffs are a floor, not a link.** The payoff floor keeps a few outlets of each kind in
  the cube; it does not require an outlet for every engine the outcome minimum counts, and a
  payoff-only card has utilization 0 by definition. The precise form ("a mana combo counts
  only with a mana outlet present") and a wider inference are open questions in
  [plans/payoff-support-plan.md](plans/payoff-support-plan.md).
