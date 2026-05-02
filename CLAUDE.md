- **Always** use type hints for function/method parameters and return types.
- Use latest type hinting format (Python 3.12). For example:
  - Use `list[str]` instead of `List[str]`.
  - Use `dict[str, int]` instead of `Dict[str, int]`.
  - Use `tuple[int, ...]` instead of `Tuple[int, ...]`.
  - Use `set[str]` instead of `Set[str]`.
- Prefer using `pathlib` module over `os.path` for file and path manipulations.
- Prefer using `pydantic.BaseModel` over `dataclasses.dataclass` for data validation and serialization.
- Use `logger.exception` for logging exceptions with stack traces instead of `logger.error(f"Error occured: {e}")`
- Use `logger.xxx()` for logging instead of `print()`
- Do not use emojis or any special characters in code comments or log messages.
- Use f-strings for string formatting instead of `str.format()` or concatenation.
- Do not write docsctrings
- Prefer defining functions over classes with methods when state is not needed.
- Use `pydantic_settings.BaseSettings` for configuration settings.
- Always run `ruff format <modified files>` and `ruff check <modified files>` (add `--fix` to `ruff check` to auto-fix some of the issues).
- Use semantic git commit messages (e.g., `feat: add new feature`, `fix: correct a bug`, `docs: update documentation`).

## Commits

- Keep commits small and focused: one concern per commit. Reviewers should be able to read the diff and say exactly why it exists.
- The commit message must describe what the diff actually changes. No scope creep: if you find an unrelated fix while doing the task, split it into its own commit.
- Prefer a linear history. When you need to fix a prior commit in the current branch, amend or rebase rather than appending a fixup commit at the tip. New work on top is fine; leftover "address review" or "fix typo" tail commits are not.
- Prefer runtime-configurable parameters over hard-coded constants. Anything a user or operator might want to tune (thresholds, timeouts, delays, model ids, toggles) goes through `Config` / `pydantic_settings.BaseSettings` so it can change without a rebuild.
- Single source of truth for tool configs. Don't embed `[tool.ruff]` / `[tool.basedpyright]` in `pyproject.toml` if `ruff.toml` / `pyrightconfig.json` already exist — pick one.
- Comments: prefer zero. When you do write one, it must explain WHY (non-obvious constraint, workaround for a specific bug, surprising invariant). Comments that restate what the next line does are noise. Use `TODO:` / `NOTE:` / `HACK:` prefixes for intentional signals.
- Unverified correctness fixes must be labelled. If you're patching a race or edge case you haven't reproduced, add a `# NOTE (unverified hypothesis): ...` inline rather than presenting it as a confirmed fix.

## Working with agents / subagents

- Verify agent findings before acting on them. Dead-code / dead-UI claims from a review agent are hypotheses — grep for actual references before deleting.
- Never revert uncommitted working-tree changes without asking. If a dirty diff appears unexpectedly, it is more likely the user's in-progress work than a stray edit. Ask first.
- Use multiple agents in parallel only for independent research; apply findings serially to avoid conflicts.
