import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals import agentic_video_judge as judge  # noqa: E402
from app.services.creative.budget import IterationBudgetLedger  # noqa: E402


def response():
    return {"summary": "A flower opens.", "observations": [
        {"start_ms": 0, "end_ms": 900, "description": "Petals open."}],
        "checks": [{"requirement": "Show a flower opening", "status": "met",
                    "observation_indices": [0], "reason": "Visible opening."}],
        "issues": []}


class AgenticVideoTests(unittest.TestCase):
    def test_transport_preserves_processing_and_steps(self):
        """EVAL-9.2: the HTTP body, not merely a local dict, contains processing."""
        request = judge.build_request({"request": "A flower opens"}, "uri",
                                      "gemini-3.6-flash", "agentic")
        raw = {"steps": [{"type": "processing_call", "id": "x"},
                         {"type": "processing_result", "call_id": "x"}]}
        with patch.object(judge.httpx, "post", return_value=Mock(status_code=200,
                          json=Mock(return_value=raw))) as post:
            result = judge.InteractionsREST("test-key").create(**request)
        self.assertEqual(post.call_args.kwargs["json"], request)
        self.assertFalse(post.call_args.kwargs["follow_redirects"])
        self.assertTrue(judge.processing_trace(result["steps"])["verified"])

    def test_request_driven_prompt_and_identical_modes(self):
        """EVAL-9.1: no scenario constants; only processing differs."""
        contract = {"request": "Show a flower opening in stop motion", "audio": "silent"}
        a = judge.build_request(contract, "uri", "gemini-3.6-flash", "static")
        b = judge.build_request(contract, "uri", "gemini-3.6-flash", "agentic")
        self.assertIn("flower", a["input"][1]["text"])
        self.assertNotIn("phone", a["input"][1]["text"].lower())
        self.assertEqual(a["response_format"], b["response_format"])
        b["input"][0]["processing"] = "static"
        self.assertEqual(a, b)

    def test_invalid_timestamps_and_missing_evidence(self):
        """EVAL-9.1: response citations must reference actual in-range observations."""
        judge.validate_response(response(), 1000)
        invalid = response()
        invalid["observations"][0]["end_ms"] = 1001
        with self.assertRaises(ValueError):
            judge.validate_response(invalid, 1000)
        invalid = response()
        invalid["checks"][0]["observation_indices"] = [7]
        with self.assertRaises(ValueError):
            judge.validate_response(invalid, 1000)

    def test_agentic_trace_requires_linked_results(self):
        """EVAL-9.2: requesting agentic is not proof of actual navigation."""
        self.assertFalse(judge.processing_trace([])["verified"])
        self.assertFalse(judge.processing_trace([
            {"type": "processing_call", "id": "x"}])["verified"])
        trace = judge.processing_trace([
            {"type": "processing_call", "id": "x", "signature": "omit"},
            {"type": "processing_result", "call_id": "x"}])
        self.assertTrue(trace["verified"])
        self.assertNotIn("signature", str(trace))

    def test_usage_includes_reasoning_and_tool_input(self):
        """EVAL-9.3: navigation is not free or silently omitted."""
        cost = judge.usage_cost({"total_input_tokens": 100,
            "total_output_tokens": 20, "total_thought_tokens": 30,
            "total_tool_use_tokens": 50}, "gemini-3.6-flash")
        self.assertEqual(cost, 600)  # Conservative 1.5 input / 7.5 output rates.

    def test_replay_and_ambiguous_failure(self):
        """EVAL-9.3: exact replay is free, ambiguous retry is blocked."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "video.mp4"
            video.write_bytes(b"test-video")
            ledger = IterationBudgetLedger(root / "ledger.sqlite3",
                scope_id="test", cap_microusd=1000000)
            client = Mock()
            client.files.upload.return_value = Mock(uri="provider-uri", name="files/x",
                state=Mock(name="ACTIVE"))
            client.files.upload.return_value.state.name = "ACTIVE"
            client.interactions.create.return_value = {"id": "response-1",
                "status": "completed", "output_text": __import__("json").dumps(response()),
                "steps": [], "usage": {"total_input_tokens": 100,
                "total_output_tokens": 20, "total_thought_tokens": 0,
                "total_tool_use_tokens": 0}}
            kwargs = dict(client=client, ledger=ledger, video=video, duration_ms=1000,
                contract={"request": "Show a flower"}, model="gemini-3.6-flash",
                mode="static", operation_id="one", output=root / "one.json")
            first = judge.run_mode(**kwargs)
            self.assertFalse(first["agentic_verified"])
            self.assertIsNone(judge.compare_results([first])["accuracy"])
            other = copy.deepcopy(first)
            other["binding"].update(mode="agentic", contract_sha256="different")
            with self.assertRaises(ValueError):
                judge.compare_results([first, other])
            self.assertEqual(first, judge.run_mode(**kwargs))
            self.assertEqual(client.interactions.create.call_count, 1)
            changed = copy.copy(kwargs)
            changed["contract"] = {"request": "Something else"}
            with self.assertRaises(ValueError):
                judge.run_mode(**changed)
            kwargs.update(operation_id="two", output=root / "two.json")
            client.interactions.create.side_effect = TimeoutError("provider timeout")
            with self.assertRaises(RuntimeError):
                judge.run_mode(**kwargs)
            count = client.interactions.create.call_count
            with self.assertRaises((RuntimeError, ValueError)):
                judge.run_mode(**kwargs)
            self.assertEqual(client.interactions.create.call_count, count)
            with self.assertRaises((RuntimeError, ValueError)):
                judge.run_mode(**kwargs, retry_upload=True)
            self.assertEqual(client.interactions.create.call_count, count)
            kwargs.update(operation_id="three", output=root / "three.json")
            client.files.upload.side_effect = ConnectionError("upload did not complete")
            with self.assertRaises(RuntimeError):
                judge.run_mode(**kwargs)
            self.assertIsNone(ledger.find_operation("three"))
            client.files.upload.side_effect = None
            client.interactions.create.side_effect = None
            retried = judge.run_mode(**kwargs, retry_upload=True)
            self.assertEqual(len(retried["previous_attempts"]), 1)
            # Hash and metric drift must fail offline, without a provider call.
            tampered = copy.deepcopy(retried)
            tampered["assessment"]["summary"] = "Changed"
            kwargs["output"].write_text(json.dumps(tampered))
            with self.assertRaises(ValueError):
                judge.run_mode(**kwargs)
