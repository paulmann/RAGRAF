#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Замер выгоды возможности `contract-drift`: замысел против кода по FQN.

Что измеряется на ЖИВОМ корпусе, а не на словах:

  * `binds` — сколько привязок контракта разрешено полным fqn (`resolved`),
    сколько выведено по паре файл+имя (`derived`) и сколько не разрешено
    (`unresolved`). Это и есть ответ на вопрос «сколько обещаний манифеста
    подтверждается кодом»;
  * `replaced_reads` — сколько символов агент нашёл БЕЗ чтения файлов: каждый
    разрешённый bind даёт `path:line`, файл и строки объявления. Числа файлов,
    строк и символов — оценка по объёму объявлений, помечена как оценка;
  * `findings` — расхождения по видам (закрытый список);
  * `precision` и `recall` на PLANTED-корпусе: в чистый манифест подсаживаются
    заведомые порчи (переименованные bind и несуществующие вызовы), и замер
    сверяет, что детектор нашёл ровно их и не выдумал лишних. Чистый прогон
    обязан дать 0 находок — иначе `precision` падает на ложных срабатываниях;
  * `latency_ms` — время одного прогона на живом корпусе.

Почему замер отдельный, а не «доктрина сказала». Доктрина проверяет наличие,
схему и гейт издания; она не отвечает, сколько пользы приносит возможность и не
срабатывает ли детектор на чистом манифесте. Здесь два числа, которые можно
сравнить до и после правки: `resolved` (сколько замысла подтверждается) и
`precision/recall` на подсадках.

Границы, названные прямо: замер статический (значения во время выполнения не
покрыты); оценка «заменённых чтений» считается по строкам объявлений, а не по
фактическому числу прочитанных байт; planted-порчи поставлены ЗАМЕРОМ, а не
проектом, поэтому они меряют детектор, а не качество чужого манифеста.

Запуск:
    python tools/measure_contract_drift.py
    python tools/measure_contract_drift.py --config config/ragraf.yaml --json
    python tools/measure_contract_drift.py --binds 100 --save out.json

Коды возврата: 0 — замер снят; 1 — детектор противоречит замеру (ложные
срабатывания на чистом манифесте или пропуск planted-порчи); 2 — снять нечем
(нет конфига или индекса).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
# Каталог скрипта выкидывается из sys.path: иначе `tools/ragraf.py` затеняет
# пакет `src/ragraf` (измерено 12.10.2026, повторено в registry_line.py).
_TOOLS_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if str(Path(p or ".").resolve()) != _TOOLS_DIR]

#: Сколько привязок брать в манифест по умолчанию. Предел из критерия приёмки —
#: до 100 binds на корпусе RAGRAF; 40 хватает, чтобы снять числа и остаться
#: в пределе ответа.
DEFAULT_BINDS = 40

#: Каждая N-я привязка в planted-манифесте переименовывается, каждая M-я
#: получает несуществующий ожидаемый вызов. Оба числа кратны, чтобы порчи
#: не накладывались на одну и ту же привязку.
PLANTED_RENAME_EVERY = 4
PLANTED_CALL_EVERY = 8

#: Оценка длины строки для «заменённых чтений». Берётся у фасада, чтобы оценка
#: не заводила вторую редакцию единицы измерения; недоступна — 80 и названная
#: граница, а не молчаливое число.
_FALLBACK_CHARS_PER_LINE = 80


def _chars_per_line() -> int:
    try:
        sys.path.insert(0, str(DEFAULT_ROOT / "src"))
        from ragraf.telemetry import CHARS_PER_LINE

        return int(CHARS_PER_LINE)
    except Exception:  # noqa: BLE001 — оценка не должна ронять замер
        return _FALLBACK_CHARS_PER_LINE


