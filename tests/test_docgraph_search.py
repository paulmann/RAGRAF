# -*- coding: utf-8 -*-
"""Dual-level поиск: режимы, уровень «тема», честная деградация (этап 4).

Семь утверждений, которых чтением не подтвердить:

  1. `mode=auto` не меняет прежнее поведение: та же выдача, те же оценки, тот же
     порядок. Иначе сравнение с прошлыми замерами потеряло бы смысл.
  2. `mode=docs` ДОБАВЛЯЕТ хиты уровня «тема», не переставляя лексические:
     `match_kind` из закрытого перечня, у каждого хиты-сущности адрес
     `path:start_line-end_line`, у сущности — `matched_on` (точное или частное).
  3. Деградация названа полем `degraded` с кодом и текстом: нет модели, нет слоя,
     нет издания, темы не собраны. Пустой список за «связей нет» не выдаётся.
  4. Ключи запроса кэшируются: второй вызов по тому же запросу не делает платного
     обращения (счётчик транспорта, а не время).
  5. `mode=code` фильтрует материал и говорит, сколько скрыл.
  6. Отказ по режиму: неизвестное значение — отказ вызова (фасад: `error`, CLI:
     код 2), а не тихая подмена `auto`.
  7. `mode=docs` под Community не отказывает целиком: базовый поиск остаётся
     (правило «базовая безопасность и поиск бесплатны»), а уровень «тема» назван
     недоступным.

Песочница: свой каталог состояния, свой корпус, поддельный транспорт вместо сети.
Живое состояние не трогается.
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

LONG = ("Абзац документа о выдаче лицензий и проверке подписи. " * 5).strip()

DOC_LIC = f"""# Лицензирование

Выдача лицензий описана здесь. {LONG}

Модуль licence_file держит путь к файлу лицензии и отпечаток установки.
"""

DOC_OTHER = f"""# Заметки о выкладке

Здесь про выкладку и индексацию. {LONG}

