### Coding practices

1. Use native python typing (e.g. prefer `list` to `typing.List`, `str | None` to `typing.Optional[str]`).
2. Python module imports should ALWAYS be relative to the `mtg_combo_cube` directory, e.g. `from mtg_combo_cube.ilp.ilp_runner import run_ilp`.
3. Prefer using `task`s for typchecking, lint, and final validation (see `Taskfile.yml` for available tasks).
4. When not using a `task`, always use `uv` to execute python related operations, e.g. `uv run python`, `uv run pytest`, `uv add httpx`.

### Planning guidance

1. After a planning session for a signficant feature, before starting implementation, ask to save the plan to a `.md` file in the `docs/plans/` folder.