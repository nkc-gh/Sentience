from unittest.mock import patch

from prompt_toolkit.document import Document

from cli.prompts import _NavigationCompleter, prompt


def test_global_prompt_completes_back_and_clear():
    values = {item.text for item in _NavigationCompleter().get_completions(Document(""), None)}
    assert values == {"back", "clear", "home"}


def test_clear_works_inside_nested_prompt_and_reprompts():
    with patch("cli.prompts._prompt", side_effect=["clear", "back"]), patch(
        "cli.prompts.os.system", return_value=0
    ) as clear, patch("cli.prompts._redraw_current_screen") as redraw:
        assert prompt("model [add] > ") == "back"
    clear.assert_called_once()
    redraw.assert_called_once()
