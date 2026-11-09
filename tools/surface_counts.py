"""Числа поверхности — тонкая обёртка над `src/ragraf/surface.py`.

Зачем обёртка, а не второй счёт. Раньше счёт жил здесь, и документы расходились
с кодом, потому что у каждого потребителя была своя редакция. Теперь счёт один
(`surface.collect`), а этот файл только печатает его в прежнем формате: на него
завязаны чекер `tools/Invoke-ProjectDoctrine.ps1`, ворота публикации и тесты.

Формат ответа сохранён посимвольно по ключам: `facade_ops`, `mcp_tools`,
`cli_subcommands`, `tree_languages`, `text_languages`, `formats`,
`capabilities` плюс `*_source` и `problems`. Менять его — ломать потребителей.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ragraf import surface  # noqa: E402 — путь к src известен только здесь


def main() -> int:
    try:
        payload = surface.collect(ROOT)
    except surface.SurfaceError as exc:
        print(json.dumps({"problems": [str(exc)]}, ensure_ascii=False))
        return 1
    counts = payload["counts"]
    sources = payload["sources"]
    result: dict[str, object] = {
        "facade_ops": counts["ops"],
        "facade_ops_source": sources["ops"],
        "mcp_tools": counts["tools"],
        "mcp_tools_source": sources["tools"],
        "cli_subcommands": counts["cli"],
        "cli_subcommands_source": sources["cli"],
        "tree_languages": counts["tree"],
        "text_languages": counts["text"],
        "tree_languages_source": sources["languages"],
        "formats": counts["formats"],
        "formats_source": sources["formats"],
        "capabilities": counts["caps"],
        "capabilities_source": sources["features"],
        "problems": list(payload.get("problems") or []),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
