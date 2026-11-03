# -*- coding: utf-8 -*-
"""Две операции чтения документного графа и гейт изданий (этап 3 плана).

Проверяются шесть утверждений, которых чтением кода не подтвердить:

  1. Пустой слой отвечает `found=false` С ПРИЧИНОЙ, а не пустым списком: «фактов
     нет» и «слой не собран» — разные ответы, и второй требует прохода.
  2. У КАЖДОГО факта в ответе есть адрес: у соседа — адрес связи, у пары
     документов — адреса с ОБЕИХ сторон. Адрес разрешается по тексту файла.
  3. Пределы (`depth` 1..3, `limit` 1..200, `kind` из двух значений) отклоняются
     с кодом вызова, а не обрезаются молча: иначе ответ выглядел бы полным.
  4. Под Community обе операции получают `EditionRefusal` с названием издания
     `pro`, под разрешённой возможностью — исполняются. Обе половины важны:
     «отказывает всегда» и «не отказывает никогда» одинаково бесполезны.
  5. Набор инструментов MCP, их описания и обработчики совпадают: инструмент без
     обработчика — обещание, обработчик без инструмента — недоступная работа.
  6. Проход извлечения закрыт той же возможностью, что и чтение: половина гейта
     (чтение закрыто, запись открыта) была бы дырой.

Песочница: свой каталог состояния, свой ключ хранилища, корпус в `%TEMP%`,
поддельный транспорт вместо сети. Живое состояние не трогается.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

PASS = 0
FAIL = 0

LONG = ("Абзац документа о выкладке, индексации и проверках. " * 5).strip()

DOC_A = f"""# Руководство по выкладке

Метка документа: MARKER-ALPHA. {LONG}

## Раздел первый

{LONG} RAGRAF хранит факты и адреса.

## Раздел второй

{LONG} RAGRAF строит индекс командой index.
"""

DOC_B = f"""# Заметки о доставке

Метка документа: MARKER-BETA. {LONG}

Второй абзац. {LONG} RAGRAF отдаёт ответы с цитатами.

Третий абзац. {LONG} RAGRAF хранит факты о доставке.
"""

DOC_C = f"""# Заметка об индексации

Метка документа: МЕТКА-C. {LONG}

Второй абзац без общих имён. {LONG}
"""

MODULE = '''"""Пробный модуль корпуса."""


def probe_ops(value: int) -> int:
    return value * 3
'''


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


class ScriptedTransport:
    """Поддельный провайдер: детерминированный ответ, цитаты ИЗ текста чанка."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: dict) -> dict:
        self.calls += 1
        messages = (request.get("json") or {}).get("messages") or []
        content = str(messages[-1].get("content") or "") if messages else ""
        text = content.rsplit("\n\n", 1)[-1] if "\n\n" in content else content
        quote = text[:60] if len(text) >= 60 else text
        other = text[60:140] if len(text) >= 140 else text[:40]
        # Имя берётся ИЗ ТЕКСТА чанка. Запасное имя — начало самого текста: оно
        # разное у разных заголовков и потому не склеивает документы, которым
        # склеиваться нечем. Подделка обязана вести себя как честная модель:
        # придуманное общее имя создало бы связь, которой в корпусе нет.
        name = ""
        for token in ("MARKER-ALPHA", "MARKER-BETA", "МЕТКА-C"):
            if token in text:
                name = token
                break
        if not name:
            name = "RAGRAF" if "RAGRAF" in text else text.strip()[:24]
        payload = {
            "entities": [
                {"name": name, "kind": "system", "quote": quote},
                {"name": f"Тема {self.calls}", "kind": "concept", "quote": other},
            ],
            "relations": [
                {"from": name, "to": f"Тема {self.calls}", "kind": "mentions",
                 "quote": quote},
            ],
        }
        body = {"choices": [{"message": {"role": "assistant",
                                        "content": json.dumps(payload,
                                                              ensure_ascii=False)}}]}
        return {"status": 200, "body": json.dumps(body, ensure_ascii=False)}


