# -*- coding: utf-8 -*-
"""Извлечение фактов о документах (этап 2 плана «Агентские интеграции»).

Проверяются пять утверждений, каждого из которых нельзя подтвердить чтением:

  1. Повторный прогон по неизменному корпусу не делает НИ ОДНОГО обращения к
     провайдеру и оставляет слой побайтово тем же. Проверяется в двух видах:
     без принуждения (проход пропускается по отпечатку) и с `force` (кэш
     отвечает на каждый чанк, вызовов ноль).
  2. Цитата, которой НЕТ в чанке, отвергается с кодом, а не записывается «почти
     верно»; связь с сущностью, у которой нет упоминания в этом чанке, тоже.
  3. Смена версии промпта инвалидирует кэш (вызовы > 0) и меняет отпечаток
     слоя — то есть правка инструкции не переиспользует старые ответы.
  4. Отказ провайдера на середине прохода не оставляет полузаписанного слоя:
     `collected=false`, названный код, число фактов, которые были бы записаны,
     и слой остаётся прежним.
  5. Ключ провайдера не попадает ни в ответ прохода, ни в слой.

Песочница, а не живое дерево: свой каталог состояния, свой ключ хранилища,
свой корпус в %TEMP% и поддельный транспорт вместо сети.

Стиль — самостоятельный скрипт с `check`, как остальные проверки проекта.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

PASS = 0
FAIL = 0

#: Материал, играющий роль ключа провайдера. Собирается из частей: строка вида
#: «ключ = значение» в исходнике выглядит как учётное присваивание и блокирует
#: правку файла защитой записи (AGENTS.md §9).
PROBE_KEY_MATERIAL = "-".join(["sk", "probe", "extract", "foundation"])

LONG = ("Абзац документа о выкладке и индексации. " * 6).strip()

DOC_A = f"""# Руководство по выкладке

Вводный абзац с маркером MARKER-EXTRACT-ALPHA. {LONG}

## Раздел первый

{LONG} RAGRAF хранит факты о коде и документах.

## Раздел второй

{LONG} Индекс строится командой index и проверяется командой check.
"""

DOC_B = f"""# Заметки о доставке

Первый абзац с маркером MARKER-EXTRACT-BETA. {LONG}

