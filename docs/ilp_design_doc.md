# ILP-Based MTG Combo Cube Optimization

## Problem Statement

Given a universe of MTG combos from Commander Spellbook, select exactly N cards to maximize the total weighted value of completable combos.

### Current Approach (Greedy + Error Correction)

The existing implementation uses a multi-phase heuristic:

1. Fetch top combos by popularity
2. Select top cards by appearance frequency or popularity
3. Fill remaining slots with "almost included" cards
4. Remove "dead cards" (cards appearing in only one combo)
5. Backfill with more almost-included cards

**Problems:**
- No optimality guarantees
- Iterative error correction is fragile
- Doesn't jointly optimize card selection
- May miss synergies between less-popular cards

---

## Proposed Solution: Integer Linear Programming

### Decision Variables

```
x[c] ∈ {0, 1}  for each card c       # 1 if card is in cube
y[j] ∈ {0, 1}  for each combo j      # 1 if combo is completable
```

### Objective Function

Maximize the total number of completed combos:

```
maximize Σ_j  y[j]
```

For tiebreaking among solutions with equal combo counts, we use log-scaled popularity as a secondary weight with small coefficient:

```
maximize Σ_j  (1 + ε · log(1 + popularity[j])) · y[j]

where ε = 0.001  # Small enough to not override combo count
```

This ensures:
1. Primary goal: maximize combo count
2. Secondary goal: prefer popular combos when combo counts are equal

### Constraints

**1. Cube Size Constraint**
```
Σ_c x[c] = N
```

**2. Combo Completion - Required Cards**

For each combo j, all cards in `uses` must be present:
```
y[j] ≤ x[c]    ∀ c ∈ required_cards[j]
```

**3. Combo Completion - Optional Requirements**

For combos with `requires` (template-based requirements), at least one matching card must be present:
```
y[j] ≤ Σ_{c ∈ matching_cards[r]} x[c]    ∀ r ∈ requirements[j]
```

Where `matching_cards[r]` is the set of cards satisfying requirement template r (fetched via Scryfall API).

**4. Variable Bounds**
```
x[c], y[j] ∈ {0, 1}
```

---

## Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    Data Collection Phase                         │
├─────────────────────────────────────────────────────────────────┤
│  1. Fetch top K combos from Commander Spellbook API             │
│  2. For each combo with requirements, resolve template cards    │
│  3. Build card universe (all cards appearing in any combo)      │
│  4. Build combo-to-card mapping                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Model Construction Phase                      │
├─────────────────────────────────────────────────────────────────┤
│  1. Create binary variable for each unique card                 │
│  2. Create binary variable for each combo                       │
│  3. Add cube size constraint                                    │
│  4. Add combo completion constraints                            │
│  5. Set objective coefficients                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Optimization Phase                            │
├─────────────────────────────────────────────────────────────────┤
│  1. Solve ILP using OR-Tools CP-SAT solver                      │
│  2. Extract selected cards from solution                        │
│  3. Identify which combos are completable                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Details

### New Module: `ilp_optimizer.py`

```python
from ortools.sat.python import cp_model
from dataclasses import dataclass
import math

@dataclass
class ComboData:
    """Preprocessed combo data for ILP."""
    id: str
    required_cards: set[str]           # Cards in 'uses'
    requirement_options: list[set[str]] # For each 'requires', valid cards
    popularity: int                     # For tiebreaking

class ILPOptimizer:
    def __init__(
        self,
        combos: list[ComboData],
        cube_size: int,
        tiebreak_epsilon: float = 0.001,
    ):
        self.combos = combos
        self.cube_size = cube_size
        self.tiebreak_epsilon = tiebreak_epsilon
        
    def _compute_weight(self, popularity: int) -> float:
        """Combo count + small log-popularity tiebreaker."""
        return 1.0 + self.tiebreak_epsilon * math.log1p(popularity)
        
    def build_model(self) -> tuple[cp_model.CpModel, dict, dict]:
        """Construct the CP-SAT model."""
        ...
        
    def solve(self, time_limit_seconds: int = 300) -> list[str]:
        """Solve and return selected card names."""
        ...
```

### Preprocessing Pipeline

```python
class ComboPreprocessor:
    """Converts Variant objects to ILP-ready ComboData."""
    
    REQUIREMENT_CARD_LIMIT = 10
    
    async def preprocess(
        self,
        variants: list[Variant],
    ) -> tuple[list[ComboData], set[str]]:
        """
        Returns:
            - List of ComboData for ILP (with popularity for tiebreaking)
            - Set of all card names in universe
        """
        ...
```

