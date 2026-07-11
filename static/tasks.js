let currentUser = null;

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

function pad2(n) {
  return String(n).padStart(2, "0");
}

async function loadTasks() {
  const data = await api("/api/tasks");
  const tbody = document.getElementById("task-rows");
  tbody.textContent = "";

  for (const t of data.tasks) {
    const mine = t.owner_user === currentUser;
    const tr = document.createElement("tr");

    const tdName = document.createElement("td");
    tdName.textContent = t.name;
    if (t.desc) tdName.title = t.desc;
    tr.appendChild(tdName);

    const tdUser = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = t.owner_user;
    tdUser.appendChild(badge);
    tr.appendChild(tdUser);

    const tdStatus = document.createElement("td");
    tdStatus.textContent = statusLabel(t.status, t.pid);
    tr.appendChild(tdStatus);

    const tdType = document.createElement("td");
    tdType.textContent = t.type;
    tr.appendChild(tdType);

    const tdSchedule = document.createElement("td");
    if (t.schedule) {
      if (mine) {
        const cronInput = document.createElement("input");
        cronInput.type = "text";
        cronInput.value = t.cron || "";
        cronInput.placeholder = "分 时 日 月 周，如 0 2 * * *";
        cronInput.style.width = "150px";
        cronInput.style.fontFamily = "ui-monospace,monospace";
        const errEl = document.createElement("div");
        errEl.style.cssText = "color:#c0392b;font-size:11px;max-width:220px;";
        const saveBtn = document.createElement("button");
        saveBtn.textContent = "保存";
        saveBtn.addEventListener("click", async () => {
          saveBtn.disabled = true;
          errEl.textContent = "";
          try {
            await api(`/api/tasks/${t.owner_user}/${encodeURIComponent(t.label)}/schedule`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ cron: cronInput.value.trim() }),
            });
            saveBtn.textContent = "已保存";
          } catch (err) {
            errEl.textContent = err.message;
          } finally {
            saveBtn.disabled = false;
          }
        });
        tdSchedule.append(cronInput, saveBtn, errEl);
      } else {
        tdSchedule.textContent = t.cron || `${pad2(t.schedule.Hour ?? 0)}:${pad2(t.schedule.Minute ?? 0)}`;
      }
    } else {
      tdSchedule.textContent = "—";
    }
    tr.appendChild(tdSchedule);

    const tdActions = document.createElement("td");
    const toggleBtn = document.createElement("button");
    const isEnabled = t.status !== "disabled";
    toggleBtn.textContent = isEnabled ? "禁用" : "启用";
    toggleBtn.disabled = !mine;
    if (!mine) toggleBtn.title = `需要以 ${t.owner_user} 身份登录才能操作`;
    toggleBtn.addEventListener("click", async () => {
      toggleBtn.disabled = true;
      try {
        await api(`/api/tasks/${t.owner_user}/${encodeURIComponent(t.label)}/${isEnabled ? "disable" : "enable"}`, { method: "POST" });
        await loadTasks();
      } catch (err) {
        alert(err.message);
        toggleBtn.disabled = false;
      }
    });
    tdActions.appendChild(toggleBtn);

    if (t.type === "scheduled") {
      const runBtn = document.createElement("button");
      runBtn.textContent = "立即触发";
      runBtn.disabled = !mine || !isEnabled;
      runBtn.style.marginLeft = "6px";
      runBtn.addEventListener("click", async () => {
        runBtn.disabled = true;
        try {
          await api(`/api/tasks/${t.owner_user}/${encodeURIComponent(t.label)}/run`, { method: "POST" });
          runBtn.textContent = "已触发";
        } catch (err) {
          alert(err.message);
        } finally {
          runBtn.disabled = false;
        }
      });
      tdActions.appendChild(runBtn);
    }

    const logBtn = document.createElement("button");
    logBtn.textContent = "看日志";
    logBtn.style.marginLeft = "6px";
    logBtn.addEventListener("click", () => showLogs(t));
    tdActions.appendChild(logBtn);

    tr.appendChild(tdActions);
    tbody.appendChild(tr);
  }
}

async function showLogs(t) {
  const panel = document.getElementById("detail-panel");
  const overlay = document.getElementById("detail-overlay");
  panel.textContent = "";

  const close = document.createElement("span");
  close.className = "close-btn";
  close.textContent = "✕";
  close.addEventListener("click", () => {
    panel.style.display = "none";
    overlay.style.display = "none";
  });
  panel.appendChild(close);

  const h2 = document.createElement("h2");
  h2.textContent = `${t.owner_user} / ${t.name} · 日志`;
  panel.appendChild(h2);

  const pre = document.createElement("pre");
  pre.style.cssText = "white-space:pre-wrap;font-size:12px;font-family:ui-monospace,monospace;";
  panel.appendChild(pre);

  const refreshBtn = document.createElement("button");
  refreshBtn.textContent = "刷新";
  refreshBtn.addEventListener("click", load);
  panel.appendChild(refreshBtn);

  async function load() {
    const data = await api(`/api/tasks/${t.owner_user}/${encodeURIComponent(t.label)}/logs?n=100`);
    pre.textContent = data.lines && data.lines.length ? data.lines.join("\n") : (data.note || "(空)");
  }
  await load();

  panel.style.display = "block";
  overlay.style.display = "block";
}

document.getElementById("detail-overlay").addEventListener("click", () => {
  document.getElementById("detail-panel").style.display = "none";
  document.getElementById("detail-overlay").style.display = "none";
});
document.getElementById("logout").addEventListener("click", async (e) => {
  e.preventDefault();
  await fetch("/api/logout", { method: "POST" });
  window.location.href = "/login";
});

(async function init() {
  await loadMe();
  await loadTasks();
})();
