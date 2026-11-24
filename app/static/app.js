"use strict";

const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "style") e.style.cssText = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) e.append(kid);
  return e;
};

let INFO = null;
const COLORS = ["#2563eb", "#16a34a", "#d97706", "#db2777", "#7c3aed", "#0891b2", "#dc2626", "#65a30d", "#c026d3", "#0d9488"];

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

function dur(sec) {
  if (sec == null) return "–";
  const m = Math.round(sec / 60);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}
function clock(ts) {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: INFO.schedule.timezone });
}
function addDays(iso, n) {
  const d = new Date(iso + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
function weekday(iso) {
  return new Date(iso + "T12:00:00Z").toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" });
}

/* ---------- tabs & status ---------- */
const tabLoaders = {};
let activeTab = "daily";
document.querySelectorAll("nav button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
    activeTab = b.dataset.tab;
    tabLoaders[activeTab]?.();
  })
);

async function refreshStatus() {
  INFO = await api("/api/info");
  $("#status").replaceChildren(
    ...INFO.cameras.map((c) =>
      el("span", { title: c.error || `last frame ${c.frame_age}s ago` }, el("span", { class: "dot" + (c.online ? " on" : "") }), c.name)
    ),
    el("span", {}, INFO.tracking ? "● Tracking" : "Paused (outside work hours)")
  );
}

function fillRoomSelect(sel) {
  if (sel.options.length > 1) return;
  INFO.cameras.forEach((c) => sel.append(el("option", { value: c.id }, c.name)));
}

/* ---------- daily ---------- */
function timeline(rep, sessions) {
  const t0 = rep.track_start, span = rep.track_end - rep.track_start;
  const pct = (t) => `${(100 * (t - t0)) / span}%`;
  const bar = el("div", { class: "timeline" },
    el("div", { class: "work", style: `left:${pct(rep.work_start)};width:${(100 * (rep.work_end - rep.work_start)) / span}%` }));
  sessions.forEach(([s, e]) => {
    const title = `${clock(s)} – ${clock(e)} (${dur(e - s)})`;
    bar.append(el("div", { class: "s", title, style: `left:${pct(s)};width:${Math.max(0.3, (100 * (e - s)) / span)}%` }));
  });
  const now = Date.now() / 1000;
  if (now > t0 && now < rep.track_end) bar.append(el("div", { class: "now", style: `left:${pct(now)}` }));
  return bar;
}
function axis(rep) {
  const a = el("div", { class: "axis" });
  const span = rep.track_end - rep.track_start;
  for (let t = Math.ceil(rep.track_start / 3600) * 3600; t <= rep.track_end; t += 3600)
    a.append(el("span", { style: `left:${(100 * (t - rep.track_start)) / span}%` }, clock(t).slice(0, 2)));
  return a;
}

