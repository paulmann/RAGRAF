# RAGRAF documentation

**A graph-and-vector knowledge base of code for LLM agents.** RAGRAF indexes a repository into
three SQLite stores inside the indexed project — a symbol and call graph, an FTS5 lexicon with
vectors, and a revision and drift ledger — and answers an agent's questions through a **fixed set
of 38 operations** (40 tools over MCP). Every answer carries a `path:line` reference and its
provenance, so a fact can be checked instead of believed.

This site renders the documentation that ships in the repository. Nothing here is written twice:
if a page below diverges from the code, that is a defect — the project's
[defect register](DEFECTS.md) treats a doc↔code divergence the same way it treats a wrong answer.

## Where else the project lives

| Where | What is there |
|---|---|
| [ragraf.ru](https://ragraf.ru) | the public website: capabilities, measured benchmarks, an emulator of the admin panel |
| [GitHub repository](https://github.com/paulmann/RAGRAF) | source, README (English and Russian), releases, issues, roadmap |
| [Wiki](https://github.com/paulmann/RAGRAF/wiki) | 15 task-oriented pages: getting started, CLI, MCP, CPG, troubleshooting, FAQ |
| [Discussions](https://github.com/paulmann/RAGRAF/discussions) | questions, ideas, and measurements on your own corpus |

## Start here

| I want to… | Read |
|---|---|
| Understand what the product is and how it was measured | [What this is and why](https://github.com/paulmann/RAGRAF#what-this-is-and-why) in the README |
| Install it | [README → Installation](https://github.com/paulmann/RAGRAF#installation) |
| Understand the architecture and the store schemas | [Principles and components](architecture/ARCHITECTURE.md) |
| Check the requirements and the acceptance criteria | [Specification](architecture/SPECIFICATION.md) · [Acceptance and measurements](architecture/ACCEPTANCE.md) |
| Attach the index to an agent over MCP | [Attaching to an agent](implementation/INTEGRATION.md) |
| Turn on the code-property-graph layer (Joern, JDK) | [Code property graph](implementation/CPG_INTEGRATION.md) |
| Work with the index as an agent: operations, recovery | [Skill overview](skills/ragraf-core/SKILL.md) · [operations](skills/ragraf-core/reference/operations.md) · [repair](skills/ragraf-core/reference/repair.md) |
| Read the long-form account of the design | [RAGRAF article](article/RAGRAF_ARTICLE.md) |
| See what is known to be broken, with evidence | [Defect register](DEFECTS.md) |
| Read the Russian originals | [Русская документация](ru/02_architecture/ARCHITECTURE.md) |

## The contract this documentation is held to

* **A fact without a reference is a guess.** Claims carry a `file:line`, a command with its output,
  or a measurement artifact under `bench/results/`.
* **Numbers are checked against the code**, not against earlier prose: the facade declares 38
  `op_*` operations and the MCP server 40 `ragraf_*` tools in the published revision.
* **Degradation is named, never silent.** When the Joern layer is absent the answer says
  `capability=degraded` and explains which operation is unavailable — the documentation says the
  same thing in [the CPG page](implementation/CPG_INTEGRATION.md).

Found a page that disagrees with the code? That is one of the most welcome reports in this
project — see [CONTRIBUTING](https://github.com/paulmann/RAGRAF/blob/main/CONTRIBUTING.md) and
open an issue with the **Doc divergence** template.
