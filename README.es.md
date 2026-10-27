# RAGRAF

<!-- md-i18n:switcher -->
**En otros idiomas:** [Русский](README.ru.md) · [Deutsch](README.de.md) · [English](README.md) · [中文](README.zh.md) · [हिन्दी](README.hi.md) · Español

> **Traducción completa a la versión 0.4.0.** Esta es la versión española del documento principal [`README.md`](README.md): sigue siendo el único lugar donde se hacen los cambios, y en caso de discrepancia prevalece el texto inglés. Estado de esta versión: 27.10.2026.

**Base de conocimiento grafo-vectorial sobre el código fuente para agentes LLM.**

[![CI](https://github.com/paulmann/RAGRAF/actions/workflows/ci.yml/badge.svg)](https://github.com/paulmann/RAGRAF/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/paulmann/RAGRAF?label=release&sort=semver)](https://github.com/paulmann/RAGRAF/releases)
[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-informational)](pyproject.toml)

![RAGRAF — base de conocimiento grafo-vectorial sobre el código fuente para agentes LLM: 61 herramientas MCP, hechos verificables con referencia archivo:línea, totalmente local](assets/social-preview.png)

[**Inicio rápido**](#primeros-pasos) · [**Documentación**](#documentación) · [**Wiki**](https://github.com/paulmann/RAGRAF/wiki) · [**Registro de cambios**](CHANGELOG.md) · [**English version**](README.md)

> **Estado de la documentación.** Todas las cifras de abajo se midieron el 26–27.10.2026 contra la versión 0.4.0; los tamaños del índice de RAGRAF se comparan con la entrada de revisión activa mediante `Test-DocumentedNumbers`, y una cifra que pertenece a una revisión pasada hace fallar la puerta de publicación en lugar de pasar por medición de hoy.

Un asistente que responde a las preguntas de un agente sobre la base de código — *dónde está, quién lo llama, qué se rompe si lo cambio, cómo se hizo algo parecido en otro sitio* — con hechos verificables (`archivo:línea`) en lugar de un `rg` a ciegas y de la lectura de archivos completos. Python 3.13+, SQLite (grafo + FTS5 + vectores en un solo lugar), tree-sitter, embeddings locales sin torch. La capa opcional de grafo de propiedades del código (Java + Joern) abre los cortes; sin ella el producto sigue funcionando y lo dice.

---

## Contenido

- [Qué es y para qué sirve](#qué-es-y-para-qué-sirve)
- [Capacidades](#capacidades)
- [Cómo funciona](#cómo-funciona)
- [Instalación](#instalación)
- [Primeros pasos](#primeros-pasos)
- [Requisitos y límites](#requisitos-y-límites)
- [Documentación](#documentación)
- [Desarrollo](#desarrollo)
- [Contribuir](#contribuir)
- [El proyecto en cifras y el banco de pruebas medido](#el-proyecto-en-cifras-y-el-banco-de-pruebas-medido)
- [Licencia](#licencia)

---

## Qué es y para qué sirve

Un agente que trabaja en un cambio de código consume la mayor parte de su contexto en **encontrar el lugar**: dónde se define una función, quién la llama, cómo se hizo algo parecido cerca. El camino habitual es `rg` más la lectura de archivos completos.

RAGRAF sustituye ese camino por un índice. El agente pregunta mediante una operación fija y recibe un hecho con una referencia `archivo:línea` en lugar de diez candidatos que revisar.

Medido en un proyecto real (un portal en PHP, 14 tareas de navegación, `bench/results/`):

| Indicador | Sin el índice | Con el índice |
|---|---|---|
| Llamadas a herramientas en 14 tareas | 28 (realista: 43) | **14** |
| Promedio por tarea | 2,0 / 3,1 | **1,0** |
| Candidatos que revisar | hasta 34 archivos | 1–5 exactos |
| Archivos leídos completos | 14 (el mínimo) | **0** |
| Acierto en el archivo objetivo al primer intento | — | **92 %** (12 de 13) |
| Verificabilidad (hechos con `archivo:línea`) | — | **100 %** |

El número de llamadas, la tasa de acierto y la verificabilidad son **mediciones** (los artefactos de la ejecución están en `bench/results/`). «Realista: 43» es una corrección del modelo — el agente revisa hasta cinco candidatos en lugar de uno — y queda anotada dentro del propio artefacto.

Una advertencia que suele omitirse: **la ventaja de 2 a 3 veces se refiere a las tareas de localización** («dónde se define», «quién lo llama»). En el ciclo de edición — leer, cambiar, comprobar — el número de llamadas apenas cambia; ahí la ventaja está en otra parte: las ediciones no corrompen el índice y el SQL dudoso se comprueba antes de ejecutarse.

---

## Capacidades

**54 operaciones** sobre un repositorio — un conjunto fijo, sin lenguaje de consultas al grafo:

| Operación | Responde a la pregunta |
|---|---|
| `search` | dónde se habla de esto (híbrido: léxico BM25 + vectores) |
| `search_multi` | dónde aparecen varias cadenas a la vez; qué archivos son copias y cuál de las copias está viva |
| `duplicates` | qué archivos son copias unos de otros y qué demuestra cuál está activo |
| `entrypoints` | puntos de entrada: rutas, manejadores HTTP, CLI, extremos del marcado |
| `dead_code` | archivos y rutinas sin referencias, fuera del alcance |
| `symbol` | dónde se define un símbolo: tipo, archivo, líneas, firma |
| `references` | dónde más se usa |
| `callers` / `callees` | quién lo llama / a qué llama |
| `impact` | qué toca un cambio (aristas declaradas y heurísticas, por separado) |
| `slice` | corte estructural: nodos, aristas, líneas — sin los cuerpos de las funciones |
| `code` | cuerpos de los símbolos (solo con petición explícita) |
| `repo_map` | el mapa del repositorio dentro de un presupuesto de caracteres dado |
| `schema` / `table_usage` | qué guarda una tabla de la base de datos y quién la lee o la escribe |
| `routes` | rutas y puntos de entrada |
| `policy_trace` | quién obtiene un permiso y bajo qué condición |
| `class_usage` | una clase de estilo: dónde se define, dónde se usa, dónde no coinciden |
| `write_check` | si una escritura es posible (el plan se coteja con el esquema antes de ejecutarse) |
| `live_read` | el archivo actual, con las diferencias respecto al índice marcadas |
| `freshness` | si el índice está al día (cotejo de la suma de control) |
| `index_status` | revisión, cobertura, degradación, tamaño del registro |
| `drift_report` / `drift_ack` | el registro de divergencias entre el índice y el código |
| `catalog` | el registro de corpus: qué está indexado y con qué sumas |
| `route` | adónde ir por una respuesta: qué corpus responde y por qué (sin citar) |
| `neighbors` | si existe una implementación lista en corpus vecinos de la misma pila |
| `calibrate` | calibración de umbrales |
| `usage` | telemetría de accesos y estimación del efecto |
| `diff` | qué cambió entre revisiones: archivos, símbolos, aristas, regresiones (P2-1) |
| `task_stats` | informe de una tarea: llamadas al índice, decisiones de la puerta (§3.4) |
| `gate` | la decisión de la puerta para una llamada de herramienta: `allow` / `ask` / `deny` (§3.2) |
| `note` / `notes` | registrar y leer una nota de decisión — conocimiento entre sesiones (P2-3) |
| `quality_suite` / `quality_diff` / `quality_prune` | el bucle de calidad (edición pro): ejecutar las comprobaciones del índice, comparar dos ejecuciones, aligerar el registro de ejecuciones con copia y confirmación |
| `shared_index_status` / `shared_index_publish` / `shared_index_query` | el índice de equipo (edición pro): publicar un corpus para lecturas en red, admitir un cliente, consultar el índice publicado |
| `broker_issue` / `broker_rotate` | el bróker de secretos (edición pro): emitir un secreto a petición — un préstamo con plazo, custodio y motivo — y rotar el valor según un plan (la rotación revoca los préstamos) |

Otros dos modos no son operaciones sino una forma de respuesta: **`answer=true`** devuelve una respuesta redactada con referencias en lugar del resultado estructural (P2-2), y `brief=true` devuelve una envoltura breve de respuesta. `usage` informa de las métricas del efecto (`saved_calls`, `hit_rate`, `avg_ms`, `misses`, `stale_served`).

Las mismas operaciones se exponen como **61 herramientas MCP** (`ragraf_*`) — para conectarlas a un agente como servidor — y a través de la CLI. Un grupo de herramientas es la capa viva de bases de datos: `ragraf_db_schema`, `ragraf_db_table`, `ragraf_db_relations`, `ragraf_db_sample`, `ragraf_db_drift`, `ragraf_db_cross`. Dos operaciones del nivel cerrado (edición enterprise) se declaran como herramientas: `ragraf_license_offline` (licencia sin conexión: solicitud, emisión, aceptación, transferencia) y `ragraf_attest_bundle` (atestación de la entrega: un paquete de pruebas firmado y su verificación). El bucle de calidad (edición pro) es `ragraf_quality_suite` (una batería de comprobaciones del índice más una ejecución registrada), `ragraf_quality_diff` (divergencias entre dos ejecuciones) y `ragraf_quality_prune` (aligerar el registro de ejecuciones: ensayo por defecto, borrado solo con confirmación y tras una copia del registro). Tres operaciones del índice compartido (también pro) quedan fuera del conjunto MCP: gestionan la publicación y el canal de red, no lecturas del índice, y se ejecutan como `python -m ragraf.shared_index` (véase `docs/03_implementation/SHARED-INDEX.md`). El bróker de secretos (pro) está representado por dos herramientas — `ragraf_broker_issue` (préstamo: plazo, custodio, motivo, canje único) y `ragraf_broker_rotate` (estado del plan, programación, rotación con revocación de préstamos); el estado de los préstamos vive en `catalog/broker.json`, y el esquema de almacenamiento no cambia.
La vigésima cuarta es `use_project`: elegir un corpus cuando un solo servidor atiende varios proyectos. `context` responde qué corpus está seleccionado y con qué fundamento — sin ella, las respuestas sobre un proyecto ajeno eran indistinguibles de las respuestas sobre el propio.

**Veintinueve lenguajes** se analizan en un árbol de sintaxis (nodos y aristas del grafo). Dieciséis tienen su mapa de nodos descrito por un perfil: PHP, Python, JavaScript, TypeScript, Java, C#, C, C++, Ruby, Lua, Go, Perl, bash, PowerShell, CSS y HTML. Los trece restantes (Rust, Kotlin, Swift, Scala, Dart, R, F#, OCaml, Solidity, Fortran, MATLAB, Elm, Lisp) lo toman de la consulta de etiquetas de la propia gramática: sus nombres de nodo no coinciden con los habituales, y un perfil por lenguaje significaría inventar nombres. Cada lenguaje se comprueba sobre un archivo de muestra — al menos un símbolo encontrado (`tests/test_new_languages.py`).

Cuarenta más se conectan como texto plano: la búsqueda encuentra los archivos, pero no producen nodos. Son Julia, Elixir, Erlang, Haskell, Clojure, Groovy, Visual Basic, Pascal/Delphi, Objective-C, Zig, Nim, Crystal, Tcl, Prolog, Ada, Verilog, VHDL, ensamblador, HCL/Terraform, Dockerfile, Makefile, CMake, SCSS, Less, Vue, Svelte, GraphQL, Protobuf, SQL, batch, Markdown, RST, YAML, JSON, TOML, INI, XML, texto plano y el lenguaje `document`.

**Los documentos se indexan completos, incluidas las versiones antiguas.** Word: `.docx`, `.docm`, `.dotx` y `.doc` (Word 97 mediante un analizador OLE propio, sin LibreOffice). Excel: `.xlsx`, `.xlsm`, `.xltx` y `.xls` (BIFF8). PowerPoint: `.pptx`, `.pptm`, `.ppsx`, `.potx` y `.ppt`. Además PDF, OpenDocument (`.odt`, `.ods`, `.odp`, `.odg`), EPUB, RTF, correo `.eml` y `.msg`, y archivos de WPS Office. Los formatos sin analizador (Pages, OneNote, CHM, DjVu, `.xlsb`) no fingen ser documentos vacíos: se informan con el motivo.

Lo que se indexa se elige en el panel: **Ajustes → la pestaña «Formatos»** — 84 formatos agrupados por tipo (lenguajes, documentos, datos), un filtro por nombre, extensión y modo de análisis, botones «todos»/«ninguno» y un contador de entradas habilitadas. Todos los formatos están habilitados por defecto, y `*` significa «todos, incluidos los que se añadan después». Deshabilitar un formato cambia el contenido del corpus y provoca una reindexación en la siguiente comprobación de actualidad.

---

## Cómo funciona

**Tres almacenes SQLite** por corpus, en un directorio `.ragraf/` **dentro del proyecto indexado**:

| Almacén | Contenido |
|---|---|
| `graph.sqlite` | nodos (clase, función, método), aristas (llamadas, herencia, importaciones), esquema de la base de datos |
| `lexvec.sqlite` | léxico FTS5 (BM25) y vectores en un solo archivo; texto crudo de los fragmentos para los extractos |
| `control.sqlite` | revisiones, registro de divergencias, telemetría de accesos |

**Flujo de indexación:** `scan` (recorrido y sumas de control) → `ast` (tree-sitter: símbolos, llamadas, SQL) → `load` → `resolve` (nombres) → `schema` (tablas de las migraciones) → `cpg` (llamadas interprocedurales mediante Joern) → `lexvec` (FTS5 + vectores).

**Tres decisiones que definen el comportamiento** — cada una derivada de un defecto medido:

1. **Un índice inmutable y un registro de divergencias en lugar de actualizar sobre la marcha.** Dentro de una revisión el índice no cambia; las ediciones del código se acumulan en el registro y los hechos afectados se marcan como `stale`. De otro modo, hechos de momentos distintos se mezclarían en una misma respuesta sin nada que los distinga — y así es exactamente como nacen las conclusiones erróneas.
2. **El agente no recibe un lenguaje de consultas al grafo.** Solo operaciones fijas. El fundamento: en LLMxCPG (USENIX Security '25) los modelos base producen 132/1278 y 19/1278 consultas CPGQL válidas frente a 1278/1278 de un modelo entrenado, así que un lenguaje libre se convierte en un generador de errores.
3. **Cada hecho lleva su prueba.** `path:line`, `provenance` (`stated` — demostrado, `derived` — heurística), `index_rev`, `confidence`. Una arista sin prueba parece fiable solo porque llegó «de la base de datos».

**Degradación en lugar de caída.** Sin la capa de grafo de propiedades se obtiene `capability=degraded` y una negativa clara de `slice` que nombra el modo real; las demás operaciones siguen funcionando. Sin modelo de embeddings funciona la búsqueda léxica. Sin puerto libre, el servidor indica el motivo. La degradación silenciosa no existe.

---

## Instalación

**Se requiere Python 3.13 o superior.** No hacen falta permisos de administrador.

### Windows (PowerShell 7.6+)

```powershell
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
pwsh -NoLogo -NoProfile -File scripts/install.ps1
```

El script crea un entorno virtual en `~/.ragraf/venv`, instala las dependencias y **comprueba que de verdad ha salido un entorno funcional** — paso a paso, con una tabla resumen. No finge haber instalado nada: cada paso termina con una verificación.

Variantes útiles:

```powershell
# instalar y preparar de inmediato un corpus para el propio proyecto
pwsh -File scripts/install.ps1 -Project C:\work\myproject

# lo mismo más la indexación completa (descarga el modelo de embeddings, ~240 MB)
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index

# indexación sin vectores: no se descarga el modelo, la búsqueda léxica sigue funcionando
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index -NoVectors

# detrás de una inspección TLS (proxy corporativo)
pwsh -File scripts/install.ps1 -WithTruststore
```

### Otras plataformas

Un camino que funciona allí donde exista Python 3.13+:

```bash
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -e .                # o: pip install -r requirements.txt
ragraf --version
```

El producto también funciona sin instalar el paquete, mediante un envoltorio que pone `src/` en la ruta por sí mismo:

```bash
python tools/ragraf.py --version
```

> `scripts/install.ps1` está verificado en Windows (PowerShell 7.6, Python 3.14.7). El camino de `pip` en Linux y macOS **no está verificado** — el producto se desarrolló y se midió en Windows.

### Capa externa: Java y Joern (opcional)

El grafo interprocedural (la operación `slice`) necesita JDK 21 y Joern. Sin ellos el producto sigue funcionando: el índice se construye, la búsqueda y la estructura están disponibles, y `slice` se niega indicando el motivo.

```powershell
pwsh -File scripts/install-joern.ps1
```

El archivo se guarda en `.tools/downloads/` — el script es idempotente. El conjunto descomprimido ocupa unos 5 GB.

---

## Primeros pasos

```bash
# 1. Preparar el corpus: encontrarlo, crearlo si falta, reconstruir el índice atrasado.
#    Para un directorio desconocido el corpus lo crea este mismo comando.
ragraf ensure C:\work\myproject

# 2. Comprobar el entorno (informe sobre 11 componentes).
#    ATENCIÓN: este comando SIEMPRE devuelve el código 0 — no es una puerta, lea el informe.
ragraf -p <config> selftest

# 3. Preguntar
ragraf -p <config> query search "latencia de reindexación"
ragraf -p <config> query symbol --name corpus_hash
ragraf -p <config> query code --name corpus_hash
ragraf -p <config> query callers --name dedupe
ragraf -p <config> query impact --target Facade --depth 2

# 4. Actualidad: ¿ha cambiado el código desde la última indexación?
ragraf -p <config> check
```

La lista completa de comandos es `ragraf --help`. Aviso: **las opciones globales `--project/-p` y `--json` van ANTES del subcomando** (`ragraf -p cfg query search ...`); las opciones propias del subcomando van después de él.

### Gestión del corpus

```bash
ragraf init C:\work\myproject         # crear la configuración del corpus e imprimir su ruta
ragraf -p <config> register           # registrar el proyecto en el registro
ragraf -p <config> index              # indexación completa
ragraf -p <config> reindex            # reindexar y archivar el registro de divergencias
ragraf -p <config> drift --scan       # registrar divergencias SIN tocar el índice
ragraf -p <config> status             # estado: revisión, cobertura, tamaño del registro
ragraf -p <config> usage              # telemetría: llamadas, hechos, estimación del efecto
```

### Conexión a un agente como servidor MCP

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

`--project` es obligatorio. `--agent` y `--agent-kind` solo hacen falta para la estadística de accesos por agente: sin ellos las llamadas quedan registradas como `unknown`.

### Panel de ajustes

```bash
python tools/ragraf-admin.py --no-browser
```

Se abre en `127.0.0.1:8777` y escucha solo en esa dirección. Muestra los corpus y el estado de sus índices, el tamaño de los almacenes, la calidad del índice y la estadística de accesos. Las peticiones que modifican algo requieren un token de acceso que se imprime al arrancar.

La página de fuentes — `http://127.0.0.1:8777/sources`: raíces permitidas para el modo «I am you», su estado, alta, escaneo, habilitación y borrado. Los cambios requieren el mismo token; el contenido de los archivos nunca se lee, y los archivos con secretos solo se ven como recuento y regla. Una raíz de red se acepta solo como ruta UNC o como ruta montada con `--network`; los conectores por URL (`ssh://`, `s3://`, `webdav://`) no están implementados y se rechazan con el motivo.

La página de valor — `http://127.0.0.1:8777/value`: llamadas de 30 días, hechos, estimaciones de tokens y de líneas, sumadas entre corpus. Las estimaciones están etiquetadas; no hay rutas ni consultas.

---

## Requisitos y límites

| Aspecto | Valor |
|---|---|
| Python | **3.13+** (declarado en `requires-python`; verificado en 3.14) |
| Plataforma | Windows + PowerShell 7.6 — verificado; otros sistemas — **no verificados** |
| Permisos | ninguno: todo se ejecuta desde el perfil del usuario |
| Disco | el índice de un corpus `.ragraf/` — alrededor de 1 MB por cada 1–2 archivos fuente; la capa Java/Joern — unos 5 GB |
| Red | se necesita dos veces: al instalar (PyPI) y en la **primera indexación con vectores** — el modelo de embeddings, de unos 240 MB, se descarga en `~/.ragraf/models` |
| Java + Joern | opcionales; sin ellos `capability=degraded` y `slice` no está disponible |
| Tarjeta gráfica | opcional; solo acelera la codificación de vectores y se activa al alcanzar el umbral de rentabilidad |

**Sobre la red y los certificados.** Detrás de una inspección TLS (proxy corporativo) la descarga del modelo falla en la comprobación del certificado. Instalar `truststore` lo resuelve:

```bash
pip install truststore      # o: install.ps1 -WithTruststore
```

En una red doméstica el paquete no hace falta. Medido: sin él — `certificate verify failed`; con él — 240 MB en 9,2 s.

**Sobre la caché del modelo.** El directorio de caché se redefine con `RAGRAF_MODEL_CACHE`. Las operaciones de lectura nunca descargan el modelo — solo lo hace la indexación con vectores.

---

## Documentación

| Documento | Contenido |
|---|---|
| `docs/02_architecture/ARCHITECTURE.md` | principios, componentes, esquema de los almacenes, contrato de operaciones |
| `docs/02_architecture/TZ.md` | requisitos funcionales y no funcionales, criterios de aceptación |
| `docs/03_implementation/INTEGRATION.md` | conexión a un agente: MCP, reglas de trabajo, política |
| `docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md` | instalación en HARNESS y AutoClaw, catálogo completo de capacidades |
| `docs/03_implementation/HARNESS_INTEGRATION.md` | DeepSeek Harness: MCP, habilidad, reglas, puerta |
| `docs/03_implementation/AUTOCLAW_INTEGRATION.md` | AutoClaw: MCP, habilidad, reglas, retroceso |
| `docs/03_implementation/CPG_INTEGRATION.md` | grafo de propiedades del código: Joern, JDK, modos de degradación |
| `docs/skills/ragraf-core/SKILL.md` | la habilidad para trabajar con el índice: elección de la operación, recuperación |
| `docs/05_article/RAGRAF_ARTICLE.md` | artículo de repaso: pila tecnológica, arquitectura, mediciones |
| `AGENTS.md` | las reglas de desarrollo de este proyecto; el verificador de convenciones de base |

---

## Desarrollo

```bash
# las pruebas se ejecutan como scripts sueltos — no hay un ejecutor único
python tests/test_lang_routing.py
python tests/test_watch.py

# verificador de convenciones del proyecto (14 comprobaciones)
pwsh -NoLogo -NoProfile -File tools/Invoke-ProjectDoctrine.ps1

# actualizar el índice de RAGRAF mismo
python tools/ragraf.py ensure .
```

La entrega incluye solo las comprobaciones que se ejecutan sobre **fixtures sintéticos** y viajan bien. Las demás miden el comportamiento en los corpus reales del autor (un portal en PHP, un servicio en C#, las configuraciones de sus proyectos) y no significan nada con otro código.

Qué pasa justo después de la instalación:

| Prueba | Comprobaciones |
|---|---|
| `test_lang_routing.py` | 56 |
| `test_watch.py` | 31 |
| `test_script_extract.py` | 25 |
| `test_threshold.py` | 23 |
| `test_ensure.py` | 17 |
| `test_csharp_records.py` | 12 |

Tres comprobaciones necesitan algo que la instalación base no tiene, y eso **no es un defecto de la entrega** sino la naturaleza de la comprobación:

* `test_joern_server.py` — hace falta la capa Java + Joern (`scripts/install-joern.ps1`);
* `test_graph_algo.py` — hace falta `torch` (unos 2,5 GB), que no está ni en `pyproject.toml` ni en `requirements.txt`: los algoritmos de grafo en la tarjeta gráfica son una rama opcional, y sin `torch` degrada al procesador con honestidad;
* `test_cpg_call_names.py` — hace falta una configuración de corpus (el directorio `config/` no se publica); se puede ejecutar copiando `examples/ragraf.example.yaml` en `config/` y apuntándolo a su propio directorio raíz.

`selftest` **no es una puerta**: imprime un informe sobre 11 componentes y siempre termina con el código 0. No hay otro código previsto para él, así que un `selftest` en verde no demuestra nada por sí mismo.

---

## Contribuir

Informes útiles: un defecto reproducible con su comando y su salida; un documento que diverge del código; una degradación que el producto no nombró. Una cosa se agradece por encima de todo: un hecho con archivo y línea.

El proyecto se construye para tareas concretas, por eso las propuestas de arquitectura encuentran oído antes que los parches grandes sin una conversación previa.

---

## El proyecto en cifras y el banco de pruebas medido

Cifras publicadas (comprobadas el 26.10.2026; tamaños del índice según la entrada `rev-0397` de 27.10.2026): **54 operaciones** en la fachada, **61 herramientas MCP**, **21** subcomandos de CLI, **29** lenguajes analizados por árbol y **40** conectados como texto (incluido `document`), **84** formatos de datos en el registro (61 lenguajes, 9 familias de documentos, 10 tipos de datos, 4 sin analizador); el índice propio de RAGRAF — 861 archivos, 5 165 nodos y 67 914 aristas (el retrato se compara con la entrada de revisión activa mediante `Test-DocumentedNumbers`: las cifras de una revisión pasada producen un aviso que nombra su `rev_id` y su fecha, y en la publicación eso es una negativa — `tools/release/gate_release.py`; las cifras vivas salen de `python tools/ragraf.py -p config/ragraf.yaml status`).

**El banco de pruebas, anonimizado** (nombres de proyecto, rutas y dominios eliminados). Las cifras de esta sección son un **retrato del 16.10.2026**, no el estado actual: en ese momento el registro tenía **28 corpus, 25 indexados** — 10 con la capa CPG, 15 en modo `degraded`. Para 16 de ellos la entrada de revisión se leyó el mismo día: **7 527 archivos, 94 528 símbolos, 791 871 aristas, 86 790 fragmentos**.

El tamaño actual del registro se mantiene aparte del retrato a propósito: cambia con cada proyecto conectado, y ponerlo en la tabla de abajo sería presentar la medición de ayer como la de hoy. Se nombra en su propia línea y se compara con `catalog/catalog.json` — **según el registro el 27.10.2026: 42 corpus, 33 indexados**. Una comprobación vigila esta cifra: si diverge del registro, es una observación, no una línea atrasada.

| Corpus | Tipo | Archivos | Símbolos | Aristas | Modo |
|---|---|---|---|---|---|
| C-02 | aplicación web en PHP | 2 722 | 32 761 | 355 192 | degraded |
| C-04 | servicio / escritorio en C# | 1 274 | 20 331 | 98 605 | degraded |
| C-05 | aplicación web en PHP | 928 | 17 381 | 99 029 | full |
| C-18 | conjunto de automatización en PowerShell | 81 | 324 | 4 214 | degraded |
| C-24 | Markdown + bash (el más pequeño) | 18 | 184 | 1 515 | degraded |

La tabla completa de 16 filas, las reglas de anonimización y la advertencia sobre el recuento están en [la wiki → Benchmarks and Limits](https://github.com/paulmann/RAGRAF/wiki/11-Benchmarks-and-Limits). La advertencia: el registro de corpus y una entrada de revisión dan totales distintos para la misma revisión (8 721 frente a 4 498 símbolos, 41 201 frente a 56 106 aristas), así que las cifras se toman de la entrada de revisión. El mapa privado de las etiquetas `C-*` a proyectos queda fuera del repositorio: pertenece al autor y no forma parte de la entrega.

---

## Licencia

**[Business Source License 1.1](LICENSE)** — el código está abierto, y la licencia permite cualquier uso, incluidos la actividad comercial y el uso en producción dentro de una organización. Una cosa está prohibida: ofrecer el producto o sus derivados a terceros como servicio alojado o gestionado cuya propuesta de valor principal sea la funcionalidad de RAGRAF. Cuatro años después de la publicación de una versión, esta pasa a Apache-2.0.

Fuentes de verdad — una pregunta por archivo, sin paráfrasis:

| Pregunta | Archivo |
|---|---|
| El texto de la licencia y sus parámetros | [`LICENSE`](LICENSE) |
| Terceros y sus licencias | [`NOTICE`](NOTICE) |
| Condiciones de contribución | [`CLA.md`](CLA.md) |
| Soporte: qué se promete y qué no | [`SUPPORT.md`](SUPPORT.md) |
| Qué está permitido, qué no, historia de la licencia | [`docs/03_implementation/LICENSING.md`](docs/03_implementation/LICENSING.md) |

> BSL 1.1 **no es una licencia de código abierto**: la OSI no la aprueba, así que lo correcto es decir «código disponible», «el código está abierto». La insignia de licencia de GitHub muestra «Other»: BSL no está en el catálogo que reconoce.

Un caso que la concesión no cubre necesita una licencia comercial aparte del titular de los derechos: todos los derechos no concedidos expresamente siguen siendo suyos. Los derechos ya concedidos no se revocan — la historia de la licencia está en [`docs/03_implementation/LICENSING.md`](docs/03_implementation/LICENSING.md).

Las dependencias externas conservan sus propias licencias: la lista con las versiones medidas está en [`NOTICE`](NOTICE). Java y Joern no forman parte de la entrega y se instalan aparte.

### Almacén de secretos (decisión del propietario, 24.10.2026)

La regla era «los valores no existen y no pueden existir» — solo un registro de lugares. El propietario la cambió: los valores hacen falta, pero pueden vivir **solo** en `catalog/` (fuera del corpus), **solo** cifrados, y se entregan a un agente por derecho y con registro de auditoría. La regla de «no indexar» se mantiene: `catalog/**` está excluido del corpus, así que un valor no llega ni al índice ni a la búsqueda.

- tipos: contraseña, token, clave de API, clave privada, certificado, cadena de conexión,
  clave SSH, webhook, nota — es decir, no «solo contraseñas»;
- cifrado: PyNaCl `SecretBox` (XSalsa20-Poly1305), la clave de cada entrada se deriva de la
  clave maestra usando el identificador de la entrada como sal, y el contexto de la entrada
  va dentro del texto cifrado — intercambiar filas entre entradas lo rechaza la comprobación
  de autenticidad;
- derechos por agente: `all` (lo predeterminado — disponible para todos), `listed` (solo los
  agentes nombrados), `owner_only` (solo el agente que lo guardó); además una lista global de
  agentes permitidos en los ajustes;
- auditoría: cada entrega y cada negativa, con un registro de solo adición a nivel de base de datos;
- ajustes: la sección `secrets_*` del panel — habilitar el almacén, capturar los secretos que
  se encuentren, visibilidad predeterminada, exigir motivo para la entrega, límite de longitud
  del valor, lista de agentes permitidos, aviso de caducidad.

CLI: `python tools/ragraf.py secrets list | show | reveal | set | rm | grant |
revoke | check | rotate | export | import | policy | audit`. El valor de `set` entra
por la entrada estándar, por una variable de entorno o por un archivo — nunca como
argumento de la línea de comandos. MCP: `ragraf_secret_list/show/reveal/set/grant/revoke/delete/policy`.
