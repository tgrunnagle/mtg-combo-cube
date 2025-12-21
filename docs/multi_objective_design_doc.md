# Multi-Objective ILP: Combo Count + Card Utilization Balance

## Problem Statement

Extend the ILP optimizer to jointly optimize two objectives:

1. **Maximize combo count** (primary, existing objective)
2. **Minimize variation in card utilization** — cards should appear in roughly equal numbers of combos

### Motivation

A cube where some cards appear in 20 combos while others appear in only 1 creates unbalanced gameplay. The "star" cards become must-picks, while low-utilization cards feel like filler. Balancing utilization creates more interesting draft decisions.

---

## Defining Card Utilization

For each card `c` in the cube, define:

```
utilization[c] = Σ_j (y[j] · x[c] · participates[c,j])
```

Where:
- `y[j] ∈ {0,1}` indicates combo j is completable
- `x[c] ∈ {0,1}` indicates card c is in the cube
- `participates[c,j] = 1` if card c either:
  - Is in `required_cards[j]` (from `uses`), OR
  - Is in any `requirement_options[j]` set (from `requires`)

**Rationale:** The primary goal is to avoid any single card being "overloaded" — appearing in too many combos. For popular requirement templates (e.g., "creature with haste"), we want multiple cards in the cube that can satisfy it, spreading the utilization across them.

This means a card's utilization reflects how many completed combos it *could* participate in, incentivizing the solver to:
1. Include diverse cards that satisfy popular requirements
2. Avoid selecting a single card that satisfies many different combos' requirements

---

## The Variance Problem

Standard deviation is defined as:
```
σ = √(Σ(u[c] - μ)² / N)
```

This is **non-linear** (quadratic terms, square root) and cannot be directly optimized in ILP/CP-SAT.

### Linearization Approaches

| Approach | Formulation | Pros | Cons |
|----------|-------------|------|------|
| **Range minimization** | min(U_max - U_min) | Simple, intuitive | Ignores distribution shape |
| **Mean absolute deviation** | min Σ\|u[c] - μ\| | Considers all cards | Requires known μ |
| **Minimax deviation** | min max_c \|u[c] - μ\| | Bounds worst case | May leave slack |
| **Two-phase** | Fix combo count, then optimize balance | Optimal combo count guaranteed | Requires two solves |

**Recommended: Two-phase with mean absolute deviation (MAD)**

This approach:
1. Guarantees we don't sacrifice combo count for balance
2. MAD is a well-known variance proxy that's fully linearizable
3. Works well with CP-SAT's constraint handling

---

## Two-Phase Optimization

### Phase 1: Maximize Combo Count (Existing)

Solve the current ILP to find optimal combo count `K*`:

```
maximize Σ_j y[j]
subject to:
  Σ_c x[c] = N                           (cube size)
  y[j] ≤ x[c]  ∀c ∈ required[j]          (required cards)
  y[j] ≤ Σ_{c ∈ opts} x[c]  ∀ opts       (requirement options)
```

### Phase 2: Minimize Utilization Variance

Re-solve with combo count fixed, minimizing mean absolute deviation:

```
minimize Σ_c (d_plus[c] + d_minus[c])
subject to:
  Σ_j y[j] = K*                          (preserve combo count)
  Σ_c x[c] = N                           (cube size)
  
  # Utilization definition
  u[c] = Σ_j (y[j] · required[c,j])      (for each card)
  
  # MAD linearization
  u[c] - μ ≤ d_plus[c]                   (positive deviation)
  μ - u[c] ≤ d_minus[c]                  (negative deviation)
  d_plus[c], d_minus[c] ≥ 0
  
  # Original combo constraints...
```

**Computing μ (mean utilization):**

Calculate from Phase 1 solution:
```
μ = (Σ_c u[c]) / cube_size
```

---

## CP-SAT Implementation Details

### Precomputing Participation

