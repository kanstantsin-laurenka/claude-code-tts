---
name: verify
description: Run lint, format-check, and the pytest suite before marking work done. Use after any code change to claude-code-tts (Python or config) and before opening a PR.
---

Run the three checks below in order. Stop on the first failure, report which step failed, and show the tail of the output so the user can fix it.

```bash
venv/bin/ruff check .
venv/bin/ruff format --check .
venv/bin/pytest tests/ -v
```

Notes:
- All three commands MUST use `venv/bin/...` — the venv is populated by `install.sh`. If `venv/` is missing, tell the user to run `./install.sh` first; do not silently fall back to system Python.
- If `ruff format --check` fails, suggest running `venv/bin/ruff format .` to fix.
- If `ruff check` fails with fixable issues, suggest `venv/bin/ruff check --fix .`.
- Do not skip pytest even if lint passes — CI runs pytest across 3.10–3.13 and will catch things local lint won't.
