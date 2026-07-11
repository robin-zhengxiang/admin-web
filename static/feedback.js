async function api(path, opts) {
  const res = await fetch(path, opts);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error((await res.json()).error || "request failed");
  return res.json();
}

const STATUS_LABEL = {
  open: "待处理",
  in_progress: "处理中",
  needs_input: "需要澄清",
  resolved: "已修复",
  wontfix: "不处理",
};

async function loadMe() {
  const me = await api("/api/me");
  document.getElementById("whoami").textContent = "当前登录: " + me.username;
}

async function loadTickets() {
  const data = await api("/api/feedback");
  const tbody = document.getElementById("ticket-rows");
  tbody.textContent = "";
  for (const t of data.tickets) {
    const tr = document.createElement("tr");
    tr.addEventListener("click", () => showTicket(t.id));

    const tdStatus = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = STATUS_LABEL[t.status] || t.status;
    tdStatus.appendChild(badge);
    tr.appendChild(tdStatus);

    const tdTitle = document.createElement("td");
    tdTitle.textContent = t.title;
    tr.appendChild(tdTitle);

    const tdOwner = document.createElement("td");
    tdOwner.textContent = t.owner_user;
    tr.appendChild(tdOwner);

    const tdUpdated = document.createElement("td");
    tdUpdated.textContent = (t.updated_at || "").replace("T", " ").slice(0, 16);
    tr.appendChild(tdUpdated);

    tbody.appendChild(tr);
  }
}

async function showTicket(id) {
  const data = await api(`/api/feedback/${id}`);
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
  h2.textContent = data.ticket.title;
  panel.appendChild(h2);

  const meta = document.createElement("div");
  meta.className = "turn meta";
  meta.textContent = `${data.ticket.owner_user} · ${STATUS_LABEL[data.ticket.status] || data.ticket.status}`;
  panel.appendChild(meta);

  for (const m of data.messages) {
    const div = document.createElement("div");
    div.className = "turn";
    const who = document.createElement("div");
    who.className = "meta";
    who.textContent = `${m.role === "agent" ? "🤖 助手" : "用户"} · ${(m.created_at || "").replace("T", " ").slice(0, 16)}`;
    div.appendChild(who);
    const body = document.createElement("div");
    body.style.whiteSpace = "pre-wrap";
    body.textContent = m.content;
    div.appendChild(body);
    panel.appendChild(div);
  }

  const replyBox = document.createElement("textarea");
  replyBox.placeholder = "补充说明 / 回复…";
  replyBox.style.cssText = "width:100%;height:70px;box-sizing:border-box;margin-top:10px;padding:6px 8px;";
  panel.appendChild(replyBox);

  const sendBtn = document.createElement("button");
  sendBtn.textContent = "发送";
  sendBtn.style.marginTop = "6px";
  sendBtn.addEventListener("click", async () => {
    if (!replyBox.value.trim()) return;
    sendBtn.disabled = true;
    try {
      await api(`/api/feedback/${id}/reply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: replyBox.value }),
      });
      await loadTickets();
      await showTicket(id);
    } catch (err) {
      alert(err.message);
    } finally {
      sendBtn.disabled = false;
    }
  });
  panel.appendChild(sendBtn);

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
  await loadTickets();
})();
