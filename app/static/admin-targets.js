(() => {
  const csrf = document.querySelector('input[name="csrf_token"]')?.value;
  const editors = document.querySelectorAll(".target-note");

  editors.forEach((editor) => {
    let original = editor.dataset.originalNote ?? editor.value;
    let requestVersion = 0;
    const status = editor.parentElement?.querySelector(".note-status");

    const setStatus = (text, kind = "") => {
      if (!status) return;
      status.textContent = text;
      status.dataset.state = kind;
    };

    editor.addEventListener("focus", () => {
      original = editor.value;
      setStatus("");
    });

    editor.addEventListener("blur", async () => {
      const value = editor.value;
      if (value === original) return;

      const version = ++requestVersion;
      editor.disabled = true;
      setStatus("保存中…");
      try {
        const body = new URLSearchParams({ note: value, csrf_token: csrf || "" });
        const response = await fetch(editor.dataset.noteUrl, {
          method: "POST",
          headers: { "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded" },
          credentials: "same-origin",
          body,
        });
        const payload = await response.json();
        if (!response.ok || payload.status !== "ok") throw new Error(payload.error || "save failed");
        if (version === requestVersion) {
          original = payload.note || "";
          editor.dataset.originalNote = original;
          editor.value = original;
          setStatus("已保存", "ok");
        }
      } catch (_error) {
        if (version === requestVersion) setStatus("保存失败，请重试", "error");
      } finally {
        editor.disabled = false;
      }
    });
  });
})();
