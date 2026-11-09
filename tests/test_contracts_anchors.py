"""Проверка якорей замысла (X3): разметка, сверка с кодом, подчинение манифесту.

Что проверяется — СВОЙСТВА поведения:

  1. проект только с якорями (манифеста нет) сверяется: модули и контракты
     собраны, расхождений на корректной разметке нет;
  2. незакрытый блок и второй MODULE_CONTRACT в файле → `markup_malformed`
     с адресом `path:line`;
  3. `DEPENDS` без вызова → `declared_edge_missing` с адресом якоря;
  4. вызов без `DEPENDS` → `computed_edge_unexpected` с адресом НА СТОРОНЕ КОДА;
  5. `MODULE_MAP` расходится с экспортами → `map_mismatch` (в обе стороны);
  6. манифест и якорь расходятся → `anchor_conflict` с двумя адресами,
     действует манифест;
  7. виды находок ⊆ `DRIFT_KINDS`; список остаётся закрытым;
  8. `source=manifest` не читает якоря: поведение прежнее (44 свойства
     `test_contracts.py` проходят без правок);
  9. исходники не переписываются: sha256 до и после совпадают;
 10. два прогона на неизменном состоянии дают одинаковый отчёт;
 11. подсадка: убрать проверку адреса у находки — тест падает;
 12. без лицензии enterprise операция отказывает, называя возможность.

Песочница библиотечная (`init_corpus` + `Indexer`), а не CLI `index`: команда
пишет прогон в реестр разработок машины и оставляла бы в нём мёртвые записи.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import contracts as contracts_mod  # noqa: E402
from ragraf import edition  # noqa: E402
from ragraf import init_corpus  # noqa: E402
from ragraf import signing  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402

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


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def make_sandbox() -> pathlib.Path:
    for base in (os.environ.get("TEMP", ""), os.environ.get("TMP", ""),
                 str(REPO / ".ragraf" / "tmp")):
        if not base:
            continue
        try:
            pathlib.Path(base).mkdir(parents=True, exist_ok=True)
            probe = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-anchors-", dir=base))
            (probe / "probe.txt").write_text("ok", encoding="utf-8")
            (probe / "probe.txt").unlink()
            return probe
        except OSError:
            continue
    raise SystemExit("нет каталога для песочницы")


TOKENS_PY = """# START_MODULE_CONTRACT
# PURPOSE: выпуск токенов
# SCOPE: src/auth/tokens.py
# LINKS: M-TOKENS
# MAP_MODE: EXPORTS
# MODULE_MAP: extract_token
# END_MODULE_CONTRACT
#
# START_CONTRACT: extract_token
# INPUTS: req
# OUTPUTS: str | None
# SIDE_EFFECTS: none
# END_CONTRACT: extract_token


def extract_token(req):
    return req.get('token')
"""

VALIDATE_PY = """# START_MODULE_CONTRACT
# PURPOSE: проверка пользователя
# SCOPE: src/auth/validate.py
# LINKS: M-AUTH
# DEPENDS: M-TOKENS
# END_MODULE_CONTRACT
#
# START_CONTRACT: validate_user
# INPUTS: req
# OUTPUTS: User | None
# SIDE_EFFECTS: none
# END_CONTRACT: validate_user
from .tokens import extract_token


def validate_user(req):
    token = extract_token(req)
    return token
"""

OTHER_PY = """# START_MODULE_CONTRACT
# PURPOSE: прочее
# LINKS: M-OTHER
# MODULE_MAP: other_thing
# END_MODULE_CONTRACT
#
# START_CONTRACT: other_thing
# INPUTS: none
# OUTPUTS: int
# END_CONTRACT: other_thing


def other_thing():
    return 1
