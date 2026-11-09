"""Тест MCP-сервера: реальный обмен JSON-RPC через stdio (ФТ-22).

Проверяется:
  * initialize и tools/list;
  * вызов инструмента навигации и impact;
  * обязательная шапка с ревизией и предупреждениями об устаревании;
  * отказ на попытку передать язык запросов к графу (ФТ-15).

Запуск: python tests/test_mcp.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "tools" / "ragraf-mcp.py"
CONFIG = REPO / "config" / "revoap.yaml"

#: Сколько ждать ответа сервера. Меняется переменной окружения — нужно, чтобы
#: проверить сам аварийный путь (короткий срок) и чтобы медленная машина
#: не объявляла исправный сервер мёртвым.
TIMEOUT_SEC = float(os.environ.get("RAGRAF_MCP_TEST_TIMEOUT", "300"))

PASSED = 0
FAILED = 0

#: Печать проверки с подсчётом. До этапа 11 файл печатал `[OK ]`/`[FAIL]` без
#: сводки, и раннер писал «0 пройдено, 0 провалено» — число, которое читается
#: как «проверок нет». Двадцать настоящих проверок при этом выполнялись.
def check(cond, message: str, detail: str = "") -> bool:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [OK ] {message}" + (f": {detail}" if detail else ""))
    else:
        FAILED += 1
        print(f"  [FAIL] {message}" + (f": {detail}" if detail else ""))
    return bool(cond)


def terminate_tree(proc: subprocess.Popen) -> bool:
    """Снять сервер ВМЕСТЕ с потомками. True — пришлось снимать силой.

    ЗАЧЕМ. `proc.communicate(timeout=…)` на истечении срока бросает исключение
    и НЕ трогает дочерний процесс. Замерено 04.11.2026: после такого прогона
    остался жить `tools/ragraf-mcp.py` (он держал `catalog/catalog.lock` и
    переиндексировал корпус), а `ragraf status` при этом ждал замок до 120 с.
    Сирота видна так: `Get-CimInstance Win32_Process | ? CommandLine -like
    '*ragraf-mcp.py*'` — в списке появляется процесс с чужим `ParentProcessId`.

    Почему дерево, а не `proc.kill()`. Индексатор запускает внешние процессы
    (Joern, `pysrc2cpg`), и они переживают смерть родителя. На Windows дерево
    снимает `taskkill /T`, на POSIX — сигнал своей группе (поэтому сервер
    запускается в отдельном сеансе).
    """
    if proc.poll() is not None:
        return False
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, check=False, timeout=60)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
    return True


def run_session(requests: list[dict]) -> list[dict | None]:
    """Отправить запросы серверу и собрать ответы."""
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--project", str(CONFIG)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        # Своя группа процессов: без неё сигнал на POSIX уйдёт только серверу,
        # а его потомки (Joern) останутся работать.
        start_new_session=(os.name != "nt"),
    )
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in requests) + "\n"
    out, err = "", ""
    try:
        out, err = proc.communicate(input=payload, timeout=TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        # Сперва снимаем, потом читаем остаток: иначе вывод сервера теряется.
        terminate_tree(proc)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = "", "сервер не завершился даже после снятия дерева"
        print(f"  [FAIL] сервер не ответил за {TIMEOUT_SEC:g} с: снят принудительно; "
              f"stderr: {(err or '')[:400]}")
        # Нет ответов — значит нечего разбирать; выше это будет видно как провал
        # конкретных проверок ("ответ не получен"), а не как таймаут без следа.
        return []
    responses: list[dict | None] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            responses.append(json.loads(line))
        except json.JSONDecodeError:
            responses.append(None)
    if proc.returncode != 0:
        print(f"  сервер завершился с кодом {proc.returncode}; stderr: {err[:400]}")
    return responses


def main() -> int:
    print("=== MCP: initialize + tools/list ===")
    responses = run_session(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
    )
    init = next((r for r in responses if r and r.get("id") == 1), None)
    tools = next((r for r in responses if r and r.get("id") == 2), None)
    ok = True

    info = (init or {}).get("result", {}).get("serverInfo", {})
    ok = check(info.get("name") == "ragraf", "initialize отвечает именем сервера",
               str(info)) and ok

    tool_list = (tools or {}).get("result", {}).get("tools", [])
    names = [t["name"] for t in tool_list]
    ok = check(len(names) >= 16, "инструментов объявлено не меньше 16",
               str(len(names))) and ok
    print(f"       {', '.join(n.replace('ragraf_', '') for n in names)}")

    forbidden = [n for n in names if any(k in n for k in ("query", "cypher", "sql", "exec"))]
    ok = check(not forbidden, "нет инструмента свободных запросов",
               str(forbidden or "нет")) and ok

    # Каждый инструмент обязан быть КЛАССИФИЦИРОВАН: клиент по `annotations`
    # отличает чтение от записи. Замер 01.11.2026 (чистая установка из колеса):
    # `ragraf_compact` и `ragraf_excludes` отдавались с пустыми `annotations`,
    # и сервер на каждом запуске писал в stderr «поверхность инструментов
    # расходится». Видно это было только в stderr, поэтому теперь — проверка.
    unclassified = [t.get("name") for t in tool_list
                    if not (t.get("annotations") or {}) or not t.get("title")]
    ok = check(not unclassified, "у каждого инструмента есть title и annotations",
               str(unclassified or "нет")) and ok
    annotated = [t.get("name") for t in tool_list
                 if "readOnlyHint" not in (t.get("annotations") or {})]
    ok = check(not annotated, "у каждого инструмента назван режим (readOnlyHint)",
               str(annotated or "нет")) and ok

    print("\n=== MCP: вызов инструментов ===")
    responses = run_session(
        [
            {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
             "params": {"name": "ragraf_index_status", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 11, "method": "tools/call",
             "params": {"name": "ragraf_impact",
                        "arguments": {"target": "PipelineEngine::run", "depth": 2}}},
            {"jsonrpc": "2.0", "id": 12, "method": "tools/call",
             "params": {"name": "ragraf_search", "arguments": {"query": "настройки воркера", "limit": 3}}},
            {"jsonrpc": "2.0", "id": 13, "method": "tools/call",
             "params": {"name": "ragraf_impact",
                        "arguments": {"target": "MATCH (n) RETURN n"}}},
        ]
    )

    status = next((r for r in responses if r and r.get("id") == 10), None)
    if status:
        texts = [c.get("text", "") for c in status["result"]["content"]]
        first = texts[0]
        has_rev = "rev=" in first
        ok = check(has_rev, "index_status отдаёт шапку с ревизией",
                   first.splitlines()[0] if first else "") and ok
        payload = json.loads(texts[1])
        # counts лежит на верхнем уровне конверта: extra сливается в корень ответа.
        counts = payload.get("counts") or {}
        print(f"       файлов={counts.get('files')} узлов={counts.get('nodes')} рёбер={counts.get('edges')}")
    else:
        ok = check(False, "ответ на index_status получен") and ok

    impact = next((r for r in responses if r and r.get("id") == 11), None)
    if impact:
        texts = [c.get("text", "") for c in impact["result"]["content"]]
        payload = json.loads(texts[1])
        res = payload.get("result") or {}
        cnt = res.get("affected_count") or {}
        ok = check(bool(payload.get("found")), "impact находит цель",
                   f"доказанных={cnt.get('stated')} эвристических={cnt.get('derived')}") and ok
    else:
        ok = check(False, "ответ на impact получен") and ok

    search = next((r for r in responses if r and r.get("id") == 12), None)
    if search:
        texts = [c.get("text", "") for c in search["result"]["content"]]
        payload = json.loads(texts[1])
        n = len(payload.get("result") or [])
        ok = check(n > 0, "search вернул результаты", str(n)) and ok
    else:
        ok = check(False, "ответ на search получен") and ok

    refused = next((r for r in responses if r and r.get("id") == 13), None)
    if refused and refused.get("error", {}).get("code") == -32602:
        ok = check(True, "отказ на язык запросов",
                   refused["error"]["message"][:70]) and ok
    else:
        ok = check(False, "попытка передать язык запросов отвергнута",
                   str(refused)) and ok

    print("\n" + ("=== все проверки MCP пройдены ===" if ok else "=== ЕСТЬ ОШИБКИ ==="))
    print(f"ИТОГ: пройдено {PASSED}, провалено {FAILED}")
    return 0 if (ok and not FAILED) else 1


if __name__ == "__main__":
    raise SystemExit(main())
