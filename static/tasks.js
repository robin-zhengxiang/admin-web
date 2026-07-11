let currentUser = null;
let tasksCache = [];

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error((await res.json()).error || "request failed");
  return res.json();
}

async function loadMe() {
  const me = await api("/api/me");
  currentUser = me.username;
  document.getElementById("whoami").textContent = "当前登录: " + me.username;
}

function statusLabel(status, pid) {
  if (status === "running") return `● 运行中 (pid ${pid})`;
  if (status === "enabled") return "● 已启用·空闲";
  return "○ 已禁用";
}

function statusBadgeClass(status) {
  if (status === "running") return "badge status-running";
  if (status === "enabled") return "badge status-enabled";
  return "badge status-disabled";
}

async function loadTasks() {
  const data = await api("/api/tasks");
  tasksCache = data.tasks;
  const tbody = document.getElementById("task-rows");
  tbody.textContent = "";

  for (const t of tasksCache) {
    const tr = document.createElement("tr");
    tr.addEventListener("click", () => openTaskDrawer(t));

    const tdName = document.createElement("td");
    tdName.textContent = t.name;
    if (t.desc) tdName.title = t.desc;
    tr.appendChild(tdName);

    const tdUser = document.createElement("td");
    const userBadge = document.createElement("span");
    userBadge.className = "badge";
    userBadge.textContent = t.owner_user;
    tdUser.appendChild(userBadge);
    tr.appendChild(tdUser);

    const tdStatus = document.createElement("td");
    const statusBadge = document.createElement("span");
    statusBadge.className = statusBadgeClass(t.status);
    statusBadge.textContent = statusLabel(t.status, t.pid);
    tdStatus.appendChild(statusBadge);
    tr.appendChild(tdStatus);

    const tdType = document.createElement("td");
    tdType.textContent = t.type;
    tr.appendChild(tdType);

    const tdSchedule = document.createElement("td");
    tdSchedule.className = "mono";
    tdSchedule.textContent = t.cron || (t.schedule ? "自定义（见详情）" : "—");
    tr.appendChild(tdSchedule);

    tbody.appendChild(tr);
  }
}

