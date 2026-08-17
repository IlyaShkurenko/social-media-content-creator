# Specification process

This project uses specifications to stabilize non-trivial product behavior before implementation while keeping small, local changes lightweight.

## When a specification is required

A feature specification is required when a change introduces or materially alters externally visible behavior, a provider contract, a multi-stage pipeline, durable data, safety or cost controls, or acceptance criteria shared by several components. Routine refactors, formatting, dependency maintenance, and local bug fixes with an obvious contract do not require a new specification.

An RFC is required before implementation when the proposal is architectural or cross-cutting: for example, adding Runway as a generation provider, introducing a storyboard intermediate representation, changing task orchestration, or adding a new persistence boundary. RFCs are historical decision records and live under `records/rfcs/NNNN-<name>.md`. That directory is created with the first real RFC rather than populated with placeholders.

## Artifacts and identifiers

- Current feature contracts live in `docs/specs/<feature>.md`.
- Executable specifications remain in the existing `test/` tree.
- Selected high-value behavior scenarios live in `test/bdd/features/` with steps in `test/bdd/steps/`.
- Stable requirement IDs are domain-oriented: `ADPIPE-*`, `RUNWAY-*`, `STORY-*`, `BRAND-*`, and `EVAL-*`.
- Tests and BDD scenario names reference the exact requirement IDs they verify.

## Delivery flow

1. Retrieve relevant current context from `docs/` and inspect the implementation.
2. Write an RFC when the change crosses architectural or subsystem boundaries.
3. Define normative, testable requirements in the feature specification.
4. Add executable tests; use BDD only for behavior that benefits from shared domain language.
5. Implement the smallest change satisfying the contract.
6. Run focused tests, the full relevant suite, and documentation/spec drift checks.
7. Update the canonical context documents when architecture or durable behavior changed.

Subjective video quality is evaluated by the measurable experiment loop, not expressed as vague Gherkin. BDD is appropriate for deterministic decisions around that loop, such as whether a candidate may be accepted automatically.

The existing mixed-media/Runway architecture is governed by RFC-0001. Future architectural changes—such as a new generation provider, orchestration model, or persistence boundary—must update or add an RFC before their dependent feature contracts and implementation. Routine experiment hypotheses remain in their staged experiment records and do not require an RFC.
