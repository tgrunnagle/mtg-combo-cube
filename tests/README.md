# MTG Combo Cube Tests

This directory contains unit tests for the MTG Combo Cube project, with a focus on the ILP optimization logic.

## Test Structure

```
tests/
├── unit/                          # Unit tests (no external dependencies)
│   ├── test_ilp_models.py        # Data model tests
│   ├── test_ilp_optimizer.py     # Core optimization logic tests
│   └── test_ilp_runner.py        # Stats generation tests
└── README.md                      # This file
```

## Running Tests

```bash
# Run all unit tests
task test

# Run with coverage report
task test:cov

# Run specific test file
uv run pytest tests/unit/test_ilp_optimizer.py -v

# Run specific test class
uv run pytest tests/unit/test_ilp_optimizer.py::TestILPOptimizerSolve -v

# Run specific test
uv run pytest tests/unit/test_ilp_optimizer.py::TestILPOptimizerSolve::test_solve_simple_combo -v
```

## Test Coverage

Current coverage (as of last update):

- **ilp_models.py**: 100% - All data model functionality
- **ilp_optimizer.py**: 96% - Core optimization algorithms
- **ilp_runner.py**: 55% - Integration and async logic

## Test Categories

### Data Models (`test_ilp_models.py`)

Tests for Pydantic models and data structures:

- **ComboData**: Combo representation and requirement validation
- **UtilizationStats**: Statistical metrics for card utilization
- **OptimizationResult**: Result container with backward compatibility

### ILP Optimizer (`test_ilp_optimizer.py`)

Tests for the core optimization engine:

1. **Initialization**: Card collection, participation graph construction
2. **Helper Methods**: Weight computation, utilization calculation, statistics
3. **Single-Phase Solver**: Constraint satisfaction, combo completion, edge cases
4. **Two-Phase Solver**: Utilization balancing, combo count preservation, improvement metrics

Key test scenarios:
- Empty combos
- Insufficient cards for cube size
- Simple and complex combo structures
- Optional requirements (OR constraints)
- Overlapping combos (card sharing)
- Utilization variance minimization

### ILP Runner (`test_ilp_runner.py`)

Tests for output generation and statistics:

- Stats file generation (single-phase and two-phase)
- JSON structure and metadata
- Improvement metric calculation
- Top/bottom utilized cards
- Filename derivation
- Edge cases (zero division, empty results)

## Testing Philosophy

These tests focus on:

1. **Unit isolation**: No external API calls or file I/O (except temp files)
2. **Logic coverage**: Core algorithms thoroughly tested
3. **Edge cases**: Empty inputs, boundary conditions, error handling
4. **Backward compatibility**: Optional fields with defaults
5. **Deterministic behavior**: Reproducible results

## Future Test Additions

Potential areas for expansion:

- Integration tests with real combo data (smaller datasets)
- Performance tests for large-scale optimization
- Property-based testing with hypothesis
- Regression tests for specific combo patterns

## Contributing

When adding new features:

1. Write tests first (TDD approach)
2. Maintain >90% coverage for core logic
3. Include edge cases and error conditions
4. Document any non-obvious test scenarios
5. Run `task test:cov` before committing
