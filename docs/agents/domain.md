# Domain docs

Engineering skills read this repository's domain documentation before working in an affected area.

## Before exploring

- Read root `CONTEXT.md` when it exists.
- Read ADRs in `docs/adr/` that affect the work.
- Continue silently when these files do not exist. `/domain-modeling` creates them when the project settles terms or decisions.

## Layout

This is a single-context repository.

```text
/
|- CONTEXT.md
|- docs/
|  |- adr/
|  |  |- 0001-example-decision.md
|  |- agents/
|- app/
```

## Vocabulary

Use terms defined in `CONTEXT.md` in issue titles, tests, refactor proposals, and hypotheses. Do not substitute synonyms for a defined term. If a needed concept is absent, record the gap for `/domain-modeling` rather than inventing a competing name.

## ADR conflicts

When proposed work contradicts an ADR, state the conflict instead of silently replacing the decision.
