(() => {
  const csrf = document.querySelector('input[name="csrf_token"]')?.value || "";
  const editors = document.querySelectorAll(".target-note");
  const emailEditors = document.querySelectorAll(".target-email");
  const selectionInputs = document.querySelectorAll(".target-select");
  const exportButton = document.querySelector("#export-selected");
  const exportDialog = document.querySelector("#export-dialog");
  const copyExport = document.querySelector("#copy-export");
  const downloadExport = document.querySelector("#download-export");
  const importButton = document.querySelector("#show-import");
  const importPanel = document.querySelector("#import-panel");
  const cancelImport = document.querySelector("#cancel-import");
  const emailDialog = document.querySelector("#email-confirm-dialog");
  const emailBefore = document.querySelector("#email-before");
  const emailAfter = document.querySelector("#email-after");
  const confirmEmailChange = document.querySelector("#confirm-email-change");
  const cancelEmailChange = document.querySelector("#cancel-email-change");
  let pendingEmail = null;

  const setStatus = (element, text, kind = "") => {
    if (!element) return;
    element.textContent = text;
    element.dataset.state = kind;
  };

  editors.forEach((editor) => {
    let original = editor.dataset.originalNote ?? editor.value;
    let requestVersion = 0;
    const status = editor.parentElement?.querySelector(".note-status");

    editor.addEventListener("focus", () => {
      original = editor.value;
      setStatus(status, "");
    });

    editor.addEventListener("blur", async () => {
      const value = editor.value;
      if (value === original) return;
      const version = ++requestVersion;
      editor.disabled = true;
      setStatus(status, "保存中…");
      try {
        const body = new URLSearchParams({ note: value, csrf_token: csrf });
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
          setStatus(status, "已保存", "ok");
        }
      } catch (_error) {
        if (version === requestVersion) setStatus(status, "保存失败，请重试", "error");
      } finally {
        editor.disabled = false;
      }
    });
  });

  const updateExportState = () => {
    if (exportButton) exportButton.disabled = !Array.from(selectionInputs).some((input) => input.checked);
  };
  selectionInputs.forEach((input) => input.addEventListener("change", updateExportState));

  const selectedRows = () => Array.from(selectionInputs)
    .filter((input) => input.checked)
    .map((input) => input.closest("tr"))
    .filter(Boolean);

  const exportText = () => {
    const header = ["编号", "隐私邮箱地址", "入库时间", "邮件查看地址", "当前状态"].join("\t");
    const rows = selectedRows().map((row) => {
      const cells = row.querySelectorAll("td");
      const id = cells[1]?.textContent.trim() || "";
      const email = row.querySelector(".target-email")?.value.trim() || "";
      const createdAt = cells[4]?.textContent.trim() || "";
      const link = cells[5]?.querySelector("a")?.href || "";
      const status = cells[6]?.textContent.trim() || "";
      return [id, email, createdAt, link, status].join("\t");
    });
    return [header, ...rows].join("\n");
  };

  const closeExportDialog = () => {
    if (exportDialog?.open) exportDialog.close();
  };
  exportButton?.addEventListener("click", () => exportDialog?.showModal());
  copyExport?.addEventListener("click", async (event) => {
    event.preventDefault();
    try {
      await navigator.clipboard.writeText(exportText());
      closeExportDialog();
    } catch (_error) {
      window.alert("复制失败，请检查浏览器剪贴板权限。");
    }
  });
  downloadExport?.addEventListener("click", (event) => {
    event.preventDefault();
    const blob = new Blob([exportText()], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "private-targets.txt";
    anchor.click();
    URL.revokeObjectURL(url);
    closeExportDialog();
  });

  importButton?.addEventListener("click", () => {
    if (!importPanel) return;
    importPanel.hidden = !importPanel.hidden;
    if (!importPanel.hidden) importPanel.querySelector("textarea")?.focus();
  });
  cancelImport?.addEventListener("click", () => {
    if (importPanel) importPanel.hidden = true;
  });

  const submitEmailChange = async (editor, value, status) => {
    editor.disabled = true;
    setStatus(status, "保存中…");
    try {
      const body = new URLSearchParams({ email_address: value, csrf_token: csrf });
      const response = await fetch(editor.dataset.emailUrl, {
        method: "POST",
        headers: { "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded" },
        credentials: "same-origin",
        body,
      });
      const payload = await response.json();
      if (!response.ok || payload.status !== "ok") throw new Error(payload.error || "save failed");
      editor.value = payload.email_address;
      editor.dataset.originalEmail = payload.email_address;
      setStatus(status, "已保存", "ok");
    } catch (error) {
      editor.value = editor.dataset.originalEmail || editor.value;
      setStatus(status, error.message || "保存失败，请重试", "error");
    } finally {
      editor.disabled = false;
      pendingEmail = null;
    }
  };

  emailEditors.forEach((editor) => {
    editor.addEventListener("focus", () => {
      editor.dataset.editingEmail = editor.value;
      setStatus(editor.parentElement?.querySelector(".email-status"), "");
    });
    editor.addEventListener("blur", () => {
      const before = editor.dataset.editingEmail ?? editor.dataset.originalEmail ?? editor.value;
      const after = editor.value;
      if (before === after) return;
      pendingEmail = { editor, before, after, status: editor.parentElement?.querySelector(".email-status") };
      if (emailBefore) emailBefore.textContent = before;
      if (emailAfter) emailAfter.textContent = after;
      emailDialog?.showModal();
    });
  });

  confirmEmailChange?.addEventListener("click", async (event) => {
    event.preventDefault();
    if (!pendingEmail) return;
    const current = pendingEmail;
    if (emailDialog?.open) emailDialog.close();
    await submitEmailChange(current.editor, current.after, current.status);
  });
  const cancelPendingEmail = () => {
    if (pendingEmail) {
      pendingEmail.editor.value = pendingEmail.before;
      pendingEmail.editor.dataset.originalEmail = pendingEmail.before;
      setStatus(pendingEmail.status, "已取消");
      pendingEmail = null;
    }
    if (emailDialog?.open) emailDialog.close();
  };

  cancelEmailChange?.addEventListener("click", (event) => {
    event.preventDefault();
    cancelPendingEmail();
  });
  emailDialog?.addEventListener("cancel", (event) => {
    event.preventDefault();
    cancelPendingEmail();
  });
})();