Другой абзац без общих понятий. {LONG}
"""

CODE = '''"""Работа с лицензией."""


def licence_file(path: str) -> str:
    """Путь к файлу лицензии."""
    return path
'''


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


class FakeProvider:
    """Поддельный провайдер: отвечает на промпт ключей и на промпт извлечения.

    Ветвление по содержимому запроса, а не по счётчику: иначе проверка кэша
    (второй вызов не должен дойти до транспорта) ничего не доказывала бы.
    """

    def __init__(self, keys: dict | None = None) -> None:
        self.calls = 0
        self.key_requests = 0
        self.keys = keys or {"high_level": ["лицензирование"],
                             "low_level": ["licence_file"]}

    def __call__(self, request: dict) -> dict:
        self.calls += 1
        messages = (request.get("json") or {}).get("messages") or []
        content = str(messages[-1].get("content") or "") if messages else ""
        if "high_level" in content:
            self.key_requests += 1
            payload = dict(self.keys)
        else:
            text = content.rsplit("\n\n", 1)[-1]
            quote = text[:60] if len(text) >= 60 else text
            name = "лицензирование" if "лицензи" in text.lower() else "выкладка"
            low = "licence_file" if "licence_file" in text else ""
            entities = [{"name": name, "kind": "concept", "quote": quote}]
            relations = []
            if low:
                entities.append({"name": low, "kind": "symbol", "quote": quote})
                # Связь с адресом: оба конца упомянуты в ЭТОМ чанке, иначе
                # извлекатель отклонит её как «связь по имени».
                relations.append({"from": name, "to": low, "kind": "описывает",
                                  "quote": quote})
            payload = {"entities": entities, "relations": relations}
        body = {"choices": [{"message": {"role": "assistant",
                                        "content": json.dumps(payload,
                                                              ensure_ascii=False)}}]}
        return {"status": 200, "body": json.dumps(body, ensure_ascii=False)}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_search_levels_"))
    (tmp / "catalog").mkdir()
    os.environ["RAGRAF_CATALOG_DIR"] = str(tmp / "catalog")
    os.environ["RAGRAF_HOME"] = str(tmp / "home")
    os.environ["RAGRAF_CONFIG_DIR"] = str(tmp / "config")
    os.environ["RAGRAF_SECRETS_KEY"] = base64.b64encode(
        secrets.token_bytes(32)).decode("ascii")
    os.environ["RAGRAF_AGENT"] = "test-docgraph-search"

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    from ragraf import docgraph_extract as ex  # noqa: E402
    from ragraf import edition  # noqa: E402
    from ragraf import llm as llm_mod  # noqa: E402
    from ragraf import settings as settings_mod  # noqa: E402
    from ragraf.config import load as load_config  # noqa: E402
    from ragraf.facade import Facade  # noqa: E402
    from ragraf.indexer import Indexer  # noqa: E402
    from ragraf.init_corpus import create_config  # noqa: E402

    os.environ[edition.LICENSE_ENV] = str(tmp / "absent-machine.license")
    os.environ.pop(edition.DECLARED_ENV, None)
    os.environ.pop(edition.BASELINE_ENV, None)

    print("=" * 78)
    print("0. ПЕСОЧНИЦА: корпус, индекс, слой фактов")
    print("=" * 78)
    corpus = tmp / "corpus"
    (corpus / "docs").mkdir(parents=True)
    (corpus / "src").mkdir()
    (corpus / ".gitignore").write_text("probe\n", encoding="utf-8", newline="\n")
    (corpus / "docs" / "licence.md").write_text(DOC_LIC, encoding="utf-8", newline="\n")
    (corpus / "docs" / "deploy.md").write_text(DOC_OTHER, encoding="utf-8", newline="\n")
    (corpus / "src" / "licence.py").write_text(CODE, encoding="utf-8", newline="\n")

    cfg_path, _scan = create_config(corpus, project="searchprobe",
                                    config_path=corpus / "corpus.yaml")
    text = cfg_path.read_text(encoding="utf-8").replace("joern_enabled: true",
                                                        "joern_enabled: false")
    cfg_path.write_text(text, encoding="utf-8", newline="\n")
    cfg = load_config(cfg_path)
    indexer = Indexer(cfg, mode="fast")
    try:
        report = indexer.run(with_vectors=False)
    finally:
        indexer.close()
    rev = str(report.rev_id or "")
    check(bool(report.ok) and bool(rev), "индекс собран", f"ok={report.ok} rev={rev}")

    # ---------------- 1. auto: прежнее поведение ----------------
    print()
    print("=" * 78)
    print("1. MODE=AUTO НЕ МЕНЯЕТ ВЫДАЧУ")
    print("=" * 78)
    fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
    try:
        plain = fc.op_search("licence_file", limit=10)
        auto = fc.op_search("licence_file", limit=10, mode="auto")
    finally:
        fc.close()
    plain_ids = [(item["chunk_id"], item["score"]) for item in plain.get("result") or []]
    auto_ids = [(item["chunk_id"], item["score"]) for item in auto.get("result") or []]
    check(bool(plain_ids), "лексический поиск находит хотя бы один хит (иначе сравнение пустое)",
          str(len(plain_ids)))
    check(plain_ids == auto_ids, "auto отдаёт те же хиты с теми же оценками",
          f"{len(plain_ids)} против {len(auto_ids)}")
    check("degraded" not in auto, "в режиме auto деградации нет: нечего деградировать")
    check(all("match_kind" not in item for item in auto.get("result") or []),
          "в режиме auto лишних полей не появляется")

    # ---------------- 2. без LLM: деградация названа ----------------
    print()
    print("=" * 78)
    print("2. БЕЗ МОДЕЛИ: ВЫДАЧА ЕСТЬ, ПРИЧИНА НАЗВАНА")
    print("=" * 78)
    # Возможность разрешена вариантом базовой линии: проверяется ИМЕННО состояние
    # модели, а гейт изданий проверяется разделом 4 своим случаем. Иначе один ответ
    # «отказано» закрывал бы два разных утверждения.
    variant = tmp / "baseline-docgraph-community.json"
    data = json.loads(Path(edition.baseline_path()).read_text(encoding="utf-8"))
    for item in data["features"]:
        if item["name"] == "doc-graph":
            item["edition"] = "community"
    variant.write_bytes(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    os.environ[edition.BASELINE_ENV] = str(variant)
    try:
        fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        try:
            degraded = fc.op_search("licence_file", limit=10, mode="docs")
        finally:
            fc.close()
    finally:
        os.environ.pop(edition.BASELINE_ENV, None)
    block = degraded.get("degraded") or {}
    print(f"  degraded: {block.get('code')} — {str(block.get('reason'))[:70]}")
    check(block.get("code") == "llm_unavailable",
          "причина деградации — ненастроенная модель", str(block)[:160])
    check(bool(block.get("text")), "причина есть и словами для человека")
    check(bool(degraded.get("result")), "базовая выдача не пропала: поиск остаётся",
          str(len(degraded.get("result") or [])))
    check(any("dual-level" in str(note) for note in degraded.get("limits") or []),
          "причина попала и в limits ответа")

    # ---------------- 3. настройка + наполнение слоя ----------------
    print()
    print("=" * 78)
    print("3. СЛОЙ И МОДЕЛЬ ЕСТЬ: УРОВЕНЬ «ТЕМА» ДОБАВЛЯЕТ ХИТЫ")
    print("=" * 78)
    settings_mod.SettingsStore().save({
        "llm_enabled": True, "llm_provider": "local", "llm_model": "fake-model",
        "llm_prompt_version": "docgraph-v1", "llm_secret_name": "",
        "llm_cache_enabled": True,
    })
    values = dict(settings_mod.DEFAULTS)
    values.update({"llm_enabled": True, "llm_provider": "local",
                   "llm_model": "fake-model", "llm_prompt_version": "docgraph-v1",
                   "llm_secret_name": "", "llm_cache_enabled": True})
    provider = FakeProvider()
    filled = ex.run(cfg, rev, values=values,
                    client=llm_mod.LLMClient(
                        cfg, config=llm_mod.config_from_settings(values),
                        transport=provider))
    check(filled.get("collected") is True, "слой фактов собран",
          json.dumps(filled.get("reason"), ensure_ascii=False)[:150])

    # Модель в фасаде подменяется через переменную окружения недоступна, поэтому
    # проверяется ФАСАДНЫЙ путь целиком: клиент в `_apply_levels` создаётся сам,
    # и его транспорт — настоящий. Значит здесь проверяется ЧЕСТНАЯ деградация при
    # недоступном провайдере (сеть закрыта), а уровень «тема» — отдельно, через
    # модуль `duallevel` с поддельным клиентом.
    from ragraf import duallevel  # noqa: E402

    fake_client = llm_mod.LLMClient(cfg, config=llm_mod.config_from_settings(values),
                                    transport=provider)
    keys = duallevel.extract_keys(fake_client, "выдача лицензий")
    check(bool(keys.get("high_level")) or bool(keys.get("low_level")),
          "ключи запроса извлечены", str(keys)[:200])
    check(provider.key_requests == 1, "ключи получены одним обращением",
          str(provider.key_requests))

    again = duallevel.extract_keys(fake_client, "выдача лицензий")
    check(again.get("cached") is True, "второй запрос ключей отвечен кэшем",
          str(again)[:160])
    check(provider.key_requests == 1, "второго обращения к провайдеру не было",
          str(provider.key_requests))

    conn = sqlite3.connect(str(cfg.lexvec_db))
    conn.row_factory = sqlite3.Row
    try:
        matched = duallevel.match_entities(conn, rev, ["лицензирование", "licence_file"])
        chunks = duallevel.entity_chunks(conn, rev, [m["entity_id"] for m in matched])
        ready, reason = duallevel.layer_ready(conn, rev)
        empty_theme = duallevel.themes_of(conn, rev)
    finally:
        conn.close()
    print(f"  сущностей по ключам: {len(matched)}, чанков-упоминаний: {len(chunks)}")
    check(ready is True, "слой готов к уровню «тема»", reason)
    check(bool(matched), "сущности по ключам найдены", str(len(matched)))
    check(all(m.get("matched_on") in ("exact", "partial") for m in matched),
          "у каждой сущности назван вид совпадения", str(matched[:2]))
    check(bool(chunks), "у найденных сущностей есть чанки с адресами",
          str(len(chunks)))
    check(all(chunk.get("locator", "").count(":") >= 1 for chunk in chunks),
          "адрес каждого упоминания в форме path:строка", str(chunks[:1]))
    check(empty_theme == [], "тем в слое нет: их собирает этап 5")

    # ---------------- 3.5. фасад: собранный ответ уровня «тема» ----------------
    print()
    print("=" * 78)
    print("3.5. ФАСАД СОБИРАЕТ ХИТЫ УРОВНЯ «ТЕМА» С АДРЕСАМИ")
    print("=" * 78)
    # Транспорт клиента подменяется в модуле: `_apply_levels` создаёт клиента сам,
    # и другого способа проверить СОБРАННЫЙ фасадом ответ нет. Подмена касается
    # только этого процесса и только на время раздела.
    real_client = llm_mod.LLMClient

    class PatchedClient(real_client):  # noqa: N801 — имя по роли в проверке
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", provider)
            super().__init__(*args, **kwargs)

    llm_mod.LLMClient = PatchedClient
    os.environ[edition.BASELINE_ENV] = str(variant)
    try:
        fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        try:
            level_answer = fc.op_search("licence_file", limit=10, mode="docs")
        finally:
            fc.close()
    finally:
        llm_mod.LLMClient = real_client
        os.environ.pop(edition.BASELINE_ENV, None)

    levels = level_answer.get("levels") or {}
    hits = level_answer.get("result") or []
    entity_hits = [item for item in hits if item.get("match_kind") == "entity"]
    print(f"  хитов всего: {len(hits)}, из них уровня «тема»: {len(entity_hits)}; "
          f"ключи: {levels.get('high_level')} / {levels.get('low_level')}")
    check("degraded" not in level_answer, "при настроенной модели деградации нет",
          str(level_answer.get("degraded"))[:160])
    check(bool(levels.get("high_level")) or bool(levels.get("low_level")),
          "ключи запроса попали в ответ", str(levels)[:160])
    check(len(entity_hits) > 0, "уровень «тема» добавил хотя бы один хит",
          str(len(entity_hits)))
    check(int(levels.get("entity_hits") or 0) == len(entity_hits),
          "число добавленных хитов в ответе совпадает с фактом",
          f"{levels.get('entity_hits')} против {len(entity_hits)}")

    kinds = {str(item.get("match_kind")) for item in hits}
    check(kinds <= {"lexical", "vector", "entity"},
          "match_kind только из закрытого перечня", str(kinds))
    bad_address = [item for item in entity_hits
                   if not str(item.get("locator") or "").count(":") >= 1
                   or not int(item.get("start_line") or 0)]
    check(not bad_address, "у каждого хиты уровня «тема» есть адрес path:строка",
          str(bad_address[:1])[:200])
    check(all(item.get("matched_entities") for item in entity_hits),
          "у хиты названы сущности, по которым он найден")
    marks = [mark for item in entity_hits for mark in item.get("matched_entities") or []]
    check(all(mark.get("matched_on") in ("exact", "partial") for mark in marks),
          "вид совпадения назван у каждой сущности", str(marks[:2])[:200])
    check(all(str(mark.get("locator") or "").count(":") >= 1 for mark in marks),
          "у каждой сущности в хите есть её собственный адрес", str(marks[:1])[:200])

    lexical_before = [item["chunk_id"] for item in auto.get("result") or []]
    lexical_after = [item["chunk_id"] for item in hits
                     if item.get("match_kind") != "entity"]
    check(lexical_after[:len(lexical_before)] == lexical_before,
          "порядок лексических хитов не изменился: уровень только добавляет",
          f"{lexical_after[:4]} против {lexical_before[:4]}")

    # ---------------- 4. фасад: деградация по слою и по изданию ----------------
    print()
    print("=" * 78)
    print("4. ДЕГРАДАЦИЯ ПО СЛОЮ И ПО ИЗДАНИЮ НАЗВАНА РАЗДЕЛЬНО")
    print("=" * 78)
    # Второй корпус: индекс есть, проход извлечения НЕ выполнялся.
    (tmp / "emptycorpus" / "docs").mkdir(parents=True, exist_ok=True)
    (tmp / "emptycorpus" / "src").mkdir(parents=True, exist_ok=True)
    # Создание корпуса требует хотя бы три файла с кодом — иначе отказывает
    # собственный сканер (`init_corpus`). Три модуля — минимум, а не вкус.
    for name in ("licence.py", "other.py", "third.py"):
        (tmp / "emptycorpus" / "src" / name).write_text(
            f'"""Модуль {name}."""\n\n\ndef f_{name.split(".")[0]}(x):\n    return x\n',
            encoding="utf-8", newline="\n")
    (tmp / "emptycorpus" / "docs" / "licence.md").write_text(DOC_LIC, encoding="utf-8",
                                                            newline="\n")
    (tmp / "emptycorpus" / ".gitignore").write_text("probe\n", encoding="utf-8",
                                                   newline="\n")
    cfg2_path, _ = create_config(tmp / "emptycorpus", project="emptyprobe",
                                 config_path=tmp / "emptycorpus" / "corpus.yaml")
    text2 = cfg2_path.read_text(encoding="utf-8").replace("joern_enabled: true",
                                                          "joern_enabled: false")
    cfg2_path.write_text(text2, encoding="utf-8", newline="\n")
    cfg2 = load_config(cfg2_path)
    indexer = Indexer(cfg2, mode="fast")
    try:
        indexer.run(with_vectors=False)
    finally:
        indexer.close()
    os.environ[edition.BASELINE_ENV] = str(variant)
    try:
        fc2 = Facade(cfg2, tool="selftest", telemetry=False, watch=False)
        try:
            by_layer = fc2.op_search("licence", limit=5, mode="docs")
        finally:
            fc2.close()
    finally:
        os.environ.pop(edition.BASELINE_ENV, None)
    layer_block = by_layer.get("degraded") or {}
    print(f"  пустой слой: {layer_block.get('code')} — "
          f"{str(layer_block.get('reason'))[:70]}")
    check(layer_block.get("code") == "layer_empty",
          "пустой слой назван своим кодом, а не «нет модели»", str(layer_block)[:160])

    # Темы читаются из слоя, и модель им не нужна. Значит и отказ обязан называть
    # слой, а не ненастроенный LLM: иначе читатель пойдёт настраивать ключи, которые
    # этому режиму не требуются (замер: до правки порядка проверок так и было).
    os.environ[edition.BASELINE_ENV] = str(variant)
    try:
        probe = Facade(cfg2, tool="selftest", telemetry=False, watch=False)
        try:
            themes_no_llm = probe.op_search("licence", limit=5, mode="themes")
        finally:
            probe.close()
    finally:
        os.environ.pop(edition.BASELINE_ENV, None)
    themes_block = themes_no_llm.get("degraded") or {}
    check(themes_block.get("code") == "layer_empty",
          "themes без модели называет слой, а не модель", str(themes_block)[:160])

    fc3 = Facade(cfg2, tool="selftest", telemetry=False, watch=False)
    try:
        by_edition = fc3.op_search("licence", limit=5, mode="docs")
    finally:
        fc3.close()
    edition_block = by_edition.get("degraded") or {}
    print(f"  без издания: {edition_block.get('code')} — "
          f"{str(edition_block.get('reason'))[:70]}")
    check(edition_block.get("code") == "edition",
          "под Community уровень «тема» назван недоступным", str(edition_block)[:160])
    check("pro" in str(edition_block.get("reason")),
          "причина называет издание", str(edition_block.get("reason"))[:120])
    check("result" in by_edition,
          "базовая выдача под Community не отменяется: поиск не платный",
          str(list(by_edition.keys()))[:160])

    # ---------------- 5. режим code ----------------
    print()
    print("=" * 78)
    print("5. РЕЖИМ CODE ФИЛЬТРУЕТ МАТЕРИАЛ")
    print("=" * 78)
    fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
    try:
        code_only = fc.op_search("licence_file", limit=10, mode="code")
    finally:
        fc.close()
    materials = {(item.get("material") or "") for item in code_only.get("result") or []}
    check(materials <= {"code"} or not materials,
          "в выдаче только материал кода", str(materials))
    check(any("режим code" in str(note) for note in code_only.get("limits") or []),
          "сколько скрыто — названо", str(code_only.get("limits"))[-160:])

    # ---------------- 6. отказ по режиму ----------------
    print()
    print("=" * 78)
    print("6. НЕИЗВЕСТНЫЙ РЕЖИМ — ОТКАЗ, А НЕ ПОДМЕНА AUTO")
    print("=" * 78)
    fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
    try:
        bad = fc.op_search("лицензия", limit=5, mode="bogus")
    finally:
        fc.close()
    check(bad.get("found") is False and "неизвестное значение mode" in str(bad.get("error")),
          "фасад отказал с названием допустимых значений", str(bad.get("error"))[:140])
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    # Запрос — позиционный аргумент `query`. Через `--text` argparse принимает
    # `--text` за сокращение `--text-width` и падает на "invalid int value".
    cli = subprocess.run([sys.executable, str(REPO / "tools" / "ragraf.py"),
                          "-p", str(cfg_path), "query", "search", "лицензия",
                          "--mode", "bogus"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=env, timeout=300)
    check(cli.returncode == 2, "CLI: код ошибки вызова", f"код {cli.returncode}")
    # Отказ приходит от argparse (объявление `--mode` с закрытым перечнем), а не
    # от `resolve_mode`: проверка называет ФАКТИЧЕСКИЙ текст, иначе она охраняла бы
    # несуществующую ветку.
    check("invalid choice" in (cli.stderr or "")
          or "неверный параметр" in (cli.stderr or ""),
          "CLI: отказ объясняет допустимые значения",
          (cli.stderr or "").strip().splitlines()[-1][:160])

    # ---------------- 7. темы ----------------
    print()
    print("=" * 78)
    print("7. РЕЖИМ THEMES ГОВОРИТ, ЧТО ТЕМ ЕЩЁ НЕТ")
    print("=" * 78)
    os.environ[edition.BASELINE_ENV] = str(variant)
    try:
        fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        try:
            themes = fc.op_search("лицензия", limit=5, mode="themes")
        finally:
            fc.close()
    finally:
        os.environ.pop(edition.BASELINE_ENV, None)
    theme_block = themes.get("degraded") or {}
    check(theme_block.get("code") == "themes_not_built",
          "темы не выданы за собранные", str(theme_block)[:160])
    check(("этапом 5" in str(theme_block.get("text"))
           or "этап 5" in str(theme_block.get("text"))),
          "причина называет, когда темы появятся", str(theme_block.get("text"))[:140])

    # ---------------- 8. два уровня одним ответом ----------------
    print()
    print("=" * 78)
    print("8. DUAL-LEVEL: ЛОКАЛЬНЫЕ ФАКТЫ И ГЛОБАЛЬНЫЙ УРОВЕНЬ СО СВЯЗЯМИ")
    print("=" * 78)
    os.environ[edition.BASELINE_ENV] = str(variant)
    try:
        fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        try:
            both = fc.op_doc_search_levels("licence_file", limit=10)
            only_local = fc.op_doc_search_levels("licence_file", limit=10,
                                                 mode="local")
            only_global = fc.op_doc_search_levels("licence_file", limit=10,
                                                  mode="global")
        finally:
            fc.close()
    finally:
        os.environ.pop(edition.BASELINE_ENV, None)

    result = both.get("result") or {}
    local = result.get("local") or {}
    glob = result.get("global") or {}
    hits = local.get("hits") or []
    entities = glob.get("entities") or []
    relations = glob.get("relations") or []
    print(f"  mode=both: локальных хитов {len(hits)}, сущностей {len(entities)}, "
          f"связей {len(relations)}")
    check({"local", "global"} <= set(result),
          "both отдаёт ОБА уровня, а не один из них", str(sorted(result)))
    check(bool(hits), "локальный уровень не пуст: сравнивать есть что", str(len(hits)))
    check(all(str(item.get("locator") or "").count(":") >= 1 for item in hits),
          "у каждого локального факта адрес path:строка",
          str([item.get("locator") for item in hits[:2]]))
    check(all(int(item.get("start_line") or 0) > 0 for item in hits),
          "адрес локального факта содержит номер строки",
          str([item.get("start_line") for item in hits[:3]]))
    check(glob.get("found") is True,
          "глобальный уровень найден: иначе связывать нечего",
          str(glob.get("reason"))[:160])
    check(bool(entities), "сущности глобального уровня есть", str(len(entities)))
    check(all((item.get("via_chunk") or {}).get("chunk_id") for item in entities),
          "у каждой сущности назван чанк, через который она получена",
          str(entities[:1])[:200])
    check(all(str((item.get("via_chunk") or {}).get("locator") or "").count(":") >= 1
              for item in entities),
          "у каждой сущности есть адрес цитаты, а не только имя",
          str(entities[:1])[:200])
    chunk_ids = {str(item.get("chunk_id")) for item in hits}
    linked = {str((item.get("via_chunk") or {}).get("chunk_id")) for item in entities}
    check(linked <= chunk_ids,
          "via_chunk ссылается на локальные факты этой же выдачи",
          f"лишние: {sorted(linked - chunk_ids)[:3]}")
    rel_addresses_ok = all(str((rel.get("citations") or [""])[0]).count(":") >= 1
                            for rel in relations)
    check(rel_addresses_ok, "у каждой связи есть адрес цитаты",
          str(relations[:1])[:200])
    rel_links_ok = all(len(rel.get("via_entities") or []) == 2 for rel in relations)
    check(rel_links_ok, "у связи названы обе сущности (via_entities)",
          str([rel.get("via_entities") for rel in relations[:1]])[:200])
    marks = [mark for item in hits for mark in item.get("via_entities") or []]
    check(all(str(mark.get("locator") or "").count(":") >= 1 for mark in marks),
          "у сущности в локальном факте есть её собственный адрес",
          str(marks[:1])[:200])
    links = glob.get("links") or {}
    linked_hits = sum(1 for item in hits if item.get("via_entities"))
    check(links.get("hits_with_entities") == linked_hits,
          "счётчик связанных хитов совпадает с фактом",
          f"{links.get('hits_with_entities')} против {linked_hits}")
    counts_ok = (links.get("entities") == len(entities)
                 and links.get("relations") == len(relations))
    check(counts_ok, "счётчики уровня совпадают с числом фактов в ответе",
          f"{links} против entities={len(entities)} relations={len(relations)}")
    check("global" not in (only_local.get("result") or {}),
          "mode=local не считает и не показывает глобальный уровень",
          str(sorted(only_local.get("result") or {})))
    check(only_local.get("found") is True and "error" not in only_local,
          "mode=local работает без графа и без отказа", str(only_local.get("error"))[:120])
    check("local" not in (only_global.get("result") or {}),
          "mode=global отдаёт только глобальный уровень",
          str(sorted(only_global.get("result") or {})))
    check((only_global.get("result") or {}).get("global", {}).get("found") is True,
          "mode=global находит те же сущности",
          str((only_global.get("result") or {}).get("global", {}).get("reason"))[:160])

    # ---------------- 9. пустой слой и «связей нет» — разные ответы ----------------
    print()
    print("=" * 78)
    print("9. ПУСТОЙ СЛОЙ НЕ ВЫДАЁТСЯ ЗА «СВЯЗЕЙ НЕТ»")
    print("=" * 78)
    os.environ[edition.BASELINE_ENV] = str(variant)
    try:
        fc2b = Facade(cfg2, tool="selftest", telemetry=False, watch=False)
        try:
            empty_both = fc2b.op_doc_search_levels("licence", limit=5)
            empty_global = fc2b.op_doc_search_levels("licence", limit=5, mode="global")
            empty_local = fc2b.op_doc_search_levels("licence", limit=5, mode="local")
        finally:
            fc2b.close()
    finally:
        os.environ.pop(edition.BASELINE_ENV, None)
    eblock = (empty_both.get("result") or {}).get("global") or {}
    print(f"  пустой слой: {eblock.get('code')} — {str(eblock.get('reason'))[:70]}")
    check(eblock.get("code") == "layer_empty",
          "пустой слой назван своим кодом", str(eblock)[:200])
    check((empty_both.get("result") or {}).get("local", {}).get("found") is True,
          "в режиме both локальные факты выданы при пустом слое")
    check(any("неполон" in str(note) or "пустой" in str(note)
              for note in empty_both.get("limits") or []),
          "режим both помечает неполноту в limits",
          str(empty_both.get("limits"))[-200:])
    check(empty_global.get("found") is False and not empty_global.get("error"),
          "mode=global: found=false и НЕТ error (это не сбой, а состояние)",
          f"found={empty_global.get('found')} error={empty_global.get('error')}")
    check(((empty_global.get("result") or {}).get("global") or {}).get("code")
          == "layer_empty", "mode=global называет причину пустоты")
    check(empty_local.get("found") is True,
          "пустой слой не мешает локальному уровню", str(empty_local.get("error"))[:120])

    # Слой СОБРАН, но в чанках выдачи упоминаний нет: это «связей нет», и код у
    # него другой. Иначе читатель пойдёт пересобирать слой, который в порядке.
    os.environ[edition.BASELINE_ENV] = str(variant)
    try:
        fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        try:
            code_only = fc.op_doc_search_levels("return path", limit=1)
        finally:
            fc.close()
    finally:
        os.environ.pop(edition.BASELINE_ENV, None)
    code_block = (code_only.get("result") or {}).get("global") or {}
    code_hit = ((code_only.get("result") or {}).get("local", {}).get("hits") or [{}])[0]
    print(f"  чанк без упоминаний ({code_hit.get('source_kind')}): "
          f"{code_block.get('code')} — {str(code_block.get('reason'))[:60]}")
    check(code_hit.get("source_kind") not in (None, "", "doc"),
          "проверка взяла чанк кода, а не документ (слой покрывает документы)",
          str(code_hit.get("source_kind")))
    check(code_block.get("code") == "no_facts_in_chunks",
          "собранный слой отвечает «связей нет» своим кодом, а не «слой пуст»",
          str(code_block)[:200])

    # ---------------- 10. гейт: только глобальный уровень ----------------
    print()
    print("=" * 78)
    print("10. ГЕЙТ ИЗДАНИЯ: ГЛОБАЛЬНЫЙ УРОВЕНЬ ЗАКРЫТ, ЛОКАЛЬНЫЙ — НЕТ")
    print("=" * 78)
    fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
    try:
        denied_global = fc.op_doc_search_levels("licence_file", limit=5, mode="global")
        denied_both = fc.op_doc_search_levels("licence_file", limit=5, mode="both")
        denied_local = fc.op_doc_search_levels("licence_file", limit=5, mode="local")
    finally:
        fc.close()
    refused = denied_global.get("refused") or {}
    check(denied_global.get("found") is False
          and "EditionRefusal" in str(denied_global.get("error")),
          "под Community mode=global отказывает с причиной издания",
          str(denied_global.get("error"))[:160])
    check(refused.get("feature") == "doc-graph"
          and str(refused.get("requires_edition") or "") == "pro",
          "отказ называет возможность и нужное издание", str(refused)[:200])
    check((denied_both.get("result") or {}).get("global", {}).get("code") == "edition",
          "в режиме both глобальный уровень назван недоступным",
          str((denied_both.get("result") or {}).get("global"))[:200])
    check((denied_both.get("result") or {}).get("local", {}).get("found") is True,
          "режим both под Community отдаёт локальные факты (поиск бесплатен)")
    check(denied_local.get("found") is True and "degraded" not in denied_local,
          "локальный уровень не спрашивает издание вовсе",
          str(denied_local.get("degraded"))[:160])

    # ---------------- 11. поверхности и отказ по режиму ----------------
    print()
    print("=" * 78)
    print("11. ПОВЕРХНОСТИ: MCP, CLI, БАЗОВАЯ ЛИНИЯ ИЗДАНИЙ")
    print("=" * 78)
    from ragraf import mcp_server  # noqa: E402

    name = "ragraf_doc_search_levels"
    tool_names = [tool.get("name") for tool in mcp_server.TOOLS]
    check(name in tool_names, "инструмент объявлен в TOOLS", str(len(tool_names)))
    check(name in mcp_server._TOOL_META,
          "у инструмента есть метаданные (readOnlyHint)")
    check(bool((mcp_server._TOOL_META.get(name) or {}).get("readOnlyHint")),
          "инструмент объявлен только для чтения")
    handler_names = getattr(mcp_server, "_handlers", None)
    if isinstance(handler_names, dict):
        check(name in handler_names, "у инструмента есть обработчик")
    else:
        src_text = (SRC / "ragraf" / "mcp_server.py").read_text(encoding="utf-8")
        check(f'"{name}": lambda' in src_text,
              "обработчик объявлен в таблице _handlers", "по тексту файла")
    tools_schema = next((tool for tool in mcp_server.TOOLS
                         if tool.get("name") == name), {})
    props = ((tools_schema.get("inputSchema") or {}).get("properties") or {})
    check(sorted((props.get("mode") or {}).get("enum") or []) == ["both", "global", "local"],
          "схема инструмента объявляет три режима и только их",
          str((props.get("mode") or {}).get("enum")))
    baseline = json.loads(Path(edition.baseline_path()).read_text(encoding="utf-8"))
    declared = [item for item in baseline.get("features") or []
                if "doc_search_levels" in (item.get("operations") or [])]
    check(len(declared) == 1 and declared[0].get("edition") == "community",
          "операция объявлена в базовой линии изданий (community)",
          str([d.get("name") for d in declared]))

    fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
    try:
        bad_mode = fc.op_doc_search_levels("лицензия", limit=5, mode="bogus")
    finally:
        fc.close()
    check(bad_mode.get("found") is False
          and "неизвестное значение mode" in str(bad_mode.get("error")),
          "неизвестный режим — отказ вызова, а не подмена both",
          str(bad_mode.get("error"))[:160])
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    cli = subprocess.run([sys.executable, str(REPO / "tools" / "ragraf.py"),
                          "-p", str(cfg_path), "query", "doc_search_levels",
                          "лицензия", "--mode", "bogus"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=env, timeout=300)
    check(cli.returncode == 2, "CLI: код ошибки вызова на неизвестном режиме",
          f"код {cli.returncode}")
    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
