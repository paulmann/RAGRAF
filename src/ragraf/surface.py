"""Поверхность продукта: единственный источник чисел и описаний операций.

Зачем отдельный модуль. Числа «сколько операций, инструментов, возможностей»
считались в `tools/surface_counts.py`, но каждый потребитель (чекер, тесты,
генераторы документов) заводил свою редакцию счёта — ровно так в проекте жили
54 / 59 / 61 / 66 при фактических 60 и 67. Здесь счёт один: и число, и состав
(имена операций, их параметры, ключи ответа), и отпечаток этого состава.

Что считается и откуда:
  ops          — `def op_*` в `src/ragraf/facade.py`, с параметрами и ключами
                 результата (разбор AST, а не регулярное выражение);
  tools        — объявления `"name": "ragraf_..."` в `src/ragraf/mcp_server.py`;
  cli          — `sub.add_parser("...")` в `src/ragraf/cli.py`;
  tree/text    — `TREE_LANGS` и `LANG_BY_EXT` из `src/ragraf/config.py`;
  formats      — `FORMATS` из `src/ragraf/formats.py`;
  features     — `src/ragraf/edition_baseline.json` (состав и издание каждой);
  caps/caps_pro/caps_enterprise — возможности по уровням лицензии: community
                 входит в оба платных уровня, поэтому число Pro = 6 + все
                 возможности pro и enterprise до pro включительно;
  revision     — запись активной ревизии (`rev` в графе) или `rev.json`.

Границы, названные прямо:
  * ключи ответа берутся только из литерала `env.result = {...}`. Если операция
    собирает ответ из переменной (`env.result = data`), ключей нет и это
    названо (`returns_from` = "variable"), а не выдано за пустой ответ;
  * отпечаток (`digest`) считается БЕЗ ревизии: ревизия меняется при каждой
    переиндексации, и включённая в отпечаток она делала бы гейт красным на
    неизменном коде;
  * отсутствие индекса — не ошибка: счёт чисел от него не зависит, ревизия
    тогда `None` с причиной.

Автор: Mikhail Deynekin
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = 1

#: Ключи чисел, которые обязаны быть положительными. Нулевое число прошло бы
#: проверку «сколько есть — столько и есть», поэтому ноль — находка.
COUNT_KEYS = ("ops", "tools", "cli", "tree", "text", "formats", "caps",
              "caps_pro", "caps_enterprise")


class SurfaceError(RuntimeError):
    """Счёт невозможен по названной причине. Не «ноль вместо числа»."""


def canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def digest_of(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SurfaceError(f"не прочитан {path.name}: {type(exc).__name__}: {exc}") from exc


def _annotation(node: ast.expr | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 — подпись не должна ронять счёт
        return ""


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, Any]]:
    args = list(getattr(fn.args, "posonlyargs", [])) + list(fn.args.args)
    if args and args[0].arg in ("self", "cls"):
        args = args[1:]
    defaults = [None] * (len(args) - len(fn.args.defaults)) + list(fn.args.defaults)
    out: list[dict[str, Any]] = []
    for arg, default in zip(args, defaults):
        item: dict[str, Any] = {"name": arg.arg, "type": _annotation(arg.annotation)}
        if default is not None:
            item["default"] = _unparse(default)
        out.append(item)
    for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
        item = {"name": arg.arg, "type": _annotation(arg.annotation), "keyword": True}
        if default is not None:
            item["default"] = _unparse(default)
        out.append(item)
    return out


def _unparse(node: ast.expr) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return "?"


def _returns(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[str], str]:
    """Ключи литерала `env.result = {...}`. Второй элемент — как получены."""
    keys: list[str] = []
    literal = False
    for node in ast.walk(fn):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        for target in targets:
            if not (isinstance(target, ast.Attribute) and target.attr == "result"):
                continue
            if isinstance(value, ast.Dict):
                literal = True
                for key in value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.append(key.value)
    if keys:
        return sorted(set(keys)), "literal"
    return [], "literal" if literal else "variable"


def ops_of(root: Path) -> list[dict[str, Any]]:
    body = _read(root / "src" / "ragraf" / "facade.py")
    try:
        tree = ast.parse(body)
    except SyntaxError as exc:
        raise SurfaceError(f"facade.py не разобран: {exc}") from exc
    found: dict[str, dict[str, Any]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "Facade":
            continue
        for fn in node.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not fn.name.startswith("op_") or fn.name.startswith("op__"):
                continue
            returns, how = _returns(fn)
            found[fn.name[3:]] = {
                "name": fn.name[3:],
                "params": _params(fn),
                "returns": returns,
                "returns_from": how,
            }
    if not found:
        raise SurfaceError("в классе Facade не найдено ни одной операции op_*")
    return [found[name] for name in sorted(found)]


def _names(pattern: str, body: str) -> list[str]:
    return sorted(set(re.findall(pattern, body)))


def tools_of(root: Path) -> list[str]:
    body = _read(root / "src" / "ragraf" / "mcp_server.py")
    return _names(r'"name":\s*"(ragraf_[a-z0-9_]+)"', body)


def cli_of(root: Path) -> list[str]:
    body = _read(root / "src" / "ragraf" / "cli.py")
    return _names(r'(?m)^\s*p[a-z]+ = sub\.add_parser\(\s*\n?\s*"([a-z_]+)"', body)


def _probe(root: Path, code: str) -> Any:
    """Считать величину ОТДЕЛЬНЫМ процессом с PYTHONPATH этого корня.

    Импорт в текущем процессе взял бы уже загруженный модуль живого дерева и
    для песочницы (--root) вернул бы чужие числа — тихо и правдоподобно.
    """
    import json as json_mod
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    src = str(root / "src")
    # PYTHONPATH в этой сборке Python не применяется (вендоренный интерпретатор
    # запускается изолированно): путь вставляется в код явно. Проверено замером:
    # с PYTHONPATH `import ragraf` давал ModuleNotFoundError.
    prefix = f"import sys; sys.path.insert(0, {src!r}); "
    completed = subprocess.run([sys.executable, "-c", prefix + code], cwd=str(root),
                               capture_output=True, text=True, encoding="utf-8",
                               env=env, check=False)
    if completed.returncode != 0:
        raise SurfaceError(
            f"счёт языков/форматов не выполнен: {(completed.stderr or '').strip()[-300:]}")
    try:
        return json_mod.loads(completed.stdout)
    except ValueError as exc:
        raise SurfaceError(f"счёт вернул не JSON: {exc}") from exc


def languages_of(root: Path) -> tuple[int, int]:
    return tuple(_probe(root, "import json; from ragraf import config; "
                              "tree=set(config.TREE_LANGS); langs=set(config.LANG_BY_EXT.values()); "
                              "print(json.dumps([len(tree), len(langs-tree)]))"))


def formats_of(root: Path) -> int:
    return int(_probe(root, "import json; from ragraf import formats; "
                            "print(json.dumps(len(formats.FORMATS)))"))


def features_of(root: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    path = root / "src" / "ragraf" / "edition_baseline.json"
    try:
        data = json.loads(_read(path))
    except ValueError as exc:
        raise SurfaceError(f"edition_baseline.json не разобран: {exc}") from exc
    features = data.get("features")
    if not isinstance(features, list) or not features:
        raise SurfaceError("в edition_baseline.json нет списка features")
    order = [str(item) for item in data.get("order") or []]
    rank = {name: index for index, name in enumerate(order)}
    items = []
    for feature in features:
        items.append({
            "name": str(feature.get("name")),
            "edition": str(feature.get("edition")),
            "status": str(feature.get("status") or ""),
            "operations": sorted(str(op) for op in feature.get("operations") or []),
        })
    counts = {}
    for level in order:
        limit = rank[level]
        counts[level] = sum(1 for item in items
                            if rank.get(item["edition"], 99) <= limit)
    return items, counts


def revision_of(root: Path) -> dict[str, Any]:
    notes: list[str] = []
    candidates = [root / ".ragraf" / "index" / "graph" / "graph.sqlite",
                  root / ".ragraf" / "index" / "graph.sqlite"]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            row = conn.execute(
                "SELECT rev_id, file_count, node_count, edge_count, created_at"
                " FROM rev WHERE state = 'ready' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            conn.close()
        except sqlite3.Error as exc:
            notes.append(f"граф прочитан не полностью: {type(exc).__name__}: {exc}")
            continue
        if row:
            return {"rev_id": str(row[0]), "files": int(row[1] or 0),
                    "nodes": int(row[2] or 0), "edges": int(row[3] or 0),
                    "sealed_at": str(row[4] or ""), "source": "graph", "notes": notes}
    rev_json = root / ".ragraf" / "index" / "rev.json"
    if rev_json.is_file():
        try:
            data = json.loads(_read(rev_json))
        except ValueError:
            data = {}
        if data.get("rev_id"):
            notes.append("размеры ревизии в rev.json не записаны: числа узлов и рёбер неизвестны")
            return {"rev_id": str(data["rev_id"]), "files": None, "nodes": None,
                    "edges": None, "sealed_at": str(data.get("sealed_at") or ""),
                    "source": "rev.json", "notes": notes}
    return {"rev_id": None, "files": None, "nodes": None, "edges": None,
            "sealed_at": "", "source": "", "notes": ["индекс не найден: размеры ревизии неизвестны"]}


def collect(root: Path) -> dict[str, Any]:
    """Полная поверхность и её отпечаток. Ошибка — SurfaceError, не нули."""
    root = Path(root)
    problems: list[str] = []
    ops = ops_of(root)
    tools = tools_of(root)
    cli = cli_of(root)
    tree, text = languages_of(root)
    formats = formats_of(root)
    features, by_edition = features_of(root)
    counts = {
        "ops": len(ops),
        "tools": len(tools),
        "cli": len(cli),
        "tree": tree,
        "text": text,
        "formats": formats,
        "caps": len(features),
        "caps_pro": int(by_edition.get("pro", 0)),
        "caps_enterprise": int(by_edition.get("enterprise", 0)),
    }
    for key in COUNT_KEYS:
        if not isinstance(counts[key], int) or counts[key] < 1:
            problems.append(f"число {key} посчитано как {counts[key]!r}: образец разошёлся с кодом")
    for op in ops:
        if not op["returns"]:
            continue
    notes: list[str] = []
    hidden = [op["name"] for op in ops if not op["returns"]]
    if hidden:
        shown = ", ".join(hidden[:12])
        tail = f" и ещё {len(hidden) - 12}" if len(hidden) > 12 else ""
        notes.append(
            f"ключи ответа не видны статически у {len(hidden)} операций ({shown}{tail}):"
            " ответ собирается из переменной, а не литералом")
    documented = {op for feature in features for op in feature["operations"]}
    missing = sorted({op["name"] for op in ops} - documented)
    extra = sorted(documented - {op["name"] for op in ops})
    if missing:
        problems.append("операции без возможности издания: " + ", ".join(missing))
    if extra:
        problems.append("возможность называет отсутствующие операции: " + ", ".join(extra))
    payload = {
        "schema": SCHEMA,
        "counts": counts,
        "ops": ops,
        "tools": tools,
        "cli": cli,
        "features": features,
    }
    return {
        **payload,
        "sources": {
            "ops": "src/ragraf/facade.py",
            "tools": "src/ragraf/mcp_server.py",
            "cli": "src/ragraf/cli.py",
            "languages": "src/ragraf/config.py",
            "formats": "src/ragraf/formats.py",
            "features": "src/ragraf/edition_baseline.json",
        },
        "revision": revision_of(root),
        "digest": digest_of(payload),
        "notes": notes,
        "problems": problems,
    }


def artifact(payload: dict[str, Any]) -> dict[str, Any]:
    """Содержимое `docs/surface.json`: поверхность и ревизия отдельно.

    Ревизия лежит рядом, а не внутри отпечатка: иначе каждая переиндексация
    делала бы гейт красным на неизменном коде.
    """
    return {
        key: payload[key] for key in
        ("schema", "counts", "ops", "tools", "cli", "features", "sources", "digest")
    } | {"revision": payload.get("revision")}