```python
# Build mapping: card -> list of combos it participates in
card_to_combos: dict[str, list[ComboData]] = defaultdict(list)

for combo in self.combos:
    # Cards in required_cards
    for card in combo.required_cards:
        card_to_combos[card].append(combo)
    
    # Cards in requirement_options
    for opts in combo.requirement_options:
        for card in opts:
            if combo not in card_to_combos[card]:  # avoid double-counting
                card_to_combos[card].append(combo)
```

### Utilization Variables

```python
# u[c] = utilization of card c (integer: 0 to max possible)
u: dict[str, cp_model.IntVar] = {}
for card in self.all_cards:
    max_possible = len(card_to_combos[card])
    u[card] = model.new_int_var(0, max_possible, f"util_{card}")
```

### Utilization Constraints

The utilization constraint is more complex because we need:
- `u[c] = Σ_j (y[j] · x[c] · participates[c,j])`

This is a product of two binary variables (`y[j]` and `x[c]`). We can linearize using:

```python
# For each card, utilization = sum of completed combos it participates in
# But only if the card is selected

for card in self.all_cards:
    participating_combos = card_to_combos[card]
    
    if not participating_combos:
        model.add(u[card] == 0)
        continue
    
    # u[c] = x[c] * Σ_j (y[j] · participates[c,j])
    # Linearize: u[c] ≤ Σ_j y[j] (when x[c]=1)
    #           u[c] ≤ M * x[c] (when x[c]=0, u[c]=0)
    #           u[c] ≥ Σ_j y[j] - M*(1-x[c])
    
    combo_sum = sum(y[combo.id] for combo in participating_combos)
    M = len(participating_combos)  # big-M value
    
    model.add(u[card] <= combo_sum)
    model.add(u[card] <= M * x[card])
    model.add(u[card] >= combo_sum - M * (1 - x[card]))
```

Alternatively, using CP-SAT's conditional constraints:

```python
for card in self.all_cards:
    participating_combos = card_to_combos[card]
    
    if not participating_combos:
        model.add(u[card] == 0)
        continue
    
    combo_sum = sum(y[combo.id] for combo in participating_combos)
    
    # If card is selected: u[c] = sum of completed combos
    model.add(u[card] == combo_sum).only_enforce_if(x[card])
    
    # If card is not selected: u[c] = 0
    model.add(u[card] == 0).only_enforce_if(x[card].Not())
```

### MAD Linearization

```python
# Mean can be computed from Phase 1 or estimated
target_mean = phase1_total_utilization // cube_size

# Deviation variables
d_plus: dict[str, cp_model.IntVar] = {}
d_minus: dict[str, cp_model.IntVar] = {}

for card in self.all_cards:
    max_dev = max(target_mean, max_utilization - target_mean)
    d_plus[card] = model.new_int_var(0, max_dev, f"dplus_{card}")
    d_minus[card] = model.new_int_var(0, max_dev, f"dminus_{card}")
    
    # Only count deviation for selected cards
    # u[c] - target ≤ d_plus[c] + M·(1 - x[c])
    # target - u[c] ≤ d_minus[c] + M·(1 - x[c])
    model.add(u[card] - target_mean <= d_plus[card]).only_enforce_if(x[card])
    model.add(target_mean - u[card] <= d_minus[card]).only_enforce_if(x[card])
    
    # Zero deviation for unselected cards
    model.add(d_plus[card] == 0).only_enforce_if(x[card].Not())
    model.add(d_minus[card] == 0).only_enforce_if(x[card].Not())
```

### Phase 2 Objective

```python
# Fix combo count from Phase 1
model.add(sum(y[combo.id] for combo in self.combos) == phase1_combo_count)

# Minimize total absolute deviation
model.minimize(sum(d_plus[card] + d_minus[card] for card in self.all_cards))
```

---

## Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    Phase 1: Maximize Combos                      │
├─────────────────────────────────────────────────────────────────┤
│  1. Solve existing ILP                                          │
│  2. Record: K* (combo count), utilization per card              │
│  3. Calculate target_mean = total_utilization / cube_size       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Phase 2: Balance Utilization                  │
├─────────────────────────────────────────────────────────────────┤
│  1. Add constraint: combo_count = K*                            │
│  2. Add utilization variables and constraints                   │
│  3. Add MAD deviation variables                                 │
│  4. Minimize sum of deviations                                  │
│  5. Extract balanced solution                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Plan

