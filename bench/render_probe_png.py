"""render_probe_png — картинка таблицы замера из JSON-артефакта nav_probe.

Зачем. Пост в X требует проверяемого материала, а числа мелким шрифтом в тексте
не читают. Картинка собирается ИЗ артефакта (`bench/results/nav_probe_*.json`),
а не рисуется руками: любое расхождение с JSON сразу видно при повторном
запуске, и подделать таблицу, не подделав артефакт, нельзя.

Запуск:
    python bench/render_probe_png.py --json bench/results/nav_probe_20261021.json \
        --out ../SUBMISSION-ROUND2/media/probe-table.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = (15, 17, 21)
PANEL = (22, 26, 33)
INK = (232, 236, 244)
DIM = (138, 148, 166)
GOOD = (94, 214, 155)
ACCENT = (122, 162, 247)
LINE = (40, 46, 58)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\CascadiaMono.ttf",
    r"C:\Windows\Fonts\lucon.ttf",
]

COLUMNS = [
    ("definition", 300),
    ("rg files", 120),
    ("index", 110),
    ("rg bytes", 170),
    ("answer", 150),
    ("first hit", 130),
]


def font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size)


def human(number: int) -> str:
    """Байты в читаемый вид: 1 071 375 → 1.07 MB."""
    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f} MB"
    if number >= 1_000:
        return f"{number / 1_000:.1f} KB"
    return str(number)


def spaced(number: int) -> str:
    return f"{number:,}".replace(",", " ")


def render(report: dict, out_path: Path) -> None:
    questions = report["questions"]
    summary = report["summary"]
    corpus = report["corpus"]

    width = 1600
    pad = 56
    row_h = 46
    header_h = 210
    table_top = header_h + 96
    footer_h = 210
    height = table_top + row_h * (len(questions) + 1) + footer_h

    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    f_title = font(44)
    f_sub = font(24)
    f_cell = font(26)
    f_head = font(22)
    f_big = font(40)
    f_note = font(22)

    # ── шапка ────────────────────────────────────────────────────────────────
    draw.text((pad, 48), "RAGRAF · nav_probe", font=f_title, fill=INK)
    shell = corpus.get("root_name") or Path(corpus.get("name", "corpus")).name
    draw.text((pad, 108),
              f"corpus {shell}   "
              f"rev {corpus['rev']}   {corpus['capability']}   "
              f"{report['measured_on']}",
              font=f_sub, fill=DIM)
    draw.text((pad, 146),
              "ground truth from source text (not the index) · "
              "deterministic 14-name sample · one symbol call per question",
              font=f_note, fill=DIM)
    draw.line([(pad, header_h + 30), (width - pad, header_h + 30)], fill=LINE, width=2)

    # ── таблица ──────────────────────────────────────────────────────────────
    x = pad
    for title, column_w in COLUMNS:
        draw.text((x, table_top - 44), title.upper(), font=f_head, fill=DIM)
        x += column_w

    y = table_top
    for index, question in enumerate(questions):
        if index % 2 == 0:
            draw.rectangle([(pad - 16, y - 8), (width - pad + 16, y + row_h - 12)], fill=PANEL)
        x = pad
        values = [
            question["name"],
            str(question["baseline_files"]),
            str(question["index_files"]),
            human(question["baseline_bytes"]),
            human(question["index_bytes"]),
            f"#{question['first_hit_rank']}" if question["first_hit_rank"] else "—",
        ]
        for position, ((_title, column_w), value) in enumerate(zip(COLUMNS, values)):
            if position == 0:
                fill = INK
            elif position == 5:
                fill = GOOD if question["first_hit_rank"] == 1 else DIM
            else:
                fill = DIM
            draw.text((x, y), value, font=f_cell, fill=fill)
            x += column_w
        y += row_h

    draw.line([(pad, y + 4), (width - pad, y + 4)], fill=LINE, width=2)

    # ── итог ─────────────────────────────────────────────────────────────────
    y += 34
    headline = [
        (f"{summary['hit_at_1']}/{summary['questions']}", "located on the\nfirst fact"),
        (f"{summary['hit_any_rank']}/{summary['questions']}", "found in the\nsingle call"),
        (f"{summary['baseline_files_total']} → {summary['index_files_total']}", "candidate files\nrg → index"),
        # Округление ВНИЗ: картинка обязана совпадать с числом в README (×253),
        # иначе два документа проекта называют разное сужение одного замера.
        (f"×{int(summary['bytes_narrowing'])}",
         f"context bytes\n{summary['baseline_bytes_total'] / 1_000_000:.2f} MB → "
         f"{summary['index_bytes_total'] / 1_000:.1f} KB"),
        (f"{summary['median_index_ms']:.0f} ms", "median answer"),
    ]
    x = pad
    for value, label in headline:
        draw.text((x, y), value, font=f_big, fill=ACCENT)
        draw.multiline_text((x, y + 52), label, font=f_note, fill=DIM, spacing=6)
        x += 300

    draw.text((pad, height - 62),
              "reproduce: python bench/nav_probe.py --project config/<corpus>.yaml "
              "--limit 14 --out bench/results/nav_probe_<date>.json",
              font=f_note, fill=DIM)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "PNG", optimize=True)
    print(f"[render] {out_path.as_posix()}  {image.width}×{image.height}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Картинка таблицы замера из JSON")
    parser.add_argument("--json", required=True, help="артефакт nav_probe")
    parser.add_argument("--out", required=True, help="куда положить PNG")
    args = parser.parse_args(argv)

    report = json.loads(Path(args.json).read_text(encoding="utf-8"))
    render(report, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
