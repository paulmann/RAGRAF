# -*- coding: utf-8 -*-
"""Темы документного графа: кластеры, адреса, сводки (этап 5).

Шесть утверждений, которых чтением не подтвердить:

  1. `theme_id` стабилен: он равен sha1(seed + отсортированные участники) и не
     зависит от порядка обхода — иначе тема «переезжает» между прогонами.
  2. Тема без участников не выдаётся: участник — факт, у каждого свой адрес.
  3. Сводка без цитат не выдаётся и не записывается; цитата вне списка цитат
     участников — отказ разбора, а не «почти подошло».
  4. «Тем нет» отличается от «слой не собран»: у второго есть причина слоя, у
     первого — названное действие.
  5. Гейт изданий стоит на обеих операциях (`themes`, `global_overview`):
     возможность `doc-graph`, издание pro; под Community — отказ с названием.
  6. Область (`scope`) сужает темы по адресам участников, а не по догадке.

Песочница: свой каталог состояния, свой корпус, поддельные провайдеры вместо
сети. Живое состояние не трогается.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

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


def doc_text(words: str) -> str:
    return (f"# {words}\n\n"
            f"{words} обсуждается здесь: {words} и порядок работы. "
            "Абзац достаточно длинный, чтобы цитата нашлась в чанке целиком.")


class FakeExtractor:
    """Поддельный провайдер извлечения: сущности по словам в тексте чанка."""

    WORDS = ("alpha", "beta", "gamma", "delta", "solo")

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: dict) -> dict:
        self.calls += 1
        messages = (request.get("json") or {}).get("messages") or []
        content = str(messages[-1].get("content") or "") if messages else ""
        text = content.rsplit("\n\n", 1)[-1]
        quote = text[:60] if len(text) >= 60 else text
        found = [w for w in self.WORDS if w in text]
        entities = [{"name": w, "kind": "concept", "quote": quote} for w in found]
        relations = []
        if len(found) >= 2:
            relations.append({"from": found[0], "to": found[1], "kind": "рядом",
                              "quote": quote})
        payload = {"entities": entities, "relations": relations}
        body = {"choices": [{"message": {"role": "assistant",
                                        "content": json.dumps(payload,
                                                              ensure_ascii=False)}}]}
        return {"status": 200, "body": json.dumps(body, ensure_ascii=False)}


class FakeWriter:
    """Поддельный провайдер сводок: отвечает по списку цитат из запроса."""

    def __init__(self, mode: str = "good") -> None:
        self.mode = mode
        self.calls = 0

    def __call__(self, request: dict) -> dict:
        self.calls += 1
        messages = (request.get("json") or {}).get("messages") or []
        content = str(messages[-1].get("content") or "") if messages else ""
        body_text = content.rsplit("\n\n", 1)[-1]
        try:
            payload = json.loads(body_text)
        except ValueError:
            payload = {}
        quotes = payload.get("quotes") or []
        citations = [q.get("locator") for q in quotes][:2]
        if self.mode == "no_citations":
            citations = []
        elif self.mode == "invented":
            citations = ["docs/нет-такого.md:1-2"]
        text = ("Участники темы обсуждаются в одних и тех же местах "
                f"({payload.get('title', '')}).")
        answer = {"summary": text, "citations": citations}
        body = {"choices": [{"message": {"role": "assistant",
                                        "content": json.dumps(answer,
                                                              ensure_ascii=False)}}]}
        return {"status": 200, "body": json.dumps(body, ensure_ascii=False)}


def make_corpus(root: Path, project: str, docs: dict[str, str]) -> Path:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    for name, text in docs.items():
        (root / "docs" / name).write_text(text, encoding="utf-8", newline="\n")
    for name in ("alpha.py", "beta.py", "gamma.py"):
        (root / "src" / name).write_text(
            f'"""Модуль {name}."""\n\n\ndef f_{name.split(".")[0]}(x):\n    return x\n',
            encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text("probe\n", encoding="utf-8", newline="\n")
    return root


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_themes_"))
    (tmp / "catalog").mkdir()
    os.environ["RAGRAF_CATALOG_DIR"] = str(tmp / "catalog")
    os.environ["RAGRAF_HOME"] = str(tmp / "home")
    os.environ["RAGRAF_CONFIG_DIR"] = str(tmp / "config")
    os.environ["RAGRAF_SECRETS_KEY"] = base64.b64encode(
        secrets.token_bytes(32)).decode("ascii")
    os.environ["RAGRAF_AGENT"] = "test-docgraph-themes"

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    from ragraf import docgraph as dg  # noqa: E402
    from ragraf import docgraph_extract as ex  # noqa: E402
    from ragraf import edition  # noqa: E402
    from ragraf import llm as llm_mod  # noqa: E402
    from ragraf import settings as settings_mod  # noqa: E402
    from ragraf import themes as themes_mod  # noqa: E402
    from ragraf.config import load as load_config  # noqa: E402
    from ragraf.facade import Facade  # noqa: E402
    from ragraf.indexer import Indexer  # noqa: E402
    from ragraf.init_corpus import create_config  # noqa: E402

    os.environ[edition.LICENSE_ENV] = str(tmp / "absent-machine.license")
    os.environ.pop(edition.DECLARED_ENV, None)
    os.environ.pop(edition.BASELINE_ENV, None)

    print("=" * 78)
    print("0. ПЕСОЧНИЦА: корпус, индекс, слой с двумя кластерами")
    print("=" * 78)
    corpus = tmp / "corpus"
    make_corpus(corpus, "themes", {
        "a1.md": doc_text("alpha beta"),
        "a2.md": doc_text("alpha beta"),
        "b1.md": doc_text("gamma delta"),
        "b2.md": doc_text("gamma delta"),
    })
    cfg_path, _ = create_config(corpus, project="themesprobe",
                                config_path=corpus / "corpus.yaml")
    text = cfg_path.read_text(encoding="utf-8").replace("joern_enabled: true",
                                                        "joern_enabled: false")
    cfg_path.write_text(text, encoding="utf-8", newline="\n")
    cfg = load_config(cfg_path)
    indexer = Indexer(cfg, mode="fast")
    try:
        report = indexer.run(with_vectors=False)
    finally:
        indexer.close()
    rev = str(report.rev_id or "")
    check(bool(report.ok) and bool(rev), "индекс собран", f"ok={report.ok} rev={rev}")

    settings_mod.SettingsStore().save({
        "llm_enabled": True, "llm_provider": "local", "llm_model": "fake-model",
        "llm_prompt_version": "docgraph-v1", "llm_secret_name": "",
        "llm_cache_enabled": True,
    })
    values = dict(settings_mod.DEFAULTS)
    values.update({"llm_enabled": True, "llm_provider": "local",
                   "llm_model": "fake-model", "llm_prompt_version": "docgraph-v1",
                   "llm_secret_name": "", "llm_cache_enabled": True})
    provider = FakeExtractor()
    filled = ex.run(cfg, rev, values=values,
                    client=llm_mod.LLMClient(
                        cfg, config=llm_mod.config_from_settings(values),
                        transport=provider))
    check(filled.get("collected") is True, "слой фактов собран",
          json.dumps(filled.get("reason"), ensure_ascii=False)[:150])

    conn = sqlite3.connect(str(cfg.lexvec_db))
    conn.row_factory = sqlite3.Row
    try:
        built_themes = int(filled["stats"].get("themes") or 0)
        entities = conn.execute("SELECT COUNT(*) AS c FROM entity WHERE rev_id = ?",
                                (rev,)).fetchone()["c"]
        themes = themes_mod.read(conn, rev)
    finally:
        conn.close()
    print(f"  сущностей {entities}, тем {built_themes}")
    check(int(entities) >= 4, "в слое не меньше четырёх сущностей", str(entities))
    check(built_themes >= 2, "тем построено не меньше двух", str(built_themes))

    # ---------------- 1. theme_id стабилен и вычислим ----------------
    print()
    print("=" * 78)
    print("1. THEME_ID ВЫЧИСЛИМ: seed + ОТСОРТИРОВАННЫЕ УЧАСТНИКИ")
    print("=" * 78)
    check(themes["count"] >= 2, "чтение отдало темы", str(themes["count"]))
    mismatched = []
    for theme in themes["themes"]:
        member_ids = sorted(m["entity_id"] for m in theme["members"])
        expected = dg.theme_id(theme["seed_entity_id"], member_ids)
        if expected != theme["theme_id"]:
            mismatched.append((theme["title"], expected[:8], theme["theme_id"][:8]))
    check(not mismatched, "theme_id равен sha1(seed + отсортированные участники)",
          str(mismatched[:2]))

    repeat_conn = sqlite3.connect(str(cfg.lexvec_db))
    try:
        again = themes_mod.build(repeat_conn, rev, store_rows=False)
    finally:
        repeat_conn.close()
    ids_first = sorted(t["theme_id"] for t in themes["themes"])
    ids_second = sorted(t["theme_id"] for t in again["themes"])
    check(ids_first == ids_second, "повторная сборка даёт те же идентификаторы",
          f"{ids_first[:2]} против {ids_second[:2]}")
    check(all(t["size"] >= 2 for t in themes["themes"]),
          "в теме не меньше двух участников",
          str([(t["title"], t["size"]) for t in themes["themes"][:3]]))

    # ---------------- 2. участники и адреса ----------------
    print()
    print("=" * 78)
    print("2. У КАЖДОГО УЧАСТНИКА И У КАЖДОЙ ТЕМЫ ЕСТЬ АДРЕС")
    print("=" * 78)
    members = [m for t in themes["themes"] for m in t["members"]]
    check(bool(members), "участники есть", str(len(members)))
    check(all(m["citations"] for m in members),
          "у каждого участника есть адрес цитаты",
          str([m["name"] for m in members if not m["citations"]][:3]))
    check(all(str(m["locator"]).count(":") >= 1 for m in members),
          "адрес участника в форме path:строка",
          str([m["locator"] for m in members[:2]]))
    check(all(t["citations"] for t in themes["themes"]),
          "у каждой темы есть адреса цитат")

    # ---------------- 3. сводки: только с цитатами ----------------
    print()
    print("=" * 78)
    print("3. СВОДКА БЕЗ ЦИТАТ НЕ ВЫДАЁТСЯ И НЕ ЗАПИСЫВАЕТСЯ")
    print("=" * 78)
    good = FakeWriter("good")
    conn = sqlite3.connect(str(cfg.lexvec_db))
    conn.row_factory = sqlite3.Row
    try:
        stats = themes_mod.summaries(
            conn, rev,
            llm_mod.LLMClient(cfg, config=llm_mod.config_from_settings(values),
                              transport=good), model="fake-model")
        read_after = themes_mod.read(conn, rev)
    finally:
        conn.close()
    check(int(stats.get("written") or 0) == len(themes["themes"]),
          "сводки записаны для всех тем", str(stats)[:200])
    check(all((t.get("summary") or {}).get("citations") for t in read_after["themes"]),
          "у каждой сводки есть цитаты",
          str([t["title"] for t in read_after["themes"] if not (t.get("summary") or {}).get("citations")]))
    summary_addresses = [str(((t.get("summary") or {}).get("citations") or [""])[0])
                         for t in read_after["themes"] if t.get("summary")]
    check(all(address.count(":") >= 1 for address in summary_addresses),
          "адрес цитаты сводки в форме path:строка", str(summary_addresses[:2]))

    # Пустые цитаты и выдуманный адрес — отказ разбора, а не «почти подошло».
    def writer_values(model: str) -> dict:
        """Настройки поддельного провайдера с СВОЕЙ моделью.

        Кэш ответов адресуется моделью вместе с промптом и входом. Если оставить
        ту же модель, негативный случай получит закэшированный хороший ответ, и
        проверка будет охранять кэш вместо разбора.
        """
        return dict(values, llm_model=model)

    conn = sqlite3.connect(str(cfg.lexvec_db))
    conn.row_factory = sqlite3.Row
    try:
        empty = themes_mod.summaries(
            conn, rev, llm_mod.LLMClient(
                cfg, config=llm_mod.config_from_settings(writer_values("fake-no-cite")),
                transport=FakeWriter("no_citations")))
        invented = themes_mod.summaries(
            conn, rev, llm_mod.LLMClient(
                cfg, config=llm_mod.config_from_settings(writer_values("fake-invented")),
                transport=FakeWriter("invented")))
    finally:
        conn.close()
    check(int(empty.get("written") or 0) == 0 and int(empty.get("rejected") or 0) >= 1,
          "сводка без цитат отвергнута", str(empty)[:200])
    check(int(invented.get("written") or 0) == 0
          and int(invented.get("rejected") or 0) >= 1,
          "цитата вне списка отвергнута", str(invented)[:200])

    # Запись с пустыми цитатами (в обход разбора) не должна показываться.
    conn = sqlite3.connect(str(cfg.lexvec_db))
    conn.row_factory = sqlite3.Row
    try:
        theme_id = read_after["themes"][0]["theme_id"]
        conn.execute(
            "UPDATE theme_summary SET citations_json = '[]' WHERE rev_id = ? "
            "AND theme_id = ?", (rev, theme_id))
        conn.commit()
        stripped = [t for t in themes_mod.read(conn, rev)["themes"]
                    if t["theme_id"] == theme_id][0]
    finally:
        conn.close()
    check(stripped.get("summary") is None,
          "сводка с пустыми цитатами не выдаётся даже из хранилища",
          str(stripped.get("summary"))[:120])

    # ---------------- 4. гейт изданий и поверхности ----------------
    print()
    print("=" * 78)
    print("4. ГЕЙТ: ОБЕ ОПЕРАЦИИ ЗАКРЫТЫ ИЗДАНИЕМ, ЛОКАЛЬНОЕ — НЕТ")
    print("=" * 78)
    fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
    try:
        denied_themes = fc.op_themes(limit=5)
        denied_overview = fc.op_global_overview(limit=5)
    finally:
        fc.close()
    for title, answer in (("themes", denied_themes), ("global_overview", denied_overview)):
        check(answer.get("found") is False
              and "EditionRefusal" in str(answer.get("error")),
              f"{title}: под Community отказ с причиной издания",
              str(answer.get("error"))[:160])
        check(((answer.get("refused") or {}).get("feature") == "doc-graph"),
              f"{title}: отказ называет возможность doc-graph",
              str(answer.get("refused"))[:160])

    from ragraf import mcp_server  # noqa: E402

    baseline = json.loads(Path(edition.baseline_path()).read_text(encoding="utf-8"))
    declared = [item for item in baseline.get("features") or []
                if {"themes", "global_overview"} <= set(item.get("operations") or [])]
    check(len(declared) == 1 and declared[0].get("name") == "doc-graph"
          and declared[0].get("edition") == "pro",
          "обе операции объявлены возможностью doc-graph (pro)",
          str([d.get("name") for d in declared]))
    for name in ("ragraf_themes", "ragraf_global_overview"):
        check(name in [tool.get("name") for tool in mcp_server.TOOLS],
              f"{name}: объявлен в TOOLS")
        check(bool((mcp_server._TOOL_META.get(name) or {}).get("readOnlyHint")),
              f"{name}: только чтение в метаданных")

    # ---------------- 5. разрешённая возможность: темы с адресами ----------------
    print()
    print("=" * 78)
    print("5. ПРИ РАЗРЕШЁННОЙ ВОЗМОЖНОСТИ: ТЕМЫ, ОБЛАСТЬ, КАРТА")
    print("=" * 78)
    variant = tmp / "baseline-docgraph-community.json"
    data = json.loads(Path(edition.baseline_path()).read_text(encoding="utf-8"))
    for item in data["features"]:
        if item["name"] == "doc-graph":
            item["edition"] = "community"
    variant.write_bytes(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    os.environ[edition.BASELINE_ENV] = str(variant)
    try:
        fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        try:
            opened = fc.op_themes(limit=5)
            scoped = fc.op_themes(scope="docs/b", limit=5)
            narrow = fc.op_themes(scope="src", limit=5)
            overview = fc.op_global_overview(limit=5)
        finally:
            fc.close()
    finally:
        os.environ.pop(edition.BASELINE_ENV, None)
    check(opened.get("found") is True, "themes отдаёт темы",
          str(opened.get("error"))[:160])
    check(all(t["citations"] for t in (opened.get("result") or {}).get("themes") or []),
          "у каждой темы в ответе есть адреса")
    scoped_titles = [t["title"] for t in (scoped.get("result") or {}).get("themes") or []]
    check(bool(scoped_titles), "область docs/b нашла тему", str(scoped_titles))
    check(all(t["size_in_scope"] <= t["size"]
              for t in (scoped.get("result") or {}).get("themes") or []),
          "размер в области не больше полного")
    check(narrow.get("found") is False
          and "области" in str((narrow.get("result") or {}).get("reason") or ""),
          "область без упоминаний названа словами",
          str((narrow.get("result") or {}).get("reason"))[:160])
    counts = (overview.get("result") or {}).get("counts") or {}
    check(int(counts.get("entities") or 0) >= 4, "карта считает сущности", str(counts))
    check(int(counts.get("themes") or 0) >= 2, "карта считает темы", str(counts))
    tops = (overview.get("result") or {}).get("top_themes") or []
    check(bool(tops) and all(t["citations"] for t in tops),
          "топ-темы карты приходят с адресами", str(tops[:1])[:160])

    # ---------------- 6. «тем нет» против «слой не собран» ----------------
    print()
    print("=" * 78)
    print("6. «ТЕМ НЕТ» И «СЛОЙ НЕ СОБРАН» — РАЗНЫЕ ОТВЕТЫ")
    print("=" * 78)
    thin = tmp / "thinprobe"
    make_corpus(thin, "thin", {"solo.md": doc_text("solo")})
    cfg3_path, _ = create_config(thin, project="thinprobe",
                                 config_path=thin / "corpus.yaml")
    text3 = cfg3_path.read_text(encoding="utf-8").replace("joern_enabled: true",
                                                          "joern_enabled: false")
    cfg3_path.write_text(text3, encoding="utf-8", newline="\n")
    cfg3 = load_config(cfg3_path)
    indexer = Indexer(cfg3, mode="fast")
    try:
        report3 = indexer.run(with_vectors=False)
    finally:
        indexer.close()
    rev3 = str(report3.rev_id or "")
    ex.run(cfg3, rev3, values=values,
           client=llm_mod.LLMClient(cfg3, config=llm_mod.config_from_settings(values),
                                    transport=FakeExtractor()))
    os.environ[edition.BASELINE_ENV] = str(variant)
    try:
        fc = Facade(cfg3, tool="selftest", telemetry=False, watch=False)
        try:
            no_pairs = fc.op_themes(limit=5)
        finally:
            fc.close()
    finally:
        os.environ.pop(edition.BASELINE_ENV, None)
    block = no_pairs.get("degraded") or {}
    check(no_pairs.get("found") is False and block.get("code") == "themes_not_built",
          "собранный слой без пар отвечает «тем нет» своим кодом", str(block)[:160])
    check("docgraph themes" in str(block.get("text")),
          "причина называет действие, а не только состояние", str(block.get("text"))[:160])

    empty = tmp / "emptylayer"
    make_corpus(empty, "empty", {"a1.md": doc_text("alpha beta")})
    cfg2_path, _ = create_config(empty, project="emptylayer",
                                 config_path=empty / "corpus.yaml")
    text2 = cfg2_path.read_text(encoding="utf-8").replace("joern_enabled: true",
                                                          "joern_enabled: false")
    cfg2_path.write_text(text2, encoding="utf-8", newline="\n")
    cfg2 = load_config(cfg2_path)
    indexer = Indexer(cfg2, mode="fast")
    try:
        indexer.run(with_vectors=False)
    finally:
        indexer.close()
    os.environ[edition.BASELINE_ENV] = str(variant)
    try:
        fc = Facade(cfg2, tool="selftest", telemetry=False, watch=False)
        try:
            unsorted = fc.op_themes(limit=5)
        finally:
            fc.close()
    finally:
        os.environ.pop(edition.BASELINE_ENV, None)
    check(unsorted.get("found") is False and bool(unsorted.get("error")),
          "несобранный слой назван ошибкой с причиной, а не «тем нет»",
          str(unsorted.get("error"))[:160])

    # ---------------- 7. CLI ----------------
    print()
    print("=" * 78)
    print("7. CLI: ТЕМЫ СТРОЯТСЯ БЕЗ МОДЕЛИ")
    print("=" * 78)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env[edition.BASELINE_ENV] = str(variant)
    cli = subprocess.run([sys.executable, str(REPO / "tools" / "ragraf.py"),
                          "-p", str(cfg_path), "--json", "docgraph", "themes"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=env, timeout=600)
    payload = {}
    try:
        payload = json.loads(cli.stdout or "{}")
    except ValueError:
        payload = {}
    check(cli.returncode == 0, "docgraph themes завершился успешно",
          (cli.stderr or "")[-200:])
    check(int(payload.get("themes") or 0) >= 2,
          "команда построила темы", str(payload.get("themes")))

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