def _sandbox() -> Path:
    """Каталог для манифестов замера: %TEMP%, иначе `.ragraf/tmp` корпуса.

    Рядом с рабочими файлами манифест сдвигал бы контрольную сумму корпуса, а
    `.ragraf` исключён из скана и из суммы по построению.
    """
    for base in ("TEMP", "TMP"):
        value = __import__("os").environ.get(base, "")
        if not value:
            continue
        try:
            candidate = Path(tempfile.mkdtemp(prefix="ragraf-measure-contracts-", dir=value))
            (candidate / "probe.txt").write_text("ok", encoding="utf-8")
            (candidate / "probe.txt").unlink()
            return candidate
        except OSError:
            continue
    fallback = DEFAULT_ROOT / ".ragraf" / "tmp"
    fallback.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="ragraf-measure-contracts-", dir=str(fallback)))


def _module_id(path: str) -> str:
    """Детерминированный id модуля по каталогу файла."""
    head = path.rsplit("/", 1)[0] if "/" in path else "."
    return "M-" + head.replace("/", "-").replace(".", "root").upper()


def build_manifest(sample: list[dict], *, planted: bool) -> tuple[dict, list[dict]]:
    """Манифест замера: по модулю на каталог, контракт на каждый bind.

    `planted=True` переименовывает каждую N-ю привязку и подсовывает
    несуществующий ожидаемый вызов каждой M-й (только тем, что НЕ переименованы:
    иначе порчи накладывались бы друг на друга и одна привязка давала бы две
    ожидаемые находки при одной фактической). Возвращается и список ожидаемых
    находок — по нему считается recall.
    """
    modules: dict[str, dict] = {}
    contracts: list[dict] = []
    expected: list[dict] = []
    for index, row in enumerate(sample):
        path = str(row["path"])
        module_id = _module_id(path)
        head = path.rsplit("/", 1)[0] if "/" in path else "."
        module = modules.setdefault(module_id, {
            "id": module_id, "purpose": "замер", "files": [f"{head}/**"], "depends_on": [],
        })
        contract_id = f"V-{index:03d}"
        bind = str(row["fqn"])
        expects: dict = {}
        renamed = planted and (index + 1) % PLANTED_RENAME_EVERY == 0
        if renamed:
            bind = bind + "__planted_renamed"
            expected.append({"kind": "unbound_fqn", "contract": contract_id})
        elif planted and (index + 1) % PLANTED_CALL_EVERY == 0:
            expects["calls"] = ["__planted_missing_call__"]
            expected.append({"kind": "contract_violation", "contract": contract_id})
        contract = {"id": contract_id, "module": module_id, "bind": bind}
        if expects:
            contract["expects"] = expects
        contracts.append(contract)
    manifest = {
        "schema": 1,
        "strict_code_coverage": False,
        "modules": [modules[key] for key in sorted(modules)],
        "contracts": contracts,
    }
    return manifest, expected


def sample_binds(conn, rev_id: str, limit: int) -> list[dict]:
    """Детерминированная выборка привязок: по одному символу на файл.

    Две вещи здесь не случайны. **Порядок** — по пути, а не по времени или
    порядку вставки: иначе два прогона замера снимали бы числа с разных наборов
    и сравнивать «до/после» было бы нечем. **Один символ на файл** — иначе
    выборка `ORDER BY fqn LIMIT 40` собрала бы 28 узлов одного архивного файла
    (замер 07.11.2026: 28 из 40 пришлись на 2 файла), и число «заменённых
    чтений» говорило бы о двух файлах вместо сорока.

    Связи с повторами fqn исключены: fqn, встречающийся в ревизии больше одного
    раза, разрешается правилом «два узла — не доказательство», и такой bind
    попал бы в отчёт как `unbound_fqn` — порча набора, а не находка корпуса.
    """
    placeholders = ",".join("?" for _ in ("function", "method"))
    sql = (
        "SELECT fqn, path, start_line, end_line FROM ("
        "  SELECT n.fqn AS fqn, n.path AS path, n.start_line AS start_line,"
        "         n.end_line AS end_line,"
        "         ROW_NUMBER() OVER (PARTITION BY n.path ORDER BY n.start_line, n.node_id) AS rn"
        f"  FROM node n WHERE n.rev_id = ? AND n.kind IN ({placeholders})"
        "    AND n.fqn IS NOT NULL AND n.fqn <> ''"
        "    AND n.path IS NOT NULL AND n.path <> ''"
        f"    AND (SELECT COUNT(*) FROM node m WHERE m.rev_id = n.rev_id"
        f"         AND m.fqn = n.fqn) = 1"
        ") WHERE rn = 1 ORDER BY path LIMIT ?")
    rows = conn.execute(sql, (rev_id, "function", "method", limit)).fetchall()
    out: list[dict] = []
    for row in rows:
        out.append({
            "fqn": str(row[0]),
            "path": str(row[1]).replace("\\", "/"),
            "start_line": int(row[2] or 0),
            "end_line": int(row[3] or 0),
        })
    return out


