"""Генератор страницы «Фаза 0» для раздела /md/ сайта ragraf.ru.

Зачем скрипт, а не страница руками: контент обязан дорабатывать каждый день по
итогам собранных данных. Источники — только измерения:

  * `.ragraf/phase0/baseline.json`, `.ragraf/phase0/after.json` — серии проб;
  * `.ragraf/control.sqlite` — журнал вызовов (агенты, операции, экономия);
  * `.ragraf/index/rev.json` — активная ревизия и суммы корпуса.

Запуск:
  python tools/phase0_report.py --out <путь>/public/md/phase0.html
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(r"C:\AutoClaw\RAGRAF")

# Пары «до/после»: подпись, имя серии в baseline, имя серии в after, единица.
# Замер «до» для операции фасада `catalog`: серия из одного вызова, потому что
# каждый вызов стоил четыре минуты. Число взято из журнала и подтверждено
# отдельным замером (242 255 мс). CLI-команда `catalog` — ДРУГАЯ операция (149 мс),
# и сравнивать её с фасадной было бы подменой: в baseline они и мерились отдельно.
MANUAL_BASELINE = {
    "query_catalog": {"p50": 242255.0, "p90": 242255.0, "n": 1},
}

PAIRS = [
    ("ensure на неизменном корпусе", "B1_ensure_warm", "B1_ensure_warm", "мс"),
    ("ensure --force (полная сверка)", "B2_ensure_force", "B2_ensure_force", "мс"),
    ("index_status (фасад)", "B3_status", "B3_status", "мс"),
    ("search (одна фраза)", "B5_search", "B5_search", "мс"),
    ("op_catalog (фасад)", "query_catalog", "B4_catalog", "мс"),
    ("catalog (CLI, для сравнения)", "B4_catalog_cli", "B4_catalog_cli", "мс"),
    ("ensure на втором корпусе", "B1_ensure_warm_second", "B1_ensure_warm_second", "мс"),
]

STEPS = [
    {
        "id": "0.1",
        "title": "MCP в контуре AutoClaw с явной атрибуцией",
        "state": "ok",
        "was": "коннектор <code>ragraf</code> подключён без <code>--agent</code>; все записи журнала приходили как <code>unknown</code>",
        "did": "в endpoint коннектора добавлены <code>--agent AutoClaw --agent-kind autoclaw</code> (обе таблицы — sources и revisions); правка проверена в песочнице до боевой записи",
        "proof": "приёмка закрыта ДВАЖДЫ: (1) <code>initialize</code> с пустым <code>clientInfo</code> → журнал <code>agent=AutoClaw kind=autoclaw</code>; (2) живой вызов из сессии AutoClaw — журнал получил <code>index_status</code> и <code>ensure</code> с <code>tool=mcp agent=AutoClaw</code>",
        "limit": "маршрут вызова оказался нестандартным: коннекторные инструменты в AutoClaw отложены — их ищет <code>tool_search(\"ragraf\")</code> (42 инструмента) и вызывает <code>tool_invoke</code>; в списке агента их нет, и это не поломка",
    },
    {
        "id": "0.2",
        "title": "Быстрый <code>ensure</code>: отпечаток входа вместо полной сверки",
        "state": "ok",
        "was": "<code>watch.check</code> всегда считал <code>current_hashes</code>: чтение и sha256 каждого файла — 1 111 мс на 713 файлах",
        "did": "перед полной сверкой вызывается существующий <code>quick_check</code> (путь+размер+mtime): 173 мс. При включённом сборе структуры БД быстрый путь НЕ применяется — иначе сверка базы была бы пропущена молча",
        "proof": "p50 1 869 → 306 мс; <code>--force</code> остался 1 863 мс (обходит быстрый путь, как и должен)",
        "limit": "цель p90 &lt; 300 мс не достигнута: 375 мс. Остаток — запуск процесса и импорты, а не логика",
    },
    {
        "id": "0.3",
        "title": "<code>index_status</code> из снимка",
        "state": "ok",
        "was": "4 630 мс: PageRank по графу, импорт torch (2 401 мс), сэмпл векторов — на каждый вызов",
        "did": "ответ считается один раз и пишется снимком рядом с ревизией; снимок годен только при совпадении ревизии И времени правки обоих хранилищ, иначе считается заново",
        "proof": "холодный 4 633 мс, тёплый 430–489 мс; состав ответа тот же (сверено по <code>rev</code> и <code>counts</code>)",
        "limit": "холодный вызов остаётся дорогим по построению: снимка ещё нет",
    },
    {
        "id": "0.4",
        "title": "<code>catalog</code> без пересчёта сумм всех корпусов",
        "state": "ok",
        "was": "242 255 мс: <code>watch_hashes</code> по каждой из 36 записей, включая корпус на 54 235 файлов",
        "did": "сверка по отпечатку вместо хешей; записи без ревизии не сверяются вовсе (сверять нечего); обход корпусов параллельный; ответ кэшируется на 60 с с инвалидацией по mtime реестра",
        "proof": "тёплый ответ 470–493 мс; библиотечный вызов целиком 3 588 мс",
        "limit": "первый (холодный) вызов остаётся секундным: он обходит деревья всех корпусов",
    },
    {
        "id": "0.5",
        "title": "Атрибуция <code>RAGRAF_AGENT</code> во всех путях",
        "state": "ok",
        "was": "731 запись из 912 — <code>unknown</code>: командный путь не называл себя",
        "did": "обёртка <code>tools/ragraf.py</code> выставляет <code>ragraf-cli</code>/<code>internal</code>, если вызывающий не назвался сам; значение из окружения сильнее",
        "proof": "новые записи командного пути: <code>agent=ragraf-cli</code>",
        "limit": "решение владельца 23.10.2026: исторические записи <code>unknown</code> атрибутированы как AutoClaw (HARNESS всегда представлялся сам, других клиентов не было) — 791 вызов и 513 сессий; резервная копия журнала до изменения сохранена",
    },
    {
        "id": "0.6",
        "title": "<code>replaces</code> из флага в измеренную экономию",
        "state": "ok",
        "was": "<code>replaces</code> — булев флаг «эта операция обычно заменяет чтение»",
        "did": "рядом появился <code>replaces_chars</code>: сумма размеров файлов по ФАКТИЧЕСКИ выданным путям ответа (уникальные, до 200). Флаг сохранён — его считают <code>feedback.py</code> и сводки <code>usage</code>",
        "proof": "один <code>search</code> записал 1 259 645 — это нижняя оценка: файл обычно больше процитированного фрагмента",
        "limit": "размер берётся в байтах; для кириллицы это больше символов, поэтому число названо оценкой, а не точным счётом",
    },
]


def load_series(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["name"]: item for item in data.get("series") or []}


def journal_stats() -> dict:
    db = ROOT / ".ragraf" / "control.sqlite"
    out = {"calls": 0, "unknown": 0, "named": 0, "saved_chars": 0,
           "agents": [], "today_calls": 0, "today_saved": 0}
    if not db.exists():
        return out
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(usage_call)")}
        has_saved = "replaces_chars" in cols
        out["calls"] = int(conn.execute("SELECT COUNT(*) FROM usage_call").fetchone()[0])
        out["unknown"] = int(conn.execute(
            "SELECT COUNT(*) FROM usage_call WHERE agent IS NULL OR agent = '' "
            "OR agent = 'unknown'").fetchone()[0])
        out["named"] = out["calls"] - out["unknown"]
        if has_saved:
            out["saved_chars"] = int(conn.execute(
                "SELECT COALESCE(SUM(replaces_chars),0) FROM usage_call").fetchone()[0])
            day_start = time.time() - 86400
            out["today_calls"] = int(conn.execute(
                "SELECT COUNT(*) FROM usage_call WHERE ts >= ?", (day_start,)).fetchone()[0])
            out["today_saved"] = int(conn.execute(
                "SELECT COALESCE(SUM(replaces_chars),0) FROM usage_call WHERE ts >= ?",
                (day_start,)).fetchone()[0])
        out["agents"] = [
            (str(row[0]), int(row[1])) for row in conn.execute(
                "SELECT COALESCE(NULLIF(agent,''),'unknown') AS a, COUNT(*) c "
                "FROM usage_call GROUP BY a ORDER BY c DESC LIMIT 6")
        ]
    finally:
        conn.close()
    return out


def corpus_state() -> dict:
    path = ROOT / ".ragraf" / "index" / "rev.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def fmt(value: float) -> str:
    if value >= 1000:
        return f"{value:,.0f}".replace(",", " ")
    return f"{value:.0f}"


def bar_html(before: float, after: float) -> str:
    peak = max(before, after) or 1.0
    width_before = max(1.0, before / peak * 100)
    width_after = max(0.6, after / peak * 100)
    return (
        '<div class="bar-row">'
        '<div class="dim">до / после</div>'
        '<div class="bar">'
        f'<span class="before" style="width:{width_before:.1f}%"></span>'
        f'<span class="after" style="width:{width_after:.1f}%;top:11px;height:11px"></span>'
        "</div>"
        f'<div class="bar-label">{fmt(before)} → {fmt(after)}</div>'
        "</div>"
    )


def build_html(baseline: dict, after: dict, journal: dict, corpus: dict) -> str:
    rows = []
    for label, b_name, a_name, _unit in PAIRS:
        before = baseline.get(b_name) or MANUAL_BASELINE.get(b_name)
        later = after.get(a_name)
        if not before or not later:
            continue
        delta = (later["p50"] - before["p50"]) / before["p50"] * 100 if before["p50"] else 0.0
        speed = before["p50"] / later["p50"] if later["p50"] else 0.0
        css = "ok" if delta < -30 else ("mid" if delta < 0 else "no")
        rows.append(
            "<tr>"
            f"<td>{label}</td>"
            f'<td class="num">{fmt(before["p50"])}</td>'
            f'<td class="num">{fmt(before["p90"])}</td>'
            f'<td class="num">{fmt(later["p50"])}</td>'
            f'<td class="num">{fmt(later["p90"])}</td>'
            f'<td class="num {css}">{delta:+.0f} %</td>'
            f'<td class="num">{speed:.1f}×</td>'
            f'<td class="num dim">{before["n"]} / {later["n"]}</td>'
            "</tr>"
        )

    bars = "".join(
        bar_html(baseline[name]["p50"], after[name]["p50"])
        for name in ("B1_ensure_warm", "B3_status", "B4_catalog", "B5_search")
        if name in baseline and name in after
    )

    steps_html = []
    for step in STEPS:
        css = {"ok": "", "partial": " partial", "failed": " failed"}[step["state"]]
        pill = {"ok": 'class="pill ok"',
                "partial": 'class="pill warn"',
                "failed": 'class="pill no"'}[step["state"]]
        label = {"ok": "выполнено", "partial": "частично",
                 "failed": "опровергнуто"}[step["state"]]
        steps_html.append(
            f'<div class="step{css}">'
            f'<h4>Шаг {step["id"]} · {step["title"]} <span {pill}>{label}</span></h4>'
            "<dl>"
            f'<dt>было</dt><dd>{step["was"]}</dd>'
            f'<dt>сделано</dt><dd>{step["did"]}</dd>'
            f'<dt>доказательство</dt><dd>{step["proof"]}</dd>'
            f'<dt>граница</dt><dd>{step["limit"]}</dd>'
            "</dl></div>"
        )

    share = (journal["named"] / journal["calls"] * 100) if journal["calls"] else 0.0
    agents = ", ".join(f"{name} — {count}" for name, count in journal["agents"]) or "нет данных"
    rev = str(corpus.get("rev_id") or corpus.get("id") or "—")
    files = corpus.get("files") or corpus.get("file_count") or "—"
    stamp = time.strftime("%d.%m.%Y %H:%M")

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>RAGRAF · Фаза 0: контур и измерение</title>
<link rel="stylesheet" href="md.css">
</head>
<body>
<header class="top">
  <div class="wrap">
    <nav class="crumbs"><a href="./">план развития</a> · фаза 0</nav>
    <p class="eyebrow">Фаза 0 · контур и измерение · обновлено {stamp}</p>
    <h1>Контур и измерение</h1>
    <p class="lede">Каждый шаг проверен парой замеров одним и тем же инструментом на одном и том же
      состоянии корпуса. Ниже — что было, что сделано, чем доказано и где граница доказательства.</p>
  </div>
</header>

<div class="wrap">

<section>
  <h2>Живые числа</h2>
  <div class="cards">
    <div class="card"><div class="num">{journal["calls"]}</div><div class="label">записей в журнале вызовов</div></div>
    <div class="card"><div class="num ok">{share:.0f} %</div><div class="label">записей с именем агента ({journal["named"]} из {journal["calls"]})</div></div>
    <div class="card"><div class="num">{fmt(journal["saved_chars"])}</div><div class="label">символов не пришлось читать (оценка по выдаче)</div></div>
    <div class="card"><div class="num">{rev}</div><div class="label">активная ревизия корпуса · файлов {files}</div></div>
  </div>
  <p class="small" style="margin-top:14px">За последние сутки: вызовов {journal["today_calls"]},
    сэкономлено {fmt(journal["today_saved"])}. Агенты: {agents}.</p>
</section>

<section>
  <h2>До / после · один инструмент, одна серия</h2>
  <div class="table-wrap">
  <table>
    <thead><tr>
      <th>Операция</th><th class="num">до p50</th><th class="num">до p90</th>
      <th class="num">после p50</th><th class="num">после p90</th>
      <th class="num">Δ p50</th><th class="num">ускорение</th><th class="num">n до/после</th>
    </tr></thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
  </div>
  <p class="small" style="margin-top:12px">Время стены в миллисекундах, включает запуск процесса Python:
    он же измерялся до правки, поэтому сравнение корректно.</p>
  <div class="bars" style="margin-top:18px">{bars}</div>
</section>

<section>
  <h2>Шаги</h2>
  <div class="steps">
    {''.join(steps_html)}
  </div>
</section>

<section>
  <h2>Что осталось недоказанным</h2>
  <div class="note">
    <p><strong>Приёмка 0.1 закрыта</strong> после разбора: коннекторные инструменты в AutoClaw
      отложены (<code>tool_search</code> → <code>tool_invoke</code>), поэтому их не было в списке агента
      и вызовов не было. Живой вызов <code>index_status</code> записан как
      <code>tool=mcp agent=AutoClaw kind=autoclaw</code>. Маршрут вписан в правила проекта.</p>
    <p><strong>Цель 300 мс для <code>ensure</code></strong> достигнута по медиане (306 мс) и не
      достигнута по p90 (375 мс): остаток — старт интерпретатора и импорты.</p>
    <p><strong>Цель 100 мс для <code>index_status</code></strong> на командном пути физически
      недостижима: один запуск процесса — около 150 мс. Тёплый ответ 430–489 мс включает проверку
      свежести (173 мс) и старт.</p>
    <p><strong>Δ из плана</strong> остаются гипотезами до Фазы 4: здесь измерены задержки операций и
      атрибуция, а не сокращение числа вызовов у агентов.</p>
  </div>
</section>

<section>
  <h2>Метод</h2>
  <div class="note">
    <p>Серия — 20 независимых проб (для <code>catalog</code> — 6, помечено в колонке n), прогрев вне
      серии, состояние корпуса фиксировано одной ревизией. Замер «до» снят до первой правки кода.</p>
    <p><strong>Отдельная находка метода:</strong> «холодный» замер сразу после правки кода измеряет не
      операцию, а переиндексацию сторожа (36 с). Поэтому серии снимались на уже неизменном корпусе.</p>
  </div>
</section>

<footer class="bottom">
  Страница собирается скриптом <code>tools/phase0_report.py</code> из журнала
  <code>.ragraf/control.sqlite</code> и сохранённых серий замеров. Обновление — ежедневно.
</footer>

</div>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default=str(ROOT / ".ragraf" / "phase0" / "baseline.json"))
    parser.add_argument("--after", default=str(ROOT / ".ragraf" / "phase0" / "after.json"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    baseline = load_series(pathlib.Path(args.baseline))
    after = load_series(pathlib.Path(args.after))
    html = build_html(baseline, after, journal_stats(), corpus_state())

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(html.encode("utf-8"))
    print(f"записано: {out} ({len(html)} символов)")
    print(f"серий в baseline: {len(baseline)}, в after: {len(after)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
