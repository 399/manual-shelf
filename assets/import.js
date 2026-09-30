// 已停用（DISABLED）。本文件保留为源码，但不再由构建发布，首页也不再加载它。
// 资料改为放入 site.json 的 manuals_dir（默认 manuals），每本一个文件夹，运行 build 生成网站与报告。
// Optional local import: the user grants a directory handle; no data is uploaded.
(() => {
  const dialog = document.querySelector("#import-dialog");
  if (!dialog) return;
  if (location.hash === "#getting-started") {
    try { history.replaceState(null, "", location.pathname + location.search); } catch { /* file:// origin may restrict history writes */ }
    window.scrollTo(0, 0);
  }
  const $ = (selector) => dialog.querySelector(selector);
  const fileInput = $("#manual-files");
  const details = $("#manual-details");
  const saveButton = $("#save-manual");
  const error = $("#save-error");
  const result = $("#save-result");
  let step = 1;
  let opener;
  let imported = false;
  if (location.protocol === "file:") {
    $("#ai-project").value = decodeURI(new URL("../", location.href).pathname).replace(/\/$/, "");
  }

  function segment(value, fallback) {
    const safe = value.normalize("NFC").replace(/[\\/:*?"<>|\u0000-\u001f]/g, "-").replace(/^[.\s]+|[.\s]+$/g, "").slice(0, 70);
    return safe || fallback;
  }

  function metadata() {
    return {
      title: $("#manual-title").value.trim(),
      category: $("#manual-category").value.trim(),
      brand: $("#manual-brand").value.trim(),
      model: $("#manual-model").value.trim(),
      aliases: $("#manual-aliases").value.split(/[,，、]/u).map((value) => value.trim()).filter(Boolean),
      tags: $("#manual-tags").value.split(/[,，、]/u).map((value) => value.trim()).filter(Boolean),
    };
  }

  function destination(meta) {
    return [segment(meta.category, "未分类"), segment(meta.brand, "未标品牌"), segment(meta.model || meta.title, "未命名")];
  }

  function setStep(next) {
    step = next;
    dialog.querySelectorAll("[data-step]").forEach((panel) => { panel.hidden = Number(panel.dataset.step) !== next; });
    dialog.querySelectorAll(".flow-progress li").forEach((item, index) => {
      if (index + 1 === next) item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    });
    if (next === 3) $("#target-path").textContent = `manuals/${destination(metadata()).join("/")}/`;
  }

  function promptText() {
    const project = $("#ai-project").value.trim() || "[请填写项目根目录]";
    $("#ai-prompt").value = [
      "我已将说明书作为附件交给你。请阅读内容，帮我整理进本地说明书网站；若不能访问本地文件，请说明，不要声称已完成。",
      "",
      `项目根目录：${project}`,
      "",
      "1. 先检查 manuals/ 里有没有同一设备；阅读附件识别设备信息，不确定的品牌、型号或照片页序先问我，绝不猜测或覆盖现有文件。",
      "2. 每本说明书放在 manuals/<类别>/<品牌>/<型号>/；未知品牌用“未标品牌”，未知型号用清楚的设备名称作目录名。保留附件原件，照片核对顺序后编号 001、002……。尽量沿用已有类别。",
      "3. 在该设备文件夹写 manual.json，保存确认后的信息：title 是简明设备名称（如“空气净化器”，不重复堆品牌型号）；category 是复用的类别；brand 是原件品牌；model 是原件完整型号（保留大小写和连字符）；aliases 是俗称或确认过的其他写法；tags 是少量原件可确认的功能/维护主题。无法确认的 brand/model 留空，aliases/tags 用空数组。该 JSON 是供 Python 读取的源信息，不是搜索索引。",
      "4. 在项目根目录运行 python3 manualsite.py build。它读取 manual.json、提取附件正文，生成 site/ 中的清单、逐页网页和搜索索引；检查构建提醒，打开 site/index.html 验证名称、搜索和原件链接，告诉我仍待确认之处。无法运行时明确告诉我需要手动执行这条命令。",
    ].join("\n");
  }

  function mode(name) {
    dialog.querySelectorAll("[data-mode]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.mode === name)));
    dialog.querySelectorAll("[data-panel]").forEach((panel) => { panel.hidden = panel.dataset.panel !== name; });
    if (name === "ai") { promptText(); $("#ai-project").focus(); }
    else (step === 1 ? fileInput : step === 2 ? $("#manual-title") : saveButton.hidden ? $("#write-fallback") : saveButton).focus();
  }

  function open(name, trigger) {
    opener = trigger;
    if (dialog.showModal) dialog.showModal();
    else dialog.setAttribute("open", "");
    mode(name);
  }

  document.querySelectorAll("[data-open-import]").forEach((button) => button.addEventListener("click", () => open("local", button)));
  document.querySelectorAll("[data-open-ai]").forEach((button) => button.addEventListener("click", () => open("ai", button)));
  $("[data-close]").addEventListener("click", () => {
    if (dialog.close) dialog.close();
    else { dialog.removeAttribute("open"); opener?.focus(); }
  });
  dialog.addEventListener("close", () => {
    if (imported) {
      imported = false;
      details.reset();
      fileInput.value = "";
      $("#file-summary").textContent = "可选 1 个 PDF，或同一本说明书的多张照片。";
      $("#after-save").hidden = true;
      result.hidden = true;
      saveButton.hidden = !window.showDirectoryPicker;
      dialog.querySelector('[data-back="2"]').hidden = false;
      setStep(1);
    }
    opener?.focus();
  });
  dialog.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => mode(button.dataset.mode)));
  dialog.querySelectorAll("[data-back]").forEach((button) => button.addEventListener("click", () => setStep(Number(button.dataset.back))));

  fileInput.addEventListener("change", () => {
    const chosen = [...fileInput.files];
    $("#file-summary").textContent = chosen.length ? chosen.map((file) => file.name).join("、") : "可选 1 个 PDF，或同一本说明书的多张照片。";
    $("#file-error").textContent = "";
    if (chosen.length === 1 && !$("#manual-title").value.trim()) $("#manual-title").value = chosen[0].name.replace(/\.[^.]+$/, "");
  });
  $("[data-next-file]").addEventListener("click", () => {
    const chosen = [...fileInput.files];
    const extensions = chosen.map((file) => file.name.split(".").pop().toLowerCase());
    const images = new Set(["jpg", "jpeg", "png", "webp", "tif", "tiff"]);
    const valid = chosen.length && (chosen.length === 1 && ["pdf", "txt", "md", ...images].includes(extensions[0]) || chosen.length > 1 && extensions.every((ext) => images.has(ext)));
    if (!valid) {
      $("#file-error").textContent = "请选择 1 个 PDF/文字文件，或同一本说明书的多张照片。";
      fileInput.focus();
      return;
    }
    setStep(2);
    $("#manual-title").focus();
  });
  details.addEventListener("submit", (event) => {
    event.preventDefault();
    for (const field of [$("#manual-title"), $("#manual-category")]) {
      field.setCustomValidity(field.value.trim() ? "" : "请填写此项");
      if (!field.reportValidity()) { field.focus(); return; }
    }
    setStep(3);
    (saveButton.hidden ? $("#write-fallback") : saveButton).focus();
  });
  for (const field of [$("#manual-title"), $("#manual-category")]) field.addEventListener("input", () => field.setCustomValidity(""));

  if (!window.showDirectoryPicker) {
    saveButton.hidden = true;
    $("#capability-note").hidden = false;
    $("#write-fallback").hidden = false;
  }
  saveButton.addEventListener("click", async () => {
    if (!window.showDirectoryPicker) return;
    error.textContent = "";
    result.hidden = false;
    result.textContent = "等待选择项目目录…";
    saveButton.disabled = true;
    try {
      // Must run inside this click handler: the directory picker needs a user gesture.
      const project = await window.showDirectoryPicker({mode: "readwrite", id: "manual-shelf-project"});
      await project.getFileHandle("manualsite.py");
      await project.getFileHandle("site.json");
      const root = await project.getDirectoryHandle("manuals");
      const [category, brand, model] = destination(metadata());
      const categoryDir = await root.getDirectoryHandle(category, {create: true});
      const brandDir = await categoryDir.getDirectoryHandle(brand, {create: true});
      try {
        await brandDir.getDirectoryHandle(model);
        throw new Error("该设备目录已存在。请先检查是否重复，或修改设备名称/型号；没有覆盖任何文件。");
      } catch (reason) {
        if (reason.name !== "NotFoundError") throw reason;
      }
      const folder = await brandDir.getDirectoryHandle(model, {create: true});
      const files = [...fileInput.files];
      const photos = files.length > 1;
      if (photos) {
        const collator = new Intl.Collator(undefined, {numeric: true});
        files.sort(collator.compare);
      }
      for (const [index, file] of files.entries()) {
        const extension = file.name.split(".").pop().toLowerCase();
        const filename = photos ? `${String(index + 1).padStart(3, "0")}.${extension}` : `说明书.${extension}`;
        const output = await (await folder.getFileHandle(filename, {create: true})).createWritable();
        await output.write(file);
        await output.close();
      }
      const manifest = await (await folder.getFileHandle("manual.json", {create: true})).createWritable();
      await manifest.write(JSON.stringify(metadata(), null, 2) + "\n");
      await manifest.close();
      result.textContent = `已保存到 manuals/${category}/${brand}/${model}/`;
      $("#after-save").hidden = false;
      saveButton.hidden = true;
      dialog.querySelector('[data-back="2"]').hidden = true;
      imported = true;
    } catch (reason) {
      result.hidden = true;
      if (reason.name === "AbortError") return;
      error.textContent = reason.name === "NotFoundError" ? "请选择包含 manualsite.py、site.json 和 manuals/ 的项目根目录。" : reason.name === "SecurityError" || reason.name === "NotAllowedError" ? "浏览器未授予目录写入权限；可换支持的浏览器，或使用 AI 整理方案。" : `保存失败：${reason.message}`;
    } finally {
      saveButton.disabled = false;
    }
  });
  $("#ai-project").addEventListener("input", promptText);
  $("#copy-prompt").addEventListener("click", async () => {
    promptText();
    const text = $("#ai-prompt").value;
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else { $("#ai-prompt").focus(); $("#ai-prompt").select(); if (!document.execCommand("copy")) throw Error("copy unavailable"); }
      $("#copy-result").textContent = "已复制";
    } catch {
      $("#copy-result").textContent = "未能自动复制，请手动选中文字复制";
      $("#ai-prompt").focus();
      $("#ai-prompt").select();
    }
  });
  setStep(1);
})();
