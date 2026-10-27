"""Каталог .ragraf не должен попадать в git — ни в коммит, ни в чужой клон.

Индекс корпуса лежит ВНУТРИ проекта, который индексируется, и весит от сотен
мегабайт до 1,6 ГБ. Правило `.ragraf/` в корневом `.gitignore` держится
с 24.10.2026, но проверки на него не было: чекер смотрит обратное свойство
(файлы поставки не скрыты от git), а проектный `.gitignore` никто не сверял.
Отсюда два разных способа потерять правило незаметно:

  * строку `.ragraf/` удалили или переписали при чистке `.gitignore` — тогда
    `git add -A` заберёт индекс в коммит, и из истории он уже не убирается;
  * каталог уже отслеживается (добавили до появления правила) — тогда
    `.gitignore` на него НЕ действует, и он тихо уезжает в каждый коммит.

Проверяются три свойства, и все три обязательны:

  1. в корневом `.gitignore` есть правило `.ragraf/` (оно уезжает в клон);
  2. git ДЕЙСТВИТЕЛЬНО игнорирует `.ragraf` — его держит какой-то слой
     (проектный `.gitignore` ИЛИ глобальный `core.excludesFile`);
  3. каталог не отслеживается (`git ls-files -- .ragraf` пуст).

Разделение слоёв — не педантизм. Глобальный слой (`~/.gitignore_global`) есть на
этой машине с 27.10.2026, и он МАСКИРУЕТ отсутствие проектного правила:
`git check-ignore` ответит «игнорируется», хотя в чужом клоне правила нет.
Поэтому свойство 1 читается ИЗ ФАЙЛА, а не спрашивается у git.

Живое дерево проекта репозиторием не является (публикация идёт из отдельного
клона), поэтому свойства 2 и 3 проверяются только там, где есть `.git`; при
отсутствии репозитория в ответе стоит `git_checked: false`, и это видно, а не
выдаётся за проверенное.

Коды возврата: 0 — нарушений нет, 1 — есть, 2 — не проверено (нужен git, а его
нет). Молчаливая зелень при непроверенном состоянии была бы вторым дефектом,
поэтому 2 — провал, а не «пропуск».
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Правило считается объявленным в этих написаниях: без ведущего слеша шаблон
# действует на любой глубине, со слешем привязан к корню. Оба варианта
# закрывают `.ragraf` в корне проекта, а он там и создаётся.
ACCEPTED = {".ragraf/", ".ragraf", "/.ragraf/", "/.ragraf"}


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def project_rule(root: Path) -> tuple[int | None, str | None]:
    """Строка `.ragraf/` в корневом .gitignore: (номер строки, текст) или (None, None)."""
    gi = root / ".gitignore"
    if not gi.is_file():
        return None, None
    for number, line in enumerate(gi.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text in ACCEPTED:
            return number, text
    return None, None


def ignored_by(root: Path) -> str | None:
    """Кто игнорирует `.ragraf`: строка вида «источник:строка:правило», иначе None.

    Спрашивается путь ВНУТРИ каталога: шаблон `.ragraf/` привязан к каталогу, а
    `check-ignore` отвечает и на несуществующий путь. Сам каталог не создаётся —
    проверка ничего не меняет на диске.
    """
    result = run_git(root, "check-ignore", "-v", "--no-index", ".ragraf/probe.sqlite")
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.strip():
            return line.split("\t")[0].strip()
    return None


def tracked(root: Path) -> list[str]:
    result = run_git(root, "ls-files", "--", ".ragraf")
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Каталог .ragraf не попадает в git")
    parser.add_argument("--root", default=".", help="корень проекта (по умолчанию текущий каталог)")
    parser.add_argument("--json", action="store_true", help="машинный ответ")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    payload: dict = {"root": str(root), "failures": [], "notes": [], "examined": 0}

    if not (root / ".gitignore").is_file():
        payload["failures"].append({
            "path": ".gitignore",
            "rule": "корневого .gitignore нет: правило .ragraf/ объявить негде"})
        payload["examined"] += 1
        return report(payload, args.json, 1)

    line, rule_text = project_rule(root)
    payload["examined"] += 1
    payload["project_rule"] = {"line": line, "text": rule_text}
    if line is None:
        payload["failures"].append({
            "path": ".gitignore",
            "rule": "нет правила .ragraf/ — в чужом клоне индекс ничем не защищён"})

    repo = run_git(root, "rev-parse", "--is-inside-work-tree")
    is_repo = repo.returncode == 0 and repo.stdout.strip() == "true"
    payload["git_checked"] = False
    if not is_repo:
        payload["notes"].append("дерево не репозиторий: свойства «игнорируется» и «не отслеживается» проверяются в клоне")
        payload["ok"] = not payload["failures"]
        return report(payload, args.json, 0 if payload["ok"] else 1)

    if shutil.which("git") is None:
        payload["error"] = "git не найден в PATH"
        return report(payload, args.json, 2)

    payload["git_checked"] = True
    matched = ignored_by(root)
    payload["examined"] += 1
    payload["ignored_by"] = matched
    if matched is None:
        payload["failures"].append({
            "path": ".ragraf/",
            "rule": "ни один слой не игнорирует каталог: git добавит его в коммит"})
    else:
        payload["layer"] = "проектный .gitignore" if ".gitignore" in matched else "глобальный core.excludesFile"
        payload["notes"].append(f"игнорирует: {matched} ({payload['layer']})")

    files = tracked(root)
    payload["examined"] += len(files)
    payload["tracked"] = len(files)
    for path in files[:20]:
        payload["failures"].append({
            "path": path,
            "rule": "файл уже отслеживается: .gitignore на него не действует, нужен git rm -r --cached .ragraf"})

    payload["ok"] = not payload["failures"]
    return report(payload, args.json, 0 if payload["ok"] else 1)


def report(payload: dict, as_json: bool, code: int) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return code
    print(f"корень: {payload['root']}")
    rule = payload.get("project_rule") or {}
    print("правило .gitignore: " + (f"строка {rule['line']}: {rule['text']}" if rule.get("line") else "НЕТ"))
    print(f"git-слой проверялся: {payload.get('git_checked')}")
    print(f"отслеживается файлов .ragraf: {payload.get('tracked', 'не проверялось')}")
    for note in payload.get("notes", []):
        print(f"  замечание: {note}")
    for item in payload.get("failures", []):
        print(f"  ПРОВАЛ {item['path']} — {item['rule']}")
    if payload.get("error"):
        print(f"  ОШИБКА: {payload['error']}")
    print("итог:", "PASS" if payload.get("ok") else "FAIL")
    return code


if __name__ == "__main__":
    sys.exit(main())
