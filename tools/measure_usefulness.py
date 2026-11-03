#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Замер ПОЛЬЗЫ поиска на фиксированном наборе живых вопросов — до и после.

Зачем отдельный замер. Все прежние замеры мерили СКОРОСТЬ (`INDEX-SPEED.md`)
или точность графа (`measure_impact_precision.py`). Ни один не отвечал на
вопрос владельца «стало ли агенту ПОЛЬЗОВАТЕЛЬНО лучше»: попадёт ли файл-цель
в ответ с первого запроса и сколько кандидатов придётся просмотреть.

Что измеряется на каждом вопросе из `bench/usefulness/questions.yaml`:

  * `target_rank` — на каком месте в ответе стоит файл-цель (None — не попал);
    из него считаются попадания с первого запроса (`hit@1`), в тройку и в
    пятёрку. Это и есть «попадание в файл-цель с первого запроса`;
  * `target_rank_code` — то же место, если считать ТОЛЬКО файлы кода: ответ
    часто занят документами-обзорами, и агент, спрашивающий про код, платит за
    это вторым запросом. Разница двух мест и есть цена смешанной выдачи;
  * `candidates` — сколько РАЗНЫХ файлов агент увидит и должен просмотреть;
  * `answer_chars` — сколько символов отдал ответ (стоимость для контекста);
  * `code_hits` / `doc_hits` — из чего состоит ответ: код или документы;
  * `copies` — сколько мест в ответе заняли побайтово одинаковые копии другого
    файла ответа: копия не даёт нового факта, но занимает место;
  * `grep_files` / `grep_chars` — БАЗОВАЯ ЛИНИЯ без индекса: сколько файлов
    корпуса содержат искомые слова (`rg -l`) и сколько символов пришлось бы
    прочитать, чтобы их просмотреть. Считается по тому же составу файлов, что
    и индекс (`corpus.scan`), поэтому сравнение честное: один и тот же корпус,
    один и тот же вопрос.

Разбор провалов (`--diagnose`) отвечает не «не нашлось», а ПОЧЕМУ, и к каждому
виду даёт ЛЕЧЕНИЕ ЧИСЛОМ: сколько провалов вернёт схлопывание копий в ответе,
сколько — расширение обрезки, сколько — фасет кода. Лечение проверяется, а не
объявляется: два из трёх проверенных так и не сработали (см. отчёт этапа).

Чего замер НЕ делает и не должен: он не настраивает веса и пороги поиска по
этому набору. Набор контрольный: подгонка под него превращает замер пользы в
попадание в собственный ответ (то же правило записано в `search_hybrid`).

Запуск:
    python tools/measure_usefulness.py                     (таблица + итог)
    python tools/measure_usefulness.py --diagnose          (+ разбор провалов)
    python tools/measure_usefulness.py --save out.json     (снимок для до/после)
    python tools/measure_usefulness.py --limit 10          (другая обрезка ответа)

Коды возврата: 0 — замер снят; 1 — снят, но набор противоречит корпусу (цель
не в корпусе, слово-якорь не встречается в файле-цели) или при разборе остался
провал без причины; 2 — снять нечем (нет конфига, набора или индекса).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
# Каталог скрипта выкидывается из sys.path: иначе `tools/ragraf.py` затеняет
# пакет `src/ragraf` (измерено 12.10.2026, повторено в registry_line.py).
_TOOLS_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if str(Path(p or ".").resolve()) != _TOOLS_DIR]

QUESTIONS_FILE = Path("bench") / "usefulness" / "questions.yaml"

#: Расширения, которые замер считает документами, — только для отдельного
#: столбца отчёта. ЧТО есть код, решает РЕЕСТР форматов (`formats.group_of_path`):
#: второй список расширений разошёлся бы с реестром молча.
DOC_EXT = frozenset({".md", ".markdown", ".rst", ".txt", ".pdf", ".docx", ".xlsx",
                     ".pptx", ".odt", ".eml"})


@dataclass
class Question:
    """Один вопрос набора: что спрашивают, где ответ и чем искали бы без индекса."""

    id: str
    kind: str
    question: str
    targets: list[str]
    anchor: list[str]
    note: str = ""


def load_questions(path: Path) -> list[Question]:
    """Прочитать набор. Ошибки в наборе — отказ, а не тихий пропуск вопроса."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ValueError(f"набор не разобран: в {path} нет списка questions")
    out: list[Question] = []
    seen: set[str] = set()
    for raw in data["questions"]:
        qid = str(raw.get("id") or "").strip()
        if not qid:
            raise ValueError("у вопроса нет id")
        if qid in seen:
            raise ValueError(f"повторяющийся id вопроса: {qid}")
        seen.add(qid)
        targets = [str(t) for t in (raw.get("targets") or []) if str(t).strip()]
        if not targets:
            raise ValueError(f"{qid}: не указан файл-цель")
        anchor = [str(a) for a in (raw.get("anchor") or []) if str(a).strip()]
        out.append(Question(
            id=qid,
            kind=str(raw.get("kind") or "code"),
            question=str(raw.get("question") or "").strip(),
            targets=targets,
            anchor=anchor,
            note=str(raw.get("note") or ""),
        ))
    return out


def is_code(path: str) -> bool:
    """Файл кода — по РЕЕСТРУ форматов (`formats.group_of_path`).

    Второй список расширений внутри замера противоречил бы реестру, который уже
    решает, что индексировать и как разбирать: замер обязан считать то же, что и
    продукт, иначе меряет не то.
    """
    from ragraf import formats

    return formats.group_of_path(path) == "code"


def corpus_texts(cfg) -> dict[str, str]:
    """Текст каждого файла корпуса в нижнем регистре — для базовой линии без индекса.

    Состав берётся из `corpus.scan`, то есть ТОТ ЖЕ, что индексируется: иначе
    базовая линия мерила бы другой корпус. Двоичные файлы пропускаются
    (`\\x00` в первых килобайтах), документы читаются как сырые байты: для
    якорей набора (обычные слова) этого достаточно, и это названо прямо, а не
    замаскировано.
    """
    from ragraf import corpus

    scanned = corpus.scan(cfg, digest=False)
    texts: dict[str, str] = {}
    for item in scanned.files:
        try:
            raw = (cfg.root / item.path).read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:4096]:
            continue
        texts[item.path] = raw.decode("utf-8", errors="ignore").lower()
    return texts


def file_sha(path: Path) -> str:
    """sha256 файла: по нему видно, что два места в ответе — одна и та же копия."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def first_rank(paths: list[str], targets: list[str]) -> int | None:
    """Место первого попадания в файл-цель (1 — первый), None — не попал."""
    wanted = set(targets)
    for index, path in enumerate(paths, start=1):
        if path in wanted:
            return index
    return None


def measure_baseline(questions: list[Question], texts: dict[str, str]) -> dict[str, dict]:
    """Сколько файлов и символов пришлось бы просмотреть БЕЗ индекса.

    Базовая линия считается в пользу отказа от индекса: берётся лучший случай
    для поиска по словам (совпал любой из якорей), файлы считаются уникальными,
    а объём — по полному тексту файла, как при чтении целиком.
    """
    out: dict[str, dict] = {}
    sizes = {path: len(text) for path, text in texts.items()}
    for q in questions:
        anchors = [a.lower() for a in q.anchor]
        matched = {path for path, text in texts.items()
                   if any(a in text for a in anchors)} if anchors else set()
        out[q.id] = {
            "anchors": list(q.anchor),
            "files": len(matched),
            "chars": sum(sizes[p] for p in matched),
            "has_target": any(t in matched for t in q.targets),
        }
    return out


def measure_search(facade, questions: list[Question], limit: int) -> list[dict]:
    """Ответ индекса на каждый вопрос: место цели, кандидаты, копии, символы."""
    rows: list[dict] = []
    for q in questions:
        started = time.perf_counter()
        env = facade.op_search(q.question, limit=limit)
        seconds = time.perf_counter() - started
        hits = env.get("result") or []
        paths = [str(h.get("path") or "") for h in hits]
        distinct = list(dict.fromkeys(p for p in paths if p))
        payload = json.dumps(hits, ensure_ascii=False)
        shas: set[str] = set()
        copies = 0
        for path in distinct:
            digest = file_sha(facade.cfg.root / path)
            if not digest:
                continue
            if digest in shas:
                copies += 1
            shas.add(digest)
        rows.append({
            "id": q.id,
            "kind": q.kind,
            "targets": q.targets,
            "target_rank": first_rank(distinct, q.targets),
            "candidates": len(distinct),
            "hits": len(hits),
            "copies": copies,
            "code_hits": sum(1 for p in distinct if is_code(p)),
            "doc_hits": sum(1 for p in distinct if Path(p).suffix.lower() in DOC_EXT),
            # Подсказка «лучшее кодовое совпадение вне выдачи» (поле
            # `code_candidate` ответа): она тоже даёт агенту файл, поэтому
            # считается отдельно, а не сливается с местом в выдаче.
            "code_candidate": str(env.get("code_candidate_path")
                                  or (env.get("code_candidate") or {}).get("path")
                                  or ""),
            "answer_chars": len(payload),
            "found": bool(env.get("found")),
            "seconds": round(seconds, 3),
            "paths": distinct[:limit],
        })
    return rows


def _channel_ranks(facade, question: str, targets: list[str], rev: str,
                   answer_limit: int, scan_limit: int = 400) -> dict:
    """Место цели в КАЖДОМ канале: чем именно провалился провал.

    Место в слитом списке считается ДВАЖДЫ и это не дублирование: окно отбора
    RRF зависит от запрошенной обрезки, поэтому порядок при `limit=5` и при
    `limit=400` — разные величины. Первое отвечает «что видел агент», второе —
    «нашёл ли канал вообще».
    """
    vector = None
    if facade.embedder.available():
        try:
            vector = facade.embedder.embed_one(question)
        except Exception:
            vector = None
    lex = facade.lexvec.search_lexical(rev, question, limit=scan_limit)
    vec = facade.lexvec.search_vector(rev, vector, limit=scan_limit) if vector else []
    fused_wide = facade.lexvec.search_hybrid(rev, question, vector, limit=scan_limit)
    fused_answer = facade.lexvec.search_hybrid(rev, question, vector, limit=answer_limit)
    return {
        "lex_rank": first_rank([h.path for h in lex], targets),
        "vec_rank": first_rank([h.path for h in vec], targets),
        "fused_wide": first_rank([h.path for h in fused_wide], targets),
        "fused_answer": first_rank([h.path for h in fused_answer], targets),
        "code_rank": first_rank([h.path for h in fused_wide if is_code(h.path)], targets),
        "vector_available": bool(vector),
        "wide_paths": [h.path for h in fused_wide],
    }


def recover_by_copies(paths: list[str], root: Path, limit: int) -> list[str]:
    """Что осталось бы от ответа, если схлопнуть побайтово одинаковые копии.

    Это не настройка поиска, а проверка ГИПОТЕЗЫ: копия одного текста не даёт
    нового факта, поэтому место, занятое копией, считается потерянным. Сколько
    провалов такой порядок вернёт — вопрос замера, а не вкуса.
    """
    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        digest = file_sha(root / path)
        if digest and digest in seen:
            continue
        if digest:
            seen.add(digest)
        out.append(path)
        if len(out) >= limit:
            break
    return out


def diagnose(facade, questions: list[Question], rows: list[dict], limit: int,
             texts: dict[str, str], rev: str) -> dict:
    """Почему провал и что его лечит — по видам, с числами.

    Виды названы по тому, что видно в коде, а не по догадке:

      * `нет в канале` — цель отсутствует в списке канала даже на 400 местах;
      * `ниже обрезки` — канал её нашёл, но место за пределом выдачи;
      * `копии заняли` — место цели занято побайтово одинаковыми копиями;
      * `лексика бессильна` — ни одно слово вопроса не найдено в файле-цели:
        лексический канал здесь не «плохо ранжирует», а не имеет за что взяться.

    Каждому виду — лечение числом: сколько провалов вернёт схлопывание копий,
    расширение обрезки и фасет кода (место цели среди файлов кода).
    """
    from ragraf.lexvec import tokenize_for_fts

    by_id = {row["id"]: row for row in rows}
    out_rows: list[dict] = []
    for q in questions:
        row = by_id[q.id]
        info = _channel_ranks(facade, q.question, q.targets, rev, limit)
        tokens = [t for t in tokenize_for_fts(q.question).split() if len(t) >= 4][:12]
        target_text = " ".join(texts.get(t, "") for t in q.targets)
        tokens_found = [t for t in tokens if t in target_text]
        deduped = recover_by_copies(info["wide_paths"], facade.cfg.root, limit)
        deduped_rank = first_rank(deduped, q.targets)

        verdict: list[str] = []
        if row["target_rank"]:
            verdict.append("цель в выдаче")
        elif info["fused_wide"]:
            # Канал нашёл, но не в выдачу: это провал ранжирования, а не поиска.
            verdict.append(f"ниже обрезки (место {info['fused_wide']} в широком списке)")
        else:
            if info["lex_rank"] is None:
                verdict.append("лексика не нашла")
            if info["vector_available"] and info["vec_rank"] is None:
                verdict.append("вектор не нашёл")
            if not verdict:
                verdict.append("канал нашёл, но не в выдачу")
        if not row["target_rank"] and tokens and not tokens_found:
            verdict.append("лексика бессильна (слов нет в файле-цели)")
        if not row["target_rank"] and deduped_rank and deduped_rank <= limit:
            verdict.append("копии заняли место")
        if not verdict:
            verdict.append("причина не определена")

        out_rows.append({
            "id": q.id,
            "kind": q.kind,
            "target_rank": row["target_rank"],
            "lex_rank": info["lex_rank"],
            "vec_rank": info["vec_rank"],
            "fused_answer": info["fused_answer"],
            "fused_wide": info["fused_wide"],
            "code_rank": info["code_rank"],
            "tokens": len(tokens),
            "tokens_found": len(tokens_found),
            "tokens_missing": [t for t in tokens if t not in target_text][:6],
            "rank_after_dedupe": deduped_rank,
            "recovered_by_dedupe": bool(not row["target_rank"] and deduped_rank
                                        and deduped_rank <= limit),
            "recovered_by_limit20": bool(not row["target_rank"] and info["fused_wide"]
                                         and info["fused_wide"] <= 20),
            "code_facet_hit": bool(not row["target_rank"] and info["code_rank"]
                                   and info["code_rank"] <= 3),
            "verdict": verdict,
        })

    def count(pred) -> int:
        return sum(1 for r in out_rows if pred(r))

    missed = [r for r in out_rows if not r["target_rank"]]
    summary = {
        "questions": len(out_rows),
        "missed": len(missed),
        "lex_none": count(lambda r: not r["target_rank"] and r["lex_rank"] is None),
        "vec_none": count(lambda r: not r["target_rank"] and r["vec_rank"] is None),
        "below_cut": count(lambda r: not r["target_rank"] and r["fused_wide"] is not None),
        "only_below_cut": count(lambda r: not r["target_rank"] and r["fused_wide"] is not None
                                and r["lex_rank"] is not None),
        "no_content_words": count(lambda r: not r["target_rank"] and r["tokens_found"] == 0),
        "words_found_avg": round(sum(r["tokens_found"] for r in out_rows)
                                 / max(1, sum(r["tokens"] for r in out_rows)), 3),
        "recovered_by_dedupe": count(lambda r: r["recovered_by_dedupe"]),
        "recovered_by_limit20": count(lambda r: r["recovered_by_limit20"]),
        "recovered_by_code_facet": count(lambda r: r["code_facet_hit"]),
        "unexplained": count(lambda r: r["verdict"] == ["причина не определена"]),
        "missed_ids": [r["id"] for r in missed],
    }
    return {"rows": out_rows, "summary": summary}


def summarize(rows: list[dict], baseline: dict[str, dict] | None,
              diag: dict | None = None) -> dict:
    """Доли попаданий, средние величины и отношение к базовой линии."""
    total = len(rows)
    if not total:
        return {}

    def hit(key: str, k: int) -> int:
        return sum(1 for r in rows if r.get(key) and r[key] <= k)

    answer_chars = sum(r["answer_chars"] for r in rows)
    out = {
        "questions": total,
        "hit1": hit("target_rank", 1),
        "hit3": hit("target_rank", 3),
        "hit5": hit("target_rank", 5),
        "hit1_share": round(hit("target_rank", 1) / total, 4),
        "hit3_share": round(hit("target_rank", 3) / total, 4),
        "hit5_share": round(hit("target_rank", 5) / total, 4),
        "candidates_avg": round(sum(r["candidates"] for r in rows) / total, 2),
        "copies_total": sum(r["copies"] for r in rows),
        "code_hits_total": sum(r["code_hits"] for r in rows),
        "answers_without_code": sum(1 for r in rows if r["code_hits"] == 0),
        "answers_with_code_hint": sum(1 for r in rows if r.get("code_candidate")),
        "visible_total": sum(1 for r in rows if r["target_rank"]
                               or (r.get("code_candidate") in r["targets"])),
        "answer_chars": answer_chars,
        "answer_chars_avg": round(answer_chars / total, 1),
        "seconds_avg": round(sum(r["seconds"] for r in rows) / total, 3),
    }
    if diag:
        code_rows = [r for r in diag["rows"] if r["code_rank"]]
        out["code_rank_hit1"] = sum(1 for r in code_rows if r["code_rank"] == 1)
        out["code_rank_hit3"] = sum(1 for r in code_rows if r["code_rank"] <= 3)
        out["code_rank_hit5"] = sum(1 for r in code_rows if r["code_rank"] <= 5)
        out["code_rank_counted"] = len(code_rows)
    if baseline:
        grep_files = sum(baseline[r["id"]]["files"] for r in rows)
        grep_chars = sum(baseline[r["id"]]["chars"] for r in rows)
        out["grep_files_total"] = grep_files
        out["grep_files_avg"] = round(grep_files / total, 1)
        out["grep_chars_total"] = grep_chars
        out["grep_chars_avg"] = round(grep_chars / total, 1)
        out["grep_has_target"] = sum(1 for r in rows if baseline[r["id"]]["has_target"])
        out["saved_chars"] = grep_chars - answer_chars
        out["saved_share"] = (round(1 - answer_chars / grep_chars, 4)
                              if grep_chars else 0.0)
        out["candidates_vs_grep"] = (round(sum(r["candidates"] for r in rows)
                                          / max(1, grep_files), 4))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Замер пользы поиска: попадание в файл-цель, кандидаты, стоимость ответа")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--questions", default=str(QUESTIONS_FILE))
    parser.add_argument("--limit", type=int, default=5,
                        help="сколько хитов отдаёт поиск (по умолчанию 5 — типичный ответ агенту)")
    parser.add_argument("--diagnose", action="store_true",
                        help="разобрать провалы по видам и назвать лечение числом")
    parser.add_argument("--no-baseline", action="store_true",
                        help="не считать базовую линию без индекса (быстрее)")
    parser.add_argument("--save", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    sys.path.insert(0, str(root / "src"))

    from ragraf import config as config_mod
    from ragraf.facade import Facade

    config_path = root / "config" / "ragraf.yaml"
    if not config_path.is_file():
        print(f"NOT-IMPLEMENTED: нет {config_path}")
        return 2
    questions_path = Path(args.questions)
    if not questions_path.is_absolute():
        questions_path = root / questions_path
    if not questions_path.is_file():
        print(f"NOT-IMPLEMENTED: нет набора вопросов {questions_path}")
        return 2

    questions = load_questions(questions_path)
    cfg = (getattr(config_mod, "load", None) or getattr(config_mod, "load_config"))(config_path)
    facade = Facade(cfg, tool="dev", agent="RAGRAF-Measure", agent_kind="internal",
                    task="этап13:польза", telemetry=False)
    try:
        rev = facade.current_rev()
        if not rev:
            print("NOT-IMPLEMENTED: индекс пуст — мерить нечего")
            return 2

        texts = {} if args.no_baseline else corpus_texts(cfg)
        baseline = measure_baseline(questions, texts) if texts else None
        rows = measure_search(facade, questions, args.limit)
        diag = None
        if args.diagnose and texts:
            diag = diagnose(facade, questions, rows, args.limit, texts, rev)
        # Место цели среди кода считается по тому же слиянию, что и ответ,
        # поэтому диагностика нужна даже без базовой линии.
        code_ranks = {}
        if diag is None:
            for q in questions:
                info = _channel_ranks(facade, q.question, q.targets, rev, args.limit)
                code_ranks[q.id] = info["code_rank"]
            for row in rows:
                row["target_rank_code"] = code_ranks[row["id"]]
        else:
            for row in rows:
                row["target_rank_code"] = {r["id"]: r["code_rank"]
                                           for r in diag["rows"]}[row["id"]]

        problems = [r["id"] for r in rows if not r["found"]]
        # Набор обязан соответствовать корпусу: цель — файл из корпуса, якорь —
        # слово В ФАЙЛЕ-ЦЕЛИ. Иначе базовая линия сравнивает несравнимое, и
        # «поиск не нашёл» означало бы «вопрос задан про другой корпус».
        wrong_target = [r["id"] for r in rows
                        if not any((cfg.root / t).exists() for t in r["targets"])]
        wrong_anchor = [r["id"] for r in rows if baseline and not baseline[r["id"]]["has_target"]]

        report = {
            "root": str(root),
            "rev": rev,
            "limit": args.limit,
            "questions": len(questions),
            "summary": summarize(rows, baseline, diag),
            "rows": rows,
        }
        if baseline:
            report["baseline"] = baseline
        if diag:
            report["diagnose"] = diag

        if args.save:
            Path(args.save).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                       encoding="utf-8", newline="\n")
            print(f"снимок: {args.save}")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if (problems or wrong_target or wrong_anchor) else 0

        summary = report["summary"]
        print(f"[польза] ревизия {rev}, вопросов {summary['questions']}, "
              f"обрезка ответа {args.limit}")
        print(f"  попадание в файл-цель с ПЕРВОГО запроса: {summary['hit1']} "
              f"({summary['hit1_share'] * 100:.1f} %)")
        print(f"  в первую тройку:                          {summary['hit3']} "
              f"({summary['hit3_share'] * 100:.1f} %)")
        print(f"  в первую пятёрку:                         {summary['hit5']} "
              f"({summary['hit5_share'] * 100:.1f} %)")
        if summary.get("code_rank_counted"):
            print(f"  место цели среди ТОЛЬКО файлов кода:      {summary['code_rank_hit1']} "
                  f"первых, {summary['code_rank_hit3']} в тройке, "
                  f"{summary['code_rank_hit5']} в пятёрке "
                  f"(считалось {summary['code_rank_counted']})")
        print(f"  ответов без единого файла кода:           {summary['answers_without_code']}"
              f" из {summary['questions']} (подсказка code_candidate — в "
              f"{summary['answers_with_code_hint']})")
        print(f"  цель видна в выдаче ИЛИ в подсказке:      {summary['visible_total']}"
              f" из {summary['questions']}")
        print(f"  копий в ответах:                          {summary['copies_total']}")
        print(f"  символов в ответах:                       {summary['answer_chars']} "
              f"({summary['answer_chars_avg']} на вопрос)")
        if baseline:
            print(f"  без индекса пришлось бы просмотреть:      {summary['grep_files_total']} "
                  f"файлов ({summary['grep_files_avg']} на вопрос), "
                  f"{summary['grep_chars_total']} символов")
            print(f"  замена чтения файлов:                     "
                  f"{summary['saved_chars']} символов "
                  f"({summary['saved_share'] * 100:.1f} % от чтения целиком)")
        for row in rows:
            mark = "ok " if row["target_rank"] else "нет"
            place = f"{row['target_rank']:2d}" if row["target_rank"] else " -"
            code = f"{row['target_rank_code']:2d}" if row.get("target_rank_code") else " -"
            print(f"  {mark} {row['id']:4s} место {place} (код {code})  "
                  f"кандидатов {row['candidates']:2d}  копий {row['copies']:2d}  "
                  f"{row['answer_chars']:5d} симв.  {row['kind']:5s} {', '.join(row['targets'])}")
        if diag:
            s = diag["summary"]
            print(f"[разбор] провалов {s['missed']} из {s['questions']}")
            print(f"  лексика не нашла вовсе:            {s['lex_none']}")
            print(f"  вектор не нашёл:                   {s['vec_none']}")
            print(f"  нашлось, но ниже обрезки:          {s['below_cut']}")
            print(f"  слов вопроса нет в файле-цели:     {s['no_content_words']} "
                  f"(доля найденных слов {s['words_found_avg'] * 100:.0f} %)")
            print(f"  ЛЕЧЕНИЕ схлопнуть копии:           вернёт {s['recovered_by_dedupe']}")
            print(f"  ЛЕЧЕНИЕ предел 20:                 вернёт {s['recovered_by_limit20']}")
            print(f"  ЛЕЧЕНИЕ фасет кода (тройка):       вернёт {s['recovered_by_code_facet']}")
            for r in diag["rows"]:
                if r["target_rank"]:
                    continue
                print(f"    {r['id']:4s} лексика {str(r['lex_rank']):>4s}  вектор "
                      f"{str(r['vec_rank']):>4s}  ответ {str(r['fused_answer']):>4s}  "
                      f"широко {str(r['fused_wide']):>4s}  код {str(r['code_rank']):>4s}  "
                      f"слов {r['tokens_found']}/{r['tokens']}  "
                      f"после копий {str(r['rank_after_dedupe']):>4s}  "
                      f"{'; '.join(r['verdict'])}")
            if s["unexplained"]:
                print(f"НЕОБЪЯСНЁННЫХ ПРОВАЛОВ: {s['unexplained']} — у провала нет причины, "
                      "это дефект разбора, а не свойство поиска")
        if wrong_target:
            print(f"НАБОР ПРОТИВ КОРПУСА: нет файла-цели у {', '.join(wrong_target)}")
        if wrong_anchor:
            print("НАБОР ПРОТИВ КОРПУСА: слово-якорь не встречается в файле-цели у "
                  + ", ".join(wrong_anchor)
                  + " — базовая линия без индекса здесь не сравнима")
        if problems:
            print(f"ПУСТОЙ ОТВЕТ на {', '.join(problems)}")
        print("Строка для отчёта: "
              f"попадание с первого запроса {summary['hit1']}/{summary['questions']} "
              f"({summary['hit1_share'] * 100:.0f} %), кандидатов в среднем "
              f"{summary['candidates_avg']}")
        return 1 if (problems or wrong_target or wrong_anchor
                     or (diag and diag["summary"]["unexplained"])) else 0
    finally:
        facade.close()


if __name__ == "__main__":
    sys.exit(main())
