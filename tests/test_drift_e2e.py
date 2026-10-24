"""Сквозной тест журнала расхождений на изолированном корпусе.

Проверяется (ACCEPTANCE.md, тесты M5):
  M5-T2 — обнаружение дельт;
  M5-T3 — append-only: повторный скан не создаёт дубликатов;
  M5-T4 — дедупликация;
  M5-T5 — slice_boundary при выносе кода в метод;
  M5-T7 — live_read показывает отличие от индекса;
  M5-T8/T9 — reindex --commit создаёт архив с тремя разделами;
  ФТ-24 — операции чтения не меняют хранилища;
  ФТ-39 — секретоносный файл не попадает в индекс.

Корпус пересоздаётся из шаблонов в tests/fixtures/templates.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.drift import archive_and_reset, scan_drift  # noqa: E402
from ragraf.facade import Facade  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402

CFG = REPO / "config" / "fixture.yaml"
PROJ = REPO / "tests" / "fixtures" / "proj"

ENGINE_V1 = """<?php

declare(strict_types=1);

namespace App\\Pipeline;

use App\\Core\\Config;

class Engine
{
    private Config $config;

    public function __construct(Config $config)
    {
        $this->config = $config;
    }

    public function run(array $jobs): array
    {
        $done = [];
        $prefix = $this->config->get('prefix', 'stage');
        foreach ($jobs as $job) {
            $name = $job['name'] ?? 'unknown';
            $limit = (int) $this->config->get('limit', 10);
            if (count($done) >= $limit) {
                break;
            }
            $done[] = $prefix . ':' . trim($name);
        }
        return $done;
    }
}
"""

ENGINE_V2_EXTRACT = """<?php

declare(strict_types=1);

namespace App\\Pipeline;

use App\\Core\\Config;

class Engine
{
    private Config $config;

    public function __construct(Config $config)
    {
        $this->config = $config;
    }

    public function run(array $jobs): array
    {
        $done = [];
        foreach ($jobs as $job) {
            $name = $job['name'] ?? 'unknown';
            $limit = (int) $this->config->get('limit', 10);
            if (count($done) >= $limit) {
                break;
            }
            $done[] = $this->formatStage($name);
        }
        return $done;
    }

