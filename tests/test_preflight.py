# -*- coding: utf-8 -*-
"""`preflight`: цена и границы до старта — поведением, а не наличием функции.

Что проверяется:

  1. операция НИЧЕГО не создаёт: полный снимок каталога (файлы и каталоги) до и
     после осмотра совпадает, `.ragraf` не появляется;
  2. счётчики файлов и распределение по расширениям совпадают с ручным подсчётом;
  3. ожидаемое время названо с погрешностью; при выборке, равной числу файлов с
     разбором, погрешность равна нулю (это замер, а не экстраполяция);
  4. дыры динамики найдены с адресом `путь:строка` (PHP `call_user_func` и
     `$obj->$method`, Python `getattr` и `eval`);
  5. правила исключения уменьшают ожидаемое число файлов, и это число названо;
  6. отказ «индекса нет» содержит причину, ОДНУ команду и признак «возможно ли
     это вообще» — на живой операции, которой индекс нужен;
  7. подсадка: если помощник вернёт пустоту, отказ перестаёт быть готовой
     командой — свойство держится на помощнике, а не на формулировке;
  8. без лицензии Pro операция отказывает, называя возможность `preflight`;
     с лицензией — работает через фасад и ничего не пишет.

Песочница строится в %TEMP% обычными файлами: индексация не нужна, потому что
`preflight` работает ДО неё.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import edition  # noqa: E402
from ragraf import init_corpus  # noqa: E402
from ragraf import preflight as preflight_mod  # noqa: E402
from ragraf import signing  # noqa: E402
from ragraf import util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

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


def make_sandbox() -> pathlib.Path:
    for base in (os.environ.get("TEMP", ""), os.environ.get("TMP", ""),
                 str(REPO / ".ragraf" / "tmp")):
        if not base:
            continue
        try:
            pathlib.Path(base).mkdir(parents=True, exist_ok=True)
            probe = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-x5-pre-", dir=base))
            (probe / "probe.txt").write_text("ok", encoding="utf-8")
            (probe / "probe.txt").unlink()
            return probe
        except OSError:
            continue
    raise SystemExit("нет каталога для песочницы")


def snapshot(root: pathlib.Path, *, skip: tuple[str, ...] = ()) -> list[str]:
    """Снимок каталога: файлы и каталоги с относительными путями.

    `skip` нужен фасадной проверке: открытие фасада само создаёт внутри
    `.ragraf` служебные файлы (журнал, `-wal`), и без исключения сравнение
    обвиняло бы `preflight` в чужой записи.
    """
    out: list[str] = []
    for path in sorted(root.rglob("*")):
        relative = util.to_posix(path.relative_to(root))
        if any(relative == item.rstrip("/") or relative.startswith(item)
               for item in skip):
            continue
        out.append(relative + ("/" if path.is_dir() else ""))
    return out


PHP_PY = {
    "src/controller.php": """<?php
// AUTO-GENERATED FILE. DO NOT EDIT.
namespace App;

class Controller
{
    public function dispatch($name, $handler)
    {
        call_user_func($handler, $name);
        $this->{$name}();
        return $this->handle($name);
    }
}
""",
    "src/plain.php": """<?php
namespace App;

class Plain
{
    public function run()
    {
        return 42;
    }
}
""",
    "src/dyn.py": """import importlib


def call(name):
    fn = getattr(__import__('os'), name, None)
    return eval(name)
""",
    "src/plain.py": """def add(a, b):
    return a + b
""",
    "migrations/0001_init.php": """<?php
