# Triage labels

This repo uses the five canonical triage labels to categorize work. The skills `triage`, `to-tickets`, `to-spec`, and `qa` expect these labels to exist.

| Label | Meaning |
|-------|---------|
| `needs-triage` | New issue; not yet reviewed |
| `needs-info` | Blocked waiting for more context from the reporter |
| `ready-for-agent` | Clear, actionable; agent can work on it |
| `ready-for-human` | Issue or PR needs human review (code review, design sign-off, etc.) |
| `wontfix` | Intentionally closed; won't be addressed |

If your repo already uses different label names (e.g. `bug:triage`), update the label strings above and the agent skills will apply your existing labels instead.
