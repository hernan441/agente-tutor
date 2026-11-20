# Agente tutor

Seguimiento personalizado de cada alumno, entre clase y clase, **para cualquier materia**.
El docente da la clase teórica y fija los parámetros de evaluación; el sistema recibe las
entregas, las evalúa contra la rúbrica del docente, lleva el legajo de cada alumno, redacta
una devolución socrática y le informa al docente qué retomar en la próxima clase.

Proyecto Integrador Final — Diplomatura en IA para la Enseñanza y la Innovación Educativa (Universidad Austral).

## Principios

1. **El docente decide.** Todo lo que se evalúa sale de `unidades/<unidad>/unidad.md`, que escribe el docente. Si no está ahí, no se evalúa.
2. **La IA solo en el medio.** Leer formularios, PDF, fotos y audios, llevar la base, detectar quién no entregó, armar legajos, recordatorios y mails: **todo eso lo hacen scripts**. La IA (un modelo chico y barato) se usa únicamente para evaluar contra la rúbrica y redactar la devolución.
3. **Nunca da la respuesta.** La devolución sigue la escalera de la retroalimentación (aclarar → valorar → preguntar → sugerir) y un control automático marca cualquier devolución que copie la respuesta esperada.
4. **Nada sale sin el docente.** El sistema no envía mails: deja borradores que el docente revisa y manda.
5. **Datos mínimos.** Inscripción con nombre, apellido, mail y curso. Los datos de alumnos nunca van al repositorio.
6. **Seguro por diseño.** Solo cuentas del colegio, límites contra la inundación, tope de gasto y defensa en capas contra prompt injection. Ver [SECURITY.md](SECURITY.md).

## Cómo funciona

```
Google Forms (Inscripción + Entregas) ─┐
                                       ├─ sync (rclone, solo lectura) ──▶ SQLite
Drive (archivos que suben los alumnos) ┘                                   │
                                                                           ▼
                adjuntos: PDF/docx → texto · fotos → OCR · audios → Whisper local   (sin IA)
                                                                           ▼
                lote: un paquete compacto por entrega (consigna, rúbrica, material de clase, entrega, antecedentes)
                                                                           ▼
                router: plantilla (sin IA) · Haiku (lo normal) · Sonnet (lo difícil)
                agente tutor: evalúa por criterio + redacta la devolución → JSON
                                                                           ▼
                aplicar: valida (niveles, criterios, fuga de respuesta) y guarda       (sin IA)
                pendientes: quién no entregó → recordatorio con plantilla               (sin IA)
                legajos + informe al docente + .eml / borradores IMAP                   (sin IA)
```

Solo cuando una foto no tiene texto legible (un dibujo, letra manuscrita) se le pasa esa imagen reducida a la IA.

## Las capas de una IA propia

| # | Capa | Cómo está resuelta |
|---|---|---|
| 1 | **Prompt** | `.claude/agents/tutor.md`: rol, escalera de la retroalimentación, reglas, ejemplo resuelto y salida JSON. Cada llamada registra la versión (hash) del prompt. |
| 2 | **RAG** | `tutor/rag.py`: BM25 en Python puro sobre el material de la clase. A cada entrega le llegan los 3 fragmentos que vienen al caso, y la devolución manda al alumno a esa sección. |
| 3 | **Herramientas** | Mínimas a propósito: las herramientas son los scripts. La IA solo puede *leer* las imágenes que tiene que mirar. |
| 4 | **Accesos** | Solo cuentas del colegio (dominio y nómina). IA en modo restringido, sin conectores, confinada a una carpeta. Datos con permisos de solo dueño. |
| 5 | **Guardrails** | Detector de prompt injection, texto oculto en PDF, canario, validación de toda salida, control de fuga de la respuesta, [REVISAR], docente en el circuito. Ver [SECURITY.md](SECURITY.md). |
| 6 | **Caché** | Adjuntos por hash; evaluaciones reutilizadas si el mismo texto vuelve a llegar; la parte fija del prompt va primero y una sola vez por tanda. |
| 7 | **Router** | `tutor/router.py`: entregas mínimas → plantilla (0 tokens); lo normal → modelo rápido (Haiku); imágenes, sospechas o salidas inválidas → modelo fuerte (Sonnet). Modelos fijados por versión. |
| 8 | **Observabilidad** | Tabla `llamadas` (modelo, latencia, tokens, costo, reintentos, validaciones fallidas, versión del prompt) y `./tutor.sh metricas`. Calidad real: cuánto edita el docente cada borrador antes de enviarlo (`./tutor.sh calidad`). |
| 9 | **Evaluación contra el docente** | `evals/`: el docente corrige casos a mano y `./tutor.sh evaluar-agente` mide la coincidencia del agente, criterio por criterio. Es la única forma honesta de decir que el agente es "preciso". |
| + | **Retención de datos** | `./tutor.sh purgar --ciclo 2026 [--anonimizar]` al cerrar el ciclo. |

