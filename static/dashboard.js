const state = { range: "30d", user: "" };
const charts = {};

function fmtNum(n) {
  return new Intl.NumberFormat("en-US").format(Math.round(n || 0));
}
function fmtCost(n) {
  return "$" + (n || 0).toFixed(2);
}
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

async function api(path) {
  const res = await fetch(path);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error((await res.json()).error || "request failed");
  return res.json();
}

async function loadMe() {
  const me = await api("/api/me");
  document.getElementById("whoami").textContent = "当前登录: " + me.username;
}

async function loadUsers() {
  const data = await api("/api/users");
  const sel = document.getElementById("user-select");
  for (const u of data.users) {
    const opt = document.createElement("option");
    opt.value = u.username;
    opt.textContent = u.username;
    sel.appendChild(opt);
  }
}

function renderStats(overview) {
  const t = overview.totals;
  const tiles = [
    { label: "原始 token 总量", value: fmtNum(t.input + t.output + t.cache_read + t.cache_creation) },
    { label: "估算花费", value: fmtCost(t.cost_usd) },
    { label: "input tokens", value: fmtNum(t.input) },
    { label: "output tokens", value: fmtNum(t.output) },
    { label: "cache read tokens", value: fmtNum(t.cache_read) },
    { label: "cache creation tokens", value: fmtNum(t.cache_creation) },
  ];
  const row = document.getElementById("stat-row");
  row.textContent = "";
  for (const tile of tiles) {
    const div = document.createElement("div");
    div.className = "stat-tile";
    const label = document.createElement("div");
    label.className = "label";
    label.textContent = tile.label;
    const value = document.createElement("div");
    value.className = "value";
    value.textContent = tile.value;
    div.appendChild(label);
    div.appendChild(value);
    row.appendChild(div);
  }
}

function destroyChart(key) {
  if (charts[key]) {
    charts[key].destroy();
    delete charts[key];
  }
}

function renderDailyChart(daily) {
  destroyChart("daily");
  const ctx = document.getElementById("daily-chart");
  const labels = daily.map((d) => d.day);
  const series = [
    { key: "input", label: "input", color: cssVar("--series-1") },
    { key: "output", label: "output", color: cssVar("--series-2") },
    { key: "cache_read", label: "cache read", color: cssVar("--series-3") },
    { key: "cache_creation", label: "cache creation", color: cssVar("--series-4") },
  ];
  charts.daily = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: series.map((s) => ({
        label: s.label,
        data: daily.map((d) => d[s.key]),
        backgroundColor: s.color,
        borderRadius: 2,
        stack: "tokens",
      })),
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { stacked: true, grid: { color: cssVar("--grid") }, ticks: { color: cssVar("--muted") } },
        y: { stacked: true, grid: { color: cssVar("--grid") }, ticks: { color: cssVar("--muted") } },
      },
      plugins: {
        legend: { position: "bottom", labels: { color: cssVar("--text-secondary") } },
        tooltip: {
          callbacks: {
            label: (item) => `${item.dataset.label}: ${fmtNum(item.raw)}`,
          },
        },
      },
    },
  });
}

function renderBarChart(key, canvasId, rows, labelField, color) {
  destroyChart(key);
  const ctx = document.getElementById(canvasId);
  charts[key] = new Chart(ctx, {
    type: "bar",
    data: {
      labels: rows.map((r) => r[labelField] || "(unknown)"),
      datasets: [{ label: "估算花费 (USD)", data: rows.map((r) => r.cost_usd), backgroundColor: color, borderRadius: 3 }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      scales: {
        x: { grid: { color: cssVar("--grid") }, ticks: { color: cssVar("--muted") } },
        y: { grid: { display: false }, ticks: { color: cssVar("--text-secondary") } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (item) => fmtCost(item.raw) } },
      },
    },
  });
}

let sessionsCache = [];
let sortKey = "time";

