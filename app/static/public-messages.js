(() => {
  const button = document.querySelector("#refresh-messages");
  const content = document.querySelector("#messages-content");
  const captchaHost = document.querySelector("#refresh-captcha-host");
  const interval = document.querySelector("#auto-refresh-interval");
  if (!button || !content || !captchaHost || !interval) return;

  const refreshUrl = button.dataset.refreshUrl;
  let timer = null;

  const setBusy = (busy) => {
    button.disabled = busy;
    button.classList.toggle("is-loading", busy);
    button.setAttribute("aria-busy", busy ? "true" : "false");
    button.querySelector(".refresh-label").textContent = busy ? "读取中…" : "刷新";
  };

  button.addEventListener("click", async () => {
    setBusy(true);
    try {
      const response = await fetch(refreshUrl, {
        method: "POST",
        headers: { "Accept": "application/json" },
        credentials: "same-origin",
      });
      const payload = await response.json();
      if (payload.status === "captcha_required") {
        captchaHost.innerHTML = payload.html;
        captchaHost.hidden = false;
        content.hidden = true;
        captchaHost.querySelector("input")?.focus();
        return;
      }
      if (!response.ok || payload.status !== "ok") {
        throw new Error("refresh failed");
      }
      content.innerHTML = payload.html;
      content.hidden = false;
      captchaHost.hidden = true;
    } catch (_error) {
      const notice = document.createElement("div");
      notice.className = "alert";
      notice.setAttribute("role", "alert");
      notice.textContent = "刷新失败，请稍后重试。";
      content.prepend(notice);
      content.hidden = false;
      captchaHost.hidden = true;
    } finally {
      setBusy(false);
    }
  });

  const scheduleRefresh = () => {
    if (timer !== null) window.clearTimeout(timer);
    const seconds = Number.parseInt(interval.value, 10) || 0;
    if (seconds <= 0) return;
    timer = window.setTimeout(() => {
      timer = null;
      if (!button.disabled) button.click();
      scheduleRefresh();
    }, seconds * 1000);
  };

  interval.addEventListener("change", scheduleRefresh);
  scheduleRefresh();
})();
