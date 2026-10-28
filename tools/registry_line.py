#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Строка реестра в документах: сверка и генерация — одна реализация на всех.

Измеренный дефект (28.10.2026, ПРОМТ-И33). README и пять переводов называют
текущий размер реестра одной строкой: «по реестру на ДД.ММ.ГГГГ: N корпусов,
M проиндексированы». Числа росли, дата не двигалась, и ДВА сторожа (проверка
чекера и тест цикла качества) держали эту строку ТОЧНЫМ равенством с датой
обновления `catalog/catalog.json`. Но `updated_at` реестра двигается при
индексации ЛЮБОГО корпуса, то есть дата меняется без изменения состава.

Следствие: живой документ, честно называющий вчерашнюю дату, объявлялся ложью
и валил прогон наравне с выдуманными числами; лечили это переписыванием даты
руками в шести файлах, и лечение не держалось до следующего пересбора.

Поэтому изменён КЛАСС УТВЕРЖДЕНИЯ, а не текст:

    документ совпал с реестром                       -> молчание;
    документ отстал  (дата старше даты реестра при
    том же составе, либо состав из журнала реестра)  -> предупреждение с датой;
    документ врёт    (такого состояния не было)      -> ошибка.

Точное равенство оставлено там, где оно нужно: ключ `--strict` (публикация)
превращает отставание в отказ — наружу уходит только сверенный замер.

Строка больше не пишется руками: `--write` берёт числа и дату из
`catalog/catalog.json` и переписывает их ВО ВСЕХ ШЕСТИ файлах, сохраняя
формулировку каждого языка. Ручная правка шести файлов — источник следующего
расхождения; заменяются только заменяемые куски.

Тем же прогоном переписываются РАЗМЕРЫ ИНДЕКСА и запись ревизии, из которой они
взяты (`rev-XXXX`, её дата, файлы/узлы/рёбра). Это второй подвижный литерал
того же класса: он сдвигается от добавления одного файла. Источник — запись
ревизии (`rev_history`), а не документ и не память. Проверяют эти числа
по-прежнему существующие сторожа (`Test-DocumentedNumbers` и
`tools/check_translations.py`): генератор их только ЗАПИСЫВАЕТ, а второй
редакции сверки не заводит.

Запуск:
    python tools/registry_line.py                       (сверка, код 1 при находках)
    python tools/registry_line.py --verify --strict      (как на публикации)
    python tools/registry_line.py --write               (переписать строку в шести файлах)
    python tools/registry_line.py --root <каталог> --json

Коды возврата: 0 — расхождений нет (при `--strict` и предупреждений нет);
1 — находки (или предупреждения при `--strict`); 2 — сверить не с чем:
нет реестра, документа, шаблон языка не совпал.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]

# Каталог скрипта выкидывается из sys.path: файл `tools/ragraf.py` иначе
# затеняет пакет `src/ragraf` (измерено 12.10.2026).
_TOOLS_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if str(Path(p or ".").resolve()) != _TOOLS_DIR]


@dataclass(frozen=True)
class Document:
    """Один документ, называющий размер реестра.

    `pattern` обязан ловить три заменяемых куска именованными группами
    `date`, `corpora`, `indexed`. Формулировка языка — часть документа, а не
    шаблона: генератор меняет только значения, поэтому переводы не затираются.
    """

    path: str
    language: str
    date_format: str      # как дата записана в документе
    pattern: re.Pattern[str]