### New/Modified Files

| File | Changes |
|------|---------|
| `ilp_models.py` | Add `UtilizationStats`, extend `OptimizationResult` |
| `ilp_optimizer.py` | Add `solve_multi_objective()` method implementing two-phase |
| `ilp_runner.py` | Add `build_cube_ilp_multi_objective()` entry point |
| `__main__.py` | Add `--multi-objective` flag |

### New Data Structures

```python
@dataclass
class UtilizationStats:
    """Statistics about card utilization in completed combos."""
    min_utilization: int
    max_utilization: int
    mean_utilization: float
    std_deviation: float
    total_absolute_deviation: int


@dataclass 
class MultiObjectiveOptimizationResult:
    """Extended result with utilization balance metrics."""
    # Base fields from OptimizationResult
    selected_cards: list[str]
    completable_combo_ids: list[str]
    combo_count: int
    objective_value: float
    solve_time_seconds: float
    status: str
    
    # Multi-objective specific fields
    utilization_per_card: dict[str, int]
    utilization_stats: UtilizationStats
    phase1_solve_time: float
    phase2_solve_time: float
```

### Core Algorithm

```python
def solve_multi_objective(self) -> MultiObjectiveOptimizationResult:
    """Two-phase solve: max combos, then min utilization variance."""
    
    # Phase 1: Maximize combo count
    phase1_result = self.solve()
    if phase1_result.status not in ("OPTIMAL", "FEASIBLE"):
        return self._convert_to_multi_objective_result(phase1_result)
    
    # Calculate utilization from Phase 1
    phase1_utilization = self._calculate_utilization(phase1_result)
    target_mean = sum(phase1_utilization.values()) // self.cube_size
    
    # Phase 2: Minimize MAD with fixed combo count
    return self._solve_phase2(
        target_combo_count=phase1_result.combo_count,
        target_mean=target_mean,
        phase1_time=phase1_result.solve_time_seconds
    )
```

### CLI Integration

```bash
# Standard ILP (max combos only)
uv run python -m src.mtg_combo_cube -c 300 --method ilp

# Multi-objective ILP (max combos + min utilization variance)
uv run python -m src.mtg_combo_cube -c 300 --method ilp --multi-objective
```

---

## Edge Cases

### Cards Not Participating in Any Combo

Some cards in the universe may not participate in any combo (neither in `required_cards` nor `requirement_options`). These will have `u[c] = 0` and won't be selected unless the cube size forces inclusion of non-participating cards.

**Handling:** The solver naturally avoids these cards since they contribute nothing to combo count and would skew utilization variance downward.

### Infeasible Balance Targets

If the combo structure inherently requires certain cards in many combos (e.g., a card that's the only option for a popular requirement template), perfect balance is impossible.

**Handling:** The solver will find the best achievable balance. Report the achieved std deviation vs theoretical minimum.

### Phase 2 Timeout

Phase 2 may be harder than Phase 1 due to additional constraints.

**Handling:** Return Phase 1 solution with a warning if Phase 2 times out or is infeasible.

---

## Success Metrics

| Metric | Single-Objective | Multi-Objective Target |
|--------|------------------|------------------------|
| Combo count | K* | K* (preserved) |
| Utilization std dev | σ₁ | σ₂ < σ₁ |
| Max utilization | high | reduced |
| Min utilization | possibly 0 | increased |
| Solve time | T₁ | T₁ + T₂ (< 2× T₁ ideally) |

---

## Future Improvements

1. **Warm-start Phase 2**: Use Phase 1 solution as initial hint for faster convergence
2. **Pareto frontier**: Generate multiple solutions with different combo/balance tradeoffs
3. **Per-combo weights**: Prioritize balance in high-popularity combos
4. **Alternative variance proxies**: Experiment with range minimization or Gini coefficient
5. **Utilization bounds**: Allow user to specify max utilization per card as hard constraint
