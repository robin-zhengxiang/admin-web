const STATES = ["default", "off", "user-invocable-only", "name-only"];
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

function makeStateSelect(skill) {
  const select = document.createElement("select");
  const mine = skill.owner_user === currentUser;
  for (const s of STATES) {
    const opt = document.createElement("option");
    opt.value = s;
    opt.textContent = s;
    if (s === skill.state) opt.selected = true;
    select.appendChild(opt);
  }
  select.disabled = !mine;
  if (!mine) select.title = `需要以 ${skill.owner_user} 身份登录才能修改`;
  select.addEventListener("change", async () => {
    select.disabled = true;
    try {
      await api(`/api/skills/${skill.owner_user}/${skill.name}/state`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: select.value }),
      });
    } catch (err) {
      alert(err.message);
    } finally {
      select.disabled = !mine;
    }
  });
  return select;
}

async function loadSkills() {
  const data = await api("/api/skills");
  const tbody = document.getElementById("skill-rows");
  tbody.textContent = "";
  for (const skill of data.skills) {
    const mine = skill.owner_user === currentUser;
    const tr = document.createElement("tr");

    const tdName = document.createElement("td");
    tdName.textContent = skill.name;
    tr.appendChild(tdName);

    const tdUser = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = skill.owner_user;
    tdUser.appendChild(badge);
    tr.appendChild(tdUser);

    const tdDesc = document.createElement("td");
    tdDesc.textContent = skill.description.length > 80 ? skill.description.slice(0, 80) + "…" : skill.description;
    tdDesc.title = skill.description;
    tr.appendChild(tdDesc);

    const tdState = document.createElement("td");
    tdState.appendChild(makeStateSelect(skill));
    tr.appendChild(tdState);

    const tdEdit = document.createElement("td");
    const btn = document.createElement("button");
    btn.textContent = mine ? "编辑" : "查看";
    btn.addEventListener("click", () => openEditor(skill, mine));
    tdEdit.appendChild(btn);
    tr.appendChild(tdEdit);

    tbody.appendChild(tr);
  }
}

async function openEditor(skill, editable) {
  const data = await api(`/api/skills/${skill.owner_user}/${skill.name}/content`);
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
  h2.textContent = `${skill.owner_user} / ${skill.name}`;
  panel.appendChild(h2);

  const textarea = document.createElement("textarea");
  textarea.value = data.content;
  textarea.readOnly = !editable;
  textarea.style.cssText = "width:100%;height:60vh;font-family:ui-monospace,monospace;font-size:12px;box-sizing:border-box;";
  panel.appendChild(textarea);

  if (editable) {
    const saveBtn = document.createElement("button");
    saveBtn.textContent = "保存";
    saveBtn.style.marginTop = "10px";
    saveBtn.addEventListener("click", async () => {
      saveBtn.disabled = true;
      saveBtn.textContent = "保存中…";
      try {
        await api(`/api/skills/${skill.owner_user}/${skill.name}/content`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: textarea.value }),
        });
        saveBtn.textContent = "已保存";
      } catch (err) {
        alert(err.message);
        saveBtn.textContent = "保存";
      } finally {
        saveBtn.disabled = false;
      }
    });
    panel.appendChild(saveBtn);
  }

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
  await loadSkills();
})();
