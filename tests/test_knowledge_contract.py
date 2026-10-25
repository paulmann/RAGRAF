"""Проверка контракта `knowledge_search` v1.

Контракт описывает единый поиск по эпизодам HARNESS (L1) и фактам RAGRAF (L2).
Проверка статическая: читает схему и примеры, не поднимает HARNESS/RAGRAF и не
обращается к сети. Она ловит то, что ломается молча:

  1. нарушение обязательных полей запроса/ответа;
  2. смешение доказательств: `session_id` в code-элементе или `path` в episode;
  3. ссылку `actors[].evidence[]` на несуществующий элемент lane;
  4. отсутствие `index_rev`/`source`/`degraded` у lane;
  5. неверную стратегию слияния (`rrf`);
  6. отсутствие обязательных определений в JSON Schema;
  7. BOM/CRLF в машинных файлах контракта.
"""

from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
CONTRACTS = REPO / "docs" / "03_implementation" / "contracts"
SCHEMA = CONTRACTS / "knowledge_search.v1.schema.json"
REQUEST = CONTRACTS / "knowledge_search.request.json"
RESPONSE = CONTRACTS / "knowledge_search.response.json"

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


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


print("=" * 78)
print("КОНТРАКТ knowledge_search v1")
print("=" * 78)

for path in (SCHEMA, REQUEST, RESPONSE):
    check(path.exists(), f"{path.name}: файл существует", str(path))
if FAIL:
    raise SystemExit(1)

schema = load(SCHEMA)
request = load(REQUEST)
response = load(RESPONSE)

print()
print("=" * 78)
print("СХЕМА")
print("=" * 78)
check(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema",
      "JSON Schema 2020-12")
check("$defs" in schema and isinstance(schema["$defs"], dict), "есть $defs")
for name in ("Routing", "EpisodeLane", "EpisodeItem", "CodeLane", "CodeItem",
             "Actor", "Fusion", "Limits", "Warning", "Error"):
    check(name in schema.get("$defs", {}), f"$defs.{name} объявлен")
episode_required = set((schema.get("$defs", {}).get("EpisodeItem", {})
                        .get("required") or []))
code_required = set((schema.get("$defs", {}).get("CodeItem", {})
                     .get("required") or []))
actor_required = set((schema.get("$defs", {}).get("Actor", {})
                      .get("required") or []))
check({"id", "session_id", "seq"} <= episode_required,
      "EpisodeItem требует id/session_id/seq", str(sorted(episode_required)))
check({"id", "path", "start_line"} <= code_required,
      "CodeItem требует id/path/start_line", str(sorted(code_required)))
check({"actor_id", "kind", "source", "evidence"} <= actor_required,
      "Actor требует actor_id/kind/source/evidence", str(sorted(actor_required)))

print()
print("=" * 78)
print("ЗАПРОС")
print("=" * 78)
check(request.get("schema") == "knowledge.search/v1", "schema = knowledge.search/v1")
check(isinstance(request.get("query"), str) and request["query"].strip(),
      "query непустой")
check(request.get("mode") in ("auto", "find_agent", "find_skill",
                              "find_code", "find_knowledge"),
      "mode допустим", str(request.get("mode")))
check(isinstance(request.get("include"), list)
      and set(request["include"]) <= {"episodes", "code", "skills"},
      "include допустим", str(request.get("include")))
check(isinstance(request.get("limit"), int)
      and 1 <= request["limit"] <= 50,
      "limit в 1..50", str(request.get("limit")))
budget = request.get("budget") or {}
check(500 <= int(budget.get("max_tokens", 0)) <= 20000,
      "budget.max_tokens в 500..20000", str(budget.get("max_tokens")))
check(request.get("provenance") in ("strict", "locator"),
      "provenance допустим", str(request.get("provenance")))

print()
print("=" * 78)
print("ОТВЕТ: БАЗОВЫЕ БЛОКИ")
print("=" * 78)
for key in ("schema", "query", "routing", "lanes", "fusion", "limits"):
    check(key in response, f"ответ содержит {key}")
check(response.get("schema") == "knowledge.search/v1", "schema ответа")
lanes = response.get("lanes") or {}
for lane_name in ("episodes", "code"):
    lane = lanes.get(lane_name) or {}
    check(lane_name in lanes, f"lane {lane_name} присутствует")
    check(bool(lane.get("source")), f"{lane_name}: source задан")
    check("index_rev" in lane, f"{lane_name}: index_rev задан")
    check(isinstance(lane.get("degraded"), bool), f"{lane_name}: degraded bool")
    check(isinstance(lane.get("items"), list), f"{lane_name}: items список")
routing = response.get("routing") or {}
check(routing.get("intent") in ("find_agent", "find_skill", "find_code",
                                "find_knowledge"), "routing.intent допустим")
check(0 <= float(routing.get("confidence", -1)) <= 1,
      "routing.confidence 0..1")
fusion = response.get("fusion") or {}
check(fusion.get("strategy") == "rrf", "fusion.strategy = rrf")
check(isinstance(fusion.get("k"), int) and fusion["k"] > 0,
      "fusion.k положительный", str(fusion.get("k")))
limits = response.get("limits") or {}
check(isinstance(limits.get("truncated"), bool), "limits.truncated bool")

print()
print("=" * 78)
print("ОТВЕТ: РАЗДЕЛЕНИЕ ДОКАЗАТЕЛЬСТВ")
print("=" * 78)
episode_items = (lanes.get("episodes") or {}).get("items") or []
code_items = (lanes.get("code") or {}).get("items") or []
check(bool(episode_items), "есть хотя бы один episode")
check(bool(code_items), "есть хотя бы один code item")
for item in episode_items:
    check("session_id" in item and "seq" in item,
          "episode: session_id+seq", str(item.get("id")))
    check("path" not in item, "episode: нет path (не смешивается с кодом)",
          str(item.get("id")))
for item in code_items:
    check("path" in item and "start_line" in item,
          "code: path+start_line", str(item.get("id")))
    check("session_id" not in item, "code: нет session_id",
          str(item.get("id")))

print()
print("=" * 78)
print("ОТВЕТ: АКТОРЫ И ССЫЛКИ")
print("=" * 78)
lane_ids = {
    "episodes": {str(item.get("id")) for item in episode_items},
    "code": {str(item.get("id")) for item in code_items},
    "skills": {str(item.get("name")) for item in
               ((lanes.get("skills") or {}).get("items") or [])},
}
actors = response.get("actors") or []
check(bool(actors), "есть хотя бы один actor")
for actor in actors:
    check(bool(actor.get("actor_id")), "actor_id задан")
    check(actor.get("kind") in ("root_session", "subagent", "team_member",
                                "autoclaw_agent", "unknown"),
          "actor.kind допустим", str(actor.get("kind")))
    check(actor.get("source") in ("harness", "autoclaw"),
          "actor.source допустим", str(actor.get("source")))
    check(isinstance(actor.get("evidence"), list), "actor.evidence список")
    for ev in actor.get("evidence") or []:
        lane = str(ev.get("lane"))
        ref = str(ev.get("id"))
        check(lane in lane_ids, f"evidence lane допустим: {lane}")
        check(ref in lane_ids.get(lane, set()),
              f"evidence id существует: {lane}/{ref}")

print()
print("=" * 78)
print("ФАЙЛЫ: КОДИРОВКА")
print("=" * 78)
for path in (SCHEMA, REQUEST, RESPONSE):
    raw = path.read_bytes()
    check(not raw.startswith(b"\xef\xbb\xbf"), f"{path.name}: нет BOM")
    check(b"\r\n" not in raw, f"{path.name}: переводы строк LF")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
