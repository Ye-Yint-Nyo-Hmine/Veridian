# `AGENT.md` — the format the `context/repo-map` brick loads

`AGENT.md` is a plain-Markdown guideline file that the agent treats as standing instructions for
working in a repository — coding conventions, commands to run, things to never touch, review
expectations. There is no schema and no required section: the whole file is handed to the model.

## The two locations

| location | role | who it is for |
|---|---|---|
| `$VERIDIAN_HOME/AGENT.md` (i.e. `~/.veridian/AGENT.md`) | **user guideline** | your personal defaults, applied in every workspace |
| `<workspace>/AGENT.md` | **workspace guideline** | this repository's rules, checked in with the code |

Both are optional. The brick loads whichever exist.

## Precedence

The **workspace `AGENT.md` wins on any conflict.** The brick returns both as retrieval chunks,
gives the workspace one the higher score, and prefixes each with a line stating its role, so the
model resolves a contradiction ("use tabs" vs. "use 4 spaces") in favour of the workspace file.
Non-conflicting guidance from both files applies together.

## How it reaches the model

The `context/repo-map` brick returns the guideline(s) as the first chunks of every
`context.retrieve` response (scores `0.99` workspace / `0.98` user, above the repository map and
all code chunks). The `orchestrator/autonomous` brick concatenates retrieved chunks into its
system prompt, so the guideline is in front of the model on every turn without the model having
to ask for it.

## Recommended shape (not enforced)

```markdown
# AGENT.md

## Commands
- test:   uv run pytest -q
- lint:   uv run ruff check .
- typecheck: uv run mypy src

## Conventions
- Match the style of the file you are editing.
- No new dependencies without asking.

## Do not touch
- `migrations/` — generated
- anything under `vendor/`

## Definition of done
- the test command passes
- `git diff` reviewed and minimal
```

Keep it short. Everything in it costs context on every turn.
