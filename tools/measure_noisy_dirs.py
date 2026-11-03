"""Замер «шумных» каталогов по ВСЕМ зарегистрированным корпусам.

Зачем. Владелец 28.10.2026 спросил: какие каталоги попадают в корпус, но не
несут полезной нагрузки, и нельзя ли исключить `temp/`, `cache/` по умолчанию.
Ответ «нельзя» или «можно» без замера одинаково бесполезен: базовый список
действует на все корпуса сразу, а имена неоднозначны (в `bin` одного проекта
сборка, в `bin` другого — рабочие скрипты).

Что считается. Сканирование идёт ТЕМ ЖЕ сканером, что и индексация
(`corpus.scan`): иначе числа не сопоставимы с составом индекса. Для каждого
оставленного файла смотрится, есть ли в пути сегмент из списка шумных имён,
и какой это язык. Отвечает на два вопроса:

  * сколько файлов индекса приходит из `temp/`, `cache/`, `logs/` и подобных;
  * есть ли среди них файлы КОДА (php, py, ts, sql…) — то есть потеряется ли
    работа, если исключить каталог целиком.

Ничего не меняет: только чтение.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
from collections import defaultdict

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

#: Имена каталогов, которые по названию кажутся служебными. Список совпадает с
#: `config.NON_DEFAULT_EXCLUDES` плюс `temp`/`tmp` в разных написаниях, потому
#: что именно о них спрашивал владелец.
NOISY = ("bin", "cache", "obj", "publish", "logs", "storage", "uploads",
         "tmp", "temp", "trash", "junk", "coverage", "testresults")

#: Языки, потеря которых = потеря работы. Документы и текст сюда не входят:
#: их отсутствие в индексе не ломает навигацию по коду.
CODE_LANGS = {"php", "python", "javascript", "typescript", "csharp", "perl",
              "bash", "powershell", "ruby", "lua", "go", "java", "c", "cpp",
              "sql"}


def noisy_segment(rel: str) -> str | None:
    """Сегмент пути, совпавший с шумным именем (регистрозависимо)."""
    for seg in rel.split("/"):
        if seg in NOISY:
            return seg
    return None


def main() -> int:
    registry = REPO / "catalog" / "catalog.json"
    data = json.loads(registry.read_text(encoding="utf-8"))
    entries = data.get("entries") or {}

    per_name: dict[str, dict] = defaultdict(
        lambda: {"files": 0, "code": 0, "langs": defaultdict(int),
                 "corpora": set(), "examples": []}
    )
    rows = []
    skipped = []
    t0 = time.time()

    for category, raw in sorted(entries.items()):
        config_path = pathlib.Path(str(raw.get("config") or ""))
        root = pathlib.Path(str(raw.get("root") or ""))
        if not config_path.exists() or not root.is_dir():
            skipped.append((category, "нет конфига или корня"))
            continue
        try:
            cfg = load_config(config_path)
            result = corpus.scan(cfg, digest=False)
        except Exception as exc:  # noqa: BLE001 — замер не должен падать целиком
            skipped.append((category, f"{type(exc).__name__}: {exc}"))
            continue

        total = 0
        hit_files = 0
        hit_code = 0
        by_name: dict[str, int] = defaultdict(int)
        for entry in result.files:
            total += 1
            seg = noisy_segment(entry.path)
            if not seg:
                continue
            hit_files += 1
            by_name[seg] += 1
            lang = (entry.lang or "").lower()
            bucket = per_name[seg]
            bucket["files"] += 1
            bucket["corpora"].add(category)
            bucket["langs"][lang or "?"] += 1
            if lang in CODE_LANGS:
                bucket["code"] += 1
                hit_code += 1
            if len(bucket["examples"]) < 4:
                bucket["examples"].append(f"{category}: {entry.path}")
        rows.append((category, total, hit_files, hit_code, dict(by_name)))

    print(f"корпусов осмотрено: {len(rows)}, пропущено: {len(skipped)}, "
          f"время {time.time() - t0:.1f} с")
    if skipped:
        print("пропущены:", "; ".join(f"{c} ({why})" for c, why in skipped))

    print("\n=== по корпусам: сколько файлов индекса приходит из шумных каталогов ===")
    print(f"{'корпус':32} {'файлов':>7} {'из шумных':>10} {'код':>5}  каталоги")
    rows.sort(key=lambda r: -(r[2]))
    for category, total, hit_files, hit_code, by_name in rows:
        if hit_files == 0:
            continue
        detail = ", ".join(f"{k}={v}" for k, v in
                           sorted(by_name.items(), key=lambda kv: -kv[1]))
        print(f"{category:32} {total:7} {hit_files:10} {hit_code:5}  {detail}")

    print("\n=== сводка по именам каталогов (по всем корпусам) ===")
    print(f"{'имя':12} {'файлов':>7} {'код':>5} {'корпусов':>9}  языки")
    for name, bucket in sorted(per_name.items(), key=lambda kv: -kv[1]["files"]):
        langs = ", ".join(f"{k}={v}" for k, v in
                          sorted(bucket["langs"].items(), key=lambda kv: -kv[1])[:6])
        print(f"{name:12} {bucket['files']:7} {bucket['code']:5} "
              f"{len(bucket['corpora']):9}  {langs}")
        for ex in bucket["examples"][:2]:
            print(f"             пример: {ex}")

    print("\n=== вывод ===")
    safe = [n for n, b in per_name.items() if b["files"] == 0]
    risky = [n for n, b in per_name.items() if b["code"] > 0]
    print("нет ни одного файла ни в одном корпусе:", ", ".join(sorted(safe)) or "—")
    print("содержат файлы кода (исключать глобально нельзя):",
          ", ".join(f"{n}({per_name[n]['code']})" for n in sorted(risky)) or "—")
    junk_only = [n for n, b in per_name.items() if b["files"] > 0 and b["code"] == 0]
    print("только данные/текст, кода нет:", ", ".join(sorted(junk_only)) or "—")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