async function loadDaily() {
  fillRoomSelect($("#day-room"));
  if (!$("#day-date").value) $("#day-date").value = INFO.today;
  const rep = await api(`/api/day?date=${$("#day-date").value}`);
  const room = $("#day-room").value;
  const desks = rep.desks.filter((d) => !room || d.camera === room);
  $("#day-note").textContent = rep.workday ? "" : "Not a working day";

  const present = desks.filter((d) => d.first_in);
  const avg = present.length ? present.reduce((a, d) => a + d.seated_work_seconds, 0) / present.length : null;
  const cov = desks.filter((d) => d.coverage_pct != null);
  $("#day-summary").replaceChildren(
    card(desks.length, "Desks tracked"),
    card(`${present.length}`, "Employees seen"),
    card(dur(avg), "Average time at desk (work hours)"),
    card(cov.length ? Math.round(Math.min(...cov.map((d) => d.coverage_pct))) + "%" : "–", "Lowest camera coverage")
  );

  if (!rep.desks.length) {
    $("#day-table").replaceChildren(el("p", { class: "muted" }, "No desks set up yet. Open the Desk setup tab to draw them."));
    return;
  }
  const head = el("tr", {}, ["Employee", "Desk", "First in", "Last out"].map((h) => el("th", {}, h)),
    ["At desk", "% of work hrs"].map((h) => el("th", { class: "num" }, h)),
    el("th", { class: "num" }, "Breaks"), el("th", { class: "num" }, "Longest break"), el("th", {}, axis(rep)));
  const body = el("tbody");
  for (const cam of INFO.cameras) {
    const rows = desks.filter((d) => d.camera === cam.id);
    if (!rows.length) continue;
    body.append(el("tr", { class: "room" }, el("td", { colspan: 9 }, cam.name)));
    rows.sort((a, b) => (a.employee || a.desk).localeCompare(b.employee || b.desk));
    for (const d of rows) {
      const lowCov = d.coverage_pct != null && d.coverage_pct < 90;
      body.append(el("tr", {},
        el("td", {}, d.employee || el("span", { class: "muted" }, "(unassigned)")),
        el("td", { class: "muted" }, d.desk),
        el("td", {}, clock(d.first_in)),
        el("td", {}, clock(d.last_out)),
        el("td", { class: "num" }, dur(d.seated_work_seconds)),
        el("td", { class: "num" }, d.seated_pct == null ? "–" :
          el("div", { style: "display:flex;gap:8px;align-items:center;justify-content:flex-end" }, `${Math.round(d.seated_pct)}%`,
            el("div", { class: "bar" }, el("div", { style: `width:${Math.min(100, d.seated_pct)}%` })))),
        el("td", { class: "num" }, d.breaks ? `${d.breaks} (${dur(d.break_seconds)})` : "0"),
        el("td", { class: "num" }, d.longest_break_seconds ? dur(d.longest_break_seconds) : "–"),
        el("td", {}, timeline(rep, d.sessions),
          lowCov ? el("div", { class: "warn", style: "font-size:11px" }, `camera coverage only ${Math.round(d.coverage_pct)}%`) : null)
      ));
    }
  }
  $("#day-table").replaceChildren(el("div", { class: "table-scroll" }, el("table", {}, el("thead", {}, head), body)));
}
function card(v, l) { return el("div", { class: "card" }, el("div", { class: "v" }, String(v)), el("div", { class: "l" }, l)); }
tabLoaders.daily = loadDaily;
$("#day-date").addEventListener("change", loadDaily);
$("#day-room").addEventListener("change", loadDaily);
$("#day-prev").addEventListener("click", () => { $("#day-date").value = addDays($("#day-date").value, -1); loadDaily(); });
$("#day-next").addEventListener("click", () => { $("#day-date").value = addDays($("#day-date").value, 1); loadDaily(); });

/* ---------- weekly ---------- */
function weekStartFor(iso) {
  const first = (INFO.schedule.workdays[0] || "mon").slice(0, 3).toLowerCase();
  const names = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
  const want = names.indexOf(first);
  const dow = new Date(iso + "T12:00:00Z").getUTCDay();
  return addDays(iso, -((dow - want + 7) % 7));
}
async function loadWeekly() {
  fillRoomSelect($("#week-room"));
  if (!$("#week-start").value) $("#week-start").value = weekStartFor(INFO.today);
  const start = $("#week-start").value, end = addDays(start, 6);
  $("#week-csv").href = `/api/export.csv?start=${start}&end=${end}`;
  const rep = await api(`/api/range?start=${start}&end=${end}`);
  const room = $("#week-room").value;
  const workdays = new Set(INFO.schedule.workdays.map((w) => w.slice(0, 3).toLowerCase()));
  const head = el("tr", {}, el("th", {}, "Employee"), el("th", {}, "Room"),
    rep.days.map((d) => el("th", { class: "num", style: workdays.has(weekday(d.date).toLowerCase()) ? "" : "opacity:.5" },
      `${weekday(d.date)} ${d.date.slice(8)}`)),
    el("th", { class: "num" }, "Total"), el("th", { class: "num" }, "Avg / day"));
  const body = el("tbody");
  const desks = rep.days[0].desks.filter((d) => !room || d.camera === room);
  desks.sort((a, b) => a.room.localeCompare(b.room) || (a.employee || a.desk).localeCompare(b.employee || b.desk));
  for (const desk of desks) {
    let total = 0, doneTotal = 0, doneDays = 0;
    const cells = rep.days.map((day) => {
      const d = day.desks.find((x) => x.desk_id === desk.desk_id);
      if (!d || !d.work_seconds) return el("td", { class: "heat muted" }, "");
      total += d.seated_work_seconds;
      if (day.workday && day.date < INFO.today) { doneTotal += d.seated_work_seconds; doneDays++; }
      const p = d.seated_pct || 0;
      return el("td", { class: "heat", title: `${clock(d.first_in)} – ${clock(d.last_out)}, ${Math.round(p)}%`,
        style: `background:rgba(22,163,74,${(p / 100) * 0.55})` }, d.first_in ? dur(d.seated_work_seconds) : "0");
    });
    body.append(el("tr", {}, el("td", {}, desk.employee || desk.desk), el("td", { class: "muted" }, desk.room), cells,
      el("td", { class: "num" }, dur(total)), el("td", { class: "num", title: "Average over completed workdays" }, doneDays ? dur(doneTotal / doneDays) : "–")));
  }
  $("#week-table").replaceChildren(desks.length
    ? el("div", { class: "table-scroll" }, el("table", {}, el("thead", {}, head), body))
    : el("p", { class: "muted" }, "No desks set up yet."));
}
tabLoaders.weekly = loadWeekly;
$("#week-start").addEventListener("change", loadWeekly);
$("#week-room").addEventListener("change", loadWeekly);
$("#week-prev").addEventListener("click", () => { $("#week-start").value = addDays($("#week-start").value, -7); loadWeekly(); });
$("#week-next").addEventListener("click", () => { $("#week-start").value = addDays($("#week-start").value, 7); loadWeekly(); });