### Weight Calculation

```python
import math

def compute_weight(
    popularity: int | None,
    tiebreak_epsilon: float = 0.001,
    default_popularity: int = 1,
) -> float:
    """
    Primary objective: combo count (weight = 1.0)
    Secondary objective: log-scaled popularity for tiebreaking
    """
    pop = popularity if popularity is not None else default_popularity
    return 1.0 + tiebreak_epsilon * math.log1p(pop)
```

---

## Handling Requirements (Templates)

The `requires` field contains template-based requirements that can be satisfied by any matching card. Example:

```json
{
  "template": {
    "name": "A creature with haste",
    "scryfall_api": "https://api.scryfall.com/cards/search?q=type:creature+keyword:haste"
  }
}
```

**Strategy:**
1. Cache Scryfall API responses (already done in `VariantTracker`)
2. Limit to top 10 cards by EDHREC rank per template (sufficient coverage for optimization)
3. Create OR constraint: `y[j] ≤ x[c1] + x[c2] + ... + x[c10]`

---

## Solver Configuration

Using OR-Tools CP-SAT (better than traditional ILP for this problem size):

```python
solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = 300
solver.parameters.num_search_workers = 8  # Parallel search
solver.parameters.log_search_progress = True
```

**Why CP-SAT over MILP solvers?**
- Handles large binary problems efficiently
- Good parallelization
- No license requirements (unlike Gurobi/CPLEX)
- Proven effective for weighted set cover variants

---

## Scalability Considerations

| Parameter | Typical Value | Impact |
|-----------|---------------|--------|
| Cube size (N) | 300-500 | Minimal |
| Combos (K) | 5,000-10,000 | O(K) constraints |
| Unique cards | ~3,000-5,000 | O(cards) variables |
| Requirement expansions | ~10 per template | Increases constraint density |

**Expected solve times:**
- 300 cards, 5000 combos: < 30 seconds
- 500 cards, 10000 combos: < 5 minutes

**Fallback strategy:** If solve time exceeds limit, return best solution found (CP-SAT provides feasible solutions during search).

---

## API Changes

### New Entry Point

```python
async def build_cube_ilp(
    cube_size: int,
    max_combos: int = 10000,
    max_cards_in_combo: int = 4,
    time_limit_seconds: int = 300,
) -> tuple[list[str], list[Variant]]:
    """
    Build cube using ILP optimization.
    
    Maximizes total number of completable combos.
    
    Returns:
        - List of card names in cube
        - List of completable combos
    """
```

### CLI Integration

```bash
# Existing greedy approach
uv run python -m src.mtg_combo_cube -c 300 --method greedy

# New ILP approach
uv run python -m src.mtg_combo_cube -c 300 --method ilp --time-limit 300
```

---

## Testing Strategy

### Unit Tests

1. **Weight calculation**: Verify popularity/size tradeoffs
2. **Constraint generation**: Small hand-crafted examples
3. **Requirement expansion**: Mock Scryfall responses

### Integration Tests

1. **Small ILP solve**: 50 cards, 100 combos → verify optimal
2. **Consistency**: Same input → same output (deterministic solver settings)

### Comparison Tests

1. **ILP vs Greedy**: Run both, compare combo counts
2. **Solution quality**: Track objective value, not just combo count

---

## Migration Path

1. **Phase 1**: Implement `ILPOptimizer` as standalone module
2. **Phase 2**: Add `--method ilp` CLI flag, keep greedy as default
3. **Phase 3**: Benchmark on real data, tune parameters
4. **Phase 4**: Make ILP default if proven superior

---

## Dependencies

Add to `pyproject.toml`:

```toml
dependencies = [
    "aiohttp>=3.13.2",
    "pydantic>=2.12.5",
    "ortools>=9.10",  # NEW
]
```

---

## Future Improvements

1. **Warm starting**: Seed ILP with greedy solution for faster convergence on large instances
2. **Incremental solving**: Re-optimize when adding/removing specific cards
3. **Multi-objective mode**: Pareto frontier of combo count vs average combo quality
4. **Constraint relaxation**: Allow "near-complete" combos (missing 1 card) with reduced weight

---

## Success Metrics

| Metric | Current (Greedy) | Target (ILP) |
|--------|------------------|--------------|
| Combo count (300 cards) | Baseline | ≥ Baseline (optimal) |
| Solve time | ~30s | < 5 min |
| Dead cards | ~10-20 | 0 (by construction) |
| Reproducibility | Variable | Deterministic |

The primary success metric is **combo count**—the ILP solution should match or exceed the greedy solution while guaranteeing no dead cards.
