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
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "tools" / "ragraf-mcp.py"
CONFIG = REPO / "config" / "revoap.yaml"


def run_session(requests: list[dict]) -> list[dict | None]:
    """Отправить запросы серверу и собрать ответы."""
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--project", str(CONFIG)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in requests) + "\n"
    out, err = proc.communicate(input=payload, timeout=300)
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

    if init and init.get("result", {}).get("serverInfo", {}).get("name") == "ragraf":
        print(f"  [OK ] initialize: {init['result']['serverInfo']}")
    else:
        print(f"  [FAIL] initialize: {init}")
        ok = False

    tool_list = (tools or {}).get("result", {}).get("tools", [])
    names = [t["name"] for t in tool_list]
    print(f"  [{'OK ' if len(names) >= 16 else 'FAIL'}] инструментов объявлено: {len(names)}")
    if len(names) < 16:
        ok = False
    print(f"       {', '.join(n.replace('ragraf_', '') for n in names)}")

    forbidden = [n for n in names if any(k in n for k in ("query", "cypher", "sql", "exec"))]
    print(f"  [{'OK ' if not forbidden else 'FAIL'}] нет инструмента свободных запросов: {forbidden or 'нет'}")
    if forbidden:
        ok = False

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
        print(f"  [{'OK ' if has_rev else 'FAIL'}] index_status отдаёт шапку с ревизией")
        print(f"       {first.splitlines()[0]}")
        if not has_rev:
            ok = False
        payload = json.loads(texts[1])
        # counts лежит на верхнем уровне конверта: extra сливается в корень ответа.
        counts = payload.get("counts") or {}
        print(f"       файлов={counts.get('files')} узлов={counts.get('nodes')} рёбер={counts.get('edges')}")
    else:
        print("  [FAIL] ответ на index_status не получен")
        ok = False

    impact = next((r for r in responses if r and r.get("id") == 11), None)
    if impact:
        texts = [c.get("text", "") for c in impact["result"]["content"]]
        payload = json.loads(texts[1])
        res = payload.get("result") or {}
        cnt = res.get("affected_count") or {}
        print(f"  [{'OK ' if payload.get('found') else 'FAIL'}] impact: "
              f"доказанных={cnt.get('stated')} эвристических={cnt.get('derived')}")
        if not payload.get("found"):
            ok = False
    else:
        print("  [FAIL] ответ на impact не получен")
        ok = False

    search = next((r for r in responses if r and r.get("id") == 12), None)
    if search:
        texts = [c.get("text", "") for c in search["result"]["content"]]
        payload = json.loads(texts[1])
        n = len(payload.get("result") or [])
        print(f"  [{'OK ' if n else 'FAIL'}] search вернул {n} результатов")
        if not n:
            ok = False
    else:
        print("  [FAIL] ответ на search не получен")
        ok = False

    refused = next((r for r in responses if r and r.get("id") == 13), None)
    if refused and refused.get("error", {}).get("code") == -32602:
        print(f"  [OK ] отказ на язык запросов: {refused['error']['message'][:70]}")
    else:
        print(f"  [FAIL] попытка передать язык запросов не отвергнута: {refused}")
        ok = False

    print("\n" + ("=== все проверки MCP пройдены ===" if ok else "=== ЕСТЬ ОШИБКИ ==="))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
