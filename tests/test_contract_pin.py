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
     свойство держится на сортировке, а не на формулировке.

Песочница: копия фасада и `surface.py` в %TEMP%, чтобы «изменить умолчание»
можно было делом, а не подменой словаря. Журнал — свой `control.sqlite`.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile

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


def make_corpus(base: pathlib.Path, facade_code: str) -> pathlib.Path:
    """Корпус-песочница: `facade.py` и `surface.py` в ожидаемых местах."""
    root = base / "corpus"
    (root / "src" / "ragraf").mkdir(parents=True, exist_ok=True)
    (root / "src" / "ragraf" / "facade.py").write_text(facade_code, encoding="utf-8")
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
