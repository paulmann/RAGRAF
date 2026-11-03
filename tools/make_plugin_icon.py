"""Иконка плагина RAGRAF: детерминированный рисунок, а не «картинка откуда-то».

Зачем скрипт, а не файл, положенный руками. Каталог плагинов Claude Code требует
квадратную иконку 512–2048 px (`claude plugin validate --profile` сообщает
`ICON_MISSING`), и иконка становится листингом в каталоге — единожды, при первой
подаче. Рисунок должен воспроизводиться: из этого скрипта получается та же
картинка, поэтому её можно пересобрать и сверить, а не гадать, откуда взялся
файл.

Запуск:

    python tools/make_plugin_icon.py                    # plugin/.claude-plugin/icon.png
    python tools/make_plugin_icon.py --out <путь.png>   # другой путь
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "plugin" / ".claude-plugin" / "icon.png"

#: Размер стороны. 1024 — внутри требуемого диапазона 512–2048 и делится на 4,
#: поэтому линии и скругления ложатся целыми пикселями.
SIZE = 1024

BACKGROUND = (15, 23, 42)      # #0F172A — тёмная подложка, читается на светлой и тёмной теме
NODE = (56, 189, 248)          # #38BDF8 — узлы графа
EDGE = (94, 234, 212)          # #5EEAD4 — рёбра
GLYPH = (226, 232, 240)        # #E2E8F0 — «R»


def build() -> Image.Image:
    """Нарисовать иконку: граф из пяти узлов и монограмма R."""
    image = Image.new("RGB", (SIZE, SIZE), BACKGROUND)
    draw = ImageDraw.Draw(image)

    # Рамка скруглением: на белом фоне каталога квадрат без скругления выглядит
    # грубее, чем соседние иконки.
    draw.rounded_rectangle((24, 24, SIZE - 24, SIZE - 24), radius=180,
                           outline=(51, 65, 85), width=10)

    nodes = [(330, 330), (694, 300), (330, 694), (694, 694), (512, 512)]
    edges = [(0, 4), (1, 4), (2, 4), (3, 4), (0, 1), (2, 3)]
    for a, b in edges:
        draw.line([nodes[a], nodes[b]], fill=EDGE, width=14)
    for index, (x, y) in enumerate(nodes):
        radius = 78 if index < 4 else 96
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=NODE)

    # Монограмма: RAGRAF начинается с R, и буква отличает иконку от «просто графа».
    draw.text((512, 872), "R", fill=GLYPH, anchor="mm", font=_font(210))
    return image


def _font(size: int):
    """Шрифт для монограммы: системный, с запасным вариантом по умолчанию."""
    from PIL import ImageFont

    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="make_plugin_icon",
                                     description="Иконка плагина RAGRAF (1024×1024 PNG)")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    image = build()
    image.save(out, format="PNG", optimize=True)
    print(f"[icon] {out} — {image.width}x{image.height}, {out.stat().st_size} байт")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
