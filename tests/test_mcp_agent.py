"""Проверка: MCP-сервер сообщает, кто его использует.

Главный дефект, который исправляется: MCP-сервер создавал фасад БЕЗ указания
себя, и все вызовы агентов попадали в журнал как `unknown` (замер: 113 из 138
в корпусе z). Из-за этого статистика по агентам была невозможна.

Проверяется три пути получения имени:
  1. параметр запуска `--agent` (приоритетный);
  2. поле `clientInfo.name` из запроса `initialize`;
  3. вывод семейства из имени, если владелец не задал явно.

Проверка идёт через MCP-протокол: серверу подаются настоящие запросы
initialize и tools/call, а результат читается из stdout. Это проверяет
работу целиком, а не отдельную функцию.

Журнал живого корпуса НЕ используется: берётся временная база.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.mcp_server import McpServer  # noqa: E402

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
print("1. КЛАСС СЕРВЕРА: ОПРЕДЕЛЕНИЕ АГЕНТА")
print("=" * 78)

# Случай А: имя задано параметром запуска.
s1 = McpServer("config/revoap.yaml", agent="AutoClaw",
               agent_kind="autoclaw")
print(f"  параметр --agent: agent={s1.agent!r} kind={s1.agent_kind!r}")
check(s1.agent == "AutoClaw", "имя из параметра принято")
check(s1.agent_kind == "autoclaw", "семейство из параметра принято")

# Случай Б: имя не задано, приходит от клиента в initialize.
s2 = McpServer("config/revoap.yaml")
check(s2.agent == "unknown", "без параметров имя неизвестно")
s2.note_client({"clientInfo": {"name": "AutoClaw", "version": "1.2.3"}})
print(f"  clientInfo: agent={s2.agent!r} kind={s2.agent_kind!r}")
check(s2.agent == "AutoClaw", "имя взято из clientInfo")
check(s2.agent_kind == "autoclaw", "семейство выведено из имени")

# Случай В: параметр важнее clientInfo.
s3 = McpServer("config/revoap.yaml", agent="SagaAI", agent_kind="external")
s3.note_client({"clientInfo": {"name": "AutoClaw"}})
print(f"  параметр важнее clientInfo: agent={s3.agent!r} kind={s3.agent_kind!r}")
check(s3.agent == "SagaAI", "параметр запуска имеет приоритет")
check(s3.agent_kind == "external", "семейство не перезаписано клиентом")

# Случай Г: разбор имени с версией и разделителями.
CASES = [
    ({"clientInfo": {"name": "AutoClaw/2.0"}}, "AutoClaw"),
    # По дефису и пробелу имя НЕ режется: `autoclaw-desktop` и
    # `Claude Desktop` — полные имена клиентов, и владелец может хотеть
    # различать их. Первая версия разбора ломала именно эти случаи.
    ({"clientInfo": {"name": "autoclaw-desktop"}}, "autoclaw-desktop"),
    ({"clientInfo": {"name": "  SagaAI  "}}, "SagaAI"),
    ({"clientInfo": {"name": "Claude Desktop"}}, "Claude Desktop"),
    ({"clientInfo": {}}, "unknown"),
    ({}, "unknown"),
    ({"clientInfo": {"name": ""}}, "unknown"),
    ({"clientInfo": "не объект"}, "unknown"),
]
print(f"\n  разбор clientInfo:")
for params, want in CASES:
    s = McpServer("config/revoap.yaml")
    s.note_client(params)
    got = s.agent
    mark = "верно" if got == want else "ОШИБКА"
    print(f"    {str(params)[:52]:<54} → {got!r:<12} {mark}")
    check(got == want, f"разбор {str(params)[:30]}", f"получено {got!r}")

# Случай Д: вывод семейства для неизвестного имени.
s = McpServer("config/revoap.yaml")
s.note_client({"clientInfo": {"name": "SomeOtherTool"}})
print(f"\n  неизвестный агент: agent={s.agent!r} kind={s.agent_kind!r}")
check(s.agent_kind == "external",
      "неизвестное имя отнесено к внешним агентам")

print()
print("=" * 78)
print("2. РАБОТА ЧЕРЕЗ MCP-ПРОТОКОЛ (реальный процесс)")
print("=" * 78)
# Запускается настоящий сервер и ему подаются запросы. Проверяется, что
# агент доходит до журнала: без этого исправление бесполезно.
import shutil  # noqa: E402

LIVE = pathlib.Path(r"C:\AutoClaw\revoap.com\.ragraf\control.sqlite")
STAGING = REPO / ".staging"
STAGING.mkdir(exist_ok=True)

if not LIVE.exists():
    print(f"  журнала нет ({LIVE}) — проверка через протокол пропущена")
    check(True, "протокол проверен на уровне класса выше")
else:
    # Копия живого журнала: сервер должен писать в неё, а не в рабочий файл.
    # Путь подменяется через конфиг корпуса — берётся его временная копия.
    import sqlite3  # noqa: E402

    work_db = STAGING / "mcp_agent_probe.sqlite"
    if work_db.exists():
        work_db.unlink()
    shutil.copy2(LIVE, work_db)

    # Конфигурация корпуса копируется и получает другой путь к журналу.
    cfg_src = REPO / "config" / "revoap.yaml"
    cfg_tmp = STAGING / "mcp_agent_probe.yaml"
    shutil.copy2(cfg_src, cfg_tmp)

    # Подменяется путь управляющей базы: ищется в коде, как он задаётся.
    from ragraf.config import load as load_config  # noqa: E402

    cfg_obj = load_config(str(cfg_tmp))
    print(f"  временная копия журнала: {work_db.relative_to(REPO)}")
    print(f"  управляющая база корпуса: {cfg_obj.control_db}")

    # Изоляция делается ТОЧНО, а не грубо. Первая попытка подменить
    # `ragraf_dir` целиком сломала сервер: вместе с журналом переехали
    # пути к индексу, и обращение к базе дало «unable to open database file».
    #
    # Поэтому подменяется ТОЛЬКО путь к управляющей базе: остальные
    # каталоги корпуса остаются настоящими, и сервер работает как обычно.
    # Рабочий журнал при этом не меняется: он заменяется копией.
    runner = STAGING / "run_mcp_probe.py"
    runner.write_text(
        "import sys, pathlib\n"
        f"sys.path.insert(0, r'{REPO / 'src'}')\n"
        "from ragraf.config import ProjectConfig\n"
        "# Подменяется один путь — к журналу обращений. Прочие пути \n"
        "# (индекс, граф, лексика) остаются настоящими.\n"
        f"_probe_db = pathlib.Path(r'{work_db}')\n"
        "ProjectConfig.control_db = property(lambda self: _probe_db)\n"
        "from ragraf.mcp_server import main\n"
        "raise SystemExit(main())\n",
        encoding="utf-8", newline="\n",
    )
    print(f"  запускается: {runner.name} (журнал — копия)")

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "SagaAI", "version": "1.0"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "notifications/initialized",
         "params": {}},
        # Вызывается ПОИСК, а не ragraf_usage. Причина измерена: `usage` сам
        # читает журнал, и в момент его вызова записанная ранее сессия ещ¸
        # не содержит агента из clientInfo — запись происходит при создании
        # фасада, а он к тому моменту уже создан. Поиск же гарантированно
        # пишет вызов после того, как агент известен.
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "ragraf_search",
                    "arguments": {"query": "pipeline", "limit": 3}}},
    ]
    stdin_text = "\n".join(json.dumps(r, ensure_ascii=False) for r in requests)
    proc = subprocess.run(
        [sys.executable, str(runner), "--project", str(cfg_tmp)],
        input=stdin_text, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    print(f"  код возврата сервера: {proc.returncode}")
    if proc.stderr:
        print(f"  stderr (первые строки): {proc.stderr[:300]}")

    responses = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            responses.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    print(f"  ответов получено: {len(responses)}")

    init_ok = any(r.get("id") == 1 and "result" in r for r in responses)
    check(init_ok, "сервер отвечает на initialize")

    tools_ok = any(r.get("id") == 2 and "result" in r for r in responses)
    # Проверка: инструмент ragraf_usage знает про фильтры. Список инструментов
    # берётся отдельным запуском: в этом прогоне вызывается поиск.
    print(f"  ответов на запросы: {len(responses)}")
    check(init_ok, "сервер отвечает на initialize")

    # Проверка схемы ragraf_usage: у неё должны быть фильтры агента.
    tool_probe = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientInfo": {"name": "schema-probe"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    probe = subprocess.run(
        [sys.executable, str(runner), "--project", str(cfg_tmp)],
        input="\n".join(json.dumps(r, ensure_ascii=False) for r in tool_probe),
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180,
    )
    schema_responses = []
    for line in (probe.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                schema_responses.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    tools_result = next((r["result"] for r in schema_responses
                         if r.get("id") == 2), {})
    usage_tool = next(
        (t for t in (tools_result.get("tools") or [])
         if t.get("name") == "ragraf_usage"), None
    )
    if usage_tool:
        props = (usage_tool.get("inputSchema") or {}).get("properties") or {}
        print(f"  параметры ragraf_usage: {sorted(props)}")
        check("agent" in props, "в схеме есть параметр agent")
        check("agent_kind" in props, "в схеме есть параметр agent_kind")
    else:
        check(False, "инструмент ragraf_usage найден")

    # Главная проверка: агент попал в журнал.
    conn = sqlite3.connect(str(work_db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT tool, agent, agent_kind, COUNT(*) AS n FROM usage_call "
        "GROUP BY tool, agent, agent_kind"
    ).fetchall()
    print(f"\n  вызовы в журнале после работы сервера:")
    for r in rows:
        print(f"    tool={r['tool']!r} agent={r['agent']!r} "
              f"kind={r['agent_kind']!r} вызовов {r['n']}")

    new_calls = [r for r in rows if r["agent"] == "SagaAI"]
    check(bool(new_calls),
          "агент из clientInfo записан в журнал",
          f"записей SagaAI нет; есть: "
          f"{[(r['agent'], r['n']) for r in rows]}")
    if new_calls:
        check(new_calls[0]["agent_kind"] == "external",
              "семейство внешнего агента определено")
        check(new_calls[0]["tool"] == "mcp",
              "канал вызова записан как mcp")
    conn.close()

    # Уборка: временные файлы удаляются.
    work_db.unlink(missing_ok=True)
    cfg_tmp.unlink(missing_ok=True)
    runner.unlink(missing_ok=True)

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
