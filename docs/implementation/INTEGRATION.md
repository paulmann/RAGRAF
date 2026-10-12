# RAGRAF Agent Integration Regimen

This document describes how an agent must work with the knowledge base: what to call, in which order, and what must never be done. The texts below are ready to be copied into the project's `AGENTS.md` and into the skill.

---

## 1. Connecting

### 1.1 MCP server (primary channel)

MCP client configuration:

```json
{
  "mcpServers": {
    "ragraf": {
      "command": "python",
      "args": [
        "<repository>/tools/ragraf-mcp.py",
        "--project",
        "<repository>/config/myproject.yaml"
      ]
    }
  }
}
```

> *Note:* `config/` is machine-local and git-ignored. Create your corpus config with `ragraf init <project-dir>` (it prints the path), or start from `examples/ragraf.example.yaml`.

The server declares **23 tools** with the `ragraf_` prefix (verified against `src/ragraf/mcp_server.py`): `index_status`, `drift_report`, `search`, `symbol`, `references`, `callers`, `callees`, `impact`, `slice`, `code`, `repo_map`, `schema`, `table_usage`, `routes`, `live_read`, `drift_ack`, `policy_trace`, `class_usage`, `write_check`, `usage`, `freshness`, `catalog`, `ensure`.

There is **no, and will be no,** free-form graph query tool (FT-15): an attempt to pass `MATCH ... RETURN` is rejected with code `-32602`.

### 1.2 CLI (debugging and manual operations)

```powershell
python tools/ragraf.py --project config/myproject.yaml status
python tools/ragraf.py --project config/myproject.yaml query impact --target "Core\Config::get" --depth 3
```

### 1.3 Pre-indexing

```powershell
python tools/ragraf.py --project config/myproject.yaml index
```

The first indexing downloads the embedding model (0.22 GB, once, into `<home>/.ragraf/models`). Afterwards — from cache only.

---

## 2. Agent working regimen

### 2.1 Before starting a task

```
1. ragraf_index_status   → learn the revision, parse completeness, available capabilities
2. ragraf_drift_report   → if the ledger is non-empty, account for divergences BEFORE any conclusions
```

**Why exactly this way.** The index and the code may diverge: the index is immutable within a revision, while the code has been edited. If the agent starts working without checking the ledger, it may rely on a fact that is already false — and not notice.

### 2.2 Answering a question about the code

| Agent's question | Operation |
|---|---|
| «where does this live?» | `ragraf_symbol`; when unsure — `ragraf_search` |
| «how is the module built?» | `ragraf_repo_map` (within a token budget; the `chars_used` field is the actual volume, not an estimate from a synthetic line) |
| «who calls this?» | `ragraf_callers` |
| «what does it call?» | `ragraf_callees` |
| «what breaks if I change it?» | `ragraf_impact` (mandatory before an edit) |
| «where else is it used?» | `ragraf_references` |
| «what data flows here?» | `ragraf_slice` (only at `capability=full`) |
| «what is in the table and who reads it?» | `ragraf_schema`, `ragraf_table_usage` |
| «what routes exist?» | `ragraf_routes` |
| «show me the code» | `ragraf_code` (only on explicit request) |
| «what is this fragment?» | `ragraf_search` with `with_text: true` — address and text in one call |
| «show this place in detail» | `ragraf_code` with `entry_ids` from the hit (`node_id`/`chunk_id`) |

### 2.2.1 Text comes from the index, not from disk

Measurement of 2026-10-12: `search` used to return only the hit's address, so the text had to be fetched with a SECOND call, and `code` always read the file from disk — even when the index answered exactly the same thing. Changes:

* **`search --with-text`** adds a `snippet` field to every hit — the beginning of the chunk's raw text from the index. The `text_from_index: true` marker confirms the text came from the database; if some hits have no text, that is visible in `limits` with the count of such hits, not as a silent empty string.
* **`code` returns text from the index by default**, not from disk. The source is named in every span by the `text_source` field (`index` or `disk`), and the answer's provenance matches the actual source. The previous behavior is available as `--source disk`; the corpus profile stays the same: the index does not become the source of «truth», it only spares redundant reading.
* **`code --entry-ids`** accepts `node_id` and/or `chunk_id` from `search` hits — previously they could not be passed anywhere. Unrecognized identifiers come back in an `unresolved` list instead of being dropped.

Cost and honesty: the `chunk_text` table stores raw chunk text; on revision rotation it is deleted together with them. Text in the index may lag behind the file — then `changed_since_index` fires, and the decision to trust remains with the caller.

