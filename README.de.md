# RAGRAF

<!-- md-i18n:switcher -->
**In anderen Sprachen:** [Русский](README.md) · Deutsch · [中文](README.zh.md) · [हिन्दी](README.hi.md) · [Español](README.es.md) · [Русский (Kopie)](README.ru.md)

**Graph- und vektorbasierte Wissensdatenbank über Quellcode für LLM-Agenten.**

[**Webseite**](https://ragraf.ru) · [**Wiki**](https://github.com/paulmann/RAGRAF/wiki) · [**Dokumentation (Pages)**](https://paulmann.github.io/RAGRAF/) · [**Veröffentlichungen**](https://github.com/paulmann/RAGRAF/releases) · [**Diskussionen**](https://github.com/paulmann/RAGRAF/discussions)

![RAGRAF — graph- und vektorbasierte Wissensdatenbank über Quellcode für LLM-Agenten: 61 MCP-Werkzeuge, überprüfbare Fakten mit der Angabe Datei:Zeile, vollständig lokal](assets/social-preview.png)

> **Diese Übersetzung ist unvollständig und bleibt beim Stand 0.3.x stehen.** Abschnittsbestand und Zahlen sind mit dem aktuellen Produkt abgeglichen, der Inhalt nicht: es fehlen die Lizenzstufen Community / Pro / Enterprise, der Secret-Broker, die Qualitätsschleife und der gemeinsame Index, die abgesetzten `connectors` und `fleet`, die Auswertung von Office- und PDF-Dokumenten sowie die Operationen `route` und `neighbors`. Der vollständige aktuelle Text steht im Hauptdokument [`README.md`](README.md) (russisch); welche Möglichkeiten zu welcher Lizenzstufe gehören, steht in [`EULA.md`](EULA.md) und in `src/ragraf/edition_baseline.json`. Der Stand dieser Übersetzung ist in [`docs/03_implementation/RELEASE-SOURCES.md`](docs/03_implementation/RELEASE-SOURCES.md) festgehalten.

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
| Sie binden den Index an einen Agenten an | **61 MCP-Werkzeuge** `ragraf_*` oder die CLI | dieselben Operationen über stdio — siehe [`docs/03_implementation/INTEGRATION.md`](docs/03_implementation/INTEGRATION.md) |

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
| `calibrate` | die Kalibrierung der Schwellen |
| `usage` | die Telemetrie der Zugriffe und die Schätzung des Effekts |
| `diff` | was sich zwischen Revisionen geändert hat: Dateien, Symbole, Kanten, Regressionen (P2-1) |
| `task_stats` | den Bericht zu einer Aufgabe: Zugriffe auf den Index, Entscheidungen der Freigabeprüfung (§3.4) |
| `gate` | die Entscheidung der Freigabeprüfung zu einem Werkzeugaufruf: `allow` / `ask` / `deny` (§3.2) |
| `note` / `notes` | eine Notiz zu einer Entscheidung schreiben und lesen — Wissen zwischen Sitzungen (P2-3) |

Zwei weitere Modi sind keine Operationen, sondern eine Form der Antwort: **`answer=true`** liefert eine formulierte Antwort mit Verweisen statt eines strukturellen Ergebnisses (P2-2), und `brief=true` eine knappe Hülle der Antwort. `usage` zeigt die Kennzahlen des Effekts (`saved_calls`, `hit_rate`, `avg_ms`, `misses`, `stale_served`).

Dieselben Operationen sind als **61 MCP-Werkzeuge** (`ragraf_*`) verfügbar — zum Anbinden an einen Agenten als Server — und über die CLI. Eine eigene Gruppe von Werkzeugen ist die lebende Schicht der Datenbanken: `ragraf_db_schema`, `ragraf_db_table`, `ragraf_db_relations`, `ragraf_db_sample`, `ragraf_db_drift`, `ragraf_db_cross`.
Das vierundzwanzigste ist `use_project`: die Wahl des Korpus, wenn ein Server mehrere Projekte bedient. `context` antwortet, welcher Korpus gewählt ist und auf welcher Grundlage: ohne es waren Antworten über ein fremdes Projekt nicht von Antworten über das eigene zu unterscheiden.

Die Tabelle nennt nur die Operationen der Stufe Community. Die bezahlten Stufen sind hier nicht beschrieben — das ist der Teil, den diese Übersetzung nicht abdeckt: `quality_suite`, `quality_diff`, `quality_prune`, `shared_index_status`, `shared_index_publish`, `shared_index_query`, `broker_issue`, `broker_rotate`, `sso_login`, `sso_map_roles`, `siem_export`, `siem_test`, `license_offline`, `attest_bundle`. Ihre Zusammensetzung steht in `src/ragraf/edition_baseline.json`, die Bedingungen in [`EULA.md`](EULA.md).

**Neunundzwanzig Sprachen** werden über einen Syntaxbaum zerlegt (Knoten und Kanten des Graphen), vierzig weitere werden als Text angebunden — die Dateien sind über die Suche auffindbar, geben aber keine Knoten; im Formatregister stehen **84** Formate. Der vollständige Bestand an Sprachen und Formaten steht im Hauptdokument [`README.md`](README.md).

---

## Das Projekt in Zahlen

Zahlen werden in dieser Übersetzung nicht abgeschrieben: sie ändern sich von Etappe zu Etappe, und die Übersetzung bliebe dahinter zurück. Die geprüften Werte stehen im Hauptdokument [`README.md`](README.md) im Abschnitt „Das Projekt in Zahlen und der gemessene Prüfstand“; sie bewacht `Test-DocumentedNumbers` in [`tools/Invoke-ProjectDoctrine.ps1`](tools/Invoke-ProjectDoctrine.ps1).

Abgeglichen mit dem aktiven Revisionseintrag `rev-0377` (27.10.2026): **54** Fassadenoperationen, **61** MCP-Werkzeuge, **50** `query`-Operationen bei **21** CLI-Unterbefehlen, **29** Sprachen über den Syntaxbaum und **40** als Text, **84** Formate im Register; der eigene Index von RAGRAF — 858 Dateien, 5 130 Knoten, 67 413 Kanten.

Der Prüfstand, der Umfang der Lieferung und die Testläufe werden hier absichtlich nicht wiederholt: sie ändern sich, und eine abgeschriebene Zahl wäre schneller falsch als richtig.

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
# installieren und sofort einen Korpus für das eigene Projekt vorbereiten
pwsh -File scripts/install.ps1 -Project C:\work\myproject

# dasselbe plus vollständige Indizierung (lädt das Embedding-Modell, ~240 MB)
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index

# Indizierung ohne Vektoren: kein Modell-Download, die lexikalische Suche arbeitet
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index -NoVectors

# in einem Netz mit TLS-Inspektion (Unternehmensproxy)
pwsh -File scripts/install.ps1 -WithTruststore
```

### Andere Plattformen

Ein gemeinsamer Weg, funktioniert überall, wo Python 3.13+ vorhanden ist:

```bash
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -e .                # oder: pip install -r requirements.txt
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
# 1. Korpus vorbereiten: finden, bei Bedarf anlegen, veralteten Index neu bauen.
#    Ein Korpus für ein unbekanntes Verzeichnis entsteht mit demselben Befehl.
ragraf ensure C:\work\myproject

# 2. Umgebung prüfen (Bericht über 11 Komponenten).
#    ACHTUNG: der Befehl gibt IMMER Code 0 zurück — er ist keine Freigabeprüfung, lesen Sie den Bericht.
ragraf -p <config> selftest

# 3. Fragen
ragraf -p <config> query search "Reindexierungsverzögerung"
ragraf -p <config> query symbol --name corpus_hash
ragraf -p <config> query code --name corpus_hash
ragraf -p <config> query callers --name dedupe
ragraf -p <config> query impact --target Facade --depth 2

# 4. Aktualität: hat sich der Code nach der Indizierung geändert
ragraf -p <config> check
```

Die vollständige Befehlsliste steht in `ragraf --help`. Wichtig: **die globalen Schalter `--project/-p` und `--json` stehen VOR dem Unterbefehl** (`ragraf -p cfg query search ...`), die eigenen Schalter des Unterbefehls dagegen danach.

### Verwaltung des Korpus

```bash
ragraf init C:\work\myproject         # Korpuskonfiguration anlegen und den Pfad dazu ausgeben
ragraf -p <config> register           # das Projekt im Register eintragen
ragraf -p <config> index              # vollständige Indizierung
ragraf -p <config> reindex            # Neuindizierung + Archivierung des Abweichungsjournals
ragraf -p <config> drift --scan       # Abweichungen eintragen, OHNE den Index zu ändern
ragraf -p <config> status             # Zustand: Revision, Abdeckung, Größe des Journals
ragraf -p <config> usage              # Telemetrie: Aufrufe, Fakten, Schätzung des Effekts
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
pip install truststore      # oder: install.ps1 -WithTruststore
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
| [`docs/02_architecture/ARCHITECTURE.md`](docs/02_architecture/ARCHITECTURE.md) | Prinzipien, Komponenten, Schema der Speicher, Vertrag der Operationen |
| [`docs/02_architecture/TZ.md`](docs/02_architecture/TZ.md) | funktionale und nichtfunktionale Anforderungen, Abnahmekriterien |
| [`docs/03_implementation/INTEGRATION.md`](docs/03_implementation/INTEGRATION.md) | Anbindung an einen Agenten: MCP, Arbeitsregeln, Verfahren |
| [`docs/03_implementation/CPG_INTEGRATION.md`](docs/03_implementation/CPG_INTEGRATION.md) | Code-Property-Graph: Joern, JDK, Modi der Degradierung |
| [`docs/skills/ragraf-core/SKILL.md`](docs/skills/ragraf-core/SKILL.md) | die Fertigkeit für die Arbeit mit dem Index: Wahl der Operation, Wiederherstellung |
| [`docs/05_article/RAGRAF_ARTICLE.md`](docs/05_article/RAGRAF_ARTICLE.md) | Überblicksartikel: Technikstapel, Architektur, Messungen |
| [`docs/03_implementation/RELEASE-SOURCES.md`](docs/03_implementation/RELEASE-SOURCES.md) | Herkunft jeder Datei der Lieferung und der Stand jeder Übersetzung |
| [`ROADMAP.md`](ROADMAP.md) | geplante Verbesserungen mit stabilen Kennungen (`R-*`) |

---

## Entwicklung

```bash
# Tests laufen als einzelne Skripte — es gibt keinen gemeinsamen Runner
python tests/test_lang_routing.py
python tests/test_watch.py

# Prüfprogramm der Projektkonventionen (14 Prüfungen)
pwsh -NoLogo -NoProfile -File tools/Invoke-ProjectDoctrine.ps1

# Index von RAGRAF selbst aktualisieren
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

**Lizenzstufen.** Community, Pro und Enterprise sind Stufen **einer** geschlossenen Lieferung, keine drei getrennten Zusammenstellungen: die Stufe entscheidet, was in derselben Dateisammlung offen ist, und der Übergang ist eine Lizenzaktivierung, keine Installation. Die Bedingungen stehen in [`EULA.md`](EULA.md), die Zuordnung der Möglichkeiten zu den Stufen in `src/ragraf/edition_baseline.json`; `connectors` und `fleet` gehören in keiner Stufe zum Gegenstand und sind im Entwicklungsplan genannt.

> Dem Lizenzabzeichen auf GitHub ist nicht zu trauen: nicht jeder Ausdruck wird erkannt, und BUSL-1.1 kann als „Other" erscheinen. Die Quellen der Wahrheit sind die Datei `LICENSE` und das Feld `license` in `pyproject.toml`.

Externe Abhängigkeiten behalten ihre Lizenzen: `tree-sitter` (MIT), `sqlite-vec` (MIT/Apache-2.0), `fastembed` (Apache-2.0), `PyYAML` (MIT), `tiktoken` (MIT), `ijson` (BSD). Java und Joern, die getrennt installiert werden, stehen unter Apache-2.0 und gehören nicht zur Auslieferung.
