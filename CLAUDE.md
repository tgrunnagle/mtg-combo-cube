### Coding practices

1. Use native python typing (e.g. prefer `list` to `typing.List`, `str | None` to `typing.Optional[str]`).
2. Python module imports should ALWAYS be relative to the `mtg_combo_cube` directory, e.g. `from mtg_combo_cube.greedy.greedy_runner import GreedyRunner`.
3. Prefer using `task`s for validation.
4. Always use `uv`, e.g. `uv run python`, `uv run pytest`, `uv add httpx`.