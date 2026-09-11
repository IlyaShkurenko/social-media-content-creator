# Agentic video evaluator baseline

Evaluator: 1.1.0. Status: partial.

Same video, model and contract; only processing mode varies. Findings are diagnostic.
No creative winner or evaluator accuracy is established without human labels.

The retained video is artifacts/video.mp4. Inputs and complete sanitized evidence
are tracked in inputs.json and metrics.json, including processing failures.

## Result and learning

Static analysis completed. Agentic processing returned billable usage including
2790 tool-use tokens, but the response failed local validation with ValueError.
This version did not retain the returned text/status/processing steps on failure;
the precise reason cannot be reconstructed and agentic findings are unavailable.
No failed call was retried or silently treated as a successful assessment.

Recorded conservative accounting: static $0.018087, failed agentic $0.009674;
total $0.027761. The top-level comparison's total covers completed modes only;
the failed charge is retained explicitly under failures. These are estimates,
not provider invoices. No product change or quality improvement is claimed.

Next declared change: retain public response diagnostics even on validation
failure and increase the response-token budget from 4096 to 8192 for both modes
under evaluator 1.2.0. Experiment 019 tests this configuration. The suspicion
that response headroom contributed to the failure is not proven by this record.
