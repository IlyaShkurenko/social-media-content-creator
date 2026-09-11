import json

from pytest_bdd import given, scenarios, then, when

from app.services.creative.motion_agent import run_agent

scenarios("../features/motion_agent.feature")


@given("a motion request for ten seconds and a renderer that fails once", target_fixture="motion_case")
def request_case(tmp_path):
    return {"root": tmp_path / "run", "calls": [], "render_count": 0}


@when("the motion agent authors and renders the project")
def execute(motion_case):
    case = motion_case

    def author(stage, payload, directory):
        case["calls"].append((stage, payload))
        if stage == "plan":
            return {"hypothesis": "Clarity helps", "materials": [],
                    "audio": {"mode": "silent", "asset_ids": []},
                    "beats": [{"id": "single", "start": 0, "end": 10, "intent": "Show an idea"}]}
        return {"source": "export default () => <div>Idea</div>;", "change_hypothesis": "Repair build"}

    def renderer(project, output):
        case["render_count"] += 1
        if case["render_count"] == 1:
            raise RuntimeError("fixture compilation failed")
        output.mkdir()
        (output / "video.mp4").write_bytes(b"fixture-only")
        return {"technical_pass": True}

    run_agent({"request": "Ten second editorial animation", "hypothesis": "Clarity helps",
               "duration_seconds": 10, "width": 720, "height": 1280, "fps": 30,
               "language": "en-US", "audio_mode": "silent", "required_assets": []},
              {}, case["root"], author=author, renderer=renderer, tools={})


@then("the author receives the actual build error")
def error_evidence(motion_case):
    stage, payload = motion_case["calls"][-1]
    assert stage == "repair"
    assert "fixture compilation failed" in payload["build_error"]


@then("both source attempts are retained under the same request")
def retained(motion_case):
    root = motion_case["root"]
    contracts = [json.loads((root / f"attempt-{i:02}/project.json").read_text()) for i in (1, 2)]
    assert contracts[0]["user_constraints"] == contracts[1]["user_constraints"]
    assert (root / "attempt-01/failure.json").is_file()
    assert (root / "attempt-02/render/video.mp4").is_file()
