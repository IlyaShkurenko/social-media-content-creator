# Behavior-driven testing

MoneyPrinterTurbo uses classic Gherkin through `pytest-bdd` for a small set of externally meaningful behaviors. BDD complements unit, integration, and evaluation tests; it does not replace them.

## Scope

Good BDD candidates include advertising-pipeline decisions, provider fallback behavior, user-visible task states, paid-generation safeguards, and acceptance or rejection rules. Keep low-level helpers, rendering math, schema details, and subjective judgements such as “looks human-made” in ordinary tests or the versioned video-quality evaluator.

## Layout and command

- Features: `test/bdd/features/`
- Step definitions: `test/bdd/steps/`
- Stable command: `make test-bdd`

Each scenario name starts with the stable contract requirement it verifies, such as `[EVAL-1.2]`. Steps should call production behavior at a meaningful boundary rather than duplicate the implementation or assert a constant.

Use deterministic local fixtures by default. A BDD scenario must not call a paid provider or depend on credentials unless it is explicitly marked and separately authorized.

## Authoring sequence

1. Add or update the requirement in `docs/specs/`.
2. Write the smallest scenario that demonstrates the externally meaningful outcome.
3. Confirm the scenario fails for the intended missing or incorrect behavior.
4. Implement the behavior and run `make test-bdd`.
5. Run the relevant unit/integration suite as well.

Undefined steps and failed assertions are test failures. Do not add broad catch-all step expressions that could allow incomplete scenarios to pass.