async function openTaskDrawer(t) {
  const mine = t.owner_user === currentUser;
  const panel = document.getElementById("detail-panel");
  const overlay = document.getElementById("detail-overlay");
  panel.textContent = "";

  const close = document.createElement("span");
  close.className = "close-btn";
  close.textContent = "✕";
  close.addEventListener("click", closeDrawer);
  panel.appendChild(close);

  const h2 = document.createElement("h2");
  h2.textContent = `${t.owner_user} / ${t.name}`;
  panel.appendChild(h2);

  if (t.desc) {
    const desc = document.createElement("p");
    desc.textContent = t.desc;
    panel.appendChild(desc);
  }

  const statusLine = document.createElement("p");
  const statusBadge = document.createElement("span");
  statusBadge.className = statusBadgeClass(t.status);
  statusBadge.textContent = statusLabel(t.status, t.pid);
  statusLine.append(statusBadge, ` · 类型: ${t.type}`);
  panel.appendChild(statusLine);

  if (!mine) {
    const note = document.createElement("p");
    note.className = "readonly-note";
    note.textContent = `需要以 ${t.owner_user} 身份登录才能操作或改计划`;
    panel.appendChild(note);
  }

  const actionRow = document.createElement("div");
  actionRow.className = "action-row";
  const actionErr = document.createElement("span");
  actionErr.className = "error-text";

  const isEnabled = t.status !== "disabled";
  const toggleBtn = document.createElement("button");
  toggleBtn.textContent = isEnabled ? "禁用" : "启用";
  toggleBtn.disabled = !mine;
  toggleBtn.addEventListener("click", async () => {
    toggleBtn.disabled = true;
    actionErr.textContent = "";
    try {
      await api(`/api/tasks/${t.owner_user}/${encodeURIComponent(t.label)}/${isEnabled ? "disable" : "enable"}`, { method: "POST" });
      await loadTasks();
      closeDrawer();
    } catch (err) {
      actionErr.textContent = err.message;
      toggleBtn.disabled = false;
    }
  });
  actionRow.appendChild(toggleBtn);

  if (t.type === "scheduled") {
    const runBtn = document.createElement("button");
    runBtn.textContent = "立即触发";
    runBtn.disabled = !mine || !isEnabled;
    runBtn.addEventListener("click", async () => {
      runBtn.disabled = true;
      actionErr.textContent = "";
      try {
        await api(`/api/tasks/${t.owner_user}/${encodeURIComponent(t.label)}/run`, { method: "POST" });
        runBtn.textContent = "已触发";
      } catch (err) {
        actionErr.textContent = err.message;
      } finally {
        runBtn.disabled = false;
      }
    });
    actionRow.appendChild(runBtn);
  }
  panel.appendChild(actionRow);
  panel.appendChild(actionErr);

  if (t.schedule) {
    const scheduleLabel = document.createElement("label");
    scheduleLabel.textContent = "计划时间（crontab：分 时 日 月 周）";
    panel.appendChild(scheduleLabel);

    const fieldRow = document.createElement("div");
    fieldRow.className = "field-row";
    const cronInput = document.createElement("input");
    cronInput.type = "text";
    cronInput.className = "mono";
    cronInput.value = t.cron || "";
    cronInput.placeholder = "0 2 * * *";
    cronInput.disabled = !mine;
    const saveBtn = document.createElement("button");
    saveBtn.textContent = "保存计划";
    saveBtn.disabled = !mine;
    const cronErr = document.createElement("span");
    cronErr.className = "error-text";
    saveBtn.addEventListener("click", async () => {
      saveBtn.disabled = true;
      cronErr.textContent = "";
      try {
        await api(`/api/tasks/${t.owner_user}/${encodeURIComponent(t.label)}/schedule`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cron: cronInput.value.trim() }),
        });
        saveBtn.textContent = "已保存";
        await loadTasks();
      } catch (err) {
        cronErr.textContent = err.message;
        saveBtn.textContent = "保存计划";
      } finally {
        saveBtn.disabled = !mine;
      }
    });
    fieldRow.append(cronInput, saveBtn);
    panel.appendChild(fieldRow);
    panel.appendChild(cronErr);
    if (!t.cron) {
      const compoundNote = document.createElement("p");
      compoundNote.className = "readonly-note";
      compoundNote.textContent = "当前是复合计划（多条触发规则），保存会用上面的表达式整体替换。";
      panel.appendChild(compoundNote);
    }
  }

  const logsHeading = document.createElement("h2");
  logsHeading.textContent = "日志";
  logsHeading.style.marginTop = "1.5rem";
  panel.appendChild(logsHeading);

  const pre = document.createElement("pre");
  pre.className = "mono";
  pre.style.whiteSpace = "pre-wrap";
  panel.appendChild(pre);

  const refreshBtn = document.createElement("button");
  refreshBtn.textContent = "刷新日志";
  panel.appendChild(refreshBtn);

  async function loadLogs() {
    const data = await api(`/api/tasks/${t.owner_user}/${encodeURIComponent(t.label)}/logs?n=100`);
    pre.textContent = data.lines && data.lines.length ? data.lines.join("\n") : (data.note || "(空)");
  }
  refreshBtn.addEventListener("click", loadLogs);
  await loadLogs();

  panel.style.display = "block";
  overlay.style.display = "block";
}

function closeDrawer() {
  document.getElementById("detail-panel").style.display = "none";
  document.getElementById("detail-overlay").style.display = "none";
}

document.getElementById("detail-overlay").addEventListener("click", closeDrawer);
document.getElementById("logout").addEventListener("click", async (e) => {
  e.preventDefault();
  await fetch("/api/logout", { method: "POST" });
  window.location.href = "/login";
});

(async function init() {
  await loadMe();
  await loadTasks();
})();
