# -*- coding: utf-8 -*-
"""Импорты JavaScript/TypeScript: что файл на самом деле подключает.

Зачем отдельный модуль. Вызов вида `renderExecutiveDashboard()` из другого файла
до 22.11.2026 не связывался с определением: разбор даёт имя вызова, а откуда оно
пришло — не знает, поэтому `callers` отвечал «никто», а `dead_code` называл
живой экспорт мёртвым (замечание клиента, воспроизведено на трёхфайловом
корпусе). Связь между файлами задаётся строкой импорта, и читается она здесь.

Границы, названные прямо:
  * разбираются формы `import default`, `import {a, b as c}`, `import * as ns`,
    `import type`, `export {a} from`, `export * from`, `require("...")`;
  * относительные спефицикаторы (`./x`, `../x`) разрешаются в файл корпуса с
    расширениями `.ts .tsx .mts .cts .js .jsx .mjs .cjs` и `/index.*`;
  * пакетные спефицикаторы (`react`, `@scope/pkg`) считаются внешними: их кода в
    корпусе нет, и выдумывать связь нельзя;
  * алиасы `tsconfig.paths` не разбираются — это отдельный шаг, и он назван в
    плане, а не выдан за сделанное.
"""
from __future__ import annotations

import posixpath
import re

#: Расширения, которые пробуются при разрешении относительного импорта.
EXTENSIONS: tuple[str, ...] = (
    ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".astro",
)

_IMPORT = re.compile(
    r"""^[ \t]*import\s+
        (?:
            type\s+                                  # import type {A} from "…"
        )?
        (?P<clause>[^;'"]+?)\s+from\s+
        (?P<module>['"][^'"]+['"])
      | ^[ \t]*import\s*(?P<bare>['"][^'"]+['"])     # import "./side-effect"
      | ^[ \t]*export\s+
        (?P<export_clause>[*{}\w,\s$]+?)\s+from\s+
        (?P<export_module>['"][^'"]+['"])
      | (?P<require>require)\(\s*(?P<require_module>['"][^'"]+['"])\s*\)
    """,
    re.M | re.X,
)


def _strip_quotes(text: str) -> str:
    return text.strip().strip("'").strip('"')


def _parse_clause(clause: str) -> tuple[list[tuple[str, str]], str | None, bool]:
    """Разобрать часть между `import` и `from`.

    Возвращает `(имена, default-имя, звезда)`; имя — пара
    `(как названо в модуле, как названо здесь)`.
    """
    names: list[tuple[str, str]] = []
    default: str | None = None
    star = False
    rest = clause.strip()
    # `import * as ns from` — весь модуль под одним именем.
    star_match = re.match(r"\*\s+as\s+([A-Za-z_$][\w$]*)", rest)
    if star_match:
        star = True
        names.append(("*", star_match.group(1)))
        rest = rest[star_match.end():].strip(" ,")
    # `import def, {a}` — имя по умолчанию идёт первым.
    if rest and not rest.startswith("{"):
        head = rest.split(",", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_$][\w$]*", head):
            default = head
            rest = rest[len(head):].strip(" ,")
    braced = re.search(r"\{([^}]*)\}", rest)
    if braced:
        for item in braced.group(1).split(","):
            part = item.strip()
            if not part:
                continue
            if " as " in part:
                imported, local = part.split(" as ", 1)
                names.append((imported.strip(), local.strip()))
            else:
                names.append((part, part))
    return names, default, star


def parse_imports(text: str) -> list[dict]:
    """Импорты файла: `[{module, names, default, star}]` по порядку появления."""
    found: list[dict] = []
    for match in _IMPORT.finditer(text):
        if match.group("module"):
            module = _strip_quotes(match.group("module"))
            names, default, star = _parse_clause(match.group("clause") or "")
            found.append({"module": module, "names": names, "default": default,
                          "star": star, "kind": "import"})
        elif match.group("bare"):
            found.append({"module": _strip_quotes(match.group("bare")), "names": [],
                          "default": None, "star": False, "kind": "side_effect"})
        elif match.group("export_module"):
            module = _strip_quotes(match.group("export_module"))
            names, default, star = _parse_clause(match.group("export_clause") or "")
            found.append({"module": module, "names": names, "default": default,
                          "star": star, "kind": "reexport"})
        elif match.group("require_module"):
            module = _strip_quotes(match.group("require_module"))
            names, default, star = _parse_clause(
                (match.string[:match.start()] or "").rsplit("=", 1)[0][-60:])
            found.append({"module": module, "names": names, "default": default,
                          "star": star, "kind": "require"})
    return found


def resolve_module(importer: str, module: str, files: set[str]) -> str | None:
    """Путь файла корпуса для относительного импорта; `None` — внешний пакет."""
    if not module.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), module))
    candidates = [base] + [base + ext for ext in EXTENSIONS]
    # Каталог с index-файлом: `import x from "./lib"` при `lib/index.ts`.
    candidates += [f"{base}/index{ext}" for ext in EXTENSIONS]
    for candidate in candidates:
        if candidate in files:
            return candidate
    return None


def import_bindings(imports: list[dict], importer: str, files: set[str]) -> dict[str, str]:
    """`местное имя → путь файла` для имён, пришедших из относительных импортов.

    Имя по умолчанию (`import Foo from "./x"`) связывается как `default`: файл
    корпуса объявляет его через `export default`, и привязка идёт к любому
    определению с этим именем в целевом файле — см. `bind_calls`.
    """
    mapping: dict[str, str] = {}
    for record in imports:
        target = resolve_module(importer, str(record.get("module") or ""), files)
        if target is None:
            continue
        for _imported, local in record.get("names") or []:
            mapping[local] = target
        if record.get("default"):
            mapping[str(record["default"])] = target
        if record.get("star"):
            continue
    return mapping
