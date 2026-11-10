# -*- coding: utf-8 -*-
"""Пиннинг подписи операций: поведение, а не наличие таблицы.

Что проверяется:

  1. хеш детерминирован: два прогона на неизменном коде дают один хеш, порядок
     ключей ответа и форматирование на хеш не влияют;
  2. изменение умолчания параметра меняет хеш и даёт `pin_mismatch` с обоими
     хешами и возрастом пина;
  3. подтверждение (`approve`) убирает расхождение, повторный вызов молчит;
  4. `pin_break` пишется только ПОСЛЕ расхождения и связан с его событием,
     причём для обеих причин закрытого списка: `unknown_name` и
     `bad_arguments` (у ошибки аргументов — своя точка: разбор командной
     строки);
  5. нетронутые операции не шумят: у них ноль событий;
  6. пин переживает перезапуск: читается из `control.sqlite` новым соединением;
  7. без лицензии Enterprise операции отказывают, называя `contract-pin`, и совет
     идёт через `util.next_action`;
  8. подсадка: если канонизация перестанет сортировать ключи, хеш поменяется —
     свойство держится на сортировке, а не на формулировке;
  9. описание берётся у РАБОТАЮЩЕГО кода: на чужом корпусе (там исходников
     RAGRAF нет и быть не может) хеши считаются, а не молчат пустой строкой;
 10. `pin_break` — только ПЕРВЫЙ неуспешный вызов после расхождения: повтор
     того же вызова не плодит записей, другая операция и другое расхождение —
     дают свою;
 11. ошибка аргументов через MCP тоже попадает в журнал: точка вызова сервера
     проверяется с подставным фасадом (настоящий требует собранного индекса);
 12. состояние пина различает четыре исхода (`none`, `agreed`, `partial`,
     `diverged`) и ставит расхождение выше неполноты;
 13. ворота выпуска останавливают публикацию на `diverged` и `partial`,
     пропускают `agreed` и предупреждают на `none`;
 14. `ops_pins.json` описывает код ПОСТАВКИ: файл сходится с упакованным
     деревом, а подмена хеша, версии или отсутствие файла — отказ.

Песочница: копия фасада и `surface.py` в %TEMP%, чтобы «изменить умолчание»
можно было делом, а не подменой словаря. Журнал — свой `control.sqlite`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import sqlite3
import sys
import tempfile
import types

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import edition  # noqa: E402
from ragraf import pin as pin_mod  # noqa: E402
from ragraf import store  # noqa: E402
from ragraf import util  # noqa: E402

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


def make_sandbox() -> pathlib.Path:
    for base in (os.environ.get("TEMP", ""), os.environ.get("TMP", ""),
                 str(REPO / ".ragraf" / "tmp")):
        if not base:
            continue
        try:
            pathlib.Path(base).mkdir(parents=True, exist_ok=True)
            probe = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-x6-pin-", dir=base))
            (probe / "probe.txt").write_text("ok", encoding="utf-8")
            (probe / "probe.txt").unlink()
            return probe
        except OSError:
            continue
    raise SystemExit("нет каталога для песочницы")


FACADE_STUB = '''class Facade:
    def op_alpha(self, limit: int = 10, mode: str = "auto") -> dict:
        env = None
        env.result = {"items": [], "total": 0}
        return env.to_dict()

    def op_beta(self) -> dict:
        env = None
        env.result = {"ok": True}
        return env.to_dict()


def helper() -> int:
    return 1
'''

#: Операции добавляются в ТЕЛО класса, а не после него: иначе разбор AST их не
#: увидит, и проверка окажется зелёной на несуществующем описании.
GAMMA = '''
    def op_gamma(self) -> dict:
        env = None
        env.result = {}
        return env.to_dict()
'''
DELTA = '''
    def op_delta(self) -> dict:
        env = None
        env.result = {}
        return env.to_dict()
'''
EPSILON = '''
    def op_epsilon(self) -> dict:
        env = None
        env.result = {}
        return env.to_dict()
'''


#: Сервер MCP песочницы: два инструмента, у одного — ВЛОЖЕННАЯ схема.
#: Вложенность нужна проверке на подсадку: регулярка видит имена, но не форму.
MCP_STUB = '''TOOLS = [
    {
        "name": "ragraf_alpha_tool",
        "description": "поиск",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "запрос"},
                "filter": {"type": "object",
                           "properties": {"kind": {"type": "string"}},
                           "additionalProperties": False},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_beta_tool",
        "description": "другой инструмент",
        "inputSchema": {"type": "object", "properties": {"flag": {"type": "boolean"}},
                         "additionalProperties": False},
    },
]

_TOOL_META = {
    "ragraf_alpha_tool": {"title": "Поиск в песочнице", "readOnlyHint": True,
                          "destructiveHint": False, "idempotentHint": True},
    "ragraf_beta_tool": {"title": "Флаг", "readOnlyHint": False,
                         "destructiveHint": False, "idempotentHint": True},
}
'''


def facade_text(*extras: str, limit_default: int = 10) -> str:
    """Фасад-песочница: те же операции плюс перечисленные в теле класса."""
    body = FACADE_STUB.replace("limit: int = 10", f"limit: int = {limit_default}")
    return body.replace("\n\ndef helper() -> int:",
                        "".join(extras) + "\n\ndef helper() -> int:")


def load_module(name: str, path: pathlib.Path):
    """Загрузить инструмент по пути: пакета `tools` для тестов не существует."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_corpus(base: pathlib.Path, facade_code: str) -> pathlib.Path:
    """Корпус-песочница: `facade.py`, `mcp_server.py` и `surface.py` в нужных местах."""
    root = base / "corpus"
    (root / "src" / "ragraf").mkdir(parents=True, exist_ok=True)
    (root / "src" / "ragraf" / "facade.py").write_text(facade_code, encoding="utf-8")
    (root / "src" / "ragraf" / "mcp_server.py").write_text(MCP_STUB, encoding="utf-8")
    shutil.copy(REPO / "src" / "ragraf" / "surface.py",
                root / "src" / "ragraf" / "surface.py")
    return root


def journal(path: pathlib.Path) -> sqlite3.Connection:
    conn = store.connect(path)
    store.init_control(conn)
    pin_mod.ensure_schema(conn)
    return conn


sandbox = make_sandbox()
print(f"песочница: {sandbox}")
corpus = make_corpus(sandbox, FACADE_STUB)
conn = journal(sandbox / "control.sqlite")

