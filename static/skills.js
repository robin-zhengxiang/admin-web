const STATES = ["default", "off", "user-invocable-only", "name-only"];
let currentUser = null;
let skillsCache = [];

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

async function loadSkills() {
  const data = await api("/api/skills");
  skillsCache = data.skills;
  const tbody = document.getElementById("skill-rows");
  tbody.textContent = "";
  for (const skill of skillsCache) {
    const tr = document.createElement("tr");
    tr.addEventListener("click", () => openSkillDrawer(skill));

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
    const stateBadge = document.createElement("span");
    stateBadge.className = "badge";
    stateBadge.textContent = skill.state;
    tdState.appendChild(stateBadge);
    tr.appendChild(tdState);

    tbody.appendChild(tr);
  }
}

async function openSkillDrawer(skill) {
  const mine = skill.owner_user === currentUser;
  const data = await api(`/api/skills/${skill.owner_user}/${skill.name}/content`);
  const panel = document.getElementById("detail-panel");
  const overlay = document.getElementById("detail-overlay");
  panel.textContent = "";

  const close = document.createElement("span");
  close.className = "close-btn";
  close.textContent = "✕";
  close.addEventListener("click", closeDrawer);
  panel.appendChild(close);

  const h2 = document.createElement("h2");
  h2.textContent = `${skill.owner_user} / ${skill.name}`;
  panel.appendChild(h2);

  const desc = document.createElement("p");
  desc.textContent = skill.description;
  panel.appendChild(desc);

  const fieldRow = document.createElement("div");
  fieldRow.className = "field-row";
  const stateLabel = document.createElement("label");
  stateLabel.textContent = "状态：";
  const select = document.createElement("select");
  for (const s of STATES) {
    const opt = document.createElement("option");
    opt.value = s;
    opt.textContent = s;
    if (s === skill.state) opt.selected = true;
    select.appendChild(opt);
  }
  select.disabled = !mine;
  const stateErr = document.createElement("span");
  stateErr.className = "error-text";
  select.addEventListener("change", async () => {
    select.disabled = true;
    stateErr.textContent = "";
    try {
      await api(`/api/skills/${skill.owner_user}/${skill.name}/state`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: select.value }),
      });
      await loadSkills();
    } catch (err) {
      stateErr.textContent = err.message;
    } finally {
      select.disabled = !mine;
    }
  });
  fieldRow.append(stateLabel, select, stateErr);
  panel.appendChild(fieldRow);

  if (!mine) {
    const note = document.createElement("p");
    note.className = "readonly-note";
    note.textContent = `需要以 ${skill.owner_user} 身份登录才能修改状态或编辑内容`;
    panel.appendChild(note);
  }

  const textarea = document.createElement("textarea");
  textarea.value = data.content;
  textarea.readOnly = !mine;
  textarea.rows = 24;
  textarea.style.width = "100%";
  panel.appendChild(textarea);

  if (mine) {
    const saveBtn = document.createElement("button");
    saveBtn.textContent = "保存内容";
    saveBtn.style.marginTop = "0.75rem";
    const saveErr = document.createElement("span");
    saveErr.className = "error-text";
    saveErr.style.marginLeft = "0.6rem";
    saveBtn.addEventListener("click", async () => {
      saveBtn.disabled = true;
      saveBtn.textContent = "保存中…";
      saveErr.textContent = "";
      try {
        await api(`/api/skills/${skill.owner_user}/${skill.name}/content`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: textarea.value }),
        });
        saveBtn.textContent = "已保存";
      } catch (err) {
        saveErr.textContent = err.message;
        saveBtn.textContent = "保存内容";
      } finally {
        saveBtn.disabled = false;
      }
    });
    panel.append(saveBtn, saveErr);
  }

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
  await loadSkills();
})();
