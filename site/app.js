"use strict";

// Formularz nie generuje kalendarza — tylko liczy skrót wyboru (tak samo jak
// canonical() w generate.py) i wskazuje gotowy plik c/<skrót>.ics.

const WEEKDAYS = ["niedz.", "pon.", "wt.", "śr.", "czw.", "pt.", "sob."];
const $ = (id) => document.getElementById(id);

let data = null;
const state = { c: null, gl: null, picks: {} };

function storageKey() {
  return `calendar-pk:${data.hashVersion}:sem${data.semester}`;
}

function loadSaved() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey()) || "null");
    if (saved && typeof saved === "object") return saved;
  } catch (_) { /* prywatne okno, zablokowane dane — trudno */ }
  return null;
}

function save() {
  try { localStorage.setItem(storageKey(), JSON.stringify(state)); } catch (_) {}
}

function plural(n, one, few, many) {
  if (n === 1) return one;
  const d = n % 10, t = n % 100;
  return d >= 2 && d <= 4 && (t < 12 || t > 14) ? few : many;
}

function formatDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  return `${WEEKDAYS[date.getDay()]} ${d}.${String(m).padStart(2, "0")}`;
}

function radio(name, value, text, checked) {
  const label = document.createElement("label");
  const input = document.createElement("input");
  input.type = "radio";
  input.name = name;
  input.value = value;
  input.checked = checked;
  const span = document.createElement("span");
  span.textContent = text;
  label.append(input, span);
  return label;
}

function glChoices() {
  if (state.c && data.cGroups[state.c]) return data.cGroups[state.c];
  return Array.from({ length: data.glCount }, (_, i) => i + 1);
}

function renderGroups() {
  const cBox = $("cGroups");
  const cKeys = Object.keys(data.cGroups);
  $("cField").hidden = cKeys.length === 0;
  cBox.replaceChildren(...cKeys.map((c) => radio("c", c, c, String(state.c) === c)));

  const gls = glChoices();
  if (!gls.includes(state.gl)) state.gl = gls[0];
  $("glGroups").replaceChildren(...gls.map((g) => radio("gl", g, String(g), state.gl === g)));
}

function renderElectives() {
  const box = $("electives");
  box.replaceChildren(...data.electives.map((e) => {
    const row = document.createElement("div");
    row.className = "elective";
    const text = document.createElement("div");
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = e.name;
    const teachers = document.createElement("div");
    teachers.className = "teachers";
    teachers.textContent = e.teachers.join(", ");
    text.append(name, teachers);

    const seg = document.createElement("div");
    seg.className = "segmented";
    seg.setAttribute("role", "radiogroup");
    seg.setAttribute("aria-label", e.name);
    const current = state.picks[e.key] || "";
    seg.append(radio(`e-${e.key}`, "", "Nie", current === ""));
    for (const opt of e.options) {
      seg.append(radio(`e-${e.key}`, opt, opt === "tak" ? "Tak" : opt, current === opt));
    }
    row.append(text, seg);
    return row;
  }));
}

function canonical() {
  const parts = Object.keys(state.picks)
    .filter((k) => state.picks[k])
    .sort()
    .map((k) => `${k}=${state.picks[k]}`);
  return `${data.hashVersion}|sem${data.semester}|gl${state.gl}|${parts.join(",")}`;
}

async function selectionHash() {
  const bytes = new TextEncoder().encode(canonical());
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, data.hashLength);
}

function mySessions() {
  return data.sessions.filter((s) =>
    s.elective ? s.labels.includes(state.picks[s.elective]) : s.gl.includes(state.gl));
}

function findConflicts(sessions) {
  const out = [];
  for (let i = 0; i < sessions.length; i++) {
    for (let j = i + 1; j < sessions.length && sessions[j].date === sessions[i].date; j++) {
      if (sessions[j].start < sessions[i].end) out.push([sessions[i], sessions[j]]);
    }
  }
  return out;
}

function describe(s) {
  return `${s.subject} — ${s.activity}${s.group ? ` (${s.group})` : ""}`;
}