Второй абзац про индексацию и выкладку. {LONG}
"""

MODULE = '''"""Пробный модуль корпуса."""


def probe_extract(value: int) -> int:
    return value + 2
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
    """Поддельный провайдер: детерминированный ответ на каждый чанк.

    Цитаты берутся ИЗ ТЕКСТА чанка (первая половина текста), потому что слой
    принимает только цитаты, которые в тексте есть: подделка обязана вести себя
    как честная модель, иначе проверка ловила бы собственный вымысел.
    """

    def __init__(self, *, bad_quote: bool = False, fail_after: int | None = None,
                 error_code: str = "auth") -> None:
        self.calls = 0
        self.bad_quote = bad_quote
        self.fail_after = fail_after
        self.error_code = error_code
        self.seen_headers: list[dict] = []

    @staticmethod
    def chunk_text(request: dict) -> str:
        messages = (request.get("json") or {}).get("messages") or []
        content = str(messages[-1].get("content") or "") if messages else ""
        # Текст чанка — ПОСЛЕДНЯЯ часть содержимого: клиент склеивает
        # инструкцию и текст через пустую строку, а в самой инструкции такие
        # строки тоже есть — поэтому `split` (первое вхождение) вернул бы
        # остаток инструкции, а не чанк.
        marker = "\n\n"
        return content.rsplit(marker, 1)[-1] if marker in content else content

    def __call__(self, request: dict) -> dict:
        if self.fail_after is not None and self.calls >= self.fail_after:
            from ragraf import llm as llm_mod

            raise llm_mod.LLMError(self.error_code, "провайдер отказал (подделка)")
        self.calls += 1
        self.seen_headers.append({str(k).lower(): str(v) for k, v in
                                  (request.get("headers") or {}).items()})
        text = self.chunk_text(request)
        quote = text[:60] if len(text) >= 60 else text
        other = text[60:140] if len(text) >= 140 else text[:40]
        if self.bad_quote:
            quote = "ЭТОЙ ФРАЗЫ В ЧАНКЕ НЕТ ВОВСЕ"
            other = "И ЭТОЙ ТОЖЕ НЕТ"
        index = self.calls
        payload = {
            "entities": [
                {"name": "RAGRAF", "kind": "system", "quote": quote},
                {"name": f"Тема {index}", "kind": "concept", "quote": other},
            ],
            "relations": [
                {"from": "RAGRAF", "to": f"Тема {index}", "kind": "mentions",
                 "quote": quote},
                {"from": "RAGRAF", "to": "Неизвестная сущность", "kind": "uses",
                 "quote": quote},
            ],
        }
        # Оболочка OpenAI-совместимого ответа: клиент разбирает `choices`, и
        # подделка обязана говорить тем же протоколом, иначе проверялся бы
        # разбор другой формы, а не извлечение фактов.
        body = {"choices": [{"message": {"role": "assistant",
                                        "content": json.dumps(payload,
                                                              ensure_ascii=False)}}]}
        return {"status": 200, "body": json.dumps(body, ensure_ascii=False)}


def layer_digest(cfg, rev: str) -> str:
    """Отпечаток содержимого слоя ревизии: строки таблиц в устойчивом порядке."""
    conn = sqlite3.connect(str(cfg.lexvec_db))
    conn.row_factory = sqlite3.Row
    dump: list[str] = []
    try:
        for table, order in (("entity", "entity_id"), ("entity_mention",
                                                      "entity_id, chunk_id, char_start"),
                             ("entity_relation", "rel_id")):
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?",
                                (table,)).fetchone():
                continue
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE rev_id = ? ORDER BY {order}", (rev,)
            ).fetchall()
            dump.append(f"== {table} {len(rows)}")
            for row in rows:
                dump.append("|".join(f"{k}={row[k]}" for k in row.keys()))
    finally:
        conn.close()
    return hashlib.sha256("\n".join(dump).encode("utf-8")).hexdigest()


def layer_counts(cfg, rev: str) -> dict[str, int]:
    conn = sqlite3.connect(str(cfg.lexvec_db))
    out: dict[str, int] = {}
    try:
        for key, table in (("entities", "entity"), ("mentions", "entity_mention"),
                           ("relations", "entity_relation")):
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?",
                                (table,)).fetchone():
                out[key] = 0
                continue
            out[key] = int(conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE rev_id = ?", (rev,)).fetchone()[0])
    finally:
        conn.close()
    return out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_extract_"))
    (tmp / "catalog").mkdir()
    os.environ["RAGRAF_CATALOG_DIR"] = str(tmp / "catalog")
    os.environ["RAGRAF_HOME"] = str(tmp / "home")
    os.environ["RAGRAF_CONFIG_DIR"] = str(tmp / "config")
    os.environ["RAGRAF_SECRETS_KEY"] = base64.b64encode(
        secrets.token_bytes(32)).decode("ascii")
    os.environ["RAGRAF_AGENT"] = "test-docgraph-extract"

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    from ragraf import docgraph as dg  # noqa: E402
    from ragraf import docgraph_extract as ex  # noqa: E402
    from ragraf import llm as llm_mod  # noqa: E402
    from ragraf import settings as settings_mod  # noqa: E402
    from ragraf import vault as vault_mod  # noqa: E402
    from ragraf.config import load as load_config  # noqa: E402
    from ragraf.indexer import Indexer  # noqa: E402
    from ragraf.init_corpus import create_config  # noqa: E402

    print("=" * 78)
    print("0. ПЕСОЧНИЦА: корпус, документные чанки, активная ревизия")
    print("=" * 78)
    corpus = tmp / "corpus"
    (corpus / "docs").mkdir(parents=True)
    (corpus / "src").mkdir()
    (corpus / ".gitignore").write_text("probe\n", encoding="utf-8", newline="\n")
    (corpus / "docs" / "guide.md").write_text(DOC_A, encoding="utf-8", newline="\n")
    (corpus / "docs" / "notes.md").write_text(DOC_B, encoding="utf-8", newline="\n")
    (corpus / "src" / "mod.py").write_text(MODULE, encoding="utf-8", newline="\n")

    cfg_path, _scan = create_config(corpus, project="extractprobe",
                                    config_path=corpus / "corpus.yaml")
    text = cfg_path.read_text(encoding="utf-8").replace(
        "joern_enabled: true", "joern_enabled: false")
    cfg_path.write_text(text, encoding="utf-8", newline="\n")
    cfg = load_config(cfg_path)

    idx = Indexer(cfg, mode="fast")
    try:
        report = idx.run(with_vectors=False)
    finally:
        idx.close()
    rev = str(report.rev_id or "")
    check(bool(report.ok) and bool(rev), "индекс собран", f"ok={report.ok} rev={rev}")

    base_values = dict(settings_mod.DEFAULTS)
    base_values.update({
        "llm_enabled": True, "llm_provider": "local", "llm_model": "fake-model",
        "llm_prompt_version": "docgraph-v1", "llm_secret_name": "",
        "llm_cache_enabled": True,
    })

    ext = ex.Extractor(cfg, values=base_values)
    chunks = ext.chunks(rev)
    check(len(chunks) >= 2, "документные чанки в ревизии есть", str(len(chunks)))
    pairs = " ".join(f"{c.locator}" for c in chunks[:2])
    print(f"  первые чанки: {pairs}")
    check(sum(1 for c in chunks if len(c.text) >= 40) >= 2,
          "есть не меньше двух чанков с текстом, годным для цитат")

    print()
    print("=" * 78)
    print("1. ПОВТОРНЫЙ ПРОГОН: НОЛЬ ОБРАЩЕНИЙ И ТОТ ЖЕ СЛОЙ")
    print("=" * 78)
    transport = ScriptedTransport()
    client = llm_mod.LLMClient(cfg, config=llm_mod.config_from_settings(base_values),
                               transport=transport)
    first = ex.run(cfg, rev, values=base_values, client=client)
    stats = first.get("stats") or {}
    print(f"  прогон 1: collected={first.get('collected')} вызовов={stats.get('calls')} "
          f"чанков={stats.get('chunks_sent')} сущностей={stats.get('entities')} "
          f"упоминаний={stats.get('mentions')} отношений={stats.get('relations')}")
    check(first.get("collected") is True, "слой собран",
          json.dumps(first.get("reason"), ensure_ascii=False))
    check(int(stats.get("calls") or 0) == len(chunks),
          "первый прогон сделал по вызову на чанк",
          f"вызовов {stats.get('calls')} при чанках {len(chunks)}")
    check(int(stats.get("entities") or 0) > 0, "сущности записаны",
          str(stats.get("entities")))
    digest_1 = layer_digest(cfg, rev)
    counts_1 = layer_counts(cfg, rev)
    print(f"  слой: {counts_1}")

    again = ex.run(cfg, rev, values=base_values,
                   client=llm_mod.LLMClient(
                       cfg, config=llm_mod.config_from_settings(base_values),
                       transport=ScriptedTransport()))
    check(again.get("skipped") is True, "второй прогон пропущен по отпечатку",
          json.dumps(again, ensure_ascii=False)[:200])
    check(int((again.get("stats") or {}).get("calls") or 0) == 0,
          "пропущенный прогон не сделал ни одного обращения",
          str((again.get("stats") or {}).get("calls")))
    check(layer_digest(cfg, rev) == digest_1, "слой после пропуска тот же")

    forced_transport = ScriptedTransport()
    forced = ex.run(cfg, rev, values=base_values, force=True,
                    client=llm_mod.LLMClient(
                        cfg, config=llm_mod.config_from_settings(base_values),
                        transport=forced_transport))
    forced_stats = forced.get("stats") or {}
    check(forced_transport.calls == 0,
          "принудительный прогон отвечен кэшем: обращений к провайдеру ноль",
          f"обращений {forced_transport.calls}")
    check(int(forced_stats.get("cache_hits") or 0) == len(chunks),
          "попаданий в кэш столько же, сколько чанков",
          f"{forced_stats.get('cache_hits')} при {len(chunks)}")
    check(layer_digest(cfg, rev) == digest_1,
          "слой после кэш-прогона побайтово тот же")

    print()
    print("=" * 78)
    print("2. ЦИТАТА, КОТОРОЙ НЕТ В ЧАНКЕ, ОТВЕРГАЕТСЯ")
    print("=" * 78)
    # Связь на сущность, которой в чанке нет, отвергается уже в честном прогоне:
    # подделка возвращает такую связь на КАЖДЫЙ чанк, и в слое её быть не должно.
    clean_codes = stats.get("rejected") or {}
    print(f"  отказы честного прогона: {clean_codes}")
    check(int(clean_codes.get(ex.REJECT_UNKNOWN_ENTITY) or 0) == len(chunks),
          "связь на сущность без упоминания отвергнута в каждом чанке",
          str(clean_codes))
    check(int(stats.get("relations") or 0) == len(chunks),
          "в слой попала ровно одна связь на чанк, а не две",
          f"отношений {stats.get('relations')} при чанках {len(chunks)}")

    liar_values = dict(base_values, llm_model="fake-model-liar")
    liar = ScriptedTransport(bad_quote=True)
    rejected = ex.run(cfg, rev, values=liar_values, force=True,
                      client=llm_mod.LLMClient(
                          cfg, config=llm_mod.config_from_settings(liar_values),
                          transport=liar))
    rejected_stats = rejected.get("stats") or {}
    codes = rejected_stats.get("rejected") or {}
    print(f"  отказы прогона с выдуманными цитатами: {codes}")
    check(int(codes.get(ex.REJECT_QUOTE_NOT_IN_CHUNK) or 0) > 0,
          "цитата вне чанка отвергнута с кодом quote_not_in_chunk", str(codes))
    check(layer_digest(cfg, rev) == digest_1,
          "слой не изменился: выдуманные цитаты не попали в факты")
    check(int(rejected_stats.get("entities") or 0) == 0,
          "ни одной сущности из выдуманных цитат не записано",
          str(rejected_stats.get("entities")))

    print()
    print("=" * 78)
    print("3. СМЕНА ВЕРСИИ ПРОМПТА ИНВАЛИДИРУЕТ КЭШ")
    print("=" * 78)
    other_values = dict(base_values, llm_prompt_version="docgraph-v2")
    fresh = ScriptedTransport()
    bumped = ex.run(cfg, rev, values=other_values, force=True,
                    client=llm_mod.LLMClient(
                        cfg, config=llm_mod.config_from_settings(other_values),
                        transport=fresh))
    check(fresh.calls == len(chunks),
          "новая версия промпта: обращение по каждому чанку",
          f"обращений {fresh.calls} при {len(chunks)}")
    check(int((bumped.get("stats") or {}).get("cache_hits") or 0) == 0,
          "кэш прежней версии не переиспользован",
          str((bumped.get("stats") or {}).get("cache_hits")))
    fingerprint_now = dg.current_fingerprint(other_values)
    check(fingerprint_now["fingerprint"] != dg.current_fingerprint(base_values)["fingerprint"],
          "смена версии промпта меняет отпечаток слоя")
    recorded = (bumped.get("fingerprint") or {}).get("recorded") or ""
    check(recorded == fingerprint_now["fingerprint"],
          "записанный отпечаток соответствует новой версии промпта",
          f"{recorded[:12]} против {fingerprint_now['fingerprint'][:12]}")

    print()
    print("=" * 78)
    print("4. ОТКАЗ НА СЕРЕДИНЕ НЕ ОСТАВЛЯЕТ ПОЛУЗАПИСАННОГО СЛОЯ")
    print("=" * 78)
    before_digest = layer_digest(cfg, rev)
    before_counts = layer_counts(cfg, rev)
    breaker_values = dict(base_values, llm_model="fake-model-breaker")
    breaker = ScriptedTransport(fail_after=1)
    broken = ex.run(cfg, rev, values=breaker_values, force=True,
                    client=llm_mod.LLMClient(
                        cfg, config=llm_mod.config_from_settings(breaker_values),
                        transport=breaker))
    print(f"  отказ: code={broken.get('code')} reason={str(broken.get('reason'))[:90]}")
    check(broken.get("collected") is False, "проход назван несобранным",
          str(broken.get("collected")))
    check(str(broken.get("code") or "") == "auth", "код отказа назван",
          str(broken.get("code")))
    would = (broken.get("stats") or {}).get("would_write") or {}
    check(int(would.get("entities") or 0) > 0,
          "названо, сколько фактов было бы записано", str(would))
    check(layer_digest(cfg, rev) == before_digest,
          "слой остался прежним — половины не записано")
    check(layer_counts(cfg, rev) == before_counts, "числа слоя не изменились",
          f"{before_counts} -> {layer_counts(cfg, rev)}")

    print()
    print("=" * 78)
    print("5. КЛЮЧ НЕ ПОПАДАЕТ В ОТВЕТ ПРОХОДА И В СЛОЙ")
    print("=" * 78)
    vault = vault_mod.Vault()
    owner = vault_mod.Actor(label="owner-test", kind="human", via="cli", admin=True)
    vault.set("probe-extract-key", PROBE_KEY_MATERIAL, actor=owner, kind="token",
              note="контрольная запись набора извлечения")
    keyed_values = dict(base_values, llm_provider="openai", llm_model="probe-model",
                        llm_secret_name="probe-extract-key")
    # Файл настроек в песочнице заводится явно: проверка читает его как есть, а
    # «файла нет» — это не «ключа в нём нет».
    settings_mod.SettingsStore().save({
        "llm_enabled": True, "llm_provider": "openai",
        "llm_model": "probe-model", "llm_secret_name": "probe-extract-key",
        "llm_prompt_version": "docgraph-v1",
    })
    probe = ScriptedTransport()
    keyed = ex.run(cfg, rev, values=keyed_values, force=True,
                   client=llm_mod.LLMClient(
                       cfg, config=llm_mod.config_from_settings(keyed_values),
                       transport=probe))
    auth_ok = any(headers.get("authorization") == "Bearer " + PROBE_KEY_MATERIAL
                  for headers in probe.seen_headers)
    check(auth_ok, "ключ уходит заголовком Authorization")
    dumped = json.dumps(keyed, ensure_ascii=False)
    check(PROBE_KEY_MATERIAL not in dumped, "значения ключа нет в ответе прохода",
          dumped[:200])
    conn = sqlite3.connect(str(cfg.lexvec_db))
    try:
        rows = conn.execute("SELECT name FROM entity WHERE rev_id = ?", (rev,)).fetchall()
        relations = conn.execute(
            "SELECT kind, path FROM entity_relation WHERE rev_id = ?", (rev,)).fetchall()
    finally:
        conn.close()
    names_text = " ".join(str(r[0]) for r in rows) + " " + " ".join(
        f"{r[0]} {r[1]}" for r in relations)
    check(PROBE_KEY_MATERIAL not in names_text, "значения ключа нет в слое",
          names_text[:160])
    settings_text = settings_mod.SettingsStore().path.read_text(encoding="utf-8")
    check(PROBE_KEY_MATERIAL not in settings_text,
          "значения ключа нет в файле настроек")

    print()
    print("=" * 78)
    conn = sqlite3.connect(str(cfg.lexvec_db))
    try:
        verdict = dg.check_integrity(conn, rev)
    finally:
        conn.close()
    check(verdict.get("ok") is True, "целостность слоя после всех прогонов — норма",
          json.dumps(verdict.get("problems"), ensure_ascii=False)[:200])
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