# Шесть файлов. Список ЯВНЫЙ: документ, выпавший из него, никто не заметит, а
# «перевод отстал» — ровно тот класс дефекта, ради которого всё это написано.
DOCUMENTS: tuple[Document, ...] = (
    Document(
        "README.md", "en", "%d.%m.%Y",
        re.compile(r"by the registry on (?P<date>\d{2}\.\d{2}\.\d{4})"
                   r"(?P<mid>[^\d]{0,80}?)(?P<corpora>\d+)\s+corpora,\s*"
                   r"(?P<indexed>\d+)\s+indexed"),
    ),
    Document(
        "community/README.ru.md", "ru", "%d.%m.%Y",
        re.compile(r"по реестру на (?P<date>\d{2}\.\d{2}\.\d{4})"
                   r"(?P<mid>[^\d]{0,80}?)(?P<corpora>\d+)\s+корпус\w*,\s*"
                   r"(?P<indexed>\d+)\s+проиндексирован\w*"),
    ),
    Document(
        "community/README.de.md", "de", "%d.%m.%Y",
        re.compile(r"nach dem Register am (?P<date>\d{2}\.\d{2}\.\d{4})"
                   r"(?P<mid>[^\d]{0,80}?)(?P<corpora>\d+)\s+Korpora,\s*"
                   r"(?P<indexed>\d+)\s+indiziert"),
    ),
    Document(
        "community/README.es.md", "es", "%d.%m.%Y",
        re.compile(r"según el registro el (?P<date>\d{2}\.\d{2}\.\d{4})"
                   r"(?P<mid>[^\d]{0,80}?)(?P<corpora>\d+)\s+corpus,\s*"
                   r"(?P<indexed>\d+)\s+indexados"),
    ),
    Document(
        "community/README.hi.md", "hi", "%d.%m.%Y",
        re.compile(r"(?P<date>\d{2}\.\d{2}\.\d{4})\s+को रजिस्ट्री के अनुसार"
                   r"(?P<mid>[^\d]{0,80}?)(?P<corpora>\d+)\s+कॉर्पस,\s*"
                   r"(?P<indexed>\d+)\s+इंडेक्स"),
    ),
    Document(
        "community/README.zh.md", "zh", "%Y-%m-%d",
        re.compile(r"按 (?P<date>\d{4}-\d{2}-\d{2})(?P<mid>[^\d]{0,40}?)"
                   r"(?P<corpora>\d+)\s*个语料[、,]\s*(?P<indexed>\d+)\s*个已索引"),
    ),
)

#: Договор для снимка полигона в README: дата есть, оговорка «не текущее
#: состояние» рядом, оборота текущего состояния нет.
SNAPSHOT_PATTERN = re.compile(r"snapshot of (?P<date>\d{2}\.\d{2}\.\d{4})")
SNAPSHOT_CAVEAT = "not the current state"
SNAPSHOT_COUNTER_WORDING = re.compile(r"as of today|on the current date")

#: Таблица источников поставки: объявляет sha256 каждого файла обвязки и признак
#: «совпадает с публичным main». Правка перевода меняет его хэш, поэтому
#: значения обязаны пересчитываться тем же прогоном, что и строка реестра.
SOURCES_DOC = "docs/03_implementation/RELEASE-SOURCES.md"


@dataclass(frozen=True)
class Snapshot:
    """Размеры индекса и запись ревизии — по языку документа.

    `numbers` ловит три числа: файлы, узлы, рёбра. Номер ревизии здесь
    намеренно НЕ пишется: документы входят в корпус, поэтому вписанный номер
    сдвигает контрольную сумму и порождает следующую ревизию — литерал, который
    нельзя удержать никаким механизмом (измерено 28.10.2026).
    """

    path: str
    language: str
    date_format: str
    numbers: re.Pattern[str]


