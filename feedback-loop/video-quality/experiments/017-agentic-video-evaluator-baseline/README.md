# Agentic video evaluator baseline

Evaluator: 1.0.0. Responses complete; **agentic baseline not established**.

Planned comparison: same video, model and contract; only processing mode varies.
Observed failure: SDK 2.11.0's VideoContent serializer drops the `processing`
field before sending the HTTP request. Both requested modes produced ordinary
video analysis, with zero tool-use tokens and no processing call/result steps.
Do not interpret the response differences as an agentic effect. Findings are diagnostic.
No creative winner or evaluator accuracy is established without human labels.

The retained video is artifacts/video.mp4. Inputs and complete sanitized evidence
are tracked in inputs.json and metrics.json, including processing failures.

The pre-inference upload initially failed under the network sandbox. Its failure
is retained under the static evidence's previous_attempts; no inference charge
was recorded for that upload. Explicit upload retry succeeded. The two completed
SDK calls were conservatively accounted at $0.044612, not a provider invoice.

Learning: validate the serialized HTTP body, not just the SDK input dictionary.
Retain this failed measurement attempt and establish the REST transport baseline
under measurement version 1.1.0 in experiment 018. No product-video change or
quality improvement is claimed.
