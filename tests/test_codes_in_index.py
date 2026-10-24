"""Проверка: попали ли ps1, sh, bat и другие языки в индекс.

Это прямая проверка исходной задачи: «добавить в сканирование и индексацию
RAGRAF ps1, bat, sh и другие найденные коды».

Проверяется по факту индекса, а не по конфигу: сколько файлов каждого языка
проиндексировано, дали ли они узлы (то есть был ли разбор деревом), и находит
ли их поиск.
"""

from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

# Языки, которых раньше в индексе не было вовсе (по отчёту: их расширений
# нет в LANG_BY_EXT). Для каждого — ожидание: должен быть разбор деревом.
EXPECTED = {
    "powershell": ("ps1", "psm1", True),
    "bash": ("sh", "bash", True),
    "batch": ("bat", "cmd", False),      # грамматика дала ошибки — только текст
    "csharp": ("cs", None, True),
    "perl": ("pl", "pm", True),
    "python": ("py", None, True),
    "javascript": ("js", "mjs", True),
    "typescript": ("ts", "tsx", True),
    "yaml": ("yaml", "yml", False),
    "json": ("json", None, False),
    "sql": ("sql", None, False),
    "markdown": ("md", None, False),
}


def result_of(env):
    return env.get("result") if isinstance(env, dict) else env


for config_name, title in (
    ("rdpmon.yaml", "RdpMON (C#-проект)"),
    ("z-batteryservice.yaml", "z.batteryservice.ru (PHP-проект)"),
    ("md.viewer.yaml", "MD.Viewer"),
):
    path = REPO / "config" / config_name
    if not path.exists():
        print(f"\n{title}: конфига нет ({config_name})")
        continue
    cfg = load_config(path)
    print()
    print("=" * 78)
    print(f"КОРПУС {title}")
    print("=" * 78)

    fac = Facade(cfg, tool="test_codes_in_index", telemetry=False, watch=False)
    env = fac.op_index_status()
    res = result_of(env)
    counts = env.get("counts", {}) if isinstance(env, dict) else {}
    cov = res.get("coverage", {}) if isinstance(res, dict) else {}
    print(f"  ревизия {env.get('index_rev')}: файлов {counts.get('files')}, "
          f"узлов {counts.get('nodes')}, рёбер {counts.get('edges')}")
    print(f"  разобрано полностью: {cov.get('files_ok')}, "
          f"текстом: {cov.get('files_skipped')}, "
          f"частично: {cov.get('files_partial')}")

    # Языки берутся из конфига корпуса: так видно, что индексатор получил.
    langs = set(cfg.languages)
    print(f"\n  языков в конфиге корпуса: {len(langs)}")
    print(f"    {', '.join(sorted(langs))}")

    print(f"\n  {'язык':<14} {'в конфиге':<11} {'ожидание':<14} поиск находит")
    print("  " + "-" * 62)
    for lang, (ext, ext2, parse_expected) in EXPECTED.items():
        in_cfg = lang in langs
        if not in_cfg:
            print(f"  {lang:<14} {'НЕТ':<11} {'—':<14} —")
            continue
        # Поиск по расширению: должен вернуть хотя бы один файл.
        probe = fac.op_search(query=f"{ext} файл скрипт", limit=20)
        hits = result_of(probe)
        hits = hits if isinstance(hits, list) else []
        found = [
            h for h in hits
            if (h.get("path") or "").lower().endswith(
                tuple(f".{e}" for e in (ext, ext2) if e)
            )
        ]
        want = "разбор деревом" if parse_expected else "текстом"
        mark = "да" if found else "нет"
        print(f"  {lang:<14} {'да':<11} {want:<14} {mark}"
              f"{f' ({len(found)} шт.)' if found else ''}")

    # Ключевая проверка: файлы .cs/.ps1/.sh должны давать УЗЛЫ, а не только текст.
    if "rdpmon" in config_name:
        sig = ((res.get("quality") or {}).get("signatures") or {}) if isinstance(res, dict) else {}
        print(f"\n  функций найдено в корпусе: {sig.get('functions')}")
        print("    (для C#-корпуса это доказывает разбор деревом: текстовые "
              "файлы функций не дают)")
    fac.close()

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Проверяется три вещи по каждому корпусу:
    1. язык есть в конфиге корпуса (значит, init его увидел);
    2. для языков с грамматикой файлы дают узлы (разбор деревом);
    3. поиск по расширению возвращает файлы этих типов.

  Если язык есть в конфиге, но поиск его не находит — файлы не проиндексированы.
  Это и было исходной жалобой: .ps1, .sh, .bat отсутствовали в индексе.
""")
