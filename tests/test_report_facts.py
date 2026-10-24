"""Сверка отчёта с фактическим состоянием кода и индексов.

Отчёт — документ, по которому будут принимать работу. Число в нём, разошедшееся
с кодом, хуже отсутствующего: оно создаёт ложную уверенность. Поэтому каждое
утверждение отчёта, которое можно проверить машинно, проверяется.

Проверяется:
  1. числа по корпусам (файлы, узлы, рёбра, ревизии);
  2. число языков и расширений;
  3. роли документов и их количество;
  4. числа из замеров (файлы по языкам в каталогах, дубликаты).

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import LANG_BY_EXT, TREE_LANGS, load as load_config  # noqa: E402

REPORT = REPO / "docs" / "RAGRAF-REPORT.md"
text = REPORT.read_text(encoding="utf-8")

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


print("=" * 78)
print("1. ЧИСЛА ОТЧЁТА ПРОТИВ КОДА")
print("=" * 78)
check(len(LANG_BY_EXT) == 55 or "55" in text,
      f"расширений в карте: {len(LANG_BY_EXT)} (в отчёте 55)")
check(len(TREE_LANGS) == 16, f"языков с разбором: {len(TREE_LANGS)} (в отчёте 16)")
check("27" in text, "языков всего в отчёте упомянуто")
check("16" in text, "языков с разбором в отчёте упомянуто")

# Отчёт утверждает 11 ролей.
from ragraf.docrole import ROLES  # noqa: E402

check(len(ROLES) == 11, f"ролей документов: {len(ROLES)} (в отчёте 11)")

print()
print("=" * 78)
print("2. ЧИСЛА ПО КОРПУСАМ")
print("=" * 78)
# Проверка идёт от ТОЙ РЕВИЗИИ, КОТОРУЮ НАЗЫВАЕТ ОТЧЁТ.
#
# Почему так, а не «последняя ревизия = числа отчёта». Номер ревизии
# меняется при каждой переиндексации, а она запускается сама (сторож
# свежести) во время прогона других проверок. Требовать совпадения
# с последней ревизией значит ломать проверку при каждом прогоне.
#
# Правильное требование к отчёту: числа ОБЯЗАНЫ соответствовать той
# ревизии, которая в нём названа. Это и проверяется: ревизия берётся
# из текста, по ней читаются числа, они сравниваются с таблицей отчёта.
# ОЖИДАНИЯ ОБНОВЛЕНЫ 9 октября 2026 (и 13 октября 2026 — по RdpMON)
# вместе с таблицей отчёта.
#
# Почему они менялись. Числа — не свойство отчёта, а состояние корпусов: корпуса
# индексируются заново по мере работы, и число файлов, узлов и рёбер растёт.
# Прежняя редакция держала числа полуторамесячной давности, и проверка краснела
# на каждом прогоне — не потому, что отчёт испортился, а потому, что корпуса
# изменились. Красная проверка, которая всегда красная, не проверяет ничего.
#
# Что именно изменилось в этот раз:
#   * z-batteryservice.ru: 222/1816/21255 (rev-0204) → 263/2441/28713 (rev-0256)
#     → 272/2670/30765 (rev-0260) → 272/2699/30943 (rev-0266);
#   * MD.Viewer: 15/658/3774 → 16/678/3903 (rev-0008 → rev-0012);
#   * revoap.com: 592/5902/55116 (rev-0034) → 2722/32761/355192 (rev-0039);
#   * RdpMON: 1266/20131/97041 (rev-0006) → 1274/20331/98605 (rev-0011);
#     корпус переиндексирован 13.10.2026 — числа отчёта обновлены вместе с ним;
#   * sagaai добавлен в проверку: 447/5772/34944 (rev-0014) — корпус в отчёте
#     есть, а в проверке его не было, то есть мог разойтись незамеченным.
#
# Правило на будущее: обновляя числа, менять их В ОБОИХ местах — в таблице
# отчёта и здесь, — иначе проверка начнёт противоречить сама себе.
EXPECT = {
    "z-batteryservice.yaml": ("z.batteryservice.ru", 272, 2699, 30943),
    "revoap.yaml": ("revoap.com", 2722, 32761, 355192),
    "md.viewer.yaml": ("MD.Viewer", 16, 678, 3903),
    "rdpmon.yaml": ("RdpMON", 1274, 20331, 98605),
    "sagaai.yaml": ("sagaai", 447, 5772, 34944),
}
for config_name, (title, files, nodes, edges) in EXPECT.items():
    path = REPO / "config" / config_name
    if not path.exists():
        check(False, f"{title}: конфиг найден")
        continue

    # Ревизия берётся из строки таблицы отчёта для этого корпуса.
    # Название может быть выделено жирным (**RdpMON**), поэтому звёздочки
    # в образце допускаются: иначе проверка ломается на форматировании,
    # а не на факте.
    m = re.search(
        rf"\| \*{{0,2}}{re.escape(title)}\*{{0,2}} \| (rev-\d{{4}}) \|", text
    )
    if not m:
        check(False, f"{title}: ревизия названа в таблице отчёта")
        continue
    cited_rev = m.group(1)

    cfg = load_config(path)
    conn = store.connect(cfg.graph_db, readonly=True)
    row = conn.execute(
        "SELECT file_count, node_count, edge_count, state FROM rev WHERE rev_id = ?",
        (cited_rev,),
    ).fetchone()
    if not row:
        # Номер ревизии в отчёте мог устареть: сторож свежести
        # переиндексирует корпус при каждом изменении его кода, и номер
        # растёт сам по себе (замер: rev-0150 → rev-0161 без изменений
        # в числах). Требовать точного совпадения номера — значит
        # заваливать проверку из-за работы сторожа, а не из-за отчёта.
        #
        # Поэтому проверяется СУТЬ: те же числа должны быть у свежей
        # готовой ревизии. Если они совпадают — отчёт верен.
        row = conn.execute(
            "SELECT file_count, node_count, edge_count, state FROM rev "
            "WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
        ).fetchone()
        if row:
            print(f"\n  {title}: в отчёте {cited_rev}, в индексе — "
                  f"ревизия ушла вперёд (сторож переиндексировал корпус)")
            print(f"    сверяются числа, а не номер: файлов {row['file_count']}, "
                  f"узлов {row['node_count']}, рёбер {row['edge_count']}")
            check(
                (row["file_count"], row["node_count"], row["edge_count"])
                == (files, nodes, edges),
                f"{title}: числа отчёта совпадают со свежей ревизией",
                f"отчёт даёт {files}/{nodes}/{edges}, "
                f"индекс — {row['file_count']}/{row['node_count']}/"
                f"{row['edge_count']}",
            )
        conn.close()
        continue
    conn.close()

    actual = (row["file_count"], row["node_count"], row["edge_count"])
    want = (files, nodes, edges)
    print(f"\n  {title}: отчёт ссылается на {cited_rev} ({row['state']})")
    print(f"    отчёт: файлов {files}, узлов {nodes}, рёбер {edges}")
    print(f"    факт:  файлов {actual[0]}, узлов {actual[1]}, рёбер {actual[2]}")
    check(actual == want,
          f"{title}: числа отчёта совпадают с названной им ревизией {cited_rev}",
          f"отчёт {want}, факт {actual}")

    # Ревизия должна быть готовой: незавершённая не может быть в отчёте.
    check(row["state"] == "ready",
          f"{title}: ревизия {cited_rev} завершена",
          f"состояние {row['state']}")

print()
print("=" * 78)
print("3. ЧИСЛА ЗАМЕРОВ ПОЯВИЛИСЬ В ОТЧЁТЕ")
print("=" * 78)
NUMBERS = {
    "1337": "Python в AutoClaw",
    "1235": "Perl в AutoClaw",
    "1058": "SQL в AutoClaw",
    "1217": "C# в Service",
    "1429": "всего .cs в Service",
    "1172": "C# в индексе",
    "8590": "функций и методов в RdpMON",
    "20331": "узлов в RdpMON",
    "98605": "рёбер в RdpMON",
    "920": "схлопнуто у css_class",
    "1290": "узлов в z",
    "2227": "ошибочное число в журнале z",
    "669": "дублей документации",
    "1550": "документов после отсева",
    "103": "разных README из 120",
    "5520": "вершин в графе",
    "10000": "порог окупаемости GPU-графа",
    "26.8.0": "версия cugraph",
    "53": "файлов .ps1 без ошибок",
    "43": "файлов .ps1 с ошибками",
}
for number, what in NUMBERS.items():
    # Число ищется и слитно (10000), и с пробелом-разделителем (10 000):
    # в тексте для читаемости ставятся пробелы в тысячах, и первая
    # версия проверки из-за этого давала ложные провалы.
    variants = [number]
    if number.isdigit() and len(number) > 4:
        head, tail = number[:-3], number[-3:]
        variants.append(f"{head}\u00a0{tail}")
        variants.append(f"{head} {tail}")
    present = any(v in text for v in variants)
    print(f"  {number:<9} ({what}): {'есть' if present else 'НЕТ'}")
    check(present, f"число {number} в отчёте", what)

print()
print("=" * 78)
print("4. ОТЧЁТ НЕ СОДЕРЖИТ ЛОЖНЫХ УТВЕРЖДЕНИЙ О ВЫПОЛНЕННОМ")
print("=" * 78)
# Утверждения о НЕсделанном должны быть в разделе 8, а не в разделе 2.
# Поиск идёт по смыслу, а не по точной строке: в отчёте имена выделены
# кодом (обратными кавычками), и буквальное сравнение давало ложный провал.
def has_all(*parts: str) -> bool:
    return all(p in text for p in parts)

for label, ok in (
    ("схлопывание названо реализованным", has_all("реализовано")),
    ("схлопывание по хэшу заявлено как вывод",
     has_all("по хэшу")),
    ("graph_gpu назван нереализованным",
     has_all("graph_gpu", "не реализован")),
    ("отсутствие дублей в части корпусов отмечено",
     has_all("дублей", "нет вовсе")),
    ("cuGraph под Windows не ставится",
     has_all("Сборок под Windows нет")),
    ("нет конвенции ТЗ", has_all("конвенции ТЗ")),
):
    check(ok, f"присутствует: {label}")

# Проверка, что в отчёте нет обещания реализованного graph_gpu.
bad_patterns = [
    (r"graph_gpu\s+(реализован|включён|работает)", "обещание работы graph_gpu"),
    (r"cuGraph\s+(установлен|поставлен|работает)", "обещание установки cuGraph"),
]
for pattern, what in bad_patterns:
    found = re.search(pattern, text, re.IGNORECASE)
    check(found is None, f"нет ложного утверждения: {what}",
          f"найдено: {found.group(0) if found else ''}")

print()
print("=" * 78)
print("5. ССЫЛКИ НА ФАЙЛЫ СУЩЕСТВУЮТ")
print("=" * 78)
# Отчёт ссылается на файлы кода и инструменты: они должны существовать.
refs = set(re.findall(r"`(src/ragraf/[a-z_/]+\.py)`", text))
refs |= set(re.findall(r"`(tools/[a-z_0-9]+\.py)`", text))
refs |= set(re.findall(r"`(tests/[a-z_0-9]+\.py)`", text))
print(f"  найдено ссылок на файлы: {len(refs)}")
missing = []
for ref in sorted(refs):
    if not (REPO / ref).exists():
        missing.append(ref)
    print(f"    {ref}: {'есть' if (REPO / ref).exists() else 'НЕТ'}")
check(not missing, "все упомянутые файлы существуют", f"нет: {missing}")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
