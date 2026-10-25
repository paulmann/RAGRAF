"""Офлайн-набор RAGRAF: поставка в закрытый контур без сети.

Задача. Машина в закрытом контуре не имеет выхода в интернет: `pip install ragraf`
там невозможен, модель эмбеддингов не скачивается, Joern не загружается. Набор
должен содержать ВСЁ, что нужно, и уметь доказать свою целостность.

Что кладётся в набор:

    <out>/airgap.json          манифест: состав, размеры, sha256, пробелы
    <out>/checksums.sha256     хэши всех файлов набора
    <out>/airgap.json.sig      подпись манифеста (если есть ключ и PyNaCl)
    <out>/tree/                дерево поставки (то жуткое, что собирает build_release)
    <out>/wheels/              колесо ragraf + колёса зависимостей
    <out>/model/               кэш модели эмбеддингов (fastembed)
    <out>/tools/joern/         Joern и JDK (если просили и если они есть)

Честность состава. Набор помечается `complete: false`, если чего-то не хватает,
и в `missing` названо, чего именно. Это не формальность: набор без модели
эмбеддингов ставится и работает, но векторный поиск в нём недоступен, и скрывать
это значило бы обещать полноту, которой нет. Набор без Joern работает без графа
свойств кода — это `capability` ниже `full`, а не «всё хорошо».

Проверка. `--verify` пересчитывает каждый хэш и проверяет подпись манифеста.
Если подписи нет, это сказано прямо (`signed: false`), а не выдано за проверенное.

Границы. Сеть нужна только на этапе СБОРКИ набора (колёса качаются с PyPI).
Установка из готового набора сети не требует: `scripts/install-airgap.ps1 -Set <out>`
ставит колёса с `--no-index --find-links`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import platform
import shutil
import subprocess
import sys
import time
import tomllib

REPO = pathlib.Path(__file__).resolve().parents[2]
FORMAT = "ragraf-airgap/1"
SKIP_NAMES = {"checksums.sha256"}
SKIP_SUFFIXES = {".sig"}


def sha256_file(path: pathlib.Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def dependencies(repo: pathlib.Path) -> tuple[list[str], list[str], str]:
    """Зависимости из pyproject: обязательные, дополнительные и версия продукта."""
    data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    required = [str(item) for item in data["project"].get("dependencies", [])]
    extras = data["project"].get("optional-dependencies", {}) or {}
    optional = [str(item) for group in extras.values() for item in group]
    version = "0.0.0"
    init_file = repo / "src" / "ragraf" / "__init__.py"
    for line in init_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            version = line.split("=", 1)[1].strip().strip("\"'")
            break
    return required, optional, version


def run(command: list[str], cwd: pathlib.Path | None = None) -> tuple[int, str]:
    proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(cwd) if cwd else None)
    tail = (proc.stdout or "")[-600:] + (proc.stderr or "")[-600:]
    return proc.returncode, tail.strip()


def collect_tree(out: pathlib.Path, notes: list[str]) -> pathlib.Path | None:
    tree = out / "tree"
    code, tail = run([sys.executable, str(REPO / "tools" / "release" / "build_release.py"),
                      "--out", str(tree)])
    if code != 0:
        notes.append(f"дерево поставки не собрано: код {code}: {tail[-300:]}")
        return None
    return tree


def collect_wheels(out: pathlib.Path, required: list[str], optional: list[str],
                   with_extras: bool, notes: list[str]) -> tuple[int, int]:
    wheels = out / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    wanted = list(required) + (list(optional) if with_extras else [])
    code, tail = run([sys.executable, "-m", "pip", "download", "--dest", str(wheels),
                      "--only-binary=:all:", *wanted])
    downloaded = len(list(wheels.glob("*.whl")))
    if code != 0:
        notes.append(f"колёса зависимостей скачаны не полностью: код {code}: {tail[-300:]}")
    # Колесо самого продукта — локально, без сети.
    code, tail = run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps",
                      "--no-build-isolation", "-w", str(wheels)], cwd=REPO)
    if code != 0:
        notes.append(f"колесо ragraf не собрано: код {code}: {tail[-300:]}")
    return downloaded, len(list(wheels.glob("ragraf-*.whl")))


def collect_model(out: pathlib.Path, notes: list[str]) -> pathlib.Path | None:
    source = pathlib.Path(
        str(__import__("os").environ.get("RAGRAF_MODEL_CACHE")
            or pathlib.Path.home() / ".ragraf" / "models"))
    if not source.exists():
        notes.append(f"кэша модели эмбеддингов нет ({source}): векторный поиск в "
                     f"закрытом контуре будет недоступен, лексический останется")
        return None
    target = out / "model"
    if target.exists():
        raise SystemExit(f"[airgap] приёмник уже есть: {target} — набор не перезаписывается")
    shutil.copytree(source, target)
    return target


def collect_joern(out: pathlib.Path, notes: list[str]) -> pathlib.Path | None:
    source = REPO / ".tools"
    if not source.exists():
        notes.append(f"каталога инструментов Joern нет ({source}): граф свойств кода "
                     f"в закрытом контуре недоступен, capability будет ниже full")
        return None
    target = out / "tools" / "joern"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target


def components_of(out: pathlib.Path) -> list[dict]:
    items: list[dict] = []
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        if path.name in SKIP_NAMES or path.suffix in SKIP_SUFFIXES:
            continue
        relative = path.relative_to(out).as_posix()
        items.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "kind": relative.split("/", 1)[0],
        })
    return items


def build(out: pathlib.Path, *, with_model: bool, with_joern: bool,
          with_extras: bool, sign: bool, key: str) -> int:
    if out.exists() and any(out.iterdir()):
        print(f"[airgap] приёмник не пуст: {out}. Набор не перезаписывается: "
              f"укажите новый каталог.")
        return 2
    out.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    required, optional, version = dependencies(REPO)

    tree = collect_tree(out, notes)
    downloaded, own = collect_wheels(out, required, optional, with_extras, notes)
    model = collect_model(out, notes) if with_model else None
    if not with_model:
        notes.append("модель эмбеддингов не включена по указанию (--no-model)")
    joern = collect_joern(out, notes) if with_joern else None
    if not with_joern:
        notes.append("Joern не включён по указанию (--no-joern)")

    missing: list[str] = []
    if tree is None:
        missing.append("tree")
    if own == 0 or downloaded == 0:
        missing.append("wheels")
    if with_model and model is None:
        missing.append("model")
    if with_joern and joern is None:
        missing.append("joern")

    # `complete` — про СОСТАВ: всё запрошенное на месте. Это не то же самое, что
    # «полные возможности»: набор без Joern собран полностью, но граф свойств кода
    # в нём не строится. Смешивать эти два утверждения нельзя — замерено
    # 24.10.2026: первый набор с `--no-joern` печатал «полный набор: да» и этим
    # обещал capability `full`, которого в нём нет.
    capability = {"cpg": joern is not None, "vector": model is not None}
    degraded: list[str] = []
    if not capability["cpg"]:
        degraded.append("cpg: Joern не включён — граф свойств кода не строится, "
                        "capability будет ниже full")
    if not capability["vector"]:
        degraded.append("vector: модели эмбеддингов нет — только лексический поиск")

    manifest = {
        "format": FORMAT,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "product": {"name": "ragraf", "version": version},
        "host": {"python": platform.python_version(), "platform": platform.platform()},
        "components": components_of(out),
        "notes": notes,
        "missing": missing,
        "capability": capability,
        "degraded": degraded,
        "complete": not missing,
        "full_capability": all(capability.values()),
        "install": "scripts/install-airgap.ps1 -Set <каталог набора>",
    }
    manifest_path = out / "airgap.json"
    manifest_path.write_bytes(
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    lines = [f"{item['sha256']}  {item['path']}" for item in manifest["components"]]
    lines.append(f"{sha256_file(manifest_path)}  airgap.json")
    (out / "checksums.sha256").write_bytes(("\n".join(sorted(lines)) + "\n").encode("utf-8"))

    signed = False
    if sign:
        try:
            sys.path.insert(0, str(REPO))
            from tools.release import signing

            signing.ensure_key(key or None)
            payload = signing.sign_file(manifest_path, key or None)
            signed = True
            notes.append(f"манифест подписан: sha256 {payload['sha256'][:16]}…")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"подпись манифеста не поставлена: {type(exc).__name__}: {exc}")
            manifest["notes"] = notes
            manifest_path.write_bytes(
                (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    total = sum(item["bytes"] for item in manifest["components"])
    print(f"[airgap] набор: {out}")
    print(f"  файлов: {len(manifest['components'])}, объём: {total / 1_048_576:.1f} МБ")
    print(f"  колёс зависимостей: {downloaded}, колесо ragraf: {own}")
    print(f"  модель: {'есть' if model else 'нет'}, Joern: {'есть' if joern else 'нет'}")
    print(f"  подпись: {'есть' if signed else 'нет'}")
    print(f"  состав полон: {'да' if manifest['complete'] else 'НЕТ — ' + ', '.join(missing)}")
    print(f"  полные возможности: {'да' if manifest['full_capability'] else 'НЕТ'}")
    for item in manifest["degraded"]:
        print(f"  ! {item}")
    for note in notes:
        print(f"  · {note}")
    return 0 if manifest["complete"] else 1


def verify(out: pathlib.Path) -> int:
    manifest_path = out / "airgap.json"
    if not manifest_path.exists():
        print(f"[airgap] нет манифеста: {manifest_path}")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    checked = 0
    for item in manifest.get("components", []):
        path = out / item["path"]
        checked += 1
        if not path.exists():
            problems.append(f"нет файла: {item['path']}")
            continue
        digest = sha256_file(path)
        if digest != item["sha256"]:
            problems.append(f"хэш не совпал: {item['path']}")
        elif path.stat().st_size != item["bytes"]:
            problems.append(f"размер не совпал: {item['path']}")
    checksums = out / "checksums.sha256"
    if checksums.exists():
        for line in checksums.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, _, relative = line.partition("  ")
            path = out / relative
            checked += 1
            if not path.exists():
                problems.append(f"нет файла из checksums: {relative}")
            elif sha256_file(path) != digest:
                problems.append(f"хэш не совпал с checksums: {relative}")

    signed = False
    signature_problem = ""
    if (out / "airgap.json.sig").exists():
        try:
            sys.path.insert(0, str(REPO))
            from tools.release import signing

            signing.verify_file(manifest_path)
            signed = True
        except Exception as exc:  # noqa: BLE001
            signature_problem = f"{type(exc).__name__}: {exc}"
    print(f"[airgap] проверено объектов: {checked}")
    print(f"  полный набор по манифесту: {'да' if manifest.get('complete') else 'НЕТ'}")
    print(f"  подпись: {'проверена' if signed else ('НЕ проверена: ' + signature_problem
                                                  if signature_problem else
                                                  'отсутствует (signed: false)')}")
    for problem in problems:
        print(f"  · {problem}")
    if problems or signature_problem:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Офлайн-набор RAGRAF")
    parser.add_argument("--out", default="", help="каталог набора")
    parser.add_argument("--verify", default="", help="проверить готовый набор")
    parser.add_argument("--no-model", action="store_true")
    parser.add_argument("--no-joern", action="store_true")
    parser.add_argument("--with-extras", action="store_true",
                       help="включить дополнительные наборы (documents, tls)")
    parser.add_argument("--sign", action="store_true")
    parser.add_argument("--key", default="")
    args = parser.parse_args(argv)
    if args.verify:
        return verify(pathlib.Path(args.verify))
    if not args.out:
        parser.print_help()
        return 2
    return build(pathlib.Path(args.out), with_model=not args.no_model,
                 with_joern=not args.no_joern, with_extras=args.with_extras,
                 sign=args.sign, key=args.key)


if __name__ == "__main__":
    raise SystemExit(main())
