"""Сборка Community-версии плагина для каталога Anthropic: пакет, проверки, бланк подачи.

Зачем отдельная команда, а не «соберите zip руками». Каталог читает **папку**
плагина из публичного репозитория, а человеку, который подаёт заявку, нужны ещё
три вещи, и все они должны быть посчитаны, а не переписаны глазами:

  1. **пакет** — то, что можно скачать и распаковать, чтобы посмотреть без
     установки (`--plugin-dir` или распакованный каталог);
  2. **sha256** — чтобы версия в заявке и версия в пакете были одним и тем же
     файлом, а не «примерно тем»;
  3. **бланк подачи** — поля, которые портал `claude.ai/directory/manage`
     спрашивает (имя, версия, описание, категория, ссылки, лицензия), собранные
     из манифеста, а не набранные заново.

Плюс проверка: `claude plugin validate` и `claude plugin validate --profile
directory`. Вторая — это ровно те требования, которые каталог предъявляет к
подаче; она проходит заранее, а не после отказа.

Что НЕ делает команда: не отправляет заявку и не публикует. Подача идёт из
портала под учётной записью владельца и включает необратимые шаги (иконка и
метаданные первого сохранения фиксируются), поэтому это решение владельца.

Запуск:

    python tools/package_plugin.py                 # собрать dist/ и проверить
    python tools/package_plugin.py --json          # то же, машинный отчёт
    python tools/package_plugin.py --out <каталог>  # другой каталог сборки
    python tools/package_plugin.py --no-validate   # без вызова claude
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin"
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"
MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"
#: Каталог сборки по умолчанию — ВНЕ дерева проекта. Пакет внутри дерева
#: проверяльщик поставки видит как «файл без места»: он не публикуется и не
#: объявлен удержанным, и при перестройке дерева пропал бы молча.
DEFAULT_OUT = Path(os.environ.get("TEMP") or "/tmp") / "ragraf-plugin-dist"

#: Файлы, которые обязаны лежать внутри плагина: каталог смотрит на лицензию
#: в самой папке плагина, а не по ссылке в описании.
REQUIRED_IN_PLUGIN = ("README.md", "LICENSE", "NOTICE", "EULA.md")

PORTAL = "https://claude.ai/directory/manage"
SUBMISSION_DOC = "https://claude.com/docs/plugins/submit"


def code_version() -> str:
    """Версия из исходников — без импорта пакета (импорт поднял бы зависимости)."""
    init = REPO / "src" / "ragraf" / "__init__.py"
    text = init.read_text(encoding="utf-8") if init.is_file() else ""
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split('"')[1]
    return ""


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def problems() -> list[str]:
    """Что мешает подаче: проверяется до сборки, а не после отказа портала."""
    found: list[str] = []
    manifest = load(MANIFEST)
    market = load(MARKETPLACE)
    version = code_version()
    if manifest.get("name") != "ragraf":
        found.append(f"имя плагина {manifest.get('name')!r}: слаг неизменяем и ожидается ragraf")
    if manifest.get("version") != version:
        found.append(f"версия плагина {manifest.get('version')} не равна версии кода {version}")
    entry = next((p for p in market.get("plugins") or [] if p.get("name") == "ragraf"), None)
    if not entry:
        found.append("маркетплейс не объявляет плагин ragraf")
    elif entry.get("version") != version:
        found.append(f"версия в маркетплейсе {entry.get('version')} не равна версии кода {version}")
    if not manifest.get("description") or len(str(manifest.get("description"))) < 40:
        found.append("описание короче 40 символов: каталогу нечего показать")
    if not manifest.get("license"):
        found.append("в манифесте нет лицензии")
    for name in REQUIRED_IN_PLUGIN:
        if not (PLUGIN / name).is_file():
            found.append(f"в папке плагина нет {name} (каталог смотрит лицензию там)")
    icon = PLUGIN / ".claude-plugin" / "icon.png"
    if not icon.is_file():
        found.append("нет иконки .claude-plugin/icon.png (каталог отвечает ICON_MISSING)")
    else:
        try:
            from PIL import Image

            with Image.open(icon) as image:
                width, height = image.size
            if width != height or not (512 <= width <= 2048):
                found.append(f"иконка {width}x{height}: нужен квадрат 512–2048 px")
        except Exception as exc:  # noqa: BLE001 — отсутствие Pillow не приговор
            found.append(f"иконка не проверена ({type(exc).__name__}); нужен квадрат 512–2048")
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8") if (PLUGIN / "README.md").is_file() else ""
    if len(readme) < 1000:
        found.append("README короче 1000 символов: каталог ждёт описание, установку и границы")
    return found


def build(out_dir: Path) -> dict[str, Any]:
    """Собрать пакет Community-версии и вернуть отчёт (без проверки каталогом)."""
    version = code_version()
    name = f"ragraf-community-{version}"
    target = out_dir / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(PLUGIN, target)
    archive = out_dir / f"{name}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                zf.write(path, Path(name) / path.relative_to(target))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    files = sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())
    return {"name": name, "version": version, "directory": str(target),
            "archive": str(archive), "sha256": digest, "bytes": archive.stat().st_size,
            "files": files}


def submission_form(report: dict[str, Any]) -> dict[str, Any]:
    """Поля заявки для портала: собраны из манифеста, а не набраны заново."""
    manifest = load(MANIFEST)
    entry = next((p for p in load(MARKETPLACE).get("plugins") or []
                  if p.get("name") == "ragraf"), {})
    return {
        "repository": "paulmann/RAGRAF",
        "plugin_path": "plugin",
        "tracked_branch": "main",
        "name": manifest.get("name"),
        "display_name": manifest.get("displayName") or entry.get("displayName"),
        "version": report["version"],
        "description": manifest.get("description"),
        "category": entry.get("category") or "development",
        "keywords": manifest.get("keywords") or [],
        "homepage": manifest.get("homepage"),
        "license": manifest.get("license"),
        "author": manifest.get("author"),
        "package_sha256": report["sha256"],
        "portal": PORTAL,
        "instructions": SUBMISSION_DOC,
        "edition": "community",
    }


def validate(target: Path, *, deep: bool) -> list[dict[str, Any]]:
    """Прогон валидатора клиента: обычный и в профиле каталога."""
    cli = shutil.which("claude")
    if not cli:
        return [{"check": "claude plugin validate", "ok": False,
                 "detail": "CLI claude не найден — проверка не выполнена (это названо)"}]
    runs = [("claude plugin validate", [cli, "plugin", "validate", str(target)])]
    if deep:
        runs.append(("claude plugin validate --profile directory",
                     [cli, "plugin", "validate", str(target), "--profile", "directory"]))
    results: list[dict[str, Any]] = []
    for title, command in runs:
        try:
            res = subprocess.run(command, capture_output=True, text=True, timeout=600,
                                 encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            results.append({"check": title, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
            continue
        text = ((res.stdout or "") + (res.stderr or "")).strip()
        results.append({"check": title, "ok": res.returncode == 0, "detail": text[-400:]})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="package_plugin",
        description="Сборка Community-версии плагина RAGRAF для каталога")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="каталог сборки (dist/)")
    parser.add_argument("--json", action="store_true", help="машинный отчёт")
    parser.add_argument("--no-validate", action="store_true", help="не звать claude")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    found = problems()
    report = build(out_dir)
    report["problems"] = found
    report["submission"] = submission_form(report)
    checks = [] if args.no_validate else validate(Path(report["directory"]), deep=True)
    report["checks"] = checks
    form_path = out_dir / f"{report['name']}.submission.json"
    form_path.write_text(json.dumps(report["submission"], ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    report["submission_file"] = str(form_path)
    ok = not found and all(check["ok"] for check in checks)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print(f"[plugin] пакет: {report['archive']}")
    print(f"  файлов: {len(report['files'])}, {report['bytes']} байт, sha256 {report['sha256']}")
    print(f"  бланк подачи: {form_path}")
    if found:
        print("[plugin] перед подачей исправить:")
        for item in found:
            print("  - " + item)
    for check in checks:
        print(f"  {'OK  ' if check['ok'] else 'НЕТ '}{check['check']}")
    print(f"[plugin] каталог: {PORTAL} — репозиторий {report['submission']['repository']}, "
          f"папка {report['submission']['plugin_path']}, ветка "
          f"{report['submission']['tracked_branch']}")
    print("[plugin] порядок: подключить GitHub к claude.ai → Validate → Submit for review → "
          "Publish после прохождения проверок")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