/* ---------- live ---------- */
function loadLive() {
  $("#live-grid").replaceChildren(...INFO.cameras.map((c) => {
    const img = el("img", { alt: c.name, src: `/api/snapshot/${c.id}?overlay=1&t=${Date.now()}` });
    const cap = el("figcaption", {}, el("b", {}, c.name), el("span", { class: "muted" }, `${c.occupied_now}/${c.desks} desks occupied`));
    img.onerror = () => cap.lastChild.replaceWith(el("span", { class: "warn" }, c.error || "no image"));
    return el("figure", {}, img, cap);
  }));
}
tabLoaders.live = loadLive;
setInterval(async () => {
  await refreshStatus().catch(() => {});
  if (activeTab === "live") loadLive();
}, 10000);

/* ---------- desk setup ---------- */
const S = { cam: null, desks: [], current: [], img: null, dirty: false, tuning: null };
const canvas = $("#setup-canvas"), ctx = canvas.getContext("2d");

async function loadSetup() {
  const sel = $("#setup-cam");
  if (!sel.options.length) INFO.cameras.forEach((c) => sel.append(el("option", { value: c.id }, c.name)));
  if (S.cam !== sel.value) await selectCam(sel.value);
}
async function selectCam(cam) {
  const [zones, tuning] = await Promise.all([api("/api/zones"), api("/api/tuning")]);
  Object.assign(S, { cam, desks: zones[cam]?.desks || [], current: [], dirty: false, tuning });
  S.savedIds = new Set(S.desks.map((d) => d.id));
  renderList();
  loadImage();
}
function loadImage() {
  const img = new Image();
  img.onload = () => { S.img = img; canvas.width = img.naturalWidth; canvas.height = img.naturalHeight; draw(); msg(""); };
  img.onerror = () => msg("Could not load camera image – check the camera is online.", true);
  img.src = `/api/snapshot/${S.cam}?overlay=0&t=${Date.now()}`;
}
function msg(t, warn) { $("#setup-msg").textContent = t; $("#setup-msg").className = warn ? "warn" : "muted"; }

function draw() {
  const W = canvas.width, H = canvas.height;
  if (S.img) ctx.drawImage(S.img, 0, 0); else ctx.clearRect(0, 0, W, H);
  ctx.lineWidth = Math.max(2, W / 400);
  ctx.font = `${Math.max(14, W / 50)}px system-ui`;
  S.desks.forEach((d, i) => {
    const c = COLORS[i % COLORS.length];
    ctx.beginPath();
    d.points.forEach(([x, y], j) => (j ? ctx.lineTo(x * W, y * H) : ctx.moveTo(x * W, y * H)));
    ctx.closePath();
    ctx.fillStyle = c + "40"; ctx.fill(); ctx.strokeStyle = c; ctx.stroke();
    const [x, y] = d.points[0];
    ctx.fillStyle = "#fff"; ctx.strokeStyle = "#000"; ctx.lineWidth = 3;
    const label = d.employee || d.name;
    ctx.strokeText(label, x * W + 4, y * H + 18); ctx.fillText(label, x * W + 4, y * H + 18);
    ctx.lineWidth = Math.max(2, W / 400);
  });
  if (S.current.length) {
    ctx.strokeStyle = "#facc15"; ctx.fillStyle = "#facc15";
    ctx.beginPath();
    S.current.forEach(([x, y], j) => (j ? ctx.lineTo(x * W, y * H) : ctx.moveTo(x * W, y * H)));
    ctx.stroke();
    S.current.forEach(([x, y]) => { ctx.beginPath(); ctx.arc(x * W, y * H, Math.max(4, W / 250), 0, 7); ctx.fill(); });
  }
}

