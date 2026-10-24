"""Проверка: действительно ли область docs схлопывает только документацию.

Тест сообщил: «область docs: схлопнуто кода 36». Это либо дефект (в области
docs схлопываются файлы кода, чего быть не должно), либо ошибка измерения —
в отчёт попадают только первые 50 групп, и подсчёт по ним не сходится с общим
числом отброшенных.

Проверяется напрямую: вызывается `dedupe.dedupe`, у результата берётся
ПОЛНЫЙ список отброшенных файлов, и по каждому проверяется расширение.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus, dedupe  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

for config_name, title in (("revoap.yaml", "revoap.com"),
                           ("z-batteryservice.yaml", "z"),
                           ("rdpmon.yaml", "RdpMON")):
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    cfg.index.dedupe_enabled = True

    print()
    print("=" * 78)
    print(f"{title}")
    print("=" * 78)

    # Скан БЕЗ схлопывания: нужен полный список файлов для сравнения.
    cfg.index.dedupe_enabled = False
    full = corpus.scan(cfg)
    print(f"  файлов без схлопывания: {len(full.files)}")

    # Схлопывание вызывается напрямую: у результата есть ПОЛНЫЙ список
    # отброшенных файлов, а не обрезанный до 50 групп.
    cfg.index.dedupe_enabled = True
    for scope in ("docs", "all"):
        cfg.index.dedupe_scope = scope
        decision = dedupe.dedupe(full, cfg)
        dropped = decision.dropped_files
        docs = [f for f in dropped if dedupe.is_document(f.path)]
        code = [f for f in dropped if not dedupe.is_document(f.path)]
        print(f"\n  область {scope!r}:")
        print(f"    всего отброшено:      {len(dropped)}")
        print(f"    из них документация:  {len(docs)}")
        print(f"    из них НЕ документация: {len(code)}")
        if code:
            print(f"    примеры не-документов:")
            for f in code[:8]:
                print(f"      {f.path} ({f.lang})")
            print(f"    КОД СХЛОПНУТ — это {'ОШИБКА' if scope == 'docs' else 'ожидаемо'}")
        else:
            print(f"    код не схлопнут — верно")

        # Дополнительно: уникальные файлы не потеряны.
        kept_paths = {f.path for f in decision.kept_files}
        from collections import Counter

        counts = Counter(f.sha256 for f in full.files)
        unique = {sha for sha, n in counts.items() if n == 1}
        lost = [f.path for f in full.files
                if f.sha256 in unique and f.path not in kept_paths]
        print(f"    потеряно уникальных:  {len(lost)}")

print()
print("=" * 78)
print("СВОДКА")
print("=" * 78)
print("""
  Если в области docs отбрасываются только документы (md, markdown, rst, txt),
  а не-документов ноль — реализация верна, а расхождение дал подсчёт по
  обрезанному списку групп в отчёте.
""")
