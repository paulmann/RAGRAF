# RAGRAF

<!-- md-i18n:switcher -->
**In anderen Sprachen:** [Русский](README.ru.md) · Deutsch · [English](README.md) · [中文](README.zh.md) · [हिन्दी](README.hi.md) · [Español](README.es.md)

> **Vollständige Übersetzung auf 0.4.0.** Dies ist die deutsche Fassung des Hauptdokuments [`README.md`](README.md): es bleibt der einzige Ort für Änderungen, und bei Abweichungen gilt der englische Text. Stand dieser Fassung: 27.10.2026.

**Graph- und vektorbasierte Wissensdatenbank über Quellcode für LLM-Agenten.**

[![CI](https://github.com/paulmann/RAGRAF/actions/workflows/ci.yml/badge.svg)](https://github.com/paulmann/RAGRAF/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/paulmann/RAGRAF?label=release&sort=semver)](https://github.com/paulmann/RAGRAF/releases)
[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-informational)](pyproject.toml)

![RAGRAF — graph- und vektorbasierte Wissensdatenbank über Quellcode für LLM-Agenten: 61 MCP-Werkzeuge, überprüfbare Fakten mit der Angabe Datei:Zeile, vollständig lokal](assets/social-preview.png)

[**Schnellstart**](#erste-schritte) · [**Dokumentation**](#dokumentation) · [**Wiki**](https://github.com/paulmann/RAGRAF/wiki) · [**Änderungen**](CHANGELOG.md) · [**English version**](README.md)

> **Stand der Dokumentation.** Alle Zahlen unten wurden am 26.–27.10.2026 gegen Version 0.4.0 gemessen; die Größen des RAGRAF-Index werden mit dem aktiven Revisionseintrag durch `Test-DocumentedNumbers` abgeglichen, und eine Zahl aus einer vergangenen Revision lässt die Veröffentlichung scheitern, statt als heutige Messung zu gelten.

Ein Assistent, der die Fragen eines Agenten zur Codebasis beantwortet — *wo etwas liegt, wer es aufruft, was bei einer Änderung bricht, wie Ähnliches daneben gelöst ist* — mit überprüfbaren Fakten (`Datei:Zeile`) statt blindem `rg` und dem Lesen ganzer Dateien. Python 3.13+, SQLite (Graph + FTS5 + Vektoren an einer Stelle), tree-sitter, lokale Einbettungen ohne torch. Die optionale Schicht des Code-Property-Graphen (Java + Joern) eröffnet Schnitte; ohne sie arbeitet das Produkt weiter und sagt das offen.

---

## Inhalt

- [Was es ist und wofür](#was-es-ist-und-wofür)
- [Funktionen](#funktionen)
- [Wie es aufgebaut ist](#wie-es-aufgebaut-ist)
- [Installation](#installation)
- [Erste Schritte](#erste-schritte)
- [Anforderungen und Grenzen](#anforderungen-und-grenzen)
- [Dokumentation](#dokumentation)
- [Entwicklung](#entwicklung)
- [Mitwirkung](#mitwirkung)
- [Das Projekt in Zahlen und der gemessene Prüfstand](#das-projekt-in-zahlen-und-der-gemessene-prüfstand)
- [Lizenz](#lizenz)

---

## Was es ist und wofür

Ein Agent, der an einer Weiterentwicklung von Code arbeitet, verbraucht den größten Teil seines Kontexts für die **Suche nach der Stelle**: wo eine Funktion definiert ist, wer sie aufruft, wie Ähnliches daneben gelöst ist. Der gewohnte Weg sind `rg` plus das Lesen ganzer Dateien.

RAGRAF ersetzt diesen Weg durch einen Index. Der Agent stellt seine Frage mit einer festen Operation und erhält einen Fakt mit der Angabe `Datei:Zeile`, nicht zehn Kandidaten zum Durchsehen.

Gemessen an einem realen Projekt (PHP-Portal, 14 Navigationsaufgaben, `bench/results/`):

| Kennzahl | Ohne Index | Mit Index |
|---|---|---|
| Werkzeugaufrufe für 14 Aufgaben | 28 (realistisch 43) | **14** |
| Durchschnitt pro Aufgabe | 2,0 / 3,1 | **1,0** |
| Kandidaten zum Durchsehen | bis zu 34 Dateien | 1–5 treffsichere |
| Vollständig gelesene Dateien | 14 (Minimum) | **0** |
| Treffer in der Zieldatei beim ersten Abruf | — | **92 %** (12 von 13) |
| Überprüfbarkeit (Fakten mit der Angabe `Datei:Zeile`) | — | **100 %** |

Die Zahlen zu Aufrufen, Treffern und Überprüfbarkeit sind **Messungen** (die Rohartefakte der Läufe liegen in `bench/results/`). „Realistisch 43" ist eine Modellkorrektur (der Agent sieht bis zu 5 Kandidaten statt einem durch), und sie ist im Artefakt selbst festgehalten.

Ein wichtiger Vorbehalt, der meist weggelassen wird: **der Gewinn um den Faktor 2 bis 3 gilt für Lokalisierungsaufgaben** („wo definiert", „wer ruft auf"). Im Zyklus „gelesen → geändert → geprüft" gibt es bei der Zahl der Aufrufe kaum eine Ersparnis — dort liegt der Gewinn woanders: Änderungen beschädigen den Index nicht, und zweifelhaftes SQL wird vor dem Lauf geprüft.

---

## Funktionen

**54 Operationen** auf dem Repository — ein fester Satz, ohne Abfragesprache für den Graphen:

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
| `route` | wohin man für eine Antwort geht: welcher Korpus antwortet und warum (ohne Zitate) |
| `neighbors` | ob es eine fertige Umsetzung in benachbarten Korpora desselben Stacks gibt |
| `calibrate` | die Kalibrierung der Schwellen |
| `usage` | die Telemetrie der Zugriffe und die Schätzung des Effekts |
| `diff` | was sich zwischen Revisionen geändert hat: Dateien, Symbole, Kanten, Regressionen (P2-1) |
| `task_stats` | den Bericht zu einer Aufgabe: Zugriffe auf den Index, Entscheidungen der Freigabeprüfung (§3.4) |
| `gate` | die Entscheidung der Freigabeprüfung zu einem Werkzeugaufruf: `allow` / `ask` / `deny` (§3.2) |
| `note` / `notes` | eine Notiz zu einer Entscheidung schreiben und lesen — Wissen zwischen Sitzungen (P2-3) |
| `quality_suite` / `quality_diff` / `quality_prune` | die Qualitätsschleife (Stufe pro): Prüfungen des Index fahren, zwei Läufe vergleichen, das Journal der Läufe ausdünnen — mit Kopie und Bestätigung |
| `shared_index_status` / `shared_index_publish` / `shared_index_query` | der gemeinsame Index des Teams (Stufe pro): einen Korpus zum Lesen über das Netz veröffentlichen, einen Client zulassen und den veröffentlichten Index befragen |
| `broker_issue` / `broker_rotate` | der Geheimnis-Broker (Stufe pro): ein Geheimnis auf Anfrage ausgeben — eine Leihe mit Frist, Verwalter und Grund — und den Wert nach Plan wechseln (dabei werden Leihen widerrufen) |

Zwei weitere Modi sind keine Operationen, sondern eine Form der Antwort: **`answer=true`** liefert eine formulierte Antwort mit Verweisen statt eines strukturellen Ergebnisses (P2-2), und `brief=true` eine knappe Hülle der Antwort. `usage` zeigt die Kennzahlen des Effekts (`saved_calls`, `hit_rate`, `avg_ms`, `misses`, `stale_served`).

Dieselben Operationen sind als **61 MCP-Werkzeuge** (`ragraf_*`) verfügbar — zum Anbinden an einen Agenten als Server — und über die CLI. Eine eigene Gruppe von Werkzeugen ist die lebende Schicht der Datenbanken: `ragraf_db_schema`, `ragraf_db_table`, `ragraf_db_relations`, `ragraf_db_sample`, `ragraf_db_drift`, `ragraf_db_cross`. Zwei Operationen des geschlossenen Kreises (Stufe enterprise) sind als Werkzeuge `ragraf_license_offline` (Offline-Lizenz: Antrag, Ausstellung, Abnahme, Übertragung) und `ragraf_attest_bundle` (Attestierung der Lieferung: ein signierter Satz von Belegen und seine Prüfung) deklariert. Die Qualitätsschleife (Stufe pro) — `ragraf_quality_suite` (Satz von Indexprüfungen und Aufzeichnung des Laufs), `ragraf_quality_diff` (Abweichungen zwischen zwei Läufen) und `ragraf_quality_prune` (Ausdünnen des Journals der Läufe: standardmäßig ein Probelauf, gelöscht wird nur mit Bestätigung und nach einer Kopie des Journals). Drei Operationen des gemeinsamen Index (ebenfalls pro) gehören nicht zum MCP-Satz: sie steuern Veröffentlichung und Netzkern, nicht das Lesen des Index, und laufen als Prozess `python -m ragraf.shared_index` (siehe `docs/03_implementation/SHARED-INDEX.md`). Der Geheimnis-Broker (pro) ist durch zwei Werkzeuge vertreten — `ragraf_broker_issue` (Leihe: Frist, Verwalter, Grund, einmalige Einlösung) und `ragraf_broker_rotate` (Zustand des Plans, Planung, Wechsel des Werts mit Widerruf der Leihen); der Zustand der Leihen liegt in `catalog/broker.json`, das Schema der Speicher ändert sich nicht.
Das vierundzwanzigste ist `use_project`: die Wahl des Korpus, wenn ein Server mehrere Projekte bedient. `context` antwortet, welcher Korpus gewählt ist und auf welcher Grundlage: ohne es waren Antworten über ein fremdes Projekt nicht von Antworten über das eigene zu unterscheiden.

**Neunundzwanzig Sprachen** werden über einen Syntaxbaum zerlegt (Knoten und Kanten des Graphen). Bei sechzehn ist die Karte der Knoten durch ein Profil beschrieben: PHP, Python, JavaScript, TypeScript, Java, C#, C, C++, Ruby, Lua, Go, Perl, bash, PowerShell, CSS, HTML. Bei dreizehn (Rust, Kotlin, Swift, Scala, Dart, R, F#, OCaml, Solidity, Fortran, MATLAB, Elm, Lisp) stammt sie aus der tags-Abfrage der Grammatik selbst: die Knotennamen dieser Sprachen stimmen mit den gewohnten nicht überein, und ein Profil für jede Einzelne hieße, Namen zu erfinden. Jede Sprache wird an einer Probendatei geprüft — mindestens ein gefundenes Symbol (`tests/test_new_languages.py`).

Weitere vierzig werden als Text angebunden: die Dateien sind über die Suche auffindbar, geben aber keine Knoten. Das sind Julia, Elixir, Erlang, Haskell, Clojure, Groovy, Visual Basic, Pascal/Delphi, Objective-C, Zig, Nim, Crystal, Tcl, Prolog, Ada, Verilog, VHDL, Assembler, HCL/Terraform, Dockerfile, Makefile, CMake, SCSS, Less, Vue, Svelte, GraphQL, Protobuf, SQL, batch, Markdown, RST, YAML, JSON, TOML, INI, XML, reiner Text und die Sprache `document`.

**Dokumente werden vollständig indiziert, auch alte Fassungen.** Word: `.docx`, `.docm`, `.dotx` und `.doc` (Word 97 — eigener OLE-Parser, ohne LibreOffice). Excel: `.xlsx`, `.xlsm`, `.xltx` und `.xls` (BIFF8). PowerPoint: `.pptx`, `.pptm`, `.ppsx`, `.potx` und `.ppt`. Dazu PDF, OpenDocument (`.odt`, `.ods`, `.odp`, `.odg`), EPUB, RTF, Mails `.eml` und `.msg`, Dateien von WPS Office. Formate ohne Parser (Pages, OneNote, CHM, DjVu, `.xlsb`) geben sich nicht als leeres Dokument aus — sie werden mit einem Grund gekennzeichnet.

Was indiziert wird, wählt man in der Oberfläche: **Einstellungen → Registerkarte „Formate"** — 84 Formate nach Gruppen (Sprachen, Dokumente, Daten), ein Filterfenster nach Name, Endung und Art der Auswertung, die Schaltflächen „alle"/„keine" und ein Zähler der eingeschalteten. Standardmäßig ist alles eingeschaltet, und der Wert `*` bedeutet „alles, auch später hinzugekommene". Das Abschalten eines Formats ändert den Bestand des Korpus und löst bei der nächsten Prüfung der Aktualität eine Neuindizierung aus.

---

## Wie es aufgebaut ist

**Drei SQLite-Speicher** pro Korpus, im Verzeichnis `.ragraf/` **innerhalb des indizierten Projekts**:

| Speicher | Was darin liegt |
|---|---|
| `graph.sqlite` | Knoten (Klasse, Funktion, Methode), Kanten (Aufrufe, Vererbung, Importe), Schema der Datenbank |
| `lexvec.sqlite` | die Lexik FTS5 (BM25) und die Vektoren in einer Datei; der Rohtext der Chunks für die Ausgabe von Ausschnitten |
| `control.sqlite` | Revisionen, Journal der Abweichungen, Telemetrie der Zugriffe |

**Ablauf der Indizierung:** `scan` (Durchlauf und Prüfsummen) → `ast` (tree-sitter: Symbole, Aufrufe, SQL) → `load` → `resolve` (Namen) → `schema` (Tabellen aus den Migrationen) → `cpg` (interprozedurale Aufrufe über Joern) → `lexvec` (FTS5 + Vektoren).

**Drei Entscheidungen, die das Verhalten bestimmen** — jede aus einem gemessenen Defekt abgeleitet:

1. **Unveränderlicher Index und Journal der Unterschiede statt „Aktualisierung nebenbei".** Innerhalb einer Revision ändert sich der Index nicht; Änderungen am Code sammeln sich im Journal, und die betroffenen Fakten werden als `stale` gekennzeichnet. Sonst würden Fakten aus verschiedenen Zeitpunkten in einer Antwort ohne Unterschied vermischt — genau so entstehen falsche Schlüsse.
2. **Der Agent bekommt keine Abfragesprache für den Graphen.** Nur feste Operationen. Begründung: in LLMxCPG (USENIX Security '25) liefern Basismodelle 132/1278 und 19/1278 gültige CPGQL-Abfragen gegenüber 1278/1278 bei einem trainierten Modell — eine freie Sprache wird also zum Generator von Fehlern.
3. **Jeder Fakt trägt einen Beleg.** `path:line`, `provenance` (`stated` — belegt, `derived` — Heuristik), `index_rev`, `confidence`. Eine Kante ohne Beleg wirkt nur deshalb zuverlässig, weil sie „aus der Datenbank" kommt.

**Degradierung statt Absturz.** Fehlt der Code-Property-Graph, ergibt sich `capability=degraded` und eine verständliche Ablehnung der Operation `slice` mit Angabe des tatsächlichen Modus; die übrigen Operationen arbeiten. Ohne Einbettungsmodell arbeitet die lexikalische Suche. Ohne freien Port nennt der Server den Grund. Eine stille Degradierung gibt es nicht.

---

## Installation

Erforderlich ist **Python 3.13 oder neuer**. Administratorrechte sind nicht nötig.

### Windows (PowerShell 7.6+)

```powershell
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
pwsh -NoLogo -NoProfile -File scripts/install.ps1
```

Das Skript legt eine virtuelle Umgebung in `~/.ragraf/venv` an, installiert die Abhängigkeiten und **prüft, dass eine arbeitsfähige Umgebung entstanden ist** — Schritt für Schritt, mit einer Ergebnistabelle. Es „tut nicht so, als hätte es installiert": jeder Schritt endet mit einer Prüfung.

Nützliche Varianten:

```powershell
# installieren und sofort einen Korpus für das eigene Projekt vorbereiten
pwsh -File scripts/install.ps1 -Project C:\work\myproject

# dasselbe plus vollständige Indizierung (lädt das Einbettungsmodell, ~240 MB)
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index

# Indizierung ohne Vektoren: das Modell wird nicht geladen, die lexikalische Suche arbeitet
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index -NoVectors

# in einem Netz mit TLS-Inspektion (Unternehmensproxy)
pwsh -File scripts/install.ps1 -WithTruststore
```

### Andere Plattformen

Ein gemeinsamer Weg, der überall funktioniert, wo Python 3.13+ vorhanden ist:

```bash
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -e .                # oder: pip install -r requirements.txt
ragraf --version
```

Ohne Installation des Pakets arbeitet das Produkt ebenfalls — über einen Wrapper, der `src/` selbst in den Pfad aufnimmt:

```bash
python tools/ragraf.py --version
```

> `scripts/install.ps1` ist unter Windows geprüft (PowerShell 7.6, Python 3.14.7). Der gemeinsame Weg über `pip` unter Linux und macOS wurde **nicht geprüft** — das Produkt wurde unter Windows entwickelt und gemessen.

### Externe Schicht: Java und Joern (optional)

Für den interprozeduralen Graphen (Operation `slice`) braucht man JDK 21 und Joern. Ohne sie arbeitet das Produkt: der Index wird gebaut, Suche und Struktur sind verfügbar, `slice` lehnt mit einem Grund ab.

```powershell
pwsh -File scripts/install-joern.ps1
```

Das Archiv landet in `.tools/downloads/` — das Skript ist idempotent. Das entpackte Paket belegt etwa 5 GB.

---

## Erste Schritte

```bash
# 1. Den Korpus vorbereiten: finden, nachbauen, einen veralteten Index neu bauen.
#    Ein Korpus für ein unbekanntes Verzeichnis entsteht mit demselben Befehl.
ragraf ensure C:\work\myproject

# 2. Die Umgebung prüfen (Bericht über 11 Komponenten).
#    ACHTUNG: der Befehl gibt IMMER den Code 0 zurück — er ist keine Freigabeprüfung, lesen Sie den Bericht.
ragraf -p <config> selftest

# 3. Fragen
ragraf -p <config> query search "reindex latency"
ragraf -p <config> query symbol --name corpus_hash
ragraf -p <config> query code --name corpus_hash
ragraf -p <config> query callers --name dedupe
ragraf -p <config> query impact --target Facade --depth 2

# 4. Aktualität: hat sich der Code nach der Indizierung geändert
ragraf -p <config> check
```

Die vollständige Liste der Befehle — `ragraf --help`. Wichtig: **die globalen Schlüssel `--project/-p` und `--json` stehen VOR dem Unterbefehl** (`ragraf -p cfg query search ...`), die eigenen Schlüssel des Unterbefehls danach.

### Korpusverwaltung

```bash
ragraf init C:\work\myproject         # eine Korpuskonfiguration anlegen und ihren Pfad zeigen
ragraf -p <config> register           # die Entwicklung im Register eintragen
ragraf -p <config> index              # vollständige Indizierung
ragraf -p <config> reindex            # Neuindizierung + das Journal der Abweichungen archivieren
ragraf -p <config> drift --scan       # Unterschiede eintragen, OHNE den Index zu ändern
ragraf -p <config> status             # Zustand: Revision, Abdeckung, Größe des Journals
ragraf -p <config> usage              # Telemetrie: Zugriffe, Fakten, Schätzung des Effekts
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

`--project` ist Pflicht. `--agent` und `--agent-kind` braucht man nur für getrennte Statistik der Zugriffe: ohne sie landen die Aufrufe als `unknown` im Journal.

### Einstellungsseite

```bash
python tools/ragraf-admin.py --no-browser
```

Sie öffnet unter `127.0.0.1:8777` und lauscht nur auf dieser Adresse. Sie zeigt Korpora und den Zustand ihrer Indizes, die Größen der Speicher, die Qualität des Index, die Statistik der Zugriffe. Ändernde Anfragen verlangen ein Zugangstoken, das beim Start ausgegeben wird.

Die Seite der Quellen — `http://127.0.0.1:8777/sources`: erlaubte Wurzeln „Ich bin du", ihr Zustand, Hinzufügen, Durchsuchen, Einschalten und Löschen. Änderungen verlangen dasselbe Token; der Inhalt der Dateien wird nicht gelesen, geheimnistragende Dateien sind nur als Anzahl und Regel sichtbar. Eine Netzwurzel ist nur als UNC-Pfad oder eingehängter Pfad mit `--network` zulässig; URL-Konnektoren (`ssh://`, `s3://`, `webdav://`) sind nicht umgesetzt und werden mit einem Grund abgelehnt.

Die Seite des Nutzens — `http://127.0.0.1:8777/value`: Zugriffe der letzten 30 Tage, Fakten, Schätzung von Token und Zeilen, über die Korpora summiert. Schätzungen sind ausdrücklich gekennzeichnet; Pfade und Anfragen stehen dort nicht.

---

## Anforderungen und Grenzen

| Was | Wert |
|---|---|
| Python | **3.13+** (als `requires-python` deklariert; geprüft mit 3.14) |
| Plattform | Windows + PowerShell 7.6 — geprüft; andere Betriebssysteme — **nicht geprüft** |
| Rechte | nicht erforderlich: alles arbeitet aus dem Benutzerverzeichnis |
| Speicherplatz | Index eines Korpus `.ragraf/` — etwa 1 MB auf 1–2 Quelldateien; externe Schicht Java/Joern — ~5 GB |
| Internet | zweimal nötig: bei der Installation (PyPI) und bei der **ersten Indizierung mit Vektoren** — das Einbettungsmodell wird mit ~240 MB nach `~/.ragraf/models` geladen |
| Java + Joern | optional; ohne sie `capability=degraded` und `slice` nicht verfügbar |
| Grafikkarte | optional; beschleunigt nur die Kodierung der Vektoren und wird nach der Schwelle der Rentabilität eingeschaltet |

**Zu Internet und Zertifikaten.** In einem Netz mit TLS-Inspektion (Unternehmensproxy) scheitert das Laden des Modells an der Prüfung des Zertifikats. Das behebt die Installation von `truststore`:

```bash
pip install truststore      # oder: install.ps1 -WithTruststore
```

Im Heimnetz ist das Paket nicht nötig. Gemessen: ohne es — Ablehnung `certificate verify failed`, mit ihm — 240 MB werden in 9,2 s geladen.

**Zum Cache des Modells.** Das Cache-Verzeichnis wird durch die Variable `RAGRAF_MODEL_CACHE` überschrieben. Leseoperationen laden das Modell nie herunter — nur die Indizierung mit Vektoren.

---

## Dokumentation

| Dokument | Was darin steht |
|---|---|
| `docs/02_architecture/ARCHITECTURE.md` | Prinzipien, Komponenten, Schema der Speicher, Vertrag der Operationen |
| `docs/02_architecture/TZ.md` | funktionale und nichtfunktionale Anforderungen, Abnahmekriterien |
| `docs/03_implementation/INTEGRATION.md` | Anbindung an einen Agenten: MCP, Arbeitsregeln, Reglement |
| `docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md` | Einrichtung in HARNESS und AutoClaw, vollständiger Katalog der Möglichkeiten |
| `docs/03_implementation/HARNESS_INTEGRATION.md` | DeepSeek Harness: MCP, Skill, Regeln, Freigabeprüfung |
| `docs/03_implementation/AUTOCLAW_INTEGRATION.md` | AutoClaw: MCP, Skill, Regeln, Rücknahme |
| `docs/03_implementation/CPG_INTEGRATION.md` | Code-Property-Graph: Joern, JDK, Modi der Degradierung |
| `docs/skills/ragraf-core/SKILL.md` | der Skill für die Arbeit mit dem Index: Wahl der Operation, Wiederherstellung |
| `docs/05_article/RAGRAF_ARTICLE.md` | Übersichtsartikel: Stack, Architektur, Messungen |
| `AGENTS.md` | die Regeln der Entwicklung dieses Projekts; grundlegende Prüfung der Konventionen |

---

## Entwicklung

```bash
# die Prüfungen laufen als eigene Skripte — einen gemeinsamen Starter gibt es nicht
python tests/test_lang_routing.py
python tests/test_watch.py

# die Prüfung der Konventionen des Projekts (14 Prüfungen)
pwsh -NoLogo -NoProfile -File tools/Invoke-ProjectDoctrine.ps1

# Aktualisierung des Index von RAGRAF selbst
python tools/ragraf.py ensure .
```

In der Lieferung sind nur die Prüfungen, die auf **synthetischen Fixturen** arbeiten und übertragbar sind. Die übrigen messen das Verhalten an realen Korpora des Autors (PHP-Portal, C#-Dienst, Konfigurationen ihrer Projekte) und haben auf fremdem Code keinen Sinn.

Was direkt nach der Installation durchläuft:

| Prüfung | Prüfungen |
|---|---|
| `test_lang_routing.py` | 56 |
| `test_watch.py` | 31 |
| `test_script_extract.py` | 25 |
| `test_threshold.py` | 23 |
| `test_ensure.py` | 17 |
| `test_csharp_records.py` | 12 |

Drei Prüfungen brauchen etwas, das in der Grundinstallation fehlt, und das ist **kein Defekt der Lieferung**, sondern der Charakter der Prüfung:

* `test_joern_server.py` — braucht die externe Schicht Java + Joern (`scripts/install-joern.ps1`);
* `test_graph_algo.py` — braucht `torch` (etwa 2,5 GB), das weder in `pyproject.toml` noch in `requirements.txt` steht: Graphalgorithmen auf der Grafikkarte sind ein optionaler Zweig, und ohne `torch` degradiert er ehrlich auf den Prozessor;
* `test_cpg_call_names.py` — braucht eine Korpuskonfiguration (das Verzeichnis `config/` wird nicht veröffentlicht); man kann die Prüfung starten, indem man `examples/ragraf.example.yaml` nach `config/` kopiert und dort die eigene Wurzel einträgt.

`selftest` ist **keine Freigabeprüfung**: es gibt einen Bericht über 11 Komponenten aus und liefert immer den Code 0. Ein anderer Code ist nicht vorgesehen, deshalb beweist ein „grüner selftest" für sich genommen nichts.

---

## Mitwirkung

Nützliche Meldungen: ein reproduzierbarer Defekt mit Befehl und Ausgabe; eine Abweichung zwischen Dokument und Code; ein Fall von Degradierung, den das Produkt nicht genannt hat. Willkommen ist eines: ein Fakt mit Verweis auf Datei und Zeile.

Das Projekt wird für konkrete Aufgaben entwickelt, deshalb werden Vorschläge zur Architektur bereitwilliger besprochen als große Patches ohne vorheriges Gespräch.

---

## Das Projekt in Zahlen und der gemessene Prüfstand

Die veröffentlichten Zahlen (abgeglichen am 26.10.2026; die Größen des Index — nach dem Eintrag `rev-0415` vom 27.10.2026): **54 Operationen** der Fassade, **61 MCP-Werkzeuge**, **21** CLI-Unterbefehle, **29** Sprachen über den Syntaxbaum und **40** als Text (einschließlich `document`), **84** Datenformate im Register (61 Sprachen, 9 Familien von Dokumenten, 10 Arten von Daten, 4 ohne Parser); der eigene Index von RAGRAF — 869 Dateien, 5 227 Knoten und 68 845 Kanten (der Stand wird mit dem aktiven Revisionseintrag durch die Prüfung `Test-DocumentedNumbers` abgeglichen — Zahlen einer vergangenen Revision ergeben eine Warnung mit ihrer `rev_id` und ihrem Datum, bei der Veröffentlichung ist das eine Ablehnung: `tools/release/gate_release.py`; die geltenden Zahlen — `python tools/ragraf.py -p config/ragraf.yaml status`).

**Der Prüfstand, entpersonalisiert** (Namen der Projekte, Pfade und Domänen sind verborgen). Die Zahlen dieses Abschnitts sind eine **Momentaufnahme vom 16.10.2026**, nicht der heutige Zustand: zu jenem Zeitpunkt standen **28 Korpora im Register, 25 indiziert** — 10 mit der CPG-Schicht, 15 im Modus `degraded`. Bei 16 von ihnen wurde der Revisionseintrag am selben Tag gelesen: **7 527 Dateien, 94 528 Symbole, 791 871 Kanten, 86 790 Chunks**.

Die aktuelle Größe des Registers ist mit Absicht von der Momentaufnahme getrennt: sie ändert sich mit jeder angebundenen Entwicklung, und sie in die Tabelle unten einzusetzen hieße, die gestrige Messung als heutige auszugeben. Sie steht in einer eigenen Zeile und wird mit `catalog/catalog.json` abgeglichen — **nach dem Register am 27.10.2026: 42 Korpora, 33 indiziert**. Diese Zahl bewacht eine Prüfung des Checkers: weicht sie vom Register ab, ist das ein Befund und keine veraltete Zeile.

| Korpus | Typ | Dateien | Symbole | Kanten | Modus |
|---|---|---|---|---|---|
| C-02 | Webanwendung auf PHP | 2 722 | 32 761 | 355 192 | degraded |
| C-04 | Dienst / Desktop auf C# | 1 274 | 20 331 | 98 605 | degraded |
| C-05 | Webanwendung auf PHP | 928 | 17 381 | 99 029 | full |
| C-18 | Automatisierungssatz auf PowerShell | 81 | 324 | 4 214 | degraded |
| C-24 | Markdown + bash (der kleinste) | 18 | 184 | 1 515 | degraded |

Die vollständige Tabelle mit 16 Zeilen, die Regeln der Entpersonalisierung und der Vorbehalt zur Zählung stehen im [Wiki → Benchmarks and Limits](https://github.com/paulmann/RAGRAF/wiki/11-Benchmarks-and-Limits). Der Vorbehalt: das Register der Korpora und der Revisionseintrag ergeben für eine Revision verschiedene Summen (8 721 gegen 4 498 Symbole, 41 201 gegen 56 106 Kanten), deshalb sind die Zahlen aus dem Revisionseintrag genommen. Die private Karte der Kennungen `C-*` → Projekte liegt außerhalb des Repositorys: sie ist beim Autor und gehört nicht zur Lieferung.

---

## Lizenz

**[Business Source License 1.1](LICENSE)** — der Quellcode ist offen, und die Lizenz erlaubt jede Nutzung, einschließlich kommerzieller Tätigkeit und produktiven Einsatzes innerhalb einer Organisation. Verboten ist eines: das Produkt oder seine Ableitungen Dritten als gehosteten oder verwalteten Dienst bereitzustellen, dessen Hauptwert die Funktionalität von RAGRAF ist. Vier Jahre nach dem Erscheinen einer Version geht sie auf Apache-2.0 über.

Die Quellen der Wahrheit — eine Frage pro Datei, ohne Nacherzählung:

| Frage | Datei |
|---|---|
| Text der Lizenz und ihre Parameter | [`LICENSE`](LICENSE) |
| Dritte Parteien und ihre Lizenzen | [`NOTICE`](NOTICE) |
| Bedingungen eines Beitrags | [`CLA.md`](CLA.md) |
| Unterstützung: was zugesagt ist und was nicht | [`SUPPORT.md`](SUPPORT.md) |
| Was erlaubt ist, was nicht, Geschichte der Lizenzen | [`docs/03_implementation/LICENSING.md`](docs/03_implementation/LICENSING.md) |

> BSL 1.1 ist **keine Open-Source-Lizenz**: die OSI billigt sie nicht, deshalb sind die richtigen Worte „source-available", „der Quellcode ist offen". Das Lizenzzeichen auf GitHub zeigt „Other": BSL steht nicht im Katalog, den es erkennt.

Für einen Fall, den die Erlaubnis nicht abdeckt, braucht man eine eigene kommerzielle Lizenz vom Rechtsinhaber: alle Rechte, die nicht ausdrücklich eingeräumt sind, bleiben bei ihm. Früher eingeräumte Rechte werden nicht widerrufen — die Geschichte der Lizenzen steht in [`docs/03_implementation/LICENSING.md`](docs/03_implementation/LICENSING.md).

Externe Abhängigkeiten behalten ihre Lizenzen: die Liste mit den gemessenen Versionen steht in [`NOTICE`](NOTICE). Java und Joern gehören nicht zur Lieferung und werden getrennt installiert.

### Geheimnisspeicher (Entscheidung des Eigentümers 2026-10-24)

Früher galt die Regel „Werte gibt es nicht und kann es nicht geben" — nur ein Register der Stellen. Der Eigentümer hat sie geändert: Werte werden gebraucht, aber sie dürfen **nur** in `catalog/` liegen (außerhalb des Korpus), **nur** verschlüsselt, und sie werden einem Agenten nach Recht mit Eintrag im Audit ausgegeben. Die Regel „nicht indizieren" bleibt in Kraft: `catalog/**` ist vom Korpus ausgeschlossen, deshalb gelangt ein Wert weder in den Index noch in die Suche.

- Typen: Passwort, Token, API-Schlüssel, privater Schlüssel, Zertifikat, Verbindungszeichenfolge,
  SSH-Schlüssel, Webhook, Notiz — also nicht „nur Passwörter";
- Verschlüsselung: PyNaCl `SecretBox` (XSalsa20-Poly1305), der Schlüssel eines Eintrags wird aus dem
  Hauptschlüssel mit dem Salz der Kennung abgeleitet, in den Geheimtext ist der Kontext des Eintrags
  eingebettet — das Vertauschen von Zeilen zwischen Einträgen wird durch die Prüfung der
  Authentizität abgelehnt;
- Rechte nach Agenten: `all` (standardmäßig — für alle zugänglich), `listed` (nur für die
  aufgeführten), `owner_only` (nur für den, der gespeichert hat); dazu eine gemeinsame Liste
  zugelassener Agenten in den Einstellungen;
- Audit: jede Ausgabe und jede Ablehnung, das Journal ist auf der Ebene der Datenbank append-only;
- Einstellungen: der Abschnitt `secrets_*` in der Oberfläche — Einschalten des Speichers, Speichern
  angetroffener Geheimnisse, Sichtbarkeit nach Vorgabe, Pflicht eines Grundes für die Ausgabe,
  Höchstlänge eines Werts, Liste zugelassener Agenten, Warnung zur Frist.

CLI: `python tools/ragraf.py secrets list | show | reveal | set | rm | grant |
revoke | check | rotate | export | import | policy | audit`. Der Wert für `set`
kommt aus stdin, aus einer Umgebungsvariablen oder aus einer Datei — nicht aus einem Argument der
Kommandozeile. MCP: `ragraf_secret_list/show/reveal/set/grant/revoke/delete/policy`.