def measure(config_path: Path, binds: int) -> dict:
    sys.path.insert(0, str(DEFAULT_ROOT / "src"))
    from ragraf.config import load as load_config
    from ragraf.facade import Facade

    cfg = load_config(config_path)
    facade = Facade(cfg, telemetry=False, watch=False)
    try:
        rev = facade.current_rev()
        if not rev:
            raise RuntimeError("индекс не создан: выполните ragraf index")
        sample = sample_binds(facade.graph, rev, binds)
        if not sample:
            raise RuntimeError("в индексе нет объявленных функций и методов: "
                               "привязывать не к чему")
        sandbox = _sandbox()
        clean, _clean_expected = build_manifest(sample, planted=False)
        clean_path = sandbox / "contracts-measure-clean.yaml"
        clean_path.write_text(json.dumps(clean, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        planted, planted_expected_rows = build_manifest(sample, planted=True)
        planted_path = sandbox / "contracts-measure-planted.yaml"
        planted_path.write_text(json.dumps(planted, ensure_ascii=False, indent=2),
                                encoding="utf-8")

        started = time.perf_counter()
        clean_payload = facade.op_contract_drift(manifest_path=str(clean_path))
        latency_ms = (time.perf_counter() - started) * 1000.0
        planted_payload = facade.op_contract_drift(manifest_path=str(planted_path))
        second_ms = (time.perf_counter() - started) * 1000.0 - latency_ms
        if clean_payload.get("error"):
            raise RuntimeError(str(clean_payload.get("error")))
        if planted_payload.get("error"):
            raise RuntimeError(str(planted_payload.get("error")))
        clean_result = clean_payload.get("result") or {}
        planted_result = planted_payload.get("result") or {}
    finally:
        facade.close()

    resolved = clean_result.get("resolved") or []
    files = {str(item.get("path") or "") for item in resolved if item.get("path")}
    # Объём объявления: от строки объявления до конца метода в индексе. Полные
    # диапазоны есть в выборке узлов; сопоставление идёт по fqn, а не по порядку.
    spans_by_fqn = {str(row["fqn"]): max(1, row["end_line"] - row["start_line"] + 1)
                    for row in sample}
    spans = sum(spans_by_fqn.get(str(item.get("fqn") or ""), 1) for item in resolved)

    planted_expected = len(planted_expected_rows)

    # Истинно-положительные: порчи, которые детектор назвал. Идентификация —
    # по (вид, контракт), а не по тексту причины: сравнение текста сломалось бы
    # от любой правки формулировки.
    detected: set[tuple[str, str]] = set()
    false_positives: list[dict] = []
    for finding in (planted_result.get("drift") or {}).get("findings") or []:
        kind = str(finding.get("kind"))
        contract = str(finding.get("contract") or "")
        wanted = {(str(item["kind"]), str(item["contract"]))
                  for item in planted_expected_rows}
        if (kind, contract) in wanted:
            detected.add((kind, contract))
        else:
            false_positives.append(finding)
    clean_findings = (clean_result.get("drift") or {}).get("findings") or []
    # Ложное срабатывание чистого прогона: находка, которой в чистом манифесте
    # быть не должно. В замере модули и файлы подобраны так, что расхождений
    # нет вовсе, — любая находка здесь означает ошибку детектора или набора.
    clean_false = list(clean_findings)
    true_positive = len(detected)
    precision = (true_positive / (true_positive + len(false_positives) + len(clean_false))
                 if (true_positive + len(false_positives) + len(clean_false)) else 1.0)
    recall = (true_positive / planted_expected) if planted_expected else 1.0
    return {
        "config": str(config_path),
        "rev": rev,
        "binds_requested": binds,
        "binds": clean_result.get("binds") or {},
        "modules": {
            "total": (clean_result.get("modules") or {}).get("total", 0),
            "with_code": (clean_result.get("modules") or {}).get("with_code", 0),
        },
        "findings": {
            "clean_total": len(clean_findings),
            "clean_false": len(clean_false),
            "planted_total": len((planted_result.get("drift") or {}).get("findings") or []),
            "planted_expected": planted_expected,
            "counts": (clean_result.get("drift") or {}).get("counts") or {},
            "planted_kinds": sorted({str(item["kind"]) for item in planted_expected_rows}),
        },
        "replaced_reads": {
            "symbols": len(resolved),
            "files": len(files),
            "declaration_lines": spans,
            "chars_estimate": spans * _chars_per_line(),
            "note": "оценка по объёму объявлений; фактическое чтение файла не "
                    "производилось",
        },
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "latency_ms": round(latency_ms, 1),
        "second_run_ms": round(second_ms, 1),
        "limits": [
            "статический анализ: значения во время выполнения не покрыты",
            "planted-порчи поставлены замером, а не проектом",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="measure_contract_drift.py",
        description="Замер выгоды сверки замысла и кода по FQN (contract-drift).")
    parser.add_argument("--config", default=str(DEFAULT_ROOT / "config" / "ragraf.yaml"),
                        help="конфиг корпуса (по умолчанию config/ragraf.yaml)")
    parser.add_argument("--binds", type=int, default=DEFAULT_BINDS,
                        help=f"сколько привязок взять в манифест (по умолчанию {DEFAULT_BINDS})")
    parser.add_argument("--json", action="store_true", help="машинный отчёт")
    parser.add_argument("--save", default="", help="записать машинный отчёт в файл")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"нет конфига корпуса: {config_path}", file=sys.stderr)
        return 2
    try:
        report = measure(config_path, int(args.binds))
    except RuntimeError as exc:
        print(f"замер не снят: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — код 2 «снять нечем», а не трассировка
        print(f"замер не снят: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.save:
        Path(args.save).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"машинный отчёт записан: {args.save}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        binds = report["binds"]
        print(f"Замер contract-drift на {report['config']} ({report['rev']})")
        print(f"  binds: всего {binds.get('total', 0)}, "
              f"resolved {binds.get('resolved', 0)}, "
              f"derived {binds.get('derived', 0)}, "
              f"unresolved {binds.get('unresolved', 0)}")
        replaced = report["replaced_reads"]
        print(f"  заменённые чтения: символов {replaced['symbols']}, "
              f"файлов {replaced['files']}, строк объявлений {replaced['declaration_lines']}, "
              f"символов (оценка) {replaced['chars_estimate']}")
        findings = report["findings"]
        print(f"  находки чистого манифеста: {findings['clean_total']} "
              f"(ложных {findings['clean_false']})")
        print(f"  planted: ожидалось {findings['planted_expected']}, "
              f"найдено всего {findings['planted_total']}")
        print(f"  precision {report['precision']}, recall {report['recall']}")
        print(f"  время одного прогона: {report['latency_ms']} мс")
        for limit in report["limits"]:
            print(f"  · ограничение: {limit}")
    verdict_ok = (report["findings"]["clean_false"] == 0
                  and report["recall"] >= 1.0
                  and report["findings"]["planted_total"] == report["findings"]["planted_expected"])
    if not verdict_ok:
        print("ВЕРДИКТ: детектор противоречит замеру (ложные срабатывания либо пропуск порчи)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
