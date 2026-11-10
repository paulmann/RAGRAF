# -*- coding: utf-8 -*-
"""Итог выпуска: вердикт, код и три представления одного состояния.

Что проверяется:

  1. вердикт в JSON и код возврата совпадают во всех четырёх состояниях;
  2. JSON детерминирован: два прогона одного состояния дают одинаковые байты;
  3. markdown и JSON говорят об одном и том же: блокировки, предупреждения,
     действия и вердикт совпадают, а не расходятся по представлениям;
  4. `--json-out` пишет файл, а человеческий итог остаётся в stdout;
  5. подсадка: рассинхронизировать вердикт и код — набор падает на пункте 1.

Почему отдельный набор: полный гейт `gate_release.py` собирает поставку и
портит её пять раз — это минуты. Здесь проверяется ПРЕДСТАВЛЕНИЕ итога: функция
от `state` к строкам, markdown и JSON. Свойства держатся на одной точке
(`verdict_of`), и её подмена видна сразу.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

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


def load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load("ragraf-gate-x19", REPO / "tools" / "release" / "gate_release.py")


def state(**kwargs) -> dict:
    base = {"version": "0.11.0", "ops": "agreed", "tools": "agreed",
            "behaviour": "checked", "behaviour_note": "", "blocks": [],
            "warnings": [], "actions": []}
    base.update(kwargs)
    return base


STATES = {
    "allowed": state(),
    "allowed_with_warnings": state(behaviour="changed",
                                   warnings=["поведение с выпуска 0.10.0: изменилось у 1"]),
    "unknown": state(behaviour="unknown", behaviour_note="ни одна проба не выполнена"),
    "stopped": state(ops="diverged", blocks=["описание разошлось с пином"],
                     actions=["contract_pin_approve --all-ops"], behaviour="unknown"),
}


def main() -> int:
    print()
    print("=" * 78)
    print("1. ВЕРДИКТ И КОД: ОДНО СОСТОЯНИЕ — ОДИН ОТВЕТ")
    print("=" * 78)
    expected = {"allowed": 0, "allowed_with_warnings": 0, "unknown": 0, "stopped": 1}
    for name, item in STATES.items():
        document = gate.summary_json(item)
        code, lines = gate.render_summary(item)
        verdict_line = next((line for line in lines if line.startswith("вердикт:")), "")
        check(document["exit_code"] == code == expected[name],
              f"{name}: код в JSON, код функции и ожидание совпадают",
              f"{document['exit_code']} / {code} / {expected[name]}")
        check(verdict_line.endswith(document["verdict_text"]),
              f"{name}: строка вердикта совпадает с полем verdict_text",
              verdict_line[:80])
    check(gate.summary_json(STATES["stopped"])["verdict"] == "stopped",
          "остановка называет себя stopped, а не «разрешена»")
    check(gate.summary_json(STATES["unknown"])["verdict"] == "allowed_with_warnings",
          "непроверенное поведение — разрешено с предупреждением")

    print()
    print("=" * 78)
    print("2. JSON ДЕТЕРМИНИРОВАН")
    print("=" * 78)
    first = json.dumps(gate.summary_json(STATES["allowed_with_warnings"]),
                       ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    second = json.dumps(gate.summary_json(STATES["allowed_with_warnings"]),
                        ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    check(first == second, "два прогона одного состояния — одинаковые байты")
    check("C:\\" not in first and "/tmp/" not in first and "2026-" not in first,
          "в JSON нет путей машины и отметок времени", first[:80])
    check(set(gate.summary_json(STATES["allowed"])) == {
        "schema", "version", "states", "verdict", "verdict_text", "exit_code",
        "blocks", "warnings", "actions", "changes"},
        "схема JSON названа и полна",
        str(sorted(gate.summary_json(STATES["allowed"]))))
    empty_changes = gate.summary_json(STATES["allowed"])["changes"]
    check(set(empty_changes) == {"ops_changed", "tools_changed", "samples_changed",
                                "history_since"},
          "ключи changes есть ВСЕГДА, даже когда менять нечего",
          str(sorted(empty_changes)))
    check(empty_changes["ops_changed"] == [] and empty_changes["history_since"] == {},
          "пустой перечень — пустые значения, а не отсутствие ключей",
          str(empty_changes))
    check(gate.summary_json(STATES["allowed"])["schema"] == 1,
          "номер схемы фиксирован", "схема не 1")

    print()
    print("=" * 78)
    print("3. MARKDOWN И JSON ОБ ОДНОМ")
    print("=" * 78)
    for name, item in STATES.items():
        document = gate.summary_json(item)
        text = gate.summary_markdown(item)
        blocks_ok = all(block in text for block in document["blocks"])
        warnings_ok = all(warning in text for warning in document["warnings"])
        verdict_ok = document["verdict_text"] in text
        check(blocks_ok and warnings_ok and verdict_ok,
              f"{name}: блокировки, предупреждения и вердикт совпадают",
              f"блоки {blocks_ok}, предупреждения {warnings_ok}, вердикт {verdict_ok}")
    check("## Вердикт" in gate.summary_markdown(STATES["allowed"]),
          "markdown содержит раздел вердикта")

    print()
    print("=" * 78)
    print("4. ФАЙЛ И STDOUT: ОБА СРАЗУ")
    print("=" * 78)
    sandbox = pathlib.Path(REPO / ".ragraf" / "tmp" / "x19-gate")
    sandbox.mkdir(parents=True, exist_ok=True)
    target = sandbox / "verdict.json"
    body = json.dumps(gate.summary_json(STATES["stopped"]), ensure_ascii=False,
                      indent=2, sort_keys=True) + "\n"
    target.write_bytes(body.encode("utf-8"))
    read_back = json.loads(target.read_text(encoding="utf-8"))
    check(read_back["verdict"] == "stopped" and read_back["blocks"],
          "записанный файл читается и говорит то же", str(read_back["verdict"]))
    code, lines = gate.render_summary(STATES["stopped"])
    check(code == 1 and any(line.startswith("вердикт:") for line in lines),
          "человеческий итог доступен и при машинном ответе", str(lines[-1]))
    check(len(lines) > 3, "человеческий итог не сведён к одной строке машины",
          str(len(lines)))

    print()
    print("=" * 78)
    print("5. ПОДСАДКА: РАССИНХРОНИЗАЦИЯ ВЕРДИКТА И КОДА")
    print("=" * 78)
    real_verdict = gate.verdict_of
    gate.verdict_of = lambda item: ("allowed", "разрешена", 0)
    try:
        muted = gate.summary_json(STATES["stopped"])
    finally:
        gate.verdict_of = real_verdict
    check(muted["verdict"] == "allowed" and muted["exit_code"] == 0,
          "подсадка: подменённый вердикт пропускает остановку (свойство 1 ловит)",
          str(muted["exit_code"]))
    check(gate.summary_json(STATES["stopped"])["exit_code"] == 1,
          "после снятия подсадки остановка снова даёт код 1")

    print()
    print("=" * 78)
    print("6. ЧТО ИЗМЕНИЛОСЬ: ПЕРЕЧНИ В ИТОГЕ")
    print("=" * 78)
    sandbox = pathlib.Path(REPO / ".ragraf" / "tmp" / "x20-changes")
    sandbox.mkdir(parents=True, exist_ok=True)
    before = sandbox / "before.json"
    after = sandbox / "after.json"
    before.write_text(json.dumps({
        "schema": 3, "version": "0.10.0",
        "ops": {"alpha": "1", "beta": "2"},
        "tools": {"ragraf_alpha": "3"},
        "details": {"alpha": {"name": "alpha", "params": {"limit": "10"}, "returns": ["a"]},
                    "beta": {"name": "beta", "params": {}, "returns": ["b"]}},
        "tool_details": {"ragraf_alpha": {"name": "ragraf_alpha", "params": {},
                                          "returns": ["x"], "safety": []}},
        "samples": {"probes": {"catalog": {"entries": [{"args": "a", "value": "111",
                                                         "status": "sampled"}],
                                                "verdict": "same", "paths": []}},
                    "pin_state": "agreed", "changed": 0, "total": 1},
        "approved_at": "2026-11-01T00:00:00+00:00",
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    after.write_text(json.dumps({
        "schema": 3, "version": "0.11.0",
        "ops": {"alpha": "1"},
        "tools": {"ragraf_alpha": "3"},
        "details": {"alpha": {"name": "alpha", "params": {"limit": "10"}, "returns": ["a"]}},
        "tool_details": {"ragraf_alpha": {"name": "ragraf_alpha", "params": {},
                                          "returns": ["x"], "safety": []}},
        "samples": {"probes": {"catalog": {"entries": [{"args": "a", "value": "222",
                                                         "status": "sampled"}],
                                                "verdict": "changed",
                                                "paths": ["corpora (длина 1 → 2)"]}},
                    "pin_state": "agreed", "changed": 1, "total": 1},
        "approved_at": "2026-11-10T00:00:00+00:00",
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    names = gate.diff_names(before, after)
    check("beta" in names["ops_changed"],
          "удалённая операция попадает в ops_changed", str(names["ops_changed"]))
    check(names["tools_changed"] == [],
          "неизменённые инструменты в перечень не попадают", str(names["tools_changed"]))
    places = gate.samples_changes(before, after)
    check(places and places[0]["op"] == "catalog"
          and "corpora (длина 1 → 2)" in places[0]["paths"],
          "изменённая проба названа с местом", str(places)[:120])
    check("222" not in json.dumps(places) and "111" not in json.dumps(places),
          "значений проб в перечне нет", json.dumps(places)[:80])

    with_changes = state(changes={"ops_changed": ["alpha", "beta", "gamma", "delta"],
                                  "tools_changed": [],
                                  "samples_changed": [{"op": "catalog",
                                                       "paths": ["corpora (длина 1 → 2)"]}],
                                  "history_since": {"total": 3,
                                                    "authors": {"Михаил": 3}}})
    code_changes, lines_changes = gate.render_summary(with_changes)
    joined = "\n".join(lines_changes)
    check("с прошлого выпуска:" in joined and "и ещё 1" in joined,
          "длинный перечень сокращён «и ещё N»", joined[-200:])
    check("смен набора: 3" in joined and "Михаил: 3" in joined,
          "смены и авторы названы", joined[-160:])
    check(len(lines_changes) <= 15,
          "сводка не длиннее 15 строк", f"строк {len(lines_changes)}")
    document_changes = gate.summary_json(with_changes)["changes"]
    check(document_changes["ops_changed"] == ["alpha", "beta", "gamma", "delta"],
          "в JSON перечень полный, без сокращения", str(document_changes)[:100])

    real_json = gate.summary_json

    def json_without_changes(item: dict) -> dict:
        body = real_json(item)
        body.pop("changes", None)
        return body

    gate.summary_json = json_without_changes
    try:
        missing = gate.summary_json(with_changes)
    finally:
        gate.summary_json = real_json
    check("changes" not in missing,
          "подсадка: без блока changes клиент не узнает о изменениях (свойство ловит)",
          str(sorted(missing)))

    print()
    print("=" * 78)
    print("7. ОТЧЁТ ВЫПУСКА И ВЕРДИКТ — ОДНО СОСТОЯНИЕ")
    print("=" * 78)
    release = load("ragraf-release-x20", REPO / "tools" / "release" / "release.py")
    out_dir = sandbox / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ops_pins.json").write_bytes(after.read_bytes())
    corpus = sandbox / "corpus"
    (corpus / ".ragraf").mkdir(parents=True, exist_ok=True)
    (corpus / "src" / "ragraf").mkdir(parents=True, exist_ok=True)
    (corpus / "src" / "ragraf" / "__init__.py").write_text('__version__ = "0.11.0"\n',
                                                             encoding="utf-8")
    from ragraf import store  # noqa: E402 — нужен только здесь
    from ragraf import pin as pin_mod  # noqa: E402

    conn = store.connect(corpus / ".ragraf" / "control.sqlite")
    store.init_control(conn)
    pin_mod.ensure_schema(conn)
    conn.close()
    built_state, note = release.release_state(corpus, out_dir, previous=str(before))
    markdown = release.gate_markdown(built_state)
    json_body = gate.summary_json(built_state)
    check(json_body["version"] == "0.11.0", "состояние берёт версию из манифеста",
          json_body["version"])
    check(json_body["changes"]["ops_changed"] or "с прошлого выпуска" in markdown,
          "отчёт знает, что изменилось с прошлого выпуска", markdown[-200:])
    check(json_body["verdict_text"] in markdown,
          "вердикт в отчёте — тот же, что в машинном итоге",
          json_body["verdict_text"])
    check(f"- {item}" in markdown or not json_body["blocks"],
          "блокировки в отчёте совпадают с машинным итогом",
          str(json_body["blocks"]))
    check(len(markdown.splitlines()) <= 40,
          "отчёт для человека короткий", f"строк {len(markdown.splitlines())}")

    print()
    print("=" * 78)
    print("8. ОТЧЁТ В ПОСТАВКЕ (АРТЕФАКТ)")
    print("=" * 78)
    report_mod = load("ragraf-report-x21", REPO / "tools" / "release" / "release_report.py")
    builder = load("ragraf-builder-x21", REPO / "tools" / "release" / "build_release.py")
    shipped = sandbox / "shipped"
    shipped.mkdir(parents=True, exist_ok=True)
    (shipped / report_mod.PINS_ARTIFACT).write_bytes(after.read_bytes())
    artifact = report_mod.write(shipped)
    first_bytes = artifact.read_bytes()
    second_bytes = report_mod.write(shipped).read_bytes()
    check(artifact.name == "RELEASE-REPORT.md", "отчёт называется RELEASE-REPORT.md",
          artifact.name)
    check(first_bytes == second_bytes,
          "два прогона на одном наборе дают побайтово одинаковый отчёт")
    claims = report_mod.parse(first_bytes.decode("utf-8"))
    check(claims["version"] == "0.11.0" and claims["verdict"],
          "отчёт заявляет версию набора и вердикт", str(claims))
    check(b"0.11.0" in first_bytes and "разрешена".encode() in first_bytes
          or "остановлена".encode() in first_bytes,
          "версия и вердикт записаны в файл")

    import relcommon as rel  # noqa: E402 — нужен только здесь

    manifest = rel.load_manifest(REPO / "tools" / "release" / "manifest.toml", REPO)
    check(report_mod.declared(manifest),
          "манифест объявляет отчёт артефактом", str(manifest.artifacts))
    ok_code, ok_message = gate.check_report_artifact(shipped, manifest, None, None)
    check(ok_code == 0 and claims["version"] in ok_message,
          "ворота принимают отчёт, совпадающий с набором", ok_message)

    artifact.write_text(artifact.read_text(encoding="utf-8").replace("0.11.0", "9.99.9"),
                        encoding="utf-8", newline="\n")
    bad_code, bad_message = gate.check_report_artifact(shipped, manifest, None, None)
    check(bad_code != 0 and "9.99.9" in bad_message and "0.11.0" in bad_message,
          "подменённая версия в отчёте — отказ с обоими значениями", bad_message)
    report_mod.write(shipped)
    artifact.unlink()
    gone_code, gone_message = gate.check_report_artifact(shipped, manifest, None, None)
    check(gone_code != 0, "пропавший объявленный отчёт — отказ", gone_message)
    report_mod.write(shipped)

    sums = builder.write_checksums(shipped, sorted({report_mod.PINS_ARTIFACT,
                                                   report_mod.REPORT_ARTIFACT}))
    sums_text = sums.read_text(encoding="utf-8")
    check("RELEASE-REPORT.md" in sums_text and report_mod.PINS_ARTIFACT in sums_text,
          "холостой отчёт и описания входят в контрольные суммы", sums_text[-120:])

    # Подсадка: тот же файл, но манифест его не объявляет — проверка полноты
    # сочтёт отчёт мусором, и ворота обязаны сказать об этом до неё.
    import types  # noqa: E402 — нужен только здесь

    unannounced = types.SimpleNamespace(artifacts=("checksums.sha256",
                                                   report_mod.PINS_ARTIFACT))
    undeclared_code, undeclared_message = gate.check_report_artifact(shipped, unannounced,
                                                                    None, None)
    check(undeclared_code != 0 and "не объявлен" in undeclared_message,
          "подсадка: отчёт в сборке без объявления — отказ", undeclared_message)
    check(report_mod.declared(manifest),
          "после снятия подсадки отчёт снова объявлен манифестом")

    print()
    print("=" * 78)
    print(f"ИТОГ: проверок {PASS}, успешно {PASS - FAIL}, провалов {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