### 2.3 Hard rules

1. **Reading whole files is the last resort.** Index operation first; `rg` and file reads — when the index answered «not found» or a fact is marked `stale`.
2. **A fact with `stale_warnings` must not be used as the basis for a conclusion.** First `ragraf_live_read` on that path.
3. **Function bodies do not arrive in `impact`, `slice`, `repo_map`.** This is deliberate: structural context is more precise and many times smaller. Code is requested separately — and, when needed, cheaper in one call: `search` with `with_text: true`, or `code` with `entry_ids` from the hit's address.
4. **Separate proven from heuristic.** In `impact` and `callers` answers the `stated` and `derived` blocks are separated. A conclusion resting on `derived` must be marked as requiring verification.
5. **Account for `capability`.** At `degraded`, slices and data flows are unavailable: the operation refuses with a reason. This is a limitation, not an empty result.
6. **Read `limits`.** Every answer lists its limitations (e.g. «dynamics not covered»). Ignoring them means concluding on an incomplete picture.
7. **The index is not updated during a change effort.** Edits are registered in the ledger, not in the index.

### 2.4 During the change effort

After edits:

```powershell
python tools/ragraf.py --project config/myproject.yaml drift --scan
```

This compares the index with the working tree and appends the differences to the ledger. **The index itself does not change** — that is fundamental.

If the edit extracted code into a new function or method, an entry of kind `slice_boundary` is created. This matters: such changes move slice borders, and conclusions around the affected function must be revisited after reindexing.

### 2.5 After the change effort is finished

```powershell
python tools/ragraf.py --project config/myproject.yaml reindex
```

What happens: a new index snapshot is built, the drift ledger is archived into `drift/archive/<from>_to_<to>.md` with three mandatory sections — «what changed in the corpus», «what was re-verified in the knowledge», «changed slice borders». Previous entries and the revision history are **not deleted**.

---

## 3. Text for the project's `AGENTS.md` (ready to paste)

```markdown
## Code knowledge base (RAGRAF)

Before working with code, use RAGRAF instead of reading files blind.

Order:
1. `ragraf_index_status` — index state.
2. `ragraf_drift_report` — if the ledger is non-empty, account for divergences first.
3. Questions about code — through operations: `ragraf_symbol`, `ragraf_callers`, `ragraf_impact`,
   `ragraf_search`, `ragraf_repo_map`.
4. `rg` and whole-file reads — only if the index answered «not found» or a fact is marked
   `stale` (then `ragraf_live_read` first).
5. Before an edit touching a public method or a table, `ragraf_impact` is mandatory —
   so consumers are not broken.
6. After edits: `ragraf drift --scan` (the index is NOT updated).
7. When the change effort is done: `ragraf reindex`.

Forbidden:
- writing graph queries (Cypher/CPGQL) — only the fixed operation set is available;
- relying on a `stale`-marked fact without a prior `ragraf_live_read`;
- treating `derived` links as proven;
- ignoring `limits` and `capability` in answers.
```

---

## 4. Skill description

**Name:** RAGRAF — code knowledge base
**When to apply:** any task requiring understanding of existing code: find where something is implemented; find out who calls it; assess the consequences of an edit; add functionality to an unfamiliar module.

**What it does:** queries the indexed base and returns verifiable facts about the code with file and line — instead of sequential file reading.

**Key rule:** the base answers **before** files are read; files are read only when the base honestly reported «not found» or that a fact is stale.

---

## 5. What real enablement requires

> **Translation note:** this status table describes the author's pilot machine at the time of writing. The referenced artifacts (`tests/test_mcp.py`, `config/myproject.yaml`, revision `rev-0012` of a 392-file corpus) belong to the private working environment and are **not shipped** in the public repository.

| Step | State (pilot machine, 2026-10) |
|---|---|
| MCP server written and test-verified | ✅ on the pilot (`tests/test_mcp.py` — *not shipped*) |
| Corpus configuration | ✅ `config/myproject.yaml` (*machine-local*) |
| Index built | ✅ revision `rev-0012`, 392 files (*pilot corpus*) |
| `AGENTS.md` in the pilot project | ⏳ **not modified**: the file belongs to another project; pasting the §3 text requires the owner's consent |
| MCP server registration in the client | ⏳ requires the user to edit the client configuration |
| Benefit measurements (M1, M3, M6) | ⏳ needs a task set and a no-index baseline |
