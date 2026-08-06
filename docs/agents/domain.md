# Domain Docs

How engineering skills should consume this repository’s domain documentation while exploring the codebase.

## Before exploring, read these

- `CONTEXT.md` at the repository root.
- `docs/adr/` for architectural decisions touching the area under examination.

If these files do not exist, proceed silently. Do not suggest creating them preemptively. The `/domain-modeling` skill creates and updates them when terminology or architectural decisions are resolved.

## File structure

ChangeScope uses a single-context layout:

    /
    ├── CONTEXT.md
    ├── docs/
    │   └── adr/
    └── src/

`CONTEXT.md` contains the project’s domain vocabulary and model. System-wide architectural decisions live under `docs/adr/`.

## Use the glossary’s vocabulary

When output names a domain concept—in an issue title, implementation proposal, hypothesis, report field, or test—use the term defined in `CONTEXT.md`. Do not drift toward synonyms that its glossary explicitly avoids.

If a required concept is absent, reconsider whether the term is being invented. If the gap is genuine, record it for `/domain-modeling`.

## Flag ADR conflicts

If proposed work contradicts an existing ADR, surface the conflict explicitly instead of silently overriding the decision.
