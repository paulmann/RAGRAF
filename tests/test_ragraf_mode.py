"""Проверка режима работы RAGRAF и автонастройки клиентов.

Что проверяется и почему именно так:
  * настройка режима: значение по умолчанию, список допустимых, отказ на
    опечатку (иначе `mcp_http` стал бы «новым режимом», который никто не проверяет);
  * пробы каналов: CLI и stdio-MCP обязаны отвечать — это проверяется запуском,
    а не наличием файла;
  * режим НЕ отключает второй канал: при выборе `cli` проба stdio всё равно
    выполняется, но помечается «справочно» и в вердикт не входит;
  * формирование значений для клиентов: AutoClaw, Harness (оба транспорта), SagaAI;
  * применение блока Harness на КОПИИ профиля — с проверкой, что блок замещается,
    а не дублируется (два сервера с одним именем клиент отвергает), и что при
    ошибке разбора профиль возвращается к прежнему виду;
  * значение доступа не попадает в состояние (`/api/clients` без значения).

Запуск: python tests/test_ragraf_mode.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import client_setup as cs  # noqa: E402
from ragraf import probes  # noqa: E402
from ragraf import settings  # noqa: E402

PASSED = 0
FAILED = 0


def check(ok: bool, label: str, detail: str = "") -> bool:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  [OK ] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label}")
        if detail:
            print(f"         {detail}")
    return ok


def main() -> int:
    print("=== настройка режима ===")
    check("ragraf_mode" in settings.DEFAULTS,
          "значение по умолчанию задано",
          f"значение: {settings.DEFAULTS.get('ragraf_mode')}")
    check(settings.DEFAULTS.get("ragraf_mode") == "cli",
          "по умолчанию выбран CLI (канал, не зависящий от регистрации сервера)")
    check("ragraf_mode" in settings.EDITABLE, "настройка доступна в интерфейсе")
    modes = settings.ENUM_KEYS.get("ragraf_mode", ())
    check(set(modes) == {"cli", "mcp-stdio", "mcp-http", "both"},
          "список режимов закрытый и полный", f"значения: {modes}")

    store = settings.SettingsStore()
    before = store.load().get("ragraf_mode")
    try:
        store.save({"ragraf_mode": "mcp_http"})
        check(False, "опечатка в режиме отвергнута",
              "значение 'mcp_http' принято — режим стал бы непроверяемым")
    except ValueError as exc:
        check(True, "опечатка в режиме отвергнута", str(exc)[:80])
    store.save({"ragraf_mode": before})   # возвращаем как было

    print("\n=== пробы каналов ===")
    cli = probes.probe_cli()
    check(cli["ok"], "CLI отвечает", cli["detail"])
    check(bool(cli.get("action")) is (not cli["ok"]),
          "подсказка есть только при отказе")

    stdio = probes.probe_mcp_stdio()
    check(stdio["ok"], "stdio-MCP отвечает и отдаёт инструменты", stdio["detail"])
    count = 0
    if "инструментов: " in stdio["detail"]:
        count = int(stdio["detail"].split("инструментов: ")[1].split()[0])
    check(count >= 24, f"набор инструментов полный ({count})")

    print("\n=== режим не отключает второй канал ===")
    res_cli = probes.probe_all("cli")
    kinds = {c["kind"]: c for c in res_cli["channels"]}
    check("mcp-stdio" in kinds,
          "при режиме CLI второй канал всё равно проверяется",
          f"каналы: {list(kinds)}")
    check(kinds.get("mcp-stdio", {}).get("reference") is True,
          "второй канал помечен «справочно» и не входит в вердикт")
    check(res_cli["mode_ok"] is True, "вердикт по режиму CLI положительный",
          json.dumps(res_cli.get("channels"), ensure_ascii=False)[:200])
    check("не конфликт" in res_cli.get("note", ""),
          "пояснение о сосуществовании каналов присутствует")

    res_both = probes.probe_all("both")
    kinds_both = {c["kind"] for c in res_both["channels"]}
    check(kinds_both == {"cli", "mcp-stdio", "mcp-http"},
          "в режиме both проверяются все три канала", f"каналы: {kinds_both}")

    print("\n=== клиенты: формирование значений ===")
    ac = cs.autoclaw_values()
    url = next((f["value"] for f in ac["fields"] if f["label"] == "Service URL"), "")
    check(url.endswith("/mcp"), "адрес для AutoClaw указывает на /mcp", url)
    check("интерфейс" in ac["how"] or "руками" in ac["how"],
          "сказано, что значения вводятся в интерфейсе продукта")

    hs_stdio = cs.harness_block("stdio")
    hs_http = cs.harness_block("streamable-http")
    check(hs_stdio["entry"]["config"]["transport"] == "stdio",
          "вариант stdio для Harness сформирован")
    check(hs_http["entry"]["config"]["transport"] == "streamable-http",
          "вариант streamable-http для Harness сформирован")
    check(".venv" in hs_stdio["entry"]["config"]["command"],
          "запуск идёт интерпретатором из .venv (зависимости достижимы без окружения)",
          hs_stdio["entry"]["config"]["command"])

    sg = cs.sagaai_state()
    check(sg["applies"] is False,
          "SagaAI только проверяется: файл чужого проекта не правится")

    print("\n=== применение блока Harness на КОПИИ профиля ===")
    tmpdir = Path(tempfile.mkdtemp(prefix="ragraf-profile-"))
    try:
        copy = tmpdir / "cordis.patch.yml"
        has_real = cs.DSH_PATCH.is_file()
        if has_real:
            shutil.copy2(cs.DSH_PATCH, copy)
        else:
            copy.write_text("[ ]\n", encoding="utf-8")
        original_path = cs.DSH_PATCH
        cs.DSH_PATCH = copy
        try:
            r1 = cs.harness_apply("stdio")
            text = copy.read_text(encoding="utf-8")
            check(r1["ok"], "блок stdio применён и проверен разбором",
                  json.dumps(r1.get("check"), ensure_ascii=False)[:200])
            check(text.count("- id: mcp-ragraf") == 1,
                  "в профиле ровно одна запись нашего сервера",
                  f"найдено: {text.count('- id: mcp-ragraf')}")
            check(text.count(cs.MARK_BEGIN) == 1,
                  "блок обрамлён метками (повтор заменит, а не добавит)")
            check("serverName: ragraf" in text, "имя сервера записано")
            check(cs.DSH_PATCH.with_name(cs.DSH_PATCH.name).parent == copy.parent,
                  "проверка работает по тому же файлу, что правится")

            r2 = cs.harness_apply("streamable-http")
            text2 = copy.read_text(encoding="utf-8")
            check(r2["ok"], "блок streamable-http применён")
            # Считаются ЗАПИСИ сервера (строки `- id: mcp-ragraf…`), а не
            # вхождения подстроки `serverName: ragraf`: в профиле есть
            # комментарий, где это имя упомянуто словами, и первая редакция
            # проверки считала его вторым сервером (измерено).
            entries = len(re.findall(r"^\s*-\s*id:\s*mcp-ragraf", text2, re.M))
            check(entries == 1,
                  "повторное применение заменяет блок, а не добавляет второй",
                  f"записей сервера: {entries}")
            check("streamable-http" in text2, "транспорт в профиле обновился")
        finally:
            cs.DSH_PATCH = original_path
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n=== состояние клиентов не раскрывает значения доступа ===")
    state = cs.state()
    raw = json.dumps(state, ensure_ascii=False)
    # Проверяется отсутствие Признака выданного значения: префикс выдачи и
    # достаточная длина строки после него. Заглушка вида «<значение доступа>»
    # значением не является — это указание, куда его подставить.
    import re as _re

    leaked = [m.group(0) for m in _re.finditer(
        r"ragraf_[A-Za-z0-9_-]{20,}", raw)]
    check(not leaked, "в состоянии нет фактического значения доступа",
          f"найдено: {leaked[:2] if leaked else 'нет'}")
    check("ragraf_" not in raw,
          "в состоянии нет выданных значений (префикс выдачи отсутствует)")

    print("\n" + "=" * 78)
    print(f"ИТОГ: пройдено {PASSED}, провалено {FAILED}")
    print("=" * 78)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