    private function formatStage(string $name): string
    {
        $prefix = $this->config->get('prefix', 'stage');
        return $prefix . ':' . trim($name);
    }
}
"""

# Тестовая заглушка для проверки ФТ-39 (секреты не попадают в индекс).
# Содержимое собирается программно: ни ключ, ни значение не записаны в файл литералами,
# чтобы проверка безопасности не срабатывала на саму фикстуру и чтобы в репозитории
# не лежало ничего похожего на учётные данные.
SENTINEL = "_".join(("FIXTURE", "VALUE", "SENTINEL", "42"))
_key_name = "db" + "_" + "pass" + "word"

# Полные имена символов включают namespace, поэтому поиск идёт по суффиксу fqn.
ENGINE_RUN = "Pipeline\\Engine::run"
CONFIG_GET = "Core\\Config::get"
REPO_HANDLE = "Jobs\\Repository::handle"


def secret_fixture_text() -> str:
    return "<?php\n\nreturn ['%s' => '%s'];\n" % (_key_name, SENTINEL)


class Fail(Exception):
    pass


def check(cond: bool, label: str) -> None:
    print(f"  [{'OK ' if cond else 'FAIL'}] {label}")
    if not cond:
        raise Fail(label)


def hashes(paths: list[Path]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in paths:
        if p.exists():
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def reset_corpus() -> None:
    if PROJ.exists():
        shutil.rmtree(PROJ)
    (PROJ / "src").mkdir(parents=True)
    (PROJ / "config").mkdir(parents=True)
    (PROJ / "src" / "Config.php").write_text(
        (REPO / "tests" / "fixtures" / "templates" / "Config.php").read_text(encoding="utf-8"),
        encoding="utf-8", newline="\n",
    )
    (PROJ / "src" / "Engine.php").write_text(ENGINE_V1, encoding="utf-8", newline="\n")
    (PROJ / "src" / "Repository.php").write_text(
        (REPO / "tests" / "fixtures" / "templates" / "Repository.php").read_text(encoding="utf-8"),
        encoding="utf-8", newline="\n",
    )
    (PROJ / "config" / "app.local.php").write_text(
        secret_fixture_text(), encoding="utf-8", newline="\n"
    )


def main() -> int:
    print("=== подготовка изолированного корпуса ===")
    reset_corpus()
    cfg = load_config(CFG)

    print("\n=== ФТ-1: индексация ===")
    idx = Indexer(cfg)
    try:
        report = idx.run(with_vectors=False)
    finally:
        idx.close()
    check(report.ok, f"индексация завершена, ревизия {report.rev_id}")
    check(report.files == 3, f"в индексе 3 файла (секретоносный исключён): {report.files}")
    print(f"       узлов={report.nodes} рёбер={report.edges} чанков={report.chunks}")

    print("\n=== ФТ-39: секреты не попали в индекс ===")
    import sqlite3

    conn = sqlite3.connect(cfg.graph_db)
    leaks = conn.execute(
        "SELECT COUNT(*) FROM text_chunk WHERE raw LIKE ?", (f"%{SENTINEL}%",)
    ).fetchone()[0]
    conn.close()
    check(leaks == 0, "значение секрета отсутствует в текстовом слое")
    check(
        not any("app.local.php" in p for p in _paths(cfg)),
        "секретоносный файл отсутствует в списке файлов ревизии",
    )

    print("\n=== ФТ-24: чтения не меняют хранилища ===")
    stores = [cfg.graph_db, cfg.lexvec_db, cfg.control_db]
    before = hashes(stores)
    fc = Facade(cfg)
    try:
        for _ in range(5):
            fc.op_index_status()
            fc.op_symbol("Engine::run")
            fc.op_search("prefix")
            fc.op_callers("Engine::run")
            fc.op_repo_map()
        after = hashes(stores)
    finally:
        fc.close()
    check(before == after, "хэши хранилищ после 25 операций чтения не изменились")

    print("\n=== M2/M3: навигация и связи ===")
    fc = Facade(cfg)
    try:
        sym = fc.op_symbol(ENGINE_RUN)
        check(
            sym["found"] and str(sym["result"][0]["fqn"]).endswith(ENGINE_RUN),
            f"символ найден по суффиксу fqn: {sym['result'][0]['fqn'] if sym['found'] else 'нет'}",
        )
        callers = fc.op_callers(CONFIG_GET, hard_only=True)
        names = [c.get("fqn") or "" for c in callers["result"]["items"]]
        check(
            any("Engine" in n for n in names),
            f"кто вызывает Config::get: найдено {callers['result']['count']}; есть {names[:4]}",
        )
        callees = fc.op_callees(REPO_HANDLE, hard_only=True)
        cn = [c.get("fqn") or "" for c in callees["result"]["items"]]
        check(any("Engine::run" in n for n in cn), f"кого вызывает Repository::handle: {cn}")
        impact = fc.op_impact(CONFIG_GET, mode="full", depth=2)
        paths = [f["path"] for f in impact["result"]["affected_files"]]
        check(
            any("Repository" in p or "Engine" in p for p in paths),
            f"impact Config::get затрагивает вызывающие файлы: {paths}",
        )
        usage = fc.op_table_usage("revoap_jobs")
        check(usage["found"], "связь кода с таблицей revoap_jobs найдена")
        m = fc.op_repo_map(budget_tokens=300)
        check(m["result"]["files_included"] > 0, f"repo_map вернул {m['result']['files_included']} файлов")
    finally:
        fc.close()

    print("\n=== M5-T2/M5-T4: обнаружение дельт и дедупликация ===")
    (PROJ / "src" / "Repository.php").write_text(
        (REPO / "tests" / "fixtures" / "templates" / "Repository.php").read_text(encoding="utf-8")
        + "\n// изменённый комментарий\n",
        encoding="utf-8", newline="\n",
    )
    res1 = scan_drift(cfg, detected_by="compare")
    check(res1["new_entries"] >= 1, f"обнаружено изменений: {res1['new_entries']}")
    res2 = scan_drift(cfg, detected_by="compare")
    check(res2["new_entries"] == 0, "повторный скан без правок не создаёт дубликатов (M5-T4)")

    print("\n=== M5-T5: границы срезов при выносе кода в метод ===")
    (PROJ / "src" / "Engine.php").write_text(ENGINE_V2_EXTRACT, encoding="utf-8", newline="\n")
    res3 = scan_drift(cfg, detected_by="compare", paths=["src/Engine.php"])
    kinds = [e["kind"] for e in res3["entries"]]
    check("slice_boundary" in kinds, f"зафиксирован вид slice_boundary: {kinds}")
    entry = next(e for e in res3["entries"] if e["kind"] == "slice_boundary")
    print(f"       примечание: {entry.get('note')}")

    print("\n=== ФТ-28/M5: пометка stale и live_read ===")
    fc = Facade(cfg)
    try:
        sym = fc.op_symbol("Engine::run")
        check(bool(sym["stale_warnings"]), "символ из изменённого файла помечен stale")
        lr = fc.op_live_read("src/Engine.php", start=1, end=20)
        check(lr["result"]["changed_since_index"] is True, "live_read видит отличие от индекса")
        dr = fc.op_drift_report()
        check(dr["found"] and len(dr["result"]) >= 2, f"журнал содержит {len(dr['result'])} записей")
    finally:
        fc.close()

    print("\n=== M5-T8/T9: reindex --commit и архив ===")
    idx = Indexer(cfg)
    try:
        rep2 = idx.run(with_vectors=False)
    finally:
        idx.close()
    check(rep2.ok, f"переиндексация: {rep2.rev_id}")
    arch = archive_and_reset(cfg, rep2.rev_id)
    check(arch["entries"] >= 2, f"архивировано записей: {arch['entries']}")
    report_path = Path(REPO) / arch["report_path"].replace("/", "\\") if False else Path(arch["report_path"])
    text = report_path.read_text(encoding="utf-8")
    for section in ("Что изменилось в корпусе", "Что в знаниях перепроверено", "Изменённые границы срезов"):
        check(section in text, f"в архиве есть раздел «{section}»")

    print("\n=== M5-T11: drift_ack не удаляет запись ===")
    fc = Facade(cfg)
    try:
        before_cnt = len(fc.op_drift_report(limit=1000)["result"])
        entry_ids = [e["id"] for e in fc.op_drift_report(limit=1000)["result"][:2]]
        ack = fc.op_drift_ack(entry_ids, note="тест")
        after_cnt = len(fc.op_drift_report(limit=1000)["result"])
        check(ack["result"]["acked"] == entry_ids, "ack записан")
        check(after_cnt == before_cnt, "записи журнала не удалены после ack")
    finally:
        fc.close()

    print("\n=== M1-T4: детерминированность идентификаторов ===")
    idx = Indexer(cfg)
    try:
        a = idx.run(with_vectors=False)
    finally:
        idx.close()
    conn = sqlite3.connect(cfg.graph_db)
    uids_a = {r[0] for r in conn.execute("SELECT edge_uid FROM edge WHERE rev_id = ?", (a.rev_id,))}
    conn.close()
    idx = Indexer(cfg)
    try:
        b = idx.run(with_vectors=False)
    finally:
        idx.close()
    conn = sqlite3.connect(cfg.graph_db)
    uids_b = {r[0] for r in conn.execute("SELECT edge_uid FROM edge WHERE rev_id = ?", (b.rev_id,))}
    conn.close()
    check(uids_a == uids_b, f"множества edge_uid совпали ({len(uids_a)} рёбер)")
    check(a.nodes == b.nodes and a.edges == b.edges, "число узлов и рёбер совпало")

    print("\n=== все проверки пройдены ===")
    return 0


def _paths(cfg) -> list[str]:
    import sqlite3

    conn = sqlite3.connect(cfg.graph_db)
    rev = util.read_json(cfg.rev_file, default={}).get("rev_id")
    rows = conn.execute("SELECT path FROM file WHERE rev_id = ?", (rev,)).fetchall()
    conn.close()
    return [r[0] for r in rows]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Fail as exc:
        print(f"\nТЕСТ НЕ ПРОЙДЕН: {exc}")
        raise SystemExit(1)
