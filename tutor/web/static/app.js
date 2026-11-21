// Panel del agente tutor. Los datos de alumnos viven solo en memoria: en el navegador se guarda únicamente el token.
const APP_NUM = 1;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let TOKEN = "";
let RESUMEN = null;
let historial = [];

function guardarToken(t) { try { localStorage.setItem("tutor_token", t); } catch (e) {} }
function leerToken() { try { return localStorage.getItem("tutor_token") || ""; } catch (e) { return ""; } }
function borrarToken() { try { localStorage.removeItem("tutor_token"); } catch (e) {} }

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fecha = (s) => { if (!s) return ""; const [d, h] = String(s).split(/[ T]/); const [a, m, di] = d.split("-"); return `${di}/${m}/${a}${h ? " " + h.slice(0, 5) : ""}`; };

async function api(ruta, opciones = {}) {
  let r;
  try {
    r = await fetch(ruta, { ...opciones, headers: { "X-Token": TOKEN, "Content-Type": "application/json", ...(opciones.headers || {}) } });
  } catch (e) {
    $("#sin-conexion").hidden = false;
    throw new Error("sin conexión");
  }
  $("#sin-conexion").hidden = true;
  if (r.status === 401) { mostrarLogin("El token no es válido."); throw new Error("token inválido"); }
  const tipo = r.headers.get("Content-Type") || "";
  if (!tipo.includes("json")) { if (!r.ok) throw new Error(`error ${r.status}`); return r; }
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || `error ${r.status}`);
  return d;
}
const post = (ruta, cuerpo) => api(ruta, { method: "POST", body: JSON.stringify(cuerpo) });

