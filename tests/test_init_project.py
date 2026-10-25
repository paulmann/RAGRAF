"""Проверка подключения нового проекта к RAGRAF от начала до конца.

Повод: агент MD.Viewer отрапортовал, что не смог воспользоваться RAGRAF.
Разбор показал замкнутый круг:
  * команда `register` требует готовый конфиг (аргумент `--project`);
  * команды, которая создала бы конфиг, НЕ БЫЛО.

Значит подключить новый проект было нельзя без ручного копирования чужого
конфига. Этот тест проверяет, что путь работает целиком и повторяем:

  1. конфигурация создаётся по каталогу проекта (команда init);
  2. осмотр находит все языки, включая JavaScript;
  3. каталоги зависимостей и секреты исключаются;
  4. существующий конфиг не затирается без явного разрешения;
  5. команда init работает БЕЗ --project (это её отличие от остальных);
  6. остальные команды без --project дают понятную подсказку.

Проверка идёт на ВРЕМЕННОМ проекте: рабочие конфиги не меняются.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import init_corpus  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

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


def make_project(root: pathlib.Path) -> None:
    """Создать проект со всеми видами файлов, включая секреты и мусор."""
    (root / "src").mkdir(parents=True)
    (root / "js").mkdir()
    (root / "css").mkdir()
    (root / "vendor").mkdir()
    (root / "node_modules").mkdir()
    (root / "docs").mkdir()

    (root / "src" / "main.php").write_text(
        "<?php\nfunction handler(int $x): int { return $x + 1; }\n",
        encoding="utf-8", newline="\n")
    (root / "src" / "config.local.php").write_text(
        "<?php\n$password = 'secret';\n", encoding="utf-8", newline="\n")
    (root / "js" / "app.js").write_text(
        "function init() { document.querySelector('.card'); }\n",
        encoding="utf-8", newline="\n")
    (root / "js" / "module.mjs").write_text(
        "export function run() { return 1; }\n", encoding="utf-8", newline="\n")
    (root / "css" / "style.css").write_text(
        ".card { padding: 4px; }\n", encoding="utf-8", newline="\n")
    (root / "docs" / "README.md").write_text(
        "# Проект\n\nОписание.\n", encoding="utf-8", newline="\n")
    (root / "index.html").write_text(
        "<html><body class=\"card\"></body></html>\n", encoding="utf-8", newline="\n")
    # Мусор, который обязан быть исключён.
    for i in range(5):
        (root / "vendor" / f"dep{i}.php").write_text(
            "<?php class Dep {}\n", encoding="utf-8", newline="\n")
        (root / "node_modules" / f"pkg{i}.js").write_text(
            "module.exports = {};\n", encoding="utf-8", newline="\n")


tmp = pathlib.Path(tempfile.mkdtemp(prefix="ragraf_init_"))
try:
    project = tmp / "MyProject"
    project.mkdir()
    make_project(project)

    print("=== 1. Осмотр проекта ===")
    scan = init_corpus.inspect_project(project)
    print(f"  файлов: {scan.total_files}, языки: {scan.languages}")
    print(f"  исключено каталогов: {sorted(scan.exclude_dirs)}")
    print(f"  секретные шаблоны: {[p for p, _ in scan.secret_patterns]}")
    check(scan.ok, "осмотр прошёл без замечаний", "; ".join(scan.problems))
    # JavaScript обязан быть найден: именно он потерялся в первой версии.
    check("javascript" in scan.languages, "JavaScript найден при осмотре",
          str(scan.languages))
    check(scan.languages.get("javascript") == 2, "оба файла .js учтены",
          str(scan.languages.get("javascript")))
    check("vendor" in scan.exclude_dirs, "каталог зависимостей исключён")
    check("node_modules" in scan.exclude_dirs, "каталог модулей исключён")
    check(any("local" in p for p, _ in scan.secret_patterns),
          "файлы с секретами найдены")
    # Зависимости не должны попасть в счёт файлов.
    check(scan.total_files < 15, "файлы зависимостей не посчитаны",
          f"насчитано {scan.total_files}")

    print("\n=== 2. Создание конфигурации ===")
    target, scan2 = init_corpus.create_config(
        project, project="MyProject", config_path=tmp / "my.yaml")
    check(target.exists(), "файл конфигурации создан", str(target))
    text = target.read_text(encoding="utf-8")
    check('"**/*.js"' in text, "JavaScript включён в include")
    check('"**/*.mjs"' in text, "расширение .mjs включено")
    check("vendor" in text, "зависимости исключены в конфиге")
    check("node_modules" in text, "модули исключены в конфиге")
    check("config.local" in text or "local.php" in text,
          "секреты исключены в конфиге")
    check("javascript" in text, "язык javascript объявлен в конфиге")

    print("\n=== 3. Конфигурация читается загрузчиком ===")
    cfg = load_config(target)
    print(f"  проект: {cfg.project}, языки: {cfg.languages}")
    print(f"  включений: {len(cfg.include)}, исключений: {len(cfg.exclude)}")
    check(cfg.project == "MyProject", "имя проекта прочитано")
    check("javascript" in cfg.languages, "язык javascript прочитан")
    check(cfg.language_of("js/app.js") == "javascript",
          "файл .js распознаётся как javascript",
          str(cfg.language_of("js/app.js")))
    check(cfg.language_of("src/main.php") == "php", "файл .php распознаётся как php")

    print("\n=== 4. Повторное создание не затирает конфиг ===")
    try:
        init_corpus.create_config(project, config_path=target)
        check(False, "повторное создание без разрешения отклонено",
              "исключение не возникло")
    except FileExistsError as exc:
        check(True, "повторное создание без разрешения отклонено")
        print(f"    причина: {str(exc)[:100]}")
    # С перезаписью — создаётся.
    target2, _ = init_corpus.create_config(
        project, project="MyProject", config_path=target, overwrite=True)
    check(target2.exists(), "с явным разрешением конфиг перезаписан")

    print("\n=== 5. Команда init работает без --project ===")
    cli = REPO / "tools" / "ragraf.py"
    result = subprocess.run(
        [sys.executable, str(cli), "init", str(project),
         "--name", "CliProject", "--config", str(tmp / "cli.yaml")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, cwd=str(REPO),
    )
    out = (result.stdout or "") + (result.stderr or "")
    check(result.returncode == 0, "команда init выполнена", out[-200:])
    check((tmp / "cli.yaml").exists(), "конфигурация создана командой CLI")
    print("    вывод команды:")
    for line in out.strip().splitlines()[:8]:
        print(f"      {line}")

    print("\n=== 6. Реестр читается без --project ===")
    result2 = subprocess.run(
        [sys.executable, str(cli), "catalog"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, cwd=str(REPO),
    )
    out2 = (result2.stdout or "") + (result2.stderr or "")
    # Реестр описывает ВСЕ разработки и не принадлежит ни одной из них, поэтому
    # конфигурация конкретного корпуса ему не нужна. Раньше команда требовала
    # --project, и документированный первый шаг работы не выполнялся.
    check(result2.returncode == 0, "catalog работает без --project", out2[-300:])
    check("разработок в реестре" in out2, "catalog показал реестр", out2[:200])
    for line in out2.strip().splitlines()[:5]:
        print(f"      {line}")

    print("\n=== 7. Команда, которой нужен конфиг, даёт подсказку ===")
    result3 = subprocess.run(
        [sys.executable, str(cli), "status"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, cwd=str(REPO),
    )
    out3 = (result3.stdout or "") + (result3.stderr or "")
    check(result3.returncode != 0, "status без --project не выполняется", str(result3.returncode))
    check("init" in out3, "подсказка называет команду init", out3[-300:])
    check("ensure" in out3, "подсказка называет команду ensure", out3[-300:])
    print("    подсказка:")
    for line in out3.strip().splitlines()[:6]:
        print(f"      {line}")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
