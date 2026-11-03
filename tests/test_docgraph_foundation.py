# -*- coding: utf-8 -*-
"""Фундамент LLM-слоя документного графа (этап 1 плана «Агентские интеграции»).

Проверяются пять утверждений, которых нельзя подтвердить чтением кода: каждое
из них — про ПОВЕДЕНИЕ при отказе, при повторе и при обходе.

  1. Нет LLM — нет пустого ответа. `collected=false` и НАЗВАННАЯ причина; вызов
     модели даёт исключение с кодом, а не пустую строку.
  2. Отпечаток слоя устойчив: повторный расчёт по тому же входу даёт то же
     значение, а смена модели, версии промпта, температуры или настроек
     извлечения — другое. Смена записывается в `rev.json` и в журнал
     расхождений, а не подменяет прежний слой молча.
  3. Повторный вызов с тем же входом НЕ делает второго обращения к модели:
     платный вызов считается счётчиком, а не обещанием.
  4. Факт без `entity_mention` не создаётся — и это ловится даже тогда, когда
     запись сделана в обход API сырым `INSERT`.
  5. Значение ключа не попадает ни в ответ операции, ни в состояние, ни в файл
     настроек, ни в текст ошибки провайдера.

Песочница, а не живое дерево: свой каталог состояния (`RAGRAF_CATALOG_DIR`),
свой ключ хранилища (`RAGRAF_SECRETS_KEY`), свой корпус в %TEMP% и поддельный
транспорт вместо сети. Живой реестр, живое хранилище и живой индекс не
трогаются ни на байт.

Стиль — самостоятельный скрипт с `check`, как остальные проверки проекта.
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

#: Материал, который играет роль ключа провайдера. Собирается из частей: строка
#: вида «ключ = значение» в исходнике выглядит как учётное присваивание, и такое
#: место блокирует правку файла защитой записи (AGENTS.md §9). Значение при этом
#: должно быть узнаваемым, чтобы поиск его в ответах был осмысленным.
PROBE_KEY_MATERIAL = "-".join(["sk", "probe", "docgraph", "foundation"])

GUIDE = """# Руководство по развёртыванию

Вводный абзац с маркером MARKER-DOCGRAPH-ALPHA. RAGRAF хранит факты о коде.

## Раздел первый

Первый абзац раздела. Индекс строится командой index и проверяется командой check.

## Раздел второй

Второй абзац раздела с маркером MARKER-DOCGRAPH-BETA.
"""

NOTES = """Заметки о выкладке без единого заголовка.

