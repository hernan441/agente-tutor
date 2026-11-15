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
                lote: un paquete compacto por entrega (consigna, rúbrica, entrega, antecedentes)
                                                                           ▼
                agente tutor (Haiku): evalúa por criterio + redacta la devolución → JSON
                                                                           ▼
                aplicar: valida (niveles, criterios, fuga de respuesta) y guarda       (sin IA)
                pendientes: quién no entregó → recordatorio con plantilla               (sin IA)
                legajos + informe al docente + .eml / borradores IMAP                   (sin IA)
```

Solo cuando una foto no tiene texto legible (un dibujo, letra manuscrita) se le pasa esa imagen reducida a la IA.

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
python3 -m venv venv && venv/bin/pip install pymupdf pillow pillow-heif rapidocr-onnxruntime pytest
cp config.example.toml config.toml        # completar
./tutor.sh unidades                       # unidades cargadas
./tutor.sh sync                           # trae inscripciones y entregas
./tutor.sh procesar BIO6-ALIM             # circuito completo → informe + .eml
./tutor.sh procesar BIO6-ALIM --subir     # además sube los borradores a la casilla (no envía)
./tutor.sh estado                         # inscriptos, rechazos, cuarentena
./tutor.sh liberar 12                     # saca la entrega #12 de cuarentena
```

Requiere el CLI de Claude Code (`claude`) con el agente `.claude/agents/tutor.md`. Para audios,
un python con `faster-whisper` (`audio_python` en `config.toml`).

### Probarlo sin Google (piloto)

```bash
./tutor.sh --piloto --hoy 2026-11-20 procesar BIO6-ALIM
```
Usa `piloto/`: 8 alumnos ficticios inscriptos y 2 inscripciones rechazadas (dominio ajeno y alias),
13 entregas (una sin inscripción y un reenvío duplicado, que quedan en cuarentena; un intento de
manipular la nota por texto y otro con texto oculto en un PDF, que quedan en [REVISAR]),
un PDF, una foto de etiqueta, un esquema dibujado y un audio. Las salidas quedan en `datos_piloto/`.

### Tests

```bash
venv/bin/pytest -q            # TUTOR_WHISPER_PY=/ruta/python para incluir el test de audio
```

## Cargar una unidad nueva

Copiar `plantillas/unidad.md` a `unidades/<materia>-<unidad>/unidad.md`, completar tópico, metas,
actividades (consigna, qué se espera ver, ideas erróneas frecuentes, criterios) y rúbrica, y dejar
el material de la clase en `clase.md`. Agregar el código de unidad al desplegable del formulario.

## Formularios

Ver `formulario.md` (Entregas) e Inscripción: nombre, apellido, mail verificado y curso.

## Estructura

```
tutor/            código (db, sync, adjuntos, lote, ia, aplicar, pendientes, informe, correo, cli)
.claude/agents/   el agente tutor (único punto con IA)
unidades/         una carpeta por unidad: unidad.md + clase.md
plantillas/       plantilla de unidad y formatos
piloto/           datos ficticios para probar
tests/
```
