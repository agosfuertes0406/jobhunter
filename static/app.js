/* JobHunter AI — frontend SPA (sin dependencias). Toda la lógica sensible vive en el backend. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const app = $("#app");
const STATUSES = { nueva: "Nueva", pendiente: "Pendiente de revisar", cv_generado: "CV generado", postulada: "Postulada", entrevista: "Entrevista", rechazada: "Rechazada", cerrada: "Cerrada" };
const MODALITIES = { remoto: "Remoto", hibrido: "Híbrido", presencial: "Presencial" };
const ACCESS = { api_oficial: "API oficial", api_publica: "API pública", alertas_email: "Alertas por email", no_automatizable: "No automatizable" };
let ME = null;
let pollTimer = null;

// ---------------- utilidades ----------------
async function api(method, path, body, opts = {}) {
  const init = { method, headers: { "X-Requested-With": "JobHunter" }, credentials: "same-origin" };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  const res = await fetch(path, init);
  if (res.status === 401 && !opts.allow401) { ME = null; location.hash = "#/login"; throw new Error("Sesión expirada"); }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `Error ${res.status}`);
  return data;
}
function toast(msg, err = false) {
  const t = document.createElement("div");
  t.className = "toast" + (err ? " err" : "");
  t.textContent = msg;
  const box = $("#toasts");
  box.appendChild(t);
  while (box.children.length > 3) box.firstChild.remove();
  setTimeout(() => t.remove(), err ? 7000 : 3500);
}
async function act(btn, fn, okMsg) {
  if (btn) btn.disabled = true;
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) { toast(e.message, true); }
  finally { if (btn) btn.disabled = false; }
}
const fmtDate = (iso) => iso ? new Date(iso.length === 10 ? iso + "T12:00:00" : iso + (iso.endsWith("Z") || iso.includes("+") ? "" : "Z")).toLocaleDateString("es-AR") : "—";
const fmtDateTime = (iso) => iso ? new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" }) : "—";
const scoreCls = (s) => s == null ? "lo" : s >= 70 ? "hi" : s >= 45 ? "mid" : "lo";
const scoreBadge = (s) => `<span class="score ${scoreCls(s)}" title="Compatibilidad estimada (orientativa)">${s == null ? "—" : Math.round(s)}</span>`;
const lines = (s) => String(s || "").split("\n").map((x) => x.trim()).filter(Boolean);
const csv = (s) => String(s || "").split(/[,\n]/).map((x) => x.trim()).filter(Boolean);
const statusOptions = (cur) => Object.entries(STATUSES).map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${v}</option>`).join("");
function download(url) { const a = document.createElement("a"); a.href = url; a.download = ""; document.body.appendChild(a); a.click(); a.remove(); }
function modal(html) {
  const bg = document.createElement("div");
  bg.className = "modal-bg";
  bg.innerHTML = `<div class="card modal">${html}</div>`;
  bg.addEventListener("click", (e) => { if (e.target === bg || e.target.closest("[data-close]")) bg.remove(); });
  document.body.appendChild(bg);
  return bg;
}

// ---------------- layout y router ----------------
const NAV = [
  ["#/", "Panel"], ["#/profile", "Perfil y CV"], ["#/search", "Búsqueda"], ["#/sources", "Fuentes"],
  ["#/cvs", "CVs generados"], ["#/runs", "Ejecuciones"], ["#/reports", "Informes"], ["#/settings", "Ajustes"],
];
function shell(content) {
  const h = location.hash || "#/";
  const active = (href) => (href === "#/" ? h === "#/" || h.startsWith("#/job") : h.startsWith(href)) ? "active" : "";
  app.innerHTML = `
  <div class="topbar"><div class="brand"><span class="logo">JH</span>JobHunter AI</div><button id="menuBtn" aria-label="Menú">☰</button></div>
  <div class="layout">
    <aside class="sidebar" id="sidebar">
      <div class="brand"><span class="logo">JH</span>JobHunter AI</div>
      <nav class="nav">${NAV.map(([href, label]) => `<a href="${href}" class="${active(href)}">${label}</a>`).join("")}</nav>
      <div class="foot muted">${esc(ME?.email || "")}<br><a href="#" id="logout">Cerrar sesión</a></div>
    </aside>
    <main class="main" id="main">${content}</main>
  </div>`;
  $("#logout").onclick = async (e) => { e.preventDefault(); await api("POST", "/api/auth/logout"); ME = null; location.hash = "#/login"; };
  $("#menuBtn").onclick = () => $("#sidebar").classList.toggle("open");
  return $("#main");
}
const loading = () => shell(`<div class="empty">Cargando…</div>`);

const ROUTES = [
  [/^#\/login$/, viewLogin], [/^#\/register$/, viewRegister], [/^#\/?$/, viewDashboard], [/^#\/job\/(\d+)$/, viewJob],
  [/^#\/cv\/(\d+)$/, viewCV], [/^#\/cvs$/, viewCVs], [/^#\/profile$/, viewProfile], [/^#\/search$/, viewSearch],
  [/^#\/sources$/, viewSources], [/^#\/runs$/, viewRuns], [/^#\/reports$/, viewReports], [/^#\/settings$/, viewSettings],
];
async function router() {
  clearInterval(pollTimer);
  const h = location.hash || "#/";
  if (!ME && !["#/login", "#/register"].includes(h)) {
    try { ME = await api("GET", "/api/auth/me", undefined, { allow401: true }); }
    catch { location.hash = "#/login"; return; }
  }
  for (const [re, fn] of ROUTES) {
    const m = h.match(re);
    if (m) { try { await fn(...m.slice(1)); } catch (e) { if (ME) shell(`<div class="notice">${esc(e.message)}</div>`); } window.scrollTo(0, 0); return; }
  }
  location.hash = "#/";
}
window.addEventListener("hashchange", router);

// ---------------- autenticación ----------------
function authForm(register) {
  app.innerHTML = `
  <div class="auth card">
    <div class="brand"><span class="logo">JH</span>JobHunter AI</div>
    <p class="muted">${register ? "Creá tu cuenta. Tus documentos se guardan cifrados en este servidor." : "Tu asistente personal de búsqueda laboral."}</p>
    <form id="f">
      ${register ? `<div class="field"><label>Nombre</label><input name="name" autocomplete="name"></div>` : ""}
      <div class="field"><label>Email</label><input name="email" type="email" required autocomplete="email"></div>
      <div class="field"><label>Contraseña ${register ? "(mín. 8 caracteres)" : ""}</label><input name="password" type="password" required minlength="${register ? 8 : 1}" autocomplete="${register ? "new-password" : "current-password"}"></div>
      <button class="primary" style="width:100%;justify-content:center">${register ? "Crear cuenta" : "Ingresar"}</button>
    </form>
    <p class="small muted" style="margin-top:14px">${register ? `¿Ya tenés cuenta? <a href="#/login">Ingresá</a>` : `¿Primera vez? <a href="#/register">Creá una cuenta</a>`}</p>
  </div>`;
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    const btn = $("button", e.target);
    const u = await act(btn, () => api("POST", `/api/auth/${register ? "register" : "login"}`, body, { allow401: true }));
    if (u) { ME = u; location.hash = register ? "#/profile" : "#/"; }
  };
}
function viewLogin() { authForm(false); }
function viewRegister() { authForm(true); }

// ---------------- panel principal ----------------
const DASH = { view: "activas", q: "", company: "", title: "", modality: "", status: "", source: "", min_score: "", date_from: "", date_to: "", sort: "score" };
async function viewDashboard() {
  loading();
  const [stats, sources, profile] = await Promise.all([api("GET", "/api/jobs/stats"), api("GET", "/api/sources"), api("GET", "/api/profile")]);
  const main = shell(`
    <div class="page-head">
      <div><h1>Panel</h1><div class="muted">Ofertas analizadas contra tu perfil · ${esc(stats.today)}</div></div>
      <div class="row"><button id="addJob">+ Agregar oferta manual</button><button class="primary" id="runNow">Buscar ahora</button></div>
    </div>
    ${!profile.profile ? `<div class="notice">Todavía no cargaste tu CV. <a href="#/profile">Subilo acá</a> para poder analizar ofertas.</div>` : ""}
    <div class="grid tiles">
      ${[["hoy", stats.new_today, "Ofertas nuevas (24 h)"], ["pendientes", stats.pending, "Pendientes de revisar"], ["guardadas", stats.saved, "Guardadas"], ["descartadas", stats.discarded, "Descartadas"], ["cvs", stats.cvs, "CVs generados"]]
        .map(([v, n, l]) => `<button class="card tile" data-view="${v}"><div class="n">${n}</div><div class="l">${l}</div></button>`).join("")}
    </div>
    <div class="card" style="margin-bottom:14px">
      <div class="row between"><h2 style="margin:0">Estado de las fuentes</h2><a href="#/sources" class="small">Ver detalle</a></div>
      <div class="row" style="margin-top:8px">${sources.filter((s) => s.enabled).map(sourceChip).join("") || '<span class="muted small">No hay fuentes activas. Activá fuentes en <a href="#/search">Búsqueda</a>.</span>'}</div>
    </div>
    <div class="card">
      <div class="tabs" id="tabs">${[["activas", "Activas"], ["hoy", "Nuevas hoy"], ["pendientes", "Pendientes"], ["guardadas", "Guardadas"], ["descartadas", "Descartadas"], ["todas", "Todas"]]
        .map(([k, l]) => `<button class="sm ${DASH.view === k ? "on" : ""}" data-tab="${k}">${l}</button>`).join("")}</div>
      <form class="filters" id="filters">
        <input name="q" placeholder="Buscar texto…" value="${esc(DASH.q)}">
        <input name="company" placeholder="Empresa" list="companies" value="${esc(DASH.company)}">
        <datalist id="companies">${stats.companies.map((c) => `<option value="${esc(c)}">`).join("")}</datalist>
        <input name="title" placeholder="Puesto" value="${esc(DASH.title)}">
        <select name="modality"><option value="">Modalidad: todas</option>${Object.entries(MODALITIES).map(([k, v]) => `<option value="${k}" ${DASH.modality === k ? "selected" : ""}>${v}</option>`).join("")}</select>
        <select name="status"><option value="">Estado: todos</option>${statusOptions(DASH.status)}</select>
        <select name="source"><option value="">Fuente: todas</option>${sources.map((s) => `<option value="${s.key}" ${DASH.source === s.key ? "selected" : ""}>${esc(s.name)}</option>`).join("")}<option value="manual" ${DASH.source === "manual" ? "selected" : ""}>Carga manual</option></select>
        <input name="min_score" type="number" min="0" max="100" placeholder="Compatibilidad mín." value="${esc(DASH.min_score)}">
        <input name="date_from" type="date" title="Publicada desde" value="${esc(DASH.date_from)}">
        <input name="date_to" type="date" title="Publicada hasta" value="${esc(DASH.date_to)}">
        <select name="sort"><option value="score" ${DASH.sort === "score" ? "selected" : ""}>Orden: compatibilidad</option><option value="fecha" ${DASH.sort === "fecha" ? "selected" : ""}>Orden: fecha de publicación</option><option value="recientes" ${DASH.sort === "recientes" ? "selected" : ""}>Orden: últimas detectadas</option></select>
        <button type="button" id="clearF">Limpiar filtros</button>
      </form>
      ${DASH.view === "descartadas" && stats.discard_reasons.length ? `<div style="margin-top:12px" class="row">${stats.discard_reasons.map(([r, n]) => `<span class="chip">${esc(r)}: ${n}</span>`).join("")}</div>` : ""}
    </div>
    <div id="jobList" style="margin-top:14px"></div>`);
  $$("[data-view]", main).forEach((b) => b.onclick = () => { if (b.dataset.view === "cvs") location.hash = "#/cvs"; else { DASH.view = b.dataset.view; viewDashboard(); } });
  $$("[data-tab]", main).forEach((b) => b.onclick = () => { DASH.view = b.dataset.tab; viewDashboard(); });
  const f = $("#filters");
  let deb;
  f.oninput = () => { clearTimeout(deb); deb = setTimeout(() => { Object.assign(DASH, Object.fromEntries(new FormData(f))); loadJobs(); }, 300); };
  f.onsubmit = (e) => e.preventDefault();
  $("#clearF").onclick = () => { Object.assign(DASH, { q: "", company: "", title: "", modality: "", status: "", source: "", min_score: "", date_from: "", date_to: "" }); viewDashboard(); };
  $("#runNow").onclick = (e) => startRun(e.target);
  $("#addJob").onclick = manualJobModal;
  loadJobs();
}
function sourceChip(s) {
  const map = { ok: ["ok", "OK"], error: ["bad", "Error"], no_configurada: ["warn", "Sin configurar"], sin_ejecutar: ["", "Sin ejecutar"] };
  const [cls, label] = !s.configured && s.last_status === "sin_ejecutar" ? ["warn", "Sin configurar"] : (map[s.last_status] || ["", s.last_status]);
  return `<span class="chip ${cls}" title="${esc(s.last_message || s.config_message)}">${esc(s.name)} · ${label}${s.last_status === "ok" ? ` (${s.last_count})` : ""}</span>`;
}
async function startRun(btn) {
  const r = await act(btn, () => api("POST", "/api/runs"));
  if (r) { toast("Búsqueda iniciada. Podés seguir el progreso en Ejecuciones."); location.hash = "#/runs"; }
}
async function loadJobs() {
  const box = $("#jobList");
  if (!box) return;
  const params = new URLSearchParams(Object.entries(DASH).filter(([, v]) => v !== "" && v != null));
  const data = await api("GET", "/api/jobs?" + params);
  box.innerHTML = data.items.length ? `<div class="muted small" style="margin-bottom:8px">${data.total} oferta(s)</div><div class="grid jobs">${data.items.map(jobCard).join("")}</div>`
    : `<div class="card empty">No hay ofertas para estos filtros.${DASH.view === "activas" ? " Configurá tu búsqueda y presioná <b>Buscar ahora</b>, o agregá una oferta manual." : ""}</div>`;
  bindJobCards(box);
}
function jobCard(j) {
  return `<article class="card job" data-id="${j.id}">
    <div class="row between" style="align-items:flex-start;flex-wrap:nowrap">
      <div style="min-width:0"><div class="t">${esc(j.title)}</div><div class="c">${esc(j.company || "Empresa no informada")}</div></div>
      ${scoreBadge(j.score)}
    </div>
    <div class="meta">
      <span class="chip">📍 ${esc(j.location || "Ubicación no informada")}</span>
      ${j.modality ? `<span class="chip">${MODALITIES[j.modality] || esc(j.modality)}</span>` : `<span class="chip">Modalidad no informada</span>`}
      <span class="chip">Publicada: ${fmtDate(j.published_at)}</span>
      ${j.salary_text ? `<span class="chip ok">💲 ${esc(j.salary_text)}</span>` : ""}
      <span class="chip pri">${esc(j.source_name)}</span>
      ${j.partial_analysis ? `<span class="chip warn" title="El aviso no trae la descripción completa">Análisis parcial</span>` : ""}
      ${j.is_test_data ? `<span class="chip bad">DATO DE PRUEBA</span>` : ""}
    </div>
    ${j.discarded ? `<div class="small" style="color:var(--bad)">Descartada: ${esc(j.discard_reason)}</div>` : ""}
    <div class="actions">
      <select class="sm-select" data-act="status" style="width:auto;padding:3px 6px;font-size:12px">${statusOptions(j.status)}</select>
      <a class="btn sm" href="#/job/${j.id}">Ver detalle</a>
      <a class="btn sm" href="${esc(j.url)}" target="_blank" rel="noopener noreferrer">Aviso original ↗</a>
      <button class="sm" data-act="save" title="Guardar">${j.saved ? "★ Guardada" : "☆ Guardar"}</button>
      ${j.cv_id ? `<button class="sm" data-act="docx" data-cv="${j.cv_id}">CV DOCX</button><button class="sm" data-act="pdf" data-cv="${j.cv_id}">CV PDF</button>`
        : `<button class="sm" data-act="gencv">Generar CV</button>`}
      ${j.discarded ? `<button class="sm" data-act="restore">Restaurar</button>` : `<button class="sm" data-act="discard">Descartar</button>`}
    </div>
  </article>`;
}
function bindJobCards(box) {
  $$(".job", box).forEach((card) => {
    const id = card.dataset.id;
    $("[data-act=status]", card).onchange = (e) => act(null, () => api("PATCH", `/api/jobs/${id}`, { status: e.target.value }), "Estado actualizado");
    $$("button[data-act]", card).forEach((b) => b.onclick = async () => {
      const a = b.dataset.act;
      if (a === "save") { const j = await act(b, () => api("PATCH", `/api/jobs/${id}`, { saved: !b.textContent.includes("★") })); if (j) b.textContent = j.saved ? "★ Guardada" : "☆ Guardar"; }
      if (a === "docx" || a === "pdf") download(`/api/cvs/${b.dataset.cv}/${a}`);
      if (a === "gencv") { const r = await act(b, () => api("POST", `/api/jobs/${id}/cv`), "CV generado"); if (r) location.hash = `#/cv/${r.cv_id}`; }
      if (a === "discard") { const reason = prompt("Motivo del descarte (opcional):", "No me interesa"); if (reason !== null) { await act(b, () => api("PATCH", `/api/jobs/${id}`, { discarded: true, discard_reason: "Descartada manualmente: " + reason }), "Oferta descartada"); loadJobs(); } }
      if (a === "restore") { await act(b, () => api("PATCH", `/api/jobs/${id}`, { discarded: false }), "Oferta restaurada"); loadJobs(); }
    });
  });
}
function manualJobModal() {
  const m = modal(`<h2>Agregar oferta manualmente</h2>
    <p class="muted small">Útil para avisos de LinkedIn, ZonaJobs, Computrabajo o Indeed que viste en el sitio: pegá el link y la descripción completa para analizarla y generar tu CV.</p>
    <form id="mj">
      <div class="field"><label>Link original *</label><input name="url" type="url" required placeholder="https://…"></div>
      <div class="form-grid">
        <div class="field"><label>Puesto *</label><input name="title" required></div>
        <div class="field"><label>Empresa</label><input name="company"></div>
        <div class="field"><label>Ubicación</label><input name="location" placeholder="CABA, Buenos Aires"></div>
        <div class="field"><label>Modalidad</label><select name="modality"><option value="">No informada</option>${Object.entries(MODALITIES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></div>
        <div class="field"><label>Salario publicado</label><input name="salary_text"></div>
        <div class="field"><label>Fecha de publicación</label><input name="published_at" type="date"></div>
      </div>
      <div class="field"><label>Descripción completa del aviso</label><textarea name="description" rows="8"></textarea></div>
      <div class="row"><button class="primary">Analizar y guardar</button><button type="button" data-close>Cancelar</button></div>
    </form>`);
  $("#mj", m).onsubmit = async (e) => {
    e.preventDefault();
    const j = await act($("button.primary", m), () => api("POST", "/api/jobs/manual", Object.fromEntries(new FormData(e.target))), "Oferta agregada y analizada");
    if (j) { m.remove(); location.hash = `#/job/${j.id}`; }
  };
}

// ---------------- detalle de oferta ----------------
async function viewJob(id) {
  loading();
  const j = await api("GET", `/api/jobs/${id}`);
  const d = j.score_details || {};
  const a = j.analysis || {};
  const reqList = (arr, empty) => (arr || []).length ? arr.map((r) => `<div class="req"><div>${esc(r.text)} ${r.type === "excluyente" ? '<span class="chip bad">Excluyente</span>' : r.type === "deseable" ? '<span class="chip">Deseable</span>' : ""}</div><div class="muted small">${esc(r.evidence || "")}</div></div>`).join("") : `<div class="muted small">${empty}</div>`;
  const main = shell(`
    <div class="page-head">
      <div style="min-width:0">
        <div class="muted small"><a href="#/">← Panel</a></div>
        <h1>${esc(j.title)}</h1>
        <div class="muted">${esc(j.company || "Empresa no informada")} · ${esc(j.location || "Ubicación no informada")} · ${j.modality ? MODALITIES[j.modality] : "Modalidad no informada"}</div>
        <div class="row" style="margin-top:6px">
          <span class="chip">Publicada: ${fmtDate(j.published_at)}</span>
          ${j.salary_text ? `<span class="chip ok">Salario: ${esc(j.salary_text)}</span>` : `<span class="chip">Salario no publicado</span>`}
          ${j.contract_type ? `<span class="chip">${esc(j.contract_type)}</span>` : ""}
          <span class="chip pri">Fuente: ${esc(j.source_name)}</span>
          <span class="chip">Detectada: ${fmtDateTime(j.created_at)}</span>
          ${j.is_test_data ? `<span class="chip bad">DATO DE PRUEBA</span>` : ""}
        </div>
      </div>
      <div style="text-align:center">${scoreBadge(j.score)}<div class="small muted">compatibilidad<br>estimada</div></div>
    </div>
    ${j.discarded ? `<div class="notice">Descartada: ${esc(j.discard_reason)} <button class="sm" id="restore">Restaurar</button></div>` : ""}
    <div class="row" style="margin-bottom:14px">
      <a class="btn primary" href="${esc(j.url)}" target="_blank" rel="noopener noreferrer">Abrir aviso original ↗</a>
      <button id="gencv">${j.cvs.length ? "Generar nueva versión de CV" : "Generar CV para este puesto"}</button>
      ${j.cv_id ? `<button id="dl-docx">Descargar CV (DOCX)</button><button id="dl-pdf">Descargar CV (PDF)</button><a class="btn" href="#/cv/${j.cv_id}">Editar CV</a>` : ""}
      <button id="save">${j.saved ? "★ Guardada" : "☆ Guardar"}</button>
      <button id="reanalyze">Reanalizar</button>
      ${j.discarded ? "" : `<button id="discard">Descartar</button>`}
      <button class="danger" id="del">Eliminar</button>
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(340px,1fr))">
      <div class="card">
        <h2>Seguimiento de la postulación</h2>
        <form id="track">
          <div class="form-grid">
            <div class="field"><label>Estado</label><select name="status">${statusOptions(j.status)}</select></div>
            <div class="field"><label>Fecha de postulación</label><input type="date" name="applied_at" value="${esc(j.applied_at)}"></div>
          </div>
          <div class="field"><label>Notas</label><textarea name="notes">${esc(j.notes)}</textarea></div>
          <div class="field"><label>Observaciones (contactos, entrevistas, feedback)</label><textarea name="observations">${esc(j.observations)}</textarea></div>
          <button class="primary">Guardar seguimiento</button>
          <p class="small muted">JobHunter nunca se postula ni envía tus datos a empresas: la postulación la hacés vos desde el aviso original.</p>
        </form>
      </div>
      <div class="card">
        <h2>Cómo se calculó la compatibilidad</h2>
        ${d.partial_analysis ? `<div class="notice">Análisis parcial: la fuente no entrega la descripción completa. Pegala abajo para un análisis completo.</div>` : ""}
        <table><tbody>${(d.criteria || []).map((c) => `<tr><td><b>${esc(c.label)}</b><div class="small muted">${esc(c.explanation)}</div></td><td class="nowrap right">${c.points} / ${c.weight}<div class="bar"><i style="width:${Math.round(c.value * 100)}%"></i></div></td></tr>`).join("")}
        ${d.penalty ? `<tr><td colspan="2" style="color:var(--bad)">${esc(d.penalty_reason)}</td></tr>` : ""}</tbody></table>
        <p class="small muted">${esc(d.disclaimer || "")} Método de análisis: ${d.method === "claude" ? "IA (Claude)" : "heurístico local"}.</p>
      </div>
    </div>
    <div class="card" style="margin-top:14px">
      <h2>Requisitos del aviso vs. tu perfil</h2>
      <div class="cols3">
        <div><h3 style="color:var(--ok)">✔ Cumplidos (${(d.requirements_met || []).length})</h3>${reqList(d.requirements_met, "Ninguno detectado.")}</div>
        <div><h3 style="color:var(--warn)">◐ Parcialmente cumplidos (${(d.requirements_partial || []).length})</h3>${reqList(d.requirements_partial, "Ninguno.")}</div>
        <div><h3 style="color:var(--bad)">✘ Faltantes (${(d.requirements_missing || []).length})</h3>${reqList(d.requirements_missing, "Ninguno.")}</div>
      </div>
      ${(a.responsibilities || []).length ? `<h3>Responsabilidades extraídas</h3><ul class="small">${a.responsibilities.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
      <div class="row small" style="margin-top:8px"><span class="muted">Herramientas que tenés:</span> ${(d.matched_skills || []).map((s) => `<span class="chip ok">${esc(s)}</span>`).join("") || "—"}</div>
      <div class="row small" style="margin-top:6px"><span class="muted">Herramientas que faltan:</span> ${(d.missing_skills || []).map((s) => `<span class="chip bad">${esc(s)}</span>`).join("") || "—"}</div>
    </div>
    <div class="card" style="margin-top:14px">
      <h2>Descripción del aviso</h2>
      ${j.description ? `<div class="desc">${esc(j.description)}</div>` : `<div class="muted">La fuente no incluye la descripción (p. ej., alertas por email).</div>`}
      <details style="margin-top:10px" ${j.description_complete ? "" : "open"}><summary>${j.description ? "Reemplazar por la descripción completa" : "Pegar la descripción completa"}</summary>
        <form id="descF" style="margin-top:8px"><textarea name="description" rows="8" placeholder="Copiá el texto del aviso desde el sitio original y pegalo acá"></textarea><button class="primary" style="margin-top:6px">Guardar y reanalizar</button></form>
      </details>
    </div>
    ${j.cvs.length ? `<div class="card" style="margin-top:14px"><h2>CVs generados para esta oferta</h2><table><thead><tr><th>Archivo</th><th>Fecha</th><th>Método</th><th></th></tr></thead><tbody>
      ${j.cvs.map((c) => `<tr><td>${esc(c.filename_base)}${c.edited ? ' <span class="chip">editado</span>' : ""}</td><td>${fmtDateTime(c.created_at)}</td><td>${c.method === "claude" ? "IA" : "Local"}</td><td class="right nowrap"><a class="btn sm" href="#/cv/${c.id}">Ver / editar</a> <a class="btn sm" href="/api/cvs/${c.id}/docx">DOCX</a> <a class="btn sm" href="/api/cvs/${c.id}/pdf">PDF</a></td></tr>`).join("")}
    </tbody></table></div>` : ""}`);

  const patch = (body, msg) => act(null, () => api("PATCH", `/api/jobs/${id}`, body), msg);
  $("#track").onsubmit = async (e) => { e.preventDefault(); await act($("button", e.target), () => api("PATCH", `/api/jobs/${id}`, Object.fromEntries(new FormData(e.target))), "Seguimiento guardado"); };
  $("#gencv").onclick = async (e) => { const r = await act(e.target, () => api("POST", `/api/jobs/${id}/cv`), "CV generado"); if (r) location.hash = `#/cv/${r.cv_id}`; };
  if (j.cv_id) { $("#dl-docx").onclick = () => download(`/api/cvs/${j.cv_id}/docx`); $("#dl-pdf").onclick = () => download(`/api/cvs/${j.cv_id}/pdf`); }
  $("#save").onclick = async (e) => { const r = await patch({ saved: !j.saved }); if (r) viewJob(id); };
  $("#reanalyze").onclick = async (e) => { const r = await act(e.target, () => api("POST", `/api/jobs/${id}/reanalyze`), "Oferta reanalizada"); if (r) viewJob(id); };
  if ($("#discard")) $("#discard").onclick = async () => { const reason = prompt("Motivo del descarte:", "No me interesa"); if (reason !== null) { await patch({ discarded: true, discard_reason: "Descartada manualmente: " + reason }, "Descartada"); viewJob(id); } };
  if ($("#restore")) $("#restore").onclick = async () => { await patch({ discarded: false }, "Restaurada"); viewJob(id); };
  $("#del").onclick = async (e) => { if (confirm("¿Eliminar la oferta y sus CVs generados?")) { const r = await act(e.target, () => api("DELETE", `/api/jobs/${id}`), "Oferta eliminada"); if (r) location.hash = "#/"; } };
  $("#descF").onsubmit = async (e) => { e.preventDefault(); const r = await act($("button", e.target), () => api("POST", `/api/jobs/${id}/description`, Object.fromEntries(new FormData(e.target))), "Descripción guardada y oferta reanalizada"); if (r) viewJob(id); };
}

// ---------------- editor de entradas (perfil y CV) ----------------
function entriesEditor(container, items, fields, title, blank) {
  const render = () => {
    container.innerHTML = items.map((it, i) => `<div class="entry" data-i="${i}">
      <div class="form-grid">${fields.map(([k, label, type]) => type === "lines" || type === "csv"
        ? `<div class="field" style="grid-column:1/-1"><label>${label}${type === "lines" ? " (una por línea)" : " (separadas por coma)"}</label><textarea data-k="${k}" data-type="${type}" rows="${type === "lines" ? 4 : 2}">${esc(type === "lines" ? (it[k] || []).join("\n") : (it[k] || []).join(", "))}</textarea></div>`
        : `<div class="field"><label>${label}</label><input data-k="${k}" value="${esc(it[k] || "")}"></div>`).join("")}</div>
      <div class="row"><button type="button" class="sm" data-up>↑</button><button type="button" class="sm" data-down>↓</button><button type="button" class="sm danger" data-rm>Quitar</button></div>
    </div>`).join("") + `<button type="button" class="sm" data-add>+ Agregar ${title}</button>`;
    $$(".entry", container).forEach((el) => {
      const i = +el.dataset.i;
      $$("[data-k]", el).forEach((inp) => inp.oninput = () => {
        const t = inp.dataset.type;
        items[i][inp.dataset.k] = t === "lines" ? lines(inp.value) : t === "csv" ? csv(inp.value) : inp.value;
      });
      $("[data-rm]", el).onclick = () => { items.splice(i, 1); render(); };
      $("[data-up]", el).onclick = () => { if (i > 0) { [items[i - 1], items[i]] = [items[i], items[i - 1]]; render(); } };
      $("[data-down]", el).onclick = () => { if (i < items.length - 1) { [items[i + 1], items[i]] = [items[i], items[i + 1]]; render(); } };
    });
    $("[data-add]", container).onclick = () => { items.push(structuredClone(blank)); render(); };
  };
  render();
}

// ---------------- perfil ----------------
async function viewProfile() {
  loading();
  const [pr, versions, revisions, sys] = await Promise.all([api("GET", "/api/profile"), api("GET", "/api/profile/versions"), api("GET", "/api/profile/revisions"), api("GET", "/api/system")]);
  const p = pr.profile || { experience: [], education: [], courses: [], languages: [], technical_skills: [], tools: [], soft_skills: [], achievements: [], keywords: [] };
  const main = shell(`
    <div class="page-head"><div><h1>Perfil profesional</h1><div class="muted">Tu perfil maestro: solo información real. Es la base de cada CV adaptado.</div></div></div>
    <div class="card">
      <h2>${pr.profile ? "Actualizar CV" : "Cargá tu CV"}</h2>
      <form id="up" class="row">
        <input type="file" name="file" accept=".pdf,.docx" required style="max-width:360px">
        <label class="check"><input type="checkbox" name="replace" checked> Reemplazar el perfil con el nuevo análisis</label>
        <button class="primary">Subir y analizar</button>
      </form>
      <p class="small muted">PDF con texto seleccionable o DOCX, hasta 8 MB. Se guarda cifrado. Análisis: ${sys.ai.configured ? `IA (Claude, ${esc(sys.ai.model)})` : "heurístico local (configurá ANTHROPIC_API_KEY para un análisis más preciso)"}. Las versiones anteriores se conservan.</p>
      <div id="upRes"></div>
    </div>
    ${pr.profile ? `
    <form id="pf">
    <div class="card">
      <div class="row between"><h2 style="margin:0">Datos personales y resumen</h2><span class="muted small">Experiencia total calculada: ${pr.years_experience} años · Última actualización: ${fmtDateTime(pr.updated_at)}</span></div>
      <div class="form-grid" style="margin-top:10px">
        ${[["full_name", "Nombre completo"], ["headline", "Título profesional"], ["email", "Email"], ["phone", "Teléfono"], ["location", "Ubicación"], ["linkedin", "LinkedIn"], ["website", "Sitio / portfolio"]]
          .map(([k, l]) => `<div class="field"><label>${l}</label><input name="${k}" value="${esc(p[k])}"></div>`).join("")}
      </div>
      <div class="field"><label>Resumen profesional</label><textarea name="summary" rows="4">${esc(p.summary)}</textarea></div>
    </div>
    <div class="card"><h2>Experiencia laboral</h2><div id="exp"></div></div>
    <div class="card"><h2>Formación académica</h2><div id="edu"></div></div>
    <div class="card"><h2>Cursos y certificaciones</h2><div id="courses"></div></div>
    <div class="card"><h2>Idiomas</h2><div id="langs"></div></div>
    <div class="card"><h2>Habilidades, herramientas y logros</h2>
      ${[["technical_skills", "Conocimientos técnicos"], ["tools", "Herramientas y software"], ["soft_skills", "Habilidades profesionales / blandas"], ["keywords", "Palabras clave profesionales"]]
        .map(([k, l]) => `<div class="field"><label>${l} (separadas por coma)</label><textarea name="${k}" rows="2">${esc((p[k] || []).join(", "))}</textarea></div>`).join("")}
      <div class="field"><label>Logros cuantificables (uno por línea, con cifras reales)</label><textarea name="achievements" rows="4">${esc((p.achievements || []).join("\n"))}</textarea></div>
    </div>
    <div class="card row"><button class="primary">Guardar perfil</button><span class="muted small">Cada guardado crea una revisión que podés restaurar.</span></div>
    </form>` : ""}
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(340px,1fr));margin-top:14px">
      <div class="card"><h2>Versiones de CV cargadas</h2>
        ${versions.length ? `<table><tbody>${versions.map((v) => `<tr><td>${esc(v.filename)} ${v.active ? '<span class="chip ok">base del perfil</span>' : ""}<div class="small muted">${fmtDateTime(v.created_at)} · ${v.parse_method === "claude" ? "IA" : "heurístico"}</div></td>
          <td class="right nowrap"><a class="btn sm" href="/api/profile/versions/${v.id}/download">Descargar</a> <button class="sm" data-apply="${v.id}">Usar como perfil</button> <button class="sm danger" data-delv="${v.id}">Eliminar</button></td></tr>`).join("")}</tbody></table>` : `<div class="muted">Sin versiones.</div>`}
      </div>
      <div class="card"><h2>Historial de cambios del perfil</h2>
        ${revisions.length ? `<table><tbody>${revisions.slice(0, 15).map((r) => `<tr><td>${esc(r.reason)}<div class="small muted">${fmtDateTime(r.created_at)}</div></td><td class="right"><button class="sm" data-rest="${r.id}">Restaurar</button></td></tr>`).join("")}</tbody></table>` : `<div class="muted">Sin cambios.</div>`}
      </div>
    </div>`);

  $("#up").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData();
    fd.append("file", e.target.file.files[0]);
    const r = await act($("button", e.target), () => api("POST", `/api/profile/cv?replace_profile=${e.target.replace.checked}`, fd), "CV analizado");
    if (r) {
      if (r.warnings.length) toast(r.warnings.join(" "), false);
      viewProfile();
    }
  };
  $$("[data-apply]", main).forEach((b) => b.onclick = async () => { if (confirm("¿Reemplazar el perfil actual por el análisis de esta versión? (el actual queda en el historial)")) { await act(b, () => api("POST", `/api/profile/versions/${b.dataset.apply}/apply`), "Perfil restaurado"); viewProfile(); } });
  $$("[data-delv]", main).forEach((b) => b.onclick = async () => { if (confirm("¿Eliminar este archivo de CV? El perfil no se modifica.")) { await act(b, () => api("DELETE", `/api/profile/versions/${b.dataset.delv}`), "Versión eliminada"); viewProfile(); } });
  $$("[data-rest]", main).forEach((b) => b.onclick = async () => { await act(b, () => api("POST", `/api/profile/revisions/${b.dataset.rest}/restore`), "Revisión restaurada"); viewProfile(); });
  if (!pr.profile) return;

  const exp = structuredClone(p.experience), edu = structuredClone(p.education), courses = structuredClone(p.courses), langs = structuredClone(p.languages);
  entriesEditor($("#exp"), exp, [["title", "Cargo"], ["company", "Empresa"], ["location", "Ubicación"], ["start", "Desde (ej. 03/2021)"], ["end", "Hasta (o Actualidad)"], ["responsibilities", "Funciones", "lines"], ["achievements", "Logros reales", "lines"], ["tools", "Herramientas usadas", "csv"]], "experiencia", { title: "", company: "", location: "", start: "", end: "", responsibilities: [], achievements: [], tools: [] });
  entriesEditor($("#edu"), edu, [["degree", "Título / carrera"], ["field", "Área"], ["institution", "Institución"], ["start", "Desde"], ["end", "Hasta"], ["status", "Estado (Completo / En curso)"]], "formación", { degree: "", field: "", institution: "", start: "", end: "", status: "" });
  entriesEditor($("#courses"), courses, [["name", "Curso"], ["institution", "Institución"], ["year", "Año"]], "curso", { name: "", institution: "", year: "" });
  entriesEditor($("#langs"), langs, [["language", "Idioma"], ["level", "Nivel"]], "idioma", { language: "", level: "" });
  $("#pf").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const val = (k) => f.elements[k].value;
    const profile = {
      ...Object.fromEntries(["full_name", "headline", "email", "phone", "location", "linkedin", "website", "summary"].map((k) => [k, val(k)])),
      technical_skills: csv(val("technical_skills")), tools: csv(val("tools")), soft_skills: csv(val("soft_skills")), keywords: csv(val("keywords")),
      achievements: lines(val("achievements")), experience: exp, education: edu, courses, languages: langs,
    };
    const r = await act($("button.primary", f), () => api("PUT", "/api/profile", { profile }), "Perfil guardado");
    if (r) viewProfile();
  };
}

// ---------------- configuración de búsqueda ----------------
async function viewSearch() {
  loading();
  const [c, sources] = await Promise.all([api("GET", "/api/search-config"), api("GET", "/api/sources")]);
  const ta = (k, label, hint) => `<div class="field"><label>${label}</label><textarea name="${k}" rows="2" placeholder="${hint || ""}">${esc((c[k] || []).join(", "))}</textarea></div>`;
  const main = shell(`
    <div class="page-head"><div><h1>Configuración de búsqueda</h1><div class="muted">Qué buscar, dónde y con qué criterios descartar ofertas.</div></div></div>
    <form id="sf">
      <div class="card"><h2>Puestos y palabras clave</h2>
        ${ta("titles", "Puestos deseados (separados por coma)", "Analista de RR.HH., Recruiter")}
        ${ta("alt_titles", "Puestos alternativos", "Talent Acquisition, Generalista de RR.HH.")}
        ${ta("keywords", "Palabras clave de búsqueda", "selección, onboarding")}
        ${ta("exclude_keywords", "Excluir avisos cuyo título contenga", "pasantía, ventas")}
      </div>
      <div class="card"><h2>Ubicación y modalidad</h2>
        ${ta("locations", "Ubicaciones aceptadas (además de CABA y GBA, que se reconocen automáticamente)", "Buenos Aires, CABA, Zona Norte")}
        <div class="field"><label>Modalidad</label>${Object.entries(MODALITIES).map(([k, v]) => `<label class="check"><input type="checkbox" name="mod_${k}" ${c.modalities.includes(k) ? "checked" : ""}> ${v}</label>`).join("")}</div>
        ${ta("contract_types", "Tipos de contratación aceptados (vacío = todos)", "Relación de dependencia, Full-time, Contrato")}
      </div>
      <div class="card"><h2>Criterios</h2>
        <div class="form-grid">
          <div class="field"><label>Salario mínimo pretendido (mensual)</label><input name="min_salary" type="number" min="0" value="${c.min_salary ?? ""}"></div>
          <div class="field"><label>Moneda</label><select name="salary_currency">${["ARS", "USD"].map((m) => `<option ${c.salary_currency === m ? "selected" : ""}>${m}</option>`).join("")}</select></div>
          <div class="field"><label>Máximo de años de experiencia requeridos</label><input name="max_years_required" type="number" min="0" max="30" value="${c.max_years_required ?? ""}" placeholder="Sin límite"></div>
          <div class="field"><label>Antigüedad máxima del aviso (días)</label><input name="max_age_days" type="number" min="1" max="60" value="${c.max_age_days ?? 3}"></div>
          <div class="field"><label>Compatibilidad mínima para no descartar (0-100)</label><input name="min_score" type="number" min="0" max="100" value="${c.min_score ?? 40}"></div>
        </div>
        <p class="small muted">El salario solo se compara cuando el aviso lo publica en la misma moneda.</p>
      </div>
      <div class="card"><h2>Portales a consultar</h2>
        <table><thead><tr><th></th><th>Fuente</th><th>Acceso</th><th>Configuración</th></tr></thead><tbody>
        ${sources.map((s) => `<tr><td><input type="checkbox" name="src_${s.key}" ${s.enabled ? "checked" : ""} style="width:auto"></td><td><b>${esc(s.name)}</b><div class="small muted">${esc(s.coverage)}</div></td><td><span class="chip">${ACCESS[s.access]}</span></td><td>${s.configured ? `<span class="chip ok">${esc(s.config_message)}</span>` : `<span class="chip warn">${esc(s.config_message)}</span>`}</td></tr>`).join("")}
        </tbody></table>
        <p class="small muted">Las instrucciones para habilitar cada fuente están en <a href="#/sources">Fuentes</a>.</p>
        ${ta("greenhouse_boards", "Empresas en Greenhouse (identificador del board)", "ej.: el 'xxx' de boards.greenhouse.io/xxx")}
        ${ta("lever_companies", "Empresas en Lever (identificador)", "ej.: el 'xxx' de jobs.lever.co/xxx")}
      </div>
      <div class="card row"><button class="primary">Guardar configuración</button></div>
    </form>`);
  $("#sf").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target, v = (k) => f.elements[k].value;
    const body = {
      ...Object.fromEntries(["titles", "alt_titles", "keywords", "exclude_keywords", "locations", "contract_types", "greenhouse_boards", "lever_companies"].map((k) => [k, csv(v(k))])),
      modalities: Object.keys(MODALITIES).filter((k) => f.elements["mod_" + k].checked),
      min_salary: v("min_salary"), salary_currency: v("salary_currency"), max_years_required: v("max_years_required"),
      max_age_days: v("max_age_days"), min_score: v("min_score"),
      sources: Object.fromEntries(sources.map((s) => [s.key, f.elements["src_" + s.key].checked])),
    };
    const r = await act($("button.primary", f), () => api("PUT", "/api/search-config", body), "Configuración guardada");
    if (r) viewSearch();
  };
}

// ---------------- fuentes ----------------
async function viewSources() {
  loading();
  const sources = await api("GET", "/api/sources");
  shell(`
    <div class="page-head"><div><h1>Fuentes de empleo</h1><div class="muted">Solo accesos legítimos: APIs oficiales o públicas y tus propias alertas por email. Nunca se simulan resultados.</div></div><a class="btn" href="#/search">Activar / desactivar fuentes</a></div>
    <div class="notice info">LinkedIn, ZonaJobs, Computrabajo e Indeed no ofrecen una API pública de búsqueda y sus términos prohíben el scraping. JobHunter los integra leyendo (en solo lectura) las <b>alertas de empleo</b> que esos portales te envían por email, y te permite cargar avisos manualmente.</div>
    ${sources.map((s) => `<div class="card">
      <div class="row between"><div><h2 style="margin:0">${esc(s.name)} <span class="chip">${ACCESS[s.access]}</span> ${s.enabled ? '<span class="chip pri">Activa</span>' : '<span class="chip">Inactiva</span>'}</h2></div>${sourceChip(s)}</div>
      <p class="small">${esc(s.coverage)}</p>
      <p class="small"><b>Cómo habilitarla:</b> ${esc(s.setup)} ${s.needs.length ? `<br><span class="muted">Variables de entorno: ${s.needs.map((n) => `<code>${n}</code>`).join(", ")}</span>` : ""}</p>
      <div class="small muted">Configuración: ${esc(s.config_message)} · Última consulta: ${fmtDateTime(s.last_run_at)} · Último OK: ${fmtDateTime(s.last_ok_at)}${s.last_message ? ` · Mensaje: ${esc(s.last_message)}` : ""}</div>
      <div class="small" style="margin-top:6px"><a href="${esc(s.homepage)}" target="_blank" rel="noopener">${esc(s.homepage)} ↗</a></div>
    </div>`).join("")}`);
}

// ---------------- CVs ----------------
async function viewCVs() {
  loading();
  const cvs = await api("GET", "/api/cvs");
  const main = shell(`
    <div class="page-head"><div><h1>Historial de CVs generados</h1><div class="muted">Un CV adaptado por oferta, editable antes de descargar.</div></div></div>
    <div class="card">${cvs.length ? `<table><thead><tr><th>Archivo</th><th>Oferta</th><th>Compat.</th><th>Fecha</th><th></th></tr></thead><tbody>
      ${cvs.map((c) => `<tr><td>${esc(c.filename_base)}${c.edited ? ' <span class="chip">editado</span>' : ""}<div class="small muted">${c.method === "claude" ? "Generado con IA" : "Generado localmente"}</div></td>
      <td><a href="#/job/${c.job_id}">${esc(c.job_title)}</a><div class="small muted">${esc(c.company)} · ${STATUSES[c.job_status] || ""}</div></td><td>${scoreBadge(c.score)}</td><td class="nowrap">${fmtDateTime(c.updated_at)}</td>
      <td class="right nowrap"><a class="btn sm" href="#/cv/${c.id}">Editar</a> <a class="btn sm" href="/api/cvs/${c.id}/docx">DOCX</a> <a class="btn sm" href="/api/cvs/${c.id}/pdf">PDF</a> <button class="sm danger" data-del="${c.id}">Eliminar</button></td></tr>`).join("")}
    </tbody></table>` : `<div class="empty">Todavía no se generaron CVs. Abrí una oferta y presioná “Generar CV”.</div>`}</div>`);
  $$("[data-del]", main).forEach((b) => b.onclick = async () => { if (confirm("¿Eliminar este CV?")) { await act(b, () => api("DELETE", `/api/cvs/${b.dataset.del}`), "CV eliminado"); viewCVs(); } });
}

async function viewCV(id) {
  loading();
  const cv = await api("GET", `/api/cvs/${id}`);
  const c = structuredClone(cv.content);
  const an = cv.analysis || {};
  const main = shell(`
    <div class="page-head">
      <div><div class="muted small"><a href="#/job/${cv.job_id}">← ${esc(cv.job_title)} · ${esc(cv.company)}</a></div><h1>Editar CV adaptado</h1><div class="muted">Revisá y ajustá el contenido. Al guardar se regeneran el DOCX y el PDF.</div></div>
      <div class="row"><button id="dl-docx">Descargar DOCX</button><button id="dl-pdf">Descargar PDF</button></div>
    </div>
    <div class="grid" style="grid-template-columns:minmax(0,2fr) minmax(280px,1fr);align-items:start">
      <form id="cvf">
        <div class="card">
          <div class="field"><label>Nombre del archivo</label><input name="filename_base" value="${esc(cv.filename_base)}"></div>
          <div class="form-grid">
            <div class="field"><label>Nombre</label><input name="full_name" value="${esc(c.full_name)}"></div>
            <div class="field"><label>Título profesional</label><input name="headline" value="${esc(c.headline)}"></div>
            ${["location", "phone", "email", "linkedin", "website"].map((k) => `<div class="field"><label>${{ location: "Ubicación", phone: "Teléfono", email: "Email", linkedin: "LinkedIn", website: "Sitio" }[k]}</label><input name="c_${k}" value="${esc(c.contact?.[k])}"></div>`).join("")}
          </div>
          <div class="field"><label>Perfil profesional</label><textarea name="summary" rows="5">${esc(c.summary)}</textarea></div>
          <div class="field"><label>Habilidades y herramientas (en orden de prioridad, separadas por coma)</label><textarea name="skills" rows="3">${esc((c.skills || []).join(", "))}</textarea></div>
          <div class="field"><label>Competencias</label><textarea name="soft_skills" rows="2">${esc((c.soft_skills || []).join(", "))}</textarea></div>
        </div>
        <div class="card"><h2>Experiencia laboral</h2><div id="exp"></div></div>
        <div class="card"><h2>Formación</h2><div id="edu"></div></div>
        <div class="card"><h2>Cursos</h2><div id="courses"></div></div>
        <div class="card"><h2>Idiomas</h2><div id="langs"></div></div>
        <div class="card row"><button class="primary">Guardar y regenerar archivos</button></div>
      </form>
      <div>
        <div class="card"><h2>Qué se cambió y por qué</h2>
          <p class="small muted">${cv.method === "claude" ? "Adaptado con IA (Claude) y validado contra tu perfil maestro." : "Adaptación local (sin IA): se reordenó y priorizó tu información real."}</p>
          ${(an.changes || []).length ? `<ul class="small">${an.changes.map((x) => `<li><b>${esc(x.change)}</b> ${esc(x.reason)}</li>`).join("")}</ul>` : `<p class="small muted">Sin cambios registrados.</p>`}
          ${(an.keywords_used || []).length ? `<h3>Palabras clave del aviso reflejadas</h3><div class="row">${an.keywords_used.map((k) => `<span class="chip ok">${esc(k)}</span>`).join("")}</div>` : ""}
          ${(an.omitted_keywords || []).length ? `<h3>No incluidas (sin respaldo en tu perfil)</h3><ul class="small">${an.omitted_keywords.map((k) => `<li><b>${esc(k.keyword)}</b>: ${esc(k.reason)}</li>`).join("")}</ul>` : ""}
          ${(an.warnings || []).length ? `<h3>Controles anti-invención</h3><ul class="small" style="color:var(--warn)">${an.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
        </div>
        <div class="card small muted">Formato ATS: una columna, sin tablas, gráficos ni íconos, títulos de sección estándar y texto seleccionable. Revisá que todo sea fiel a tu experiencia antes de enviarlo.</div>
      </div>
    </div>`);
  $("#dl-docx").onclick = () => download(`/api/cvs/${id}/docx`);
  $("#dl-pdf").onclick = () => download(`/api/cvs/${id}/pdf`);
  entriesEditor($("#exp"), c.experience, [["title", "Cargo"], ["company", "Empresa"], ["location", "Ubicación"], ["start", "Desde"], ["end", "Hasta"], ["bullets", "Viñetas", "lines"]], "experiencia", { title: "", company: "", location: "", start: "", end: "", bullets: [] });
  entriesEditor($("#edu"), c.education, [["degree", "Título / carrera"], ["field", "Área"], ["institution", "Institución"], ["start", "Desde"], ["end", "Hasta"], ["status", "Estado"]], "formación", { degree: "", field: "", institution: "", start: "", end: "", status: "" });
  entriesEditor($("#courses"), c.courses, [["name", "Curso"], ["institution", "Institución"], ["year", "Año"]], "curso", { name: "", institution: "", year: "" });
  entriesEditor($("#langs"), c.languages, [["language", "Idioma"], ["level", "Nivel"]], "idioma", { language: "", level: "" });
  $("#cvf").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target, v = (k) => f.elements[k].value;
    const content = {
      ...c, full_name: v("full_name"), headline: v("headline"), summary: v("summary"), skills: csv(v("skills")), soft_skills: csv(v("soft_skills")),
      contact: Object.fromEntries(["location", "phone", "email", "linkedin", "website"].map((k) => [k, v("c_" + k)])),
    };
    const r = await act($("button.primary", f), () => api("PUT", `/api/cvs/${id}`, { content, filename_base: v("filename_base") }), "CV guardado: DOCX y PDF regenerados");
    if (r) viewCV(id);
  };
}

// ---------------- ejecuciones ----------------
async function viewRuns() {
  loading();
  const runs = await api("GET", "/api/runs");
  const label = { ok: ["ok", "Completa"], parcial: ["warn", "Parcial"], error: ["bad", "Error"], running: ["pri", "En curso…"] };
  const trig = { scheduler: "Automática", manual: "Manual", cli: "Línea de comandos" };
  const main = shell(`
    <div class="page-head"><div><h1>Historial de ejecuciones</h1><div class="muted">Búsquedas automáticas diarias y manuales, con el resultado de cada fuente.</div></div><button class="primary" id="runNow">Ejecutar ahora</button></div>
    ${runs.length ? runs.map((r) => {
      const s = r.stats || {};
      const [cls, lab] = label[r.status] || ["", r.status];
      return `<div class="card">
        <div class="row between"><div><b>${fmtDateTime(r.started_at)}</b> <span class="chip">${trig[r.trigger] || r.trigger}</span> <span class="chip ${cls}">${lab}</span></div>
        <div class="small muted">${r.finished_at ? "Duración: " + Math.max(1, Math.round((new Date(r.finished_at + "Z") - new Date(r.started_at + "Z")) / 1000)) + " s" : ""}</div></div>
        <div class="row small" style="margin-top:8px">${[["fetched", "obtenidos"], ["new", "nuevos"], ["duplicates", "duplicados"], ["discarded", "descartados"], ["relevant", "compatibles"], ["cvs_generated", "CVs generados"]].map(([k, l]) => `<span class="chip">${s[k] ?? 0} ${l}</span>`).join("")}</div>
        ${(r.source_logs || []).length ? `<details style="margin-top:8px"><summary class="small">Detalle por fuente</summary><table class="small"><tbody>${r.source_logs.map((l) => `<tr><td>${esc(l.source)}</td><td><span class="chip ${l.status === "ok" ? "ok" : l.status === "error" ? "bad" : "warn"}">${esc(l.status)}</span></td><td>${esc(l.message)}</td><td class="right">${l.seconds ?? ""} s</td></tr>`).join("")}</tbody></table></details>` : ""}
        ${(r.errors || []).length ? `<ul class="small" style="color:var(--bad);margin-bottom:0">${r.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
      </div>`;
    }).join("") : `<div class="card empty">Todavía no hubo ejecuciones.</div>`}`);
  $("#runNow").onclick = async (e) => { const r = await act(e.target, () => api("POST", "/api/runs"), "Búsqueda iniciada"); if (r) setTimeout(viewRuns, 800); };
  if (runs.some((r) => r.status === "running")) pollTimer = setInterval(() => { if (location.hash === "#/runs") viewRuns(); }, 4000);
}

// ---------------- informes ----------------
async function viewReports() {
  loading();
  const reps = await api("GET", "/api/reports");
  const main = shell(`
    <div class="page-head"><div><h1>Informes diarios</h1><div class="muted">Se generan en cada ejecución y se envían al correo configurado en <a href="#/settings">Ajustes</a>.</div></div></div>
    <div class="card">${reps.length ? `<table><thead><tr><th>Fecha</th><th>Asunto</th><th>Envío</th><th></th></tr></thead><tbody>
      ${reps.map((r) => `<tr><td class="nowrap">${esc(r.local_date)}</td><td>${esc(r.subject)}</td><td>${r.sent ? `<span class="chip ok">Enviado a ${esc(r.sent_to)}</span>` : `<span class="chip warn" title="${esc(r.send_error)}">No enviado</span><div class="small muted">${esc(r.send_error)}</div>`}</td>
      <td class="right nowrap"><a class="btn sm" href="/api/reports/${r.id}" target="_blank" rel="noopener">Ver</a> <button class="sm" data-send="${r.id}">Enviar ahora</button></td></tr>`).join("")}
    </tbody></table>` : `<div class="empty">Aún no hay informes.</div>`}</div>`);
  $$("[data-send]", main).forEach((b) => b.onclick = async () => { const r = await act(b, () => api("POST", `/api/reports/${b.dataset.send}/send`)); if (r) { toast("Informe enviado a " + r.to); viewReports(); } });
}

// ---------------- ajustes ----------------
async function viewSettings() {
  loading();
  const [s, sys] = await Promise.all([api("GET", "/api/settings"), api("GET", "/api/system")]);
  const st = (ok, yes, no) => ok ? `<span class="chip ok">${yes}</span>` : `<span class="chip warn">${no}</span>`;
  const main = shell(`
    <div class="page-head"><div><h1>Ajustes</h1></div></div>
    <form id="stf">
      <div class="card"><h2>Ejecución diaria e informe</h2>
        <div class="form-grid">
          <div class="field"><label>Tu nombre</label><input name="name" value="${esc(s.name)}"></div>
          <div class="field"><label>Correo destinatario del informe</label><input name="report_email" type="email" value="${esc(s.report_email)}"></div>
          <div class="field"><label>Horario de búsqueda + informe (hora de Buenos Aires)</label><input name="run_time" type="time" value="${esc(s.run_time)}"></div>
          <div class="field"><label>Máximo de ofertas en el informe</label><input name="report_max_jobs" type="number" min="1" max="50" value="${s.report_max_jobs}"></div>
          <div class="field"><label>Generar CV automáticamente desde compatibilidad</label><input name="auto_cv_min_score" type="number" min="0" max="100" value="${s.auto_cv_min_score}"></div>
          <div class="field"><label>Máximo de CVs automáticos por ejecución</label><input name="auto_cv_max_per_run" type="number" min="0" max="30" value="${s.auto_cv_max_per_run}"></div>
        </div>
        <label class="check"><input type="checkbox" name="run_enabled" ${s.run_enabled ? "checked" : ""}> Ejecutar la búsqueda automáticamente todos los días</label>
        <label class="check"><input type="checkbox" name="report_enabled" ${s.report_enabled ? "checked" : ""}> Enviar el informe por email</label>
        <label class="check"><input type="checkbox" name="attach_cvs" ${s.attach_cvs ? "checked" : ""}> Adjuntar los CV adaptados (DOCX) al email</label>
        <div style="margin-top:12px"><button class="primary">Guardar ajustes</button></div>
      </div>
    </form>
    <div class="card"><h2>Estado del servidor</h2>
      <table><tbody>
        <tr><td>Análisis con IA (Claude)</td><td>${st(sys.ai.configured, "Configurado · " + esc(sys.ai.model || ""), "No configurado: se usa análisis heurístico local (definí ANTHROPIC_API_KEY)")}</td></tr>
        <tr><td>Envío de informes (SMTP)</td><td>${st(sys.smtp.configured, "Configurado", "No configurado: los informes se guardan pero no se envían (definí SMTP_*)")}</td></tr>
        <tr><td>Lectura de alertas por email (IMAP)</td><td>${st(sys.imap.configured, "Configurado · " + esc(sys.imap.host || ""), "No configurado: LinkedIn/ZonaJobs/Computrabajo/Indeed sin datos (definí IMAP_*)")}</td></tr>
        <tr><td>Programación diaria</td><td>${esc(sys.scheduler)} · zona ${esc(sys.timezone)}</td></tr>
      </tbody></table>
      <p class="small muted">Las claves y credenciales se configuran solo en el servidor (variables de entorno). Nunca se envían al navegador.</p>
    </div>
    <div class="card"><h2 style="color:var(--bad)">Privacidad y eliminación</h2>
      <p class="small">Podés borrar tu perfil, todos los CV (originales y generados), ofertas, informes e historial. Esta acción no se puede deshacer.</p>
      <div class="row"><button class="danger" id="delData">Eliminar perfil y archivos</button><button class="danger" id="delAcc">Eliminar cuenta completa</button></div>
    </div>`);
  $("#stf").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target, body = Object.fromEntries(new FormData(f));
    ["run_enabled", "report_enabled", "attach_cvs"].forEach((k) => body[k] = f.elements[k].checked);
    await act($("button.primary", f), () => api("PUT", "/api/settings", body), "Ajustes guardados");
  };
  $("#delData").onclick = async (e) => { if (prompt('Escribí ELIMINAR para borrar tu perfil, CVs, ofertas e informes:') === "ELIMINAR") { const r = await act(e.target, () => api("DELETE", "/api/account/data"), "Datos eliminados"); if (r) location.hash = "#/profile"; } };
  $("#delAcc").onclick = async (e) => { if (prompt('Escribí ELIMINAR para borrar tu cuenta y todos tus datos:') === "ELIMINAR") { const r = await act(e.target, () => api("DELETE", "/api/account"), "Cuenta eliminada"); if (r) { ME = null; location.hash = "#/login"; } } };
}

router();
