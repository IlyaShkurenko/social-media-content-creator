from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app.config import config


ROOT_DIR = Path(__file__).parent.parent.parent
WEBUI_MAIN = ROOT_DIR / "webui" / "Main.py"


def _widget_by_key(elements, key: str):
    return next(
        item
        for item in elements
        if str(getattr(item, "key", "")) == key
        or str(getattr(item, "key", "")).startswith(f"{key}_")
    )


def test_material_settings_save_the_shared_gemini_api_key():
    """Gemini evaluator credentials can be configured without changing LLM provider."""
    test_config = dict(
        config.app,
        llm_provider="moonshot",
        gemini_api_key="saved-gemini-key",
    )

    with (
        patch.object(config, "app", test_config),
        patch.object(config, "try_save_config", return_value=True),
    ):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=30)
        app.session_state["ui_language"] = "en"
        app.run()
        _widget_by_key(app.button, "open_settings_dialog_button").click().run()

        api_key_input = _widget_by_key(
            app.text_input,
            "gemini_material_api_key_input",
        )
        assert api_key_input.label.startswith("Google Gemini API Key")
        assert api_key_input.proto.type == api_key_input.proto.PASSWORD
        assert api_key_input.value == "saved-gemini-key"

        api_key_input.set_value("  new-gemini-key  ").run()

    assert test_config["gemini_api_key"] == "new-gemini-key"
    assert [str(item.value) for item in app.exception] == []


def test_gemini_llm_and_material_inputs_do_not_overwrite_each_other():
    """Editing either Gemini credential entry point synchronizes the other one."""
    test_config = dict(
        config.app,
        llm_provider="gemini",
        gemini_api_key="saved-gemini-key",
    )

    with (
        patch.object(config, "app", test_config),
        patch.object(config, "try_save_config", return_value=True),
    ):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=30)
        app.session_state["ui_language"] = "en"
        app.run()
        _widget_by_key(app.button, "open_settings_dialog_button").click().run()

        llm_api_key_input = _widget_by_key(app.text_input, "gemini_api_key_input")
        llm_api_key_input.set_value("llm-edited-key").run()

        material_api_key_input = _widget_by_key(
            app.text_input,
            "gemini_material_api_key_input",
        )
        assert material_api_key_input.value == "llm-edited-key"
        assert test_config["gemini_api_key"] == "llm-edited-key"

        material_api_key_input.set_value("material-edited-key").run()

    assert app.session_state["gemini_api_key_input"] == "material-edited-key"
    assert test_config["gemini_api_key"] == "material-edited-key"
    assert [str(item.value) for item in app.exception] == []
