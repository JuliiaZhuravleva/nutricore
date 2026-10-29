"""Every user-facing string: no judging words, no gender, one register («ты»).

Stage 5 rules 8 and 10 (G5-R19): the bot says what is there, never what is too
much or missing, and it is universal, so nothing it says carries the user's or
its own gender. The scan reads every Cyrillic string literal in ``app/`` and
``bot.py`` (docstrings aside); model prompts in ``openai_service.py`` are not
shown to the user and are skipped. The controls below plant known-bad strings
through the same checker, so a green run proves the checker can see them.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKIP_FILES = {"app/services/openai_service.py"}  # prompts for the model

_W = r"(?<![а-яё])"  # word start: \b does not see «ё» edges reliably
_E = r"(?![а-яё])"  # word end
RULES = {
    "judging word": re.compile(
        _W + r"(слишком|превыс\w*|перебор\w*|норм(а|е|у|ы|ой)|недобор\w*|осталось"
        r"|срыв\w*|сорвал\w*|плохо|хорошо|чист\w* питани\w*|заработал\w*"
        r"|отработа\w*|сжечь|сжёг|сжига\w*|надо|нужно|долж(на|ен|ны|но)"
        r"|просроч\w*|пора)" + _E,
        re.IGNORECASE,
    ),
    "gendered form": re.compile(
        _W + r"(сделала|записала|съела|выпила|готова|уверена|рада"
        r"|(ты|я) [а-яё]+л[аи]?)" + _E + r"|[а-яё]\((а|ла)\)",
        re.IGNORECASE,
    ),
    "«вы» register": re.compile(
        _W + r"(вы|вас|вам|ваш\w*|[а-яё]+(ите|йте))" + _E, re.IGNORECASE
    ),
}

# (file, rule, word) → why the string may keep it.
EXEMPT = {
    ("app/services/model_watch.py", "judging word", "осталось"): (
        "admin notice: days left before an AI model shuts down, not about food"
    ),
}


def findings(path: str, text: str) -> list[str]:
    out = []
    for rule, pattern in RULES.items():
        for match in pattern.finditer(text):
            word = match.group(0).lower()
            if (path, rule, word) not in EXEMPT:
                out.append(f"{path}: {rule} «{match.group(0)}» in {text!r}")
    return out


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            value = body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                ids.add(id(value))
    return ids


def user_strings() -> list[tuple[str, str]]:
    files = sorted((ROOT / "app").rglob("*.py")) + [ROOT / "bot.py"]
    out = []
    for file in files:
        path = file.relative_to(ROOT).as_posix()
        if path in SKIP_FILES:
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"))
        skip = _docstrings(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
                and re.search(r"[а-яё]", node.value, re.IGNORECASE)
            ):
                out.append((path, node.value))
    return out


def test_the_scan_sees_the_bot_strings():
    texts = [text for _path, text in user_strings()]
    assert len(texts) > 100
    assert "Разбор фото:" in texts  # a known reply: the collector reaches telegram.py


def test_no_user_facing_string_judges_genders_or_switches_register():
    found = [f for path, text in user_strings() for f in findings(path, text)]
    assert found == []


@pytest.mark.parametrize(
    "planted, rule",
    [
        ("Слишком много углеводов", "judging word"),
        ("Сегодня ты превысила норму", "judging word"),
        ("Осталось 300 ккал", "judging word"),
        ("Тебе нужно больше белка", "judging word"),
        ("✅ Сделала", "gendered form"),
        ("Что ты ел(а)?", "gendered form"),
        ("Я проанализировал фото", "gendered form"),
        ("Ты съела всё", "gendered form"),
        ("Что съел(а) на обед?", "gendered form"),
        ("Пожалуйста, попробуйте ещё раз", "«вы» register"),
        ("Это ваш приём пищи", "«вы» register"),
    ],
)
def test_control_a_planted_string_is_caught(planted, rule):
    caught = [f for f in findings("app/x.py", planted) if f" {rule} " in f]
    assert caught, f"{planted!r} passed the {rule} rule"
    assert all(f.startswith(f"app/x.py: {rule} «") for f in caught)


def test_control_the_exemption_is_scoped_to_its_file():
    text = "⚠️ До отключения модели осталось 5 дн."
    assert findings("app/services/model_watch.py", text) == []
    assert findings("app/services/stats_screen.py", text) != []


@pytest.mark.parametrize(
    "fine",
    [
        "✅ Готово",
        "✅ Записано",
        "Нормально",
        "Не получилось, попробуй ещё раз",
        "Подожди пару секунд и спроси снова.",
        "💎 Для доступа к этим функциям нужна подписка.",
        "Записей нет. Еда попадает сюда после «✅ Сохранить»",
        "За неделю записей нет.",
        "Выбери пояс",
        "Ты ешь что хочешь",
    ],
)
def test_ordinary_strings_produce_no_finding(fine):
    assert findings("app/x.py", fine) == []
