from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest import mock


LOOP_ROOT = Path(__file__).resolve().parents[2]
FINALIZER_PATH = LOOP_ROOT / "scripts" / "finalize_campaign.py"
INVARIANT_SCENARIO_PATH = (
    LOOP_ROOT / "evals" / "dataset" / "mixed-media-stock-baseline-001.json"
)


def _load_finalizer() -> ModuleType:
    scripts_dir = str(FINALIZER_PATH.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    module_spec = importlib.util.spec_from_file_location(
        "video_quality_finalize_campaign_tests",
        FINALIZER_PATH,
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"cannot load campaign finalizer: {FINALIZER_PATH}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


FINALIZER = _load_finalizer()


class _Payload:
    def __init__(self, value: dict) -> None:
        self.value = value

    def model_dump(self, *, mode: str) -> dict:
        if mode != "json":
            raise AssertionError("the finalizer must hash the JSON plan payload")
        return self.value


def _plan(candidate_id: str = "map-fragmentation") -> SimpleNamespace:
    hypothesis = "Scattered planning makes travel preparation feel overwhelming."
    concept = {
        "concept_id": candidate_id,
        "hypothesis": hypothesis,
        "audience_problem": "Planning is fragmented across too many tools.",
        "target_emotion": "overwhelm followed by relief",
        "emotional_arc": "Tension becomes relief and control.",
        "hook_setting": "A table covered in maps and notes.",
        "hook_camera": "A quick overhead push-in.",
        "hook_voiceover": "Trip planning should not feel this scattered.",
        "hook_voice_delivery": "A tired, matter-of-fact person stating the obvious.",
        "mascot_line": "tict sorts the chaos for you.",
        "mascot_pose": "excited",
        "hook_beats": [
            {
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "visible_action": "Hands rearrange conflicting notes on a map.",
                "expected_evidence": ["scattered_notes_visible"],
            }
        ],
        "product_bridge": "The map resolves into the exact product plan.",
        "quality_criteria": ["The problem is clear without audio."],
    }
    storyboard = {
        "storyboard_id": f"storyboard-{candidate_id}",
        "content_language": "en-US",
        "aspect_ratio": "9:16",
        "target_duration_seconds": 15.0,
        "hypothesis": hypothesis,
        "scenes": [
            {
                "scene_id": "hook",
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "purpose": "hook",
                "visual_intent": "Hands rearrange conflicting notes on a map.",
                "voiceover": "Trip planning should not feel this scattered.",
                "onscreen_text": "Too many plans?",
                "expected_evidence": ["scattered_notes_visible"],
            },
            {
                "scene_id": "product-demo",
                "start_seconds": 5.0,
                "end_seconds": 11.0,
                "purpose": "product_demo",
                "visual_intent": "Show the exact approved product plan.",
                "voiceover": "Bring it together with tict.",
                "onscreen_text": "One clear trip.",
                "expected_evidence": ["approved_tict_ui_visible"],
            },
            {
                "scene_id": "cta",
                "start_seconds": 11.0,
                "end_seconds": 15.0,
                "purpose": "cta",
                "visual_intent": "Show one clear CTA.",
                "voiceover": "Create your trip.",
                "onscreen_text": "Create your trip",
                "expected_evidence": ["single_cta_visible"],
            },
        ],
    }
    candidate = SimpleNamespace(
        candidate_id=candidate_id,
        concept_id=candidate_id,
        concept=_Payload(concept),
        storyboard=_Payload(storyboard),
    )
    return SimpleNamespace(candidates=[candidate])


def _scenario_document() -> dict:
    return {
        "source_path": str(INVARIANT_SCENARIO_PATH.relative_to(LOOP_ROOT)),
        "source_sha256": FINALIZER._sha256(INVARIANT_SCENARIO_PATH),
        "payload": json.loads(INVARIANT_SCENARIO_PATH.read_text(encoding="utf-8")),
    }


def _semantic_assessment(status: str, timestamp_ms: int = 1_000) -> dict:
    return {
        "status": status,
        "evidence": (
            []
            if status == "unverifiable"
            else [
                {
                    "timestamp_ms": timestamp_ms,
                    "observation": "Visible candidate evidence.",
                }
            ]
        ),
        "reason": "The timestamped observation supports this status.",
    }


def _candidate_response() -> dict:
    return {
        "observed_hook_summary": "Hands rearrange notes on a map.",
        "observed_bridge_summary": "The map cuts into the exact product card.",
        "hypothesis_match": _semantic_assessment("met"),
        "target_emotion_strength": _semantic_assessment("partially_met"),
        "first_two_seconds_hook_clarity": _semantic_assessment("met"),
        "hook_to_product_bridge_coherence": _semantic_assessment(
            "partially_met", 5_100
        ),
        "storyboard_action_alignment": _semantic_assessment("not_met"),
    }


def _candidate_document(
    candidate_id: str,
    video_sha256: str,
    *,
    plan: SimpleNamespace | None = None,
) -> dict:
    response = _candidate_response()
    binding = FINALIZER._plan_candidate_bindings(plan or _plan(candidate_id))[
        candidate_id
    ]
    return {
        "source_path": f"evidence/{candidate_id}-semantic.json",
        "source_sha256": "a" * 64,
        "payload": {
            "schema_version": FINALIZER.CANDIDATE_EVIDENCE_SCHEMA_VERSION,
            "evaluator_version": FINALIZER.CANDIDATE_EVALUATOR_VERSION,
            "observation_mode": FINALIZER.CANDIDATE_OBSERVATION_MODE,
            "status": "complete",
            "concept_id": candidate_id,
            "concept_sha256": binding["concept_sha256"],
            "storyboard_sha256": binding["storyboard_sha256"],
            "contract_sha256": binding["contract_sha256"],
            "prompt_sha256": "c" * 64,
            "response_schema_sha256": "d" * 64,
            "video_sha256": video_sha256,
            "video_duration_ms": 15_000,
            "response": response,
            "dimension_scores": FINALIZER.map_dimension_statuses(response),
            "provider": {"response_id": "semantic-response"},
        },
    }


def _invariant_assessment(status: str, scene: str, timestamp_ms: int) -> dict:
    return {
        "status": status,
        "evidence": (
            []
            if status == "unverifiable"
            else [
                {
                    "scene_purpose": scene,
                    "timestamp_ms": timestamp_ms,
                    "observation": "Visible shared downstream evidence.",
                }
            ]
        ),
        "reason": "The shared downstream evidence supports this status.",
    }


def _paired_invariant(
    status_a: str,
    status_b: str,
    scene: str,
    timestamp_ms: int,
) -> dict:
    return {
        "video_a": _invariant_assessment(status_a, scene, timestamp_ms),
        "video_b": _invariant_assessment(status_b, scene, timestamp_ms),
    }


def _invariant_response(*, candidate_label: str, null_cta: bool = False) -> dict:
    candidate_is_a = candidate_label == "A"

    def statuses(candidate: str, baseline: str) -> tuple[str, str]:
        return (candidate, baseline) if candidate_is_a else (baseline, candidate)

    audiovisual = statuses("met", "partially_met")
    brand = statuses("partially_met", "met")
    cta = statuses("unverifiable" if null_cta else "met", "partially_met")
    transition = statuses("met", "partially_met")
    finish = statuses("met", "partially_met")
    return {
        "winner": candidate_label,
        "winner_reason": "The candidate has clearer downstream evidence.",
        "criteria": {
            "transition_mechanics": _paired_invariant(
                *transition, "product_demo", 5_100
            ),
            "audiovisual_correctness": _paired_invariant(
                *audiovisual, "product_demo", 7_000
            ),
            "product_brand_fidelity": _paired_invariant(
                *brand, "product_demo", 9_000
            ),
            "cta_clarity": _paired_invariant(*cta, "cta", 12_000),
            "professional_finish": _paired_invariant(*finish, "cta", 14_000),
        },
        "scene_evidence": [
            {
                "video_label": label,
                "scene_purpose": scene,
                "timestamp_ms": timestamp_ms,
                "observation": f"Video {label} visibly shows {scene}.",
            }
            for label in ("A", "B")
            for scene, timestamp_ms in (("product_demo", 8_000), ("cta", 13_000))
        ],
    }


def _invariant_document(
    candidate_sha256: str,
    *,
    null_cta_in_second_pass: bool = False,
    scenario_document: dict | None = None,
) -> dict:
    scenario_document = scenario_document or _scenario_document()
    scenario = scenario_document["payload"]
    shared_contract = FINALIZER.build_shared_invariant_contract(scenario)
    baseline_sha256 = "e" * 64
    orders = [
        {"A": baseline_sha256, "B": candidate_sha256},
        {"A": candidate_sha256, "B": baseline_sha256},
    ]
    passes = []
    for index, order in enumerate(orders):
        candidate_label = "B" if index == 0 else "A"
        response = _invariant_response(
            candidate_label=candidate_label,
            null_cta=null_cta_in_second_pass and index == 1,
        )
        passes.append(
            {
                "pass_id": f"pass-{index + 1}",
                "order": order,
                "response": response,
                "criterion_scores": FINALIZER.map_invariant_statuses(response),
                "provider": {"response_id": f"invariant-response-{index + 1}"},
            }
        )
    return {
        "source_path": "evidence/map-invariant.json",
        "source_sha256": "f" * 64,
        "payload": {
            "schema_version": FINALIZER.INVARIANT_EVIDENCE_SCHEMA_VERSION,
            "evaluator_version": FINALIZER.INVARIANT_EVALUATOR_VERSION,
            "observation_mode": FINALIZER.INVARIANT_OBSERVATION_MODE,
            "status": "complete",
            "scenario_id": scenario["id"],
            "scenario_sha256": scenario_document["source_sha256"],
            "shared_contract_sha256": FINALIZER.invariant_sha256_json(
                shared_contract
            ),
            "prompt_sha256": "2" * 64,
            "response_schema_sha256": "3" * 64,
            "baseline_sha256": baseline_sha256,
            "candidate_sha256": candidate_sha256,
            "passes": passes,
        },
    }


def _record(candidate_id: str = "map-fragmentation") -> dict:
    return {
        "candidate_id": candidate_id,
        "eligible": True,
        "screening": {
            "temporal_status": "complete",
            "temporal_evidence": f"temporal/{candidate_id}.json",
        },
    }


class CampaignScorecardIntegrityTests(unittest.TestCase):
    def test_render_completion_does_not_invent_semantic_scores(self) -> None:
        scorecard = FINALIZER._build_scorecards(
            [
                {
                    "candidate_id": "map-fragmentation",
                    "eligible": True,
                    "final_video_sha256": "c" * 64,
                    "subtitle_safe_area_pass": True,
                }
            ]
        )[0]

        self.assertEqual(
            scorecard["dimensions"]["temporal_eligibility"],
            {"value": 1.0, "unavailable_reason": None},
        )
        for name in (
            "audiovisual_correctness",
            "product_brand_fidelity",
            "cta_clarity",
        ):
            with self.subTest(name=name):
                dimension = scorecard["dimensions"][name]
                self.assertIsNone(dimension["value"])
                self.assertTrue(dimension["unavailable_reason"])

    def test_versioned_evidence_populates_own_contract_and_invariant_dimensions(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory).resolve()
            plan = _plan()
            scenario = _scenario_document()
            final_video = output_dir / "renders" / "map-fragmentation" / "final.mp4"
            final_video.parent.mkdir(parents=True)
            final_video.write_bytes(b"exact-final-video")
            video_sha256 = FINALIZER._sha256(final_video)

            scorecard = FINALIZER._build_scorecards(
                [_record()],
                plan=plan,
                output_dir=output_dir,
                rendered=[
                    {
                        "candidate_id": "map-fragmentation",
                        "video_path": str(final_video),
                    }
                ],
                candidate_evidence=[
                    _candidate_document(
                        "map-fragmentation", video_sha256, plan=plan
                    )
                ],
                invariant_evidence=[
                    _invariant_document(video_sha256, scenario_document=scenario)
                ],
                invariant_scenario=scenario,
            )[0]

        dimensions = scorecard["dimensions"]
        self.assertEqual(dimensions["hypothesis_match"]["value"], 1.0)
        self.assertEqual(dimensions["target_emotion_strength"]["value"], 0.5)
        self.assertEqual(dimensions["storyboard_action_alignment"]["value"], 0.0)
        self.assertEqual(dimensions["audiovisual_correctness"]["value"], 1.0)
        self.assertEqual(dimensions["product_brand_fidelity"]["value"], 0.5)
        self.assertEqual(dimensions["cta_clarity"]["value"], 1.0)
        self.assertEqual(scorecard["scorecard_evaluator_version"], "1.0.0")
        self.assertEqual(
            scorecard["evidence"]["candidate_semantic"]["video_sha256"],
            video_sha256,
        )
        self.assertEqual(
            scorecard["evidence"]["shared_invariant"]["pass_values"],
            [
                {
                    "audiovisual_correctness": 1.0,
                    "product_brand_fidelity": 0.5,
                    "cta_clarity": 1.0,
                },
                {
                    "audiovisual_correctness": 1.0,
                    "product_brand_fidelity": 0.5,
                    "cta_clarity": 1.0,
                },
            ],
        )

    def test_invariant_dimension_stays_null_when_one_pass_is_unverifiable(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory).resolve()
            scenario = _scenario_document()
            final_video = output_dir / "renders" / "map" / "final.mp4"
            final_video.parent.mkdir(parents=True)
            final_video.write_bytes(b"exact-final-video")
            video_sha256 = FINALIZER._sha256(final_video)

            scorecard = FINALIZER._build_scorecards(
                [_record("map")],
                output_dir=output_dir,
                rendered=[{"candidate_id": "map", "video_path": str(final_video)}],
                invariant_evidence=[
                    _invariant_document(
                        video_sha256,
                        null_cta_in_second_pass=True,
                        scenario_document=scenario,
                    )
                ],
                invariant_scenario=scenario,
            )[0]

        self.assertIsNone(scorecard["dimensions"]["cta_clarity"]["value"])
        self.assertTrue(
            scorecard["dimensions"]["cta_clarity"]["unavailable_reason"]
        )
        pass_values = scorecard["evidence"]["shared_invariant"]["pass_values"]
        self.assertEqual(pass_values[0]["cta_clarity"], 1.0)
        self.assertIsNone(pass_values[1]["cta_clarity"])

    def test_candidate_evidence_must_match_the_exact_final_mp4(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory).resolve()
            plan = _plan("map")
            final_video = output_dir / "renders" / "map" / "final.mp4"
            final_video.parent.mkdir(parents=True)
            final_video.write_bytes(b"exact-final-video")

            with self.assertRaisesRegex(ValueError, "final MP4 hash does not match"):
                FINALIZER._build_scorecards(
                    [_record("map")],
                    plan=plan,
                    output_dir=output_dir,
                    rendered=[
                        {"candidate_id": "map", "video_path": str(final_video)}
                    ],
                    candidate_evidence=[
                        _candidate_document("map", "0" * 64, plan=plan)
                    ],
                )

    def test_invariant_evidence_must_match_the_exact_final_mp4(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory).resolve()
            scenario = _scenario_document()
            final_video = output_dir / "renders" / "map" / "final.mp4"
            final_video.parent.mkdir(parents=True)
            final_video.write_bytes(b"exact-final-video")

            with self.assertRaisesRegex(ValueError, "candidate SHA"):
                FINALIZER._build_scorecards(
                    [_record("map")],
                    output_dir=output_dir,
                    rendered=[
                        {"candidate_id": "map", "video_path": str(final_video)}
                    ],
                    invariant_evidence=[_invariant_document("0" * 64)],
                    invariant_scenario=scenario,
                )

    def test_candidate_evidence_header_and_identity_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory).resolve()
            plan = _plan("map")
            final_video = output_dir / "renders" / "map" / "final.mp4"
            final_video.parent.mkdir(parents=True)
            final_video.write_bytes(b"exact-final-video")
            video_sha256 = FINALIZER._sha256(final_video)
            rendered = [{"candidate_id": "map", "video_path": str(final_video)}]
            mutations = (
                ("schema_version", 999, "schema version"),
                ("evaluator_version", "999.0.0", "evaluator version"),
                ("status", "partial", "complete status"),
                ("concept_id", "unknown", "unknown candidate"),
            )

            for field, value, error in mutations:
                with self.subTest(field=field):
                    document = _candidate_document("map", video_sha256, plan=plan)
                    document["payload"][field] = value
                    with self.assertRaisesRegex(ValueError, error):
                        FINALIZER._build_scorecards(
                            [_record("map")],
                            plan=plan,
                            output_dir=output_dir,
                            rendered=rendered,
                            candidate_evidence=[document],
                        )

    def test_invariant_evidence_header_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory).resolve()
            scenario = _scenario_document()
            final_video = output_dir / "renders" / "map" / "final.mp4"
            final_video.parent.mkdir(parents=True)
            final_video.write_bytes(b"exact-final-video")
            video_sha256 = FINALIZER._sha256(final_video)
            rendered = [{"candidate_id": "map", "video_path": str(final_video)}]
            mutations = (
                ("schema_version", 999, "schema version"),
                ("evaluator_version", "999.0.0", "evaluator version"),
                ("status", "partial", "complete status"),
            )

            for field, value, error in mutations:
                with self.subTest(field=field):
                    document = _invariant_document(
                        video_sha256, scenario_document=scenario
                    )
                    document["payload"][field] = value
                    with self.assertRaisesRegex(ValueError, error):
                        FINALIZER._build_scorecards(
                            [_record("map")],
                            output_dir=output_dir,
                            rendered=rendered,
                            invariant_evidence=[document],
                            invariant_scenario=scenario,
                        )

    def test_candidate_evidence_must_match_exact_plan_contract_hashes(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory).resolve()
            plan = _plan("map")
            final_video = output_dir / "renders" / "map" / "final.mp4"
            final_video.parent.mkdir(parents=True)
            final_video.write_bytes(b"exact-final-video")
            video_sha256 = FINALIZER._sha256(final_video)
            rendered = [{"candidate_id": "map", "video_path": str(final_video)}]

            for field in (
                "concept_sha256",
                "storyboard_sha256",
                "contract_sha256",
            ):
                with self.subTest(field=field):
                    document = _candidate_document("map", video_sha256, plan=plan)
                    document["payload"][field] = "0" * 64
                    with self.assertRaisesRegex(ValueError, f"plan {field}"):
                        FINALIZER._build_scorecards(
                            [_record("map")],
                            plan=plan,
                            output_dir=output_dir,
                            rendered=rendered,
                            candidate_evidence=[document],
                        )

    def test_invariant_evidence_must_match_exact_scenario_contract(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory).resolve()
            scenario = _scenario_document()
            final_video = output_dir / "renders" / "map" / "final.mp4"
            final_video.parent.mkdir(parents=True)
            final_video.write_bytes(b"exact-final-video")
            video_sha256 = FINALIZER._sha256(final_video)
            rendered = [{"candidate_id": "map", "video_path": str(final_video)}]

            for field in ("scenario_sha256", "shared_contract_sha256"):
                with self.subTest(field=field):
                    document = _invariant_document(
                        video_sha256, scenario_document=scenario
                    )
                    document["payload"][field] = "0" * 64
                    with self.assertRaisesRegex(ValueError, field):
                        FINALIZER._build_scorecards(
                            [_record("map")],
                            output_dir=output_dir,
                            rendered=rendered,
                            invariant_evidence=[document],
                            invariant_scenario=scenario,
                        )

    def test_tampered_candidate_timestamp_is_revalidated(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory).resolve()
            plan = _plan("map")
            final_video = output_dir / "renders" / "map" / "final.mp4"
            final_video.parent.mkdir(parents=True)
            final_video.write_bytes(b"exact-final-video")
            video_sha256 = FINALIZER._sha256(final_video)
            document = _candidate_document("map", video_sha256, plan=plan)
            document["payload"]["response"]["hypothesis_match"]["evidence"][0][
                "timestamp_ms"
            ] = 99_000

            with self.assertRaisesRegex(ValueError, "outside the video"):
                FINALIZER._build_scorecards(
                    [_record("map")],
                    plan=plan,
                    output_dir=output_dir,
                    rendered=[
                        {"candidate_id": "map", "video_path": str(final_video)}
                    ],
                    candidate_evidence=[document],
                )

    def test_tampered_invariant_timestamp_is_revalidated(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory).resolve()
            scenario = _scenario_document()
            final_video = output_dir / "renders" / "map" / "final.mp4"
            final_video.parent.mkdir(parents=True)
            final_video.write_bytes(b"exact-final-video")
            video_sha256 = FINALIZER._sha256(final_video)
            document = _invariant_document(
                video_sha256, scenario_document=scenario
            )
            document["payload"]["passes"][0]["response"]["criteria"][
                "product_brand_fidelity"
            ]["video_b"]["evidence"][0]["timestamp_ms"] = 99_000

            with self.assertRaisesRegex(ValueError, "outside its scene"):
                FINALIZER._build_scorecards(
                    [_record("map")],
                    output_dir=output_dir,
                    rendered=[
                        {"candidate_id": "map", "video_path": str(final_video)}
                    ],
                    invariant_evidence=[document],
                    invariant_scenario=scenario,
                )

    def test_repeated_evidence_cli_arguments_are_retained(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            [
                "finalize_campaign.py",
                "--output",
                "campaign",
                "--review",
                "review.json",
                "--candidate-evidence",
                "candidate-a.json",
                "--candidate-evidence",
                "candidate-b.json",
                "--invariant-evidence",
                "invariant-a.json",
                "--invariant-evidence",
                "invariant-b.json",
                "--budget-database",
                ".state/custom-budget.sqlite3",
            ],
        ):
            args = FINALIZER._parse_args()

        self.assertEqual(
            args.candidate_evidence,
            ["candidate-a.json", "candidate-b.json"],
        )
        self.assertEqual(
            args.invariant_evidence,
            ["invariant-a.json", "invariant-b.json"],
        )
        self.assertEqual(
            args.budget_database,
            ".state/custom-budget.sqlite3",
        )

    def test_bad_evidence_cannot_mutate_or_render_campaign(self) -> None:
        with TemporaryDirectory(dir=LOOP_ROOT) as directory:
            output_dir = Path(directory).resolve()
            plan = _plan("map")
            plan_path = output_dir / "campaign-plan.json"
            pool_path = output_dir / "candidate-pool.json"
            summary_path = output_dir / "campaign-summary.json"
            review_path = output_dir / "campaign-review.json"
            evidence_path = output_dir / "candidate-evidence.json"
            state_path = output_dir / "map.state.json"
            applied_review_path = output_dir / "campaign-review-applied.json"
            scorecards_path = output_dir / "candidate-scorecards.json"
            final_video = output_dir / "renders" / "map" / "final.mp4"
            subtitle = output_dir / "renders" / "map" / "subtitles.srt"
            final_video.parent.mkdir(parents=True)
            final_video.write_bytes(b"exact-final-video")
            subtitle.write_text("retained subtitle", encoding="utf-8")
            plan_path.write_text("{}\n", encoding="utf-8")
            pool_path.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "candidate_id": "map",
                                "eligible": True,
                                "screening": {
                                    "candidate_id": "map",
                                    "temporal_events": [],
                                    "temporal_consistency_pass": True,
                                },
                            }
                        ],
                        "eligible_candidate_ids": ["map"],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            summary_path.write_text(
                json.dumps(
                    {
                        "rendered_candidates": [
                            {
                                "candidate_id": "map",
                                "video_path": str(final_video),
                                "subtitle_path": str(subtitle),
                            }
                        ]
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            review_path.write_text(
                json.dumps(
                    {"schema_version": "1.0", "candidate_reviews": []}
                )
                + "\n",
                encoding="utf-8",
            )
            evidence = _candidate_document(
                "map",
                FINALIZER._sha256(final_video),
                plan=plan,
            )["payload"]
            evidence["schema_version"] = 999
            evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
            state_path.write_bytes(b"original state\n")
            applied_review_path.write_bytes(b"original applied review\n")
            scorecards_path.write_bytes(b"original scorecards\n")
            original_files = {
                path: path.read_bytes()
                for path in (
                    pool_path,
                    summary_path,
                    state_path,
                    applied_review_path,
                    scorecards_path,
                )
            }
            args = SimpleNamespace(
                output=str(output_dir),
                review=str(review_path),
                candidate_evidence=[str(evidence_path)],
                invariant_evidence=[],
                invariant_scenario=None,
                budget_database=str(output_dir / "missing-budget.sqlite3"),
            )

            with (
                mock.patch.object(FINALIZER, "_parse_args", return_value=args),
                mock.patch.object(
                    FINALIZER.CampaignPlan,
                    "model_validate_json",
                    return_value=plan,
                ),
                mock.patch.object(
                    FINALIZER, "render_mixed_media_video"
                ) as render_video,
            ):
                with self.assertRaisesRegex(ValueError, "schema version"):
                    FINALIZER.main()

            render_video.assert_not_called()
            for path, original in original_files.items():
                with self.subTest(path=path.name):
                    self.assertEqual(path.read_bytes(), original)

    def test_bad_review_cannot_mutate_or_render_campaign(self) -> None:
        with TemporaryDirectory(dir=LOOP_ROOT) as directory:
            output_dir = Path(directory).resolve()
            plan_path = output_dir / "campaign-plan.json"
            pool_path = output_dir / "candidate-pool.json"
            summary_path = output_dir / "campaign-summary.json"
            review_path = output_dir / "campaign-review.json"
            state_path = output_dir / "map.state.json"
            plan_path.write_text("{}\n", encoding="utf-8")
            pool_path.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "candidate_id": "map",
                                "eligible": False,
                                "screening": {
                                    "candidate_id": "map",
                                    "temporal_events": [
                                        {
                                            "event_id": "event-1",
                                            "severity": "high",
                                        }
                                    ],
                                    "temporal_consistency_pass": False,
                                },
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            summary_path.write_text(
                json.dumps({"rendered_candidates": []}) + "\n",
                encoding="utf-8",
            )
            review_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "candidate_reviews": [
                            {
                                "candidate_id": "map",
                                "event_confirmations": [
                                    {
                                        "event_index": 9,
                                        "outcome": "false_positive",
                                        "reviewer": "human",
                                        "reviewed_at": "2026-08-18T12:00:00Z",
                                        "reason": "Frame review disproves it.",
                                        "evidence_frames": [],
                                    }
                                ],
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state_path.write_bytes(b"original state\n")
            original_pool = pool_path.read_bytes()
            original_summary = summary_path.read_bytes()
            original_state = state_path.read_bytes()
            args = SimpleNamespace(
                output=str(output_dir),
                review=str(review_path),
                candidate_evidence=[],
                invariant_evidence=[],
                invariant_scenario=None,
                budget_database=str(output_dir / "missing-budget.sqlite3"),
            )

            with (
                mock.patch.object(FINALIZER, "_parse_args", return_value=args),
                mock.patch.object(
                    FINALIZER.CampaignPlan,
                    "model_validate_json",
                    return_value=_plan("map"),
                ),
                mock.patch.object(
                    FINALIZER, "render_mixed_media_video"
                ) as render_video,
            ):
                with self.assertRaisesRegex(ValueError, "outside candidate events"):
                    FINALIZER.main()

            render_video.assert_not_called()
            self.assertEqual(pool_path.read_bytes(), original_pool)
            self.assertEqual(summary_path.read_bytes(), original_summary)
            self.assertEqual(state_path.read_bytes(), original_state)
            self.assertFalse((output_dir / "campaign-review-applied.json").exists())
            self.assertFalse((output_dir / "candidate-scorecards.json").exists())


if __name__ == "__main__":
    unittest.main()
