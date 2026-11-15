# Evaluación del agente contra el docente (capa 9)

La "precisión" de un agente no sale de apilar capas: sale de **medirlo contra el criterio del docente**.

1. Abrí `casos.csv` (Excel o Google Sheets). Cada fila es una entrega ficticia.
2. Completá a mano las columnas `docente_C1` … `docente_C4` con el nivel que vos le pondrías:
   `Inicial`, `En desarrollo`, `Logrado` o `Destacado`. En A1 solo van C1 y C4 (dejá C2 y C3 vacías).
3. Corré `./tutor.sh evaluar-agente` (o `--modelo claude-sonnet-5-5` para comparar modelos).
4. El resultado queda en `resultados/` con la coincidencia exacta, a un nivel de distancia,
   si el agente tiende a sobrevalorar o subvalorar, la matriz de confusión y el detalle por caso.

Cada vez que se cambia el prompt (`.claude/agents/tutor.md`) o el modelo, se vuelve a correr
para ver si mejoró o empeoró. El nombre del archivo de resultado incluye el modelo y la versión del prompt.
