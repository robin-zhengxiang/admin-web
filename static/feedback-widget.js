(function () {
  const btn = document.createElement("button");
  btn.textContent = "反馈";
  btn.style.cssText = `
    position: fixed; right: 20px; bottom: 20px; z-index: 100;
    width: auto; padding: 0.6rem 1.1rem; border-radius: 20px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.25);
  `;

  const overlay = document.createElement("div");
  overlay.style.cssText = "display:none;position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:200;";

  const form = document.createElement("form");
  form.style.cssText = `
    display:none;position:fixed;right:20px;bottom:76px;z-index:201;width:300px;
    background:var(--pico-background-color);border-radius:var(--pico-border-radius);
    padding:1rem;box-shadow:0 4px 20px rgba(0,0,0,0.3);
  `;
  form.innerHTML = `
    <strong style="display:block;margin-bottom:0.6rem;">反馈一个 bug</strong>
    <input name="title" placeholder="标题" required>
    <textarea name="description" placeholder="描述一下遇到的问题" required rows="4"></textarea>
    <div style="display:flex;gap:0.5rem;">
      <button type="submit" style="width:auto;flex:1;margin:0;">提交</button>
      <button type="button" data-cancel class="secondary" style="width:auto;margin:0;">取消</button>
    </div>
    <small id="fb-status"></small>
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
})();