// ---------- md mínimo (informe y legajo, generados por el propio sistema) ----------
function md(texto) {
  const lineas = String(texto || "").split("\n");
  let html = "", enLista = false, tabla = [];
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/_(.+?)_/g, "<i>$1</i>").replace(/`(.+?)`/g, "<code>$1</code>");
  const cerrarTabla = () => {
    if (!tabla.length) return;
    const filas = tabla.filter((f) => !/^\|\s*-/.test(f)).map((f) => f.replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
    html += "<table><thead><tr>" + filas[0].map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>"
      + filas.slice(1).map((f) => "<tr>" + f.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table>";
    tabla = [];
  };
  for (const l of lineas) {
    if (l.startsWith("|")) { tabla.push(l); continue; } else cerrarTabla();
    if (/^- /.test(l)) { if (!enLista) { html += "<ul>"; enLista = true; } html += `<li>${inline(l.slice(2))}</li>`; continue; }
    if (enLista) { html += "</ul>"; enLista = false; }
    const h = l.match(/^(#{1,3}) (.*)/);
    if (h) html += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`;
    else if (l.trim()) html += `<p>${inline(l)}</p>`;
  }
  cerrarTabla(); if (enLista) html += "</ul>";
  return html;
}

// ---------- navegación ----------
function irA(vista) {
  $$("nav button").forEach((b) => b.classList.toggle("activo", b.dataset.vista === vista));
  $$("main > section").forEach((s) => (s.hidden = s.dataset.vista !== vista));
  return ({ inicio: cargarInicio, alumnos: cargarAlumnos, entregas: cargarEntregas, borradores: cargarBorradores,
     revisar: cargarRevisar, informe: cargarInforme, ajustes: cargarAjustes, chat: () => $("#msg").focus() })[vista]?.();
}

function llenarUnidades(sel, conTodas = false) {
  const actual = sel.value;
  sel.innerHTML = (conTodas ? '<option value="">Todas</option>' : "") + (RESUMEN?.unidades || []).map((u) => `<option value="${esc(u.codigo)}">${esc(u.codigo)} — ${esc(u.titulo)}</option>`).join("");
  if (actual) sel.value = actual;
}
const unidad = (cod) => (RESUMEN?.unidades || []).find((u) => u.codigo === cod);

// ---------- inicio ----------
async function cargarInicio() {
  RESUMEN = await api("/api/resumen");
  $("#piloto-chip").hidden = !RESUMEN.piloto;
  $("#cuenta").textContent = RESUMEN.piloto ? "Modo piloto (datos ficticios)" : RESUMEN.cuenta || "";
  const ins = Object.entries(RESUMEN.inscriptos);
  const total = ins.reduce((a, [, n]) => a + n, 0);
  const dato = (n, txt, alerta = false) => `<div class="dato${alerta && n ? " alerta" : ""}"><b>${esc(n)}</b><span>${esc(txt)}</span></div>`;
  $("#tarjetas").innerHTML = [
    dato(total, "inscriptos" + (ins.length ? " · " + ins.map(([c, n]) => `${c}: ${n}`).join(" · ") : "")),
    dato(RESUMEN.entregas, "entregas recibidas"),
    dato(RESUMEN.sin_procesar, "sin procesar", true),
    dato(RESUMEN.revisar, "para revisar [REVISAR]", true),
    dato(RESUMEN.cuarentena, "en cuarentena", true),
    dato(RESUMEN.mails_a_revisar, "mails a revisar", true),
    dato(RESUMEN.borradores_sin_subir, "borradores sin subir"),
    dato("US$ " + Number(RESUMEN.gasto_total).toFixed(2), "gasto total de IA"),
  ].join("");
  $("#b-revisar").textContent = RESUMEN.mails_a_revisar || "";
  $("#b-borradores").textContent = RESUMEN.borradores_sin_subir || "";
  for (const s of ["#a-unidad-proc", "#a-unidad", "#f-unidad", "#i-unidad"]) llenarUnidades($(s), s === "#f-unidad");
  llenarActividades();
  $("#iteraciones").innerHTML = "<h3>Notas e iteraciones</h3>" + RESUMEN.unidades.map((u) => `
    <p><b>${esc(u.codigo)}</b> · Aprobaron: ${u.iteraciones.aprobados.map(esc).join("; ") || "nadie todavía"}</p>
    <p class="tenue">Siguen corrigiendo: ${u.iteraciones.iterando.map(esc).join("; ") || "nadie"}</p>`).join("")
    + (RESUMEN.ultima_corrida ? `<p class="tenue">Última corrida: ${fecha(RESUMEN.ultima_corrida.inicio)} · ${RESUMEN.ultima_corrida.entregas} entregas · US$ ${Number(RESUMEN.ultima_corrida.costo_usd || 0).toFixed(4)}</p>` : "");
}
function llenarActividades() {
  const u = unidad($("#a-unidad").value);
  $("#a-actividad").innerHTML = (u?.actividades || []).map((a) => `<option value="${esc(a.codigo)}">${esc(a.codigo)} — ${esc(a.nombre)}</option>`).join("");
  if (u?.cursos?.length && !$("#a-curso").value) $("#a-curso").value = u.cursos[0];
}

async function accion(cuerpo) {
  if ($("#a-subir").checked && ["procesar", "asignar", "inscripcion"].includes(cuerpo.accion)) cuerpo.subir = true;
  try {
    const r = await post("/api/accion", cuerpo);
    irA("inicio");
    seguir(r.id, r.desc);
  } catch (e) { alert(e.message); }
}
async function seguir(id, desc) {
  const est = $("#trabajo-estado");
  $("#log").textContent = `▶ ${desc}\n`;
  $$("[data-accion], #btn-procesar, #btn-asignar, #btn-subir").forEach((b) => (b.disabled = true));
  est.className = "estado corriendo"; est.textContent = "corriendo";
  try {
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      let t;
      try { t = await api("/api/trabajo?id=" + id); } catch (e) { if (e.message === "sin conexión") continue; throw e; }
      $("#log").textContent = `▶ ${desc}\n` + t.log;
      $("#log").scrollTop = 1e9;
      if (t.estado !== "corriendo") { est.className = "estado " + t.estado; est.textContent = t.estado === "ok" ? "listo" : "error"; break; }
    }
  } finally {
    $$("[data-accion], #btn-procesar, #btn-asignar, #btn-subir").forEach((b) => (b.disabled = false));
    cargarInicio().catch(() => {});
  }
}

// ---------- alumnos ----------
let ALUMNOS = [];
async function cargarAlumnos() {
  ALUMNOS = await api("/api/alumnos");
  pintarAlumnos();
}
function pintarAlumnos() {
  const f = $("#f-alumno").value.toLowerCase();
  const lista = ALUMNOS.filter((a) => !f || `${a.apellido} ${a.nombre} ${a.mail} ${a.curso}`.toLowerCase().includes(f));
  $("#t-alumnos").innerHTML = "<thead><tr><th>Curso</th><th>Apellido y nombre</th><th>Mail</th><th>Entregas</th></tr></thead><tbody>"
    + lista.map((a) => `<tr class="clic" data-mail="${esc(a.mail)}"><td>${esc(a.curso)}</td><td>${esc(a.apellido)}, ${esc(a.nombre)}</td><td class="tenue">${esc(a.mail)}</td><td class="num">${a.entregas}</td></tr>`).join("")
    + (lista.length ? "" : '<tr><td colspan="4" class="tenue">Todavía no hay inscriptos. Leé la casilla desde Inicio.</td></tr>') + "</tbody>";
}
async function verLegajo(mail, fila) {
  $$("#t-alumnos tr").forEach((t) => t.classList.remove("sel")); fila?.classList.add("sel");
  const r = await api("/api/legajo?mail=" + encodeURIComponent(mail));
  $("#legajo").innerHTML = md(r.texto);
}

// ---------- entregas ----------
let ENTREGAS = [];
async function cargarEntregas() {
  if (!RESUMEN) await cargarInicio();
  ENTREGAS = await api("/api/entregas?unidad=" + encodeURIComponent($("#f-unidad").value));
  const acts = [...new Set(ENTREGAS.map((e) => e.actividad))].sort();
  const sa = $("#f-act"), prev = sa.value;
  sa.innerHTML = '<option value="">Todas</option>' + acts.map((a) => `<option>${esc(a)}</option>`).join(""); sa.value = prev;
  pintarEntregas();
}
function pintarEntregas() {
  const act = $("#f-act").value, est = $("#f-estado").value, ap = RESUMEN?.nota_aprobacion ?? 7;
  const lista = ENTREGAS.filter((e) => (!act || e.actividad === act) && (!est
    || (est === "revisar" && e.revisar) || (est === "cuarentena" && e.estado === "cuarentena")
    || (est === "pendiente" && !e.procesada && e.estado === "ok") || (est === "aprobada" && e.nota >= ap)
    || (est === "iterando" && e.nota != null && e.nota < ap)));
  const crit = [...new Set(lista.flatMap((e) => Object.keys(e.niveles)))].sort();
  $("#t-entregas").innerHTML = `<thead><tr><th>Fecha</th><th>Alumno</th><th>Act.</th><th>Versión</th>${crit.map((c) => `<th>${esc(c)}</th>`).join("")}<th>Nota</th><th>Estado</th></tr></thead><tbody>`
    + lista.map((e) => {
      const nota = e.nota == null ? "" : `<span class="nota ${e.nota >= ap ? "ap" : "it"}">${e.nota}</span>`;
      let estado = e.estado === "cuarentena" ? `<span class="tag cua" title="${esc(e.motivo_cuarentena)}">cuarentena</span> <button class="sec" data-liberar="${e.id}">Liberar</button>`
        : e.revisar ? `<span class="tag rev" title="${esc(e.revisar_motivo)}">[REVISAR]</span>`
        : !e.procesada ? '<span class="tag">sin procesar</span>' : `<span class="tag">${esc(e.camino || "evaluada")}</span>`;
      if (e.senal_ia) estado += ' <span class="tag rev" title="señal de copia acrítica de IA">IA</span>';
      return `<tr><td>${fecha(e.marca_temporal)}</td><td>${esc(e.nombre)}</td><td>${esc(e.actividad)}</td><td class="num">${e.version || ""}</td>`
        + crit.map((c) => `<td>${esc(e.niveles[c] || "—")}</td>`).join("") + `<td>${nota}</td><td>${estado}</td></tr>`;
    }).join("") + (lista.length ? "" : '<tr><td colspan="8" class="tenue">Sin entregas con ese filtro.</td></tr>') + "</tbody>";
}

// ---------- borradores ----------
let BORRADORES = [];
async function cargarBorradores() {
  BORRADORES = await api("/api/devoluciones");
  $("#t-borradores").innerHTML = "<thead><tr><th>Alumno</th><th>Asunto</th><th>Estado</th></tr></thead><tbody>"
    + BORRADORES.map((d) => `<tr class="clic" data-dev="${d.id}"><td>${esc(d.alumno)}<div class="tenue">${esc(d.actividad)} · ${esc(d.tipo)}</div></td><td>${esc(d.asunto)}</td>
      <td>${d.borrador_subido ? '<span class="tag">subido</span>' : d.asunto.startsWith("[REVISAR]") ? '<span class="tag rev">revisar</span>' : '<span class="tag">sin subir</span>'}</td></tr>`).join("")
    + (BORRADORES.length ? "" : '<tr><td colspan="3" class="tenue">No hay borradores. Procesá una unidad desde Inicio.</td></tr>') + "</tbody>";
}
function editarBorrador(id, fila) {
  const d = BORRADORES.find((x) => x.id === id);
  $$("#t-borradores tr").forEach((t) => t.classList.remove("sel")); fila?.classList.add("sel");
  $("#editor").innerHTML = `<div class="tenue">Para: ${esc(d.mail)} · ${esc(d.unidad)} ${esc(d.actividad)}</div>
    <input id="e-asunto" value="${esc(d.asunto)}" aria-label="Asunto">
    <textarea id="e-cuerpo" aria-label="Cuerpo">${esc(d.cuerpo)}</textarea>
    <div class="fila"><button class="primario" id="e-guardar">Guardar cambios</button>
    <span class="tenue">Si el asunto empieza con [REVISAR], sacalo cuando lo hayas revisado. Para subirlos: "Subir borradores" en Inicio.</span></div>
    <p id="e-msg"></p>`;
  $("#e-guardar").onclick = async () => {
    try {
      await post("/api/devolucion", { id, asunto: $("#e-asunto").value, cuerpo: $("#e-cuerpo").value });
      $("#e-msg").textContent = "Guardado. Queda pendiente de subir.";
      await cargarBorradores();
    } catch (e) { $("#e-msg").textContent = e.message; }
  };
}

// ---------- mails a revisar ----------
async function cargarRevisar() {
  const lista = await api("/api/mails_revisar");
  $("#l-revisar").innerHTML = lista.map((m) => `<div class="tarjeta revisar-item" data-clave="${esc(m.clave)}">
      <div><b>${esc(m.mail)}</b> · ${fecha(m.fecha)} · «${esc(m.asunto)}»</div>
      <div class="tenue">${esc(m.motivo)}</div>
      ${m.motivo.startsWith("inscripción") ? `<form data-inscribir="${esc(m.mail)}"><input name="nombre" placeholder="Nombre" required><input name="apellido" placeholder="Apellido" required><input name="curso" placeholder="Curso (6A)" required size="6"><button class="primario">Inscribir</button></form>` : ""}
      <div><button class="sec" data-resolver="${esc(m.clave)}">Ya lo vi, sacar de la lista</button></div>
    </div>`).join("") || '<div class="tarjeta tenue">No hay mails para revisar.</div>';
}

// ---------- informe ----------
async function cargarInforme() {
  if (!RESUMEN) await cargarInicio();
  const r = await api("/api/informe?unidad=" + encodeURIComponent($("#i-unidad").value));
  $("#informe-txt").innerHTML = r.texto ? md(r.texto) : '<p class="tenue">Todavía no hay informe de esta unidad. Se genera al procesar.</p>';
}
async function bajarPlanilla() {
  const u = $("#i-unidad").value;
  const r = await api("/api/planilla?unidad=" + encodeURIComponent(u));
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: `calificaciones_${u}.xlsx` });
  a.click(); setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ---------- chat ----------
function burbuja(rol, texto, propuesta) {
  const b = document.createElement("div");
  b.className = "burbuja " + rol;
  b.textContent = texto;
  if (propuesta) {
    const btn = document.createElement("button");
    btn.className = "primario propuesta";
    btn.textContent = "▶ " + propuesta.desc;
    btn.onclick = () => { btn.disabled = true; accion(propuesta.accion); };
    b.appendChild(document.createElement("br")); b.appendChild(btn);
  }
  $("#mensajes").appendChild(b);
  $("#mensajes").scrollTop = 1e9;
  return b;
}
async function enviarChat(ev) {
  ev.preventDefault();
  const msg = $("#msg").value.trim();
  if (!msg) return;
  $("#msg").value = "";
  burbuja("docente", msg);
  const espera = burbuja("asistente", "Pensando…");
  try {
    const { id } = await post("/api/chat", { mensaje: msg, historial });
    historial.push({ rol: "docente", texto: msg });
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      let t;
      try { t = await api("/api/trabajo?id=" + id); } catch (e) { if (e.message === "sin conexión") continue; throw e; }
      if (t.estado === "corriendo") continue;
      espera.remove();
      if (t.estado === "ok") { burbuja("asistente", t.resultado.respuesta, t.resultado.propuesta); historial.push({ rol: "asistente", texto: t.resultado.respuesta }); }
      else burbuja("asistente", "No pude responder: " + (t.log || "error"));
      break;
    }
  } catch (e) { espera.textContent = "No pude responder: " + e.message; }
  historial = historial.slice(-10);
}

// ---------- ajustes ----------
async function cargarAjustes() {
  const a = await api("/api/ajustes");
  $("#ajustes-datos").innerHTML = `<h3>Configuración</h3><table><tbody>
    <tr><th>Cuenta</th><td>${esc(a.cuenta || "(sin configurar: ./tutor.sh configurar)")}</td></tr>
    <tr><th>Contraseña de aplicación</th><td>${a.clave_cargada ? '<span class="tag">cargada</span>' : '<span class="tag rev">falta</span>'}</td></tr>
    <tr><th>Servidor IMAP</th><td>${esc(a.imap_host)}</td></tr>
    <tr><th>Lee mails desde</th><td>${esc(a.desde ? fecha(a.desde) : "el principio")}</td></tr>
    <tr><th>Dominios de alumnos</th><td>${esc(a.dominios_permitidos.join(", "))}</td></tr>
    <tr><th>Aprobación</th><td>${a.nota_aprobacion} · hasta ${a.max_iteraciones} versiones</td></tr>
    <tr><th>Modelos</th><td>${esc(a.modelo_rapido)} / ${esc(a.modelo_fuerte)}</td></tr>
    <tr><th>Versión del panel</th><td>v${APP_NUM}</td></tr></tbody></table>
    <p class="tenue">Para cambiar la configuración: <code>./tutor.sh configurar</code> en el server.</p>`;
}

// ---------- arranque ----------
function mostrarLogin(error = "") {
  $("#app").hidden = true; $("#login").hidden = false;
  $("#login-error").textContent = error; $("#token").focus();
}
async function entrar() {
  try {
    await cargarInicio();
    $("#login").hidden = true; $("#app").hidden = false;
  } catch (e) { if (e.message !== "token inválido") mostrarLogin(e.message); }
}

document.addEventListener("DOMContentLoaded", () => {
  $("#app-num").textContent = "v" + APP_NUM;
  $("#form-login").onsubmit = (e) => { e.preventDefault(); TOKEN = $("#token").value.trim(); guardarToken(TOKEN); entrar(); };
  $$("nav button").forEach((b) => (b.onclick = () => Promise.resolve(irA(b.dataset.vista)).catch((e) => alert(e.message))));
  $$("[data-accion]").forEach((b) => (b.onclick = () => accion({ accion: b.dataset.accion })));
  $("#btn-procesar").onclick = () => accion({ accion: "procesar", unidad: $("#a-unidad-proc").value });
  $("#btn-subir").onclick = () => accion({ accion: "subir", unidad: $("#a-unidad-proc").value });
  $("#btn-asignar").onclick = () => accion({ accion: "asignar", unidad: $("#a-unidad").value, actividad: $("#a-actividad").value, curso: $("#a-curso").value.trim() });
  $("#a-unidad").onchange = llenarActividades;
  $("#refrescar").onclick = () => cargarInicio();
  $("#reintentar").onclick = () => cargarInicio().catch(() => {});
  $("#f-alumno").oninput = pintarAlumnos;
  $("#t-alumnos").onclick = (e) => { const f = e.target.closest("tr[data-mail]"); if (f) verLegajo(f.dataset.mail, f); };
  $("#f-unidad").onchange = cargarEntregas; $("#f-act").onchange = pintarEntregas; $("#f-estado").onchange = pintarEntregas;
  $("#t-entregas").onclick = (e) => { const b = e.target.closest("[data-liberar]"); if (b) accion({ accion: "liberar", id: Number(b.dataset.liberar) }); };
  $("#t-borradores").onclick = (e) => { const f = e.target.closest("tr[data-dev]"); if (f) editarBorrador(Number(f.dataset.dev), f); };
  $("#l-revisar").onclick = async (e) => {
    const b = e.target.closest("[data-resolver]");
    if (b) { await post("/api/mails_revisar/resolver", { clave: b.dataset.resolver }); cargarRevisar(); cargarInicio(); }
  };
  $("#l-revisar").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target, item = f.closest("[data-clave]");
    try {
      await post("/api/inscribir", { mail: f.dataset.inscribir, nombre: f.nombre.value, apellido: f.apellido.value, curso: f.curso.value, clave: item.dataset.clave });
      cargarRevisar(); cargarInicio();
    } catch (err) { alert(err.message); }
  };
  $("#i-unidad").onchange = cargarInforme;
  $("#btn-planilla").onclick = () => bajarPlanilla().catch((e) => alert(e.message));
  $("#form-chat").onsubmit = enviarChat;
  $("#form-clave").onsubmit = async (e) => {
    e.preventDefault();
    try { await post("/api/clave_imap", { clave: $("#clave").value }); $("#clave").value = ""; $("#clave-msg").textContent = "Guardada."; cargarAjustes(); }
    catch (err) { $("#clave-msg").textContent = err.message; }
  };
  $("#salir").onclick = () => { borrarToken(); TOKEN = ""; mostrarLogin(); };
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  TOKEN = leerToken();
  if (TOKEN) entrar(); else mostrarLogin();
});