return ['up' => 'CREATE TABLE t (id INT)'];
""",
    "docs/readme.md": "# Проект\n\nТекст без кода.\n",
}


def build(root: pathlib.Path) -> None:
    for rel, text in PHP_PY.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


sandbox = make_sandbox()
print(f"песочница: {sandbox}")
_ENV = (edition.BASELINE_ENV, edition.LICENSE_ENV, edition.DECLARED_ENV,
        edition.PUBKEY_ENV)
ENV_SAVED = {name: os.environ.get(name) for name in _ENV}
os.environ[edition.LICENSE_ENV] = str(sandbox / "absent.license")
os.environ.pop(edition.DECLARED_ENV, None)

try:
    project = sandbox / "corpus"
    build(project)
    before = snapshot(project)
    print("=" * 78)
    print("1. ОСМОТР НИЧЕГО НЕ СОЗДАЁТ")
    print("=" * 78)
    report = preflight_mod.scan(project, sample=50)
    after = snapshot(project)
    check(report.get("exists") is True, "каталог осмотрен", str(report.get("reason")))
    check(before == after, "снимок каталога до и после совпадает",
          f"было {len(before)}, стало {len(after)}")
    check(not (project / ".ragraf").exists(), "каталог .ragraf не создан")
    check(not any(".ragraf" in item for item in after), "в снимке нет служебных файлов")

    print()
    print("=" * 78)
    print("2. СЧЁТЧИКИ И РАСПРЕДЕЛЕНИЕ")
    print("=" * 78)
    files = report.get("files") or {}
    manual = sorted(p for p in project.rglob("*") if p.is_file())
    check(int(files.get("total") or 0) == len(manual),
          "число файлов совпадает с ручным подсчётом",
          f"{files.get('total')} против {len(manual)}")
    by_ext = files.get("by_ext") or {}
    check(int(by_ext.get(".php") or 0) == 3 and int(by_ext.get(".py") or 0) == 2
          and int(by_ext.get(".md") or 0) == 1,
          "распределение по расширениям совпадает с ручным",
          json.dumps(by_ext, ensure_ascii=False))
    # Шесть, а не пять: миграция тоже написана на PHP и обязана попасть в счёт
    # файлов кода — иначе «сколько файлов разбирать» было бы занижено.
    check(int(files.get("code_files") or 0) == 6,
          "файлы кода посчитаны отдельно от прочих", str(files.get("code_files")))
    check(int((report.get("generated") or {}).get("count") or 0) == 1,
          "сгенерированный файл найден по маркеру",
          json.dumps(report.get("generated"), ensure_ascii=False))
    migrations = report.get("migrations") or {}
    check(int(migrations.get("files") or 0) >= 1 and "migrations" in migrations.get("dirs", []),
          "миграции найдены по пути", json.dumps(migrations, ensure_ascii=False))

    print()
    print("=" * 78)
    print("3. ВРЕМЯ: ЗАМЕР И ПОГРЕШНОСТЬ")
    print("=" * 78)
    timing = report.get("timing") or {}
    if timing.get("available"):
        check(timing.get("error_promille") is not None,
              "погрешность названа числом", json.dumps(timing, ensure_ascii=False)[:160])
        check(int(timing.get("estimated_total_ms") or 0) > 0,
              "ожидаемое время положительно", str(timing.get("estimated_total_ms")))
        full = preflight_mod.scan(project, sample=1000)
        full_timing = full.get("timing") or {}
        check(full_timing.get("available") is True
              and int(full_timing.get("error_promille") or 0) == 0,
              "выборка на весь набор — замер, погрешность 0",
              json.dumps(full_timing, ensure_ascii=False)[:160])
        check(int(full_timing.get("sample_files") or 0)
              == int(full_timing.get("parseable_files") or 0),
              "разобраны все файлы с разбором деревом", str(full_timing.get("sample_files")))
    else:
        check(False, "время оценено", str(timing.get("reason")))

    print()
    print("=" * 78)
    print("4. СЛЕПЫЕ ЗОНЫ ДИНАМИКИ С АДРЕСАМИ")
    print("=" * 78)
    dynamic = report.get("dynamic") or {}
    kinds = dynamic.get("by_kind") or {}
    check(int(kinds.get("call_user_func") or 0) >= 1,
          "PHP call_user_func найден", json.dumps(kinds, ensure_ascii=False))
    check(int(kinds.get("variable_method") or 0) >= 1,
          "PHP $obj->$method найден", json.dumps(kinds, ensure_ascii=False))
    check(int(kinds.get("getattr") or 0) >= 1 and int(kinds.get("eval") or 0) >= 1,
          "Python getattr и eval найдены", json.dumps(kinds, ensure_ascii=False))
    findings = dynamic.get("findings") or []
    check(bool(findings) and all(item.get("path") and int(item.get("line") or 0) > 0
                                 for item in findings),
          "у каждого места есть адрес путь:строка",
          json.dumps(findings[:1], ensure_ascii=False))
    addresses = {item.get("address") for item in findings}
    check("src/controller.php:9" in addresses or any(
        str(a).startswith("src/controller.php:") for a in addresses),
        "адрес указывает на файл с динамическим вызовом", str(sorted(addresses)[:3]))
    check("plain.php" not in " ".join(str(a) for a in addresses),
          "чистый файл не попал в находки")

    print()
    print("=" * 78)
    print("5. ЭФФЕКТ ИСКЛЮЧЕНИЙ")
    print("=" * 78)
    all_files = int((report.get("files") or {}).get("total") or 0)
    narrowed = preflight_mod.scan(project, sample=10, skip_globs=("**/*.md", "**/migrations/**"))
    narrowed_total = int((narrowed.get("files") or {}).get("total") or 0)
    check(narrowed_total < all_files, "правила исключения уменьшают число файлов",
          f"{all_files} → {narrowed_total}")
    check(int((narrowed.get("exclusions") or {}).get("skipped_by_rule") or 0) == 2,
          "исключённые по правилу файлы посчитаны",
          json.dumps(narrowed.get("exclusions"), ensure_ascii=False))

    print()
    print("=" * 78)
    print("6. ОТКАЗ: ПРИЧИНА, ОДНА КОМАНДА, ВОЗМОЖНОСТЬ")
    print("=" * 78)
    config_path = sandbox / "corpus.yaml"
    init_corpus.create_config(project, project="corpus", config_path=config_path)
    cfg = load_config(config_path)

    # Сначала — отказ ДО сборки: фасад не открывается, и текст исключения обязан
    # нести команду, а не только перечень отсутствующих файлов.
    from ragraf.facade import IndexNotBuilt  # noqa: PLC0415 — нужен только здесь
    try:
        Facade(cfg, telemetry=False, watch=False)
        early = ""
    except IndexNotBuilt as exc:
        early = str(exc)
    check("python tools/ragraf.py" in early and "index" in early,
          "исключение при сборке фасада называет команду", early[:120])

    # Теперь индекс есть, но действующей ревизии нет — так отказ возникает уже
    # после сборки, и его форма обязана быть той же.
    from ragraf.indexer import Indexer  # noqa: PLC0415 — только для фикстуры
    indexer = Indexer(cfg)
    try:
        report = indexer.run(with_vectors=False)
    finally:
        indexer.close()
    check(report.ok, "индекс песочного корпуса построен", str(report.notes[-2:]))
    import sqlite3  # noqa: PLC0415 — правка состояния фикстуры
    conn = sqlite3.connect(str(cfg.graph_db))
    try:
        conn.execute("UPDATE rev SET state = 'aborted'")
        conn.commit()
    finally:
        conn.close()
    # `rev.json` читается раньше базы (см. ControlStore.current_rev), поэтому
    # одного состояния в базе мало: паспорт ревизии тоже убирается.
    rev_file = pathlib.Path(cfg.rev_file)
    rev_backup = rev_file.with_suffix(".json.x5-backup")
    if rev_file.exists():
        rev_file.replace(rev_backup)
    facade = Facade(load_config(config_path), telemetry=False, watch=False)
    try:
        refusal = facade.op_search("контроллер")
    finally:
        facade.close()
    advice = refusal.get("next_action") or {}
    check(refusal.get("found") is False, "операция без индекса отказала",
          str(refusal.get("error")))
    check(bool(advice.get("problem")) and bool(advice.get("why")),
          "отказ называет, что не так и почему", json.dumps(advice, ensure_ascii=False)[:160])
    check("ragraf.py" in str(advice.get("action")) and "index" in str(advice.get("action")),
          "отказ даёт ОДНУ готовую команду", str(advice.get("action")))
    check(advice.get("possible") is True, "признак «возможно ли это» назван")
    check("python tools/ragraf.py" in str(refusal.get("error")),
          "текст ошибки тоже содержит команду", str(refusal.get("error")))

    print()
    print("=" * 78)
    print("7. ПОДСАДКА: СВОЙСТВО ДЕРЖИТСЯ НА ПОМОЩНИКЕ")
    print("=" * 78)
    real = util.next_action
    util.next_action = lambda *a, **k: {}  # подсадка: помощник «ничего не советует»
    try:
        facade = Facade(load_config(config_path), telemetry=False, watch=False)
        try:
            blind = facade.op_search("контроллер")
        finally:
            facade.close()
    finally:
        util.next_action = real
    check(not (blind.get("next_action") or {}).get("action"),
          "подсадка: без помощника готовой команды в отказе нет (свойство 6 ловит)")
    check(bool(advice.get("action")),
          "на настоящем помощнике команда на месте")

    print()
    print("=" * 78)
    print("8. ИЗДАНИЕ: ОТКАЗ И РАБОТА")
    print("=" * 78)
    community = edition.resolve()
    decision = community.decide("preflight")
    check(decision.get("allowed") is False
          and "preflight" in str(decision.get("reason") or ""),
          "Community: отказ называет возможность preflight", str(decision.get("reason")))
    license_key = sandbox / "license.key"
    signing.ensure_key(license_key)
    license_file = sandbox / "edition.license"
    license_file.write_bytes(json.dumps(
        {"schema": 1, "edition": "pro", "licensee": "проверка",
         "issued_at": "2026-01-01", "expires_at": "2099-12-31"},
        ensure_ascii=False, indent=2).encode("utf-8"))
    signing.sign_file(license_file, license_key)
    os.environ[edition.LICENSE_ENV] = str(license_file)
    os.environ[edition.PUBKEY_ENV] = signing.public_key_b64(license_key)
    conn = sqlite3.connect(str(cfg.graph_db))
    try:
        conn.execute("UPDATE rev SET state = 'ready'")
        conn.commit()
    finally:
        conn.close()
    if rev_backup.exists():
        rev_backup.replace(rev_file)
    before_live = snapshot(project, skip=(".ragraf/",))
    facade = Facade(load_config(config_path), telemetry=False, watch=False)
    try:
        payload = facade.op_preflight(sample=5)
    finally:
        facade.close()
    after_live = snapshot(project, skip=(".ragraf/",))
    check(payload.get("found") is True, "с лицензией Pro операция работает",
          str(payload.get("error")))
    check(payload.get("timing_available") is True, "время оценено через фасад",
          str(payload.get("estimated_total_ms")))
    check(before_live == after_live, "фасадная форма тоже ничего не создала",
          f"было {len(before_live)}, стало {len(after_live)}")

finally:
    for name, value in ENV_SAVED.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    shutil.rmtree(sandbox, ignore_errors=True)

print()
print("=" * 78)
print(f"ИТОГ: проверок {PASS}, успешно {PASS - FAIL}, провалов {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
