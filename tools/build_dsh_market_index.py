"""Сборка индекса рынка плагинов DSH (format 1) для RAGRAF.

ЗАЧЕМ. Плагин распространяется двумя путями: каталогом (пользователь ставит
`dsh plugin --profile <p> add <каталог>`) и ИНДЕКСОМ — файлом JSON, который
клиент читает во вкладке Discover: «Add index source» → URL индекса. Индекс
привязан к коммиту, поэтому установка воспроизводима: одна и та же строка версии
всегда даёт один и тот же код.

Формат 1 (проверяется самим клиентом, `@deepseek-ai/dsh-plugin-market-index`):

    {
      "format": 1,
      "title": {"en": "..."} | "...",
      "description": {language: "..."},          // необязательно
      "metadata": {"categories": {...}, "labels": {...}, "sortKeys": {...}},
      "plugins": [{
        "name": "<имя пакета>", "title": {...}, "description": {...},
        "homepage": "https://…", "repository": {"url": "https://…", "directory": "…"},
        "metadata": {"category": "<id из categories>", "keywords": ["…"]},
        "versions": [{"version": "0.1.0", "publishTimestamp": <unix-секунды>,
                      "source": {"type": "git", "url": "https://…",
                                 "commit": "<40 hex>", "path": "integrations/dsh-plugin"},
                      "engines": {"dsh": ">=0.2.1-rc.5 <0.3.0"}}]
      }]
    }

Почему источник git, а не npm. Публикация в npm — отдельное решение владельца;
git-источник работает сразу, и версия в нём не «плавает»: клиент ставит
`git+<url>#<commit>&path:/integrations/dsh-plugin`. Когда пакет появится в npm,
достаточно поменять `source` на `{"type": "npm"}` — формат это допускает.

ЗАМЕР (03.11.2026): собранный файл проверен ВАЛИДАТОРОМ САМОГО КЛИЕНТА —
`parseMarketIndex` из установленной сборки DSH 44.7.0; проверка в
`tests/test_dsh_market_index.py`, включая отрицательный контроль (битый индекс
обязан быть отвергнут).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / "integrations" / "dsh-plugin"
DEFAULT_OUT = BUNDLE / "index.json"

CATEGORIES = {
    "developer-tools": {"title": {"en": "Developer tools", "ru": "Инструменты разработчика"}},
}


def git(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise SystemExit("git не найден в PATH: " + str(exc))
    if result.returncode != 0:
        # Рабочая копия без git — обычное дело (частный каталог разработки).
        # Тогда нужен явный коммит: индекс обязан ссылаться на то, что можно скачать.
        raise SystemExit(
            "git " + " ".join(args) + " не удался: " + (result.stderr or "").strip()
            + "\nЕсли каталог — не git-репозиторий, укажите коммит явно:\n"
              "  python tools/build_dsh_market_index.py --commit <40 hex> "
              "--timestamp <unix-секунды>")
    return result.stdout.strip()


def read_version() -> str:
    package = json.loads((BUNDLE / "package.json").read_text(encoding="utf-8"))
    return str(package["version"])


def read_engines() -> str:
    package = json.loads((BUNDLE / "package.json").read_text(encoding="utf-8"))
    return str((package.get("engines") or {}).get("dsh") or "")


def build(commit: str, timestamp: int, version: str, repo_url: str) -> dict:
    engines = read_engines()
    if not engines:
        raise SystemExit("в package.json пакета нет engines.dsh — клиент не сможет "
                         "проверить совместимость")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SystemExit(f"коммит должен быть 40 hex-символов, получено: {commit!r}")
    return {
        "format": 1,
        "title": {"en": "RAGRAF", "ru": "RAGRAF"},
        "description": {
            "en": "Local code index for DeepSeek Harness: graph, full-text search, vectors "
                  "and path:line evidence; the MCP server and the index-first gate in one plugin.",
            "ru": "Локальный индекс кода для DeepSeek Harness: граф, полнотекстовый поиск, "
                  "векторы и ссылки path:line; MCP-сервер и гейт «сначала индекс» одним плагином.",
        },
        "metadata": {"categories": CATEGORIES},
        "plugins": [{
            "name": "ragraf-dsh",
            "title": {"en": "RAGRAF — code index", "ru": "RAGRAF — индекс кода"},
            "description": {
                "en": "Ask where something is defined, who calls it and what breaks before "
                      "reading files. Ships the MCP server, session hooks and an index-first "
                      "gate; the index never leaves the machine.",
                "ru": "Спросить, где определено, кто вызывает и что сломается, — вместо чтения "
                      "файлов. Ставит MCP-сервер, хуки сеанса и гейт «сначала индекс»; индекс "
                      "не покидает машину.",
            },
            "author": "Mikhail Deynekin",
            "homepage": "https://github.com/paulmann/RAGRAF",
            "repository": {"url": repo_url, "directory": "integrations/dsh-plugin"},
            "metadata": {
                "category": "developer-tools",
                "keywords": ["code-search", "code-index", "mcp", "offline", "impact-analysis"],
            },
            "versions": [{
                "version": version,
                "publishTimestamp": timestamp,
                "source": {"type": "git", "url": repo_url, "commit": commit,
                           "path": "integrations/dsh-plugin"},
                "engines": {"dsh": engines},
            }],
        }],
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Индекс рынка плагинов DSH (format 1) для RAGRAF")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="куда писать индекс")
    ap.add_argument("--repo-url", default="https://github.com/paulmann/RAGRAF",
                    help="адрес репозитория без .git")
    ap.add_argument("--commit", default="", help="коммит (по умолчанию HEAD)")
    ap.add_argument("--timestamp", type=int, default=0,
                    help="publishTimestamp в секундах (по умолчанию время коммита)")
    args = ap.parse_args(argv[1:])

    commit = args.commit or git("rev-parse", "HEAD")
    timestamp = args.timestamp or int(git("show", "-s", "--format=%ct", commit))
    version = read_version()
    dirty = bool(git("status", "--porcelain", "--", str(BUNDLE)))
    index = build(commit, timestamp, version, args.repo_url.rstrip("/"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"индекс записан: {out}")
    print(f"  плагин: {index['plugins'][0]['name']} {version}")
    print(f"  источник: git {args.repo_url} @ {commit[:12]} (path: integrations/dsh-plugin)")
    print(f"  engines.dsh: {index['plugins'][0]['versions'][0]['engines']['dsh']}")
    if dirty:
        print("  ВНИМАНИЕ: каталог пакета отличается от коммита — установка по индексу "
              "возьмёт код ИЗ КОММИТА, а не из рабочего дерева")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