def line_at(path: Path, number: int) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    if number < 1 or number > len(lines):
        return ""
    return lines[number - 1]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_docgraph_ops_"))
    (tmp / "catalog").mkdir()
    os.environ["RAGRAF_CATALOG_DIR"] = str(tmp / "catalog")
    os.environ["RAGRAF_HOME"] = str(tmp / "home")
    os.environ["RAGRAF_CONFIG_DIR"] = str(tmp / "config")
    os.environ["RAGRAF_SECRETS_KEY"] = base64.b64encode(
        secrets.token_bytes(32)).decode("ascii")
    os.environ["RAGRAF_AGENT"] = "test-docgraph-ops"

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    from ragraf import docgraph as dg  # noqa: E402
    from ragraf import docgraph_extract as ex  # noqa: E402
    from ragraf import docgraph_ops as ops  # noqa: E402
    from ragraf import edition  # noqa: E402
    from ragraf import llm as llm_mod  # noqa: E402
    from ragraf import settings as settings_mod  # noqa: E402
    from ragraf.config import load as load_config  # noqa: E402
    from ragraf.facade import Facade  # noqa: E402
    from ragraf.indexer import Indexer  # noqa: E402
    from ragraf.init_corpus import create_config  # noqa: E402

    # Лицензия машины не должна влиять: иначе «отказ под Community» не проверить.
    os.environ[edition.LICENSE_ENV] = str(tmp / "absent-machine.license")
    os.environ.pop(edition.DECLARED_ENV, None)
    os.environ.pop(edition.BASELINE_ENV, None)

    print("=" * 78)
    print("0. ПЕСОЧНИЦА: корпус, индекс, документные чанки")
    print("=" * 78)
    corpus = tmp / "corpus"
    (corpus / "docs").mkdir(parents=True)
    (corpus / "src").mkdir()
    (corpus / ".gitignore").write_text("probe\n", encoding="utf-8", newline="\n")
    for name, text in (("guide.md", DOC_A), ("notes.md", DOC_B), ("solo.md", DOC_C)):
        (corpus / "docs" / name).write_text(text, encoding="utf-8", newline="\n")
    (corpus / "src" / "mod.py").write_text(MODULE, encoding="utf-8", newline="\n")

    cfg_path, _scan = create_config(corpus, project="opsprobe",
                                    config_path=corpus / "corpus.yaml")
    text = cfg_path.read_text(encoding="utf-8").replace(
        "joern_enabled: true", "joern_enabled: false")
    cfg_path.write_text(text, encoding="utf-8", newline="\n")
    cfg = load_config(cfg_path)

    indexer = Indexer(cfg, mode="fast")
    try:
        report = indexer.run(with_vectors=False)
    finally:
        indexer.close()
    rev = str(report.rev_id or "")
    check(bool(report.ok) and bool(rev), "индекс собран", f"ok={report.ok} rev={rev}")

    doc_a = corpus / "docs" / "guide.md"
    doc_b = corpus / "docs" / "notes.md"
    doc_c = corpus / "docs" / "solo.md"

    def facade() -> Facade:
        return Facade(cfg, tool="selftest", telemetry=False, watch=False)

    # ---------------- 1. Пустой слой ----------------
    print()
    print("=" * 78)
    print("1. ПУСТОЙ СЛОЙ: ОТВЕТ С ПРИЧИНОЙ, А НЕ ПУСТОЙ СПИСОК")
    print("=" * 78)
    variant = tmp / "baseline-docgraph-community.json"
    data = json.loads(Path(edition.baseline_path()).read_text(encoding="utf-8"))
    for item in data["features"]:
        if item["name"] == "doc-graph":
            item["edition"] = "community"
    variant.write_bytes(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

    def allowed_facade() -> tuple[Facade, dict[str, str]]:
        """Фасад, у которого возможность разрешена: вариант базовой линии.

        Переменная окружения снимается ТОЛЬКО после работы фасада: издание
        разрешается лениво, в момент вызова операции, и снятая раньше переменная
        дала бы отказ по настоящей базовой линии — то есть проверялось бы не то,
        что задумано.
        """
        saved = os.environ.get(edition.BASELINE_ENV)
        os.environ[edition.BASELINE_ENV] = str(variant)
        return Facade(cfg, tool="selftest", telemetry=False, watch=False), {
            "saved": saved or ""}

    def release(extra: dict[str, str]) -> None:
        if extra.get("saved"):
            os.environ[edition.BASELINE_ENV] = extra["saved"]
        else:
            os.environ.pop(edition.BASELINE_ENV, None)

    empty_fc, empty_env = allowed_facade()
    try:
        refused_empty = empty_fc.op_entity_neighbors("RAGRAF")
        empty_rel = empty_fc.op_doc_relations(doc="docs/guide.md")
        empty_edition = empty_fc.edition_state().effective
    finally:
        empty_fc.close()
        release(empty_env)
    check(empty_edition == "community",
          "действует издание community (лицензии машины нет)", empty_edition)
    check(refused_empty.get("found") is False, "пустой слой: соседей нет",
          str(refused_empty.get("found")))
    message = str(refused_empty.get("error") or "")
    check(bool(message), "причина названа, а не пустой ответ", message[:90])
    check("слой" in message.lower() or "факт" in message.lower(),
          "причина говорит о слое, а не о запросе", message[:90])
    check(empty_rel.get("found") is False and bool(empty_rel.get("error")),
          "связи документов: тот же вид ответа при пустом слое",
          str(empty_rel.get("error"))[:90])

    # ---------------- 2. Наполнение слоя ----------------
    print()
    print("=" * 78)
    print("2. НАПОЛНЕНИЕ СЛОЯ ПРОХОДОМ ИЗВЛЕЧЕНИЯ (поддельный провайдер)")
    print("=" * 78)
    values = dict(settings_mod.DEFAULTS)
    values.update({"llm_enabled": True, "llm_provider": "local",
                   "llm_model": "fake-model", "llm_prompt_version": "docgraph-v1",
                   "llm_secret_name": ""})
    transport = ScriptedTransport()
    filled = ex.run(cfg, rev, values=values,
                    client=llm_mod.LLMClient(
                        cfg, config=llm_mod.config_from_settings(values),
                        transport=transport))
    stats = filled.get("stats") or {}
    print(f"  проход: вызовов {stats.get('calls')}, сущностей {stats.get('entities')}, "
          f"упоминаний {stats.get('mentions')}, отношений {stats.get('relations')}")
    check(filled.get("collected") is True, "слой собран",
          json.dumps(filled.get("reason"), ensure_ascii=False)[:150])
    check(int(stats.get("entities") or 0) >= 2, "в слое больше одной сущности",
          str(stats.get("entities")))

    conn = sqlite3.connect(str(cfg.lexvec_db))
    conn.row_factory = sqlite3.Row
    try:
        state = ops.layer_state(conn, rev)
    finally:
        conn.close()
    check(state.get("built") is True, "состояние слоя: собран", str(state))

    # ---------------- 3. Гейт изданий ----------------
    print()
    print("=" * 78)
    print("3. ГЕЙТ ИЗДАНИЙ: COMMUNITY ОТКАЗЫВАЕТ, РАЗРЕШЕНИЕ ИСПОЛНЯЕТ")
    print("=" * 78)
    gated = facade()
    try:
        refusal = gated.op_entity_neighbors("RAGRAF")
        refusal_rel = gated.op_doc_relations(doc="docs/guide.md")
        edition_now = gated.edition_state().effective
        features_now = list(gated.edition_state().features)
    finally:
        gated.close()
    check(edition_now == "community", "под лицензией машины действует community",
          edition_now)
    check(str(refusal.get("error") or "").startswith("EditionRefusal"),
          "соседи: отказ помечен EditionRefusal", str(refusal.get("error"))[:90])
    blocked = refusal.get("refused") or {}
    check(blocked.get("feature") == "doc-graph",
          "отказ называет возможность doc-graph", str(blocked))
    check(blocked.get("requires_edition") == "pro",
          "отказ называет издание pro", str(blocked))
    check("doc-graph" not in features_now,
          "отвергнутая возможность отсутствует в features", str(features_now))
    check(refusal.get("found") is False, "отказ не выдаёт данных", str(refusal.get("found")))
    check(str(refusal_rel.get("error") or "").startswith("EditionRefusal"),
          "связи документов: тот же отказ", str(refusal_rel.get("error"))[:90])

    # Тот же гейт на пути ПРОХОДА: иначе чтение закрыто, а запись открыта.
    denial = edition.feature_denial("doc-graph")
    check(bool(denial) and "pro" in denial,
          "проход извлечения закрыт той же возможностью", denial[:100])
    check(edition.feature_denial("documents") == "",
          "разрешённая возможность отказа не получает",
          edition.feature_denial("documents"))

    # ---------------- 4. Соседи: адрес у каждого факта ----------------
    print()
    print("=" * 78)
    print("4. СОСЕДИ СУЩНОСТИ: АДРЕС У КАЖДОГО ФАКТА")
    print("=" * 78)
    open_fc, open_env = allowed_facade()
    try:
        one = open_fc.op_entity_neighbors("RAGRAF", depth=1, limit=50)
        two = open_fc.op_entity_neighbors("RAGRAF", depth=2, limit=50)
        by_id = None
        entity_id = (one.get("result") or {}).get("entity", {}).get("entity_id")
        if entity_id:
            by_id = open_fc.op_entity_neighbors(entity_id, depth=1, limit=5)
        theme_kind = open_fc.op_entity_neighbors("RAGRAF", kind="theme")
    finally:
        open_fc.close()
        release(open_env)

    payload = one.get("result") or {}
    neighbours = payload.get("neighbors") or []
    print(f"  соседей на глубине 1: {len(neighbours)}; на глубине 2: "
          f"{len((two.get('result') or {}).get('neighbors') or [])}")
    check(one.get("found") is True, "соседи найдены", str(one.get("error"))[:90])
    check(len(neighbours) > 0, "список соседей не пуст", str(len(neighbours)))
    check(bool(payload.get("entity", {}).get("name")),
          "разрешённая сущность названа", str(payload.get("entity"))[:90])

    addresses_ok = True
    resolves_ok = True
    details: list[str] = []
    for item in neighbours:
        citations = item.get("citations") or []
        if not citations:
            addresses_ok = False
            details.append(f"без адреса: {item.get('name')}")
            continue
        citation = citations[0]
        if not citation.get("path") or not int(citation.get("start_line") or 0):
            addresses_ok = False
            details.append(f"пустой адрес: {item.get('name')}")
            continue
        if citation.get("locator") != f"{citation['path']}:{citation['start_line']}":
            addresses_ok = False
            details.append(f"локатор не совпал: {citation.get('locator')}")
        target = corpus
        line = line_at(target / str(citation["path"]), int(citation["start_line"]))
        # Адрес обязан указывать в существующую непустую строку файла.
        if not line.strip():
            resolves_ok = False
            details.append(f"строка пуста: {citation['locator']}")
    check(addresses_ok, "у каждого соседа адрес связи", "; ".join(details[:3]))
    check(resolves_ok, "каждый адрес указывает в непустую строку файла",
          "; ".join(details[:3]))
    check(int(payload.get("rejected_no_citation") or 0) == 0,
          "ни один факт не отброшен из-за отсутствия адреса",
          str(payload.get("rejected_no_citation")))

    # Факт без адреса не выдаётся, но и не исчезает молча: его число обязано быть
    # в ответе. Строка без адреса вставляется в слой нарочно — так проверяется
    # поведение на испорченном слое, а не на заведомо чистом.
    broken = sqlite3.connect(str(cfg.lexvec_db))
    try:
        start_entity = (one.get("result") or {}).get("entity", {}).get("entity_id")
        # Сущность-приманка заводится отдельно: связь должна вести в НОВЫЙ узел,
        # иначе обход не дойдёт до неё и строка без адреса не будет проверена.
        broken.execute(
            "INSERT OR REPLACE INTO entity(rev_id, entity_id, name, norm_name, kind, "
            "mentions, first_path, first_line) VALUES(?,?,?,?,?,?,?,?)",
            (rev, "entity-broken-probe", "Проба без адреса", "проба без адреса",
             "probe", 0, "", 0),
        )
        broken.execute(
            "INSERT OR REPLACE INTO entity_relation(rev_id, rel_id, src_entity_id, "
            "dst_entity_id, kind, quote_hash, chunk_id, path, start_line, end_line, "
            "weight) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (rev, "rel-broken-probe", start_entity, "entity-broken-probe", "probe",
             "x", "chunk-probe", "", 0, 0, 1.0),
        )
        broken.commit()
    finally:
        broken.close()
    open_fc, open_env = allowed_facade()
    try:
        after_broken = open_fc.op_entity_neighbors("RAGRAF", depth=1, limit=50)
    finally:
        open_fc.close()
        release(open_env)
    broken_payload = after_broken.get("result") or {}
    check(int(broken_payload.get("rejected_no_citation") or 0) >= 1,
          "факт без адреса посчитан в rejected_no_citation, а не потерян молча",
          str(broken_payload.get("rejected_no_citation")))
    without_address = [item for item in broken_payload.get("neighbors") or []
                       if not (item.get("citations") or [])]
    check(not without_address, "ни один сосед из испорченного слоя не выдан без адреса",
          str(without_address)[:120])
    cleaner = sqlite3.connect(str(cfg.lexvec_db))
    try:
        cleaner.execute("DELETE FROM entity_relation WHERE rev_id = ? AND rel_id = ?",
                        (rev, "rel-broken-probe"))
        cleaner.execute("DELETE FROM entity WHERE rev_id = ? AND entity_id = ?",
                        (rev, "entity-broken-probe"))
        cleaner.commit()
    finally:
        cleaner.close()
    check(by_id is not None and by_id.get("found") is True,
          "сущность находится и по entity_id", str((by_id or {}).get("error"))[:90])
    check(str((by_id or {}).get("result", {}).get("entity", {}).get("entity_id") or "")
          == str(entity_id), "поиск по id возвращает ту же сущность")
    check(len((two.get("result") or {}).get("neighbors") or [])
          >= len(neighbours), "глубина 2 даёт не меньше соседей, чем глубина 1")
    check(theme_kind.get("found") is False
          and "тем" in str(theme_kind.get("result", {}).get("reason") or "").lower(),
          "вид theme отвечает причиной: слой тем собирается этапом 5",
          str(theme_kind.get("result", {}).get("reason"))[:90])

    # ---------------- 5. Связи документов: адреса с двух сторон ----------------
    print()
    print("=" * 78)
    print("5. СВЯЗИ ДОКУМЕНТОВ: АДРЕСА С ОБЕИХ СТОРОН")
    print("=" * 78)
    open_fc, open_env = allowed_facade()
    try:
        rel_doc = open_fc.op_doc_relations(doc="docs/guide.md")
        rel_entity = open_fc.op_doc_relations(entity="RAGRAF")
        rel_solo = open_fc.op_doc_relations(doc="docs/solo.md")
    finally:
        open_fc.close()
        release(open_env)

    pairs = (rel_doc.get("result") or {}).get("pairs") or []
    print(f"  пар у guide.md: {len(pairs)}; у solo.md: "
          f"{len((rel_solo.get('result') or {}).get('pairs') or [])}")
    check(rel_doc.get("found") is True, "связи документа найдены",
          str(rel_doc.get("error"))[:90])
    check(len(pairs) > 0, "есть хотя бы одна пара документов", str(len(pairs)))
    sides_ok = True
    named_ok = True
    sides_detail: list[str] = []
    for pair in pairs:
        sides = {str(c.get("side")) for c in pair.get("citations") or []}
        if not {"from", "to"} <= sides:
            sides_ok = False
            sides_detail.append(f"стороны: {sorted(sides)} для {pair.get('to')}")
        for via in pair.get("via_entities") or []:
            if not via.get("name"):
                named_ok = False
                sides_detail.append(f"без имени: {via.get('entity_id')}")
        paths = {str(c.get("path")) for c in pair.get("citations") or []}
        if not paths <= {str(pair.get("from")), str(pair.get("to"))}:
            sides_ok = False
            sides_detail.append(f"адрес вне пары: {sorted(paths)}")
    check(sides_ok, "у каждой пары адреса с обеих сторон и только её документы",
          "; ".join(sides_detail[:3]))
    check(named_ok, "у связующих сущностей есть имена", "; ".join(sides_detail[:3]))
    check(int((rel_doc.get("result") or {}).get("rejected_no_citation") or 0) == 0,
          "ни одна пара не отброшена из-за отсутствия адреса")
    check(rel_entity.get("found") is True,
          "связи по сущности тоже отвечают", str(rel_entity.get("error"))[:90])
    solo_payload = rel_solo.get("result") or {}
    solo_reason = str(solo_payload.get("reason") or "")
    check(rel_solo.get("found") is False and bool(solo_reason),
          "документ без общих сущностей: ответ с причиной, а не пустой список",
          solo_reason[:110])
    check(solo_reason in " ".join(rel_solo.get("limits") or []),
          "причина попадает и в limits ответа", str(rel_solo.get("limits"))[:110])
    check("пары" not in solo_reason and "связ" in solo_reason.lower(),
          "причина говорит о связях, а не о ошибке запроса", solo_reason[:110])

    # ---------------- 6. Пределы ----------------
    print()
    print("=" * 78)
    print("6. ПРЕДЕЛЫ: ОТКАЗ С КОДОМ, А НЕ МОЛЧАЛИВАЯ ОБРЕЗКА")
    print("=" * 78)
    for label, call in (
        ("depth=4", lambda: ops.check_depth(4)),
        ("depth=0", lambda: ops.check_depth(0)),
        ("limit=0", lambda: ops.clamp_limit(0)),
        ("limit=201", lambda: ops.clamp_limit(201)),
        ("kind=bogus", lambda: ops.check_kind("bogus")),
    ):
        try:
            call()
            check(False, f"{label}: отклонено", "исключения не было")
        except ValueError as exc:
            check(True, f"{label}: отклонено с названным пределом")
            check("диапазон" in str(exc) or "ожидается" in str(exc),
                  f"{label}: причина называет допустимые значения", str(exc)[:80])

    cli = [sys.executable, str(REPO / "tools" / "ragraf.py"), "-p", str(cfg_path),
           "query", "entity_neighbors", "--name", "RAGRAF"]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    # Пределы проверяются ДО чтения слоя, но после гейта изданий: без разрешённой
    # возможности отказ изданию вернулся бы раньше и проверка параметра не
    # выполнилась бы вовсе.
    env[edition.BASELINE_ENV] = str(variant)
    deep = subprocess.run(cli + ["--depth", "4"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=300)
    check(deep.returncode == 2, "CLI: depth=4 даёт код ошибки вызова",
          f"код {deep.returncode} {(deep.stderr or '')[-160:]}")
    check("неверный параметр" in (deep.stderr or ""),
          "CLI: отказ объясняет параметр", (deep.stderr or "").strip()[:120])
    over = subprocess.run(cli + ["--limit", "500"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=300)
    check(over.returncode == 2, "CLI: limit=500 даёт код ошибки вызова",
          f"код {over.returncode} {(over.stderr or '')[-160:]}")
    refused_cli = subprocess.run(cli, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace",
                                 env={**env, edition.BASELINE_ENV: ""}, timeout=300)
    check("EditionRefusal" in (refused_cli.stdout or ""),
          "CLI под Community печатает отказ издания, а не пустоту",
          (refused_cli.stdout or "")[:160])
    check(refused_cli.returncode == 0,
          "CLI: отказ изданию — ответ, а не сбой (код 0, как у siem_export)",
          f"код {refused_cli.returncode}")

    # ---------------- 7. Поверхность MCP ----------------
    print()
    print("=" * 78)
    print("7. ПОВЕРХНОСТЬ MCP: ИНСТРУМЕНТЫ, ОПИСАНИЯ, ОБРАБОТЧИКИ")
    print("=" * 78)
    from ragraf import mcp_server as mcp_mod  # noqa: E402

    names = {tool["name"] for tool in mcp_mod.TOOLS}
    meta = set(mcp_mod._TOOL_META)  # noqa: SLF001 — проверяется сам набор
    server = mcp_mod.McpServer(config_path=str(cfg_path), agent="tester")
    try:
        handlers = set(server._handlers())  # noqa: SLF001
        listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    finally:
        server.close()
    check(names == handlers, "набор инструментов и обработчиков совпадает",
          f"разница: {sorted(names ^ handlers)[:5]}")
    check(names == meta, "у каждого инструмента есть описание (и наоборот)",
          f"разница: {sorted(names ^ meta)[:5]}")
    check({"ragraf_entity_neighbors", "ragraf_doc_relations"} <= names,
          "оба инструмента объявлены")
    listed_names = {item["name"] for item in
                    ((listed.get("result") or {}).get("tools") or [])}
    check({"ragraf_entity_neighbors", "ragraf_doc_relations"} <= listed_names,
          "оба инструмента отдаются в tools/list")
    neighbour_meta = mcp_mod._TOOL_META["ragraf_entity_neighbors"]
    check(neighbour_meta.get("readOnlyHint") is True
          and neighbour_meta.get("destructiveHint") is False,
          "инструмент помечен только для чтения", str(neighbour_meta))

    os.environ[edition.BASELINE_ENV] = str(variant)
    try:
        mcp_server = mcp_mod.McpServer(config_path=str(cfg_path), agent="tester",
                                       access_label="probe", access_scopes=["read"])
        try:
            called = mcp_server.handle({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "ragraf_entity_neighbors",
                           "arguments": {"entity": "RAGRAF", "depth": 1}},
            })
        finally:
            mcp_server.close()
    finally:
        os.environ.pop(edition.BASELINE_ENV, None)
    result = called.get("result") or {}
    text = " ".join(item.get("text", "") for item in (result.get("content") or []))
    check(bool(text), "MCP: вызов инструмента вернул ответ", text[:120])
    check("citations" in text and "start_line" in text,
          "MCP: в ответе есть адреса цитат", text[:200])
    check(not result.get("isError"), "MCP: вызов не помечен ошибкой", text[:160])

    # ---------------- 8. Целостность и состояние ----------------
    print()
    print("=" * 78)
    print("8. ЦЕЛОСТНОСТЬ СЛОЯ ПОСЛЕ ЧТЕНИЯ")
    print("=" * 78)
    conn = sqlite3.connect(str(cfg.lexvec_db))
    try:
        verdict = dg.check_integrity(conn, rev)
    finally:
        conn.close()
    check(verdict.get("ok") is True, "целостность слоя — норма",
          json.dumps(verdict.get("problems"), ensure_ascii=False)[:200])

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
