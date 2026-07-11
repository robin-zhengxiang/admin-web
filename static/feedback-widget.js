(function () {
  const btn = document.createElement("button");
  btn.textContent = "反馈";
  btn.style.cssText = `
    position: fixed; right: 20px; bottom: 20px; z-index: 100;
    padding: 10px 16px; border: none; border-radius: 20px;
    background: var(--text-primary, #111); color: var(--surface-1, #fff);
    font-size: 13px; cursor: pointer; box-shadow: 0 2px 10px rgba(0,0,0,0.2);
  `;

  const overlay = document.createElement("div");
  overlay.style.cssText = "display:none;position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:200;";

  const form = document.createElement("form");
  form.style.cssText = `
    position:fixed;right:20px;bottom:70px;z-index:201;width:280px;
    background:var(--surface-1,#fff);border-radius:10px;padding:16px;
    box-shadow:0 4px 20px rgba(0,0,0,0.25);
  `;
  form.innerHTML = `
    <div style="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--text-primary,#111);">反馈一个 bug</div>
    <input name="title" placeholder="标题" required style="width:100%;box-sizing:border-box;padding:6px 8px;margin-bottom:8px;border:1px solid var(--baseline,#ccc);border-radius:6px;">
    <textarea name="description" placeholder="描述一下遇到的问题" required style="width:100%;box-sizing:border-box;height:70px;padding:6px 8px;margin-bottom:8px;border:1px solid var(--baseline,#ccc);border-radius:6px;"></textarea>
    <div style="display:flex;gap:8px;">
      <button type="submit" style="flex:1;padding:7px;border:none;border-radius:6px;background:var(--text-primary,#111);color:var(--surface-1,#fff);cursor:pointer;">提交</button>
      <button type="button" data-cancel style="padding:7px 10px;border:none;border-radius:6px;background:transparent;color:var(--text-secondary,#555);cursor:pointer;">取消</button>
    </div>
    <div id="fb-status" style="font-size:12px;color:var(--muted,#888);margin-top:6px;"></div>
  `;

  function open() {
    overlay.style.display = "block";
    form.style.display = "block";
  }
  function close() {
    overlay.style.display = "none";
    form.style.display = "none";
  }

  btn.addEventListener("click", open);
  overlay.addEventListener("click", close);
  form.querySelector("[data-cancel]").addEventListener("click", close);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const status = form.querySelector("#fb-status");
    status.textContent = "提交中…";
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: form.title.value,
          description: form.description.value,
          page: window.location.pathname,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).error || "提交失败");
      status.textContent = "已提交，谢谢反馈！";
      form.reset();
      setTimeout(close, 1000);
    } catch (err) {
      status.textContent = err.message;
    }
  });

  document.body.appendChild(overlay);
  document.body.appendChild(form);
  document.body.appendChild(btn);
  form.style.display = "none";
})();
