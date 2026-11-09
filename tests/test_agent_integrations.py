# -*- coding: utf-8 -*-
"""Агентские интеграции: каталоги, шаблоны, уровни и границы (этап 6).

Пять утверждений, которых чтением не подтвердить:

  1. Объявление `integrations/index.json` и таблица уровней в документе совпадают
     с фактическим набором каталогов: «объявлено, но не сделано» валит проверку.
  2. Шаблон разбирается тем же разборщиком, что продукт (JSON/YAML/TOML), и
     называет сервер `ragraf`.
  3. Секрет в шаблоне — находка, а не «значение как значение».
  4. Уровень L2 подтверждён смоуком с кодом возврата; где смоука нет — назван
     чек-лист, а не пустое место.
  5. Проверка действительно ловит порчу: подсадки в песочнице (нет обязательного
     поля, секрет, каталог объявлен без файлов, смоук без кода) обязаны давать
     провалы, а живое дерево — нет.

Песочница: копия `integrations/` и документа в %TEMP%. Живое дерево только
читается.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "check_integrations.py"
DOC = Path("docs") / "03_implementation" / "AGENT-INTEGRATIONS.md"

PASS = 0
FAIL = 0


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def run_tool(root: Path) -> dict:
    proc = subprocess.run([sys.executable, str(TOOL), "--root", str(root), "--json"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600)
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError:
        payload = {"checks": 0, "failed": 1,
                   "problems": [f"инструмент не вернул JSON: {(proc.stderr or '')[:200]}"],
                   "detail": []}
    payload["code"] = proc.returncode
    return payload


def sandbox(tmp: Path) -> Path:
    root = tmp / "sandbox"
    shutil.copytree(REPO / "integrations", root / "integrations")
    (root / DOC.parent).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / DOC, root / DOC)
    return root


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_integrations_"))
    print("=" * 78)
    print("1. ЖИВОЕ ДЕРЕВО: ОБЪЯВЛЕНИЕ, КАТАЛОГИ И УРОВНИ СОВПАДАЮТ")
    print("=" * 78)
    live = run_tool(REPO)
    check(live["code"] == 0 and live["failed"] == 0,
          "проверка интеграций на живом дереве без находок",
          "; ".join(live["problems"])[:300])
    check(int(live.get("checks") or 0) >= 6, "проверок не меньше шести",
          str(live.get("checks")))

    index = json.loads((REPO / "integrations" / "index.json").read_text(encoding="utf-8"))
    products = index["products"]
    slugs = [item["slug"] for item in products]
    check(len(products) == 12, "объявлено двенадцать продуктов", str(len(products)))
    check(len(slugs) == len(set(slugs)), "слаги не повторяются",
          str([s for s in slugs if slugs.count(s) > 1]))
    missing = [slug for slug in slugs
               if not (REPO / "integrations" / slug / "README.md").is_file()]
    check(not missing, "у каждого продукта есть README", str(missing))
    bad_levels = [item["slug"] for item in products
                  if not set(item.get("levels") or []) <= {"L0", "L1", "L2", "L3", "L4"}]
    check(not bad_levels, "уровни только из закрытого перечня", str(bad_levels))
    l2 = [item["slug"] for item in products if "L2" in (item.get("levels") or [])]
    no_smoke = [slug for slug in l2
                if not (REPO / "integrations" / slug / "smoke.ps1").is_file()]
    check(not no_smoke, "у каждого заявленного L2 есть смоук", str(no_smoke))
    gui = [item["slug"] for item in products if "L2" not in (item.get("levels") or [])]
    no_checklist = [slug for slug in gui
                    if not (REPO / "integrations" / slug / "checklist.md").is_file()]
    check(not no_checklist, "где смоука нет — есть чек-лист", str(no_checklist))

    print()
    print("=" * 78)
    print("2. ПОДСАДКИ В ПЕСОЧНИЦЕ: ПРОВЕРКА ЛОВИТ ЧЕТЫРЕ ДЕФЕКТА")
    print("=" * 78)
    root = sandbox(tmp)
    clean = run_tool(root)
    check(clean["code"] == 0 and clean["failed"] == 0,
          "чистая песочница проходит", "; ".join(clean["problems"])[:200])

    # (1) шаблон без обязательного поля: убираем имя сервера ragraf
    template = root / "integrations" / "openclaw" / "openclaw.template.json"
    original = template.read_text(encoding="utf-8")
    template.write_text(original.replace("ragraf", "other"), encoding="utf-8",
                        newline="\n")
    caught = run_tool(root)
    check(caught["code"] == 1
          and any("не назван сервер ragraf" in problem for problem in caught["problems"]),
          "шаблон без сервера ragraf пойман",
          "; ".join(caught["problems"])[:200])
    template.write_text(original, encoding="utf-8", newline="\n")

    # (2) секрет в шаблоне
    leaked = json.loads(original)
    leaked["api_key"] = "sk-live-0123456789"
    template.write_text(json.dumps(leaked, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    caught = run_tool(root)
    check(caught["code"] == 1
          and any("похоже на секрет" in problem for problem in caught["problems"]),
          "секрет в шаблоне пойман", "; ".join(caught["problems"])[:200])
    template.write_text(original, encoding="utf-8", newline="\n")

    # (3) каталог объявлен, а файлов нет
    victim = root / "integrations" / "pi"
    saved = tmp / "pi-saved"
    shutil.move(str(victim), str(saved))
    (root / "integrations" / "pi").mkdir()
    caught = run_tool(root)
    check(caught["code"] == 1
          and any("нет README.md" in problem or "нет шаблона" in problem
                  for problem in caught["problems"]),
          "каталог без файлов пойман", "; ".join(caught["problems"])[:200])
    shutil.rmtree(root / "integrations" / "pi")
    shutil.move(str(saved), str(victim))

    # (4) смоук без кода возврата
    smoke = root / "integrations" / "claude-code" / "smoke.ps1"
    smoke_text = smoke.read_text(encoding="utf-8")
    smoke.write_text(smoke_text.replace("exit 3", "Write-Host 'готово'")
                              .replace("exit 0", "Write-Host 'готово'")
                              .replace("exit 1", "Write-Host 'готово'")
                              .replace("exit 2", "Write-Host 'готово'"),
                     encoding="utf-8", newline="\n")
    caught = run_tool(root)
    check(caught["code"] == 1
          and any("не возвращает код" in problem for problem in caught["problems"]),
          "смоук без кода возврата пойман", "; ".join(caught["problems"])[:200])
    smoke.write_text(smoke_text, encoding="utf-8", newline="\n")

    # (5) таблица документа разошлась с объявлением: уровень снят у одного продукта.
    # Подсадка ищет строку продукта, а не точный текст колонок: 03.11.2026
    # выяснилось, что якорь вида «| L1, L2, L3, L4 | MCP stdio | `.mcp.json`»
    # перестал совпадать после того, как в колонку «Конфигурация» добавили второй
    # путь (`~/.claude.json` и `.mcp.json`). Подсадка тогда ничего не меняла и
    # «провал не поймавшего» теста означал испорченный якорь, а не дефект чекера —
    # ровно тот класс ложной тревоги, из-за которого доверие к гейту теряется.
    doc = root / DOC
    doc_text = doc.read_text(encoding="utf-8")
    mutated: list[str] = []
    for line in doc_text.splitlines():
        if "`claude-code`" in line and "| L1, L2, L3, L4 |" in line:
            line = line.replace("| L1, L2, L3, L4 |", "| L1, L3, L4 |", 1)
        mutated.append(line)
    doc.write_text("\n".join(mutated) + ("\n" if doc_text.endswith("\n") else ""),
                   encoding="utf-8", newline="\n")
    caught = run_tool(root)
    check(caught["code"] == 1
          and any("в документе уровни" in problem for problem in caught["problems"]),
          "расхождение таблицы уровней поймано", "; ".join(caught["problems"])[:200])
    doc.write_text(doc_text, encoding="utf-8", newline="\n")

    # (6) уровень L2 без смоука: правило объявления, а не файлов
    index_path = root / "integrations" / "index.json"
    index_text = index_path.read_text(encoding="utf-8")
    index_data = json.loads(index_text)
    for item in index_data["products"]:
        if item["slug"] == "openclaw":
            item["smoke"] = ""
    index_path.write_text(json.dumps(index_data, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")
    caught = run_tool(root)
    check(caught["code"] == 1
          and any("заявлен L2, но смоук не назван" in problem
                  for problem in caught["problems"]),
          "L2 без смоука пойман", "; ".join(caught["problems"])[:200])
    index_path.write_text(index_text, encoding="utf-8", newline="\n")

    # (7) шаблон, который не разбирается своим разборщиком
    broken = root / "integrations" / "codex-cli" / "codex.template.toml"
    toml_text = broken.read_text(encoding="utf-8")
    broken.write_text("[mcp_servers.ragraf\ncommand = ", encoding="utf-8",
                      newline="\n")
    caught = run_tool(root)
    check(caught["code"] == 1
          and any("шаблон не разбирается" in problem for problem in caught["problems"]),
          "неразбираемый шаблон пойман", "; ".join(caught["problems"])[:200])
    broken.write_text(toml_text, encoding="utf-8", newline="\n")

    final = run_tool(root)
    check(final["code"] == 0 and final["failed"] == 0,
          "после откатов песочница снова проходит",
          "; ".join(final["problems"])[:200])

    print()
    print("=" * 78)
    print("3. ЖИВОЕ ДЕРЕВО НЕ ТРОНУТО")
    print("=" * 78)
    again = run_tool(REPO)
    check(again["code"] == 0 and again["failed"] == 0,
          "живая проверка по-прежнему без находок", "; ".join(again["problems"])[:200])

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