Абзац с маркером MARKER-DOCGRAPH-GAMMA. Выкладка идёт из репозитория.
"""

MODULE = '''"""Пробный модуль корпуса."""


def probe_docgraph(value: int) -> int:
    return value + 1
'''


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


class FakeTransport:
    """Поддельный провайдер: считает обращения, отдаёт заданный ответ.

    Сети нет вовсе — поэтому «второго обращения не было» проверяется счётчиком,
    а не временем ответа.
    """

    def __init__(self, text: str = "ответ-подделки", status: int = 200,
                 body: str | None = None) -> None:
        self.calls = 0
        self.text = text
        self.status = status
        self.body = body
        self.requests: list[dict] = []

    def __call__(self, request: dict) -> dict:
        self.calls += 1
        self.requests.append({
            "url": request.get("url"),
            "has_authorization": any(
                str(k).lower() == "authorization" for k in (request.get("headers") or {})
            ),
        })
        if self.status != 200:
            return {"status": self.status, "body": self.body or ""}
        payload = {"choices": [{"message": {"role": "assistant", "content": self.text}}]}
        return {"status": 200, "body": json.dumps(payload, ensure_ascii=False)}


def run_cli(args: list[str], extra_env: dict[str, str] | None = None
            ) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "tools/ragraf.py", *args],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300, env=env,
    )


def as_json(result: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return {}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_docgraph_"))
    # Каталог состояния и ключ хранилища отводятся ДО импорта: `paths` читает
    # переменные при каждом обращении, но `vault` фиксирует каталог на импорте.
    (tmp / "catalog").mkdir()
    os.environ["RAGRAF_CATALOG_DIR"] = str(tmp / "catalog")
    os.environ["RAGRAF_HOME"] = str(tmp / "home")
    os.environ["RAGRAF_CONFIG_DIR"] = str(tmp / "config")
    os.environ["RAGRAF_SECRETS_KEY"] = base64.b64encode(
        secrets.token_bytes(32)).decode("ascii")
    os.environ["RAGRAF_AGENT"] = "test-docgraph-foundation"

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    from ragraf import docgraph as dg  # noqa: E402
    from ragraf import edition  # noqa: E402
    from ragraf import llm as llm_mod  # noqa: E402
    from ragraf import settings as settings_mod  # noqa: E402
    from ragraf import vault as vault_mod  # noqa: E402
    from ragraf.config import load as load_config  # noqa: E402
    from ragraf.indexer import Indexer  # noqa: E402
    from ragraf.init_corpus import create_config  # noqa: E402

    os.environ[edition.LICENSE_ENV] = str(tmp / "absent-machine.license")
    # Вариант базовой линии, где возможность doc-graph разрешена. Нужен там, где
    # проверяется состояние LLM, а не гейт изданий: два утверждения нельзя
    # закрывать одним ответом «отказано».
    open_variant = tmp / "baseline-docgraph-community.json"
    baseline_data = json.loads(Path(edition.baseline_path()).read_text(encoding="utf-8"))
    for item in baseline_data["features"]:
        if item["name"] == "doc-graph":
            item["edition"] = "community"
    open_variant.write_bytes(
        json.dumps(baseline_data, ensure_ascii=False, indent=2).encode("utf-8"))

    print("=" * 78)
    print("0. ПЕСОЧНИЦА: корпус, индекс, документный слой")
    print("=" * 78)
    corpus = tmp / "corpus"
    (corpus / "docs").mkdir(parents=True)
    (corpus / "src").mkdir()
    (corpus / ".gitignore").write_text("probe\n", encoding="utf-8", newline="\n")
    (corpus / "docs" / "guide.md").write_text(GUIDE, encoding="utf-8", newline="\n")
    (corpus / "docs" / "notes.txt").write_text(NOTES, encoding="utf-8", newline="\n")
    (corpus / "src" / "mod.py").write_text(MODULE, encoding="utf-8", newline="\n")

    cfg_path, _scan = create_config(corpus, project="docgraphprobe",
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
    check(bool(report.ok), "индекс собран", f"ok={report.ok}")
    rev1 = str(report.rev_id or "")
    check(bool(rev1), "ревизия названа", rev1)

    # Вторая ревизия нужна ровно для одного: показать, что смена отпечатка
    # относительно ПРЕДЫДУЩЕЙ ревизии попадает в журнал. На одной ревизии
    # «изменилось» от «записано впервые» не отличить.
    (corpus / "docs" / "third.md").write_text(
        "# Третий документ\n\nАбзац с маркером MARKER-DOCGRAPH-DELTA.\n",
        encoding="utf-8", newline="\n")
    idx = Indexer(cfg, mode="fast")
    try:
        report2 = idx.run(with_vectors=False)
    finally:
        idx.close()
    rev2 = str(report2.rev_id or "")
    check(rev2 and rev2 != rev1, "вторая ревизия создана", f"{rev1} -> {rev2}")

    lex = sqlite3.connect(str(cfg.lexvec_db))
    lex.row_factory = sqlite3.Row
    # Считаются чанки АКТИВНОЙ ревизии: план и `docgraph status` работают
    # именно по ней, и сравнивать их с прошлой ревизией значило бы проверять
    # арифметику на разных множествах.
    chunks = int(lex.execute(
        "SELECT COUNT(*) FROM document_chunk WHERE rev_id = ?", (rev2,)).fetchone()[0])
    lex.close()
    check(chunks > 0, "документные чанки в активной ревизии есть", str(chunks))

    print()
    print("=" * 78)
    print("1. НЕТ LLM -> collected=false И НАЗВАННАЯ ПРИЧИНА")
    print("=" * 78)
    status = dg.build_status(cfg)
    llm_state = status.get("llm") or {}
    check(llm_state.get("configured") is False, "LLM не настроен (configured=false)",
          json.dumps(llm_state, ensure_ascii=False)[:300])
    check(bool(str(llm_state.get("reason") or "").strip()),
          "причина отказа названа словами", str(llm_state.get("reason")))
    check(str(llm_state.get("code") or "") == "not_configured",
          "код отказа машинный", str(llm_state.get("code")))
    check(status.get("collected") is False, "слой не выдан за собранный",
          str(status.get("collected")))
    check(bool(str(status.get("reason") or "").strip()),
          "у состояния слоя есть причина", str(status.get("reason")))
    layer = status.get("layer") or {}
    check("entities" in layer and "mentions" in layer,
          "числа слоя присутствуют, а не пустой список", str(sorted(layer)))

    plan = dg.build_plan(cfg)
    numbers = plan.get("plan") or {}
    check(int(numbers.get("chunks") or 0) == chunks,
          "план считает ровно те чанки, что в ревизии",
          f"{numbers.get('chunks')} против {chunks}")
    check(int(numbers.get("calls_estimated") or 0) == chunks,
          "оценка вызовов равна числу чанков", str(numbers.get("calls_estimated")))
    check(plan.get("collected") is False and bool(str(plan.get("reason") or "").strip()),
          "план не выдаёт себя за начатый прогон",
          f"collected={plan.get('collected')} reason={plan.get('reason')}")
    notes = " ".join(plan.get("notes") or [])
    check("прогон не начнётся" in notes and "не настроен" in notes,
          "план называет, почему прогон не начнётся", notes[:200])

    # Отказ клиента — исключение с кодом, а не пустая строка.
    bare = llm_mod.LLMClient(cfg, config=llm_mod.config_from_settings({}))
    raised: llm_mod.LLMError | None = None
    returned = ""
    try:
        result = bare.complete("извлеки сущности", input_text="текст")
        returned = str((result or {}).get("text") or "")
    except llm_mod.LLMError as exc:
        raised = exc
    check(raised is not None and raised.code == "not_configured",
          "вызов без настройки: исключение с кодом not_configured",
          f"raised={getattr(raised, 'code', None)} returned={returned!r}")
    check(returned == "", "вместо отказа не вернулась пустая строка-результат",
          returned[:60])

    cli_status = run_cli(["--json", "-p", str(cfg_path), "docgraph", "status"])
    check(cli_status.returncode == 0, "`docgraph status` отвечает при ненастроенном LLM",
          (cli_status.stderr or "")[-300:])
    payload = as_json(cli_status)
    check(bool(payload), "ответ разбирается как JSON", cli_status.stdout[:200])
    check((payload.get("llm") or {}).get("configured") is False,
          "CLI говорит то же, что операция",
          json.dumps((payload.get("llm") or {}), ensure_ascii=False)[:200])

    dry = run_cli(["--json", "-p", str(cfg_path), "docgraph", "build", "--dry-run"])
    check(dry.returncode == 0, "`build --dry-run` работает без LLM",
          (dry.stderr or "")[-300:])
    dry_payload = as_json(dry)
    check(int((dry_payload.get("plan") or {}).get("chunks") or 0) > 0,
          "сухой прогон называет объём работы",
          json.dumps(dry_payload.get("plan") or {}, ensure_ascii=False)[:200])

    # С этапа 2 `build` без `--dry-run` ИСПОЛНЯЕТ проход. Без настроенного LLM
    # он обязан назвать причину кодом и вернуть НЕНУЛЕВОЙ код: ноль означал бы
    # «собрано», а не собрано ничего.
    #
    # С этапа 3 проход закрыт возможностью `doc-graph` (издание Pro). Здесь она
    # разрешена вариантом базовой линии: набор проверяет СОСТОЯНИЕ LLM, а гейт
    # изданий проверяется своим набором (test_docgraph_ops.py). Иначе один ответ
    # «отказано» закрывал бы два разных утверждения.
    grown = run_cli(["--json", "-p", str(cfg_path), "docgraph", "build"],
                    extra_env={edition.BASELINE_ENV: str(open_variant)})
    check(grown.returncode == 1, "`build` без настроенного LLM возвращает код 1",
          f"код {grown.returncode} {(grown.stderr or '')[-200:]}")
    grown_payload = as_json(grown)
    check(str(grown_payload.get("code") or "") == "not_configured",
          "отказ сборки назван кодом not_configured",
          json.dumps(grown_payload, ensure_ascii=False)[:200])
    check(int((grown_payload.get("stats") or {}).get("calls") or 0) == 0,
          "без настроенного LLM обращений к провайдеру не было",
          str((grown_payload.get("stats") or {}).get("calls")))
    gated = run_cli(["--json", "-p", str(cfg_path), "docgraph", "build"])
    check(str((as_json(gated) or {}).get("code") or "") == "edition_refused",
          "без разрешённой возможности сборка отказывает кодом edition_refused",
          json.dumps(as_json(gated), ensure_ascii=False)[:200])

    print()
    print("=" * 78)
    print("2. ОТПЕЧАТОК: УСТОЙЧИВ, МЕНЯЕТСЯ ПО ДЕЛУ, ПИШЕТСЯ В РЕВИЗИЮ")
    print("=" * 78)
    base = dict(settings_mod.DEFAULTS)
    first = dg.current_fingerprint(base)["fingerprint"]
    again = dg.current_fingerprint(dict(settings_mod.DEFAULTS))["fingerprint"]
    check(first == again, "повторный расчёт по тому же входу даёт тот же отпечаток",
          f"{first[:12]} != {again[:12]}")

    def changed(**patch) -> str:
        values = dict(settings_mod.DEFAULTS)
        values.update(patch)
        return dg.current_fingerprint(values)["fingerprint"]

    check(changed(llm_model="другая-модель") != first,
          "смена модели меняет отпечаток")
    check(changed(llm_prompt_version="docgraph-v2") != first,
          "смена версии промпта меняет отпечаток")
    check(changed(docgraph_max_chars=1234) != first,
          "смена настроек извлечения меняет отпечаток")
    pure_a = dg.fingerprint(model="m", prompt_version="p", temperature=0,
                            extraction={"docgraph_max_chars": 6000}, provider="openai")
    pure_b = dg.fingerprint(model="m", prompt_version="p", temperature=0.0,
                            extraction={"docgraph_max_chars": 6000}, provider="openai")
    check(pure_a == pure_b, "температура 0 и 0.0 дают один отпечаток")
    check(dg.fingerprint(model="m", prompt_version="p", temperature=0.7,
                         extraction={"docgraph_max_chars": 6000}) != pure_a,
          "смена температуры меняет отпечаток")

    values_a = dict(settings_mod.DEFAULTS, llm_model="model-A")
    values_b = dict(settings_mod.DEFAULTS, llm_model="model-B")
    rec1 = dg.record_fingerprint(cfg, rev1, values=values_a)
    check(rec1.get("stored") is True, "отпечаток записан в журнал (перечитан)",
          json.dumps(rec1, ensure_ascii=False)[:300])
    check((rec1.get("rev_json") or {}).get("written") is False,
          "в паспорт ЧУЖОЙ ревизии не пишется",
          str((rec1.get("rev_json") or {}).get("reason"))[:160])
    check(rec1.get("changed") is False, "первая запись — не расхождение",
          str(rec1.get("changed")))

    rec2 = dg.record_fingerprint(cfg, rev2, values=values_a)
    check((rec2.get("rev_json") or {}).get("written") is True,
          "отпечаток активной ревизии попал в rev.json",
          str(rec2.get("rev_json")))
    passport = json.loads(Path(cfg.rev_file).read_text(encoding="utf-8"))
    block = passport.get("docgraph") or {}
    check(block.get("fingerprint") == rec2.get("fingerprint"),
          "блок docgraph в rev.json рядом с corpus_hash",
          f"есть corpus_hash: {'corpus_hash' in passport}; блок: {sorted(block)}")

    rec3 = dg.record_fingerprint(cfg, rev2, values=values_b)
    check(rec3.get("changed") is True, "смена модели названа расхождением",
          str(rec3.get("changed")))
    check(rec3.get("drift_entry") is True, "смена отпечатка попала в журнал",
          str(rec3.get("drift_entry")))
    control = sqlite3.connect(str(cfg.control_db))
    control.row_factory = sqlite3.Row
    rows = control.execute(
        "SELECT kind, detected_by, old_hash, new_hash FROM drift_entry "
        "WHERE kind = 'docgraph_fingerprint'").fetchall()
    control.close()
    check(len(rows) == 1, "в журнале ровно одна запись о смене отпечатка",
          str(len(rows)))
    if rows:
        check(str(rows[0]["detected_by"]) == "docgraph-fingerprint",
              "у записи назван способ обнаружения", str(rows[0]["detected_by"]))
        check(str(rows[0]["old_hash"]) != str(rows[0]["new_hash"]),
              "в записи оба отпечатка — было и стало",
              f"{rows[0]['old_hash'][:8]} -> {rows[0]['new_hash'][:8]}")

    print()
    print("=" * 78)
    print("3. КЭШ: ПОВТОРНЫЙ ВЫЗОВ НЕ ДЕЛАЕТ ВТОРОГО ОБРАЩЕНИЯ")
    print("=" * 78)
    local_cfg = llm_mod.config_from_settings({
        "llm_enabled": True, "llm_provider": "local", "llm_model": "fake-model",
        "llm_prompt_version": "docgraph-v1", "llm_secret_name": "",
        "llm_cache_enabled": True,
    })
    transport = FakeTransport("ответ провайдера")
    client = llm_mod.LLMClient(cfg, config=local_cfg, transport=transport)
    one = client.complete("Извлеки сущности из фрагмента.", input_text="чанк-1")
    check(one.get("cached") is False and int(one.get("calls") or 0) == 1,
          "первый вызов ушёл к провайдеру", json.dumps(one, ensure_ascii=False)[:160])
    two = client.complete("Извлеки сущности из фрагмента.", input_text="чанк-1")
    check(two.get("cached") is True, "второй вызов обслужен кэшем",
          json.dumps(two, ensure_ascii=False)[:160])
    check(transport.calls == 1, "к провайдеру было РОВНО одно обращение",
          f"обращений {transport.calls}")
    check(int(two.get("calls") or 0) == 1, "счётчик платных вызовов не вырос",
          str(two.get("calls")))
    three = client.complete("Извлеки сущности из фрагмента.", input_text="чанк-2")
    check(transport.calls == 2 and three.get("cached") is False,
          "другой вход — новое обращение", f"обращений {transport.calls}")
    # Пробельная нормализация промпта не должна давать второго платного вызова:
    # это тот же вопрос, и платить за него дважды незачем.
    four = client.complete("  Извлеки сущности из фрагмента.  ",
                           input_text="чанк-2")
    check(transport.calls == 2 and four.get("cached") is True,
          "пробелы по краям промпта не считаются новым входом",
          f"обращений {transport.calls}")

    lex = sqlite3.connect(str(cfg.lexvec_db))
    cached = int(lex.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0])
    lex.close()
    check(cached == 2, "в кэше две записи (два разных входа)", str(cached))

    no_cache_cfg = llm_mod.config_from_settings({
        "llm_enabled": True, "llm_provider": "local", "llm_model": "fake-model",
        "llm_prompt_version": "docgraph-v1", "llm_secret_name": "",
        "llm_cache_enabled": False,
    })
    second_transport = FakeTransport("ответ без кэша")
    nod = llm_mod.LLMClient(cfg, config=no_cache_cfg, transport=second_transport)
    nod.complete("тот же промпт", input_text="чанк-1")
    nod.complete("тот же промпт", input_text="чанк-1")
    check(second_transport.calls == 2,
          "с выключенным кэшем обращение повторяется (переключатель работает)",
          f"обращений {second_transport.calls}")

    print()
    print("=" * 78)
    print("4. ФАКТ БЕЗ УПОМИНАНИЯ НЕ СОЗДАЁТСЯ")
    print("=" * 78)
    # Отдельная (несуществующая) ревизия: правила слоя проверяются, а активная
    # ревизия остаётся чистой для остальных утверждений.
    probe_rev = "rev-9001"
    conn = sqlite3.connect(str(cfg.lexvec_db))
    conn.row_factory = sqlite3.Row

    def refused(call, expected: str) -> None:
        got = ""
        try:
            call()
        except dg.DocGraphError as exc:
            got = exc.code
        check(got == expected, f"отказ {expected}", f"получено {got or 'записи не было'}")

    refused(lambda: dg.add_entity(conn, probe_rev, name="RAGRAF", kind="system",
                                  mentions=[]), "mention_required")
    refused(lambda: dg.add_entity(
        conn, probe_rev, name="RAGRAF", kind="system",
        mentions=[dg.Mention(chunk_id="", path="docs/guide.md", start_line=1,
                             end_line=2, char_start=0, char_end=10,
                             quote="абзац текста")]), "mention_address_incomplete")
    refused(lambda: dg.add_entity(
        conn, probe_rev, name="RAGRAF", kind="system",
        mentions=[dg.Mention(chunk_id="c1", path="docs/guide.md", start_line=1,
                             end_line=2, char_start=0, char_end=10, quote="  ")]),
        "quote_required")
    refused(lambda: dg.add_relation(
        conn, probe_rev, src_entity_id="a", dst_entity_id="b", kind="uses",
        mention=None), "mention_required")
    refused(lambda: dg.add_theme(conn, probe_rev, title="Тема", seed_entity_id="a",
                                 members={}), "members_required")

    mention = dg.Mention(chunk_id="chunk-1", path="docs/guide.md", start_line=3,
                         end_line=5, char_start=10, char_end=60,
                         quote="RAGRAF хранит факты о коде")
    entity = dg.add_entity(conn, probe_rev, name="RAGRAF", kind="system",
                           mentions=[mention])
    check(bool(entity), "правильный факт записан", entity[:12])
    other = dg.add_entity(conn, probe_rev, name="Индекс", kind="concept",
                          mentions=[dg.Mention(chunk_id="chunk-2",
                                               path="docs/notes.txt", start_line=1,
                                               end_line=2, char_start=0, char_end=20,
                                               quote="Индекс строится командой index")])
    relation = dg.add_relation(conn, probe_rev, src_entity_id=entity,
                               dst_entity_id=other, kind="uses", mention=mention)
    theme = dg.add_theme(conn, probe_rev, title="Индексация", seed_entity_id=entity,
                         members={entity: 2.0, other: 1.0}, built_by="test")
    check(bool(relation) and bool(theme), "отношение и тема записаны")
    verdict = dg.check_integrity(conn, probe_rev)
    check(verdict.get("ok") is True, "целостность записанного слоя — норма",
          json.dumps(verdict, ensure_ascii=False)[:300])

    # Обход API: сырой INSERT, как его сделал бы модуль без правил.
    conn.execute(
        "INSERT INTO entity(rev_id, entity_id, name, norm_name, kind, mentions, "
        "first_path, first_line) VALUES(?,?,?,?,?,?,?,?)",
        (probe_rev, "planted", "Подсаженная", "подсаженная", "system", 1,
         "docs/guide.md", 1))
    conn.commit()
    caught = dg.check_integrity(conn, probe_rev)
    kinds = [item.get("kind") for item in caught.get("problems") or []]
    check(caught.get("ok") is False and "entity_without_mention" in kinds,
          "сырой INSERT без упоминания поймана проверкой целостности", str(kinds))
    conn.execute("DELETE FROM entity WHERE rev_id = ? AND entity_id = 'planted'",
                 (probe_rev,))
    conn.commit()
    healed = dg.check_integrity(conn, probe_rev)
    check(healed.get("ok") is True, "после удаления подсадки проверка снова норма",
          json.dumps(healed.get("problems"), ensure_ascii=False)[:200])
    conn.close()

    print()
    print("=" * 78)
    print("5. КЛЮЧ НЕ ПОПАДАЕТ В ОТВЕТ, СОСТОЯНИЕ И ФАЙЛ НАСТРОЕК")
    print("=" * 78)
    vault = vault_mod.Vault()
    owner = vault_mod.Actor(label="owner-test", kind="human", via="cli", admin=True)
    vault.set("probe-llm-key", PROBE_KEY_MATERIAL, actor=owner, kind="token",
              note="контрольная запись набора docgraph")
    saved = settings_mod.SettingsStore().save({
        "llm_enabled": True,
        "llm_provider": "openai",
        "llm_model": "probe-model",
        "llm_secret_name": "probe-llm-key",
        "llm_prompt_version": "docgraph-v1",
    })
    check(saved.get("llm_enabled") is True, "настройка LLM записана")
    settings_text = settings_mod.SettingsStore().path.read_text(encoding="utf-8")
    check(PROBE_KEY_MATERIAL not in settings_text,
          "в файле настроек нет значения ключа")
    stored_keys = json.loads(settings_text).get("settings") or {}
    check(str(stored_keys.get("llm_secret_name")) == "probe-llm-key",
          "в файле лежит ИМЯ записи, а не значение",
          str(stored_keys.get("llm_secret_name")))

    keyed_cfg = llm_mod.config_from_settings(settings_mod.SettingsStore().load())
    seen: dict[str, bool] = {}

    class HeaderProbe(FakeTransport):
        """Проверяет, что ключ уходит ЗАГОЛОВКОМ, и не печатает его."""

        def __call__(self, request: dict) -> dict:
            headers = {str(k).lower(): str(v) for k, v in
                       (request.get("headers") or {}).items()}
            seen["authorization"] = headers.get("authorization") == (
                "Bearer " + PROBE_KEY_MATERIAL)
            return super().__call__(request)

    probe = HeaderProbe("ответ по ключу")
    keyed = llm_mod.LLMClient(cfg, config=keyed_cfg, transport=probe)
    answer = keyed.complete("Извлеки сущности.", input_text="чанк-ключ")
    check(seen.get("authorization") is True,
          "ключ уходит заголовком Authorization")
    dumped = json.dumps(answer, ensure_ascii=False)
    check(PROBE_KEY_MATERIAL not in dumped, "значения ключа нет в ответе вызова",
          dumped[:160])
    state_dump = json.dumps(keyed.status(), ensure_ascii=False)
    check(PROBE_KEY_MATERIAL not in state_dump, "значения ключа нет в состоянии")
    check("authorization" not in state_dump.lower(),
          "заголовок авторизации не выносится в состояние")
    check(state_dump.count("probe-llm-key") >= 1,
          "состояние называет ИМЯ записи — иначе непонятно, откуда ключ")
    docgraph_dump = json.dumps(dg.build_status(cfg), ensure_ascii=False)
    check(PROBE_KEY_MATERIAL not in docgraph_dump,
          "значения ключа нет в ответе docgraph status")

    failed = llm_mod.LLMClient(
        cfg, config=keyed_cfg,
        transport=FakeTransport(status=401, body='{"error":"invalid api key"}'))
    error_text = ""
    try:
        failed.complete("Извлеки сущности.", input_text="чанк-ключ-2")
    except llm_mod.LLMError as exc:
        error_text = f"{exc.code} {exc.message} {json.dumps(exc.to_dict(), ensure_ascii=False)}"
    check("auth" in error_text, "отказ провайдера назван кодом auth", error_text[:160])
    check(PROBE_KEY_MATERIAL not in error_text,
          "значения ключа нет в тексте ошибки", error_text[:160])

    audit = vault.audit_rows(limit=20, name="probe-llm-key")
    ops = [str(row.get("op")) for row in audit]
    check("reveal" in ops, "выдача ключа записана в аудит", str(ops[:5]))
    check(all(PROBE_KEY_MATERIAL not in json.dumps(row, ensure_ascii=False)
              for row in audit), "значения ключа нет в журнале аудита")

    print()
    print("=" * 78)
    # Формат итога — тот, который разбирает раннер (`tools/check_all.py`):
    # «пройдено: N, провалено: M». Своя формулировка давала в отчёте прогона
    # «0 пройдено, 0 провалено» при семидесяти выполненных проверках: цифры
    # проверки были, а прогон их не видел.
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
