# Plans and Design Documents

Working documents written before or during a piece of work. They record intent, measurements
and decisions at the time and are not kept in step with the code. For the system as it is
today, see [../architecture.md](../architecture.md).

| Document | Written | What it covers |
|----------|---------|----------------|
| [combo-grouping-plan.md](combo-grouping-plan.md) | October 2026 | Playability plan 1: count distinct Spellbook combos rather than variants in the Phase 1 objective, the combo window and reporting; measurements of variant duplication. |
| [archetype-support-plan.md](archetype-support-plan.md) | October 2026 | Playability plan 2: minimum completed combos per two-color pair and mono color, a cap on three-plus-color combos, and the pattern for adding a Phase 2 hard constraint. |
| [card-mix-plan.md](card-mix-plan.md) | October 2026 | Playability plan 3: Scryfall card attributes (type, mana value) and share caps on multicolor, colorless, expensive and creature cards. |
| [combo-variety-plan.md](combo-variety-plan.md) | October 2026 | Playability plan 4: outcome categories from Spellbook `produces` features with minimums per category, and a popularity weight shared by both phases. |
| [payoff-support-plan.md](payoff-support-plan.md) | October 2026 | Playability plan 5: a payoff table per outcome category (Scryfall queries plus inference from bundled variants), payoff-only cards added to the candidate pool, and a Phase 2 floor on outlets for mana, storm, tokens, life and counters; the first use of reserved slots for non-combo cards. |
| [color-balance-plan.md](color-balance-plan.md) | October 2026 | A color balance constraint for Phase 2 and the move to a 20,000-variant default: decisions, measurements, steps and open questions. |
| [ilp-improvement-plan.md](ilp-improvement-plan.md) | October 2026 | The staged rework of the ILP optimizer: goals, per-stage results, benchmark tables, decision log and open follow-ups. |
| [ilp-performance-investigation.md](ilp-performance-investigation.md) | December 2025 | Profiling that identified Phase 2 as the bottleneck, and the warm start, gap limit and minmax objective added in response. |
| [multi_objective_design_doc.md](multi_objective_design_doc.md) | December 2025 | The original two-phase design: card utilization, why variance needs linearizing, and the MAD formulation for Phase 2. |
| [ilp_design_doc.md](ilp_design_doc.md) | December 2025 | The original ILP design: problem statement, decision variables, Phase 1 objective and constraints, template requirements, solver choice. |

## Adding a plan

Save the plan for a significant feature here as a `.md` file before starting implementation,
and add a row to the table above.
