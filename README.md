# MTG Combo Cube

A tool for building Magic: The Gathering combo cubes by optimizing card selection to maximize combo potential. Supports both greedy heuristic and ILP-based optimization methods.

## Setup

### Prerequisites
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### Installation

```bash
# Install dependencies with uv
uv sync

# Or with pip
pip install -e .
```

## Usage

### Quick Start

```bash
# Build a 300-card cube using ILP (two-phase optimization, default)
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
-d, --debug            Enable debug logging
```

### Examples

```bash
uv run python -m src.mtg_combo_cube --help

# Two-phase ILP (balanced card utilization)
uv run python -m src.mtg_combo_cube -c 300 --method ilp

# Single-phase ILP (max combos only)
uv run python -m src.mtg_combo_cube -c 300 --method ilp --single-phase

# Greedy with custom ratio
uv run python -m src.mtg_combo_cube -c 360 --method greedy -r 1.5

# Custom output file and time limit
uv run python -m src.mtg_combo_cube -c 450 --method ilp -o my_cube.txt -t 600
```

### Output Files

- **data/cube.txt**: List of selected cards (one per line)
- **data/cube_stats.json**: Utilization statistics and optimization metrics (ILP only)

## Optimization Methods

### Greedy (Default CLI Method)
Fast heuristic approach that iteratively selects high-impact cards. Good for quick iterations.

### ILP (Recommended)
Integer Linear Programming using OR-Tools CP-SAT solver. Provides optimal solutions with two operational modes:

**Two-Phase (Default)**
- Phase 1: Maximize combo count
- Phase 2: Minimize card utilization variance while preserving combo count
- Produces balanced cubes where cards participate more evenly across combos
- Outputs detailed statistics to `{output}_stats.json`

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

The project includes comprehensive unit tests for the ILP optimization logic:

- **96% coverage** of ILP optimizer (ilp_optimizer.py)
- **100% coverage** of ILP models (ilp_models.py)
- **55% coverage** of ILP runner (ilp_runner.py - integration logic)

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
   - Combo completion requirements (required cards + optional requirements)
   - Popularity-based tiebreaking
3. **Phase 1**: Maximize weighted combo count
4. **Phase 2** (if enabled): Minimize utilization variance with fixed combo count
5. Output optimized card list and statistics

## License

MIT
