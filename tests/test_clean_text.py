import pytest

from claude_code_tts.clean_text import TRUNCATION_SUFFIX, clean_for_tts


@pytest.mark.parametrize(
    "name,raw,expected",
    [
        ("plain", "Привет мир", "Привет мир"),
        (
            "code block removed",
            "Вот решение:\n```python\nprint(1)\n```\nготово",
            "Вот решение: готово",
        ),
        ("inline backtick", "Используй `cat` и `grep`", "Используй cat и grep"),
        (
            "markdown link",
            "См. [документацию](https://example.com/docs)",
            "См. документацию",
        ),
        ("bare url", "Зайди на https://example.com сейчас", "Зайди на сейчас"),
        ("html tags", "Текст <b>жирный</b> конец", "Текст жирный конец"),
        ("heading", "# Заголовок\nабзац", "Заголовок абзац"),
        ("bullet", "- раз\n- два\n- три", "раз два три"),
        ("numbered", "1. раз\n2. два", "раз два"),
        ("bold italic", "**жирный** и *курсив*", "жирный и курсив"),
        ("pipe table", "| a | b |\n| 1 | 2 |", "a b 1 2"),
        ("blockquote", "> цитата\nпродолжение", "цитата продолжение"),
        ("empty", "", ""),
        ("whitespace only", "   \n\n  ", ""),
        ("tilde fenced", "~~~js\nx = 1\n~~~\nend", "end"),
    ],
)
def test_cases(name, raw, expected):
    assert clean_for_tts(raw) == expected


def test_truncation_adds_suffix():
    long_text = "а " * 2000
    out = clean_for_tts(long_text, max_chars=100)
    assert out.endswith(TRUNCATION_SUFFIX)
    assert len(out) <= 100 + len(TRUNCATION_SUFFIX) + 2


def test_code_only_input_becomes_empty():
    assert clean_for_tts("```python\nprint('x')\n```") == ""
