# RAGRAF

<!-- md-i18n:switcher -->
**其他语言：** [English](README.md) · [Deutsch](README.de.md) · 中文 · [हिन्दी](README.hi.md) · [Español](README.es.md) · [Русский](README.ru.md)

**面向 LLM 智能体的图-向量代码知识库。**

[**官网**](https://ragraf.ru) · [**Wiki**](https://github.com/paulmann/RAGRAF/wiki) · [**文档（Pages）**](https://paulmann.github.io/RAGRAF/) · [**发行版**](https://github.com/paulmann/RAGRAF/releases) · [**讨论**](https://github.com/paulmann/RAGRAF/discussions)

![RAGRAF — 面向 LLM 智能体的图-向量代码知识库：61 个 MCP 工具、带 file:line 引用的可验证事实、完全本地运行](assets/social-preview.png)

一个回答智能体关于代码库问题的助手——*代码在哪里、谁在调用它、改动会破坏什么、附近类似功能是怎么做的*——给出可验证的事实（`file:line`），而不是盲目的 `rg` 和整文件通读。Python 3.13+、SQLite（图 + FTS5 + 向量在同一个文件里）、tree-sitter、无 torch 的本地嵌入。**一切都在代码所在的机器上运行**：不需要启动服务，索引不会上传到云端——这就是针对你仓库的本地 RAG 索引。可选的代码属性图层（Java + Joern）解锁切片；没有它，产品照常工作，并如实说明这一点。

---

## 目录

- [这是什么，以及为什么需要它](#这是什么以及为什么需要它)
- [使用场景](#使用场景)
- [功能](#功能)
- [用数字看项目](#用数字看项目)
- [实现原理](#实现原理)
- [安装](#安装)
- [第一步](#第一步)
- [环境要求与限制](#环境要求与限制)
- [文档](#文档)
- [开发](#开发)
- [参与](#参与)
- [许可证](#许可证)

---

## 这是什么，以及为什么需要它

在代码上做改动的智能体，把大部分上下文花在**定位**上：函数在哪里定义、谁在调用它、附近类似功能是怎么实现的。惯常做法是 `rg` 加整文件通读。

RAGRAF 用索引取代这条路径。智能体通过固定操作提问，得到带 `file:line` 引用的事实，而不是十个待查看的候选。

在真实项目上实测（PHP 门户，14 个导航任务；运行在作者的机器上完成——见下文「制品里究竟有什么」）：

| 指标 | 无索引 | 有索引 |
|---|---|---|
| 14 个任务的工具调用次数 | 28（现实值 43） | **14** |
| 每任务平均 | 2.0 / 3.1 | **1.0** |
| 待查看的候选 | 最多 34 个文件 | 1–5 个精确的 |
| 整文件读取 | 14（最少） | **0** |
| 首次查询即命中目标文件 | — | **92%**（13 次里 12 次） |
| 可验证性（带 `file:line` 引用的事实） | — | **100%** |

调用次数、命中率和可验证性是那次运行的**实测值**；它的原始制品不在发布树里（[`ROADMAP.md`](ROADMAP.md) 中的 `R-REL-1` 条目）。但交付里有一个图指标制品：[`bench/results/M2_rev-0012.json`](bench/results/M2_rev-0012.json) 和 [`M2_rev-0025.json`](bench/results/M2_rev-0025.json) 在同一个门户上测量 **impact 分析的完整度**——图是否能到达那些确实一起改动的文件。在 20 和 8 个提交、深度 3 时，平均召回 **0.771 → 0.917**，召回中位数 **1.0**，精确率 0.072 → 0.020（有意偏低：该指标衡量的是连通性，而不是预测），朴素基线为 **0.0**。

「现实值 43」是模型修正（智能体最多查看 5 个候选而不是 1 个），它记录在制品本身里。

一个通常被省略的重要限定：**2–3 倍的收益只属于定位类任务**（「在哪里定义」「谁调用」）。在「读 → 改 → 验」的改动循环里，调用次数几乎没有节省——那里的收益在别处：改动不会破坏索引，有争议的 SQL 在运行前就能校验。

---

## 使用场景

| 你在做什么 | 向 RAGRAF 问什么 | 会得到什么 |
|---|---|---|
| 把智能体带进一个 20 万行的陌生仓库 | `repo_map`，然后 `search` / `symbol` | 每个问题一个事实，带 `file:line` 引用，而不是一整面候选 |
| 重构一个被十几处调用的函数 | `callers`、`impact`、CPG 可用时的 `slice` | 已证明的关联与启发式关联分开；切片说明它究竟覆盖了什么 |
| 重命名或删除数据库列 | 在运行 SQL **之前**用 `write_check`，然后 `table_usage` | 缺失的列及最接近的名字；谁读、谁写这张表 |
| 审别人的 diff | `diff`、`drift_report`、`live_read` | 变化的符号与边（含回归），以及带差异标注的当前文件 |
| 找副本（`Files.php` … `Files7.php`） | `duplicates` | 哪些文件互为副本，哪一份**有证明地**是活跃版本 |
| 「代码里别处是怎么做的？」 | `search`（BM25 + 向量，混合检索） | 语料的排序片段；回答本身不需要嵌入模型 |
| 把索引接到智能体上 | **61 个 MCP 工具** `ragraf_*` 或 CLI | 同一批操作走 stdio——见 [`docs/ru/03_implementation/INTEGRATION.md`](docs/ru/03_implementation/INTEGRATION.md) |

---

## 功能

**54 个操作**作用于仓库——固定集合，没有面向图的查询语言：

| 操作 | 回答什么问题 |
|---|---|
| `search` | 关于这件事写在哪里（混合：BM25 词法 + 向量） |
| `search_multi` | 哪几处同时出现多个字符串；文件副本以及哪一份是活跃的 |
| `duplicates` | 哪些文件互为副本，以及靠什么证明主副本是活跃的 |
| `entrypoints` | 入口点：路由、HTTP 处理器、CLI、标记中的 endpoint |
| `dead_code` | 没有引用、不在可达范围内的文件与子程序 |
| `symbol` | 符号定义在哪里：种类、文件、行号、签名 |
| `references` | 还在哪里被使用 |
| `callers` / `callees` | 谁调用 / 调用了谁 |
| `impact` | 改动会影响什么（已证明关联与启发式关联分开） |
| `slice` | 结构化切片：节点、边、行号——不含函数体 |
| `code` | 符号的函数体（仅在明确请求时） |
| `repo_map` | 在给定字符预算内的仓库地图 |
| `schema` / `table_usage` | 数据库表里有什么，谁读它、谁写它 |
| `routes` | 路由与入口点 |
| `policy_trace` | 谁、依据什么条件获得权限 |
| `class_usage` | 样式类：定义在哪里、用在哪里、哪里不一致 |
| `write_check` | 能否这样写入（运行前把写入计划与模式对照） |
| `live_read` | 标出与索引之间差异的当前文件 |
| `freshness` | 索引是否最新（校验和比对） |
| `index_status` | 修订、覆盖度、降级、日志大小 |
| `drift_report` / `drift_ack` | 索引与代码之间差异的日志 |
| `catalog` | 语料登记册：哪些已索引、各自校验和是多少 |
| `calibrate` | 阈值校准 |
| `usage` | 调用遥测与效果评估 |
| `diff` | 两个修订之间变了什么：文件、符号、边、回归（P2-1） |
| `task_stats` | 按任务的报告：索引调用次数、闸门决策（§3.4） |
| `gate` | 对工具调用的闸门决策：`allow` / `ask` / `deny`（§3.2） |
| `note` / `notes` | 写入并读取一条决策笔记——跨会话的知识（P2-3） |

另外还有两种模式——不是操作，而是回答的形式：**`answer=true`** 返回带引用的成文回答，而不是结构化结果（P2-2）；`brief=true` 返回回答的简短外壳。`usage` 显示效果指标（`saved_calls`、`hit_rate`、`avg_ms`、`misses`、`stale_served`）。

同一批操作也可以作为 **61 个 MCP 工具**（`ragraf_*`）使用——用于作为服务器接入智能体，也可以通过 CLI 使用。单独一组工具是数据库活层：`ragraf_db_schema`、`ragraf_db_table`、`ragraf_db_relations`、`ragraf_db_sample`、`ragraf_db_drift`、`ragraf_db_cross`。
第二十四个是 `use_project`：当一个服务器服务多个项目时用来选择语料。`context` 回答选中了哪个语料、依据是什么：没有它，关于别的项目的回答与本项目的回答无法区分。

**十六种语言**按树解析（图的节点与边）：PHP、Python、JavaScript、TypeScript、Java、C#、C、C++、Ruby、Lua、Go、Perl、bash、PowerShell、CSS、HTML——十三个通用剖面加上 PHP、CSS 和 HTML 的专用提取器。另有十一种以纯文本接入——文件能被搜到，但不产生节点：Markdown、SQL、YAML、JSON、TOML、INI、XML、RST、Rust、batch、普通文本。共映射 55 种扩展名。

---

## 用数字看项目

下列所有数字都按 **2026-10-16** 的发布树实测——不是从旧文本里誊抄的。其中两个关键数字由 [`tools/Invoke-ProjectDoctrine.ps1`](tools/Invoke-ProjectDoctrine.ps1) 中的 `Test-DocumentedNumbers` 看守：它把文档与代码对照。

| 面 | 数量 | 从哪里测得 |
|---|---|---|
| 门面操作 | **54** | `src/ragraf/facade.py` 中的 `def op_*` |
| MCP 工具 | **61**——其中 6 个是数据库活层 | `src/ragraf/mcp_server.py` 中的 `ragraf_*` 名称 |
| MCP 资源 / 提示 | 3 + 1 个 URI 模板 / 2 | `ragraf://status`、`ragraf://catalog`、`ragraf://corpus/{name}/map`；提示 `ragraf-check`、`ragraf-repair` |
| CLI 子命令 / `query` 操作 | 21 / **50** | `src/ragraf/cli.py` 中的 `add_parser(…)` 和 `OPS` |
| 按树解析的语言 | **16** | `src/ragraf/config.py` 中的 `TREE_LANGS` |
| 以纯文本接入的语言 | **11** | `LANG_BY_EXT` 的其余取值 |
| 已映射的扩展名 | 55 | `LANG_BY_EXT` |
| 管理面板 API 路由 | 32 | `src/ragraf/admin_server.py` |
| 运行时依赖 | 7（+1 个可选 `tls` 集合） | `pyproject.toml` |
| `src/` 中的 Python | 67 个文件 · 52 527 行 | 发布树 |
| PowerShell——安装器、包装器、检查器 | 5 个文件 · 3 423 行 | 发布树 |
| Markdown | 28 个文件（`docs/` 中 20 个，其中 9 个俄语）+ 15 个 wiki 页面 | 发布树与 wiki |
| 可移植测试集 | 6 个集合 · **164 项检查，0 项失败** | 每个集合单独运行 |
| 缺陷登记册 | 61 项发现 | [`docs/DEFECTS.md`](docs/DEFECTS.md)；按严重度汇总的审计计入了其中 43 项 |
| 发布树 | 136 个文件 · 3.5 MB | 发布树 |

作为量级参考：RAGRAF 索引自己的工作树（648 个文件、140 741 行——大于这个发布子集），得到 3 620 个符号、46 296 条边和 3 417 个分块，耗时 **74 秒**，其中 63 秒是可选的 Joern/CPG 阶段（`rev-0156`，2026-10-16 实测；`capability=full`，0 个文件出错）。详细剖析见 [Wiki → Benchmarks and Limits](https://github.com/paulmann/RAGRAF/wiki/11-Benchmarks-and-Limits)。

**实测对照场，已脱敏。** 登记册里 **28 个语料，25 个已索引**——10 个带 CPG 层，15 个处于 `degraded` 模式。其中 16 个的修订记录读取于 2026-10-16：**7 527 个文件、94 528 个符号、791 871 条边、86 790 个分块**。项目名、路径和域名有意隐藏：标签按大小排序，只有作者的私有对照表能还原。

| 语料 | 类型 | 文件 | 符号 | 边 | 模式 |
|---|---|---|---|---|---|
| C-02 | PHP Web 应用 | 2 722 | 32 761 | 355 192 | degraded |
| C-04 | C# 服务 / 桌面应用 | 1 274 | 20 331 | 98 605 | degraded |
| C-05 | PHP Web 应用 | 928 | 17 381 | 99 029 | full |
| C-18 | PowerShell 自动化脚本集 | 81 | 324 | 4 214 | degraded |
| C-24 | Markdown + bash（最小） | 18 | 184 | 1 515 | degraded |

完整的 16 行表格、脱敏规则和一条计数说明在 [Wiki → Benchmarks and Limits](https://github.com/paulmann/RAGRAF/wiki/11-Benchmarks-and-Limits)。引用数字时这条说明很重要：对同一个修订，语料登记册和修订记录给出**不同**的合计（在同一语料上实测：8 721 对 4 498 个符号，41 201 对 56 106 条边），因此上面所有数字都取自**修订记录**——也就是响应信封所返回的那一份。

---

## 实现原理

每个语料**三个 SQLite 存储**，位于**被索引项目内部**的 `.ragraf/` 目录：

| 存储 | 里面有什么 |
|---|---|
| `graph.sqlite` | 节点（类、函数、方法）、边（调用、继承、导入）、数据库模式 |
| `lexvec.sqlite` | 同一个文件里的 FTS5 词法（BM25）与向量；用于片段输出的原始分块文本 |
| `control.sqlite` | 修订、差异日志、调用遥测 |

**索引流程：** `scan`（遍历与校验和）→ `ast`（tree-sitter：符号、调用、SQL）→ `load` → `resolve`（名称）→ `schema`（来自迁移的表）→ `cpg`（通过 Joern 的跨过程调用）→ `lexvec`（FTS5 + 向量）。

**三个决定行为的设计取舍**——每一个都源自一个实测缺陷：

1. **不可变索引加差异日志，取代「边用边更新」。** 在一次修订内索引不变；代码改动累积到日志里，受影响的事实标记为 `stale`。否则不同时刻的事实会不加区分地混在同一个回答里——错误结论正是这样产生的。
2. **不给智能体面向图的查询语言。** 只有固定操作。依据：在 LLMxCPG（USENIX Security '25）中，基础模型给出 132/1278 和 19/1278 个有效的 CPGQL 查询，而经过训练的模型是 1278/1278——也就是说，自由语言会变成错误生成器。
3. **每个事实都带证据。** `path:line`、`provenance`（`stated`——已证明，`derived`——启发式）、`index_rev`、`confidence`。没有证据的边之所以看起来可靠，只是因为它是「从数据库里」来的。

**降级而不是崩溃。** 缺少代码属性图会导致 `capability=degraded`，`slice` 操作给出说明实际模式的明确拒绝；其余操作照常工作。没有嵌入模型——词法检索照常工作。没有空闲端口——服务器说明原因。不存在静默降级。

---

## 安装

需要 **Python 3.13 或更新版本**。不需要管理员权限。

### 一条命令快速安装

安装器从 GitHub 下载包，并在对话中完成部署：

```powershell
iwr -OutFile install.ps1 https://raw.githubusercontent.com/paulmann/RAGRAF/main/install.ps1
pwsh -NoLogo -NoProfile -File .\install.ps1
```

它会询问安装目录（默认 `C:\RAGRAF`），创建虚拟环境并通过 `scripts/install.ps1` 安装依赖，提议查找 AutoClaw 和 DeepSeek Harness 并配置连接，最后打印管理面板的启动说明以及关于其访问的警告。

关于它不做什么，安装器说得很直接：**给 AutoClaw 的值它会显示，但不会写入**——AutoClaw 只通过自己的界面接受 MCP 服务器，而它的存储属于该产品。对 **Harness**，安装器能写入配置块：先备份，随后校验解析。已找到的安装不会被静默覆盖，而用 `-SkipDeps` 选项可以在不装依赖的情况下做一次彩排。

### Windows（PowerShell 7.6+）

```powershell
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
pwsh -NoLogo -NoProfile -File scripts/install.ps1
```

脚本会在 `~/.ragraf/venv` 创建虚拟环境、安装依赖，并**检查结果是否是一个可用的环境**——一步一步，最后给出汇总表。它不会「假装装好了」：每一步都以检查结束。

常用变体：

```powershell
# установить и сразу подготовить корпус для своего проекта
pwsh -File scripts/install.ps1 -Project C:\work\myproject

# то же плюс полная индексация (скачает модель эмбеддингов ~240 МБ)
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index

# индексация без векторов: модель не скачивается, лексический поиск работает
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index -NoVectors

# в сети с TLS-инспекцией (корпоративный прокси)
pwsh -File scripts/install.ps1 -WithTruststore
```

### 其他平台

一条通用路径，适用于任何具备 Python 3.13+ 的地方：

```bash
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -e .                # или: pip install -r requirements.txt
ragraf --version
```

不安装包产品也能工作——通过一个自己把 `src/` 加入路径的包装器：

```bash
python tools/ragraf.py --version
```

> `scripts/install.ps1` 已在 Windows 上验证（PowerShell 7.6、Python 3.14.7）。通过 `pip` 在 Linux 和 macOS 上的通用路径**未验证**——产品的开发和实测都在 Windows 上进行。

### 外部层：Java 与 Joern（可选）

跨过程图（`slice` 操作）需要 JDK 21 和 Joern。没有它们产品照样工作：索引会构建，检索和结构可用，`slice` 会带着原因拒绝。

```powershell
pwsh -File scripts/install-joern.ps1
```

归档放在 `.tools/downloads/`——脚本是幂等的。解压后的套件约占 5 GB。

---

## 第一步

```bash
# 1. Подготовить корпус: найти, досоздать, пересобрать отставший индекс.
#    Корпус для незнакомого каталога создаётся этой же командой.
ragraf ensure C:\work\myproject

# 2. Проверить окружение (отчёт по 11 компонентам).
#    ВНИМАНИЕ: команда ВСЕГДА возвращает код 0 — это не гейт, читайте отчёт.
ragraf -p <конфиг> selftest

# 3. Спросить
ragraf -p <конфиг> query search "задержка переиндексации"
ragraf -p <конфиг> query symbol --name corpus_hash
ragraf -p <конфиг> query code --name corpus_hash
ragraf -p <конфиг> query callers --name dedupe
ragraf -p <конфиг> query impact --target Facade --depth 2

# 4. Свежесть: изменился ли код после индексации
ragraf -p <конфиг> check
```

完整命令列表见 `ragraf --help`。重要：**全局选项 `--project/-p` 和 `--json` 写在子命令之前**（`ragraf -p cfg query search ...`），子命令自己的选项写在它之后。

### 语料管理

```bash
ragraf init C:\work\myproject         # создать конфигурацию корпуса и показать путь к ней
ragraf -p <конфиг> register           # зарегистрировать разработку в реестре
ragraf -p <конфиг> index              # полная индексация
ragraf -p <конфиг> reindex            # переиндексация + архивация журнала расхождений
ragraf -p <конфиг> drift --scan       # зарегистрировать различия, НЕ меняя индекс
ragraf -p <конфиг> status             # состояние: ревизия, покрытие, размер журнала
ragraf -p <конфиг> usage              # телеметрия: обращения, факты, оценка эффекта
```

### 作为 MCP 服务器接入智能体

```json
{
  "mcpServers": {
    "ragraf": {
      "command": "python",
      "args": [
        "/path/to/RAGRAF/tools/ragraf-mcp.py",
        "--project", "/path/to/RAGRAF/examples/mycorpus.yaml",
        "--agent", "my-agent",
        "--agent-kind", "external"
      ]
    }
  }
}
```

`--project` 是必需的。`--agent` 和 `--agent-kind` 只用于按智能体分别统计调用：没有它们，调用会以 `unknown` 记入日志。

### 设置面板

```bash
python tools/ragraf-admin.py --no-browser
```

在 `127.0.0.1:8777` 打开，只监听这个地址。显示语料及其索引状态、存储大小、索引质量、调用统计。修改类请求需要访问令牌，令牌在启动时打印。

---

## 环境要求与限制

| 项目 | 取值 |
|---|---|
| Python | **3.13+**（`requires-python` 已声明；在 3.14 上验证过） |
| 平台 | Windows + PowerShell 7.6——已验证；其他操作系统**未验证** |
| 权限 | 不需要：一切都从用户目录运行 |
| 磁盘 | 语料索引 `.ragraf/`——大约每 1–2 个源文件 1 MB；外部 Java/Joern 层约 5 GB |
| 网络 | 需要两次：安装时（PyPI）和**首次带向量的索引**时——约 240 MB 的嵌入模型会下载到 `~/.ragraf/models` |
| Java + Joern | 可选；没有它们 `capability=degraded`，`slice` 不可用 |
| 显卡 | 可选；只加速向量编码，按回本阈值启用 |

**关于网络与证书。** 在带 TLS 检查的网络里（企业代理），模型下载会在证书校验处失败。办法是安装 `truststore`：

```bash
pip install truststore      # или: install.ps1 -WithTruststore
```

在家庭网络里不需要这个包。实测：没有它——拒绝为 `certificate verify failed`；有它——240 MB 用 9.2 秒下载完。

**关于模型缓存。** 缓存目录由 `RAGRAF_MODEL_CACHE` 变量覆盖。读操作从不下载模型——只有带向量的索引才会。

---

## 文档

| 文档 | 里面有什么 |
|---|---|
| [ragraf.ru](https://ragraf.ru) | 公开站点：功能、实测基准、管理面板模拟器 |
| [Wiki](https://github.com/paulmann/RAGRAF/wiki) | 15 个页面：入门、操作参考、CLI、MCP、CPG 层、诊断、FAQ |
| [文档（Pages）](https://paulmann.github.io/RAGRAF/) | 这套带搜索的文档——由 `pages.yml` 流水线构建并发布 |
| [`docs/ru/02_architecture/ARCHITECTURE.md`](docs/ru/02_architecture/ARCHITECTURE.md) | 原则、组件、存储示意图、操作契约 |
| [`docs/ru/02_architecture/TZ.md`](docs/ru/02_architecture/TZ.md) | 功能与非功能需求、验收标准 |
| [`docs/ru/03_implementation/INTEGRATION.md`](docs/ru/03_implementation/INTEGRATION.md) | 接入智能体：MCP、工作规则、规程 |
| [`docs/ru/03_implementation/CPG_INTEGRATION.md`](docs/ru/03_implementation/CPG_INTEGRATION.md) | 代码属性图：Joern、JDK、降级模式 |
| [`docs/ru/skills/ragraf-core/SKILL.md`](docs/ru/skills/ragraf-core/SKILL.md) | 使用索引的技能：选操作、恢复 |
| [`docs/ru/05_article/RAGRAF_ARTICLE.md`](docs/ru/05_article/RAGRAF_ARTICLE.md) | 综述文章：技术栈、架构、实测 |
| [`docs/DEFECTS.md`](docs/DEFECTS.md) | 已发现缺陷的登记册，每项发现都带 `file:line` 证据 |
| [`ROADMAP.md`](ROADMAP.md) | 计划中的改进，带稳定标识符（`R-*`） |

---

## 开发

```bash
# тесты запускаются отдельными скриптами — единого раннера нет
python tests/test_lang_routing.py
python tests/test_watch.py

# чекер конвенций проекта (14 проверок)
pwsh -NoLogo -NoProfile -File tools/Invoke-ProjectDoctrine.ps1

# актуализация индекса самого RAGRAF
python tools/ragraf.py ensure .
```

交付里只包含那些在**合成夹具**上运行、并且可移植的检查。其余的测量作者真实语料（PHP 门户、C# 服务、它们项目的配置）上的行为，在别的代码上没有意义。

安装后立刻就能通过的检查：

| 测试 | 检查项 |
|---|---|
| `test_lang_routing.py` | 56 |
| `test_watch.py` | 31 |
| `test_script_extract.py` | 25 |
| `test_threshold.py` | 23 |
| `test_ensure.py` | 17 |
| `test_csharp_records.py` | 12 |

有三项检查需要基础安装里没有的东西，这**不是交付的缺陷**，而是检查本身的性质：

* `test_joern_server.py`——需要外部 Java + Joern 层（`scripts/install-joern.ps1`）；
* `test_graph_algo.py`——需要 `torch`（约 2.5 GB），而它既不在 `pyproject.toml` 里，
  也不在 `requirements.txt` 里：显卡上的图算法是一条可选分支，
  没有 `torch` 时它会如实降级到处理器；
* `test_cpg_call_names.py`——需要语料配置（`config/` 目录不发布）；
  把 `examples/ragraf.example.yaml` 复制到 `config/` 并
  在其中指定自己的根目录，测试就能运行；没有配置时它拒绝启动，而不是静默「通过」。

第十个文件 `test_capability_consistency.py` 在没有语料配置时回答「不适用」：
它检查声明的 `capability` 是否与图的内容一致。`test_graph_algo.py`
在没有 `torch` 时仍然跑完 **7 项检查**，并列出**17 个它无法验证的属性**，
而不是在未验证的代码上方给出绿行。

项目的检查器对这棵发布子树同样诚实：**14 项检查**里这里通过
11 项，另有 3 项报告的文件是公有树里有意不含的（语料配置、
`tests/test_dedupe.py`、`tests/test_text_from_index.py`）——这是
[`ROADMAP.md`](ROADMAP.md) 中的 `R-REL-1` 条目。

`selftest` —— **不是闸门**：它打印 11 个组件的报告，并且始终
返回代码 0。它根本没有设计非零返回码，所以「selftest 是绿的」本身什么也证明不了。

---

## 参与

有用的报告：可复现的缺陷，带命令和输出；文档与代码不一致；产品没有命名的降级情形。欢迎的只有一件事：带文件名和行号的事实。见 [CONTRIBUTING.md](CONTRIBUTING.md)；问题、想法和在自己语料上的实测——到[讨论区](https://github.com/paulmann/RAGRAF/discussions)。参与受[行为准则](CODE_OF_CONDUCT.md)约束。

项目是为具体任务开发的，所以架构建议比没有事先沟通的大补丁更容易被讨论。安全问题请私下报告，不要开公开 issue——见 [SECURITY.md](SECURITY.md)。

如果它为你省下了一个上下文窗口——点星能帮下一个智能体找到这个项目。

---

## 许可证

**[Business Source License 1.1](LICENSE)**（BUSL-1.1，Community）。条款见 [`LICENSE`](LICENSE) 文件；机器可读表达式是 `pyproject.toml` 中的 `license = "BUSL-1.1"`。许可证变更史（MIT → PolyForm Noncommercial → BUSL-1.1）记录在 [CHANGELOG.md](CHANGELOG.md) 中。

> GitHub 上的许可证徽章不可信：它并不能识别所有表达式，BUSL-1.1 可能显示为「Other」。真值来源是 `LICENSE` 文件和 `pyproject.toml` 中的 `license` 字段。

外部依赖保留各自的许可证：`tree-sitter`（MIT）、`sqlite-vec`（MIT/Apache-2.0）、`fastembed`（Apache-2.0）、`PyYAML`（MIT）、`tiktoken`（MIT）、`ijson`（BSD）。单独安装的 Java 和 Joern 以 Apache-2.0 分发，不包含在交付里。
