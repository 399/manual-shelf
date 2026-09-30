// Static, in-browser search. No background service or external dependency.
// 搜索既匹配逐页正文（MANUAL_SHELF_INDEX），也匹配说明书级元数据（MANUAL_SHELF_META），
// 因此即使某本说明书没有可提取的正文，也能按名称、品牌、型号、别名或标签查到。
(() => {
  const input = document.querySelector("#search");
  if (!input) return;
  const cards = document.querySelector("#cards");
  const filters = document.querySelector(".filters");
  const results = document.querySelector("#results");
  const status = document.querySelector("#status");
  const buttons = [...document.querySelectorAll(".filter")];
  const entries = [...document.querySelectorAll(".manual-card")];
  let category = "all";
  const index = window.MANUAL_SHELF_INDEX || [];
  const meta = window.MANUAL_SHELF_META || [];
  let pending;

  function terms(query) {
    const words = query.toLocaleLowerCase().trim().split(/\s+/u).filter(Boolean);
    if (words.length === 1 && /^[\p{Script=Han}]{4,}$/u.test(words[0])) {
      return [...words[0]].slice(1).map((_, i) => words[0].slice(i, i + 2));
    }
    return words;
  }

  function showDefault() {
    cards.hidden = false;
    results.hidden = true;
    filters.hidden = false;
    let visible = 0;
    entries.forEach((card) => {
      card.hidden = category !== "all" && card.dataset.category !== category;
      if (!card.hidden) visible++;
    });
    status.textContent = `${visible} 本说明书`;
  }

  function resultCard(item, matchingTerms, isMeta) {
    const a = document.createElement("a");
    a.className = "result-card";
    a.href = item.url;
    const type = document.createElement("span");
    type.className = "category";
    if (isMeta) {
      type.textContent = `${item.category} / ${item.brand || "其他品牌"} · 说明书信息`;
    } else {
      type.textContent = `${item.category} / ${item.brand || "其他品牌"} / 第 ${item.page} 页`;
    }
    const heading = document.createElement("strong");
    heading.textContent = `${item.title}${item.model ? ` · ${item.model}` : ""}`;
    const excerpt = document.createElement("p");
    if (isMeta) {
      const tags = (item.tags || []).join("、");
      excerpt.textContent = tags ? `标签：${tags}` : "说明书元数据匹配（无正文，打开查看原件）。";
    } else {
      const lower = item.text.toLocaleLowerCase();
      const hits = matchingTerms.map((word) => lower.indexOf(word)).filter((position) => position >= 0);
      const offset = hits.length ? Math.max(0, Math.min(...hits) - 46) : 0;
      excerpt.textContent = (offset ? "…" : "") + item.text.slice(offset, offset + 148).replace(/\s+/gu, " ") + (offset + 148 < item.text.length ? "…" : "");
      if (!item.text) excerpt.textContent = "打开说明书原件查看本页。";
    }
    a.append(type, heading, excerpt);
    return a;
  }

  function search() {
    const query = input.value.trim();
    if (!query) return showDefault();
    cards.hidden = true;
    filters.hidden = false;
    results.hidden = false;
    const words = terms(query);
    const ranked = [];
    const seen = new Set();
    for (const item of index) {
      if (category !== "all" && item.category !== category) continue;
      const metaText = `${item.title} ${item.brand} ${item.model} ${item.category} ${(item.aliases || []).join(" ")} ${(item.tags || []).join(" ")}`.toLocaleLowerCase();
      const text = item.text.toLocaleLowerCase();
      const matched = words.filter((word) => metaText.includes(word) || text.includes(word));
      if (matched.length) {
        const score = matched.length * 3 + words.filter((word) => metaText.includes(word)).length * 5 + (text.includes(query.toLocaleLowerCase()) ? 4 : 0);
        ranked.push({item, score, matched, isMeta: false});
        seen.add(`p:${item.url}`);
      }
    }
    for (const item of meta) {
      if (category !== "all" && item.category !== category) continue;
      const metaText = `${item.title} ${item.brand} ${item.model} ${item.category} ${(item.aliases || []).join(" ")} ${(item.tags || []).join(" ")}`.toLocaleLowerCase();
      const matched = words.filter((word) => metaText.includes(word));
      if (matched.length && !seen.has(`m:${item.url}`)) {
        seen.add(`m:${item.url}`);
        ranked.push({item, score: matched.length * 5 + 1, matched, isMeta: true});
      }
    }
    ranked.sort((a, b) => b.score - a.score || a.item.title.localeCompare(b.item.title) || (a.item.page || 0) - (b.item.page || 0));
    results.replaceChildren(...ranked.slice(0, 16).map(({item, matched, isMeta}) => resultCard(item, matched, isMeta)));
    if (!ranked.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "没有找到相关页。试试品牌、型号，或缩短关键词。";
      results.append(empty);
    }
    status.textContent = ranked.length ? `找到 ${ranked.length} 个相关结果${ranked.length > 16 ? "，显示最相关的 16 个" : ""}` : "没有匹配结果";
  }

  input.addEventListener("input", () => {
    clearTimeout(pending);
    pending = setTimeout(search, 130);
  });
  buttons.forEach((button) => button.addEventListener("click", () => {
    category = button.dataset.category;
    buttons.forEach((other) => {
      const active = other === button;
      other.classList.toggle("active", active);
      other.setAttribute("aria-pressed", String(active));
    });
    search();
  }));
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
      event.preventDefault();
      input.focus();
    }
    if (event.key === "Escape" && document.activeElement === input) {
      input.value = "";
      showDefault();
      input.blur();
    }
  });
  showDefault();
})();
