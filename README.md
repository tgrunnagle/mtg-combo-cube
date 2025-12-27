# MTG Combo Cube

A tool for building Magic: The Gathering combo cubes by optimizing card selection to maximize combo potential.

## Summary

Given a target cube size (e.g., 360 cards), this tool selects cards that maximize the number of completable combos while ensuring balanced card utilization. This problem is a variant of a **weighted maximum coverage** or **set cover**, which is NP-hard. It fetches combo data from the [Commander Spellbook API](https://commanderspellbook.com/) and uses Integer Linear Programming (ILP) to find optimal solutions.

**The core optimization problem:**
- Select exactly N cards for the cube
- Maximize the number of completable combos (a combo is completable if all its required cards are in the cube)
- Balance card utilization so each card contributes to roughly the same number of combos

**Two-phase approach (default):**
1. **Phase 1** - Maximize combo count using weighted optimization (popularity as tiebreaker)
2. **Phase 2** - Minimize utilization variance while preserving combo count (within configurable tolerance)

This produces cubes where every card pulls its weight, avoiding "dead" cards that don't contribute to any combos.

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
task build:ilp CARD_COUNT=300
# or
uv run python -m src.mtg_combo_cube -c 300 --method ilp

# Build using greedy method
uv run python -m src.mtg_combo_cube -c 300 --method greedy
```

### Command-Line Options

```
-c, --cube-size        Cube size (default: 300)
-m, --method           Optimization method: greedy or ilp (default: greedy)
-o, --output-file      Output file path (default: data/cube.txt)
-r, --ratio            Golden ratio for greedy method (default: 1.2)
-t, --time-limit       ILP solver time limit in seconds (default: 300)
-n, --max-variants     Max combo variants to fetch (default: 10000)
--single-phase         Use single-phase ILP (disables utilization balancing)
--combo-tolerance      Phase 2 combo count tolerance (default: 0.1 = 10%)
--gap-limit            Phase 2 early termination gap (default: 0.05 = 5%)
--min-coverage-ratio   Min coverage ratio for requirement templates (default: 0.1)
--profile              Enable detailed profiling of ILP optimization
--blocklist            Path to card blocklist file (default: data/blocklist.txt)
--skip-api-caching     Skip writing API responses to cache files
--read-api-cache       Read from cache if available, fall back to API if not
-d, --debug            Enable debug logging
```

### Examples

```bash
uv run python -m src.mtg_combo_cube --help

# Two-phase ILP (balanced card utilization)
uv run python -m src.mtg_combo_cube -c 300 --method ilp

# Single-phase ILP (max combos only)
uv run python -m src.mtg_combo_cube -c 300 --method ilp --single-phase

# Two-phase with strict combo count (no tolerance)
uv run python -m src.mtg_combo_cube -c 300 --method ilp --combo-tolerance 0

# Two-phase with 25% combo tolerance (allows trading combos for better balance)
uv run python -m src.mtg_combo_cube -c 300 --method ilp --combo-tolerance 0.25

# Greedy with custom ratio
uv run python -m src.mtg_combo_cube -c 360 --method greedy -r 1.5

# Custom output file and time limit
uv run python -m src.mtg_combo_cube -c 450 --method ilp -o my_cube.txt -t 1800
```

### Output Files

- **data/cube.txt**: List of selected cards (one per line)
- **data/cube_stats.json**: Utilization statistics and optimization metrics (ILP only)

### API Caching

The tool caches Commander Spellbook API responses to speed up repeated runs and reduce API load.

**Cache behavior:**
- By default, API responses are written to `data/cache/` after fetching
- Use `--read-api-cache` to read from cache when available (falls back to live API on cache miss)
- Use `--skip-api-caching` to disable writing to cache
- Cache files are named based on parameters: `variants_cards{max}_max{variants}.json`

```bash
# First run: fetches from API and caches results
uv run python -m src.mtg_combo_cube -c 300 --method ilp

# Subsequent runs: use cached data for faster iteration
uv run python -m src.mtg_combo_cube -c 300 --method ilp --read-api-cache

# Force fresh API fetch without caching
uv run python -m src.mtg_combo_cube -c 300 --method ilp --skip-api-caching
```

## Optimization Methods

### Greedy (Default CLI Method)
Fast heuristic approach that iteratively selects high-impact cards. Good for quick iterations.

### ILP (Recommended)
Integer Linear Programming using OR-Tools CP-SAT solver. Provides optimal solutions with two operational modes:

**Two-Phase (Default)**
- Phase 1: Maximize combo count
- Phase 2: Minimize card utilization variance while preserving combo count (within tolerance)
- Produces balanced cubes where cards participate more evenly across combos
- Outputs detailed statistics to `{output}_stats.json`
- `--combo-tolerance` controls how much Phase 2 can deviate from Phase 1's combo count (default: 10%). Set to 0 for strict equality.
- `--gap-limit` enables early termination when the solution is within N% of optimal (default: 5%). This significantly speeds up Phase 2 by accepting "good enough" solutions instead of waiting for proof of optimality. Set to 0 to require exact optimality.

**Single-Phase** (use `--single-phase`)
- Maximizes combo count only
- Faster but may result in unbalanced card utilization

## Design Documentation

Detailed design documents are available in [docs/](docs/):

- [ILP Design Doc](docs/ilp_design_doc.md) - Mathematical formulation and constraint design for Phase 1
- [Multi-Objective Design Doc](docs/multi_objective_design_doc.md) - Two-phase optimization with utilization balancing
- [Implementation Plans](docs/) - Step-by-step implementation guides for both approaches

Key concepts:
- **Card Utilization**: Number of completable combos each card participates in
- **MAD Minimization**: Phase 2 minimizes Mean Absolute Deviation of utilization
- **Fallback Strategy**: Phase 2 failures automatically return Phase 1 results

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

# Run the application
task run
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
- Single-phase and two-phase solver behavior
- Edge cases (empty combos, insufficient cards, etc.)
- Stats file generation and formatting

Run `task test:cov` to generate an HTML coverage report in `htmlcov/`.

## How It Works

### Greedy Method
1. Fetch top combos by popularity from Commander Spellbook API
2. Select core cards that appear most frequently across combos
3. Fill remaining slots with cards from "almost included" combos
4. Remove dead cards (cards not in any completable combo)
5. Iterate until cube reaches target size

### ILP Method
1. Fetch and preprocess combo variants
2. Build constraint satisfaction model with:
   - Cube size constraint
   - Combo completion requirements (required cards + optional requirements conditions)
   - Popularity-based tiebreaking
3. **Phase 1**: Maximize weighted combo count
4. **Phase 2** (if enabled): Minimize utilization variance with fixed combo count
5. Output optimized card list and statistics

### ILP Complexity

The ILP model scales as follows (where **Q** = cube size, **N** = number of combos, **C** = number of unique cards):

| Aspect | Phase 1 | Phase 2 |
|--------|---------|---------|
| Binary variables | C + N | C + N |
| Integer variables | 0 | 3C |
| Constraints | O(N × R) | O(N × R + C) |

Where **R** is the average number of optional requirements per combo.

**Practical scaling behavior:**
- **Model construction** is O(N × R × K) where K is cards per requirement
- **Solve time** is bounded by `--time-limit` (default 300s), but typically:
  - Small cubes (Q < 200, N < 1000): seconds
  - Medium cubes (Q ~ 300, N ~ 5000): 10-60 seconds
  - Large cubes (Q > 400, N > 8000): may hit time limit

The solver uses 8 parallel workers and sophisticated pruning, so actual performance depends heavily on problem structure (card overlap between combos) rather than raw input size. Cube size **Q** primarily affects constraint tightness rather than model size.

## License

MIT
