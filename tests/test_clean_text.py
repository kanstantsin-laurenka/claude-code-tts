import pytest

from claude_code_tts.clean_text import TRUNCATION_SUFFIX, clean_for_tts, extract_tldr


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
        ("image ref dropped", "См. ![картинка](pic.png) тут", "См. тут"),
        ("nested inline", "**жир `код` внутри**", "жир код внутри"),
        ("mixed structure", "# Заголовок\n- раз\n- два\nконец", "Заголовок раз два конец"),
    ],
)
def test_cases(name, raw, expected):
    assert clean_for_tts(raw) == expected


def test_none_input_returns_empty_string():
    assert clean_for_tts(None) == ""  # type: ignore[arg-type]


def test_truncation_adds_suffix():
    long_text = "а " * 2000
    out = clean_for_tts(long_text, max_chars=100)
    assert out.endswith(TRUNCATION_SUFFIX)
    assert len(out) <= 100 + len(TRUNCATION_SUFFIX) + 2


def test_code_only_input_becomes_empty():
    assert clean_for_tts("```python\nprint('x')\n```") == ""


@pytest.mark.parametrize(
    "raw,expected",
    [
        pytest.param(
            "Всё готово.\n\n## TL;DR\n\nКратко: сделано.",
            "Кратко: сделано.",
            id="h2-header",
        ),
        pytest.param(
            "Отчёт.\n\n### TL;DR\nВсё ок.",
            "Всё ок.",
            id="h3-header",
        ),
        pytest.param(
            "Длинный текст...\n\n**TL;DR**\n\nСуть в одной строке.",
            "Суть в одной строке.",
            id="bold-marker",
        ),
        pytest.param(
            "prose prose\nTL;DR: коротко и ясно",
            "коротко и ясно",
            id="inline-colon",
        ),
        pytest.param(
            "blah\nTL;DR\n\nрезюме",
            "резюме",
            id="bare-marker",
        ),
        pytest.param(
            "bla\n## tl;dr\nlowercase",
            "lowercase",
            id="case-insensitive",
        ),
        pytest.param(
            "## TL;DR\nпервая версия\n\n## TL;DR\nокончательная версия",
            "окончательная версия",
            id="takes-last-marker",
        ),
        pytest.param(
            "## TL;DR\n\nпервая строка.\nвторая строка.",
            "первая строка.\nвторая строка.",
            id="multiline-body-preserved",
        ),
        pytest.param(
            # A code-fence containing an example TL;DR must not shadow the real
            # one above it — documentation snippets shouldn't trigger skip.
            "## TL;DR\nнастоящее резюме\n\n```\n## TL;DR\nпример из документации\n```",
            "настоящее резюме",
            id="tldr-inside-code-fence-ignored",
        ),
    ],
)
def test_extract_tldr_positive(raw, expected):
    assert extract_tldr(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("", id="empty-string"),
        pytest.param(None, id="none"),
        pytest.param("просто текст без маркера", id="no-marker"),
        pytest.param(
            "As a TL;DR approach, we could...",
            id="mid-sentence-mention",  # not at line start → not a marker
        ),
        pytest.param(
            "## TL;DR\n\n   \n",
            id="empty-body-after-strip",  # marker present but body blank
        ),
        pytest.param("*TL;DR*\ncontent", id="italic-single-stars"),
        pytest.param("*TL;DR\ncontent", id="asymmetric-left"),
        pytest.param("TL;DR**\ncontent", id="asymmetric-right"),
        pytest.param("***TL;DR***\ncontent", id="triple-stars"),
        pytest.param(
            "```\n## TL;DR\nonly inside a fence\n```",
            id="only-inside-code-fence",
        ),
    ],
)
def test_extract_tldr_absent_returns_none(raw):
    assert extract_tldr(raw) is None