## Teorías de la diplomatura que aplica

| Marco | Dónde aparece |
|---|---|
| Enseñanza para la Comprensión (tópico generativo, metas, desempeños, valoración continua) | estructura de `unidad.md`; cada entrega es un desempeño valorado |
| Rúbricas de 4 niveles | `unidad.md` §5; el agente asigna nivel + evidencia textual por criterio |
| Escalera de la retroalimentación / feedback formativo | formato fijo de cada devolución |
| Discernimiento frente a la IA | criterio transversal C3 + señal de copia acrítica para el docente |
| Metacognición | autoevaluación obligatoria en cada entrega (criterio C4) |
| DUA | el alumno responde en texto, foto, PDF o audio |

## Uso

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
./tutor.sh configurar                     # primera vez: tu mail del colegio, dominio, IMAP
./tutor.sh unidades                       # unidades cargadas
./tutor.sh sync                           # trae inscripciones y entregas
./tutor.sh procesar BIO6-ALIM             # circuito completo → informe + .eml
./tutor.sh procesar BIO6-ALIM --subir     # además sube los borradores a la casilla (no envía)
./tutor.sh estado                         # inscriptos, rechazos, cuarentena
./tutor.sh liberar 12                     # saca la entrega #12 de cuarentena
./tutor.sh metricas                       # costo, latencia, router, calidad
./tutor.sh evaluar-agente                 # coincidencia con los niveles del docente (evals/casos.csv)
./tutor.sh purgar --ciclo 2026 --anonimizar --confirmar   # retención de datos
```

`configurar` guarda tus datos en `config.toml` y `.env`, que están en `.gitignore`: tu mail nunca
llega al repositorio (antes de escribir lo verifica, y un test falla si aparece un mail real en un archivo versionado).

Requiere el CLI de Claude Code (`claude`); el prompt del agente está en `.claude/agents/tutor.md`. Para audios,
un python con `faster-whisper` (`audio_python` en `config.toml`).

### Probarlo sin Google (piloto)

```bash
./tutor.sh --piloto --hoy 2026-11-20 procesar BIO6-ALIM
```
Usa `piloto/`: 8 alumnos ficticios inscriptos y 2 inscripciones rechazadas (dominio ajeno y alias),
14 entregas (una sin inscripción y un reenvío duplicado, que quedan en cuarentena; un intento de
manipular la nota por texto y otro con texto oculto en un PDF, que quedan en [REVISAR]; una entrega
mínima que se resuelve por plantilla, sin IA), un PDF, una foto de etiqueta, un esquema dibujado y
un audio. Las salidas quedan en `datos_piloto/`. En `piloto/` hay ejemplos ya generados:
`ejemplo_informe.md`, `ejemplo_borradores.md` y `ejemplo_metricas.md`.

### Tests

```bash
venv/bin/pytest -q            # TUTOR_WHISPER_PY=/ruta/python para incluir el test de audio
```

## Cargar una unidad nueva

Copiar `plantillas/unidad.md` a `unidades/<materia>-<unidad>/unidad.md`, completar tópico, metas,
actividades (consigna, qué se espera ver, ideas erróneas frecuentes, criterios) y rúbrica, y dejar
el material de la clase en `clase.md`. Agregar el código de unidad al desplegable del formulario.

## Formularios

Ver `formulario.md`: Inscripción (nombre, apellido, mail del colegio y curso) y Entregas.

## Estructura

```
tutor/
  cli.py            comandos (./tutor.sh …)
  configurar.py     asistente de primera vez (mail, dominio, IMAP → config.toml)
  config.py         configuración (config.toml)
  db.py             base SQLite
  sync.py           formularios → base (rclone, solo lectura)
  seguridad.py      cuentas del colegio, anti-inundación, detector de prompt injection
  adjuntos.py       PDF, docx, fotos (OCR) y audios (Whisper), sin IA
  rag.py            búsqueda en el material de la clase (BM25)
  lote.py           arma el paquete de cada entrega
  router.py         plantilla / modelo rápido / modelo fuerte
  motor.py          caché → router → IA → validación
  ia.py             única puerta a la IA (modo restringido)
  aplicar.py        valida y guarda lo que devuelve la IA
  pendientes.py     quién no entregó + recordatorios
  informe.py        legajos e informe al docente
  correo.py         .eml y borradores IMAP (nunca envía)
  observabilidad.py métricas de uso, costo y calidad
  evals.py          evaluación del agente contra el docente
  unidades.py       lee unidad.md
.claude/agents/     el prompt del agente tutor
unidades/           una carpeta por unidad: unidad.md + clase.md
plantillas/         plantilla de unidad y formato de devolución
evals/              casos para que el docente corrija + resultados
piloto/             datos ficticios y ejemplos de salida
tests/              73 tests (sin IA)
SECURITY.md         amenazas y defensas
```
