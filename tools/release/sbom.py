"""SBOM в формате CycloneDX 1.5 из фактически установленных распределений.

Зачем из `importlib.metadata`, а не из `pyproject.toml`. Перечень зависимостей в
файле проекта — это НАМЕРЕНИЕ («что просили»), а SBOM обязан описывать ФАКТ («что
стоит и с какой версией»). Разница измерена: `PyNaCl` установлен и используется
хранилищем секретов, но в `pyproject.toml` не объявлен вовсе — SBOM по файлу
показал бы продукт без библиотеки шифрования.

Что попадает в документ:
  * сам продукт (имя, версия, лицензия из метаданных);
  * прямые зависимости и ВСЁ их транзитивное замыкание (`Requires-Dist`);
  * лицензия каждого компонента: `License-Expression` (PEP 639), иначе
    классификатор, иначе поле `License` — как есть, без догадок;
  * `bom-ref` и блок `dependencies` — кто от кого зависит.

Чего в документе НЕТ и почему:
  * хэшей артефактов (`hashes`): установленное распределение не хранит файл
    колеса, и подставить туда хэш из `RECORD` значило бы выдать хэш файла за
    хэш артефакта. Хэши есть там, где есть артефакт, — в манифесте офлайн-набора
    (`tools/release/airgap.py`), и он подписывается;
  * сведений об уязвимостях: они не выводятся из метаданных, а требуют базы,
    которой на машине без сети нет.

Подпись (`--sign`) считается тем же кодом, что и подпись экспорта аудита, —
`tools/release/signing.py`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import tomllib
import uuid
import importlib.metadata as metadata

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

PRODUCT_NAME = "ragraf"
SPEC_VERSION = "1.5"


def _normalize(name: str) -> str:
    """Имя распределения к канонической форме: `PyNaCl` → `pynacl`."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    """Имя из строки `Requires-Dist` (`fastembed (>=0.8,<0.9); extra == ...`)."""
    text = requirement.split(";", 1)[0].strip()
    for marker in (" ", "(", "[", "<", ">", "=", "!", "~", ";"):
        position = text.find(marker)
        if position > 0:
            text = text[:position]
    return _normalize(text)


def _extra_only(requirement: str) -> bool:
    """Зависимость нужна только при установке дополнительного набора."""
    marker = requirement.split(";", 1)[1] if ";" in requirement else ""
    return "extra ==" in marker


def _license_of(dist: metadata.Distribution) -> tuple[list[dict], str]:
    """Лицензия компонента как есть. Возвращает (licenses, как получено)."""
    try:
        expression = dist.metadata.get("License-Expression")
    except Exception:  # noqa: BLE001
        expression = None
    if expression:
        return [{"expression": str(expression).strip()}], "License-Expression"
    classifiers = [str(c) for c in (dist.metadata.get_all("Classifier") or [])]
    named = [c.split("::")[-1].strip() for c in classifiers if c.startswith("License ::")]
    if named:
        return [{"name": name} for name in named], "Classifier"
    plain = str(dist.metadata.get("License") or "").strip()
    if plain and plain.upper() != "UNKNOWN":
        # Длинный текст лицензии в SBOM не переносим: он не помещается в
        # `license.name` по схеме. Факт «лицензия не опознана как имя» важен сам.
        if len(plain) <= 200 and "\n" not in plain:
            return [{"name": plain}], "License"
        return [], "License-text-too-long"
    return [], "absent"


def _component(dist: metadata.Distribution) -> dict:
    name = _normalize(dist.metadata["Name"] or "unknown")
    version = str(dist.version or "unknown")
    licenses, _source = _license_of(dist)
    component = {
        "type": "library",
        "bom-ref": f"pkg:pypi/{name}@{version}",
        "name": name,
        "version": version,
        "purl": f"pkg:pypi/{name}@{version}",
        "modified": False,
    }
    if licenses:
        component["licenses"] = licenses
    homepage = dist.metadata.get("Home-page") or ""
    if homepage:
        component["externalReferences"] = [{"type": "website", "url": str(homepage)}]
    return component


def _tree_version(repo: pathlib.Path) -> str:
    """Версия продукта из дерева: `__init__.py` — единственное её место."""
    init_file = repo / "src" / "ragraf" / "__init__.py"
    for line in init_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "0.0.0"


def _tree_license(repo: pathlib.Path) -> list[dict]:
    """Выражение лицензии из `pyproject.toml` продукта."""
    data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    expression = str(data.get("project", {}).get("license") or "").strip()
    return [{"expression": expression}] if expression else []


def _product_info(repo: pathlib.Path) -> tuple[str, list[dict], str, str]:
    """Версия и лицензия продукта: из ДЕРЕВА, а расхождение с метаданными — в отчёт.

    Почему не из установленного распределения. Замерено 24.10.2026: в дереве
    ЛЕЖАЛ устаревший сборочный артефакт `src/ragraf.egg-info` с `Version: 0.1.0`
    и `License-Expression: MIT`, хотя код — 0.2.1 и лицензия BUSL-1.1. Первый
    прогон SBOM принял эти метаданные за факт и описал продукт как `0.1.0 / MIT`.
    Артефакт пересобран при сборке колеса, и расхождение исчезло, но правило
    остаётся: метаданные на пути импорта могут описывать ПРОШЛУЮ сборку, а SBOM
    обязан описывать поставляемое. Расхождение не замалчивается: оно попадает в
    свойство `ragraf:metadata_conflict`.
    """
    version = _tree_version(repo)
    licenses = _tree_license(repo)
    conflict = ""
    try:
        dist = metadata.distribution(PRODUCT_NAME)
    except metadata.PackageNotFoundError:
        dist = None
    if dist is not None:
        installed_version = str(dist.version)
        installed_licenses, installed_source = _license_of(dist)
        installed_expression = ""
        if installed_licenses:
            first = installed_licenses[0]
            installed_expression = str(first.get("expression") or first.get("name") or "")
        tree_expression = ""
        if licenses:
            tree_expression = str(licenses[0].get("expression") or "")
        if (installed_version != version
                or (installed_expression and tree_expression
                    and installed_expression != tree_expression)):
            conflict = (f"метаданные на пути импорта: {installed_version} / "
                        f"{installed_expression or installed_source}; "
                        f"дерево: {version} / {tree_expression or '—'}")
    return version, licenses, "pyproject", conflict


def build(repo: pathlib.Path = REPO) -> dict:
    version, product_licenses, license_source, conflict = _product_info(repo)
    data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    project = data.get("project", {})
    # Корни обхода — ОБЪЯВЛЕННЫЕ зависимости. Дополнительные наборы добавляются,
    # только если они фактически установлены: иначе SBOM перечислял бы то, чего
    # в поставке нет, либо (как было с PyNaCl 24.10.2026) молчал бы о том, что
    # код использует, но pyproject не объявляет.
    roots: set[str] = set()
    declared: set[str] = set()
    for item in project.get("dependencies", []) or []:
        name = _requirement_name(str(item))
        roots.add(name)
        declared.add(name)
    extras_included: list[str] = []
    for group, items in (project.get("optional-dependencies", {}) or {}).items():
        for item in items:
            name = _requirement_name(str(item))
            if name:
                roots.add(name)
                extras_included.append(f"{group}:{name}")
    installed: dict[str, metadata.Distribution] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            installed[_normalize(name)] = dist

    seen: dict[str, dict] = {}
    edges: dict[str, list[str]] = {}
    queue = [name for name in roots if name in installed]
    missing_roots = sorted(name for name in roots if name not in installed)
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        dist = installed[name]
        component = _component(dist)
        seen[name] = component
        children: list[str] = []
        for requirement in dist.requires or []:
            if _extra_only(requirement):
                continue
            child = _requirement_name(requirement)
            if child and child in installed:
                children.append(child)
                if child not in seen:
                    queue.append(child)
        edges[component["bom-ref"]] = sorted(
            installed[child] and f"pkg:pypi/{child}@{installed[child].version}"
            for child in children
        )

    product_ref = f"pkg:pypi/{PRODUCT_NAME}@{version}"
    edges[product_ref] = sorted(
        f"pkg:pypi/{name}@{installed[name].version}" for name in sorted(roots)
        if name in installed
    )
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tools": [{"vendor": "RAGRAF", "name": "sbom.py", "version": version}],
            "component": {
                "type": "application",
                "bom-ref": product_ref,
                "name": PRODUCT_NAME,
                "version": version,
                "purl": product_ref,
                "licenses": product_licenses,
                "modified": False,
            },
            "properties": [
                {"name": "ragraf:license_source", "value": license_source},
                {"name": "ragraf:components", "value": str(len(seen))},
                {"name": "ragraf:missing_roots",
                 "value": ",".join(missing_roots) if missing_roots else ""},
                {"name": "ragraf:extras_included",
                 "value": ",".join(sorted(extras_included))},
                {"name": "ragraf:declared_dependencies",
                 "value": ",".join(sorted(declared))},
                {"name": "ragraf:metadata_conflict", "value": conflict},
            ],
        },
        "components": [seen[key] for key in sorted(seen)],
        "dependencies": [{"ref": ref, "dependsOn": deps}
                         for ref, deps in sorted(edges.items())],
    }
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Собрать SBOM CycloneDX 1.5")
    parser.add_argument("--out", default="sbom.cdx.json")
    parser.add_argument("--root", default=str(REPO))
    parser.add_argument("--sign", action="store_true")
    parser.add_argument("--key", default="")
    args = parser.parse_args(argv)

    document = build(pathlib.Path(args.root))
    target = pathlib.Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"[sbom] {target}: компонентов {len(document['components'])}, "
          f"спецификация CycloneDX {document['specVersion']}")
    for component in document["components"]:
        licenses = component.get("licenses") or [{"name": "не указана"}]
        shown = ", ".join(item.get("expression") or item.get("name") or "?"
                          for item in licenses)
        print(f"  {component['name']} {component['version']} — {shown}")

    if args.sign:
        from tools.release import signing
        try:
            payload = signing.sign_file(target, args.key or None)
            print(f"[sbom] подпись: {target.name}.sig, sha256 {payload['sha256'][:16]}…")
        except signing.SigningUnavailable as exc:
            print(f"[sbom] подпись НЕ поставлена: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
