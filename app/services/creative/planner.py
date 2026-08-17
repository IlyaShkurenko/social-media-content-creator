from __future__ import annotations

import json
from typing import Callable

from pydantic import Field

from app.services.creative.storyboard import (
    CreativeModel,
    Storyboard,
    StoryboardValidationError,
    parse_storyboard_json,
)


class StoryboardPlanningError(RuntimeError):
    """Raised when an LLM response cannot become an executable storyboard."""


class StoryboardBrief(CreativeModel):
    product_name: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    product_facts: list[str] = Field(min_length=1)
    available_asset_ids: list[str] = Field(default_factory=list)
    content_language: str = "en-US"
    target_duration_seconds: int = 15
    aspect_ratio: str = "9:16"


def build_storyboard_prompt(brief: StoryboardBrief) -> str:
    """Build a strict, provider-neutral planning request."""

    schema = json.dumps(Storyboard.model_json_schema(), ensure_ascii=False)
    facts = "\n".join(f"- {fact}" for fact in brief.product_facts)
    assets = "\n".join(f"- {asset_id}" for asset_id in brief.available_asset_ids)
    return f"""You are the storyboard planner for a controlled short-form advertising experiment.

Product: {brief.product_name}
Audience: {brief.audience}
User-supplied hypothesis: {brief.hypothesis}
Product facts:
{facts}

Approved managed asset IDs:
{assets or "- none"}

Create exactly one {brief.target_duration_seconds}-second {brief.aspect_ratio} storyboard in {brief.content_language}.
Use exactly three ordered scenes: a 0-5 second hook, a 5-11 second product_demo,
and an 11-{brief.target_duration_seconds} second CTA. Describe concrete settings,
subject actions, camera behavior, voiceover, on-screen text, expected visual evidence,
an explicit screen_content_policy, and a layered media plan. Use approved_product_ui
only with an approved product_capture, non_product_context for a generic pre-product
screen, screen_hidden when the display must face away, and unconstrained when no
device screen matters. Product captures, logos, and mascots must reference only
the approved asset IDs. Do not ask a generative model to redraw readable application UI.
Use canonical lowercase "tict" in all visible copy and narration text. Declare the
brand pronunciation canonical="tict", spoken_alias="tickt", ipa="tɪkt" so speech
providers pronounce /tɪkt/ without changing subtitles.
Do not invent another hypothesis or paraphrase the supplied hypothesis.

Return raw JSON only: no explanation and no Markdown except one optional JSON code fence.
The JSON must validate against this schema:
{schema}
"""


def plan_storyboard(
    brief: StoryboardBrief,
    *,
    response_generator: Callable[[str], str] | None = None,
) -> Storyboard:
    """Ask the configured LLM for a plan, then fail closed on ambiguity or drift."""

    if brief.content_language != "en-US":
        raise StoryboardPlanningError("the first slice must use en-US")
    if brief.target_duration_seconds != 15 or brief.aspect_ratio != "9:16":
        raise StoryboardPlanningError(
            "the first slice must be a 15-second 9:16 advertisement"
        )

    if response_generator is None:
        from app.services import llm

        response_generator = llm._generate_response

    raw_response = response_generator(build_storyboard_prompt(brief))
    try:
        storyboard = parse_storyboard_json(raw_response)
    except StoryboardValidationError as exc:
        raise StoryboardPlanningError(str(exc)) from exc

    if storyboard.hypothesis != brief.hypothesis:
        raise StoryboardPlanningError(
            "planner changed the user-supplied hypothesis; execution is blocked"
        )
    if storyboard.target_duration_seconds != brief.target_duration_seconds:
        raise StoryboardPlanningError("planner changed the target duration")
    if storyboard.content_language != brief.content_language:
        raise StoryboardPlanningError("planner changed the content language")
    return storyboard