SNAPSHOTS: tuple[Snapshot, ...] = (
    Snapshot(
        "README.md", "en", "%d.%m.%Y",
        re.compile(r"the RAGRAF index itself — (?P<files>\d{1,3}(?: \d{3})*) files,\s*"
                   r"(?P<nodes>\d{1,3}(?: \d{3})*) nodes\s+and\s+"
                   r"(?P<edges>\d{1,3}(?: \d{3})*) edges"),
    ),
    Snapshot(
        "community/README.ru.md", "ru", "%d.%m.%Y",
        re.compile(r"индекс RAGRAF: файлов — (?P<files>\d{1,3}(?: \d{3})*), "
                   r"узлов — (?P<nodes>\d{1,3}(?: \d{3})*), "
                   r"рёбер — (?P<edges>\d{1,3}(?: \d{3})*)"),
    ),
    Snapshot(
        "community/README.de.md", "de", "%d.%m.%Y",
        re.compile(r"Index von RAGRAF — (?P<files>\d{1,3}(?: \d{3})*) Dateien, "
                   r"(?P<nodes>\d{1,3}(?: \d{3})*) Knoten und "
                   r"(?P<edges>\d{1,3}(?: \d{3})*) Kanten"),
    ),
    Snapshot(
        "community/README.es.md", "es", "%d.%m.%Y",
        re.compile(r"índice propio de RAGRAF — (?P<files>\d{1,3}(?: \d{3})*) archivos, "
                   r"(?P<nodes>\d{1,3}(?: \d{3})*) nodos y "
                   r"(?P<edges>\d{1,3}(?: \d{3})*) aristas"),
    ),
    Snapshot(
        "community/README.hi.md", "hi", "%d.%m.%Y",
        re.compile(r"अपना इंडेक्स — (?P<files>\d{1,3}(?: \d{3})*) फ़ाइलें, "
                   r"(?P<nodes>\d{1,3}(?: \d{3})*) नोड और "
                   r"(?P<edges>\d{1,3}(?: \d{3})*) किनारे"),
    ),
    Snapshot(
        "community/README.zh.md", "zh", "%Y-%m-%d",
        re.compile(r"自身索引——(?P<files>\d{1,3}(?: \d{3})*) 个文件、"
                   r"(?P<nodes>\d{1,3}(?: \d{3})*) 个节点、"
                   r"(?P<edges>\d{1,3}(?: \d{3})*) 条边"),
    ),
)


