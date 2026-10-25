"""Журнал аудита хранилища: цепочка хэшей, обнаружение порчи, подписанный экспорт.

Зачем отдельный набор. Журнал аудита — единственное доказательство «кто и когда
трогал секрет». Пока он просто таблица, его можно переписать, и внешний аудитор
не отличит правку от исходного состояния. Здесь проверяются СВОЙСТВА:

  1. каждая новая запись продолжает цепочку (prev_hash = хэш предыдущей);
  2. цепочка сходится на нетронутом журнале;
  3. порча содержимого строки обнаруживается и называет id;
  4. удаление строки обнаруживается как разрыв связи;
  5. записи без хэшей (сделанные до цепочки) считаются ОТДЕЛЬНО и не выдаются
     за проверенные;
  6. экспорт содержит журнал целиком с хэшами + итоговую строку и подписан;
  7. экспорт сам попадает в журнал.

Порча выполняется на КОПИИ базы, а не на живой: триггеры append-only на живой
базе не дают ни обновить, ни удалить строку — для проверки порчи триггеры
снимаются в копии осознанно.

Запуск: python tests/test_vault_audit.py
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ragraf import settings as settings_mod  # noqa: E402
from ragraf import signing as signing_mod  # noqa: E402
from ragraf import vault as vault_mod  # noqa: E402

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


def make_vault(directory: pathlib.Path, key: bytes):
    settings = dict(settings_mod.DEFAULTS)
    settings.update({
        "secrets_enabled": True,
        "secrets_capture_enabled": False,
        "secrets_default_visibility": "all",
        "secrets_reveal_requires_reason": False,
        "secrets_max_value_bytes": 8192,
        "secrets_allowed_agents": [],
        "secrets_expiry_warn_days": 30,
    })
    db = directory / "secrets.sqlite"
    return vault_mod.Vault(path=db, master=key, settings=settings), db


def actor(label: str = "AutoClaw"):
    return vault_mod.Actor(label=label, kind="autoclaw", via="cli", admin=True)


print("=" * 78)
print("ЖУРНАЛ АУДИТА: ЦЕПОЧКА, ПОРЧА, ПОДПИСАННЫЙ ЭКСПОРТ")
print("=" * 78)

work = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-vault-audit-"))
key = os.urandom(32)
key_dir = work / "keys"
key_dir.mkdir()
os.environ["RAGRAF_SIGNING_KEY"] = str(key_dir / "audit-signing.key")

try:
    box, db = make_vault(work, key)
    print("\n1. Цепочка строится на новых записях")
    for index in range(3):
        box.set(f"svc/secret-{index}", f"value-{index}", actor=actor(),
                kind="password", service="demo")
    box.reveal("svc/secret-0", actor=actor())
    report = box.verify_audit()
    check(report["ok"], "цепочка сходится", json.dumps(report["broken"], ensure_ascii=False))
    check(report["checked"] >= 4, "проверено записей", str(report["checked"]))
    check(report["legacy"] == 0, "записей вне цепочки нет", str(report["legacy"]))

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, prev_hash, row_hash FROM audit ORDER BY id").fetchall()
    links_ok = all(rows[i]["prev_hash"] == rows[i - 1]["row_hash"] for i in range(1, len(rows)))
    check(links_ok, "каждая запись продолжает предыдущую")
    check(rows[0]["prev_hash"] == "", "первая запись цепочки начинается с пустого хэша")

    print("\n2. Порча содержимого строки обнаруживается")
    tampered = work / "tampered.sqlite"
    shutil.copyfile(db, tampered)
    with sqlite3.connect(tampered) as conn:
        conn.execute("DROP TRIGGER IF EXISTS audit_no_update")
        conn.execute("UPDATE audit SET op='noop' WHERE id=(SELECT MIN(id) FROM audit)")
        conn.commit()
    box_t = vault_mod.Vault(path=tampered, master=key,
                            settings=dict(settings_mod.DEFAULTS, secrets_enabled=True))
    report_t = box_t.verify_audit()
    check(not report_t["ok"], "порча обнаружена")
    check(any(item["problem"].startswith("хэш записи") for item in report_t["broken"]),
          "названа причина «хэш записи не сходится»",
          json.dumps(report_t["broken"], ensure_ascii=False))

    print("\n3. Удаление строки обнаруживается как разрыв связи")
    removed = work / "removed.sqlite"
    shutil.copyfile(db, removed)
    with sqlite3.connect(removed) as conn:
        conn.execute("DROP TRIGGER IF EXISTS audit_no_delete")
        victim = conn.execute("SELECT id FROM audit ORDER BY id LIMIT 1 OFFSET 1").fetchone()[0]
        conn.execute("DELETE FROM audit WHERE id = ?", (victim,))
        conn.commit()
    box_r = vault_mod.Vault(path=removed, master=key,
                            settings=dict(settings_mod.DEFAULTS, secrets_enabled=True))
    report_r = box_r.verify_audit()
    check(not report_r["ok"], "разрыв обнаружен")
    check(any("не продолжает" in item["problem"] for item in report_r["broken"]),
          "названа причина «запись не продолжает предыдущую»",
          json.dumps(report_r["broken"], ensure_ascii=False))

    print("\n4. Записи без хэшей считаются отдельно")
    legacy = work / "legacy.sqlite"
    shutil.copyfile(db, legacy)
    with sqlite3.connect(legacy) as conn:
        conn.execute(
            "INSERT INTO audit(ts, op, name, actor, actor_kind, via, client, task,"
            " result, reason) VALUES('2026-01-01T00:00:00+0000','set','old','x','ext',"
            "'cli','','','ok','')")
        conn.commit()
    box_l = vault_mod.Vault(path=legacy, master=key,
                            settings=dict(settings_mod.DEFAULTS, secrets_enabled=True))
    report_l = box_l.verify_audit()
    check(report_l["legacy"] == 1, "запись без хэша посчитана как legacy",
          str(report_l["legacy"]))
    check(report_l["ok"], "наличие legacy-записи не объявляет журнал испорченным",
          json.dumps(report_l["broken"], ensure_ascii=False))

    print("\n5. Экспорт журнала подписан и проверяем")
    export_path = work / "audit-export.jsonl"
    result = box.export_audit(export_path, actor=actor("RAGRAF-Admin"))
    check(export_path.exists(), "файл экспорта создан", str(export_path))
    lines = export_path.read_text(encoding="utf-8").strip().split("\n")
    check(len(lines) == result["rows"] + 1, "строк: журнал + итог",
          f"{len(lines)} против {result['rows'] + 1}")
    summary = json.loads(lines[-1])
    check(summary["kind"] == "summary" and summary["format"] == "ragraf-audit-export/1",
          "последняя строка — итог с форматом")
    check(summary["ok"] and summary["head"] == result["head"],
          "итог несёт голову цепочки и результат проверки")
    check(result["signed"], "подпись поставлена", str(result.get("signature_error", "")))
    try:
        verified = signing_mod.verify_file(export_path)
        check(verified["ok"], "подпись проверяется тем же кодом")
    except Exception as exc:  # noqa: BLE001
        check(False, "подпись проверяется тем же кодом", f"{type(exc).__name__}: {exc}")

    print("\n6. Порча экспорта после подписи видна")
    export_path.write_bytes(export_path.read_bytes().replace(b'"kind": "audit"',
                                                             b'"kind": "auditt"', 1))
    try:
        signing_mod.verify_file(export_path)
        check(False, "изменённый экспорт не проходит проверку подписи")
    except signing_mod.SignatureBroken:
        check(True, "изменённый экспорт не проходит проверку подписи")
    except Exception as exc:  # noqa: BLE001
        check(False, "изменённый экспорт не проходит проверку подписи",
              f"{type(exc).__name__}: {exc}")

    print("\n7. Экспорт самого журнала записан в журнал")
    report_after = box.verify_audit()
    with sqlite3.connect(db) as conn:
        ops = [row[0] for row in conn.execute("SELECT op FROM audit ORDER BY id")]
    check("audit_export" in ops, "операция audit_export есть в журнале", str(ops[-3:]))
    check(report_after["ok"], "журнал остался целым после записи об экспорте",
          json.dumps(report_after["broken"], ensure_ascii=False))
finally:
    print(f"\nпесочница оставлена: {work}")
    os.environ.pop("RAGRAF_SIGNING_KEY", None)

print()
print(f"проверок: {PASS + FAIL}, успешно: {PASS}, провалов: {FAIL}")
sys.exit(0 if FAIL == 0 else 1)
