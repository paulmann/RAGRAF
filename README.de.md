# RAGRAF

<!-- md-i18n:switcher -->
**In anderen Sprachen:** [English](README.md) · Deutsch · [中文](README.zh.md) · [हिन्दी](README.hi.md) · [Español](README.es.md) · [Русский](README.ru.md)

**Graph- und vektorbasierte Wissensdatenbank über Quellcode für LLM-Agenten.**

[**Webseite**](https://ragraf.ru) · [**Wiki**](https://github.com/paulmann/RAGRAF/wiki) · [**Dokumentation (Pages)**](https://paulmann.github.io/RAGRAF/) · [**Veröffentlichungen**](https://github.com/paulmann/RAGRAF/releases) · [**Diskussionen**](https://github.com/paulmann/RAGRAF/discussions)

![RAGRAF — graph- und vektorbasierte Wissensdatenbank über Quellcode für LLM-Agenten: 40 MCP-Werkzeuge, überprüfbare Fakten mit der Angabe Datei:Zeile, vollständig lokal](assets/social-preview.png)

Ein Assistent, der die Fragen eines Agenten zur Codebasis beantwortet — *wo etwas liegt, wer es aufruft, was bei einer Änderung bricht, wie Ähnliches daneben gelöst ist* — mit überprüfbaren Fakten (`Datei:Zeile`) statt blindem `rg` und dem Lesen ganzer Dateien. Python 3.13+, SQLite (Graph + FTS5 + Vektoren in einer Datei), tree-sitter, lokale Einbettungen ohne torch. **Alles läuft auf der Maschine, auf der der Code liegt**: kein Dienst muss gestartet werden, der Index geht nicht in die Cloud — ein lokaler RAG-Index über Ihr Repository. Die optionale Schicht des Code-Property-Graphen (Java + Joern) eröffnet Schnitte; ohne sie arbeitet das Produkt und sagt das offen.

---

## Inhalt

- [Was es ist und wofür](#was-es-ist-und-wofür)
- [Szenarien](#szenarien)
- [Funktionen](#funktionen)
- [Das Projekt in Zahlen](#das-projekt-in-zahlen)
- [Wie es aufgebaut ist](#wie-es-aufgebaut-ist)
- [Installation](#installation)
- [Erste Schritte](#erste-schritte)
- [Anforderungen und Grenzen](#anforderungen-und-grenzen)
- [Dokumentation](#dokumentation)
- [Entwicklung](#entwicklung)
- [Mitwirkung](#mitwirkung)
- [Lizenz](#lizenz)

---

## Was es ist und wofür

Ein Agent, der an einer Weiterentwicklung von Code arbeitet, verbraucht den größten Teil seines Kontexts für die **Suche nach der Stelle**: wo eine Funktion definiert ist, wer sie aufruft, wie Ähnliches daneben gelöst ist. Der gewohnte Weg sind `rg` plus das Lesen ganzer Dateien.

RAGRAF ersetzt diesen Weg durch einen Index. Der Agent stellt seine Frage mit einer festen Operation und erhält einen Fakt mit der Angabe `Datei:Zeile`, nicht zehn Kandidaten zum Durchsehen.

Gemessen an einem realen Projekt (PHP-Portal, 14 Navigationsaufgaben; der Lauf erfolgte auf der Maschine des Autors — siehe „was genau in den Artefakten liegt" unten):

| Kennzahl | Ohne Index | Mit Index |
|---|---|---|
| Werkzeugaufrufe für 14 Aufgaben | 28 (realistisch 43) | **14** |
| Durchschnitt pro Aufgabe | 2,0 / 3,1 | **1,0** |
| Kandidaten zum Durchsehen | bis zu 34 Dateien | 1–5 treffsichere |
| Vollständig gelesene Dateien | 14 (Minimum) | **0** |
| Treffer in der Zieldatei beim ersten Abruf | — | **92 %** (12 von 13) |
| Überprüfbarkeit (Fakten mit der Angabe `Datei:Zeile`) | — | **100 %** |

Die Zahlen zu Aufrufen, Treffern und Überprüfbarkeit sind **Messungen** jenes Laufs; seine Rohartefakte gehören nicht zum veröffentlichten Baum (Punkt `R-REL-1` in [`ROADMAP.md`](ROADMAP.md)). Dafür enthält die Auslieferung ein Artefakt der Graphmetrik: [`bench/results/M2_rev-0012.json`](bench/results/M2_rev-0012.json) und [`M2_rev-0025.json`](bench/results/M2_rev-0025.json) messen die **Vollständigkeit der Impact-Analyse** am selben Portal — ob der Graph bis zu den Dateien reicht, die tatsächlich gemeinsam geändert wurden. Durchschnittlicher Recall **0,771 → 0,917** bei 20 und 8 Commits und Tiefe 3, medianer Recall **1,0**, Precision 0,072 → 0,020 (niedrig mit Absicht: die Metrik misst Zusammenhang, nicht Vorhersage) bei einer naiven Baseline von **0,0**.

„Realistisch 43" ist eine Modellkorrektur (der Agent sieht bis zu 5 Kandidaten statt einem durch), und sie ist im Artefakt selbst festgehalten.

Ein wichtiger Vorbehalt, der meist weggelassen wird: **der Gewinn um den Faktor 2 bis 3 gilt für Lokalisierungsaufgaben** („wo definiert", „wer ruft auf"). Im Zyklus „gelesen → geändert → geprüft" gibt es bei der Zahl der Aufrufe kaum eine Ersparnis — dort liegt der Gewinn woanders: Änderungen beschädigen den Index nicht, und zweifelhaftes SQL wird vor dem Lauf geprüft.

---

## Szenarien

| Was Sie tun | Was Sie RAGRAF fragen | Was Sie als Antwort erhalten |
|---|---|---|
| Sie führen einen Agenten in ein unbekanntes Repository mit 200 000 Zeilen ein | `repo_map`, danach `search` / `symbol` | einen Fakt pro Frage, mit der Angabe `Datei:Zeile`, statt einer Wand von Kandidaten |
| Sie refaktorieren eine Funktion, die von einem Dutzend Stellen aufgerufen wird | `callers`, `impact`, `slice` bei verfügbarem CPG | belegte Beziehungen getrennt von Heuristiken; einen Schnitt, der sagt, was er genau abdeckt |
| Sie benennen eine Datenbankspalte um oder löschen sie | `write_check` **vor** dem SQL-Lauf, danach `table_usage` | fehlende Spalten mit den nächstliegenden Namen; wer die Tabelle liest und wer schreibt |
| Sie arbeiten einen fremden Diff durch | `diff`, `drift_report`, `live_read` | geänderte Symbole und Kanten einschließlich Regressionen sowie die aktuelle Datei mit Kennzeichnung der Abweichungen |
| Sie suchen Kopien (`Files.php` … `Files7.php`) | `duplicates` | welche Dateien Kopien sind und welche Kopie **belegt** die aktive ist |
| „Wie ist das an anderen Stellen im Code gelöst?" | `search` (BM25 + Vektoren, hybrid) | rangierte Ausschnitte des Korpus; für die Antworten selbst ist kein Einbettungsmodell nötig |
| Sie binden den Index an einen Agenten an | **40 MCP-Werkzeuge** `ragraf_*` oder die CLI | dieselben Operationen über stdio — siehe [`docs/ru/03_implementation/INTEGRATION.md`](docs/ru/03_implementation/INTEGRATION.md) |

---

## Funktionen

**38 Operationen** auf dem Repository — ein fester Satz, ohne Abfragesprache für den Graphen:

| Operation | Beantwortet die Frage |
|---|---|
| `search` | wo darüber geschrieben ist (hybrid: Lexik BM25 + Vektor) |
| `search_multi` | wo mehrere Zeichenketten zugleich vorkommen; Kopien von Dateien und welche davon aktiv ist |
| `duplicates` | welche Dateien Kopien voneinander sind und wodurch die Aktivität der Hauptkopie belegt ist |
| `entrypoints` | Einstiegspunkte: Routen, HTTP-Handler, CLI, Endpunkte im Markup |
| `dead_code` | Dateien und Unterprogramme ohne Verweise und außerhalb der Erreichbarkeit |
| `symbol` | wo ein Symbol definiert ist: Art, Datei, Zeilen, Signatur |
| `references` | wo es sonst verwendet wird |
| `callers` / `callees` | wer aufruft / wen es aufruft |
| `impact` | was eine Änderung berührt (belegte und heuristische Beziehungen getrennt) |
| `slice` | struktureller Schnitt: Knoten, Kanten, Zeilen — ohne Funktionsrümpfe |
| `code` | die Rümpfe von Symbolen (nur auf ausdrückliche Anfrage) |
| `repo_map` | die Karte des Repositorys im vorgegebenen Zeichenbudget |
| `schema` / `table_usage` | was in einer Datenbanktabelle steht und wer sie liest oder schreibt |
| `routes` | Routen und Einstiegspunkte |
| `policy_trace` | wer und unter welcher Bedingung ein Recht erhält |
| `class_usage` | Formatierungsklasse: wo definiert, wo verwendet, was abweicht |
| `write_check` | ob so geschrieben werden darf (Abgleich des Schreibplans mit dem Schema vor dem Lauf) |
| `live_read` | die aktuelle Datei mit Hervorhebung der Unterschiede zum Index |
| `freshness` | ob der Index aktuell ist (Abgleich der Prüfsumme) |
| `index_status` | Revision, Abdeckung, Degradierung, Größe des Journals |
| `drift_report` / `drift_ack` | das Journal der Abweichungen zwischen Index und Code |
| `catalog` | das Register der Korpora: was indiziert ist und mit welchen Summen |
| `calibrate` | die Kalibrierung der Schwellen |
| `usage` | die Telemetrie der Zugriffe und die Schätzung des Effekts |
| `diff` | was sich zwischen Revisionen geändert hat: Dateien, Symbole, Kanten, Regressionen (P2-1) |
| `task_stats` | den Bericht zu einer Aufgabe: Zugriffe auf den Index, Entscheidungen der Freigabeprüfung (§3.4) |
| `gate` | die Entscheidung der Freigabeprüfung zu einem Werkzeugaufruf: `allow` / `ask` / `deny` (§3.2) |
| `note` / `notes` | eine Notiz zu einer Entscheidung schreiben und lesen — Wissen zwischen Sitzungen (P2-3) |

Zwei weitere Modi sind keine Operationen, sondern eine Form der Antwort: **`answer=true`** liefert eine formulierte Antwort mit Verweisen statt eines strukturellen Ergebnisses (P2-2), und `brief=true` eine knappe Hülle der Antwort. `usage` zeigt die Kennzahlen des Effekts (`saved_calls`, `hit_rate`, `avg_ms`, `misses`, `stale_served`).

Dieselben Operationen sind als **40 MCP-Werkzeuge** (`ragraf_*`) verfügbar — zum Anbinden an einen Agenten als Server — und über die CLI. Eine eigene Gruppe von Werkzeugen ist die lebende Schicht der Datenbanken: `ragraf_db_schema`, `ragraf_db_table`, `ragraf_db_relations`, `ragraf_db_sample`, `ragraf_db_drift`, `ragraf_db_cross`.
Das vierundzwanzigste ist `use_project`: die Wahl des Korpus, wenn ein Server mehrere Projekte bedient. `context` antwortet, welcher Korpus gewählt ist und auf welcher Grundlage: ohne es waren Antworten über ein fremdes Projekt nicht von Antworten über das eigene zu unterscheiden.

**Sechzehn Sprachen** werden über einen Syntaxbaum zerlegt (Knoten und Kanten des Graphen): PHP, Python, JavaScript, TypeScript, Java, C#, C, C++, Ruby, Lua, Go, Perl, bash, PowerShell, CSS, HTML — dreizehn allgemeine Profile plus eigene Extraktoren für PHP, CSS und HTML. Elf weitere werden als Text angebunden — die Dateien sind über die Suche auffindbar, geben aber keine Knoten: Markdown, SQL, YAML, JSON, TOML, INI, XML, RST, Rust, batch, reiner Text. Insgesamt sind 55 Erweiterungen zugeordnet.

---

## Das Projekt in Zahlen

Alle Zahlen unten sind am veröffentlichten Baum am **16.10.2026** gemessen — nicht aus früheren Texten abgeschrieben. Zwei der wichtigsten bewacht `Test-DocumentedNumbers` in [`tools/Invoke-ProjectDoctrine.ps1`](tools/Invoke-ProjectDoctrine.ps1): er gleicht die Dokumente mit dem Code ab.

| Oberfläche | Anzahl | Woher gemessen |
|---|---|---|
| Operationen der Fassade | **38** | `def op_*` in `src/ragraf/facade.py` |
| MCP-Werkzeuge | **40** — davon 6 für die lebende Schicht der Datenbanken | die Namen `ragraf_*` in `src/ragraf/mcp_server.py` |
| MCP-Ressourcen / Prompts | 3 + 1 URI-Vorlage / 2 | `ragraf://status`, `ragraf://catalog`, `ragraf://corpus/{name}/map`; die Prompts `ragraf-check`, `ragraf-repair` |
| CLI-Unterbefehle / `query`-Operationen | 14 / **39** | `add_parser(…)` und `OPS` in `src/ragraf/cli.py` |
| Sprachen, über den Syntaxbaum zerlegt | **16** | `TREE_LANGS` in `src/ragraf/config.py` |
| Sprachen, als Text angebunden | **11** | die übrigen Werte von `LANG_BY_EXT` |
| Zugeordnete Erweiterungen | 55 | `LANG_BY_EXT` |
| API-Routen der Verwaltungsoberfläche | 32 | `src/ragraf/admin_server.py` |
| Laufzeitabhängigkeiten | 7 (+1 optionaler Satz `tls`) | `pyproject.toml` |
| Python in `src/` | 50 Dateien · 37 592 Zeilen | veröffentlichter Baum |
| PowerShell — Installationsprogramm, Hüllen, Prüfprogramm | 5 Dateien · 3 423 Zeilen | veröffentlichter Baum |
| Markdown | 28 Dateien (20 in `docs/`, davon 9 russisch) + 15 Wiki-Seiten | veröffentlichter Baum und Wiki |
| Übertragbare Testsätze | 6 Sätze · **164 Prüfungen, 0 Fehlschläge** | jeder Satz einzeln gestartet |
| Register der Defekte | 61 Funde | [`docs/DEFECTS.md`](docs/DEFECTS.md); in der Zusammenfassung des Audits nach Wichtigkeit sind 43 davon berücksichtigt |
| Veröffentlichter Baum | 136 Dateien · 3,5 MB | veröffentlichter Baum |

Zum Maßstab: RAGRAF indiziert den eigenen Arbeitsbaum (648 Dateien, 140 741 Zeilen — mehr als diese veröffentlichte Teilmenge) in 3 620 Symbole, 46 296 Kanten und 3 417 Abschnitte in **74 s**, davon 63 s für die optionale Stufe Joern/CPG (`rev-0156`, Messung vom 16.10.2026; `capability=full`, 0 Dateien mit Fehler). Die ausführliche Analyse steht im [Wiki → Benchmarks and Limits](https://github.com/paulmann/RAGRAF/wiki/11-Benchmarks-and-Limits).

**Der gemessene Prüfstand, entpersonalisiert.** Im Register stehen **28 Korpora, 25 davon indiziert** — 10 mit CPG-Schicht, 15 im Modus `degraded`. Bei 16 von ihnen wurde der Revisionseintrag am 16.10.2026 gelesen: **7 527 Dateien, 94 528 Symbole, 791 871 Kanten, 86 790 Abschnitte**. Projektnamen, Pfade und Domänen sind mit Absicht verborgen: die Kennungen sind nach Größe geordnet, und nur die private Karte des Autors löst sie auf.

| Korpus | Typ | Dateien | Symbole | Kanten | Modus |
|---|---|---|---|---|---|
| C-02 | Webanwendung in PHP | 2 722 | 32 761 | 355 192 | degraded |
| C-04 | Dienst / Desktop in C# | 1 274 | 20 331 | 98 605 | degraded |
| C-05 | Webanwendung in PHP | 928 | 17 381 | 99 029 | full |
| C-18 | Automatisierungssatz in PowerShell | 81 | 324 | 4 214 | degraded |
| C-24 | Markdown + bash (der kleinste) | 18 | 184 | 1 515 | degraded |

Die vollständige Tabelle mit 16 Zeilen, die Regeln der Entpersonalisierung und ein Vorbehalt zur Zählung stehen im [Wiki → Benchmarks and Limits](https://github.com/paulmann/RAGRAF/wiki/11-Benchmarks-and-Limits). Der Vorbehalt ist beim Zitieren von Zahlen wichtig: das Korpusregister und der Revisionseintrag ergeben für dieselbe Revision **unterschiedliche** Summen (an einem Korpus gemessen: 8 721 gegen 4 498 Symbole, 41 201 gegen 56 106 Kanten), deshalb sind alle Zahlen oben aus dem **Revisionseintrag** genommen — dem, den die Antworthülle liefert.

---

## Wie es aufgebaut ist

**Drei SQLite-Speicher** pro Korpus, im Verzeichnis `.ragraf/` **innerhalb des indizierten Projekts**:

| Speicher | Inhalt |
|---|---|
| `graph.sqlite` | Knoten (Klasse, Funktion, Methode), Kanten (Aufrufe, Vererbung, Importe), Datenbankschema |
| `lexvec.sqlite` | Lexik FTS5 (BM25) und Vektoren in einer Datei; der Rohtext der Abschnitte für die Ausgabe von Ausschnitten |
| `control.sqlite` | Revisionen, Journal der Abweichungen, Telemetrie der Zugriffe |

**Ablauf der Indizierung:** `scan` (Durchlauf und Prüfsummen) → `ast` (tree-sitter: Symbole, Aufrufe, SQL) → `load` → `resolve` (Namen) → `schema` (Tabellen aus Migrationen) → `cpg` (prozedurübergreifende Aufrufe über Joern) → `lexvec` (FTS5 + Vektoren).

**Drei Entscheidungen, die das Verhalten bestimmen** — jede ist aus einem gemessenen Defekt abgeleitet:

1. **Ein unveränderlicher Index und ein Journal der Unterschiede statt einer „Fortschreibung im Betrieb".** Innerhalb einer Revision ändert sich der Index nicht; Codeänderungen sammeln sich im Journal, und berührte Fakten werden als `stale` markiert. Sonst würden Fakten aus verschiedenen Zeitpunkten in einer Antwort ununterscheidbar vermischt — genau so entstehen falsche Schlüsse.
2. **Der Agent bekommt keine Abfragesprache für den Graphen.** Nur feste Operationen. Grundlage: in LLMxCPG (USENIX Security '25) liefern die Basismodelle 132/1278 und 19/1278 gültige CPGQL-Abfragen gegenüber 1278/1278 beim trainierten — eine freie Sprache wird also zum Fehlergenerator.
3. **Jeder Fakt trägt einen Beleg.** `path:line`, `provenance` (`stated` — belegt, `derived` — Heuristik), `index_rev`, `confidence`. Eine Kante ohne Beleg wirkt nur deshalb glaubwürdig, weil sie „aus der Datenbank" kommt.

**Degradierung statt Absturz.** Fehlt der Code-Property-Graph, ergibt sich `capability=degraded` und eine verständliche Ablehnung der Operation `slice` unter Angabe des tatsächlichen Modus; die übrigen Operationen arbeiten. Ohne Einbettungsmodell arbeitet die lexikalische Suche. Ohne freien Port nennt der Server den Grund. Eine stille Degradierung gibt es nicht.

---

## Installation

Erforderlich ist **Python 3.13 oder neuer**. Administratorrechte sind nicht nötig.

### Schnellinstallation mit einem Befehl

Das Installationsprogramm lädt das Paket von GitHub und entpackt es im Dialog:

```powershell
iwr -OutFile install.ps1 https://raw.githubusercontent.com/paulmann/RAGRAF/main/install.ps1
pwsh -NoLogo -NoProfile -File .\install.ps1
```

Es fragt nach dem Installationsverzeichnis (voreingestellt `C:\RAGRAF`), legt eine virtuelle Umgebung an und installiert die Abhängigkeiten über `scripts/install.ps1`, bietet an, AutoClaw und DeepSeek Harness zu finden und die Anbindung einzurichten, und gibt am Ende die Anleitung zum Start der Verwaltungsoberfläche samt einem Hinweis zu deren Zugang aus.

Worüber es nicht hinausgeht, sagt das Installationsprogramm offen: **Werte für AutoClaw zeigt es an, schreibt sie aber nicht** — AutoClaw nimmt MCP-Server nur über seine eigene Oberfläche an, und sein Speicher gehört dem Produkt. Für den **Harness** kann das Installationsprogramm einen Profilblock schreiben: mit Sicherungskopie und anschließender Prüfung der Auswertung. Eine gefundene Installation wird nicht still überschrieben, und eine Probe ohne Abhängigkeiten ist über den Schalter `-SkipDeps` möglich.

### Windows (PowerShell 7.6+)

```powershell
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
pwsh -NoLogo -NoProfile -File scripts/install.ps1
```

Das Skript legt eine virtuelle Umgebung in `~/.ragraf/venv` an, installiert die Abhängigkeiten und **prüft, dass eine arbeitsfähige Umgebung entstanden ist** — Schritt für Schritt, mit einer abschließenden Tabelle. Es „tut nicht so, als hätte es installiert": jeder Schritt endet mit einer Prüfung.

Nützliche Varianten:

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

### Andere Plattformen

Ein gemeinsamer Weg, funktioniert überall, wo Python 3.13+ vorhanden ist:

```bash
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -e .                # или: pip install -r requirements.txt
ragraf --version
```

Auch ohne Installation des Pakets arbeitet das Produkt — über eine Hülle, die `src/` selbst in den Pfad aufnimmt:

```bash
python tools/ragraf.py --version
```

> `scripts/install.ps1` ist unter Windows geprüft (PowerShell 7.6, Python 3.14.7). Der gemeinsame Weg über `pip` unter Linux und macOS wurde **nicht geprüft** — das Produkt wurde unter Windows entwickelt und gemessen.

### Externe Schicht: Java und Joern (optional)

Für den prozedurübergreifenden Graphen (Operation `slice`) sind JDK 21 und Joern nötig. Ohne sie arbeitet das Produkt: der Index wird gebaut, Suche und Struktur sind verfügbar, `slice` lehnt mit einer Begründung ab.

```powershell
pwsh -File scripts/install-joern.ps1
```

Das Archiv landet in `.tools/downloads/` — das Skript ist idempotent. Das entpackte Paket belegt etwa 5 GB.

---

## Erste Schritte

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

Die vollständige Befehlsliste steht in `ragraf --help`. Wichtig: **die globalen Schalter `--project/-p` und `--json` stehen VOR dem Unterbefehl** (`ragraf -p cfg query search ...`), die eigenen Schalter des Unterbefehls dagegen danach.

### Verwaltung des Korpus

```bash
ragraf init C:\work\myproject         # создать конфигурацию корпуса и показать путь к ней
ragraf -p <конфиг> register           # зарегистрировать разработку в реестре
ragraf -p <конфиг> index              # полная индексация
ragraf -p <конфиг> reindex            # переиндексация + архивация журнала расхождений
ragraf -p <конфиг> drift --scan       # зарегистрировать различия, НЕ меняя индекс
ragraf -p <конфиг> status             # состояние: ревизия, покрытие, размер журнала
ragraf -p <конфиг> usage              # телеметрия: обращения, факты, оценка эффекта
```

### Anbindung an einen Agenten als MCP-Server

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

`--project` ist Pflicht. `--agent` und `--agent-kind` sind nur für die getrennte Statistik der Zugriffe nötig: ohne sie landen die Aufrufe als `unknown` im Journal.

### Einstellungsoberfläche

```bash
python tools/ragraf-admin.py --no-browser
```

Sie öffnet auf `127.0.0.1:8777` und lauscht nur auf dieser Adresse. Sie zeigt die Korpora und den Zustand ihrer Indizes, die Größe der Speicher, die Qualität des Index und die Statistik der Zugriffe. Ändernde Anfragen verlangen ein Zugangstoken, das beim Start ausgegeben wird.

---

## Anforderungen und Grenzen

| Punkt | Wert |
|---|---|
| Python | **3.13+** (als `requires-python` angegeben; auf 3.14 geprüft) |
| Plattform | Windows + PowerShell 7.6 — geprüft; andere Betriebssysteme — **nicht geprüft** |
| Rechte | nicht erforderlich: alles arbeitet aus dem Benutzerverzeichnis |
| Speicherplatz | der Korpusindex `.ragraf/` — etwa 1 MB je 1–2 Quelldateien; die externe Schicht Java/Joern — ~5 GB |
| Internet | zweimal nötig: bei der Installation (PyPI) und bei der **ersten Indizierung mit Vektoren** — das Einbettungsmodell von ~240 MB wird nach `~/.ragraf/models` geladen |
| Java + Joern | optional; ohne sie `capability=degraded` und `slice` nicht verfügbar |
| Grafikkarte | optional; beschleunigt nur die Kodierung der Vektoren und wird erst ab der Schwelle der Rentabilität eingeschaltet |

**Zu Internet und Zertifikaten.** In einem Netz mit TLS-Inspektion (Unternehmensproxy) scheitert das Laden des Modells an der Zertifikatsprüfung. Abhilfe schafft die Installation von `truststore`:

```bash
pip install truststore      # или: install.ps1 -WithTruststore
```

In einem Heimnetz ist das Paket nicht nötig. Gemessen: ohne es die Ablehnung `certificate verify failed`, mit ihm werden 240 MB in 9,2 s geladen.

**Zum Zwischenspeicher des Modells.** Das Verzeichnis des Zwischenspeichers wird durch die Variable `RAGRAF_MODEL_CACHE` überschrieben. Leseoperationen laden das Modell nie — nur die Indizierung mit Vektoren.

---

## Dokumentation

| Dokument | Inhalt |
|---|---|
| [ragraf.ru](https://ragraf.ru) | die öffentliche Webseite: Funktionen, gemessene Benchmarks, ein Nachbau der Verwaltungsoberfläche |
| [Wiki](https://github.com/paulmann/RAGRAF/wiki) | 15 Seiten: Einstieg, Nachschlagewerk der Operationen, CLI, MCP, CPG-Schicht, Diagnose, FAQ |
| [Dokumentation (Pages)](https://paulmann.github.io/RAGRAF/) | dieser Dokumentationssatz mit Suche — wird von der Pipeline `pages.yml` gebaut und veröffentlicht |
| [`docs/ru/02_architecture/ARCHITECTURE.md`](docs/ru/02_architecture/ARCHITECTURE.md) | Prinzipien, Komponenten, Schema der Speicher, Vertrag der Operationen |
| [`docs/ru/02_architecture/TZ.md`](docs/ru/02_architecture/TZ.md) | funktionale und nichtfunktionale Anforderungen, Abnahmekriterien |
| [`docs/ru/03_implementation/INTEGRATION.md`](docs/ru/03_implementation/INTEGRATION.md) | Anbindung an einen Agenten: MCP, Arbeitsregeln, Verfahren |
| [`docs/ru/03_implementation/CPG_INTEGRATION.md`](docs/ru/03_implementation/CPG_INTEGRATION.md) | Code-Property-Graph: Joern, JDK, Modi der Degradierung |
| [`docs/ru/skills/ragraf-core/SKILL.md`](docs/ru/skills/ragraf-core/SKILL.md) | die Fertigkeit für die Arbeit mit dem Index: Wahl der Operation, Wiederherstellung |
| [`docs/ru/05_article/RAGRAF_ARTICLE.md`](docs/ru/05_article/RAGRAF_ARTICLE.md) | Überblicksartikel: Technikstapel, Architektur, Messungen |
| [`docs/DEFECTS.md`](docs/DEFECTS.md) | das Register der gefundenen Defekte, zu jedem Fund ein Beleg `Datei:Zeile` |
| [`ROADMAP.md`](ROADMAP.md) | geplante Verbesserungen mit stabilen Kennungen (`R-*`) |

---

## Entwicklung

```bash
# тесты запускаются отдельными скриптами — единого раннера нет
python tests/test_lang_routing.py
python tests/test_watch.py

# чекер конвенций проекта (14 проверок)
pwsh -NoLogo -NoProfile -File tools/Invoke-ProjectDoctrine.ps1

# актуализация индекса самого RAGRAF
python tools/ragraf.py ensure .
```

In der Auslieferung sind nur die Prüfungen, die auf **synthetischen Fixturen** arbeiten und übertragbar sind. Die übrigen messen das Verhalten an den realen Korpora des Autors (PHP-Portal, C#-Dienst, die Konfigurationen dieser Projekte) und haben an anderem Code keinen Sinn.

Was direkt nach der Installation durchläuft:

| Test | Prüfungen |
|---|---|
| `test_lang_routing.py` | 56 |
| `test_watch.py` | 31 |
| `test_script_extract.py` | 25 |
| `test_threshold.py` | 23 |
| `test_ensure.py` | 17 |
| `test_csharp_records.py` | 12 |

Drei Prüfungen verlangen, was in der Basisinstallation fehlt, und das ist **kein Defekt der Auslieferung**, sondern der Charakter der Prüfung:

* `test_joern_server.py` — verlangt die externe Schicht Java + Joern (`scripts/install-joern.ps1`);
* `test_graph_algo.py` — verlangt `torch` (etwa 2,5 GB), das weder in `pyproject.toml` noch in `requirements.txt` steht: Graphalgorithmen auf der Grafikkarte sind ein optionaler Zweig, und ohne `torch` degradiert er offen auf den Prozessor;
* `test_cpg_call_names.py` — verlangt eine Korpuskonfiguration (das Verzeichnis `config/` wird nicht veröffentlicht); der Test lässt sich starten, indem `examples/ragraf.example.yaml` nach `config/` kopiert und dort die eigene Wurzel eingetragen wird; ohne Konfiguration weigert er sich zu starten, statt still zu „bestehen".

Die zehnte Datei, `test_capability_consistency.py`, antwortet „nicht anwendbar", solange keine Korpuskonfiguration vorliegt: sie prüft, ob das angegebene `capability` zum Inhalt des Graphen passt. `test_graph_algo.py` führt ohne `torch` trotzdem **7 Prüfungen** aus und nennt **17 Eigenschaften, die er nicht prüfen konnte**, statt einer grünen Zeile über ungeprüftem Code.

Das Prüfprogramm des Projekts ist ebenso offen über diese veröffentlichte Teilmenge: von **14 Prüfungen** laufen hier 11 durch, und 3 melden Dateien, die im öffentlichen Baum mit Absicht fehlen (die Korpuskonfiguration, `tests/test_dedupe.py`, `tests/test_text_from_index.py`) — das ist Punkt `R-REL-1` in [`ROADMAP.md`](ROADMAP.md).

`selftest` ist **keine Freigabeprüfung**: er gibt einen Bericht über 11 Komponenten aus und liefert immer den Code 0. Ein anderer Code ist für ihn nicht vorgesehen, deshalb beweist ein „grüner selftest" für sich genommen nichts.

---

## Mitwirkung

Willkommen sind: ein reproduzierbarer Defekt mit Befehl und Ausgabe; eine Abweichung zwischen Dokument und Code; ein Fall von Degradierung, den das Produkt nicht genannt hat. Erwünscht ist eines: ein Fakt mit Angabe von Datei und Zeile. Siehe [CONTRIBUTING.md](CONTRIBUTING.md); Fragen, Ideen und Messungen am eigenen Korpus gehören in die [Diskussionen](https://github.com/paulmann/RAGRAF/discussions). Die Mitwirkung regelt der [Verhaltenskodex](CODE_OF_CONDUCT.md).

Das Projekt wird für konkrete Aufgaben entwickelt, deshalb werden Vorschläge zur Architektur lieber besprochen als große Patches ohne vorheriges Gespräch. Sicherheitsprobleme werden vertraulich gemeldet, nicht als öffentliches Issue — siehe [SECURITY.md](SECURITY.md).

Wenn Ihnen das ein Kontextfenster erspart hat — ein Stern hilft dem nächsten Agenten, das Projekt zu finden.

---

## Lizenz

**[Business Source License 1.1](LICENSE)** (BUSL-1.1, Community). Die Bedingungen stehen in der Datei [`LICENSE`](LICENSE); der maschinenlesbare Ausdruck ist `license = "BUSL-1.1"` in `pyproject.toml`. Der Wechsel der Lizenzen (MIT → PolyForm Noncommercial → BUSL-1.1) ist in [CHANGELOG.md](CHANGELOG.md) festgehalten.

> Dem Lizenzabzeichen auf GitHub ist nicht zu trauen: nicht jeder Ausdruck wird erkannt, und BUSL-1.1 kann als „Other" erscheinen. Die Quellen der Wahrheit sind die Datei `LICENSE` und das Feld `license` in `pyproject.toml`.

Externe Abhängigkeiten behalten ihre Lizenzen: `tree-sitter` (MIT), `sqlite-vec` (MIT/Apache-2.0), `fastembed` (Apache-2.0), `PyYAML` (MIT), `tiktoken` (MIT), `ijson` (BSD). Java und Joern, die getrennt installiert werden, stehen unter Apache-2.0 und gehören nicht zur Auslieferung.
