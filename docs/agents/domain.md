# Domain docs: single-context layout

This repo uses a **single-context** domain layout: one `CONTEXT.md` file at the repo root, with ADRs under `docs/adr/`.

## Files

- **`CONTEXT.md`** (repo root) — overview of the domain, ubiquitous language, key concepts
- **`docs/adr/`** — Architecture Decision Records, one file per decision (e.g. `OOXX-title.md`)

## How agent skills use these files

The `triage`, `to-spec`, and `qa` skills read `CONTEXT.md` to understand the domain before analyzing issues and writing specs. They use ADRs to justify design decisions.

**For consumers (you):**

1. Keep `CONTEXT.md` lightweight — ~500 words covering *what* the project does, key abstractions, and vocabulary
2. Use ADRs to record *why* architectural decisions were made (e.g. "why did we pick this library?", "why is the auth model structured this way?")
3. Link between files freely — `CONTEXT.md` can reference ADRs, ADRs can cross-link

The agent skills will follow these links and build a richer mental model of the codebase.

## Getting started

If `CONTEXT.md` doesn't exist yet:

1. Spend 15 minutes writing one (domain overview, key terms)
2. Commit it
3. When agent skills run, they'll read it and make better decisions

For ADRs, use the format:
```
# OOXX — [Title]

## Status
Accepted / Pending / Superseded

## Context
Why did we need to decide this?

## Decision
What did we decide?

## Rationale
Why this over the alternatives?

## Consequences
What follows from this decision?
```

Then save as `docs/adr/OOXX-title.md`.