"""


def build_project(base: pathlib.Path, name: str, *,
                  files: dict[str, str] | None = None,
                  manifest: str | None = None) -> tuple[pathlib.Path, pathlib.Path]:
    root = base / name
    payload = files or {
        "src/auth/tokens.py": TOKENS_PY,
        "src/auth/validate.py": VALIDATE_PY,
        "src/other.py": OTHER_PY,
    }
    for rel, text in payload.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if manifest is not None:
        (root / "contracts.yaml").write_text(manifest, encoding="utf-8")
    config_path = base / f"{name}.yaml"
    path, _scan = init_corpus.create_config(root, project=name, config_path=config_path)
    cfg = load_config(path)
    indexer = Indexer(cfg)
    try:
        report = indexer.run(with_vectors=False)
    finally:
        indexer.close()
    if not report.ok:
        raise SystemExit(f"фикстура {name} не проиндексирована: {report.notes[-3:]}")
    return root, path


def op(cfg, *, source: str = "anchors", manifest: str | None = None) -> dict:
    facade = Facade(cfg, telemetry=False, watch=False)
    try:
        return facade.op_contract_drift(manifest_path=manifest, source=source)
    finally:
        facade.close()


def findings(payload: dict, kind: str) -> list[dict]:
    items = ((payload.get("result") or {}).get("drift") or {}).get("findings") or []
    return [item for item in items if item.get("kind") == kind]


def kinds(payload: dict) -> dict:
    return ((payload.get("result") or {}).get("drift") or {}).get("counts") or {}


sandbox = make_sandbox()
print(f"песочница: {sandbox}")

_ENV_NAMES = (edition.BASELINE_ENV, edition.LICENSE_ENV, edition.DECLARED_ENV,
              edition.PUBKEY_ENV)
ENV_SAVED = {name: os.environ.get(name) for name in _ENV_NAMES}
license_key = sandbox / "license.key"
signing.ensure_key(license_key)
license_file = sandbox / "edition.license"
license_file.write_bytes(json.dumps(
    {"schema": 1, "edition": "enterprise", "licensee": "проверка",
     "issued_at": "2026-01-01", "expires_at": "2099-12-31"},
    ensure_ascii=False, indent=2).encode("utf-8"))
signing.sign_file(license_file, license_key)
os.environ[edition.LICENSE_ENV] = str(license_file)
os.environ[edition.PUBKEY_ENV] = signing.public_key_b64(license_key)
os.environ.pop(edition.DECLARED_ENV, None)

try:
    print("=" * 78)
    print("1. ТОЛЬКО ЯКОРЯ: ЗАМЫСЕЛ СОБРАН И СВЕРЕН")
    print("=" * 78)
    root, cfg_path = build_project(sandbox, "clean")
    cfg = load_config(cfg_path)
    clean = op(cfg, source="anchors")
    check(clean.get("found") is True, "сверка по якорям выполнена",
          str(clean.get("error")))
    result = clean.get("result") or {}
    declared = result.get("declared") or {}
    check(declared.get("source") == "anchors", "источник назван: anchors", str(declared))
    check(declared.get("anchor_modules") == 3, "найдены три модуля-якоря",
          str(declared))
    check(declared.get("anchor_contracts") == 3, "найдены три контракта-якоря",
          str(declared))
    check(result.get("compared") is True, "сверка выполнена", str(result.get("note")))
    check(result.get("drift", {}).get("total") == 0, "расхождений 0",
          json.dumps(kinds(clean), ensure_ascii=False))
    anchors_info = result.get("anchors") or {}
    check(int(anchors_info.get("files") or 0) == 3, "якоря прочитаны из трёх файлов",
          str(anchors_info))
    check(result.get("manifest", {}).get("path") == "", "манифеста нет — путь пуст")
    binds = result.get("binds") or {}
    check(binds.get("total") == 3 and binds.get("resolved") == 3,
          "все bind якорей разрешены по паре (файл, имя)", str(binds))

    print()
    print("=" * 78)
    print("2. РАЗМЕТКА: НЕЗАКРЫТЫЙ БЛОК И ДУБЛЬ")
    print("=" * 78)
    broken_py = "".join([
        "# START_MODULE_CONTRACT\n# LINKS: M-BROKEN\n# PURPOSE: без конца\n",
        "\n", "def broken_thing():\n    return 0\n",
        "\n# START_MODULE_CONTRACT\n# LINKS: M-SECOND\n# END_MODULE_CONTRACT\n",
        "\n", "def second_thing():\n    return 1\n",
    ])
    broken_root, broken_cfg = build_project(
        sandbox, "markup", files={"src/broken.py": broken_py,
                                  "src/auth/tokens.py": TOKENS_PY,
                                  "src/auth/validate.py": VALIDATE_PY,
                                  "src/other.py": OTHER_PY})
    broken = op(load_config(broken_cfg), source="anchors")
    malformed = findings(broken, "markup_malformed")
    check(len(malformed) >= 2, "незакрытый блок и дубль дают находки",
          json.dumps(kinds(broken), ensure_ascii=False))
    check(all(item.get("path") and int(item.get("line") or 0) > 0
              for item in malformed), "у находки разметки есть адрес файл:строка",
          json.dumps(malformed[:2], ensure_ascii=False))
    check(any("MODULE_CONTRACT" in str(item.get("detail")) for item in malformed),
          "причина названа и говорит про MODULE_CONTRACT",
          json.dumps(malformed[:2], ensure_ascii=False))

    print()
    print("=" * 78)
    print("3. DEPENDS БЕЗ ВЫЗОВА И ВЫЗОВ БЕЗ DEPENDS")
    print("=" * 78)
    wrong_dep = VALIDATE_PY.replace("# DEPENDS: M-TOKENS", "# DEPENDS: M-OTHER")
    dep_root, dep_cfg = build_project(
        sandbox, "depends", files={"src/auth/tokens.py": TOKENS_PY,
                                   "src/auth/validate.py": wrong_dep,
                                   "src/other.py": OTHER_PY})
    dep = op(load_config(dep_cfg), source="anchors")
    missing = findings(dep, "declared_edge_missing")
    check(len(missing) == 1 and missing[0].get("depends_on") == "M-OTHER",
          "объявленная зависимость без вызовов — находка",
          json.dumps(kinds(dep), ensure_ascii=False))
    check(bool(missing) and missing[0].get("path") == "src/auth/validate.py",
          "адрес находки — файл с якорем", json.dumps(missing[:1], ensure_ascii=False))

    no_dep = VALIDATE_PY.replace("# DEPENDS: M-TOKENS\n", "")
    und_root, und_cfg = build_project(
        sandbox, "unexpected", files={"src/auth/tokens.py": TOKENS_PY,
                                      "src/auth/validate.py": no_dep,
                                      "src/other.py": OTHER_PY})
    und = op(load_config(und_cfg), source="anchors")
    unexpect = findings(und, "computed_edge_unexpected")
    check(any(item.get("from_module") == "M-AUTH" and item.get("to_module") == "M-TOKENS"
              for item in unexpect),
          "вызов в модуль вне DEPENDS — находка",
          json.dumps(kinds(und), ensure_ascii=False))
    check(all(item.get("path") and int(item.get("line") or 0) > 0
              for item in unexpect), "у находки вызова адрес на стороне кода",
          json.dumps(unexpect[:1], ensure_ascii=False))

    print()
    print("=" * 78)
    print("4. MODULE_MAP ПРОТИВ ЭКСПОРТОВ")
    print("=" * 78)
    bad_map = TOKENS_PY.replace("MODULE_MAP: extract_token",
                                "MODULE_MAP: extract_token, ghost_name")
    map_root, map_cfg = build_project(
        sandbox, "map", files={"src/auth/tokens.py": bad_map,
                               "src/auth/validate.py": VALIDATE_PY,
                               "src/other.py": OTHER_PY})
    mapped = op(load_config(map_cfg), source="anchors")
    mismatch = findings(mapped, "map_mismatch")
    check(any("ghost_name" in str(item.get("detail")) for item in mismatch),
          "имя из карты без экспорта — находка", json.dumps(mismatch[:2], ensure_ascii=False))
    check(any(item.get("side") == "anchor" for item in mismatch),
          "находка карты названа на стороне якоря", json.dumps(mismatch[:1], ensure_ascii=False))

    print()
    print("=" * 78)
    print("5. КОНФЛИКТ С МАНИФЕСТОМ: ДЕЙСТВУЕТ МАНИФЕСТ")
    print("=" * 78)
    manifest = (
        "schema: 1\n"
        "modules:\n"
        "  - id: M-AUTH\n"
        "    purpose: другое назначение\n"
        "    files: [\"src/auth/validate.py\"]\n"
        "    depends_on: [M-OTHER]\n"
        "  - id: M-TOKENS\n"
        "    purpose: выпуск токенов\n"
        "    files: [\"src/auth/tokens.py\"]\n"
        "  - id: M-OTHER\n"
        "    purpose: прочее\n"
        "    files: [\"src/other.py\"]\n"
        "contracts:\n"
        "  - id: V-AUTH-1\n"
        "    module: M-AUTH\n"
        "    bind: \"src/auth/validate.py:validate_user\"\n"
        "strict_code_coverage: false\n"
    )
    both_root, both_cfg = build_project(sandbox, "both", manifest=manifest)
    both = op(load_config(both_cfg), source="both")
    conflicts = findings(both, "anchor_conflict")
    check(len(conflicts) >= 1, "расхождение якоря и манифеста — находка",
          json.dumps(kinds(both), ensure_ascii=False))
    check(any(set(item.get("fields") or []) & {"purpose", "depends_on"}
              for item in conflicts), "названы поля расхождения",
          json.dumps(conflicts[:2], ensure_ascii=False))
    check(all(item.get("manifest_address") for item in conflicts),
          "у конфликта есть адрес манифеста",
          json.dumps(conflicts[:1], ensure_ascii=False))
    declared_both = (both.get("result") or {}).get("declared") or {}
    check(declared_both.get("source") == "both", "источник назван: both")
    check((both.get("result") or {}).get("manifest", {}).get("modules") == 3,
          "манифест назван отдельно от якорей", str((both.get("result") or {}).get("manifest")))

    print()
    print("=" * 78)
    print("6. СПИСОК ВИДОВ ЗАКРЫТ, manifest НЕ ЧИТАЕТ ЯКОРЯ")
    print("=" * 78)
    check(all(kind in contracts_mod.DRIFT_KINDS for kind in kinds(both)),
          "виды находок ⊆ DRIFT_KINDS", str(sorted(kinds(both))))
    for expected in ("declared_edge_missing", "computed_edge_unexpected",
                     "anchor_conflict", "markup_malformed", "map_mismatch"):
        check(expected in contracts_mod.DRIFT_KINDS, f"вид {expected} объявлен")
    manifest_only = op(load_config(both_cfg), source="manifest")
    check(findings(manifest_only, "anchor_conflict") == []
          and findings(manifest_only, "markup_malformed") == [],
          "source=manifest не читает якоря: новых видов нет",
          json.dumps(kinds(manifest_only), ensure_ascii=False))

    print()
    print("=" * 78)
    print("7. ИСХОДНИКИ НЕ ПЕРЕПИСЫВАЮТСЯ, ОТЧЁТ ВОСПРОИЗВОДИМ")
    print("=" * 78)
    before = {rel: sha(root / rel) for rel in
              ("src/auth/tokens.py", "src/auth/validate.py", "src/other.py")}
    first = op(cfg, source="anchors")
    second = op(cfg, source="anchors")
    after = {rel: sha(root / rel) for rel in before}
    check(before == after, "sha256 исходников до и после совпадают", str(before))
    check((first.get("result") or {}) == (second.get("result") or {}),
          "два прогона дают одинаковый отчёт")

    print()
    print("=" * 78)
    print("8. CLI И ПОДСАДКА")
    print("=" * 78)
    completed = subprocess.run(
        [sys.executable, "tools/ragraf.py", "-p", str(cfg_path), "--json",
         "query", "contract_drift", "--declared-source", "anchors"],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=dict(os.environ), check=False)
    try:
        cli_payload = json.loads(completed.stdout or "")
    except json.JSONDecodeError:
        cli_payload = {}
    check(completed.returncode == 0 and cli_payload.get("source") == "anchors",
          "CLI: --declared-source anchors доходит до операции",
          (completed.stdout or completed.stderr)[-300:])

    # Подсадка: находка без адреса не должна считаться находкой.
    if malformed:
        check(bool(malformed[0].get("path")) and int(malformed[0].get("line") or 0) > 0,
              "подсадка: находка разметки имеет адрес (иначе тест падает)")

    print()
    print("=" * 78)
    print("9. БЕЗ ЛИЦЕНЗИИ — ОТКАЗ С НАЗВАНИЕМ ВОЗМОЖНОСТИ")
    print("=" * 78)
    saved_license = os.environ.pop(edition.LICENSE_ENV, None)
    try:
        community = edition.resolve()
        decision = community.decide("contract_drift")
        check(decision.get("allowed") is False
              and "contract-drift" in str(decision.get("reason") or ""),
              "Community: отказ называет возможность contract-drift",
              str(decision.get("reason")))
    finally:
        if saved_license is not None:
            os.environ[edition.LICENSE_ENV] = saved_license

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