function renderResult(hash) {
  const sessions = mySessions();
  const conflicts = findConflicts(sessions);
  const clashing = new Set(conflicts.flat());
  const days = new Set(sessions.map((s) => s.date));

  const today = new Date().toISOString().slice(0, 10);
  const next = sessions.find((s) => s.date >= today);
  let summary = `${sessions.length} ${plural(sessions.length, "spotkanie", "spotkania", "spotkań")} w ${days.size} ${days.size === 1 ? "dniu" : "dniach"} zjazdowych.`;
  if (next) summary += ` Najbliższe: ${formatDay(next.date)}, ${next.start} — ${next.subject} (${next.activity}).`;
  $("summary").textContent = summary;

  const box = $("conflicts");
  box.hidden = conflicts.length === 0;
  if (conflicts.length) {
    const head = document.createElement("p");
    head.textContent = conflicts.length === 1
      ? "Jedna kolizja w planie przy tym wyborze:"
      : `${conflicts.length} ${plural(conflicts.length, "kolizja", "kolizje", "kolizji")} w planie przy tym wyborze:`;
    const list = document.createElement("ul");
    for (const [a, b] of conflicts) {
      const li = document.createElement("li");
      const sameTime = a.start === b.start && a.end === b.end;
      li.textContent = `${formatDay(a.date)}, ${a.start}–${a.end}: ${describe(a)} × ${describe(b)}` +
        (sameTime ? "" : ` (${b.start}–${b.end})`);
      list.append(li);
    }
    box.replaceChildren(head, list);
  }

  const url = new URL(`c/${hash}.ics`, location.href).href;
  const webcal = url.replace(/^https?:/, "webcal:");
  $("subscribe").href = webcal;
  $("google").href = `https://calendar.google.com/calendar/render?cid=${encodeURIComponent(webcal)}`;
  $("link").value = url;
  $("copied").textContent = "";

  $("previewTitle").textContent = `Lista zajęć (${sessions.length})`;
  const groups = [];
  for (const s of sessions) {
    if (!groups.length || groups[groups.length - 1].date !== s.date) groups.push({ date: s.date, items: [] });
    groups[groups.length - 1].items.push(s);
  }
  $("preview").replaceChildren(...groups.map((g) => {
    const day = document.createElement("div");
    day.className = "day";
    const h = document.createElement("h3");
    h.textContent = formatDay(g.date);
    day.append(h);
    for (const s of g.items) {
      const row = document.createElement("div");
      row.className = clashing.has(s) ? "row clash" : "row";
      const time = document.createElement("span");
      time.className = "time";
      time.textContent = `${s.start}–${s.end}`;
      const what = document.createElement("span");
      const where = document.createElement("span");
      where.className = "where";
      where.textContent = ` · ${s.room}${s.group ? ` · ${s.group}` : ""}`;
      what.append(`${s.subject} — ${s.activity}`, where);
      row.append(time, what);
      day.append(row);
    }
    return day;
  }));
}

async function update() {
  save();
  renderResult(await selectionHash());
}

function onChange(event) {
  const input = event.target;
  if (input.name === "c") {
    state.c = input.value;
    renderGroups();
  } else if (input.name === "gl") {
    state.gl = Number(input.value);
  } else if (input.name.startsWith("e-")) {
    state.picks[input.name.slice(2)] = input.value || null;
  }
  update();
}

async function copyLink() {
  const text = $("link").value;
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    $("link").select();
    document.execCommand("copy");
  }
  $("copied").textContent = "Skopiowano link.";
}

async function init() {
  try {
    const resp = await fetch("options.json", { cache: "no-cache" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    data = await resp.json();
  } catch (err) {
    $("loading").hidden = true;
    $("loadError").hidden = false;
    $("loadError").textContent = `Nie udało się wczytać planu (${err.message}). Odśwież stronę za chwilę.`;
    return;
  }

  $("term").textContent = `Informatyka, studia niestacjonarne · rok ${data.year}, semestr ${data.semester} · ${data.term}`;
  $("saved").textContent = `Stan planu: ${data.savedAt}.`;

  const saved = loadSaved();
  const cKeys = Object.keys(data.cGroups);
  state.c = saved && cKeys.includes(String(saved.c)) ? String(saved.c) : (cKeys[0] || null);
  state.gl = saved && Number.isInteger(saved.gl) ? saved.gl : null;
  for (const e of data.electives) {
    const pick = saved && saved.picks ? saved.picks[e.key] : null;
    state.picks[e.key] = e.options.includes(pick) ? pick : null;
  }

  renderGroups();
  renderElectives();
  $("form").addEventListener("change", onChange);
  $("form").addEventListener("submit", (e) => e.preventDefault());
  $("copy").addEventListener("click", copyLink);
  $("loading").hidden = true;
  $("form").hidden = false;
  await update();
}

init();
