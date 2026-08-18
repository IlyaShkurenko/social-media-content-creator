from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.creative.compiler import compile_comparison_plans
from app.services.creative.storyboard import (
    StoryboardValidationError,
    apply_brand_pronunciations,
    parse_storyboard_json,
    resolve_managed_asset,
    resolve_narration_voice,
    validate_storyboard,
)


def valid_payload() -> dict:
    return {
        "schema_version": "1.0",
        "storyboard_id": "fixture",
        "content_language": "en-US",
        "aspect_ratio": "9:16",
        "target_duration_seconds": 15,
        "hypothesis": "Planning is easier with one complete trip plan.",
        "scenes": [
            {
                "scene_id": "hook",
                "start_seconds": 0,
                "end_seconds": 5,
                "purpose": "hook",
                "visual_intent": {
                    "setting": "airport",
                    "subject_action": "traveller looks overwhelmed",
                    "camera": "push in",
                },
                "media_plan": {
                    "base": {"kind": "stock", "intent": "traveller at airport"},
                    "overlays": [],
                },
                "voiceover": "Planning a trip should not feel like another job.",
                "onscreen_text": "Too much planning?",
                "expected_evidence": ["traveller", "airport"],
            },
            {
                "scene_id": "demo",
                "start_seconds": 5,
                "end_seconds": 11,
                "purpose": "product_demo",
                "visual_intent": {
                    "setting": "airport lounge",
                    "subject_action": "traveller checks a trip plan on a phone",
                    "camera": "push toward phone",
                },
                "media_plan": {
                    "base": {"kind": "stock", "intent": "phone in hand"},
                    "overlays": [
                        {
                            "kind": "product_capture",
                            "asset_id": "screens/app.png",
                            "placement": "tracked_phone_screen",
                        }
                    ],
                },
                "voiceover": "Your complete trip, ready in one place.",
                "onscreen_text": "One plan. One place.",
                "expected_evidence": ["phone", "approved_ui"],
            },
            {
                "scene_id": "cta",
                "start_seconds": 11,
                "end_seconds": 15,
                "purpose": "cta",
                "visual_intent": {
                    "setting": "brand card",
                    "subject_action": "call to action appears",
                    "camera": "static",
                },
                "media_plan": {
                    "base": {"kind": "solid_or_graphic", "intent": "brand card"},
                    "overlays": [],
                },
                "voiceover": "Start planning your next trip today.",
                "onscreen_text": "Plan less. Travel more.",
                "call_to_action": "Create your trip",
                "expected_evidence": ["cta"],
            },
        ],
    }


def test_story_1_1_valid_storyboard_is_typed_and_ordered() -> None:
    storyboard = validate_storyboard(valid_payload())
    assert storyboard.schema_version == "1.0"
    assert storyboard.content_language == "en-US"
    assert [scene.scene_id for scene in storyboard.scenes] == ["hook", "demo", "cta"]


def test_story_1_2_overlapping_scenes_are_rejected() -> None:
    payload = valid_payload()
    payload["scenes"][1]["start_seconds"] = 4
    with pytest.raises(StoryboardValidationError, match="overlap"):
        validate_storyboard(payload)


def test_story_1_2_scene_beyond_target_duration_is_rejected() -> None:
    payload = valid_payload()
    payload["scenes"][-1]["end_seconds"] = 16
    with pytest.raises(StoryboardValidationError, match="target duration"):
        validate_storyboard(payload)


def test_story_1_3_non_english_first_slice_is_rejected() -> None:
    payload = valid_payload()
    payload["content_language"] = "ru-RU"
    with pytest.raises(StoryboardValidationError, match="en-US"):
        validate_storyboard(payload)


def test_story_1_3_voice_defaults_to_english_independent_of_ui_locale() -> None:
    settings = resolve_narration_voice(
        content_language="en-US",
        requested_voice="",
        interface_locale="ru-RU",
    )
    assert settings.voice_name == "en-US-JennyNeural-Female"


def test_story_1_3_explicit_incompatible_voice_is_rejected() -> None:
    with pytest.raises(StoryboardValidationError, match="incompatible"):
        resolve_narration_voice(
            content_language="en-US",
            requested_voice="ru-RU-SvetlanaNeural-Female",
            interface_locale="ru-RU",
        )


def test_story_1_2_parser_accepts_one_surrounding_code_fence() -> None:
    raw = f"```json\n{json.dumps(valid_payload())}\n```"
    assert parse_storyboard_json(raw).storyboard_id == "fixture"


def test_story_1_2_parser_rejects_explanatory_prose() -> None:
    raw = f"Here is the storyboard: {json.dumps(valid_payload())}"
    with pytest.raises(StoryboardValidationError, match="raw JSON"):
        parse_storyboard_json(raw)