def revision_facts(root: Path, config_rel: str) -> dict:
    """Активная ревизия: номер, дата и размеры — из `rev_history`, только чтение.

    Порядок задаётся `rev_id DESC`, а не временем: активная ревизия
    определяется нумерацией, часы машины в этом не участвуют (AGENTS.md, 4).
    """
    from ragraf import store  # noqa: PLC0415 — путь к пакету добавлен в main
    from ragraf.config import load as load_config  # noqa: PLC0415

    cfg = load_config(root / config_rel)
    conn = store.connect(cfg.control_db, readonly=True)
    try:
        row = conn.execute(
            "SELECT rev_id, created_at, files, nodes, edges FROM rev_history "
            "WHERE project = ? AND files IS NOT NULL AND state IN ('ready','rotated') "
            "ORDER BY rev_id DESC LIMIT 1", (cfg.project,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError(f"в rev_history нет ревизии проекта {cfg.project}: "
                           f"размеры индекса взять неоткуда")
    return {"rev_id": str(row["rev_id"]), "date": str(row["created_at"])[:10],
            "files": int(row["files"]), "nodes": int(row["nodes"]),
            "edges": int(row["edges"])}


def _thousands(value: int) -> str:
    """Число в том же виде, что в документах: 5 309, а не 5309 и не 5,309."""
    return f"{value:,}".replace(",", " ")


def corpora_ru(count: int) -> str:
    """«1 корпус», «2 корпуса», «42 корпуса», «5 корпусов» — для сообщений."""
    tail = abs(int(count)) % 100
    if 11 <= tail <= 14:
        return f"{count} корпусов"
    tail %= 10
    if tail == 1:
        return f"{count} корпус"
    if 2 <= tail <= 4:
        return f"{count} корпуса"
    return f"{count} корпусов"


def _iso_to_display(iso: str, date_format: str) -> str:
    if not iso:
        return iso
    year, month, day = iso.split("-")
    if date_format == "%Y-%m-%d":
        return f"{year}-{month}-{day}"
    return f"{day}.{month}.{year}"


def _display_to_iso(text: str, date_format: str) -> str:
    if date_format == "%Y-%m-%d":
        return text
    day, month, year = text.split(".")
    return f"{year}-{month}-{day}"


def registry_facts(root: Path) -> dict:
    """Числа и дата реестра — из файла реестра, а не из документа."""
    from ragraf import registry_facts as facts  # noqa: PLC0415 — путь добавлен в main

    catalog_path = root / "catalog" / "catalog.json"
    if not catalog_path.exists():
        raise RuntimeError(f"нет {catalog_path}: сверить строку реестра не с чем")
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    state = facts.registry_state(data.get("entries") or {})
    stamp = str(data.get("updated_at") or "")
    if not stamp[:10]:
        raise RuntimeError(f"в {catalog_path} нет updated_at: дату строки взять неоткуда")
    return {
        "date": stamp[:10],
        "updated_at": stamp,
        "corpora": state["corpora"],
        "indexed": state["indexed"],
        "history": facts.normalize_state(facts.read_history(catalog_path)),
    }


def classify(stated: dict, known: dict) -> tuple[str, str]:
    """Три исхода одной строки: `ok`, `lag`, `lie` — и причина словами."""
    date = stated["date"]
    corpora = stated["corpora"]
    indexed = stated["indexed"]
    current_date = known["date"]
    current = (known["corpora"], known["indexed"])

    if date > current_date:
        return "lie", (f"дата {date} впереди даты реестра {current_date} "
                       f"({known['updated_at']}): замер из будущего — это не измерение")

    if (corpora, indexed) == current:
        if date == current_date:
            return "ok", ""
        return "lag", (f"отстала дата: документ называет {date}, а реестр обновлён "
                       f"{current_date} ({known['updated_at']}); состав тот же — "
                       f"{corpora_ru(corpora)}, {indexed} проиндексированы, "
                       f"поэтому это устаревший замер, а не расхождение")

    for record in known["history"]:
        if (record["corpora"], record["indexed"]) == (corpora, indexed):
            return "lag", (f"отстал состав: документ называет {corpora_ru(corpora)} и "
                           f"{indexed} проиндексированных, такое состояние реестра "
                           f"записано {record['recorded_at']}; сейчас — "
                           f"{known['corpora']} и {known['indexed']} "
                           f"(реестр обновлён {current_date})")

    return "lie", (f"документ называет {corpora_ru(corpora)} и {indexed} "
                   f"проиндексированных на {date}, а реестр — {known['corpora']} и "
                   f"{known['indexed']} на {current_date}; такого состава нет ни в "
                   f"одной из {len(known['history'])} записей журнала реестра: "
                   f"это не отставание, а вымысел")


def inspect_document(root: Path, doc: Document, known: dict) -> dict:
    """Один документ: где строка, что она называет, каков исход."""
    entry: dict = {"path": doc.path, "language": doc.language}
    path = root / doc.path
    if not path.exists():
        entry.update(verdict="absent",
                     message=f"нет файла {doc.path}: строка реестра в нём не проверена")
        return entry

    text = path.read_text(encoding="utf-8")
    found = list(doc.pattern.finditer(text))
    if not found:
        entry.update(verdict="absent",
                     message=(f"{doc.path} перестал называть размер реестра: шаблон "
                              f"языка «{doc.language}» не найден — заявление исчезло "
                              f"или переписано так, что сверка его больше не видит"))
        return entry
    if len(found) > 1:
        entry.update(verdict="lie",
                     message=(f"{doc.path} называет размер реестра {len(found)} раза: "
                              f"читатель не может понять, какое число верно"))
        return entry

    match = found[0]
    stated = {
        "date": _display_to_iso(match.group("date"), doc.date_format),
        "corpora": int(match.group("corpora")),
        "indexed": int(match.group("indexed")),
    }
    verdict, message = classify(stated, known)
    entry.update(line=text.count("\n", 0, match.start()) + 1,
                 stated=stated, verdict=verdict, message=message)
    return entry


def inspect_snapshot(root: Path) -> dict:
    """Договор снимка полигона: дата, оговорка, отсутствие оборота «сегодня»."""
    entry: dict = {"path": "README.md", "what": "snapshot of the test bed"}
    path = root / "README.md"
    if not path.exists():
        entry.update(verdict="lie", message="нет README.md: снимок полигона сверить не с чем")
        return entry
    text = path.read_text(encoding="utf-8").replace("\u00a0", " ")
    match = SNAPSHOT_PATTERN.search(text)
    if match is None:
        entry.update(verdict="lie",
                     message=("снимок полигона в README не помечен датой: замер "
                              "16.10.2026 читается как текущее состояние"))
        return entry
    tail = text[match.start():match.start() + 600]
    if SNAPSHOT_CAVEAT not in tail:
        entry.update(verdict="lie", date=match.group("date"),
                     message=("снимок полигона помечен датой, но не сказано, что числа "
                              "не текущие: дата без этой оговорки не отделяет замер от "
                              "сегодняшнего дня"))
        return entry
    if SNAPSHOT_COUNTER_WORDING.search(tail):
        entry.update(verdict="lie", date=match.group("date"),
                     message=("рядом со снимком полигона стоит оборот текущего "
                              "состояния: замер выдан за сегодняшний"))
        return entry
    entry.update(verdict="ok", date=match.group("date"), message="")
    return entry


def verify(root: Path, known: dict) -> dict:
    """Полная сверка: шесть документов, взаимное согласие, снимок полигона."""
    problems: list[str] = []
    lags: list[str] = []
    notes: list[str] = []

    documents = []
    for doc in DOCUMENTS:
        entry = inspect_document(root, doc, known)
        documents.append(entry)
        where = f"{doc.path}:{entry['line']}" if entry.get("line") else doc.path
        if entry["verdict"] == "lie":
            problems.append(f"{where}: {entry['message']}")
        elif entry["verdict"] == "lag":
            lags.append(f"{where}: {entry['message']}")
        elif entry["verdict"] == "absent":
            problems.append(f"{where}: {entry['message']}")

    # Взаимное согласие. Даже в состоянии «отстал» все шесть обязаны называть
    # одно и то же: перевод с другим числом — не вольность, а расхождение.
    stated = {d["path"]: (d["stated"]["date"], d["stated"]["corpora"], d["stated"]["indexed"])
              for d in documents if d.get("stated")}
    distinct = set(stated.values())
    if len(distinct) > 1:
        detail = ", ".join(f"{path} — {value[0]} / {value[1]} / {value[2]}"
                           for path, value in sorted(stated.items()))
        problems.append(f"документы расходятся между собой по строке реестра: {detail}; "
                        f"перевод с другим числом — расхождение, а не вольность")

    snapshot = inspect_snapshot(root)
    if snapshot["verdict"] != "ok":
        problems.append(f"README.md: {snapshot['message']}")

    if not known["history"]:
        notes.append("журнал прежних состояний реестра пуст: отставание по составу "
                     "отличимо от вымысла только после первой смены состава")

    examined = len(documents) + 1 + 1    # документы, строка реестра, снимок
    return {"registry": {k: known[k] for k in ("date", "updated_at", "corpora", "indexed")},
            "documents": documents,
            "snapshot": snapshot,
            "agree": len(distinct) <= 1,
            "stated": [{"path": path, "date": value[0], "corpora": value[1],
                        "indexed": value[2]} for path, value in sorted(stated.items())],
            "problems": problems, "lags": lags, "notes": notes,
            "examined": examined}


def _table_rows(text: str) -> list[list[str]]:
    """Строки markdown-таблицы по шесть ячеек: файл, источник, два хэша, признак, путь."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        body = line.rstrip()
        if not body.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in body.strip().strip("|").split("|")]
        if len(cells) == 6:
            rows.append(cells)
    return rows


def refresh_sources_table(root: Path, changed: list[str]) -> dict:
    """Пересчитать хэши изменённых документов в таблице источников поставки.

    Меняются ДВА столбца: «sha256 в дереве» и «совпадает». Столбец «sha256 в
    публичном main» не трогается никогда: это утверждение о внешнем
    репозитории, и генератор его не проверяет — только сравнивает с ним. Пока
    правка не опубликована, честное значение признака — «нет»; закрывает его
    публикация, пересчитывая таблицу на новой голове (порядок описан в самом
    документе).
    """
    report: dict = {"path": SOURCES_DOC, "rows": [], "problems": []}
    path = root / SOURCES_DOC
    if not path.exists():
        report["problems"].append(
            f"нет {SOURCES_DOC}: объявленные хэши переводов не пересчитаны, "
            f"и гейт объявит расхождение")
        return report

    wanted = set(changed)
    original = path.read_text(encoding="utf-8")
    declared = {cells[0] for cells in _table_rows(original)}
    # Таблица объявляет ПЕРЕНОС обвязки (community/**, scripts/**), а README.md
    # в неё не входит. Отсутствие строки — наблюдение, а не отказ: полноту
    # таблицы сторожит tools/check_community_wrapper.py, он и падает на файле
    # community/ без строки.
    for name in sorted(wanted - declared):
        report.setdefault("notes", []).append(
            f"{SOURCES_DOC}: {name} в таблице источников не объявлен — "
            f"хэш не пересчитывался")

    lines: list[str] = []
    for line in original.splitlines(keepends=True):
        body = line.rstrip("\n")
        if body.lstrip().startswith("|"):
            cells = [cell.strip() for cell in body.strip().strip("|").split("|")]
            if len(cells) == 6 and cells[0] in wanted:
                actual = hashlib.sha256((root / cells[0]).read_bytes()).hexdigest()
                same = "да" if cells[2].lower() == actual else "нет"
                if cells[3].lower() != actual or cells[4].lower() != same:
                    cells[3] = actual
                    cells[4] = same
                    report["rows"].append({"path": cells[0], "same": same})
                    lines.append("| " + " | ".join(cells) + " |" +
                                 ("\n" if line.endswith("\n") else ""))
                    continue
        lines.append(line)

    updated = "".join(lines)
    if updated != original:
        path.write_bytes(updated.encode("utf-8"))
    return report


def write_snapshots(root: Path, revision: dict) -> dict:
    """Переписать размеры индекса и запись ревизии в шести документах.

    Заменяются пять кусков: номер ревизии, её дата и три числа. Формулировка
    языка не трогается — она часть документа.
    """
    changed: list[dict] = []
    unchanged: list[str] = []
    problems: list[str] = []
    wanted = {
        "files": _thousands(revision["files"]),
        "nodes": _thousands(revision["nodes"]),
        "edges": _thousands(revision["edges"]),
    }

    for snapshot in SNAPSHOTS:
        path = root / snapshot.path
        if not path.exists():
            problems.append(f"нет файла {snapshot.path}: размеры индекса записывать некуда")
            continue
        text = path.read_text(encoding="utf-8")
        spans: list[tuple[int, int, str]] = []
        found = list(snapshot.numbers.finditer(text))
        if len(found) != 1:
            problems.append(f"{snapshot.path}: шаблон языка «{snapshot.language}» "
                            f"совпал {len(found)} раз вместо одного — писать вслепую нельзя")
            continue
        match = found[0]
        for group in ("files", "nodes", "edges"):
            spans.append((*match.span(group), wanted[group]))
        for start, end, replacement in sorted(spans, reverse=True):
            text = text[:start] + replacement + text[end:]
        updated = path.read_text(encoding="utf-8")
        if text == updated:
            unchanged.append(snapshot.path)
            continue
        path.write_bytes(text.encode("utf-8"))
        changed.append({"path": snapshot.path, "language": snapshot.language,
                        "files": revision["files"], "nodes": revision["nodes"],
                        "edges": revision["edges"]})
    return {"changed": changed, "unchanged": unchanged, "problems": problems}


def write(root: Path, known: dict, revision: dict | None = None) -> dict:
    """Переписать строку реестра в шести файлах. Возвращает отчёт об изменении."""
    changed: list[dict] = []
    unchanged: list[str] = []
    problems: list[str] = []
    notes: list[str] = []

    for doc in DOCUMENTS:
        path = root / doc.path
        if not path.exists():
            problems.append(f"нет файла {doc.path}: строку реестра записывать некуда")
            continue
        text = path.read_text(encoding="utf-8")
        found = list(doc.pattern.finditer(text))
        if len(found) != 1:
            problems.append(f"{doc.path}: шаблон языка «{doc.language}» совпал "
                            f"{len(found)} раз вместо одного — писать вслепую нельзя")
            continue
        match = found[0]
        pieces: list[str] = []
        last = 0
        replacements = (
            ("date", _iso_to_display(known["date"], doc.date_format)),
            ("corpora", str(known["corpora"])),
            ("indexed", str(known["indexed"])),
        )
        for group, value in replacements:
            start, end = match.span(group)
            pieces.append(text[last:start])
            pieces.append(value)
            last = end
        pieces.append(text[last:])
        updated = "".join(pieces)
        if updated == text:
            unchanged.append(doc.path)
            continue
        path.write_bytes(updated.encode("utf-8"))
        changed.append({"path": doc.path, "language": doc.language,
                        "line": text.count("\n", 0, match.start()) + 1})

    # Хэши пересчитываются для ВСЕХ шести документов, а не только для тех, где
    # строка изменилась: расхождение в таблице приходит и не от строки реестра
    # (правка перевода руками, смена шапки), и тогда `--write` обязан его снять.
    snapshots = write_snapshots(root, revision) if revision else {
        "changed": [], "unchanged": [], "problems": []}
    problems.extend(snapshots["problems"])

    sources = refresh_sources_table(root, [doc.path for doc in DOCUMENTS])
    problems.extend(sources["problems"])
    notes.extend(sources.get("notes") or [])
    return {"changed": changed, "unchanged": unchanged,
            "snapshots": snapshots, "sources": sources,
            "problems": problems, "notes": notes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="строка реестра в документах: сверка и генерация")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="корень проекта")
    parser.add_argument("--write", action="store_true",
                        help="переписать строку реестра в шести файлах из реестра")
    parser.add_argument("--verify", action="store_true", help="только сверка (по умолчанию)")
    parser.add_argument("--config", default="config/ragraf.yaml",
                        help="конфиг корпуса для записи размеров индекса")
    parser.add_argument("--strict", action="store_true",
                        help="отставание считать находкой (режим публикации)")
    parser.add_argument("--json", action="store_true", help="машинный ответ")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    try:
        known = registry_facts(root)
    except Exception as exc:  # noqa: BLE001 — причина отказа важнее типа
        payload = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload["error"])
        return 2

    if args.write:
        try:
            revision = revision_facts(root, args.config)
        except Exception as exc:  # noqa: BLE001 — причина отказа важнее типа
            payload = {"error": f"активная ревизия не прочитана: {type(exc).__name__}: {exc}"}
            print(json.dumps(payload, ensure_ascii=False) if args.json else payload["error"])
            return 2
        report = write(root, known, revision)
        report["registry"] = {k: known[k] for k in ("date", "updated_at", "corpora", "indexed")}
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=1))
        else:
            for item in report["changed"]:
                print(f"  записано: {item['path']}:{item['line']} ({item['language']})")
            for name in report["unchanged"]:
                print(f"  без изменений: {name}")
            for item in (report.get("snapshots") or {}).get("changed", []):
                print(f"  размеры индекса: {item['path']} — "
                      f"{item['files']} / {item['nodes']} / {item['edges']}")
            for row in (report.get("sources") or {}).get("rows", []):
                print(f"  хэш в таблице источников: {row['path']} — "
                      f"совпадает с main: {row['same']}")
            for problem in report["problems"]:
                print(f"  ОТКАЗ: {problem}")
            for note in report.get("notes") or []:
                print(f"  наблюдение: {note}")
            print(f"строка реестра: {known['date']} — {corpora_ru(known['corpora'])}, "
                  f"{known['indexed']} проиндексированы")
        return 0 if not report["problems"] else 2

    report = verify(root, known)
    failed = bool(report["problems"]) or (args.strict and bool(report["lags"]))
    if args.json:
        report["strict"] = bool(args.strict)
        report["ok"] = not failed
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(f"реестр: {report['registry']['date']} — "
              f"{corpora_ru(report['registry']['corpora'])}, "
              f"{report['registry']['indexed']} проиндексированы "
              f"({report['registry']['updated_at']})")
        # По документу печатается ЧТО он называет и каков исход; причина
        # исхода — ниже, в списках. Иначе одна и та же находка печаталась бы
        # дважды, и «предупреждений 6» выглядело бы как 12 строк.
        for entry in report["documents"]:
            place = f"{entry['path']}:{entry['line']}" if entry.get("line") else entry["path"]
            stated = entry.get("stated")
            what = (f"{stated['date']} / {corpora_ru(stated['corpora'])} / "
                    f"{stated['indexed']} проиндексированы" if stated else "не назван")
            print(f"  [{entry['verdict']}] {place} — {what}")
        for problem in report["problems"]:
            print(f"  ОШИБКА: {problem}")
        for lag in report["lags"]:
            print(f"  ПРЕДУПРЕЖДЕНИЕ: {lag}")
        for note in report["notes"]:
            print(f"  наблюдение: {note}")
        print(f"осмотрено: {report['examined']}, ошибок: {len(report['problems'])}, "
              f"предупреждений: {len(report['lags'])}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
