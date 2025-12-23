# Plan: Track Cross-Template Card Overlap

## Summary

Add tracking for cards that satisfy multiple requirement templates (e.g., "Kitchen Finks" satisfies both "Green creature with persist" and "Persist Creature"). Introduce a central `CandidateCard` type that aggregates all card relationships. Include this in stats output and add a bonus weight in the optimizer to prefer versatile cards.

## Files to Modify

1. `src/mtg_combo_cube/ilp/ilp_models.py` - Add `CandidateCard`, update `ComboData` and `RequirementOption`
2. `src/mtg_combo_cube/ilp/combo_preprocessor.py` - Build `CandidateCard` instances during preprocessing
3. `src/mtg_combo_cube/ilp/ilp_optimizer.py` - Use `CandidateCard` for optimization and stats
4. `src/mtg_combo_cube/ilp/ilp_runner.py` - Update stats JSON output
5. `tests/unit/test_ilp_optimizer.py` - Add unit tests
6. `docs/cross_template_overlap_plan.md` - Save this plan as documentation

---

## Step 1: Add Central `CandidateCard` Model (`ilp_models.py`)

Add new dataclass that centralizes all card relationships:

```python
@dataclass
class CandidateCard:
    """A card that could be included in the cube, with all its relationships."""
    name: str
    combo_ids: frozenset[str]  # Combo IDs where this card is directly required (from 'uses')
    requirement_group_keys: frozenset[str]  # Requirement option keys this card satisfies

    @property
    def template_count(self) -> int:
        """Number of distinct requirement templates this card satisfies."""
        return len(self.requirement_group_keys)

    @property
    def is_multi_template(self) -> bool:
        """Whether this card satisfies multiple requirement templates."""
        return self.template_count >= 2
```

Update `ComboData` to reference card names (unchanged, but document relationship):
```python
@dataclass
class ComboData:
    id: str
    required_cards: frozenset[str]  # Card names from 'uses' field
    requirement_options: list[RequirementOption]  # Template + valid card names
    popularity: int
```

Update `RequirementOption` to store card names (unchanged structure):
```python
@dataclass
class RequirementOption:
    template_name: str
    group_key: str
    cards: frozenset[str]  # Card names that match this template
```

Add stats dataclasses for reporting:

```python
@dataclass
class TemplateOverlapPair:
    """Statistics about overlap between two requirement templates."""
    template1_name: str
    template2_name: str
    shared_cards: list[str]
    overlap_count: int
    jaccard_similarity: float  # |intersection| / |union|

@dataclass
class CrossTemplateStats:
    """Aggregate statistics for cross-template card overlap."""
    multi_template_card_count: int
    max_templates_per_card: int
    mean_templates_per_card: float
    cards_by_template_count: dict[int, int]
    top_versatile_cards: list[CandidateCard]  # Top 10 by template_count
    top_overlapping_pairs: list[TemplateOverlapPair]  # Top 10 by overlap_count
```

Extend `OptimizationResult`:
```python
cross_template_stats: CrossTemplateStats | None = None
```

---

## Step 2: Build `CandidateCard` Instances (`combo_preprocessor.py`)

Modify `ComboPreprocessor.preprocess_variants()` to also build candidate cards:

```python
async def preprocess_variants(
    self,
    variants: list[Variant],
) -> tuple[list[ComboData], dict[str, CandidateCard]]:
    """
    Convert variants to ComboData and build candidate cards.

    Returns:
        - List of ComboData for ILP
        - Dict of card_name -> CandidateCard with all relationships
    """
    combo_data_list: list[ComboData] = []

    # Track relationships per card
    card_combo_ids: dict[str, set[str]] = defaultdict(set)
    card_requirement_keys: dict[str, set[str]] = defaultdict(set)

    for variant in variants:
        combo_data = await self._process_single_variant(variant)
        if combo_data is None:
            continue

        combo_data_list.append(combo_data)

        # Track direct combo participation
        for card in combo_data.required_cards:
            card_combo_ids[card].add(combo_data.id)

        # Track requirement template satisfaction
        for opt in combo_data.requirement_options:
            for card in opt.cards:
                card_requirement_keys[card].add(opt.group_key)

    # Build CandidateCard instances
    all_card_names = set(card_combo_ids.keys()) | set(card_requirement_keys.keys())
    candidate_cards = {
        name: CandidateCard(
            name=name,
            combo_ids=frozenset(card_combo_ids.get(name, set())),
            requirement_group_keys=frozenset(card_requirement_keys.get(name, set())),
        )
        for name in all_card_names
    }

    return combo_data_list, candidate_cards
```

---

## Step 3: Update ILP Optimizer (`ilp_optimizer.py`)

### 3a: Update `__init__` to accept candidate cards

```python
def __init__(
    self,
    combos: list[ComboData],
    candidate_cards: dict[str, CandidateCard],  # NEW
    cube_size: int,
    ...
):
    self.combos = combos
    self.candidate_cards = candidate_cards
    self.cube_size = cube_size
    ...

    # Build card universe from candidate cards
    self.all_cards: list[str] = sorted(candidate_cards.keys())
```

### 3b: Add versatility bonus in Phase 2 objective

```python
VERSATILITY_EPSILON = 0.0001  # Small bonus for multi-template cards

def _solve_phase2(self, ...):
    ...
    # Original MAD objective
    mad_terms = sum(d_plus[card] + d_minus[card] for card in self.all_cards)

    # Add versatility bonus (subtract because we're minimizing)
    versatility_bonus = sum(
        int(self.VERSATILITY_EPSILON * math.log1p(
            self.candidate_cards[card].template_count
        ) * self.WEIGHT_SCALE) * x[card]
        for card in self.all_cards
    )

    model.minimize(mad_terms - versatility_bonus)
```