def test_brand_1_3_managed_asset_resolves_inside_root(tmp_path: Path) -> None:
    asset = tmp_path / "screens" / "app.png"
    asset.parent.mkdir()
    asset.write_bytes(b"png")
    assert resolve_managed_asset(tmp_path, "screens/app.png") == asset.resolve()


def test_brand_1_3_path_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(StoryboardValidationError, match="managed asset root"):
        resolve_managed_asset(tmp_path, "../secret.png")


def test_adpipe_1_2_comparison_changes_only_hook_base() -> None:
    plans = compile_comparison_plans(validate_storyboard(valid_payload()))
    baseline = plans["stock-baseline"]
    candidate = plans["runway-candidate"]
    assert baseline.scenes[0].media_plan.base.kind == "stock"
    assert candidate.scenes[0].media_plan.base.kind == "generated"
    assert candidate.scenes[0].media_plan.base.provider == "runway"
    assert candidate.scenes[0].media_plan.base.model == "gen4.5"
    assert candidate.scenes[0].media_plan.base.duration_seconds == 5
    assert baseline.scenes[1:] == candidate.scenes[1:]
    assert baseline.narration_script == candidate.narration_script
    assert baseline.storyboard_fingerprint == candidate.storyboard_fingerprint


def test_story_1_5_v11_requires_explicit_screen_policy() -> None:
    payload = valid_payload()
    payload["schema_version"] = "1.1"
    with pytest.raises(StoryboardValidationError, match="screen_content_policy"):
        validate_storyboard(payload)


def test_brand_1_5_approved_ui_requires_product_capture() -> None:
    payload = valid_payload()
    payload["schema_version"] = "1.1"
    for scene in payload["scenes"]:
        scene["visual_intent"]["screen_content_policy"] = "unconstrained"
    payload["scenes"][0]["visual_intent"]["screen_content_policy"] = (
        "approved_product_ui"
    )
    with pytest.raises(StoryboardValidationError, match="product_capture"):
        validate_storyboard(payload)


def test_brand_1_4_canonical_copy_and_spoken_alias_are_separate() -> None:
    payload = valid_payload()
    payload["schema_version"] = "1.1"
    payload["brand_pronunciations"] = [
        {"canonical": "tict", "spoken_alias": "tickt", "ipa": "tɪkt"}
    ]
    for scene in payload["scenes"]:
        scene["visual_intent"]["screen_content_policy"] = "unconstrained"
    payload["scenes"][1]["visual_intent"]["screen_content_policy"] = (
        "approved_product_ui"
    )
    payload["scenes"][1]["voiceover"] = "tict keeps the trip in one place."
    storyboard = validate_storyboard(payload)
    assert storyboard.scenes[1].voiceover == "tict keeps the trip in one place."
    assert apply_brand_pronunciations(
        storyboard,
        storyboard.scenes[1].voiceover,
    ) == "tickt keeps the trip in one place."


def test_brand_1_4_uppercase_visible_brand_copy_is_rejected() -> None:
    payload = valid_payload()
    payload["schema_version"] = "1.1"
    payload["brand_pronunciations"] = [
        {"canonical": "tict", "spoken_alias": "tickt", "ipa": "tɪkt"}
    ]
    for scene in payload["scenes"]:
        scene["visual_intent"]["screen_content_policy"] = "unconstrained"
    payload["scenes"][1]["visual_intent"]["screen_content_policy"] = (
        "approved_product_ui"
    )
    payload["scenes"][1]["voiceover"] = "TICT keeps the trip in one place."
    with pytest.raises(StoryboardValidationError, match="canonical lowercase"):
        validate_storyboard(payload)


def test_brand_1_6_v11_cta_requires_storyboard_owned_action_copy() -> None:
    payload = valid_payload()
    payload["schema_version"] = "1.1"
    for scene in payload["scenes"]:
        scene["visual_intent"]["screen_content_policy"] = "unconstrained"
    payload["scenes"][1]["visual_intent"]["screen_content_policy"] = (
        "approved_product_ui"
    )
    payload["scenes"][2]["call_to_action"] = ""

    with pytest.raises(StoryboardValidationError, match="call_to_action"):
        validate_storyboard(payload)


def test_brand_1_6_v12_cta_requires_semantic_layout_intent() -> None:
    payload = valid_payload()
    payload["schema_version"] = "1.2"
    for scene in payload["scenes"]:
        scene["visual_intent"]["screen_content_policy"] = "unconstrained"
    payload["scenes"][1]["visual_intent"]["screen_content_policy"] = (
        "approved_product_ui"
    )

    with pytest.raises(StoryboardValidationError, match="layout_intent"):
        validate_storyboard(payload)