function renderSessions() {
  const tbody = document.getElementById("session-rows");
  tbody.textContent = "";
  const rows = [...sessionsCache].sort((a, b) => {
    if (sortKey === "cost") return b.cost_usd - a.cost_usd;
    return (b.last_ts || "").localeCompare(a.last_ts || "");
  });
  for (const s of rows) {
    const tr = document.createElement("tr");
    tr.addEventListener("click", () => showDetail(s.session_id));

    const tdTime = document.createElement("td");
    tdTime.textContent = (s.last_ts || "").replace("T", " ").slice(0, 16);
    tr.appendChild(tdTime);

    const tdTitle = document.createElement("td");
    tdTitle.textContent = s.title;
    tr.appendChild(tdTitle);

    const tdUser = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = s.owner_user;
    tdUser.appendChild(badge);
    tr.appendChild(tdUser);

    const tdProject = document.createElement("td");
    tdProject.textContent = s.project || "";
    tdProject.title = s.project || "";
    tr.appendChild(tdProject);

    const tdCost = document.createElement("td");
    tdCost.className = "num";
    tdCost.textContent = fmtCost(s.cost_usd);
    tr.appendChild(tdCost);

    tbody.appendChild(tr);
  }
}

document.querySelectorAll("th[data-sort]").forEach((th) => {
  th.addEventListener("click", () => {
    document.querySelectorAll("th[data-sort]").forEach((t) => t.classList.remove("active"));
    th.classList.add("active");
    sortKey = th.dataset.sort;
    renderSessions();
  });
});

async function showDetail(sessionId) {
  const detail = await api(`/api/sessions/${sessionId}`);
  const panel = document.getElementById("detail-panel");
  const overlay = document.getElementById("detail-overlay");
  panel.textContent = "";

  const close = document.createElement("span");
  close.className = "close-btn";
  close.textContent = "✕";
  close.addEventListener("click", hideDetail);
  panel.appendChild(close);

  const h2 = document.createElement("h2");
  h2.textContent = detail.title;
  panel.appendChild(h2);

  const meta = document.createElement("div");
  meta.className = "turn meta";
  meta.textContent = `${detail.owner_user} · ${detail.project || ""} · ${detail.main_thread.length} 轮`;
  panel.appendChild(meta);

  const renderTurn = (t) => {
    const div = document.createElement("div");
    div.className = "turn";
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${t.ts} · ${t.model} · ${fmtNum(t.input + t.output + t.cache_read + t.cache_creation)} tok · ${fmtCost(t.cost_usd)}`;
    div.appendChild(meta);
    if (t.tools.length) {
      const tools = document.createElement("div");
      tools.className = "tools";
      tools.textContent = "工具: " + t.tools.join(", ");
      div.appendChild(tools);
    }
    if (t.text_preview) {
      const preview = document.createElement("div");
      preview.textContent = t.text_preview;
      div.appendChild(preview);
    }
    return div;
  };

  for (const t of detail.main_thread) panel.appendChild(renderTurn(t));

  if (detail.sidechain.length) {
    const section = document.createElement("div");
    section.className = "sidechain-section";
    const h3 = document.createElement("h2");
    h3.textContent = `子任务 (${detail.sidechain.length} 轮)`;
    section.appendChild(h3);
    for (const t of detail.sidechain) section.appendChild(renderTurn(t));
    panel.appendChild(section);
  }

  panel.style.display = "block";
  overlay.style.display = "block";
}

function hideDetail() {
  document.getElementById("detail-panel").style.display = "none";
  document.getElementById("detail-overlay").style.display = "none";
}
document.getElementById("detail-overlay").addEventListener("click", hideDetail);

async function refresh() {
  const qs = `range=${state.range}` + (state.user ? `&user=${encodeURIComponent(state.user)}` : "");
  const [overview, sessions] = await Promise.all([
    api(`/api/overview?${qs}`),
    api(`/api/sessions?${qs}`),
  ]);
  renderStats(overview);
  renderDailyChart(overview.daily);
  renderBarChart("model", "model-chart", overview.by_model, "model", cssVar("--series-1"));
  renderBarChart("project", "project-chart", overview.by_project, "project", cssVar("--series-1"));
  renderBarChart("user", "user-chart", overview.by_user, "owner_user", cssVar("--series-1"));
  sessionsCache = sessions.sessions;
  renderSessions();
}

document.getElementById("range-select").addEventListener("change", (e) => {
  state.range = e.target.value;
  refresh();
});
document.getElementById("user-select").addEventListener("change", (e) => {
  state.user = e.target.value;
  refresh();
});
document.getElementById("logout").addEventListener("click", async (e) => {
  e.preventDefault();
  await fetch("/api/logout", { method: "POST" });
  window.location.href = "/login";
});

(async function init() {
  await loadMe();
  await loadUsers();
  await refresh();
})();
