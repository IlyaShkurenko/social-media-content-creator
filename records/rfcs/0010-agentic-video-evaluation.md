# RFC-0010: Agentic video evaluation

- Status: Accepted
- Date: 2026-09-11

## Decision

Add an opt-in Interactions API evaluator with `static` and `agentic` processing
and a comparison command. Both modes use the same model, prompt, input contract,
response schema and MP4. The contract is arbitrary JSON containing the user's
request, creative hypothesis, storyboard and material/audio/layout requirements.
No particular object, brand, scene type or advertising premise is required.

The evaluator describes observations with timestamps, assesses contract-derived
requirements and reports suspected continuity or audiovisual defects with
evidence. Deliberate edits and effects are interpreted against the contract.
Findings remain diagnostic and require calibration against human labels before
they can replace existing acceptance gates. Agreement is not evaluator accuracy.

Use the existing Gemini key and iteration ledger. Retain complete evidence,
processing call/result metadata, latency, usage, video/contract/prompt hashes
and operation identities. Complete evidence replays without paid work; incomplete
operations cannot silently retry. Comparison resumes completed modes.

Agentic navigation has variable token usage; report the local preflight amount
as a conservative allowance, not a provider-enforced price ceiling. Include tool
input and reasoning usage in accounting. Historical evaluator versions remain
unchanged; this evaluator owns version 1.2.0 and a separate diagnostic baseline.

## Transport validation

The first live SDK 2.11.0 attempt (measurement version 1.0.0, experiment 017)
silently dropped `processing` during typed serialization. Its two responses
therefore do not establish an agentic baseline. Version 1.1.0 uses the documented
REST Interactions endpoint with an unchanged JSON body and raw JSON response,
while keeping the existing SDK for file upload/deletion. Experiment 018 records
the corrected transport separately; linked processing steps are still required
to claim observed agentic navigation. No model, content or acceptance-policy
change is bundled with this transport fix. Experiment 018's agentic call returned
billable tool usage but failed response validation; its raw response was not
retained, so the precise failure is unknown. Version 1.2.0 preserves public
response text, status and processing metadata even on validation failure and
increases the response-token headroom to 8192 for both modes. Generation settings
are now hash-bound too. Experiment 019 tests this configuration; earlier metrics
remain unchanged and replayable where complete.

## References

- https://ai.google.dev/gemini-api/docs/video-understanding
- https://ai.google.dev/gemini-api/docs/structured-output
- `docs/specs/video-quality-feedback-loop.md`
