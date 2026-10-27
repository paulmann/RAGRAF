# RAGRAF

<!-- md-i18n:switcher -->
**En otros idiomas:** [Русский](README.md) · [Deutsch](README.de.md) · [中文](README.zh.md) · [हिन्दी](README.hi.md) · Español · [Русский (copia)](README.ru.md)

**Base de conocimiento grafo-vectorial sobre el código para agentes LLM.**

[**Sitio web**](https://ragraf.ru) · [**Wiki**](https://github.com/paulmann/RAGRAF/wiki) · [**Documentación (Pages)**](https://paulmann.github.io/RAGRAF/) · [**Versiones**](https://github.com/paulmann/RAGRAF/releases) · [**Debates**](https://github.com/paulmann/RAGRAF/discussions)

![RAGRAF — base de conocimiento grafo-vectorial sobre el código para agentes LLM: 61 herramientas MCP, hechos verificables con referencia archivo:línea, totalmente local](assets/social-preview.png)

> **Esta traducción está incompleta y se detiene en la edición 0.3.x.** La lista de secciones y las cifras están contrastadas con el producto actual; el contenido no: faltan los niveles de licencia Community / Pro / Enterprise, el bróker de secretos, el bucle de calidad y el índice compartido, los `connectors` y `fleet` retirados, el análisis de documentos de Office y PDF, y las operaciones `route` y `neighbors`. El texto completo y vigente está en el documento principal [`README.md`](README.md) (en ruso); qué capacidades pertenecen a cada nivel de licencia consta en [`EULA.md`](EULA.md) y en `src/ragraf/edition_baseline.json`. El estado de esta traducción queda registrado en [`docs/03_implementation/RELEASE-SOURCES.md`](docs/03_implementation/RELEASE-SOURCES.md).

Un asistente que responde a las preguntas del agente sobre la base de código — *dónde está esto, quién lo llama, qué se romperá al modificarlo, cómo se hizo algo parecido cerca* — con hechos verificables (`archivo:línea`) en lugar de un `rg` a ciegas y de la lectura de archivos completos. Python 3.13+, SQLite (grafo + FTS5 + vectores en un solo archivo), tree-sitter, embeddings locales sin torch. **Todo funciona en la máquina donde está el código**: no hace falta levantar ningún servicio y el índice no va a la nube: es un índice RAG local sobre su repositorio. La capa opcional de grafo de propiedades del código (Java + Joern) abre los cortes; sin ella el producto funciona y lo dice con honestidad.

---

## Contenido

- [Qué es y para qué sirve](#qué-es-y-para-qué-sirve)
- [Casos de uso](#casos-de-uso)
- [Capacidades](#capacidades)
- [El proyecto en cifras](#el-proyecto-en-cifras)
- [Cómo está construido](#cómo-está-construido)
- [Instalación](#instalación)
- [Primeros pasos](#primeros-pasos)
- [Requisitos y limitaciones](#requisitos-y-limitaciones)
- [Documentación](#documentación)
- [Desarrollo](#desarrollo)
- [Contribuir](#contribuir)
- [Licencia](#licencia)

---

## Qué es y para qué sirve

Un agente que trabaja en una modificación de código consume la mayor parte de su contexto en **localizar el lugar**: dónde se define una función, quién la llama, cómo se hizo algo parecido cerca. El camino habitual es `rg` más la lectura de archivos completos.

RAGRAF sustituye ese camino por un índice. El agente formula la pregunta con una operación fija y recibe un hecho con referencia `archivo:línea`, no diez candidatos para revisar.

Medido en un proyecto real (portal en PHP, 14 tareas de navegación; la ejecución se hizo en la máquina del autor — véase «qué hay exactamente en los artefactos» más abajo):

| Indicador | Sin índice | Con índice |
|---|---|---|
| Llamadas a herramientas en 14 tareas | 28 (realista: 43) | **14** |
| Promedio por tarea | 2,0 / 3,1 | **1,0** |
| Candidatos para revisar | hasta 34 archivos | 1–5 exactos |
| Archivos leídos completos | 14 (mínimo) | **0** |
| Acierto en el archivo objetivo al primer intento | — | **92%** (12 de 13) |
| Verificabilidad (hechos con referencia `archivo:línea`) | — | **100%** |

Las cifras de llamadas, aciertos y verificabilidad son **mediciones** de aquella ejecución; sus artefactos en bruto no forman parte del árbol publicado (punto `R-REL-1` en [`ROADMAP.md`](ROADMAP.md)). En cambio, en la distribución sí hay un artefacto de la métrica de grafo: [`bench/results/M2_rev-0012.json`](bench/results/M2_rev-0012.json) y [`M2_rev-0025.json`](bench/results/M2_rev-0025.json) miden la **exhaustividad del análisis de impacto** en el mismo portal: si el grafo llega hasta los archivos que realmente cambiaron juntos. Recall medio **0,771 → 0,917** sobre 20 y 8 commits con profundidad 3, recall mediano **1,0**, precisión 0,072 → 0,020 (baja a propósito: la métrica mide conectividad, no predicción) frente a una línea base ingenua de **0,0**.

«Realista: 43» es una corrección de modelo (el agente revisa hasta 5 candidatos en lugar de uno), y está anotada en el propio artefacto.

Una advertencia importante que suele omitirse: **la ventaja de 2 a 3 veces se refiere a las tareas de localización** («dónde se define», «quién llama»). En el ciclo de modificación «leí → cambié → comprobé» casi no hay ahorro en el número de llamadas; ahí la ventaja es otra: las modificaciones no rompen el índice y el SQL dudoso se comprueba antes de ejecutarlo.

---

## Casos de uso

| Qué está haciendo | Qué preguntar a RAGRAF | Qué llega en la respuesta |
|---|---|---|
| Introduce al agente en un repositorio desconocido de 200 mil líneas | `repo_map` y después `search` / `symbol` | un hecho por pregunta, con referencia `archivo:línea`, en lugar de una pared de candidatos |
| Refactoriza una función a la que se llama desde una decena de lugares | `callers`, `impact`, `slice` con CPG disponible | relaciones demostradas por separado de las heurísticas; un corte que dice qué cubre exactamente |
| Renombra o elimina una columna de la base de datos | `write_check` **antes** de ejecutar el SQL y luego `table_usage` | columnas inexistentes con los nombres más cercanos; quién lee y quién escribe la tabla |
| Revisa un diff ajeno | `diff`, `drift_report`, `live_read` | símbolos y aristas modificados, incluidas las regresiones, y el archivo actual con las divergencias marcadas |
| Busca copias (`Files.php` … `Files7.php`) | `duplicates` | qué archivos son copias y cuál de ellas es **demostradamente** la activa |
| «¿Cómo está hecho esto en otras partes del código?» | `search` (BM25 + vectores, de forma híbrida) | fragmentos del corpus ordenados; para las respuestas en sí no hace falta el modelo de embeddings |
| Conecta el índice a un agente | **61 herramientas MCP** `ragraf_*` o la CLI | las mismas operaciones por stdio — véase [`docs/03_implementation/INTEGRATION.md`](docs/03_implementation/INTEGRATION.md) |

---

## Capacidades

**54 operaciones** sobre el repositorio: un conjunto fijo, sin lenguaje de consultas al grafo:

| Operación | Responde a la pregunta |
|---|---|
| `search` | dónde se habla de esto (de forma híbrida: léxico BM25 + vector) |
| `search_multi` | dónde aparecen varias cadenas a la vez; copias de archivos y cuál de ellas está activa |
| `duplicates` | qué archivos son copia unos de otros y con qué se demuestra la actividad del principal |
| `entrypoints` | puntos de entrada: rutas, manejadores HTTP, CLI, endpoints del marcado |
| `dead_code` | archivos y subrutinas sin referencias y fuera del alcance |
| `symbol` | dónde se define un símbolo: tipo, archivo, líneas, firma |
| `references` | dónde más se usa |
| `callers` / `callees` | quién llama / a quién llama |
| `impact` | qué afectará el cambio (relaciones demostradas y heurísticas por separado) |
| `slice` | corte estructural: nodos, aristas, líneas, sin los cuerpos de las funciones |
| `code` | cuerpos de los símbolos (solo con petición explícita) |
| `repo_map` | mapa del repositorio con un presupuesto de caracteres dado |
| `schema` / `table_usage` | qué hay en la tabla de la base de datos y quién la lee o la escribe |
| `routes` | rutas y puntos de entrada |
| `policy_trace` | quién obtiene el permiso y con qué condición |
| `class_usage` | clase de estilo: dónde se define, dónde se usa, qué no coincide |
| `write_check` | si se puede escribir así (cotejo del plan de escritura con el esquema antes de ejecutar) |
| `live_read` | archivo actual con las diferencias respecto al índice resaltadas |
| `freshness` | si el índice está actualizado (cotejo de la suma de control) |
| `index_status` | revisión, cobertura, degradación, tamaño del registro |
| `drift_report` / `drift_ack` | registro de divergencias entre el índice y el código |
| `catalog` | registro de corpus: qué está indexado y con qué sumas |
| `calibrate` | calibración de umbrales |
| `usage` | telemetría de llamadas y estimación del efecto |
| `diff` | qué cambió entre revisiones: archivos, símbolos, aristas, regresiones (P2-1) |
| `task_stats` | informe por tarea: llamadas al índice, decisiones de la puerta (§3.4) |
| `gate` | decisión de la puerta ante una llamada de herramienta: `allow` / `ask` / `deny` (§3.2) |
| `note` / `notes` | escribir y leer una nota sobre una decisión: conocimiento entre sesiones (P2-3) |

Otros dos modos no son operaciones, sino una forma de respuesta: **`answer=true`** entrega una respuesta redactada con referencias en lugar del resultado estructurado (P2-2), y `brief=true`, una envoltura breve de la respuesta. `usage` muestra las métricas del efecto (`saved_calls`, `hit_rate`, `avg_ms`, `misses`, `stale_served`).

Las mismas operaciones están disponibles como **61 herramientas MCP** (`ragraf_*`), para conectarlas a un agente como servidor, y mediante la CLI. Hay un grupo aparte de herramientas para la capa viva de bases de datos: `ragraf_db_schema`, `ragraf_db_table`, `ragraf_db_relations`, `ragraf_db_sample`, `ragraf_db_drift`, `ragraf_db_cross`.
La vigésima cuarta es `use_project`: la elección de corpus cuando un solo servidor atiende varios proyectos. `context` responde qué corpus está elegido y con qué fundamento: sin ella, las respuestas sobre un proyecto ajeno eran indistinguibles de las respuestas sobre el propio.

La tabla recoge solo las operaciones del nivel Community. Los niveles de pago no se describen aquí: es la parte que esta traducción no cubre — `quality_suite`, `quality_diff`, `quality_prune`, `shared_index_status`, `shared_index_publish`, `shared_index_query`, `broker_issue`, `broker_rotate`, `sso_login`, `sso_map_roles`, `siem_export`, `siem_test`, `license_offline`, `attest_bundle`. Su composición está en `src/ragraf/edition_baseline.json` y las condiciones en [`EULA.md`](EULA.md).

**Veintinueve lenguas** se analizan mediante árbol de sintaxis (nodos y aristas del grafo), otras cuarenta se conectan como texto — los archivos se encuentran por búsqueda pero no producen nodos; en el registro de formatos hay **84** formatos. La lista completa de lenguas y formatos está en el documento principal [`README.md`](README.md).

---

## El proyecto en cifras

Las cifras no se copian en esta traducción: cambian de una etapa a otra y la traducción se quedaría atrás. Los valores comprobados están en el documento principal [`README.md`](README.md), en la sección «El proyecto en cifras y el banco de pruebas medido»; los vigila `Test-DocumentedNumbers` en [`tools/Invoke-ProjectDoctrine.ps1`](tools/Invoke-ProjectDoctrine.ps1).

Contrastado con el registro de revisión activo `rev-0385` (27.10.2026): **54** operaciones de fachada, **61** herramientas MCP, **50** operaciones `query` con **21** subcomandos de CLI, **29** lenguas por árbol de sintaxis y **40** como texto, **84** formatos en el registro; el índice propio de RAGRAF —860 archivos, 5 158 nodos, 67 721 aristas.

El banco de pruebas, el volumen de la entrega y las corridas de pruebas no se repiten aquí a propósito: cambian, y una cifra copiada sería más pronto falsa que correcta.

---

## Cómo está construido

**Tres almacenes SQLite** por corpus, en el directorio `.ragraf/` **dentro del proyecto indexado**:

| Almacén | Qué contiene |
|---|---|
| `graph.sqlite` | nodos (clase, función, método), aristas (llamadas, herencia, importaciones), esquema de la base de datos |
| `lexvec.sqlite` | léxico FTS5 (BM25) y vectores en un solo archivo; texto sin procesar de los fragmentos para entregar extractos |
| `control.sqlite` | revisiones, registro de divergencias, telemetría de llamadas |

**Flujo de indexación:** `scan` (recorrido y sumas de control) → `ast` (tree-sitter: símbolos, llamadas, SQL) → `load` → `resolve` (nombres) → `schema` (tablas de las migraciones) → `cpg` (llamadas interprocedurales mediante Joern) → `lexvec` (FTS5 + vectores).

**Tres decisiones que determinan el comportamiento**, cada una derivada de un defecto medido:

1. **Índice inmutable y registro de diferencias en lugar de «actualización sobre la marcha».** Dentro de una revisión el índice no cambia; las modificaciones del código se acumulan en el registro y los hechos afectados se marcan como `stale`. De otro modo, hechos de momentos distintos se mezclarían en una misma respuesta sin distinción, y así es exactamente como nacen las conclusiones erróneas.
2. **No se le da al agente un lenguaje de consultas al grafo.** Solo operaciones fijas. El fundamento: en LLMxCPG (USENIX Security '25) los modelos base producen 132/1278 y 19/1278 consultas CPGQL válidas frente a 1278/1278 de un modelo entrenado, es decir, el lenguaje libre se convierte en un generador de errores.
3. **Cada hecho lleva su prueba.** `path:line`, `provenance` (`stated` — demostrado, `derived` — heurística), `index_rev`, `confidence`. Una arista sin prueba parece fiable solo porque llegó «de la base de datos».

**Degradación en lugar de caída.** La ausencia del grafo de propiedades del código produce `capability=degraded` y un rechazo comprensible de la operación `slice` que indica el modo real; las demás operaciones funcionan. Si no hay modelo de embeddings, funciona la búsqueda léxica. Si no hay puerto libre, el servidor informa la causa. La degradación silenciosa no existe.

---

## Instalación

Se requiere **Python 3.13 o superior**. No hacen falta permisos de administrador.

### Instalación rápida con un solo comando

El instalador descarga el paquete desde GitHub y lo despliega en un diálogo:

```powershell
iwr -OutFile install.ps1 https://raw.githubusercontent.com/paulmann/RAGRAF/main/install.ps1
pwsh -NoLogo -NoProfile -File .\install.ps1
```

Preguntará el directorio de instalación (por defecto `C:\RAGRAF`), creará un entorno virtual e instalará las dependencias mediante `scripts/install.ps1`, ofrecerá buscar AutoClaw y DeepSeek Harness y configurar la conexión, y al final imprimirá las instrucciones para iniciar el panel de configuración y una advertencia sobre el acceso a él.

El instalador dice con claridad lo que no hace: **los valores para AutoClaw los muestra pero no los escribe**, porque AutoClaw solo acepta servidores MCP a través de su propia interfaz y su almacén pertenece al producto. Para **Harness** el instalador sí sabe escribir el bloque del perfil: con copia de seguridad y verificación posterior del análisis. Una instalación detectada no se sobrescribe en silencio, y el ensayo sin dependencias está disponible con la clave `-SkipDeps`.

### Windows (PowerShell 7.6+)

```powershell
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
pwsh -NoLogo -NoProfile -File scripts/install.ps1
```

El script creará un entorno virtual en `~/.ragraf/venv`, instalará las dependencias y **comprobará que el entorno resultante es funcional**, paso a paso y con una tabla final. No «finge haber instalado»: cada paso termina con una comprobación.

Variantes útiles:

```powershell
# instalar y preparar de inmediato un corpus para el propio proyecto
pwsh -File scripts/install.ps1 -Project C:\work\myproject

# lo mismo más la indexación completa (descarga el modelo de embeddings, ~240 MB)
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index

# indexación sin vectores: no se descarga el modelo, la búsqueda léxica funciona
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index -NoVectors

# en una red con inspección TLS (proxy corporativo)
pwsh -File scripts/install.ps1 -WithTruststore
```

### Otras plataformas

Un camino común, funciona en cualquier lugar donde haya Python 3.13+:

```bash
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -e .                # o: pip install -r requirements.txt
ragraf --version
```

El producto también funciona sin instalar el paquete, mediante un envoltorio que añade `src/` a la ruta por sí mismo:

```bash
python tools/ragraf.py --version
```

> `scripts/install.ps1` está probado en Windows (PowerShell 7.6, Python 3.14.7). El camino común mediante `pip` en Linux y macOS **no se ha probado**: el producto se desarrolló y se midió en Windows.

### Capa externa: Java y Joern (opcional)

Para el grafo interprocedural (operación `slice`) se necesitan JDK 21 y Joern. Sin ellos el producto funciona: el índice se construye, la búsqueda y la estructura están disponibles y `slice` rechaza la petición indicando el motivo.

```powershell
pwsh -File scripts/install-joern.ps1
```

El archivo se guarda en `.tools/downloads/`; el script es idempotente. El conjunto descomprimido ocupa alrededor de 5 GB.

---

## Primeros pasos

```bash
# 1. Preparar el corpus: encontrarlo, crearlo si falta, reconstruir el índice atrasado.
#    El corpus de un directorio desconocido lo crea este mismo comando.
ragraf ensure C:\work\myproject

# 2. Comprobar el entorno (informe sobre 11 componentes).
#    ATENCIÓN: el comando SIEMPRE devuelve el código 0 — no es una puerta, lea el informe.
ragraf -p <config> selftest

# 3. Preguntar
ragraf -p <config> query search "retraso de reindexación"
ragraf -p <config> query symbol --name corpus_hash
ragraf -p <config> query code --name corpus_hash
ragraf -p <config> query callers --name dedupe
ragraf -p <config> query impact --target Facade --depth 2

# 4. Actualidad: ¿cambió el código después de la indexación?
ragraf -p <config> check
```

La lista completa de comandos está en `ragraf --help`. Importante: **las claves globales `--project/-p` y `--json` se colocan ANTES del subcomando** (`ragraf -p cfg query search ...`), y las claves propias del subcomando, después de él.

### Gestión del corpus

```bash
ragraf init C:\work\myproject         # crear la configuración del corpus y mostrar su ruta
ragraf -p <config> register           # registrar el proyecto en el registro
ragraf -p <config> index              # indexación completa
ragraf -p <config> reindex            # reindexación + archivo del registro de divergencias
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

`--project` es obligatorio. `--agent` y `--agent-kind` solo hacen falta para la estadística separada de llamadas: sin ellos las llamadas quedan en el registro como `unknown`.

### Panel de configuración

```bash
python tools/ragraf-admin.py --no-browser
```

Se abre en `127.0.0.1:8777` y escucha solo en esa dirección. Muestra los corpus y el estado de sus índices, el tamaño de los almacenes, la calidad del índice y la estadística de llamadas. Las peticiones que modifican algo requieren un token de acceso que se imprime al iniciar.

---

## Requisitos y limitaciones

| Aspecto | Valor |
|---|---|
| Python | **3.13+** (declarado en `requires-python`; probado en 3.14) |
| Plataforma | Windows + PowerShell 7.6: probado; otros sistemas operativos: **no probados** |
| Permisos | no se requieren: todo funciona desde el directorio del usuario |
| Disco | índice del corpus `.ragraf/`: aproximadamente 1 MB por cada 1–2 archivos de código fuente; capa externa Java/Joern: ~5 GB |
| Internet | se necesita dos veces: en la instalación (PyPI) y en la **primera indexación con vectores**, cuando se descarga el modelo de embeddings (~240 MB) en `~/.ragraf/models` |
| Java + Joern | opcionales; sin ellos `capability=degraded` y `slice` no está disponible |
| Tarjeta gráfica | opcional; solo acelera la codificación de vectores y se activa según un umbral de rentabilidad |

**Sobre Internet y los certificados.** En una red con inspección TLS (proxy corporativo) la descarga del modelo falla en la verificación del certificado. Se resuelve instalando `truststore`:

```bash
pip install truststore      # o: install.ps1 -WithTruststore
```

En una red doméstica el paquete no hace falta. Medido: sin él, el rechazo `certificate verify failed`; con él, los 240 MB se descargan en 9,2 s.

**Sobre la caché del modelo.** El directorio de caché se redefine con la variable `RAGRAF_MODEL_CACHE`. Las operaciones de lectura nunca descargan el modelo; solo lo hace la indexación con vectores.

---

## Documentación

| Documento | Qué contiene |
|---|---|
| [ragraf.ru](https://ragraf.ru) | sitio público: capacidades, benchmarks medidos, emulador del panel de administración |
| [Wiki](https://github.com/paulmann/RAGRAF/wiki) | 15 páginas: primeros pasos, referencia de operaciones, CLI, MCP, capa CPG, diagnóstico, FAQ |
| [Documentación (Pages)](https://paulmann.github.io/RAGRAF/) | este conjunto de documentación con búsqueda: se compila y se publica con el flujo `pages.yml` |
| [`docs/02_architecture/ARCHITECTURE.md`](docs/02_architecture/ARCHITECTURE.md) | principios, componentes, esquema de los almacenes, contrato de operaciones |
| [`docs/02_architecture/TZ.md`](docs/02_architecture/TZ.md) | requisitos funcionales y no funcionales, criterios de aceptación |
| [`docs/03_implementation/INTEGRATION.md`](docs/03_implementation/INTEGRATION.md) | conexión a un agente: MCP, reglas de trabajo, reglamento |
| [`docs/03_implementation/CPG_INTEGRATION.md`](docs/03_implementation/CPG_INTEGRATION.md) | grafo de propiedades del código: Joern, JDK, modos de degradación |
| [`docs/skills/ragraf-core/SKILL.md`](docs/skills/ragraf-core/SKILL.md) | habilidad de trabajo con el índice: elección de la operación, recuperación |
| [`docs/05_article/RAGRAF_ARTICLE.md`](docs/05_article/RAGRAF_ARTICLE.md) | artículo de repaso: pila tecnológica, arquitectura, mediciones |
| [`docs/03_implementation/RELEASE-SOURCES.md`](docs/03_implementation/RELEASE-SOURCES.md) | origen de cada archivo de la entrega y estado de cada traducción |
| [`ROADMAP.md`](ROADMAP.md) | mejoras previstas con identificadores estables (`R-*`) |

---

## Desarrollo

```bash
# las pruebas se ejecutan como scripts sueltos — no hay un runner común
python tests/test_lang_routing.py
python tests/test_watch.py

# verificador de convenciones del proyecto (14 comprobaciones)
pwsh -NoLogo -NoProfile -File tools/Invoke-ProjectDoctrine.ps1

# actualizar el índice de RAGRAF mismo
python tools/ragraf.py ensure .
```

En la distribución solo se incluyen las comprobaciones que funcionan con **fixtures sintéticos** y son portables. Las demás miden el comportamiento en los corpus reales del autor (portal en PHP, servicio en C#, configuraciones de sus proyectos) y no tienen sentido con otro código.

Qué pasa inmediatamente después de la instalación:

| Prueba | Comprobaciones |
|---|---|
| `test_lang_routing.py` | 56 |
| `test_watch.py` | 31 |
| `test_script_extract.py` | 25 |
| `test_threshold.py` | 23 |
| `test_ensure.py` | 17 |
| `test_csharp_records.py` | 12 |

Tres comprobaciones requieren algo que no está en la instalación básica, y eso **no es un defecto de la distribución**, sino la naturaleza de la comprobación:

* `test_joern_server.py`: hace falta la capa externa Java + Joern (`scripts/install-joern.ps1`);
* `test_graph_algo.py`: hace falta `torch` (unos 2,5 GB), que no está ni en `pyproject.toml`
  ni en `requirements.txt`; los algoritmos de grafo en la tarjeta gráfica son una rama
  opcional, y sin `torch` degradan con honestidad al procesador;
* `test_cpg_call_names.py`: hace falta la configuración del corpus (el directorio `config/` no se publica);
  la prueba se puede ejecutar copiando `examples/ragraf.example.yaml` en `config/` e
  indicando ahí su propio directorio raíz; sin configuración se niega a ejecutarse, en lugar de «pasar» en silencio.

El décimo archivo, `test_capability_consistency.py`, responde «no aplicable» mientras no haya configuración del corpus:
comprueba que el `capability` declarado corresponda al contenido del grafo. `test_graph_algo.py`
sin `torch` ejecuta igualmente **7 comprobaciones** y nombra **17 propiedades que no pudo comprobar**,
en lugar de una línea verde sobre código no comprobado.

El verificador del proyecto es igual de honesto con este subconjunto publicado: de **14 comprobaciones** aquí pasan
11, y 3 informan de archivos que faltan a propósito en el árbol público (la configuración del corpus,
`tests/test_dedupe.py`, `tests/test_text_from_index.py`); es el punto `R-REL-1` de
[`ROADMAP.md`](ROADMAP.md).

`selftest` **no es una puerta**: imprime un informe sobre 11 componentes y siempre
devuelve el código 0. No tiene previsto ningún código distinto de cero, así que un «selftest en verde»
por sí mismo no demuestra nada.

---

## Contribuir

Mensajes útiles: un defecto reproducible con su comando y su salida; una divergencia entre el documento y el código; un caso de degradación que el producto no anunció. Se agradece una sola cosa: un hecho con referencia al archivo y a la línea. Véase [CONTRIBUTING.md](CONTRIBUTING.md); las preguntas, las ideas y las mediciones sobre su propio corpus van a los [Debates](https://github.com/paulmann/RAGRAF/discussions). La participación se rige por el [Código de conducta](CODE_OF_CONDUCT.md).

El proyecto se desarrolla para tareas concretas, por eso las propuestas de arquitectura se discuten con más gusto que los parches grandes sin una conversación previa. Los problemas de seguridad se comunican de forma privada, no con un issue público: véase [SECURITY.md](SECURITY.md).

Si esto le ha ahorrado una ventana de contexto, una estrella ayuda a que el siguiente agente encuentre el proyecto.

---

## Licencia

**[Business Source License 1.1](LICENSE)** (BUSL-1.1, Community). Las condiciones están en el archivo [`LICENSE`](LICENSE); la expresión legible por máquina es `license = "BUSL-1.1"` en `pyproject.toml`. La historia de los cambios de licencia (MIT → PolyForm Noncommercial → BUSL-1.1) está registrada en [CHANGELOG.md](CHANGELOG.md).

**Niveles de licencia.** Community, Pro y Enterprise son niveles de **una** entrega cerrada, no tres conjuntos separados: el nivel decide qué queda abierto en la misma colección de archivos, y el paso entre niveles es una activación de licencia, no una instalación. Las condiciones están en [`EULA.md`](EULA.md) y la asignación de capacidades a los niveles en `src/ragraf/edition_baseline.json`; `connectors` y `fleet` no forman parte del objeto en ningún nivel y están nombrados en el plan de desarrollo.

> No se puede confiar en la insignia de licencia de GitHub: no toda expresión se reconoce, y BUSL-1.1 puede mostrarse como «Other». Las fuentes de verdad son el archivo `LICENSE` y el campo `license` de `pyproject.toml`.

Las dependencias externas conservan sus licencias: `tree-sitter` (MIT), `sqlite-vec` (MIT/Apache-2.0), `fastembed` (Apache-2.0), `PyYAML` (MIT), `tiktoken` (MIT), `ijson` (BSD). Java y Joern, que se instalan por separado, se distribuyen bajo Apache-2.0 y no forman parte de la distribución.
