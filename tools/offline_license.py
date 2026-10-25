#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Офлайн-лицензия: заявка, выпуск, приёмка и перенос БЕЗ гейта издания.

Зачем отдельный вход. Операция `license_offline` объявлена в данных слоя изданий
как возможность издания enterprise — и это правильно: она открывает платное.
Но из этого следует ловушка, найденная при первом же прогоне: установка на
Community не может выпустить ДАЖЕ ЗАЯВКУ, потому что гейт отказывает операции
раньше, чем она начинается. Замкнутый контур оказывается незамыкаемым: заявка
нужна ДО лицензии, а операция доступна только ПОСЛЕ неё.

Поэтому заявка и приёмка живут ещё и здесь — в инструменте, который не проходит
через фасад. Это не обход гейта: приёмка НЕ включает платное издание сама по
себе, она лишь проверяет подпись и отпечаток установки. Права по-прежнему даёт
подписанный файл, который читает `edition.read_license`.

Коды возврата: 0 — шаг выполнен, 1 — отказ с названной причиной.

    python tools/offline_license.py --mode request --out catalog/edition-request.json
    python tools/offline_license.py --mode issue --request <заявка> --license <файл>
    python tools/offline_license.py --mode accept --license <файл> --request <заявка>
    python tools/offline_license.py --mode transfer --license <файл> --out <заявка>
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import edition  # noqa: E402
from ragraf import license_offline as offline  # noqa: E402


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="offline_license.py",
        description="Офлайн-лицензия RAGRAF: заявка, выпуск, приёмка, перенос",
    )
    parser.add_argument("--mode", default="accept",
                        choices=["request", "issue", "accept", "transfer"])
    parser.add_argument("--request", default="", help="файл заявки")
    parser.add_argument("--license", default="",
                        help="входной файл лицензии (приёмка, перенос)")
    parser.add_argument("--out", default="",
                        help="куда записать результат; у выпуска по умолчанию — "
                             "действующий файл лицензии, и тогда нужен --force")
    parser.add_argument("--force", action="store_true",
                        help="разрешить выпуск в действующий файл лицензии")
    parser.add_argument("--edition", default=offline.DEFAULT_EDITION,
                        help="запрашиваемое издание")
    parser.add_argument("--licensee", default="", help="лицензиат")
    parser.add_argument("--days", type=int, default=offline.DEFAULT_DAYS,
                        help="срок лицензии в днях")
    parser.add_argument("--note", default="", help="пометка в заявке")
    parser.add_argument("--host", default="", help="имя хоста для отпечатка (по умолчанию своё)")
    parser.add_argument("--public-key", dest="public_key", default="",
                        help="доверенный открытый ключ лицензии")
    parser.add_argument("--key", default="", help="файл ключа подписи (для выпуска)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    host = args.host or None
    # Предохранитель, поставленный после измеренной ошибки: первый прогон этого
    # инструмента указал путь лицензии во флаге --license, а приёмник выпуска
    # берётся из --out, поэтому молча перезаписался ДЕЙСТВУЮЩИЙ файл лицензии.
    # Молчаливая замена действующего файла — необратимое действие по
    # последствиям, поэтому теперь она требует явного --force.
    destination = pathlib.Path(args.out or str(edition.LICENSE_FILE)).resolve()
    if args.mode == "issue" and not args.out and not args.force:
        print(json.dumps({
            "ok": False, "state": "refused",
            "reason": ("выпуск в действующий файл лицензии "
                       f"({destination}) требует явного --force: укажите --out, "
                       "если это проверочный выпуск")},
            ensure_ascii=False, indent=2))
        return 1
    try:
        if args.mode == "request":
            data = offline.request(out=args.out or None, edition_name=args.edition,
                                   licensee=args.licensee, days=args.days,
                                   note=args.note, host=host)
        elif args.mode == "issue":
            data = offline.issue(
                args.request or str(offline.default_request_path()),
                str(destination),
                key=args.key or None, edition_name=args.edition if args.edition else "")
        elif args.mode == "accept":
            data = offline.accept(
                args.license or str(edition.license_path()),
                request_file=args.request or None,
                public_key=args.public_key or None, host=host)
        else:
            data = offline.transfer(
                args.license or str(edition.license_path()),
                out=args.out or None, note=args.note, days=args.days, host=host)
    except Exception as exc:  # noqa: BLE001 — причина называется, а не глотается
        print(json.dumps({"ok": False, "state": "refused",
                          "reason": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