### 3c: Add cross-template stats calculation

```python
def _calculate_cross_template_stats(
    self,
    selected_cards: set[str],
    group_key_to_name: dict[str, str],
) -> CrossTemplateStats:
    """Calculate cross-template overlap statistics for selected cards."""

    # Get candidate cards that are selected
    selected_candidates = [
        self.candidate_cards[name]
        for name in selected_cards
        if name in self.candidate_cards
    ]

    # Count distribution
    template_counts = [c.template_count for c in selected_candidates]
    cards_by_count: dict[int, int] = {}
    for count in template_counts:
        cards_by_count[count] = cards_by_count.get(count, 0) + 1

    # Multi-template cards sorted by template_count
    multi_template = [c for c in selected_candidates if c.is_multi_template]
    top_versatile = sorted(multi_template, key=lambda c: -c.template_count)[:10]

    # Calculate pairwise template overlaps
    template_to_cards: dict[str, set[str]] = defaultdict(set)
    for card in selected_candidates:
        for key in card.requirement_group_keys:
            template_to_cards[key].add(card.name)

    pair_overlaps: list[TemplateOverlapPair] = []
    template_keys = sorted(template_to_cards.keys())
    for i, t1 in enumerate(template_keys):
        for t2 in template_keys[i+1:]:
            cards1, cards2 = template_to_cards[t1], template_to_cards[t2]
            shared = cards1 & cards2
            if shared:
                union_size = len(cards1 | cards2)
                pair_overlaps.append(TemplateOverlapPair(
                    template1_name=group_key_to_name.get(t1, t1),
                    template2_name=group_key_to_name.get(t2, t2),
                    shared_cards=sorted(shared),
                    overlap_count=len(shared),
                    jaccard_similarity=len(shared) / union_size,
                ))

    top_pairs = sorted(pair_overlaps, key=lambda p: -p.overlap_count)[:10]

    return CrossTemplateStats(
        multi_template_card_count=len(multi_template),
        max_templates_per_card=max(template_counts) if template_counts else 0,
        mean_templates_per_card=sum(template_counts) / len(template_counts) if template_counts else 0,
        cards_by_template_count=cards_by_count,
        top_versatile_cards=top_versatile,
        top_overlapping_pairs=top_pairs,
    )
```

### 3d: Integrate into `solve()` and `_solve_phase2()`

After computing requirement stats, call `_calculate_cross_template_stats()` and include in result.

---

## Step 4: Update Stats Output (`ilp_runner.py`)

In `write_utilization_stats()`, add new section:

```python
if result.cross_template_stats:
    cts = result.cross_template_stats
    stats["cross_template_overlap"] = {
        "summary": {
            "multi_template_card_count": cts.multi_template_card_count,
            "max_templates_per_card": cts.max_templates_per_card,
            "mean_templates_per_card": round(cts.mean_templates_per_card, 2),
            "cards_by_template_count": cts.cards_by_template_count,
        },
        "top_versatile_cards": [
            {
                "card": c.name,
                "template_count": c.template_count,
                "requirement_keys": sorted(c.requirement_group_keys),
            }
            for c in cts.top_versatile_cards
        ],
        "template_pair_overlaps": [
            {
                "template1": p.template1_name,
                "template2": p.template2_name,
                "shared_cards": p.shared_cards,
                "overlap_count": p.overlap_count,
                "jaccard_similarity": round(p.jaccard_similarity, 3),
            }
            for p in cts.top_overlapping_pairs
        ],
    }
```

---

## Step 5: Update ILP Runner (`ilp_runner.py`)

Update `run_ilp_optimization()` to pass candidate cards to optimizer:

```python
async def run_ilp_optimization(...):
    preprocessor = ComboPreprocessor()
    combo_data, candidate_cards = await preprocessor.preprocess_variants(variants)

    optimizer = ILPOptimizer(
        combos=combo_data,
        candidate_cards=candidate_cards,
        cube_size=cube_size,
        ...
    )
```

---

## Step 6: Add Unit Tests

Test cases in `tests/unit/test_ilp_optimizer.py`:

1. `test_candidate_card_template_count` - Verify `template_count` property
2. `test_candidate_card_is_multi_template` - Verify `is_multi_template` property
3. `test_cross_template_stats_basic` - End-to-end stats calculation
4. `test_template_pair_overlap_jaccard` - Jaccard similarity accuracy
5. `test_versatility_bonus_effect` - Verify bonus influences card selection

---

## Migration Notes

- `ComboPreprocessor.preprocess_variants()` return type changes from `tuple[list[ComboData], set[str]]` to `tuple[list[ComboData], dict[str, CandidateCard]]`
- Callers need to be updated (`ilp_runner.py`)
- The `all_cards` set is now derived from `candidate_cards.keys()`

## Expected Output Example

```json
{
  "cross_template_overlap": {
    "summary": {
      "multi_template_card_count": 12,
      "max_templates_per_card": 3,
      "mean_templates_per_card": 1.15,
      "cards_by_template_count": {"1": 280, "2": 10, "3": 2}
    },
    "top_versatile_cards": [
      {
        "card": "Kitchen Finks",
        "template_count": 3,
        "requirement_keys": ["scryfall:q=keyword:persist", "scryfall:q=c:g keyword:persist", "..."]
      }
    ],
    "template_pair_overlaps": [
      {
        "template1": "Persist Creature",
        "template2": "Creature with Persist or Undying",
        "shared_cards": ["Kitchen Finks", "Murderous Redcap"],
        "overlap_count": 2,
        "jaccard_similarity": 0.667
      }
    ]
  }
}
```
