# -*- coding: utf-8 -*-
"""Проверки конвертации хита индекса в `Document` — без обращения к корпусу.

Почему это отдельный файл: здесь проверяется КОНТРАКТ метаданных. Если `path:line`
потеряется при конвертации, агент получит текст без доказательства и не заметит
этого — именно такой отказ и должен ловить тест, а не человек.

Файл запускается и как скрипт (`python tests/test_retriever_document.py`), и через
pytest: у проекта RAGRAF штатный раннер — самостоятельные скрипты с кодом выхода,
а не pytest, поэтому оба способа должны работать.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragraf_langchain.retriever import _stale_flag, hit_to_document  # noqa: E402

#: Настоящая форма хита `op_search` (замер на rev-1355, `src/ragraf/facade.py:3929`).
HIT = {
    "chunk_id": "ee47b30ed2a03637",
    "path": "src/ragraf/corpus.py",
    "node_id": None,
    "start_line": 75,
    "end_line": 86,
    "source_kind": "code",
    "score": 16.3934,
    "why": "совпало по: лексика, вектор",
    "material": "code",
    "doc_role": None,
    "doc_role_title": None,
    "snippet": "def corpus_hash(self) -> str:",
}
ENVELOPE = {"index_rev": "rev-1355", "capability": "full", "edition": "community",
            "provenance": "index", "drift": {"files": 0, "symbols": 0, "entries": 0}}


def test_ref_is_path_and_line() -> None:
    doc = hit_to_document(HIT, ENVELOPE, stale=False)
    assert doc.metadata["ref"] == "src/ragraf/corpus.py:75", doc.metadata["ref"]
    assert doc.metadata["start_line"] == 75
    assert doc.metadata["end_line"] == 86


def test_envelope_fields_travel_with_document() -> None:
    doc = hit_to_document(HIT, ENVELOPE, stale=False)
    assert doc.metadata["index_rev"] == "rev-1355"
    assert doc.metadata["capability"] == "full"
    assert doc.metadata["provenance"] == "index"
    assert doc.metadata["origin"] == "ragraf"
    assert doc.metadata["stale"] is False


def test_chunk_id_is_document_id() -> None:
    doc = hit_to_document(HIT, ENVELOPE)
    assert doc.id == "ee47b30ed2a03637"


def test_snippet_is_content_and_ref_is_fallback() -> None:
    assert hit_to_document(HIT, ENVELOPE).page_content.startswith("def corpus_hash")
    without_text = dict(HIT, snippet=None)
    doc = hit_to_document(without_text, ENVELOPE)
    assert doc.page_content == "src/ragraf/corpus.py:75", doc.page_content


def test_missing_lines_do_not_invent_a_number() -> None:
    hit = {"path": "docs/readme.md", "start_line": None, "end_line": None}
    doc = hit_to_document(hit, ENVELOPE)
    assert doc.metadata["ref"] == "docs/readme.md"
    assert doc.metadata["start_line"] is None


def test_stale_unknown_is_not_reported_as_fresh() -> None:
    class Broken:
        def op_freshness(self, **_kwargs):
            raise RuntimeError("индекс недоступен")

    class Empty:
        def op_freshness(self, **_kwargs):
            return {"result": {}}

    class Fresh:
        def op_freshness(self, **_kwargs):
            return {"result": {"changed_since_index": False}}

    class Stale:
        def op_freshness(self, **_kwargs):
            return {"result": {"changed_since_index": True}}

    assert _stale_flag(Broken()) is None
    assert _stale_flag(Empty()) is None
    assert _stale_flag(Fresh()) is False
    assert _stale_flag(Stale()) is True


if __name__ == "__main__":
    import traceback

    failed = 0
    for name in sorted(n for n in dict(globals()) if n.startswith("test_")):
        try:
            globals()[name]()
            print(f"ok   {name}")
        except Exception:  # noqa: BLE001 — провал печатается, а не глотается
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\nпровалов: {failed}")
    raise SystemExit(1 if failed else 0)
