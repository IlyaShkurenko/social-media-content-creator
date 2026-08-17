# 006-runway-generated-hook

- Created: `2026-08-17`
- Kind: `failed_runner_attempt`
- Scenario: `scenario.json`
- Hypothesis: Replacing only the hook source with a gen4.5 airport traveller visibly struggling with phone-based planning improves hook timeline alignment while preserving the exact product demo and CTA.

## Failure

The completed MP4 was passed to the local experiment runner relative to the
feedback-loop directory, while video inputs are intentionally resolved from
the repository root. Path validation rejected the nonexistent resolved file
before evaluation.

## Paid impact

This runner failure made no provider request and incurred no additional cost.
It only attempted to evaluate the already completed Runway artifact.

## Decision

`failed_before_evaluation`

The sequential experiment identity is retained and must not be reused. The
same hypothesis was evaluated with the corrected repository-relative path in
experiment 007.