try:
    print("=" * 78)
    print("1. ХЕШ ДЕТЕРМИНИРОВАН И НЕ ЗАВИСИТ ОТ ФОРМАТИРОВАНИЯ")
    print("=" * 78)
    first = pin_mod.hashes(corpus)
    second = pin_mod.hashes(corpus)
    check(first == second, "два прогона дают одинаковые хеши", str(first)[:80])
    check(set(first) == {"alpha", "beta"}, "описаны обе операции", str(sorted(first)))
    # Порядок ключей ответа и отступы не меняют хеш: сравнение идёт по
    # канонической форме, а не по тексту файла.
    reordered = FACADE_STUB.replace('{"items": [], "total": 0}', '{"total": 0, "items": []}')
    other = make_corpus(sandbox / "reordered", reordered)
    check(pin_mod.hashes(other) == first,
          "порядок ключей ответа на хеш не влияет",
          str(pin_mod.hashes(other))[:80])
    changed_default = FACADE_STUB.replace('limit: int = 10', 'limit: int = 20')
    changed_corpus = make_corpus(sandbox / "changed", changed_default)
    changed_hash = pin_mod.hashes(changed_corpus)["alpha"]
    check(changed_hash != first["alpha"], "изменённое умолчание меняет хеш",
          f"{first['alpha'][:12]} vs {changed_hash[:12]}")
    check(pin_mod.hashes(changed_corpus)["beta"] == first["beta"],
          "у нетронутой операции хеш прежний")

    print()
    print("=" * 78)
    print("2. РАСХОЖДЕНИЕ: ОБА ХЕША И ВОЗРАСТ")
    print("=" * 78)
    approved = pin_mod.approve(conn, corpus, everything=True, by="проверка",
                               note="пин на релиз")
    check(approved.get("total") == 2, "подтверждены обе операции", str(approved.get("total")))
    quiet = pin_mod.status(conn, corpus)
    check(quiet["totals"]["mismatches"] == 0 and quiet["totals"]["pinned"] == 2,
          "после подтверждения расхождений нет", str(quiet["totals"]))
    # Умолчание меняется в САМОМ корпусе: `status` сравнивает пин с описанием
    # того же корпуса, и расхождение на другом каталоге он не увидит (измерено
    # первым прогоном набора).
    (corpus / "src" / "ragraf" / "facade.py").write_text(changed_default,
                                                         encoding="utf-8")
    observed_new = pin_mod.hash_of(corpus, "alpha")
    check(observed_new == changed_hash, "хеш после правки совпадает с ожидаемым",
          f"{observed_new[:12]} vs {changed_hash[:12]}")
    event = pin_mod.observe(conn, "alpha", observed_new, root=corpus)
    check(event is not None and event["observed"] == observed_new
          and event["pinned"] == first["alpha"],
          "событие несёт оба хеша", json.dumps(event, ensure_ascii=False)[:140])
    check(event is not None and event["pin_age_seconds"] is not None,
          "возраст пина назван числом", str((event or {}).get("pin_age_seconds")))
    again = pin_mod.observe(conn, "alpha", observed_new, root=corpus)
    check(again is None, "повторное наблюдение того же хеша не плодит событий")
    report = pin_mod.status(conn, corpus)
    check(report["totals"]["mismatches"] == 1
          and report["mismatches"][0]["op"] == "alpha",
          "статус называет разошедшуюся операцию", str(report["mismatches"]))

    print()
    print("=" * 78)
    print("3. ПОДТВЕРЖДЕНИЕ УБИРАЕТ РАСХОЖДЕНИЕ")
    print("=" * 78)
    pin_mod.approve(conn, corpus, op="alpha", by="проверка")
    pinned_now = pin_mod.pins(conn)["alpha"]["hash"]
    after = pin_mod.status(conn, corpus)
    check(after["totals"]["mismatches"] == 0, "после подтверждения расхождений нет",
          str(after["mismatches"]))
    check(pinned_now == pin_mod.hash_of(corpus, "alpha"),
          "пин встал на ТЕКУЩЕЕ описание", f"{pinned_now[:12]}")
    silent = pin_mod.observe(conn, "alpha", observed_new, root=corpus)
    check(silent is None, "совпавший вызов молчит")

    print()
    print("=" * 78)
    print("4. PIN_BREAK ТОЛЬКО ПОСЛЕ РАСХОЖДЕНИЯ И СО ССЫЛКОЙ")
    print("=" * 78)
    # Нет расхождения — нет записи: неизвестное имя до расхождения обычная ошибка.
    fresh = journal(sandbox / "fresh.sqlite")
    try:
        pin_mod.approve(fresh, corpus, everything=True, by="проверка")
        early = pin_mod.note_break(fresh, "op_gone", reason="unknown_name")
        check(early is None, "без расхождения pin_break не пишется")
        # Наблюдается ПРЕЖНИЙ хеш, а пин стоит на изменённом: только так
        # возникает расхождение, без которого pin_break писать нечего.
        pin_mod.observe(fresh, "alpha", first["alpha"], root=corpus)
        broken = pin_mod.note_break(fresh, "op_gone", reason="unknown_name",
                                    detail="имени нет в описании")
        check(broken is not None and broken.get("related_id"),
              "после расхождения pin_break связан событием",
              json.dumps(broken, ensure_ascii=False))
        listed = pin_mod.events(fresh)
        kinds = [item["kind"] for item in listed]
        check(kinds[0] == "pin_break" and "pin_mismatch" in kinds,
              "журнал содержит оба вида событий", str(kinds))
        mismatch_id = next(item["id"] for item in listed if item["kind"] == "pin_mismatch")
        check(broken["related_id"] == mismatch_id,
              "ссылка ведёт на событие расхождения",
              f"{broken['related_id']} vs {mismatch_id}")
        try:
            pin_mod.note_break(fresh, "op_gone", reason="неизвестная причина")
            refused = False
        except pin_mod.PinRefusal:
            refused = True
        check(refused, "причина вне закрытого списка — отказ")
    finally:
        fresh.close()

    # Корпус возвращается к исходному описанию: подтверждение уже прошло, и
    # дальнейшие проверки должны видеть ровно то состояние, что описано.
    (corpus / "src" / "ragraf" / "facade.py").write_text(FACADE_STUB,
                                                         encoding="utf-8")
    pin_mod.approve(conn, corpus, everything=True, by="проверка")

    print()
    print("=" * 78)
    print("4б. BAD_ARGUMENTS: ОШИБКА АРГУМЕНТОВ ПОСЛЕ РАСХОЖДЕНИЯ")
    print("=" * 78)
    from ragraf import cli as cli_mod

    check(cli_mod._attempted_op(["query", "search", "--text-width", "abc"])
          == "search", "имя операции достаётся из argv при ошибке разбора")
    check(cli_mod._attempted_op(["query"]) == "",
          "команда без операции имени не выдумывает")
    check(cli_mod._attempted_op(["--help"]) == "",
          "служебное слово операцией не считается")
    check(cli_mod._config_from_argv(["-p", "config/x.yaml", "query", "search"])
          == "config/x.yaml", "путь конфига берётся из -p")
    check(cli_mod._config_from_argv(["--project=C:/p/ragraf.yaml"])
          == "C:/p/ragraf.yaml", "путь конфига берётся из --project=")
    check(cli_mod._config_from_argv(["query", "search"]) is None,
          "без пути конфига ответ пустой, а не выдуманный")

    guard = journal(sandbox / "badargs.sqlite")
    try:
        pin_mod.approve(guard, corpus, everything=True, by="проверка")
        early_args = pin_mod.note_break(guard, "alpha", reason="bad_arguments")
        check(early_args is None,
              "без расхождения bad_arguments не пишется")
        # Наблюдается заведомо другой хеш: расхождение нужно как условие, а не как предмет.
        pin_mod.observe(guard, "alpha", "f" * 64, root=corpus)
        broken_args = pin_mod.note_break(guard, "alpha", reason="bad_arguments",
                                         detail="аргументы вызова не разобраны")
        check(broken_args is not None and broken_args.get("related_id"),
              "после расхождения bad_arguments связан событием",
              json.dumps(broken_args, ensure_ascii=False))
        found = [item for item in pin_mod.events(guard)
                 if item["kind"] == "pin_break"]
        check(found and "bad_arguments" in str(found[0].get("detail") or ""),
              "причина видна в записи журнала", str(found[:1]))
    finally:
        guard.close()

    seen: dict[str, list[str]] = {}
    original_note = cli_mod._note_bad_arguments
    cli_mod._note_bad_arguments = lambda argv: seen.setdefault("argv", list(argv))
    try:
        try:
            cli_mod.main(["query", "search", "--text-width", "abc"])
            bad_code = 0
        except SystemExit as exc:
            bad_code = int(getattr(exc, "code", 0) or 0)
        check(bad_code == 2 and seen.get("argv"),
              "ошибка разбора даёт код 2 и попадает в журнал",
              f"код {bad_code}, argv {seen.get('argv')}")
        seen.clear()
        try:
            cli_mod.main(["query", "--help"])
            help_code = 0
        except SystemExit as exc:
            help_code = int(getattr(exc, "code", 0) or 0)
        check(help_code == 0 and not seen.get("argv"),
              "справка не считается ошибкой аргументов",
              f"код {help_code}, argv {seen.get('argv')}")
    finally:
        cli_mod._note_bad_arguments = original_note

    early_note = cli_mod._note_bad_arguments
    try:
        cli_mod._note_bad_arguments(["-p", str(sandbox / "нет" / "конфига.yaml"),
                                     "query", "search", "--bogus"])
        survived = True
    except Exception:  # noqa: BLE001 — проверка на то, что исключения нет
        survived = False
    finally:
        cli_mod._note_bad_arguments = early_note
    check(survived, "без конфига журнал молчит, а вызов не падает")

    print()
    print("=" * 78)
    print("5. НЕТРОНУТЫЕ ОПЕРАЦИИ НЕ ШУМЯТ")
    print("=" * 78)
    quiet_status = pin_mod.status(conn, corpus)
    beta_events = [item for item in quiet_status["events"] if item["op"] == "beta"]
    check(not beta_events, "у beta событий нет", str(beta_events))
    check(quiet_status["totals"]["pinned"] == 2, "оба пина на месте",
          str(quiet_status["totals"]))

    print()
    print("=" * 78)
    print("6. ПИН ПЕРЕЖИВАЕТ ПЕРЕЗАПУСК")
    print("=" * 78)
    conn.close()
    conn = journal(sandbox / "control.sqlite")
    survived = pin_mod.status(conn, corpus)
    check(survived["totals"]["pinned"] == 2, "пины прочитаны новым соединением",
          str(survived["totals"]))
    check(survived["pins"][0]["approved_by"] == "проверка",
          "автор пина сохранён", str(survived["pins"][0]))

    print()
    print("=" * 78)
    print("7. ИЗДАНИЕ: ОТКАЗ НАЗЫВАЕТ ВОЗМОЖНОСТЬ И ДАЁТ СОВЕТ")
    print("=" * 78)
    for name, value in (("baseline", edition.BASELINE_ENV),
                        ("declared", edition.DECLARED_ENV)):
        os.environ.pop(value, None)
    os.environ[edition.LICENSE_ENV] = str(sandbox / "absent.license")
    community = edition.resolve()
    decision = community.decide("contract_pin_status")
    check(decision.get("allowed") is False
          and "contract-pin" in str(decision.get("reason") or ""),
          "Community: отказ называет возможность contract-pin",
          str(decision.get("reason")))
    advice = util.next_action("contract_pin_status", "", kind="edition",
                              detail=str(decision.get("reason") or ""))
    check(bool(advice.get("action")) and advice.get("possible") is True,
          "совет отказа: одна команда и признак возможности",
          json.dumps(advice, ensure_ascii=False)[:120])

    print()
    print("=" * 78)
    print("8. ПОДСАДКА: СВОЙСТВО ДЕРЖИТСЯ НА КАНОНИЗАЦИИ")
    print("=" * 78)
    # 8.1 Канонизация действительно сортирует ключи: два порядка одного словаря
    # дают ОДНУ запись. Это и есть свойство, на котором стоит хеш.
    left = pin_mod.canonical({"beta": 1, "alpha": 2})
    right = pin_mod.canonical({"alpha": 2, "beta": 1})
    check(left == right, "canonical гасит порядок ключей", f"{left} vs {right}")
    real = pin_mod.canonical
    pin_mod.canonical = lambda payload: json.dumps(payload, ensure_ascii=False)
    try:
        broken_left = pin_mod.canonical({"beta": 1, "alpha": 2})
        broken_right = pin_mod.canonical({"alpha": 2, "beta": 1})
    finally:
        pin_mod.canonical = real
    check(broken_left != broken_right,
          "подсадка: без сортировки порядок снова влияет (свойство 8.1 ловит)",
          f"{broken_left} vs {broken_right}")

    # 8.2 Умолчания входят в описание: если их выкинуть, хеш обязан измениться.
    real_desc = pin_mod.description

    def without_defaults(op):
        item = real_desc(op)
        for param in item["params"]:
            param.pop("default", None)
        return item

    pin_mod.description = without_defaults
    try:
        pin_mod._CACHE.clear()          # кэш описаний: см. комментарий выше
        loose = pin_mod.hash_of(corpus, "alpha")
    finally:
        pin_mod.description = real_desc
        pin_mod._CACHE.clear()
    check(loose != first["alpha"],
          "подсадка: без умолчаний хеш другой (иначе умолчание не входит в пин)",
          f"{loose[:12]} vs {first['alpha'][:12]}")
    check(pin_mod.hashes(corpus) == first, "на настоящем описании хеш прежний")

    print()
    print("=" * 78)
    print("9. ОПИСАНИЕ БЕРЁТСЯ У РАБОТАЮЩЕГО КОДА, А НЕ У КОРПУСА")
    print("=" * 78)
    # Корпус пользователя — чужой проект: `src/ragraf/facade.py` там нет и не
    # будет. До правки 10.11.2026 хеши на таком корпусе не считались вовсе:
    # наблюдение возвращало пустую строку, и расхождение не возникало никогда.
    foreign = sandbox / "foreign"
    (foreign / "src").mkdir(parents=True, exist_ok=True)
    (foreign / "readme.md").write_text("чужой проект: исходников RAGRAF здесь нет",
                                      encoding="utf-8")
    real_hashes = pin_mod.hashes(REPO)
    from_foreign = pin_mod.hashes(foreign)
    check(from_foreign == real_hashes,
          "на чужом корпусе описание то же, что у работающего кода",
          f"{len(from_foreign)} против {len(real_hashes)}")
    check(len(from_foreign) > 40, "операции описаны, а не пустой словарь",
          str(len(from_foreign)))
    check(pin_mod.hash_of(foreign, "search") == pin_mod.hash_of(REPO, "search"),
          "хеш search на чужом корпусе не пуст и совпадает",
          pin_mod.hash_of(foreign, "search")[:12])
    # Подсадка: вернуть поиск только под корпусом — свойство 9 обязано упасть.
    real_path = pin_mod._facade_path
    pin_mod._facade_path = lambda root: pathlib.Path(root) / "src" / "ragraf" / "facade.py"
    pin_mod._CACHE.clear()
    try:
        silenced = False
        try:
            pin_mod.hashes(foreign)
        except pin_mod.PinRefusal:
            silenced = True
    finally:
        pin_mod._facade_path = real_path
        pin_mod._CACHE.clear()
    check(silenced, "подсадка: без поиска у работающего кода наблюдение молчит")
    outsider = journal(sandbox / "foreign.sqlite")
    try:
        pin_mod.approve(outsider, foreign, everything=True, by="проверка")
        outsider_event = pin_mod.observe(outsider, "search", "0" * 64, root=foreign)
        check(outsider_event is not None and outsider_event["op"] == "search",
              "на чужом корпусе расхождение записывается",
              json.dumps(outsider_event, ensure_ascii=False)[:120])
        outsider_status = pin_mod.status(outsider, foreign)
        check(outsider_status["totals"]["pinned"] == len(real_hashes),
              "пины на чужом корпусе читаются", str(outsider_status["totals"]))
    finally:
        outsider.close()

    print()
    print("=" * 78)
    print("10. PIN_BREAK — ТОЛЬКО ПЕРВЫЙ ВЫЗОВ ПОСЛЕ РАСХОЖДЕНИЯ")
    print("=" * 78)
    first_only = journal(sandbox / "first.sqlite")
    try:
        pin_mod.approve(first_only, corpus, everything=True, by="проверка")
        beta_mismatch = pin_mod.observe(first_only, "beta", "b" * 64, root=corpus)
        check(beta_mismatch is not None, "расхождение по beta записано")
        once = pin_mod.note_break(first_only, "beta", reason="bad_arguments",
                                  detail="аргументы вызова не разобраны")
        twice = pin_mod.note_break(first_only, "beta", reason="bad_arguments",
                                   detail="аргументы вызова не разобраны")
        check(once is not None and twice is None,
              "второй такой же вызов записи не добавляет",
              f"{bool(once)} / {bool(twice)}")
        check(once is not None and once.get("same_op") is True,
              "связь ведёт на расхождение той же операции", str(once))
        other_op = pin_mod.note_break(first_only, "alpha", reason="bad_arguments")
        check(other_op is not None and other_op.get("same_op") is False
              and other_op.get("related_op") == "beta",
              "другая операция даёт своё событие и называет чужое расхождение",
              str(other_op))
        other_reason = pin_mod.note_break(first_only, "beta", reason="unknown_name",
                                          detail="имени нет в описании")
        check(other_reason is not None,
              "другая причина — отдельное событие", str(other_reason))
        pin_mod.observe(first_only, "alpha", "a" * 64, root=corpus)
        after_other = pin_mod.note_break(first_only, "beta", reason="bad_arguments")
        check(after_other is None,
              "новое расхождение чужой операции не плодит записей по beta")
        pin_mod.observe(first_only, "beta", "c" * 64, root=corpus)
        after_same = pin_mod.note_break(first_only, "beta", reason="bad_arguments")
        check(after_same is not None,
              "новое расхождение той же операции снова даёт одну запись",
              str(after_same))
        breaks = [item for item in pin_mod.events(first_only) if item["kind"] == "pin_break"]
        check(len(breaks) == 4, "всего записей предела: 4 (а не по числу вызовов)",
              str(len(breaks)))
    finally:
        first_only.close()

    print()
    print("=" * 78)
    print("11. ТОЧКА ВЫЗОВА MCP: НЕИЗВЕСТНЫЙ ИНСТРУМЕНТ И АРГУМЕНТЫ")
    print("=" * 78)
    from ragraf import mcp_server as mcp_mod

    check(mcp_mod.pin_break_reason(TypeError("got an unexpected keyword argument"))
          == "bad_arguments", "TypeError — ошибка аргументов")
    check(mcp_mod.pin_break_reason(KeyError("query")) == "bad_arguments",
          "KeyError — ошибка аргументов")
    check(mcp_mod.pin_break_reason(RuntimeError("нет индекса")) == "",
          "прочие сбои поводом не считаются")
    mcp_journal = sandbox / "mcp.sqlite"
    mcp_conn = journal(mcp_journal)
    try:
        # Подтверждаем ОБА контракта: точка MCP пишет события вида `tool`, и
        # связывать их с расхождением операции было бы чужой причиной.
        pin_mod.approve(mcp_conn, corpus, everything=True, by="проверка", kind="all")
        pin_mod.observe(mcp_conn, "ragraf_alpha_tool", "d" * 64, root=corpus, kind="tool")
    finally:
        mcp_conn.close()
    server = mcp_mod.McpServer(None, watch=False)
    server.config = lambda: types.SimpleNamespace(control_db=mcp_journal)
    answer = server._call_tool(None, {"name": "ragraf_gone", "arguments": {}})
    check("isError" in json.dumps(answer, ensure_ascii=False),
          "неизвестный инструмент отвечает отказом", json.dumps(answer)[:100])

    def boom(_args):
        raise TypeError("op_alpha() got an unexpected keyword argument 'text'")

    server._handlers = lambda: {"ragraf_fake": boom}
    server._call_tool(None, {"name": "ragraf_fake", "arguments": {"text": "x"}})
    check_conn = store.connect(mcp_journal)
    try:
        found = [item for item in pin_mod.events(check_conn) if item["kind"] == "pin_break"]
    finally:
        check_conn.close()
    ops = sorted(item["op"] for item in found)
    check(ops == ["ragraf_fake", "ragraf_gone"],
          "оба вызова попали в журнал контракта и связаны с расхождением",
          str(ops))
    check(all("bad_arguments" in item["detail"] for item in found
              if item["op"] == "ragraf_fake"),
          "причина ошибки аргументов видна в записи", str(found[:1]))

    print()
    print("=" * 78)
    print("12. СОСТОЯНИЕ ПИНА: NONE / AGREED / PARTIAL / DIVERGED")
    print("=" * 78)
    three = sandbox / "three"
    three = make_corpus(three, facade_text(GAMMA))
    states = journal(sandbox / "states.sqlite")
    try:
        empty = pin_mod.state(states, three)
        check(empty["state"] == "none" and empty["total_ops"] == 3
              and empty["pin_age_seconds"] is None,
              "без пинов — none, и возраст не выдуман", str(empty))
        pin_mod.approve(states, three, everything=True, by="проверка")
        agreed = pin_mod.state(states, three)
        check(agreed["state"] == "agreed" and agreed["pinned"] == 3
              and agreed["unpinned"] == 0,
              "все подтверждено — agreed", str(agreed))
        check(isinstance(agreed["pin_age_seconds"], int) and agreed["pin_age_seconds"] >= 0,
              "возраст пина назван числом", str(agreed.get("pin_age_seconds")))
        shown = pin_mod.status(states, three)
        check(shown["pin_state"]["state"] == "agreed"
              and shown["totals"]["pinned"] == shown["pin_state"]["pinned"],
              "операция status называет то же состояние, что и ворота",
              str(shown["pin_state"]["state"]))
        # Неполнота: в описании появилась операция, которую никто не подтверждал.
        (three / "src" / "ragraf" / "facade.py").write_text(
            facade_text(GAMMA, DELTA), encoding="utf-8")
        partial = pin_mod.state(states, three)
        check(partial["state"] == "partial" and partial["unpinned"] == 1,
              "новая операция без пина — partial", str(partial))
        # Расхождение важнее неполноты: правится УМОЛЧАНИЕ уже подтверждённого.
        pin_mod.approve(states, three, everything=True, by="проверка")
        (three / "src" / "ragraf" / "facade.py").write_text(
            facade_text(GAMMA, DELTA, limit_default=99), encoding="utf-8")
        diverged = pin_mod.state(states, three)
        check(diverged["state"] == "diverged" and diverged["diverged"] == ["alpha"],
              "изменённое умолчание — diverged с именем операции", str(diverged))
        # Оба признака сразу: расхождение обязано перекрыть неполноту.
        (three / "src" / "ragraf" / "facade.py").write_text(
            facade_text(GAMMA, DELTA, EPSILON, limit_default=99),
            encoding="utf-8")
        mixed = pin_mod.state(states, three)
        check(mixed["state"] == "diverged" and mixed["unpinned"] == 1,
              "расхождение перекрывает неполноту", str(mixed))
        # Устаревший пин (операция исчезла) при совпадающих остальных — partial.
        pin_mod.approve(states, three, everything=True, by="проверка")
        (three / "src" / "ragraf" / "facade.py").write_text(facade_text(limit_default=99),
                                                             encoding="utf-8")
        stale = pin_mod.state(states, three)
        check(stale["state"] == "partial" and stale["stale"] == 3,
              "пины на исчезнувшие операции — partial", str(stale))
    finally:
        states.close()
        (three / "src" / "ragraf" / "facade.py").write_text(facade_text(GAMMA),
                                                              encoding="utf-8")

    print()
    print("=" * 78)
    print("13. ВОРОТА ВЫПУСКА: ЧТО ОСТАНАВЛИВАЕТ ПУБЛИКАЦИЮ")
    print("=" * 78)
    gate = load_module("ragraf-gate", REPO / "tools" / "release" / "gate_release.py")
    builder = load_module("ragraf-build", REPO / "tools" / "release" / "build_release.py")
    gate_corpus = make_corpus(sandbox / "gate", FACADE_STUB)
    journal_path = gate_corpus / ".ragraf" / "control.sqlite"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    gate_conn = journal(journal_path)
    try:
        empty_code, empty_message = gate.check_contract_pin(gate_corpus)
        check(empty_code == 0 and "ПРЕДУПРЕЖДЕНИЕ" in empty_message,
              "без пина выпуск не блокируется, но предупреждает", empty_message[:120])
        pin_mod.approve(gate_conn, gate_corpus, everything=True, by="проверка",
                        kind="all")
        agree_code, agree_message = gate.check_contract_pin(gate_corpus)
        check(agree_code == 0 and "контракт принят" in agree_message,
              "принятый контракт выпуск пропускает", agree_message[:120])
        check("принято" in agree_message,
              "в строке выпуска есть возраст пина", agree_message[:120])
        check("3 дня" in gate._age_text(3 * 86400)
              and "5 дней" in gate._age_text(5 * 86400)
              and "возраст не назван" == gate._age_text(None),
              "возраст склоняется по-русски и не выдумывается без числа",
              gate._age_text(3 * 86400))
        (gate_corpus / "src" / "ragraf" / "facade.py").write_text(
            facade_text(limit_default=11), encoding="utf-8")
        bad_code, bad_message = gate.check_contract_pin(gate_corpus)
        check(bad_code == 1 and "разошлось" in bad_message and "alpha" in bad_message,
              "расхождение останавливает выпуск и называет операцию", bad_message[:140])
        check("contract_pin_approve" in bad_message and "--approved-by" in bad_message,
              "отказ даёт одну готовую команду", bad_message[:140])
        (gate_corpus / "src" / "ragraf" / "facade.py").write_text(
            facade_text(GAMMA), encoding="utf-8")
        part_code, part_message = gate.check_contract_pin(gate_corpus)
        check(part_code == 1 and "не целиком" in part_message
              and "операции: partial" in part_message,
              "неполный набор тоже останавливает выпуск", part_message[:160])
        # Подсадка: если ворота перестанут смотреть на состояние, проверка провалится.
        real_check = gate.check_contract_pin
        real_state = pin_mod.state_payload
        pin_mod.state_payload = lambda current, known: {**real_state(current, known),
                                                        "state": "agreed"}
        try:
            muted = gate.check_contract_pin(gate_corpus)
        finally:
            pin_mod.state_payload = real_state
        check(muted[0] == 0,
              "подсадка: без состояния ворота пропускают неполный набор (свойство 13 ловит)",
              str(muted[0]))
        check(real_check(gate_corpus)[0] == 1, "на настоящем состоянии ворота снова красные")
    finally:
        gate_conn.close()

    print()
    print("=" * 78)
    print("14. OPS_PINS.JSON ОПИСЫВАЕТ КОД ПОСТАВКИ, А НЕ ЧУЖОЙ КОРПУС")
    print("=" * 78)
    delivery = sandbox / "delivery"
    source_tree = sandbox / "artifact-source"
    for root in (delivery, source_tree):
        (root / "src" / "ragraf").mkdir(parents=True, exist_ok=True)
        (root / "src" / "ragraf" / "facade.py").write_text(facade_text(GAMMA),
                                                          encoding="utf-8")
        (root / "src" / "ragraf" / "__init__.py").write_text(
            '__version__ = "9.9.9"\n', encoding="utf-8")
    src_journal = source_tree / ".ragraf" / "control.sqlite"
    src_journal.parent.mkdir(parents=True, exist_ok=True)
    src_conn = journal(src_journal)
    try:
        pin_mod.approve(src_conn, source_tree, everything=True, by="проверка", kind="all")
    finally:
        src_conn.close()
    pins_path = builder.write_pins(delivery, source_tree)
    document = json.loads(pins_path.read_text(encoding="utf-8"))
    check(document.get("ops_total") == 3 and set(document.get("ops") or {}) ==
          {"alpha", "beta", "gamma"},
          "в файле все операции упакованного кода", str(document.get("ops_total")))
    check(document.get("version") == "9.9.9", "версия взята из кода поставки",
          str(document.get("version")))
    check(document.get("pin_state") == "agreed" and len(document.get("pins") or {}) == 3,
          "подтверждённый набор уехал вместе с описаниями", str(document.get("pin_state")))
    first_bytes = pins_path.read_bytes()
    builder.write_pins(delivery, source_tree)
    check(pins_path.read_bytes() == first_bytes, "два прогона дают одинаковые байты")
    ok_code, ok_message = gate.check_pins_artifact(delivery)
    check(ok_code == 0 and "совпадают" in ok_message, "файл сходится с кодом поставки",
          ok_message[:120])
    (delivery / "src" / "ragraf" / "facade.py").write_text(
        facade_text(GAMMA, limit_default=12), encoding="utf-8")
    drift_code, drift_message = gate.check_pins_artifact(delivery)
    check(drift_code == 1 and "хеш расходится" in drift_message,
          "подмена кода в поставке ловится", drift_message[:120])
    (delivery / "src" / "ragraf" / "facade.py").write_text(facade_text(GAMMA),
                                                          encoding="utf-8")
    (delivery / "src" / "ragraf" / "__init__.py").write_text(
        '__version__ = "9.9.8"\n', encoding="utf-8")
    version_code, version_message = gate.check_pins_artifact(delivery)
    check(version_code == 1 and "версия в файле" in version_message,
          "расхождение версии ловится", version_message[:120])
    pins_path.unlink()
    missing_code, missing_message = gate.check_pins_artifact(delivery)
    check(missing_code == 1 and "нет ops_pins.json" in missing_message,
          "сборка без описаний не выпускается", missing_message[:120])

    print()
    print("=" * 78)
    print("15. КОНТРАКТ ИНСТРУМЕНТА: СХЕМА МЕНЯЕТ ХЕШ, ТЕКСТ — НЕТ")
    print("=" * 78)
    tool_corpus = make_corpus(sandbox / "toolcheck", facade_text())
    server_file = tool_corpus / "src" / "ragraf" / "mcp_server.py"
    base_tools = pin_mod.tool_hashes(tool_corpus)
    check(set(base_tools) == {"ragraf_alpha_tool", "ragraf_beta_tool"},
          "оба инструмента описаны", str(sorted(base_tools)))
    nested = MCP_STUB.replace('"kind": {"type": "string"}',
                              '"kind": {"type": "integer"}')
    check(nested != MCP_STUB, "правка вложенной схемы в песочнице применилась")
    server_file.write_text(nested, encoding="utf-8")
    after_tools = pin_mod.tool_hashes(tool_corpus)
    check(after_tools["ragraf_alpha_tool"] != base_tools["ragraf_alpha_tool"],
          "вложенная схема входит в хеш", f"{base_tools['ragraf_alpha_tool'][:12]}")
    check(after_tools["ragraf_beta_tool"] == base_tools["ragraf_beta_tool"],
          "нетронутый инструмент не меняет хеш")
    wording = MCP_STUB.replace('"description": "поиск"',
                              '"description": "поиск по документам и таблицам"')
    server_file.write_text(wording, encoding="utf-8")
    check(pin_mod.tool_hashes(tool_corpus) == base_tools,
          "правка ТЕКСТА описания хеш не меняет (названная граница)")
    tool_journal = journal(sandbox / "toolstate.sqlite")
    try:
        server_file.write_text(MCP_STUB, encoding="utf-8")
        pin_mod.approve(tool_journal, tool_corpus, everything=True, by="проверка",
                        kind="tool")
        check(pin_mod.state(tool_journal, tool_corpus, kind="tool")["state"] == "agreed",
              "пин на оба инструмента — agreed")
        server_file.write_text(nested, encoding="utf-8")
        observed_tool = pin_mod.tool_hash_of(tool_corpus, "ragraf_alpha_tool")
        tool_event = pin_mod.observe(tool_journal, "ragraf_alpha_tool", observed_tool,
                                     root=tool_corpus, kind="tool")
        check(tool_event is not None and tool_event["kind"] == "tool"
              and tool_event["pinned"] == base_tools["ragraf_alpha_tool"],
              "событие по инструменту несёт оба хеша",
              json.dumps(tool_event, ensure_ascii=False)[:120])
        check(pin_mod.state(tool_journal, tool_corpus, kind="tool")["state"] == "diverged",
              "схема разошлась с пином — diverged")
        check(pin_mod.status(tool_journal, tool_corpus)["pin_state_tools"]["state"]
              == "diverged", "статус называет состояние инструментов")
    finally:
        tool_journal.close()

    print()
    print("=" * 78)
    print("16. СВОДКА ИЗМЕНЕНИЙ: ЛОМАЮЩЕЕ ОТДЕЛЬНО ОТ СОВМЕСТИМОГО")
    print("=" * 78)
    diff_mod = load_module("ragraf-diff", REPO / "src" / "ragraf" / "contract_diff.py")
    before_tree = sandbox / "rel-a"
    after_tree = sandbox / "rel-b"
    for root in (before_tree, after_tree):
        (root / "src" / "ragraf").mkdir(parents=True, exist_ok=True)
        (root / "src" / "ragraf" / "facade.py").write_text(facade_text(), encoding="utf-8")
        (root / "src" / "ragraf" / "__init__.py").write_text('__version__ = "1.0.0"\n',
                                                             encoding="utf-8")
    (before_tree / "src" / "ragraf" / "mcp_server.py").write_text(MCP_STUB, encoding="utf-8")
    # Выпуск B: смена типа во вложенной схеме, новое необязательное свойство,
    # удалённый инструмент и изменённое умолчание операции.
    after_mcp = (MCP_STUB.replace('"kind": {"type": "string"}',
                                  '"kind": {"type": "integer"}')
                 .replace('"limit": {"type": "integer", "default": 10},',
                          '"limit": {"type": "integer", "default": 10},\n                "verbose": {"type": "boolean"},')
                 .replace('''    {
        "name": "ragraf_beta_tool",
        "description": "другой инструмент",
        "inputSchema": {"type": "object", "properties": {"flag": {"type": "boolean"}},
                         "additionalProperties": False},
    },
''', ""))
    (after_tree / "src" / "ragraf" / "mcp_server.py").write_text(after_mcp, encoding="utf-8")
    (after_tree / "src" / "ragraf" / "facade.py").write_text(
        facade_text(limit_default=20), encoding="utf-8")
    for root in (before_tree, after_tree):
        (root / ".ragraf").mkdir(parents=True, exist_ok=True)
        item_conn = journal(root / ".ragraf" / "control.sqlite")
        try:
            pin_mod.approve(item_conn, root, everything=True, by="проверка", kind="all")
        finally:
            item_conn.close()
    builder.write_pins(before_tree, before_tree)
    builder.write_pins(after_tree, after_tree)
    report = diff_mod.diff(before_tree / "ops_pins.json", after_tree / "ops_pins.json")
    op_changed = {item["name"]: item for item in report["ops"]["changed"]}
    tool_changed = {item["name"]: item for item in report["tools"]["changed"]}
    check(op_changed.get("alpha", {}).get("breaking") is False
          and any("умолчание" in reason["what"]
                  for reason in op_changed.get("alpha", {}).get("reasons", [])),
          "изменённое умолчание — changed, но не ломающее",
          json.dumps(op_changed.get("alpha"), ensure_ascii=False)[:140])
    check(report["tools"]["removed"] == ["ragraf_beta_tool"],
          "исчезнувший инструмент назван", str(report["tools"]["removed"]))
    check(tool_changed.get("ragraf_alpha_tool", {}).get("breaking") is True,
          "смена типа во вложенной схеме — ломающая",
          json.dumps(tool_changed.get("ragraf_alpha_tool"), ensure_ascii=False)[:140])
    check(report["totals"]["breaking"] >= 2 and "ломающие" in report["verdict"],
          "вердикт называет ломающие изменения", json.dumps(report["totals"], ensure_ascii=False))
    same = diff_mod.diff(before_tree / "ops_pins.json", before_tree / "ops_pins.json")
    check(same["totals"]["breaking"] == 0 and same["verdict"] == "совместимо",
          "сравнение набора с собой ломающих не находит", same["verdict"])
    # Сужение диапазона и правка без отдельного правила: отчёт обязан назвать
    # и то, и другое — иначе он промолчит там, где пин сработал.
    def crafted(tools: dict, ops: dict) -> dict:
        return {"schema": 2, "version": "1.0.0", "ops_total": len(ops),
                "tools_total": len(tools), "ops": {name: "h" for name in ops},
                "tools": {name: "t" for name in tools},
                "details": ops, "tool_details": tools}

    tool_old = {"ragraf_x": {"name": "ragraf_x",
                             "schema": {"type": "object", "properties": {
                                 "limit": {"type": "integer", "minimum": 1}}},
                             "annotations": {}}}
    tool_narrow = {"ragraf_x": {"name": "ragraf_x",
                                "schema": {"type": "object", "properties": {
                                    "limit": {"type": "integer", "minimum": 5}}},
                                "annotations": {}}}
    tool_pattern = {"ragraf_x": {"name": "ragraf_x",
                                 "schema": {"type": "object", "properties": {
                                     "limit": {"type": "integer", "minimum": 1,
                                               "pattern": "^[0-9]+$"}}},
                                 "annotations": {}}}
    op_old = {"alpha": {"name": "alpha", "params": [], "returns": [],
                        "returns_from": "literal"}}
    path_a = sandbox / "crafted-a.json"
    path_b = sandbox / "crafted-b.json"
    path_c = sandbox / "crafted-c.json"
    path_a.write_text(json.dumps(crafted(tool_old, op_old), ensure_ascii=False),
                      encoding="utf-8")
    path_b.write_text(json.dumps(crafted(tool_narrow, op_old), ensure_ascii=False),
                      encoding="utf-8")
    path_c.write_text(json.dumps(crafted(tool_pattern, op_old), ensure_ascii=False),
                      encoding="utf-8")
    narrow = diff_mod.diff(path_a, path_b)
    narrow_reasons = narrow["tools"]["changed"][0]["reasons"] if narrow["tools"]["changed"] else []
    check(any("minimum limit повышен" in reason["what"] and reason["kind"] == "breaking"
              for reason in narrow_reasons),
          "сужение диапазона считается ломающим", json.dumps(narrow_reasons, ensure_ascii=False)[:140])
    loose = diff_mod.diff(path_a, path_c)
    loose_reasons = loose["tools"]["changed"][0]["reasons"] if loose["tools"]["changed"] else []
    check(any(reason["what"] == "форма limit изменилась" for reason in loose_reasons),
          "правка без своего правила попадает в changed, а не пропадает",
          json.dumps(loose_reasons, ensure_ascii=False)[:140])
    try:
        diff_mod.diff(sandbox / "нет.json", before_tree / "ops_pins.json")
        refused = False
    except diff_mod.ContractDiffError:
        refused = True
    check(refused, "отсутствующий файл — отказ с причиной, а не пустая сводка")

    print()
    print("=" * 78)
    print("17. ПОДСАДКА: БЕЗ РАЗБОРА СХЕМ ИЗМЕНЕНИЕ НЕ ВИДНО")
    print("=" * 78)
    from ragraf import surface as surface_mod

    real_from_file = surface_mod.tools_from_file

    def names_only(path):
        body = pathlib.Path(path).read_text(encoding="utf-8")
        return [{"name": name, "schema": {}, "annotations": {}}
                for name in sorted(set(re.findall(r'"name": "(ragraf_[a-z_]+)"', body)))]

    surface_mod.tools_from_file = names_only
    pin_mod._TOOL_CACHE.clear()
    try:
        regex_before = pin_mod.tool_hashes(before_tree)
        regex_after = pin_mod.tool_hashes(after_tree)
    finally:
        surface_mod.tools_from_file = real_from_file
        pin_mod._TOOL_CACHE.clear()
    check(regex_before.get("ragraf_alpha_tool") == regex_after.get("ragraf_alpha_tool"),
          "подсадка: имена без схем не видят правки схемы (свойство 15 ловит)")
    check(pin_mod.tool_hashes(before_tree)["ragraf_alpha_tool"]
          != pin_mod.tool_hashes(after_tree)["ragraf_alpha_tool"],
          "на настоящем разборе правка схемы видна")

    print()
    print("=" * 78)
    print("18. ИСТОРИЯ ПИНОВ И РЕШЕНИЕ ПО ВЕРСИИ")
    print("=" * 78)
    hist_corpus = make_corpus(sandbox / "history", facade_text())
    hist_conn = journal(sandbox / "history.sqlite")
    try:
        first = pin_mod.approve(hist_conn, hist_corpus, everything=True,
                                by="Михаил", kind="op")
        check(len(pin_mod.history(hist_conn)) == first["total"],
              "первое подтверждение пишет историю", str(first["total"]))
        quiet = len(pin_mod.history(hist_conn))
        pin_mod.approve(hist_conn, hist_corpus, everything=True, by="Михаил", kind="op")
        check(len(pin_mod.history(hist_conn)) == quiet,
              "повтор без изменений историю не растит", str(len(pin_mod.history(hist_conn))))
        (hist_corpus / "src" / "ragraf" / "facade.py").write_text(
            facade_text(limit_default=30), encoding="utf-8")
        pin_mod.approve(hist_conn, hist_corpus, everything=True, by="Михаил", kind="op")
        rows = [item for item in pin_mod.history(hist_conn) if item["op"] == "alpha"]
        check(len(rows) == 2, "смена хеша даёт вторую строку", str(len(rows)))
        check(rows[0]["previous_hash"] == rows[1]["hash"],
              "цепочка previous_hash сходится",
              f"{rows[0]['previous_hash'][:12]} против {rows[1]['hash'][:12]}")
        change = pin_mod.last_change(hist_conn)
        check(change is not None and change["approved_by"] == "Михаил"
              and isinstance(change["age_seconds"], int),
              "последняя смена названа с автором и возрастом", str(change)[:120])
        report18 = pin_mod.status(hist_conn, hist_corpus)
        check(report18["history"] and report18["changed_at"]
              and isinstance(report18["changed_age_seconds"], int),
              "статус отдаёт последнюю смену и её возраст",
              str(report18.get("changed_at")))
    finally:
        hist_conn.close()

    check(gate.version_parts("1.2.3") == (1, 2, 3) and gate.version_parts("") is None,
          "версия разбирается числами, а не строками")
    stub_without_beta = MCP_STUB.replace('''    {
        "name": "ragraf_beta_tool",
        "description": "другой инструмент",
        "inputSchema": {"type": "object", "properties": {"flag": {"type": "boolean"}},
                         "additionalProperties": False},
    },
''', "")
    manifests = {}
    for label, version in (("prev", "1.0.0"), ("minor", "1.1.0"), ("major", "2.0.0")):
        root = sandbox / f"ver-{label}"
        (root / "src" / "ragraf").mkdir(parents=True, exist_ok=True)
        (root / "src" / "ragraf" / "facade.py").write_text(facade_text(), encoding="utf-8")
        (root / "src" / "ragraf" / "mcp_server.py").write_text(
            MCP_STUB if label == "prev" else stub_without_beta, encoding="utf-8")
        (root / "src" / "ragraf" / "__init__.py").write_text(
            f'__version__ = "{version}"\n', encoding="utf-8")
        (root / ".ragraf").mkdir(parents=True, exist_ok=True)
        item_conn = journal(root / ".ragraf" / "control.sqlite")
        try:
            pin_mod.approve(item_conn, root, everything=True, by="проверка", kind="all")
        finally:
            item_conn.close()
        builder.write_pins(root, root)
        manifests[label] = root / "ops_pins.json"
    minor_code, minor_message = gate.check_contract_diff(manifests["prev"],
                                                          manifests["minor"])
    check(minor_code == 1 and "не мажорная" in minor_message
          and "ragraf_beta_tool" in minor_message,
          "минорная версия с ломающим изменением — отказ с именем",
          minor_message[:160])
    major_code, major_message = gate.check_contract_diff(manifests["prev"],
                                                          manifests["major"])
    check(major_code == 0 and "мажорная версия" in major_message
          and "ragraf_beta_tool" in major_message,
          "мажорная версия — проход, ломающее в отчёт", major_message[:160])
    warn_code, warn_message = gate.check_previous(None, sandbox / "ver-prev")
    check(warn_code == 0 and "WARN" in warn_message,
          "без --previous — предупреждение и проход", warn_message[:120])
    missing_code, missing_message = gate.check_previous(
        str(sandbox / "нет-такого.json"), sandbox / "ver-prev")
    check(missing_code == 1 and "нет" in missing_message,
          "названный прошлый манифест обязан существовать", missing_message[:120])
    real_parts = gate.version_parts
    counter = {"calls": 0}

    def always_major(_text):
        counter["calls"] += 1
        return (1, 0, 0) if counter["calls"] == 1 else (2, 0, 0)

    gate.version_parts = always_major
    try:
        muted = gate.check_contract_diff(manifests["prev"], manifests["minor"])
    finally:
        gate.version_parts = real_parts
    check(muted[0] == 0,
          "подсадка: с «всегда мажорной» версией ломающее проходит (свойство 18 ловит)",
          str(muted[0]))

    print()
    print("=" * 78)
    print("19. ОДНА КОМАНДА ВЫПУСКА: ПОРЯДОК И ЧЕГО НЕ ДЕЛАЕТ БЕЗ ИМЕНИ")
    print("=" * 78)
    rel_mod = load_module("ragraf-release", REPO / "tools" / "release" / "release.py")
    rel_corpus = make_corpus(sandbox / "rel", FACADE_STUB)
    (rel_corpus / "src" / "ragraf" / "__init__.py").write_text(
        '__version__ = "0.10.0"\n', encoding="utf-8")
    rel_out = sandbox / "rel-out"
    rel_out.mkdir(parents=True, exist_ok=True)
    order: list[str] = []

    def step_build(out_dir, source, manifest):
        order.append("build")
        return 0, "собрано"

    def step_gate(source, previous, allowed, reason, manifest):
        order.append("gate")
        return 0, "OK: сводка: ломающих 0, совместимых 0, всего изменённых 0 — совместимо"

    def step_approve(source, *, by, note):
        order.append("approve")
        return rel_mod.approve_pins(source, by=by, note=note)

    no_name_code, no_name_message = rel_mod.run_release(
        out=rel_out, source=rel_corpus, approved_by="  ",
        build=step_build, gate=step_gate, approve=step_approve)
    check(no_name_code == 2 and "--approved-by" in no_name_message and not order,
          "без имени человека выпуск не начинается", no_name_message[:120])

    no_reason_code, no_reason_message = rel_mod.run_release(
        out=rel_out, source=rel_corpus, approved_by="Михаил", allowed=("alpha",),
        build=step_build, gate=step_gate, approve=step_approve)
    check(no_reason_code == 2 and "--break-reason" in no_reason_message,
          "разрешение ломающих без причины — отказ", no_reason_message[:120])

    rel_conn = journal(rel_corpus / ".ragraf" / "control.sqlite")
    try:
        pins_before = len(pin_mod.history(rel_conn))
    finally:
        rel_conn.close()
    order.clear()

    def failing_build(out_dir, source, manifest):
        order.append("build")
        return 1, "нет источника из манифеста: community/CHANGELOG.md"

    build_code, build_message = rel_mod.run_release(
        out=rel_out, source=rel_corpus, approved_by="Михаил",
        build=failing_build, gate=step_gate, approve=step_approve)
    check(build_code == 1 and "сборка не состоялась" in build_message
          and order == ["build"],
          "отказ сборки останавливает ворота и подтверждение", str(order))
    rel_conn = journal(rel_corpus / ".ragraf" / "control.sqlite")
    try:
        pins_after_build = len(pin_mod.history(rel_conn))
    finally:
        rel_conn.close()
    check(pins_after_build == pins_before,
          "пины не тронуты отказом сборки", f"{pins_before} → {pins_after_build}")

    order.clear()

    def failing_gate(source, previous, allowed, reason, manifest):
        order.append("gate")
        return 1, "FAIL: README.md:409: документ называет 56 корпусов"

    gate_code, gate_message = rel_mod.run_release(
        out=rel_out, source=rel_corpus, approved_by="Михаил",
        build=step_build, gate=failing_gate, approve=step_approve)
    check(gate_code == 1 and "ворота не пропустили" in gate_message
          and order == ["build", "gate"],
          "отказ ворот останавливает подтверждение", str(order))
    rel_conn = journal(rel_corpus / ".ragraf" / "control.sqlite")
    try:
        pins_after_gate = len(pin_mod.history(rel_conn))
    finally:
        rel_conn.close()
    check(pins_after_gate == pins_before,
          "пины не тронуты отказом ворот", f"{pins_before} → {pins_after_gate}")

    order.clear()
    ok_code, ok_text = rel_mod.run_release(
        out=rel_out, source=rel_corpus, approved_by="Михаил", note="проверка",
        build=step_build, gate=step_gate, approve=step_approve)
    check(ok_code == 0 and order == ["build", "gate", "approve"],
          "порядок шагов: сборка → ворота → подтверждение", str(order))
    rel_conn = journal(rel_corpus / ".ragraf" / "control.sqlite")
    try:
        rows19 = [item for item in pin_mod.history(rel_conn) if item["op"] == "alpha"]
    finally:
        rel_conn.close()
    check(rows19 and rows19[0]["note"].startswith("0.10.0:"),
          "подтверждение внутри выпуска пишет версию в пометку пина",
          str(rows19[0]["note"]) if rows19 else "строк нет")
    builder.write_pins(rel_corpus, rel_corpus)
    rel_text = rel_mod.report(
        rel_corpus, rel_corpus, by="Михаил", note="проверка",
        allowed=("alpha",), break_reason="переименование по просьбе клиента",
        gate_message="OK: сводка: ломающих 0", approve_message="подтверждено объектов: 3")
    check("версия 0.10.0" in rel_text and "операции" in rel_text
          and "инструменты" in rel_text,
          "в отчёте есть версия и оба состояния", rel_text[:120])
    check("последняя смена набора:" in rel_text,
          "в отчёте есть дата последней смены", rel_text[-160:])
    check("разрешено человеком: alpha" in rel_text
          and "переименование по просьбе клиента" in rel_text,
          "в отчёте названо разрешённое и причина", rel_text[-160:])

    allowed_code, allowed_message = gate.check_contract_diff(
        manifests["prev"], manifests["minor"], allowed=("ragraf_beta_tool",),
        reason="решение клиента")
    check(allowed_code == 0 and "разрешено человеком" in allowed_message
          and "ragraf_beta_tool" in allowed_message,
          "поимённое разрешение с причиной пропускает минорную версию",
          allowed_message[:160])
    partial_code, partial_message = gate.check_contract_diff(
        manifests["prev"], manifests["minor"], allowed=("другой_объект",),
        reason="решение клиента")
    check(partial_code == 1 and "не мажорная" in partial_message,
          "разрешение чужого имени ломающее не пропускает", partial_message[:160])

    real_gate_step = rel_mod.gate_check
    rel_mod.gate_check = lambda *a, **k: (0, "OK: сводка: без ворот")
    try:
        sabotaged_code, _ = rel_mod.run_release(
            out=rel_out, source=rel_corpus, approved_by="Михаил",
            build=step_build, gate=rel_mod.gate_check, approve=step_approve)
    finally:
        rel_mod.gate_check = real_gate_step
    check(sabotaged_code == 0,
          "подсадка: подменённые ворота пропускают всё (свойство 19 ловит)",
          str(sabotaged_code))

    print()
    print("=" * 78)
    print("20. ЖУРНАЛ СМЕН: ЧТЕНИЕ, СРОК ХРАНЕНИЯ И СЧЁТЧИК")
    print("=" * 78)
    hist2 = make_corpus(sandbox / "hist2", FACADE_STUB)
    hist2_conn = journal(hist2 / ".ragraf" / "control.sqlite")
    try:
        check(pin_mod.history(hist2_conn, limit=50) == [],
              "пустая история — пустой список, а не выдумка")
        pin_mod.approve(hist2_conn, hist2, everything=True, by="Михаил", kind="op")
        (hist2 / "src" / "ragraf" / "facade.py").write_text(
            facade_text(limit_default=30), encoding="utf-8")
        pin_mod.approve(hist2_conn, hist2, everything=True, by="Анна", kind="op")
        rows = pin_mod.history(hist2_conn, limit=500)
        per_op: dict[str, int] = {}
        for item in rows:
            per_op[item["op"]] = per_op.get(item["op"], 0) + 1
        check(per_op.get("alpha") == 2 and sorted(per_op.values()).count(1) == len(per_op) - 1,
              "каждая СМЕНА — строка, неизменённое описание строк не плодит",
              str(per_op))
        filtered = pin_mod.history(hist2_conn, limit=500, op="alpha")
        check(len(filtered) == 2 and all(item["op"] == "alpha" for item in filtered),
              "фильтр по имени объекта работает", f"строк {len(filtered)}")
        check(pin_mod.history(hist2_conn, limit=50, kind="tool") == [],
              "фильтр по виду контракта работает")
        check(len(pin_mod.history(hist2_conn, limit=1)) == 1, "предел соблюдается")
        check(all(isinstance(item["age_seconds"], int) for item in rows),
              "у каждой строки есть возраст", "возраст не число")
        summary20 = pin_mod.authors(hist2_conn)
        check(summary20["total"] == len(rows),
              "сводка сходится с числом строк истории",
              f"{summary20['total']} против {len(rows)}")
        check({item["by"] for item in summary20["by_author"]} == {"Михаил", "Анна"},
              "сводка называет обоих авторов", str(summary20["by_author"])[:100])
        counts = {item["op"]: item["changes"] for item in summary20["by_object"]}
        check(counts.get("alpha") == 2 and summary20["top_object_changes"] == 2,
              "объект с наибольшим числом смен назван", str(summary20["top_object"]))
        check("не оценка" in summary20["note"],
              "сводка сама говорит, что это счётчик, а не качество",
              summary20["note"][:80])
    finally:
        hist2_conn.close()

    one = make_corpus(sandbox / "hist-one", FACADE_STUB)
    one_conn = journal(one / ".ragraf" / "control.sqlite")
    try:
        pin_mod.approve(one_conn, one, op="alpha", by="Михаил", kind="op")
        single = pin_mod.authors(one_conn)
        check(single["total"] == 1 and single["top_object_changes"] == 1,
              "при одной смене сводка не выдумывает лидера",
              str(single["top_object_changes"]))
    finally:
        one_conn.close()

    # Срок хранения: много старых смен и одна защищённая ссылкой из журнала.
    keep = make_corpus(sandbox / "hist-keep", FACADE_STUB)
    keep_conn = journal(keep / ".ragraf" / "control.sqlite")
    try:
        old = "2024-01-01T00:00:00+00:00"
        for index in range(60):
            keep_conn.execute(
                "INSERT INTO pin_history(op, kind, hash, approved_at, approved_by,"
                " note, previous_hash) VALUES(?,?,?,?,?,?,?)",
                ("alpha", "op", f"hash{index:02d}", old, "Михаил", "", ""))
        protected_hash = "hash00"
        keep_conn.execute(
            "INSERT INTO pin_event(ts, kind, level, op, observed, pinned,"
            " pin_age_seconds, detail, related_id, contract)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (old, "pin_mismatch", "high", "alpha", "наблюдено", protected_hash, 10,
             "ссылка на историю", None, "op"))
        keep_conn.commit()
        before_rows = len(pin_mod.history(keep_conn, limit=1000))
        trimmed = pin_mod.prune(keep_conn, keep_per_object=5, keep_days=180)
        after_rows = len(pin_mod.history(keep_conn, limit=1000))
        check(trimmed["removed"] == before_rows - after_rows and trimmed["removed"] > 0,
              "чистка удаляет старое и называет число", str(trimmed)[:100])
        kept_hashes = {item["hash"] for item in pin_mod.history(keep_conn, limit=1000)}
        check(len(kept_hashes) == 6,
              "осталось 5 последних смен плюс защищённая", f"осталось {len(kept_hashes)}")
        check(protected_hash in kept_hashes,
              "строка со ссылкой из журнала не удалена", str(sorted(kept_hashes)))
        check(trimmed["protected"] == 1,
              "чистка говорит, сколько строк прикрыла защита", str(trimmed["protected"]))
        real_protect = pin_mod._protected_ids
        pin_mod._protected_ids = lambda conn: set()
        try:
            again = pin_mod.prune(keep_conn, keep_per_object=2, keep_days=180)
            left = {item["hash"] for item in pin_mod.history(keep_conn, limit=1000)}
        finally:
            pin_mod._protected_ids = real_protect
        check(protected_hash not in left and again["removed"] > 0,
              "подсадка: без защиты связанная строка удаляется (свойство ловит)",
              str(again)[:100])
    finally:
        keep_conn.close()

    from ragraf import facade as facade_mod  # noqa: E402 — тяжёлый импорт, только здесь
    from ragraf import cli as cli_mod  # noqa: E402 — пакетный импорт, не по пути
    check("contract_pin_history" in cli_mod.OPS,
          "операция объявлена в списке подкоманд CLI")
    render = make_corpus(sandbox / "hist-render", FACADE_STUB)
    render_conn = journal(render / ".ragraf" / "control.sqlite")
    try:
        pin_mod.approve(render_conn, render, everything=True, by="Михаил", kind="op")
        stub = types.SimpleNamespace(
            control=types.SimpleNamespace(conn=render_conn),
            _config_hint=lambda: "config/ragraf.yaml",
            make_env=lambda op, provenance="": facade_mod.Envelope(
                op=op, index_rev="rev-test", capability="cap", drift={},
                provenance=provenance),
            _feature_module=lambda operation, module, feature: (pin_mod, None),
        )
        answer = facade_mod.Facade.op_contract_pin_history(stub, kind="all", limit=5)
        check(answer["found"] is True and answer["result"]["changes"],
              "операция отдаёт смены через фасад", str(answer["result"]["total"]))
        check(answer["result"]["retention"].startswith("хранится"),
              "операция называет срок хранения", answer["result"]["retention"][:60])
        check(bool(answer.get("latest_citation"))
              and answer["latest_citation"]["locator"].startswith("pin_history#"),
              "у строки есть адрес в журнале, а не только текст",
              str(answer.get("latest_citation"))[:100])
        empty_answer = facade_mod.Facade.op_contract_pin_history(stub, op="нет-такого")
        check(empty_answer["found"] is False
              and any("история смен пуста" in item for item in empty_answer["limits"]),
              "пустая выборка названа состоянием, а не пустым ответом",
              str(empty_answer["limits"])[:120])
        bad_kind = facade_mod.Facade.op_contract_pin_history(stub, kind="что-то")
        check(bad_kind["found"] is False and "вне списка" in str(bad_kind.get("error")),
              "вид контракта вне списка — отказ с причиной", str(bad_kind.get("error"))[:80])
    finally:
        render_conn.close()

    print()
    print("=" * 78)
    print("21. ПРОБА ПОВЕДЕНИЯ: ЧТО ПИН НЕ ВИДИТ")
    print("=" * 78)

    def make_call(behaviour: dict):
        """Подставной фасад: ответ зависит от поведения, а не от файла."""

        def call(op: str, args: dict) -> dict:
            if op in behaviour.get("raise", ()):
                raise RuntimeError("индекс не собран: proba")
            return {"op": op, "index_rev": f"rev-{behaviour.get('rev', 1)}",
                    "freshness": {"stale": bool(behaviour.get("stale", False))},
                    "value": behaviour.get("value", 1),
                    "found": True, "limits": ["предел"],
                    "checked_at": f"2026-11-10T00:00:0{behaviour.get('rev', 1)}+00:00"}

        return call

    probe = make_corpus(sandbox / "probe", FACADE_STUB)
    probe_conn = journal(probe / ".ragraf" / "control.sqlite")
    try:
        first = pin_mod.take(probe_conn, probe, call=make_call({"value": 1, "rev": 1}))
        hashes_first = {item["op"]: item["value_hash"] for item in first["taken"]}
        check(all(item["status"] == "sampled" for item in first["taken"]),
              "проба берётся для всех вызовов списка", str(first["unknown"]))
        second = pin_mod.take(probe_conn, probe, call=make_call({"value": 1, "rev": 9}))
        hashes_second = {item["op"]: item["value_hash"] for item in second["taken"]}
        check(hashes_first == hashes_second,
              "один код дважды — один хеш, хотя ревизия и время разные",
              f"{list(hashes_first.values())[:1]} против {list(hashes_second.values())[:1]}")

        volatile = pin_mod.normalise({"a": 1, "index_rev": "x", "limits": [],
                                      "checked_at": "t",
                                      "nested": {"age_seconds": 5, "keep": "да"}})
        check(volatile == {"a": 1, "nested": {"keep": "да"}},
              "изменчивые ключи вырезаются, остальное остаётся", str(volatile))

        real_drop = pin_mod.SAMPLE_VOLATILE
        pin_mod.SAMPLE_VOLATILE = ()
        try:
            # Взяты ЧИСЛОВЫЕ изменчивые значения: строки вида `rev-1` маскируются
            # очисткой текста и подсадку бы не выявили.
            raw_one = pin_mod.normalise({"age_seconds": 1, "value": 1})
            raw_two = pin_mod.normalise({"age_seconds": 2, "value": 1})
            sabotaged = pin_mod._short(pin_mod.canonical(raw_one)) != \
                pin_mod._short(pin_mod.canonical(raw_two))
        finally:
            pin_mod.SAMPLE_VOLATILE = real_drop
        check(sabotaged,
              "подсадка: без вырезания возраста проба видит разницу (свойство 1 ловит)",
              str(raw_one))

        no_index = pin_mod.take(probe_conn, probe,
                                call=make_call({"raise": ("usage",)}))
        skipped = [item for item in no_index["taken"] if item["status"] == "not_sampled"]
        check(len(skipped) == 1 and "индекс не собран" in skipped[0]["detail"],
              "невозможный вызов — not_sampled с причиной", str(skipped)[:120])
        empty_conn = journal(sandbox / "probe-empty.sqlite")
        try:
            empty_compare = pin_mod.compare(empty_conn, probe, call=make_call({}))
        finally:
            empty_conn.close()
        check(empty_compare["totals"]["no_baseline"] == len(pin_mod.PROBES),
              "без прошлой пробы сверка не выдаёт совпадение",
              str(empty_compare["totals"]))
    finally:
        probe_conn.close()

    # Форма не изменилась, поведение изменилось — пин этого не видит.
    shape = make_corpus(sandbox / "probe-shape", FACADE_STUB)
    shape_conn = journal(shape / ".ragraf" / "control.sqlite")
    try:
        pin_mod.approve(shape_conn, shape, everything=True, by="Михаил", kind="op")
        pin_mod.take(shape_conn, shape, call=make_call({"value": 1}))
        before_state = pin_mod.status(shape_conn, shape)
        changed_only = pin_mod.compare(shape_conn, shape, call=make_call({"value": 2}))
        check(before_state["pin_state"]["state"] == "agreed",
              "правка только поведения: пин остаётся agreed",
              str(before_state["pin_state"]["state"]))
        check(changed_only["verdict"] == "изменилось"
              and set(changed_only["changed"]) == set(pin_mod.PROBES),
              "правка только поведения видна пробой", str(changed_only["changed"])[:80])
        events_before = len(pin_mod.events(shape_conn))
        check(events_before == 0,
              "при этом pin_mismatch не появился", f"событий {events_before}")

        # Правка формы: и пин, и проба это видят.
        (shape / "src" / "ragraf" / "facade.py").write_text(
            facade_text(limit_default=30), encoding="utf-8")
        form_state = pin_mod.status(shape_conn, shape)
        check(form_state["pin_state"]["state"] == "diverged",
              "правка формы даёт расхождение пина",
              str(form_state["pin_state"]["state"]))
        both = pin_mod.compare(shape_conn, shape, call=make_call({"value": 2}))
        check(both["verdict"] == "изменилось",
              "и проба при правке формы тоже расходится", str(both["changed"])[:80])
        accepted = pin_mod.take(shape_conn, shape, call=make_call({"value": 2}))
        quiet = pin_mod.compare(shape_conn, shape, call=make_call({"value": 2}))
        check(quiet["verdict"] == "не изменилось"
              and quiet["totals"]["same"] == len(pin_mod.PROBES),
              "после новой пробы поведение снова сходится",
              str(quiet["totals"]))
        state21 = pin_mod.sample_state(shape_conn)
        check(state21["state"] == "agreed" and state21["checked"] > 0,
              "состояние проб читается для ворот", str(state21))
        check(accepted["taken"][0]["value_hash"] != "",
              "проба не пустая", str(accepted["taken"][0])[:80])
    finally:
        shape_conn.close()

    render21 = make_corpus(sandbox / "probe-render", FACADE_STUB)
    render21_conn = journal(render21 / ".ragraf" / "control.sqlite")
    try:
        stub21 = types.SimpleNamespace(
            control=types.SimpleNamespace(conn=render21_conn),
            _config_hint=lambda: "config/ragraf.yaml",
            make_env=lambda op, provenance="": facade_mod.Envelope(
                op=op, index_rev="rev-test", capability="cap", drift={},
                provenance=provenance),
            cfg=types.SimpleNamespace(root=render21),
            _feature_module=lambda operation, module, feature: (pin_mod, None),
            _probe_ready=lambda need: True,
        )
        stub21._sample_call = lambda op, args: facade_mod.Facade._sample_call(stub21, op, args)
        stub21.op_catalog = lambda: {"corpora": ["a", "b"], "index_rev": "x"}
        stub21.usage_summary = lambda **kwargs: {"calls": 3, "index_rev": "y"}
        stub21.op_freshness = lambda **kwargs: {"stale": False}
        stub21.op_index_status = lambda: {"rev": "rev-test"}
        stub21.op_route = lambda **kwargs: {"candidates": ["ragraf"], "limit": kwargs.get("limit")}
        take_answer = facade_mod.Facade.op_contract_pin_sample(
            stub21, mode="take", op="catalog,usage")
        check(take_answer["sampled"] == 2 and not take_answer["not_sampled"],
              "операция записывает пробу через живой путь", str(take_answer["sampled"]))
        same_answer = facade_mod.Facade.op_contract_pin_sample(
            stub21, mode="compare", op="catalog,usage")
        check(same_answer["state"] == "agreed" and not same_answer["changed"],
              "сверка сразу после пробы — совпадение", str(same_answer["state"]))
        bad_mode = facade_mod.Facade.op_contract_pin_sample(stub21, mode="потом")
        check(bad_mode["found"] is False and "вне списка" in str(bad_mode.get("error")),
              "режим вне списка — отказ с причиной", str(bad_mode.get("error"))[:80])
        unknown_op = facade_mod.Facade.op_contract_pin_sample(
            stub21, mode="take", op="нет-такой")
        check(unknown_op["found"] is False and "отбор проб пуст" in str(unknown_op.get("error")),
              "чужое имя пробой не прикидывается", str(unknown_op.get("error"))[:100])
    finally:
        render21_conn.close()

    check("contract_pin_sample" in cli_mod.OPS,
          "операция пробы объявлена в списке подкоманд CLI")

    print()
    print("=" * 78)
    print("22. ПРОБЫ В ВЫПУСКЕ: УСЛОВИЯ, ПУТИ И ПРИНЯТИЕ")
    print("=" * 78)
    static_pin_mod = pin_mod  # тот же модуль: пробы и история — одни и те же
    check(static_pin_mod.PROBES and all(
        {"method", "args", "needs"} <= set(item) for item in static_pin_mod.PROBES.values()),
          "у каждой пробы есть метод, аргументы и условие",
          str(sorted(static_pin_mod.PROBES)[:5]))
    check(any(item.get("args") for item in static_pin_mod.PROBES.values()),
          "есть проба С АРГУМЕНТАМИ (проверяет их передачу)",
          str([name for name, item in static_pin_mod.PROBES.items() if item.get("args")]))
    check(static_pin_mod.probe_method("usage") == "usage_summary",
          "имя метода берётся из объявления, а не из op_<имя>",
          static_pin_mod.probe_method("usage"))

    needs_corpus = make_corpus(sandbox / "needs", FACADE_STUB)
    needs_conn = journal(needs_corpus / ".ragraf" / "control.sqlite")
    try:
        not_ready = static_pin_mod.take(
            needs_conn, needs_corpus, call=make_call({}), ready=lambda need: False)
        degraded_all = [item for item in not_ready["taken"]
                        if item["status"] == "sampled_degraded"]
        check(len(degraded_all) == len(static_pin_mod.PROBES)
              and all("needs=" in item["detail"] for item in degraded_all),
              "условие не выполнено — ответ-отказ записан с названным условием",
              str(degraded_all)[:120])
        only_config = static_pin_mod.take(
            needs_conn, needs_corpus, call=make_call({}),
            ready=lambda need: need == "config")
        statuses = {item["op"]: item["status"] for item in only_config["taken"]}
        check(statuses.get("catalog") == "sampled"
              and statuses.get("freshness") == "sampled_degraded",
              "готовность проверяется по КАЖДОМУ условию", str(statuses))
    finally:
        needs_conn.close()

    paths_check = static_pin_mod.diff_paths(
        {"corpora": ["a", "b"], "totals": {"changed": 0}},
        {"corpora": ["a", "c"], "totals": {"changed": 2}})
    check("corpora[1]" in paths_check and "totals.changed" in paths_check,
          "разница называется путями, а не «изменилось»", str(paths_check))
    check(static_pin_mod.diff_paths({"a": 1}, {"a": 1, "b": 2}) == ["b (появилось)"],
          "новый ключ назван отдельно",
          str(static_pin_mod.diff_paths({"a": 1}, {"a": 1, "b": 2})))

    accept_corpus = make_corpus(sandbox / "accept", FACADE_STUB)
    accept_conn = journal(accept_corpus / ".ragraf" / "control.sqlite")
    try:
        static_pin_mod.take(accept_conn, accept_corpus, call=make_call({"value": 1}))
        refused = None
        try:
            static_pin_mod.accept(accept_conn, accept_corpus, by="  ",
                                  call=make_call({"value": 2}))
        except static_pin_mod.PinRefusal as exc:
            refused = str(exc)
        check(refused is not None and "принимающий" in refused,
              "принятие без имени — отказ с причиной", str(refused)[:100])
        accepted = static_pin_mod.accept(accept_conn, accept_corpus, by="Михаил",
                                         note="сдвиг границы",
                                         call=make_call({"value": 2}))
        check(accepted["total"] == len(static_pin_mod.PROBES),
              "принятие записывает все пробы", str(accepted["total"]))
        sample_history = [item for item in static_pin_mod.history(accept_conn, limit=200,
                                                                  kind="sample")
                          if item["approved_by"] == "Михаил"]
        check(sample_history and all(item["kind"] == "sample" for item in sample_history),
              "принятие оставляет строку истории вида sample",
              str(len(sample_history)))
        if sample_history:
            check(sample_history[0]["note"] == "сдвиг границы",
                  "в истории есть пометка, зачем приняли", str(sample_history[0]["note"]))
            check(any(item["previous_hash"] for item in sample_history),
                  "в истории видно, каким было поведение",
                  str(sample_history[0]["previous_hash"])[:20])
        else:
            check(False, "в истории есть пометка, зачем приняли", "строк нет")
            check(False, "в истории видно, каким было поведение", "строк нет")
        after_accept = static_pin_mod.compare(accept_conn, accept_corpus,
                                             call=make_call({"value": 2}))
        check(after_accept["verdict"] == "не изменилось",
              "после принятия сверка снова совпадает", after_accept["verdict"])

        real_execute = static_pin_mod.accept

        def accept_without_history(conn, root, *, by, note="", call=None, ready=None,
                                   ops=None):
            """Подсадка: принятие без записи в историю."""
            pin_mod_local = static_pin_mod
            saved = pin_mod_local.history
            try:
                result = real_execute(conn, root, by=by, note=note, call=call,
                                      ready=ready, ops=ops)
            finally:
                pin_mod_local.history = saved
            conn.execute("DELETE FROM pin_history WHERE kind='sample'")
            conn.commit()
            return result

        static_pin_mod.take(accept_conn, accept_corpus, call=make_call({"value": 2}))
        accept_without_history(accept_conn, accept_corpus, by="Михаил",
                              call=make_call({"value": 3}))
        lost = [item for item in static_pin_mod.history(accept_conn, limit=200,
                                                        kind="sample")
                if item["approved_by"] == "Михаил"]
        check(not lost,
              "подсадка: без записи в историю смена поведения теряется (свойство ловит)",
              str(len(lost)))
    finally:
        accept_conn.close()

    manifest_corpus = make_corpus(sandbox / "manifest", FACADE_STUB)
    manifest_conn = journal(manifest_corpus / ".ragraf" / "control.sqlite")
    try:
        static_pin_mod.take(manifest_conn, manifest_corpus, call=make_call({"value": 1}))
        first_doc = static_pin_mod.samples_document(manifest_conn)
    finally:
        manifest_conn.close()
    check(first_doc["probes"] and first_doc["state"]["total"] == len(static_pin_mod.PROBES),
          "пробы для манифеста собираются", str(list(first_doc["probes"])[:4]))
    check(all("entries" in block for block in first_doc["probes"].values())
          and all(entry.get("value") for block in first_doc["probes"].values()
                  for entry in block["entries"]),
          "в манифест идут хеши, а не значения", str(first_doc["probes"])[:80])

    before_manifest = sandbox / "samples-before.json"
    after_manifest = sandbox / "samples-after.json"
    before_manifest.write_text(json.dumps(
        {"version": "0.10.0", "samples": {"probes": {"catalog": {
            "entries": [{"args": "aaa", "value": "111", "status": "sampled"}],
            "verdict": "same"}}, "pin_state": "agreed", "changed": 0, "total": 1}},
        ensure_ascii=False), encoding="utf-8")
    after_manifest.write_text(json.dumps(
        {"version": "0.11.0", "samples": {"probes": {
            "catalog": {"entries": [{"args": "aaa", "value": "222", "status": "sampled"}],
                        "verdict": "changed"},
            "route": {"entries": [{"args": "bbb", "value": "333", "status": "sampled"}],
                      "verdict": ""}}, "pin_state": "agreed", "changed": 1, "total": 2}},
        ensure_ascii=False), encoding="utf-8")
    code22, message22 = gate.check_samples_since(before_manifest, after_manifest)
    check(code22 == 0 and "WARN" in message22 and "catalog" in message22
          and "0.10.0" in message22,
          "сравнение двух выпусков называет операцию и выпуск", message22[:150])
    same_code, same_message = gate.check_samples_since(after_manifest, after_manifest)
    check(same_code == 0 and "не изменилось" in same_message,
          "одинаковые пробы — не изменилось", same_message[:120])
    none_code, none_message = gate.check_samples_since(None, after_manifest)
    check(none_code == 0 and "прошлого манифеста нет" in none_message,
          "без прошлого манифеста — предупреждение, а не отказ", none_message[:100])

    print()
    print("=" * 78)
    print("23. ПРОБЫ БЕЗ ИНДЕКСА, ПУТИ В МАНИФЕСТЕ И ПРИЁМ В СБОРКЕ")
    print("=" * 78)

    def refusal_call(marker: str):
        """Фасад без индекса: не падает, а отвечает отказом."""

        def call(op: str, args: dict) -> dict:
            if op in ("freshness", "index_status"):
                return {"op": op, "found": False, "error": marker,
                        "index_rev": "нет", "limits": ["индекс не собран"]}
            return {"op": op, "ok": True, "index_rev": "rev-1"}

        return call

    degraded_corpus = make_corpus(sandbox / "degraded", FACADE_STUB)
    degraded_conn = journal(degraded_corpus / ".ragraf" / "control.sqlite")
    try:
        first = static_pin_mod.take(degraded_conn, degraded_corpus,
                                    call=refusal_call("индекс не собран"),
                                    ready=lambda need: need != "index")
        statuses = {item["op"]: item["status"] for item in first["taken"]}
        check(statuses.get("freshness") == "sampled_degraded"
              and statuses.get("catalog") == "sampled",
              "без индекса проба записана как sampled_degraded, а не пустая",
              str(statuses))
        hashes = {item["op"]: item["value_hash"] for item in first["taken"]
                  if item["status"] == "sampled_degraded"}
        check(all(hashes.values()),
              "у деградированной пробы есть хеш ОТВЕТА-ОТКАЗА", str(hashes))
        again = static_pin_mod.take(degraded_conn, degraded_corpus,
                                    call=refusal_call("индекс не собран"),
                                    ready=lambda need: need != "index")
        hashes_again = {item["op"]: item["value_hash"] for item in again["taken"]
                        if item["status"] == "sampled_degraded"}
        check(hashes == hashes_again,
              "хеш отказа стабилен между двумя прогонами", f"{hashes} против {hashes_again}")
        other = static_pin_mod.take(degraded_conn, degraded_corpus,
                                    call=refusal_call("индекс не собран: код 3"),
                                    ready=lambda need: need != "index")
        hashes_other = {item["op"]: item["value_hash"] for item in other["taken"]
                        if item["status"] == "sampled_degraded"}
        check(hashes != hashes_other,
              "правка текста отказа меняет хеш (свойство 2 ловит)",
              f"{hashes} против {hashes_other}")
        empty = static_pin_mod.take(degraded_conn, degraded_corpus, call=None)
        still_empty = [item for item in empty["taken"] if item["status"] == "not_sampled"]
        check(len(still_empty) == len(static_pin_mod.PROBES),
              "без вызывающего остаётся not_sampled: вызов невозможен",
              str(len(still_empty)))
    finally:
        degraded_conn.close()

    paths_corpus = make_corpus(sandbox / "paths", FACADE_STUB)
    paths_conn = journal(paths_corpus / ".ragraf" / "control.sqlite")
    try:
        static_pin_mod.take(paths_conn, paths_corpus, call=make_call({"value": 1}))
        changed23 = static_pin_mod.compare(paths_conn, paths_corpus,
                                          call=make_call({"value": 2}))
        doc23 = static_pin_mod.samples_document(paths_conn)
        paths_in_doc = {name: block.get("paths") for name, block in doc23["probes"].items()
                        if block.get("paths")}
        check(changed23["changed"] and paths_in_doc,
              "пути расхождения попадают в манифест", str(paths_in_doc)[:100])
        doc_again = static_pin_mod.samples_document(paths_conn)
        check(json.dumps(doc23, ensure_ascii=False, sort_keys=True)
              == json.dumps(doc_again, ensure_ascii=False, sort_keys=True),
              "манифест двух чтений побайтово одинаков", "разные")
        check(all(isinstance(path, str) for group in paths_in_doc.values() for path in group),
              "в путях строки, а не значения ответа", str(paths_in_doc)[:80])
    finally:
        paths_conn.close()

    builder_mod = load_module("ragraf-build16", REPO / "tools" / "release" / "build_release.py")
    gate_mod = gate
    no_name = builder_mod.accept_samples(paths_corpus, by="  ", note="")
    check(no_name[0] == 2 and "--approved-by" in no_name[1],
          "приём проб в сборке без имени — отказ", no_name[1][:100])
    accepted = builder_mod.accept_samples(paths_corpus, by="Михаил", note="0.11.0")
    check(accepted[0] == 0 and "принято проб" in accepted[1],
          "приём проб с именем выполняется", accepted[1][:80])
    hist_conn = journal(paths_corpus / ".ragraf" / "control.sqlite")
    try:
        rows23 = static_pin_mod.history(hist_conn, limit=100, kind="sample")
        check(rows23 and rows23[0]["approved_by"] == "Михаил"
              and rows23[0]["note"] == "0.11.0",
              "приём оставляет строку истории с автором и пометкой",
              str(rows23[0])[:120] if rows23 else "строк нет")
        check(all(item["previous_hash"] != item["hash"] for item in rows23),
              "в истории видно прежнее значение", "previous совпал с hash")
    finally:
        hist_conn.close()
    rebuilt = static_pin_mod.samples_document(
        journal(paths_corpus / ".ragraf" / "control.sqlite"))
    check(rebuilt["state"]["changed"] == 0,
          "после приёма в манифесте нет расхождений", str(rebuilt["state"]))

    gate_with_paths = sandbox / "gate-paths-after.json"
    gate_with_paths.write_text(json.dumps(
        {"version": "0.11.0", "samples": {"probes": {"catalog": {
            "entries": [{"args": "a", "value": "999", "status": "sampled"}],
            "verdict": "changed", "paths": ["corpora (длина 1 → 2)"]}},
            "pin_state": "changed", "changed": 1, "total": 1}}, ensure_ascii=False),
        encoding="utf-8")
    code23, message23 = gate_mod.check_samples_since(before_manifest, gate_with_paths)
    check(code23 == 0 and "corpora (длина 1 → 2)" in message23,
          "отчёт ворот называет путь, а не только операцию", message23[:150])
    check("999" not in message23 and "111" not in message23,
          "значения проб в отчёт не попадают", message23[:100])

    real_take = static_pin_mod.take

    def take_without_degraded(*args, **kwargs):
        """Подсадка: условие не выполнено — сразу not_sampled, без ответа."""
        kwargs.pop("ready", None)
        return real_take(*args, **kwargs)

    static_pin_mod.take = take_without_degraded
    try:
        probe_conn = journal(sandbox / "sabotage23.sqlite")
        try:
            plain = static_pin_mod.take(probe_conn, degraded_corpus,
                                        call=refusal_call("индекс не собран"))
        finally:
            probe_conn.close()
    finally:
        static_pin_mod.take = real_take
    plain_statuses = {item["op"]: item["status"] for item in plain["taken"]}
    check("sampled_degraded" not in plain_statuses.values(),
          "подсадка: без деградированных проб отказ не запоминается (свойство 1 ловит)",
          str(plain_statuses))

    print()
    print("=" * 78)
    print("24. ОТКАЗ КАК ПРОБА: ЧИСТЫЙ ХЕШ, ОТБОР И ВИДЫ ИЗМЕНЕНИЙ")
    print("=" * 78)
    check(static_pin_mod.clean_text(r"файл C:\\AutoClaw\\RAGRAF\\config\\ragraf.yaml не найден")
          == "файл ragraf.yaml не найден",
          "абсолютный путь заменён именем файла",
          static_pin_mod.clean_text(r"C:\\a\\b\\c.yaml"))
    check(static_pin_mod.clean_text("корпус: C:/tmp/x/config/ragraf.yaml")
          == "корпус: ragraf.yaml",
          "прямые слэши в пути тоже убираются",
          static_pin_mod.clean_text("корпус: C:/tmp/x/config/ragraf.yaml"))
    check("rev-*" in static_pin_mod.clean_text("ревизия rev-1503 недоступна")
          and "<хеш>" in static_pin_mod.clean_text("хеш a8b28ec85165f420bb12"),
          "номер ревизии и длинный хеш маскируются",
          static_pin_mod.clean_text("rev-1503 a8b28ec85165f420bb12"))

    def refusal_with(path: str):
        """Фасад, в отказе которого лежит адрес корпуса."""

        def call(op: str, args: dict) -> dict:
            return {"op": op, "found": False,
                    "error": f"корпус не собран: {path}", "limits": [],
                    "index_rev": "нет"}

        return call

    left = make_corpus(sandbox / "corpus-left", FACADE_STUB)
    right = make_corpus(sandbox / "other-root-24" / "corpus", FACADE_STUB)
    left_conn = journal(left / ".ragraf" / "control.sqlite")
    right_conn = journal(right / ".ragraf" / "control.sqlite")
    try:
        left_take = static_pin_mod.take(left_conn, left,
                                        call=refusal_with(str(left.resolve())))
        right_take = static_pin_mod.take(right_conn, right,
                                         call=refusal_with(str(right.resolve())))
        left_hashes = {item["op"]: item["value_hash"] for item in left_take["taken"]}
        right_hashes = {item["op"]: item["value_hash"] for item in right_take["taken"]}
        check(left_hashes == right_hashes,
              "два корпуса в разных каталогах дают ОДИН хеш отказа",
              f"{list(left_hashes.values())[:1]} против {list(right_hashes.values())[:1]}")

        real_clean = static_pin_mod.clean_text
        static_pin_mod.clean_text = lambda value: str(value)
        try:
            raw_left = static_pin_mod.take(left_conn, left,
                                          call=refusal_with(str(left.resolve())))
            raw_right = static_pin_mod.take(right_conn, right,
                                           call=refusal_with(str(right.resolve())))
        finally:
            static_pin_mod.clean_text = real_clean
        raw_a = {item["op"]: item["value_hash"] for item in raw_left["taken"]}
        raw_b = {item["op"]: item["value_hash"] for item in raw_right["taken"]}
        check(raw_a != raw_b,
              "подсадка: без очистки путь в отказе разводит хеши (свойство 1 ловит)",
              "хеши совпали")
    finally:
        left_conn.close()
        right_conn.close()

    select_corpus = make_corpus(sandbox / "select", FACADE_STUB)
    select_conn = journal(select_corpus / ".ragraf" / "control.sqlite")
    try:
        static_pin_mod.take(select_conn, select_corpus, call=make_call({"value": 1}))
        static_pin_mod.compare(select_conn, select_corpus, call=make_call({"value": 1}))
        only_one = static_pin_mod.accept(
            select_conn, select_corpus, by="Михаил", note="одна правка",
            call=make_call({"value": 2}), ops=["catalog"])
        check(only_one["total"] == 1,
              "приём касается ТОЛЬКО названной пробы", str(only_one["total"]))
        after_one = static_pin_mod.compare(select_conn, select_corpus,
                                          call=make_call({"value": 2}))
        check(after_one["totals"]["changed"] == len(static_pin_mod.PROBES) - 1,
              "остальные пробы остались расходящимися", str(after_one["totals"]))
        changed_list = static_pin_mod.changed_ops(select_conn)
        check("catalog" not in changed_list and changed_list,
              "`changed_ops` не показывает уже принятое", str(changed_list))
        stub24 = types.SimpleNamespace(
            control=types.SimpleNamespace(conn=select_conn),
            _config_hint=lambda: "config/ragraf.yaml",
            make_env=lambda op, provenance="": facade_mod.Envelope(
                op=op, index_rev="rev-test", capability="cap", drift={},
                provenance=provenance),
            cfg=types.SimpleNamespace(root=select_corpus),
            _feature_module=lambda operation, module, feature: (pin_mod, None),
            _probe_ready=lambda need: True,
        )
        stub24._sample_call = lambda op, args: facade_mod.Facade._sample_call(stub24, op, args)
        stub24.op_catalog = lambda: {"corpora": ["a"], "value": 3}
        stub24.usage_summary = lambda **kwargs: {"calls": 4}
        stub24.op_freshness = lambda **kwargs: {"stale": False}
        stub24.op_index_status = lambda: {"rev": "rev-2"}
        stub24.op_route = lambda **kwargs: {"candidates": ["x"]}
        only_changed = facade_mod.Facade.op_contract_pin_sample(
            stub24, mode="accept", by="Михаил", only_changed=True)
        check(only_changed["found"] is True
              and "catalog" not in only_changed["selected"]
              and len(only_changed["selected"]) == len(pin_mod.PROBES) - 1,
              "`only_changed` берёт только разошедшиеся, без принятой",
              str(only_changed.get("selected")))
        skipped = facade_mod.Facade.op_contract_pin_sample(
            stub24, mode="compare", except_ops="catalog,usage")
        check("catalog" not in skipped["selected"] and "usage" not in skipped["selected"],
              "`except` исключает названные пробы", str(skipped.get("selected")))
        empty_pick = facade_mod.Facade.op_contract_pin_sample(
            stub24, mode="compare", op="catalog", except_ops="catalog")
        check(empty_pick["found"] is False and "отбор проб пуст" in str(empty_pick.get("error")),
              "пустой отбор — отказ с причиной, а не пустой ответ",
              str(empty_pick.get("error"))[:80])
    finally:
        select_conn.close()

    kinds = gate.group_paths(["corpora (длина 1 → 2)", "b (появилось)",
                             "c (исчезло)", "totals.changed"])
    check(kinds == {"длина списка": 1, "новый ключ": 1, "исчез ключ": 1, "значение": 1},
          "виды изменений считаются по пути", str(kinds))
    grouped_manifest = sandbox / "grouped-after.json"
    grouped_payload = {
        "version": "0.12.0",
        "samples": {
            "probes": {
                "catalog": {
                    "entries": [{"args": "a", "value": "777", "status": "sampled"}],
                    "verdict": "changed",
                    "paths": ["corpora (длина 1 → 2)", "totals.changed"],
                },
            },
            "pin_state": "changed", "changed": 1, "total": 1,
        },
    }
    grouped_manifest.write_text(json.dumps(grouped_payload, ensure_ascii=False),
                               encoding="utf-8")
    grouped_code, grouped_message = gate.check_samples_since(before_manifest,
                                                            grouped_manifest)
    check(grouped_code == 0 and "по видам:" in grouped_message
          and "длина списка 1" in grouped_message and "значение 1" in grouped_message,
          "отчёт группирует изменения по видам", grouped_message[:170])
    check("777" not in grouped_message and "111" not in grouped_message,
          "значений в отчёте нет", grouped_message[:120])

    history_corpus = make_corpus(sandbox / "history24", FACADE_STUB)
    history_conn = journal(history_corpus / ".ragraf" / "control.sqlite")
    try:
        static_pin_mod.take(history_conn, history_corpus, call=make_call({"value": 1}))
        static_pin_mod.accept(history_conn, history_corpus, by="Михаил",
                              note="принято в X17", call=make_call({"value": 2}))
        stub24b = types.SimpleNamespace(
            control=types.SimpleNamespace(conn=history_conn),
            _config_hint=lambda: "config/ragraf.yaml",
            make_env=lambda op, provenance="": facade_mod.Envelope(
                op=op, index_rev="rev-test", capability="cap", drift={},
                provenance=provenance),
            _feature_module=lambda operation, module, feature: (pin_mod, None),
        )
        sample_history = facade_mod.Facade.op_contract_pin_history(
            stub24b, kind="sample", limit=20)
        check(sample_history["found"] is True and sample_history["result"]["changes"],
              "история вида sample читается операцией",
              str(sample_history["result"].get("total")))
        check(all(item["kind"] == "sample"
                  for item in sample_history["result"]["changes"]),
              "в ответе только строки принятий",
              str([item["kind"] for item in sample_history["result"]["changes"]])[:60])
        bad_kind24 = facade_mod.Facade.op_contract_pin_history(stub24b, kind="что-то")
        check(bad_kind24["found"] is False and "вне списка" in str(bad_kind24.get("error")),
              "чужой вид по-прежнему отказ", str(bad_kind24.get("error"))[:70])
    finally:
        history_conn.close()

    print()
    print("=" * 78)
    print("25. ИТОГ ВЫПУСКА: ТРИ СОСТОЯНИЯ И ОДНА СТРОКА ВЕРДИКТА")
    print("=" * 78)

    def state25(**kwargs) -> dict:
        base = {"version": "0.11.0", "ops": "agreed", "tools": "agreed",
                "behaviour": "checked", "behaviour_note": "", "blocks": [],
                "warnings": [], "actions": []}
        base.update(kwargs)
        return base

    code_ok, lines_ok = gate.render_summary(state25())
    check(code_ok == 0 and any(line.startswith("вердикт: разрешена") for line in lines_ok),
          "состояние «всё сверено» — разрешена", str(lines_ok[-1]))
    check(any(line.startswith("состояния:") for line in lines_ok)
          and any(line.startswith("блокирует:") for line in lines_ok)
          and any(line.startswith("предупреждает:") for line in lines_ok),
          "итог называет три состояния и две группы", str(lines_ok[:4]))

    code_warn, lines_warn = gate.render_summary(
        state25(warnings=["поведение с выпуска 0.10.0: изменилось у 1"]))
    check(code_warn == 0 and "разрешена с предупреждениями" in lines_warn[-1],
          "предупреждение — разрешена с предупреждениями", lines_warn[-1])

    code_stop, lines_stop = gate.render_summary(
        state25(blocks=["числа README не совпадают"], actions=["выровнять числа"]))
    check(code_stop == 1 and "остановлена: числа README не совпадают" in lines_stop[-1],
          "блокировка — остановлена с причиной", lines_stop[-1])
    check(any(line.startswith("действие:") for line in lines_stop),
          "к остановке приложено действие", str(lines_stop[-2]))

    # Главное свойство: «все пробы деградированы» — это НЕ «всё хорошо».
    degraded_doc = {"samples": {"probes": {
        "catalog": {"entries": [{"status": "sampled_degraded"}], "verdict": ""},
        "route": {"entries": [{"status": "sampled_degraded"}], "verdict": ""}},
        "changed": 0, "total": 2}}
    degraded_state, degraded_note = gate.behaviour_state(degraded_doc)
    check(degraded_state == "unknown" and "ни одна проба" in degraded_note,
          "все пробы деградированы — поведение НЕ проверено",
          f"{degraded_state}: {degraded_note}")
    code_unknown, lines_unknown = gate.render_summary(
        state25(behaviour=degraded_state, behaviour_note=degraded_note))
    check(code_unknown == 0 and "поведение не проверено" in lines_unknown[-1],
          "вердикт называет непроверенное поведение, а не молчит", lines_unknown[-1])

    mixed_doc = {"samples": {"probes": {
        "catalog": {"entries": [{"status": "sampled"}], "verdict": "same"},
        "route": {"entries": [{"status": "sampled_degraded"}], "verdict": ""}},
        "changed": 0, "total": 2}}
    mixed_state, mixed_note = gate.behaviour_state(mixed_doc)
    check(mixed_state == "partial" and "частично" in mixed_note,
          "часть проб деградирована — сверено частично", f"{mixed_state}: {mixed_note}")

    no_probes_state, no_probes_note = gate.behaviour_state({"samples": {"probes": {}, "total": 0}})
    check(no_probes_state == "unknown", "без проб поведение не проверено даже при agreed",
          no_probes_note)

    real_behaviour = gate.behaviour_state
    gate.behaviour_state = lambda document: ("checked", "совпало")
    try:
        muted = gate.behaviour_state(degraded_doc)
    finally:
        gate.behaviour_state = real_behaviour
    check(muted[0] == "checked" and muted[0] != degraded_state,
          "подсадка: если «деградировано» считать «совпало», свойство 3 падает",
          f"{muted} против {degraded_state}")

    markdown_one = gate.summary_markdown(state25())
    markdown_two = gate.summary_markdown(state25())
    check(markdown_one == markdown_two and "## Вердикт" in markdown_one,
          "markdown двух прогонов побайтово одинаков", "разные")
    check("C:\\" not in markdown_one and "/tmp" not in markdown_one,
          "в отчёте нет путей машины", markdown_one[:60])
    check("разрешена" in markdown_one.split("## Вердикт")[-1],
          "вердикт есть и в markdown", markdown_one[-80:])

    from ragraf import facade as facade_mod25  # noqa: E402 — нужен уже выше
    report_corpus = make_corpus(sandbox / "report25", FACADE_STUB)
    report_conn = journal(report_corpus / ".ragraf" / "control.sqlite")
    try:
        static_pin_mod.take(report_conn, report_corpus, call=make_call({"value": 1}))
        builder_made = load_module("ragraf-build25", REPO / "tools" / "release" / "build_release.py")
        builder_made.write_pins(report_corpus, report_corpus)
        rel_mod25 = load_module("ragraf-release25", REPO / "tools" / "release" / "release.py")
        samples = rel_mod25.sample_facts(report_corpus)
        check(samples["total"] == len(static_pin_mod.PROBES),
              "отчёт знает число проб", str(samples))
        text_no_probes = rel_mod25.report(
            report_corpus, report_corpus, by="Михаил", note="", allowed=(),
            break_reason="", gate_message="сводка", approve_message="подтверждено",
            facts={"age_seconds": None, "changed_at": "", "by": "", "op": ""},
            samples={"total": 0, "checked": 0, "changed": 0, "state": "none",
                     "degraded": 0, "not_sampled": 0})
        check("поведение не проверено" in text_no_probes,
              "без проб отчёт говорит «не проверено»", text_no_probes[:120])
        text_degraded = rel_mod25.report(
            report_corpus, report_corpus, by="Михаил", note="", allowed=(),
            break_reason="", gate_message="сводка", approve_message="подтверждено",
            facts={"age_seconds": None, "changed_at": "", "by": "", "op": ""},
            samples={"total": 4, "checked": 0, "changed": 0, "state": "none",
                     "degraded": 4, "not_sampled": 0})
        check("ни одна не сверялась" in text_degraded,
              "пробы без сверки названы отдельно", text_degraded[:160])
        text_changed = rel_mod25.report(
            report_corpus, report_corpus, by="Михаил", note="", allowed=(),
            break_reason="", gate_message="сводка", approve_message="подтверждено",
            facts={"age_seconds": None, "changed_at": "", "by": "", "op": ""},
            samples={"total": 4, "checked": 3, "changed": 1, "state": "changed",
                     "degraded": 0, "not_sampled": 0},
            samples_note="изменилось у 1 (catalog)")
        check("изменилось у 1 из 4" in text_changed
              and "с пробы прошлого выпуска" in text_changed,
              "отчёт называет расхождение и сравнение с прошлым",
              text_changed[:200])
    finally:
        report_conn.close()

finally:
    try:
        conn.close()
    except Exception:  # noqa: BLE001 — соединение могло быть закрыто
        pass
    shutil.rmtree(sandbox, ignore_errors=True)

print()
print("=" * 78)
print(f"ИТОГ: проверок {PASS}, успешно {PASS - FAIL}, провалов {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
