# Formularios (Google Forms, en la cuenta de la cátedra)

Se crean **una sola vez** y sirven para todas las materias y unidades. Las respuestas van a una Hoja
de cálculo cada uno; los scripts las leen con rclone en modo solo lectura.

## 1. Inscripción (al inicio del ciclo)
Configuración: recopilar correos electrónicos **verificados**; una respuesta por persona.
Hoja de respuestas: "Inscripción (respuestas)".

| Pregunta | Tipo | Obligatoria |
|---|---|---|
| (correo, lo toma Forms) | automático | sí |
| Nombre | Respuesta corta | sí |
| Apellido | Respuesta corta | sí |
| Curso | Desplegable (ej.: 6A, 6B) | sí |

## 2. Entregas
Configuración: recopilar correos verificados; permitir carga de archivos.
Hoja de respuestas: "Entregas (respuestas)". Carpeta de archivos: la que crea Forms.

| Pregunta | Tipo | Obligatoria |
|---|---|---|
| (correo, lo toma Forms) | automático | sí |
| Nombre y apellido | Respuesta corta | sí |
| Unidad | Desplegable (códigos de unidad, ej.: BIO6-ALIM) | sí |
| Actividad | Desplegable (A1, A2, …) | sí |
| Tu respuesta | Párrafo | sí |
| (opcional) Subí un archivo o audio | Carga de archivos (PDF, imagen, audio; hasta 5) | no |
| ¿Usaste IA? ¿Cuál, qué le pediste y qué corregiste vos? | Párrafo | sí |
| ¿Qué entendiste bien y qué te falta? | Párrafo | sí |

Los títulos pueden variar un poco: `tutor/sync.py` reconoce los encabezados por cómo empiezan.
Una entrega de un mail que no está inscripto se procesa igual y queda marcada en el informe.
