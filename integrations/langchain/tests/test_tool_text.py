# -*- coding: utf-8 -*-
"""Проверки форматирования ответов инструментов: доказательство против догадки.

Главное свойство, которое здесь защищается: инструмент обязан отличать
ДОКАЗАННУЮ связь (`stated`) от ВЫВЕДЕННОЙ (`derived`). В этом дереве выведенных
связей 75.7 % (`index_status`, rev-1359), то есть если метка пропадёт, агент будет
цитировать догадку как факт — самый дорогой класс ошибки в RAGRAF.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragraf_langchain.tools import (  # noqa: E402
    callers_text,
    format_mcp_output,
    freshness_text,
    impact_text,
    read_text,
    search_text,
    symbol_text,
    write_check_text,
)

SEARCH = {
    "op": "search", "index_rev": "rev-1355", "provenance": "index", "found": True,
    "result": [{"path": "src/ragraf/corpus.py", "start_line": 75, "end_line": 86,
                "score": 16.39, "source_kind": "code", "why": "совпало по: лексика",
                "snippet": "def corpus_hash(self) -> str:"}],
}


def test_search_reports_address_score_and_snippet() -> None:
    text = search_text(SEARCH)
    assert "src/ragraf/corpus.py:75-86" in text
    assert "score=16.39" in text
    assert "def corpus_hash" in text
    assert "rev=rev-1355" in text


def test_empty_search_says_not_found_and_is_not_an_error() -> None:
    text = search_text({"index_rev": "rev-1", "provenance": "index", "result": []})
    assert "ничего не найдено" in text
    assert "не ошибка" in text


def test_symbol_without_matches_names_the_fact() -> None:
    text = symbol_text({"index_rev": "rev-1", "result": []})
    assert "не найден" in text


def test_callers_marks_derived_as_not_proven() -> None:
    envelope = {"index_rev": "rev-1", "result": {
        "target": "dedupe", "stated": 1, "derived": 1,
        "items": [
            {"path": "src/ragraf/dedupe.py", "start_line": 322, "fqn": "a:apply",
             "provenance": "stated", "confidence": 1.0, "evidence": "src/x.py:10"},
            {"path": "src/ragraf/watch.py", "start_line": 200, "fqn": "b:check",
             "provenance": "derived", "confidence": 0.4, "evidence": None},
        ]}}
    text = callers_text(envelope)
    assert "доказано 1, выведено 1" in text
    assert "ВЫВЕДЕНО" in text
    assert "src/ragraf/watch.py:200" in text


def test_callers_empty_is_not_proof_of_isolation() -> None:
    text = callers_text({"index_rev": "rev-1", "result": {"target": "x", "items": []}})
    assert "не доказательство изолированности" in text


def test_impact_names_heuristic_share() -> None:
    envelope = {"index_rev": "rev-1", "result": {
        "target": "corpus_hash", "affected_count": 2, "stated": 1, "derived": 1,
        "heuristic_share": 0.5,
        "affected_files": [{"path": "src/ragraf/indexer.py", "start_line": 812,
                            "fqn": "indexer:run"}]}}
    text = impact_text(envelope)
    assert "затрагивает файлов 2" in text
    assert "доля эвристики 0.5" in text
    assert "src/ragraf/indexer.py:812" in text


def test_read_warns_when_file_changed_after_index() -> None:
    text = read_text({"result": {"path": "src/a.py", "requested": "1-2", "total_lines": 9,
                                 "text": "x = 1", "changed_since_index": True}})
    assert "изменился после индексации" in text


def test_write_check_reports_problems() -> None:
    blocked = write_check_text({"problems": ["колонки отсутствуют: email"],
                                "result": {"table": "users", "column_count": 4,
                                           "operation": "insert"}})
    assert "НЕ пройдёт" in blocked and "email" in blocked
    ok = write_check_text({"result": {"table": "users", "column_count": 4,
                                      "operation": "insert"}})
    assert "согласован" in ok


def test_freshness_calls_stale_stale() -> None:
    text = freshness_text({"result": {"changed_since_index": True, "rev_id": "rev-9",
                                      "corpus_hash_now": "a" * 64,
                                      "corpus_hash_indexed": "b" * 64}})
    assert "УСТАРЕЛ" in text and "rev=rev-9" in text
    fresh = freshness_text({"result": {"changed_since_index": False, "rev_id": "rev-9",
                                       "corpus_hash_now": "a", "corpus_hash_indexed": "a"}})
    assert "свеж" in fresh and "УСТАРЕЛ" not in fresh


def test_mcp_blocks_are_rendered_with_addresses() -> None:
    """Сервер отдаёт сводку и JSON — адрес должен появиться из JSON-блока.

    Именно на этом падала проверка MCP-пути (03.11.2026): в сводке хитов нет, а в
    JSON `path` и `start_line` лежат отдельными полями, то есть строки
    «путь:строка» нет ни в одном блоке.
    """
    blocks = [
        {"type": "text", "text": "op=search rev=rev-1 found=True\nОГРАНИЧЕНИЕ: …"},
        {"type": "text", "text": json.dumps(SEARCH, ensure_ascii=False)},
    ]
    text = format_mcp_output("ragraf_search", blocks)
    assert "src/ragraf/corpus.py:75-86" in text, text
    assert "ничего не найдено" not in text


def test_mcp_output_without_json_falls_back_to_server_text() -> None:
    blocks = [{"type": "text", "text": "op=schema found=False"}]
    text = format_mcp_output("ragraf_schema", blocks)
    assert text == "op=schema found=False"


def test_mcp_symbol_output_keeps_signature() -> None:
    symbol = {"index_rev": "rev-1", "result": [
        {"path": "src/ragraf/corpus.py", "start_line": 75, "end_line": 86,
         "kind": "method", "fqn": "src/ragraf/corpus.py:corpus_hash",
         "signature": "def corpus_hash(self) -> str:"}]}
    text = format_mcp_output("ragraf_symbol", [{"type": "text", "text": json.dumps(symbol)}])
    assert "corpus.py:75-86" in text and "def corpus_hash" in text


if __name__ == "__main__":
    import traceback

    failed = 0
    for name in sorted(n for n in dict(globals()) if n.startswith("test_")):
        try:
            globals()[name]()
            print(f"ok   {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\nпровалов: {failed}")
    raise SystemExit(1 if failed else 0)
