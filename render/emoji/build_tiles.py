"""Draw the bot's custom emoji set from the marks the cards already use.

    CHROME=<chrome-headless-shell> uv run --with pillow python render/emoji/build_tiles.py <out-dir>

Writes <out-dir>/100/<name>.png (100×100 RGBA) and <out-dir>/emoji.json for the
telegram-emoji-pack skill's ``emoji_set.py``. The set's first emoji on each sticker
is the mark in the code, so ``app/services/custom_emoji.py`` needs no table.

Left out on purpose: ✓ ▰ ▱ → ─ ≈ − are text and Telegram takes no such fallback.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE.parent / "templates"
TILE = 100
PER_ROW = 10

# (mark, icon in icons.js or marks.js, keywords)
SET = [
    # food groups
    ("🫘", "legumes", ["бобовые", "beans"]),
    ("⚓", "anchor", ["опора", "белок", "anchor"]),
    ("🥦", "veg", ["овощи", "vegetables"]),
    ("🍎", "fruit", ["фрукты", "fruit"]),
    ("🌾", "grain", ["злаки", "grain"]),
    ("🥜", "nuts", ["орехи", "nuts"]),
    ("🥛", "calcium", ["кальций", "calcium"]),
    ("🌰", "omega", ["омега", "omega"]),
    ("🌱", "plants", ["растения", "plants"]),
    ("🥕", "carrot", ["овощ", "carrot"]),
    # the record
    ("🍽", "plate", ["приём", "meal"]),
    ("🍲", "dish", ["блюдо", "dish"]),
    ("🥫", "can", ["готовое", "can"]),
    ("🔥", "fire", ["калории", "calories"]),
    ("🔢", "digits", ["цифры", "numbers"]),
    ("⚖", "scale", ["вес", "weight"]),
    ("📷", "camera", ["фото", "photo"]),
    ("🎙", "mic", ["голос", "voice"]),
    ("🕘", "clock9", ["время", "time"]),
    ("🕐", "clock", ["время", "пораньше", "time"]),
    # acting on it
    ("✅", "done", ["готово", "done"]),
    ("✏", "pencil", ["поправить", "edit"]),
    ("🗑", "trash", ["удалить", "delete"]),
    ("↩", "undo", ["отменить", "undo"]),
    ("➕", "plus", ["добавить", "add"]),
    ("➖", "minus", ["убрать", "remove"]),
    ("✖", "cross", ["убрать", "remove"]),
    ("❌", "reject", ["нет", "no"]),
    ("🔁", "repeat", ["повторить", "repeat"]),
    ("🔄", "swap", ["другой", "swap"]),
    ("🔗", "link", ["связать", "link"]),
    ("🔍", "search", ["найти", "search"]),
    ("🏷", "tag", ["группы", "tag"]),
    ("🔣", "symbols", ["значок", "symbols"]),
    # moving around
    ("⬅", "back", ["назад", "back"]),
    ("◀", "prev", ["назад", "prev"]),
    ("▶", "next", ["дальше", "next"]),
    ("⚙", "gear", ["настройки", "settings"]),
    ("📋", "clipboard", ["записи", "records"]),
    ("❓", "question", ["вопрос", "help"]),
    # progress and one small step
    ("📊", "chart", ["неделя", "chart"]),
    ("👣", "step", ["шаг", "step"]),
    ("🎯", "target", ["цель", "шаг", "target"]),
    ("⭐", "star", ["избранное", "favourite"]),
    ("🧺", "basket", ["надёжная", "еда", "basket"]),
    ("✋", "hand", ["не предлагать", "hand"]),
    ("😴", "later", ["не сегодня", "сон", "sleep"]),
    ("🔋", "battery", ["силы", "battery"]),
    ("🪫", "lowbat", ["мало сил", "battery"]),
    ("🛋", "sofa", ["минимум", "sofa"]),
    # routines
    ("💊", "routine", ["рутина", "pill"]),
    ("🚶", "walk", ["прогулка", "walk"]),
    ("🧘", "lotus", ["спокойно", "calm"]),
    ("💧", "drop", ["вода", "water"]),
    # nutrients
    ("🧪", "flask", ["нутриенты", "flask"]),
    ("🧂", "salt", ["йод", "salt"]),
    ("☀", "sun", ["витамин d", "sun"]),
    ("🩸", "blood", ["железо", "iron"]),
    # notices
    ("⚠", "warn", ["внимание", "warning"]),
    ("⛔", "stop", ["нельзя", "stop"]),
    ("⏳", "hourglass", ["ждём", "wait"]),
    ("🔒", "lock", ["закрыто", "lock"]),
    ("👍", "thumb", ["да", "yes"]),
    ("👋", "wave", ["привет", "hello"]),
    ("✨", "spark", ["новое", "spark"]),
    ("💫", "dizzy", ["подписка", "spark"]),
    # the service
    ("💎", "gem", ["подписка", "gem"]),
    ("💳", "bankcard", ["оплата", "card"]),
    ("💸", "money", ["расход", "cost"]),
    ("📦", "box", ["штрихкод", "box"]),
    ("🌐", "globe", ["сеть", "web"]),
    ("📱", "phone", ["связаться", "phone"]),
    ("🤖", "robot", ["бот", "robot"]),
    ("🧠", "brain", ["модели", "brain"]),
    ("🗓", "calendar2", ["проверка", "calendar"]),
    ("📅", "calendar", ["дата", "calendar"]),
]


def page() -> str:
    """One page with every tile on a transparent grid, cut apart afterwards."""
    names = json.dumps([icon for _, icon, _ in SET])
    font = (HERE.parent / "fonts" / "Nunito.ttf").as_uri()
    return (
        "<!doctype html><meta charset=utf-8><style>"
        f'@font-face{{font-family:"Nunito";src:url("{font}");font-weight:200 1000}}'
        "html,body{margin:0;background:transparent}"
        f"#g{{display:grid;grid-template-columns:repeat({PER_ROW},{TILE}px);"
        f"grid-auto-rows:{TILE}px}}"
        ".tile{display:block;box-sizing:border-box;color:#23262B}"
        ".ico{display:block;width:100%;height:100%}"
        "</style><div id=g></div><script>"
        + (TEMPLATES / "icons.js").read_text(encoding="utf-8")
        + (HERE / "marks.js").read_text(encoding="utf-8")
        # The whole 100 px is the tile: Telegram adds its own gap around an emoji.
        + f"{names}.forEach(function(n){{var t=tile(n,{TILE},n==='done'?0:13);"
        "document.getElementById('g').appendChild(t);});</script>"
    )


def shoot(html: str, out: Path) -> None:
    rows = -(-len(SET) // PER_ROW)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "tiles.html"
        src.write_text(html, encoding="utf-8")
        subprocess.run(
            [
                os.environ["CHROME"],
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                "--default-background-color=00000000",
                f"--window-size={PER_ROW * TILE},{rows * TILE}",
                f"--screenshot={out}",
                src.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )


def main(out_dir: Path) -> int:
    from PIL import Image  # the tile build only: the bot's image does not need it

    icons = [icon for _, icon, _ in SET]
    if len(set(icons)) != len(icons) or len({m for m, _, _ in SET}) != len(SET):
        print("a mark or an icon appears twice", file=sys.stderr)
        return 1
    (out_dir / "100").mkdir(parents=True, exist_ok=True)
    grid = out_dir / "grid.png"
    shoot(page(), grid)
    sheet = Image.open(grid).convert("RGBA")
    spec = []
    for i, (mark, icon, keywords) in enumerate(SET):
        x, y = (i % PER_ROW) * TILE, (i // PER_ROW) * TILE
        cell = sheet.crop((x, y, x + TILE, y + TILE))
        if cell.getextrema()[3][1] == 0:
            print(f"{icon} came out empty", file=sys.stderr)
            return 1
        cell.save(out_dir / "100" / f"{icon}.png")
        spec.append({"file": f"100/{icon}.png", "mark": mark, "keywords": keywords})
    (out_dir / "emoji.json").write_text(
        json.dumps(
            {"title": "Nutricore", "adaptive": False, "emoji": spec},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"{len(spec)} tiles in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