canvas.addEventListener("click", (ev) => {
  const r = canvas.getBoundingClientRect();
  const x = (ev.clientX - r.left) / r.width, y = (ev.clientY - r.top) / r.height;
  if (S.current.length >= 3) {
    const [fx, fy] = S.current[0];
    if (Math.hypot((fx - x) * r.width, (fy - y) * r.height) < 12) return finishDesk();
  }
  S.current.push([+x.toFixed(4), +y.toFixed(4)]);
  draw();
});
function finishDesk() {
  if (S.current.length < 3) return msg("A desk needs at least 3 points.", true);
  const camName = INFO.cameras.find((c) => c.id === S.cam).name;
  const employee = prompt(`Employee name for this desk (${camName}):`, "") ?? "";
  S.desks.push({ id: `${S.cam}-${Date.now().toString(36)}`, name: `Desk ${S.desks.length + 1}`, employee: employee.trim(), points: S.current });
  S.current = []; S.dirty = true;
  renderList(); draw(); msg("Not saved yet – press Save.", true);
}
function tuningInfo(d) {
  const T = S.tuning;
  if (!T || !T.enabled) return null;
  const e = T.desks[d.id];
  const setMode = (mode) => async () => {
    await api(`/api/tuning/${encodeURIComponent(d.id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode }) });
    S.tuning = await api("/api/tuning"); renderList();
  };
  const row = (text, btn) => el("div", { class: "tune muted" }, el("span", {}, text), btn);
  if (!e && !S.savedIds.has(d.id)) return row("Save to start learning", null);
  if (e?.mode === "default")
    return row(`Fixed default settings (confidence ${T.default.confidence}, overlap ${T.default.min_overlap})`,
      el("button", { onclick: setMode("auto") }, "Use auto-tuning"));
  if (e?.status === "tuned")
    return row(`Auto-tuned ${new Date(e.updated * 1000).toLocaleDateString("en-GB")}: confidence ${e.confidence}, overlap ${e.min_overlap} · ${Math.round(e.agreement * 100)}% consistent`,
      el("button", { onclick: setMode("default") }, "Reset to default"));
  if (e?.status === "unused") return row("Auto-tuning: desk hardly used yet, keeping default settings", null);
  return row(`Learning: ${e?.data_days ?? 0} of ${T.min_days} full days collected`, null);
}

function renderList() {
  $("#setup-list").replaceChildren(...S.desks.map((d, i) => el("div", { class: "desk-item" },
    el("div", { class: "row" }, el("span", { class: "swatch", style: `background:${COLORS[i % COLORS.length]}` }),
      el("input", { value: d.employee, placeholder: "Employee name", oninput: (e) => { d.employee = e.target.value; S.dirty = true; draw(); } })),
    el("div", { class: "row" },
      el("input", { value: d.name, placeholder: "Desk name", oninput: (e) => { d.name = e.target.value; S.dirty = true; draw(); } }),
      el("button", { onclick: () => { if (confirm(`Delete ${d.employee || d.name}?`)) { S.desks.splice(i, 1); S.dirty = true; renderList(); draw(); } } }, "Delete")),
    tuningInfo(d)
  )), S.desks.length ? "" : el("p", { class: "muted" }, "No desks yet for this camera."));
}

tabLoaders.setup = loadSetup;
$("#setup-cam").addEventListener("change", (e) => {
  if (S.dirty && !confirm("Discard unsaved changes for this camera?")) { e.target.value = S.cam; return; }
  selectCam(e.target.value);
});
$("#setup-reload").addEventListener("click", loadImage);
$("#setup-tune").addEventListener("click", async () => {
  msg("Tuning…");
  try {
    const r = await api("/api/tuning/run", { method: "POST" });
    S.tuning = await api("/api/tuning"); renderList();
    msg(`Auto-tune done: ${r.tuned} tuned, ${r.learning} still learning.`);
  } catch (e) { msg("Auto-tune failed: " + e.message, true); }
});
$("#setup-undo").addEventListener("click", () => { S.current.pop(); draw(); });
$("#setup-finish").addEventListener("click", finishDesk);
$("#setup-save").addEventListener("click", async () => {
  try {
    await api(`/api/zones/${S.cam}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ desks: S.desks }) });
    S.dirty = false; S.savedIds = new Set(S.desks.map((d) => d.id)); renderList(); msg("Saved ✓");
  } catch (e) { msg("Save failed: " + e.message, true); }
});
window.addEventListener("beforeunload", (e) => { if (S.dirty) e.preventDefault(); });

/* ---------- start ---------- */
refreshStatus().then(loadDaily).catch((e) => $("#day-table").replaceChildren(el("p", { class: "warn" }, e.message)));
