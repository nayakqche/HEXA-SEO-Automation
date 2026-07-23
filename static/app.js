// HEXA SEO Automation — front-end controller.
// Streams NDJSON progress from /api/generate and renders log + result cards.
// Also owns: theme, keyword blocks + used/not-used status, the resource
// container, and persistence of everything across reloads.

const $ = (sel) => document.querySelector(sel);

const form = $("#genForm");
const runBtn = $("#runBtn");
const runPanel = $("#runPanel");
const logEl = $("#log");
const cardsEl = $("#cards");
const bar = $("#progressBar");
const progressText = $("#progressText");

// ── Storage keys ────────────────────────────────────────────────────────────
const STORE_KEY  = "hexa-seo-form-v1";      // form fields
const KW_KEY     = "hexa-seo-kw-status-v1";  // { "<keyword lower>": "used" }
const BLOGS_KEY  = "hexa-seo-blogs-v1";      // generated blog records
const THEME_KEY  = "hexa-seo-theme";         // "dark" | "light"

const PERSIST_FIELDS = ["website", "primary_sources", "secondary_sources",
                        "keywords", "brief", "extra", "format", "target_words",
                        "limit", "personalized"];

// ── Theme ────────────────────────────────────────────────────────────────────
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const btn = $("#themeToggle");
  if (btn) btn.textContent = theme === "dark" ? "☀️" : "🌙";
}
applyTheme(localStorage.getItem(THEME_KEY) ||
  (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
$("#themeToggle").addEventListener("click", () => {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
});

// ── Form persistence ─────────────────────────────────────────────────────────
function saveForm() {
  const data = {};
  for (const name of PERSIST_FIELDS) {
    const el = form.elements[name];
    if (!el) continue;
    data[name] = el.type === "checkbox" ? el.checked : el.value;
  }
  localStorage.setItem(STORE_KEY, JSON.stringify(data));
  updateQueueInfo();
  renderKeywordBlocks();
  applyPersonalizedMode();
}

function restoreForm() {
  let data = {};
  try { data = JSON.parse(localStorage.getItem(STORE_KEY) || "{}"); } catch {}
  for (const name of PERSIST_FIELDS) {
    const el = form.elements[name];
    if (!el || data[name] == null) continue;
    if (el.type === "checkbox") el.checked = !!data[name];
    else if (data[name] !== "") el.value = data[name];
  }
  updateQueueInfo();
  renderKeywordBlocks();
  applyPersonalizedMode();
}

// Personalized toggle: force the format to hexa-update (and lock it) so the
// writer treats the post as a Hexa developments update, and reveal a stronger
// hint for the brief field. Also drives how the "keyword_done" event is handled.
function applyPersonalizedMode() {
  const on = form.elements["personalized"] && form.elements["personalized"].checked;
  const fmt = form.elements["format"];
  if (!fmt) return;
  if (on) {
    fmt.dataset.prevValue = fmt.dataset.prevValue || fmt.value;
    fmt.value = "hexa-update";
    fmt.disabled = true;
    fmt.title = "Locked while Personalized is on — always writes as a Hexa developments update.";
  } else {
    fmt.disabled = false;
    fmt.title = "";
    if (fmt.value === "hexa-update" && fmt.dataset.prevValue) {
      fmt.value = fmt.dataset.prevValue;
    }
    delete fmt.dataset.prevValue;
  }
  const brief = document.getElementById("briefText");
  if (brief) brief.placeholder = on
    ? "What this Hexa update should convey — e.g. commissioning of the 200 MW Wardha project, key numbers to interpret from the uploaded table."
    : "e.g. celebrate the commissioning of our 200 MW Wardha wind project; highlight the OA capacity commissioned in April; explain the numbers in the uploaded table.";
}

// ── Keyword status (used / not used) ─────────────────────────────────────────
function loadStatus() {
  try { return JSON.parse(localStorage.getItem(KW_KEY) || "{}"); } catch { return {}; }
}
function saveStatus(map) { localStorage.setItem(KW_KEY, JSON.stringify(map)); }

function masterKeywords() {
  const seen = new Set();
  const out = [];
  for (const raw of $("#keywordsText").value.split("\n")) {
    const kw = raw.trim();
    if (!kw) continue;
    const key = kw.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(kw);
  }
  return out;
}

function pendingKeywords() {
  const status = loadStatus();
  return masterKeywords().filter((kw) => status[kw.toLowerCase()] !== "used");
}

function markUsed(kw) {
  const status = loadStatus();
  status[kw.trim().toLowerCase()] = "used";
  saveStatus(status);
  renderKeywordBlocks();
  updateQueueInfo();
}

// Click a status cell to flip it: used → not used (re-queues it) and back.
function toggleStatus(kw) {
  const status = loadStatus();
  const key = kw.trim().toLowerCase();
  if (status[key] === "used") delete status[key];
  else status[key] = "used";
  saveStatus(status);
  renderKeywordBlocks();
  updateQueueInfo();
}

function removeKeyword(kw) {
  const ta = $("#keywordsText");
  ta.value = ta.value.split("\n").filter((l) => l.trim().toLowerCase() !== kw.toLowerCase()).join("\n");
  const status = loadStatus();
  delete status[kw.toLowerCase()];
  saveStatus(status);
  saveForm();
}

function updateQueueInfo() {
  const info = $("#queueInfo");
  if (!info) return;
  const pending = pendingKeywords().length;
  const per = parseInt(form.elements["limit"]?.value || "2", 10) || 2;
  info.textContent = pending
    ? `${pending} keyword(s) not yet used — next run generates ${Math.min(per, pending)}.`
    : (masterKeywords().length ? "All keywords used. Add more, or remove a chip to re-queue it." : "");
}

// Map each keyword (lowercased) to the blog made for it, numbered in the order
// blogs were generated so the table can show "#N · Preview".
function blogsByKeyword() {
  const map = {};
  loadBlogs().forEach((rec, i) => {
    if (rec.keyword) map[rec.keyword.toLowerCase()] = { serial: i + 1, rec };
  });
  return map;
}

function renderKeywordBlocks() {
  const box = $("#keywordBox");
  if (!box) return;
  const status = loadStatus();
  const blogs = blogsByKeyword();
  const kws = masterKeywords();
  if (!kws.length) { box.innerHTML = ""; return; }

  const table = document.createElement("table");
  table.className = "kw-table";
  table.innerHTML =
    `<thead><tr><th>#</th><th>Keyword</th><th>Status</th><th>Blog</th><th></th></tr></thead>`;
  const tbody = document.createElement("tbody");

  kws.forEach((kw, i) => {
    const used = status[kw.toLowerCase()] === "used";
    const hit = blogs[kw.toLowerCase()];
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="num">${i + 1}</td>` +
      `<td class="kw-name"></td>` +
      `<td class="st-cell"><span class="st ${used ? "used" : "pending"}" title="Click to toggle used / not used">${used ? "✓ used" : "✗ not used"}</span></td>` +
      `<td class="blog-cell"></td>` +
      `<td class="act-cell">` +
        `<button type="button" class="regen" title="Regenerate a fresh, different version">↻</button>` +
        `<button type="button" class="rm" title="Remove keyword">×</button>` +
      `</td>`;
    tr.querySelector(".kw-name").textContent = kw;
    tr.querySelector(".st").addEventListener("click", () => toggleStatus(kw));
    tr.querySelector(".regen").addEventListener("click", () => regenerateKeyword(kw));
    const blogCell = tr.querySelector(".blog-cell");
    if (hit && hit.rec.downloads && hit.rec.downloads.html) {
      const a = document.createElement("a");
      a.className = "blog-link";
      a.href = "/outputs/" + hit.rec.downloads.html;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = `#${hit.serial} Preview ↗`;
      blogCell.appendChild(a);
    } else {
      blogCell.innerHTML = `<span class="muted">—</span>`;
    }
    tr.querySelector(".rm").addEventListener("click", () => removeKeyword(kw));
    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  box.innerHTML = "";
  box.appendChild(table);
}

form.addEventListener("input", saveForm);
form.addEventListener("change", saveForm); // catches checkbox toggles & selects

// ── Generated-blog persistence ───────────────────────────────────────────────
function loadBlogs() {
  try { return JSON.parse(localStorage.getItem(BLOGS_KEY) || "[]"); } catch { return []; }
}
function saveBlogs(list) {
  localStorage.setItem(BLOGS_KEY, JSON.stringify(list.slice(-100)));
  $("#clearBlogs").hidden = list.length === 0;
}
function addBlog(rec) {
  const list = loadBlogs();
  list.push(rec);
  saveBlogs(list);
}
// Replace the stored blog for a keyword (used when regenerating).
function replaceBlog(rec) {
  const key = (rec.keyword || "").toLowerCase();
  const list = loadBlogs().filter((b) => (b.keyword || "").toLowerCase() !== key);
  list.push(rec);
  saveBlogs(list);
}
const cssEscape = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/"/g, '\\"');

$("#clearBlogs").addEventListener("click", () => {
  saveBlogs([]);
  cardsEl.innerHTML = "";
  renderKeywordBlocks();   // drop the blog links from the table too
});

// ── Resource container ───────────────────────────────────────────────────────
async function fetchResources() {
  try {
    const resp = await fetch("/api/resources");
    const data = await resp.json();
    renderResources(data.resources || []);
  } catch { /* container simply shows empty */ }
}

function renderResources(resources) {
  const list = $("#resList");
  list.innerHTML = "";
  for (const r of resources) {
    const chip = document.createElement("span");
    chip.className = "res-chip";
    chip.innerHTML =
      `<span class="kind ${r.kind}"></span>` +
      `<span class="name"></span>` +
      `<span class="muted note"></span>` +
      `<button type="button" class="rm" title="Remove">×</button>`;
    chip.querySelector(".kind").textContent = r.kind;
    chip.querySelector(".name").textContent = r.name;
    if (r.note) chip.querySelector(".note").textContent = "· " + r.note;
    chip.querySelector(".rm").addEventListener("click", () => removeResource(r.id));
    list.appendChild(chip);
  }
  $("#resClear").hidden = resources.length === 0;
}

async function uploadResourceFiles(files) {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  await postResources(fd);
}

async function postResources(fd) {
  try {
    const resp = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    renderResources(data.resources || []);
  } catch (err) {
    alert("Upload failed: " + err.message);
  }
}

async function removeResource(id) {
  const resp = await fetch("/api/resources/" + encodeURIComponent(id), { method: "DELETE" });
  const data = await resp.json();
  renderResources(data.resources || []);
}

$("#resBrowse").addEventListener("click", () => $("#resInput").click());
$("#resInput").addEventListener("change", () => {
  if ($("#resInput").files.length) uploadResourceFiles($("#resInput").files);
  $("#resInput").value = "";
});
$("#resAddText").addEventListener("click", () => {
  const text = $("#resPaste").value.trim();
  if (!text) return;
  const fd = new FormData();
  fd.append("text", text);
  postResources(fd).then(() => { $("#resPaste").value = ""; });
});
$("#resClear").addEventListener("click", async () => {
  if (!confirm("Clear all uploaded resources from the container?")) return;
  await fetch("/api/clear-resources", { method: "POST" });
  renderResources([]);
});

const resDrop = $("#resDropzone");
["dragover", "dragenter"].forEach((ev) =>
  resDrop.addEventListener(ev, (e) => { e.preventDefault(); resDrop.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) =>
  resDrop.addEventListener(ev, (e) => { e.preventDefault(); resDrop.classList.remove("drag"); }));
resDrop.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) uploadResourceFiles(e.dataTransfer.files);
});

// ── CSV/Excel keyword import → master queue ──────────────────────────────────
const dropzone = $("#dropzone");
const csvInput = $("#csvInput");
const csvName = $("#csvName");

async function importKeywordFile(file) {
  csvName.textContent = `Importing ${file.name}…`;
  const fd = new FormData();
  fd.append("csv", file);
  try {
    const resp = await fetch("/api/parse-keywords", { method: "POST", body: fd });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    // Merge into the existing master list without wiping the status of old ones.
    const existing = new Set(masterKeywords().map((k) => k.toLowerCase()));
    const merged = $("#keywordsText").value.split("\n").map((l) => l.trimEnd()).filter((l) => l.trim());
    for (const kw of data.keywords) {
      if (!existing.has(kw.toLowerCase())) merged.push(kw);
    }
    $("#keywordsText").value = merged.join("\n");
    csvInput.value = "";
    saveForm();
    csvName.innerHTML = `Imported <strong>${data.count}</strong> keywords from ` +
      `${file.name} — saved in your browser. No need to re-upload.`;
  } catch (err) {
    csvName.textContent = `Import failed: ${err.message}`;
  }
}

$("#browseBtn").addEventListener("click", () => csvInput.click());
csvInput.addEventListener("change", () => {
  if (csvInput.files.length) importKeywordFile(csvInput.files[0]);
});
["dragover", "dragenter"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); }));
dropzone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) importKeywordFile(e.dataTransfer.files[0]);
});

// ── Sample CSV download ──
$("#sampleLink").addEventListener("click", (e) => {
  e.preventDefault();
  const sample = [
    "keyword",
    "green open access for indian industries",
    "corporate ppa structures india",
    "round-the-clock renewable energy",
    "scope 2 emissions reduction strategy",
    "battery energy storage india c&i",
  ].join("\n");
  const url = URL.createObjectURL(new Blob([sample], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url; a.download = "sample_keywords.csv"; a.click();
  URL.revokeObjectURL(url);
});

// ── Logging helpers ──
function log(msg, cls = "") {
  const line = document.createElement("div");
  if (cls) line.className = cls;
  line.textContent = msg;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

let total = 0, completed = 0;
function setProgress() {
  const pct = total ? Math.round((completed / total) * 100) : 0;
  bar.style.width = pct + "%";
  progressText.textContent = total ? `${completed} / ${total} posts` : "";
}

// ── Result card ──
function renderCard(rec) {
  const card = document.createElement("div");
  card.className = "card";
  card.dataset.keyword = (rec.keyword || "").toLowerCase();
  // The thumb is always a fixed cell so the card keeps its 2-column layout even
  // when the hero image is missing (e.g. files cleared on a server restart).
  const img = rec.hero_image
    ? `<div class="thumb"><img src="/outputs/${rec.hero_image}" alt=""
         onerror="const t=this.closest('.thumb'); t.classList.add('placeholder'); t.textContent='image unavailable';"></div>`
    : `<div class="thumb placeholder">${(rec.image_errors && rec.image_errors.length) ? "image gen failed" : "no image"}</div>`;
  const tags = (rec.tags || []).map(() => `<span class="tag"></span>`).join("");
  const cache = rec.usage && rec.usage.cache_read
    ? ` · ${rec.usage.cache_read.toLocaleString()} cached tokens` : "";

  const dl = rec.downloads || {};
  const editRel = (dl.json || "").replace(/\/post\.json$/, "");
  const downloadLinks = `
    <a class="dl dl-json" href="/outputs/${dl.json}" download>JSON</a>
    <a class="dl dl-md"   href="/outputs/${dl.markdown}" download>MD</a>
    <a class="dl dl-pdf"  href="/outputs/${dl.pdf}" download>PDF</a>
    <a class="dl dl-doc"  href="/outputs/${dl.docx}" download>DOCX</a>
    <a class="dl dl-html" href="/outputs/${dl.html}" target="_blank">Preview ↗</a>
    ${editRel ? `<a class="dl dl-edit" href="/edit/${editRel}" target="_blank">✎ Edit</a>` : ""}
  `;

  const imgErr = rec.image_errors && rec.image_errors.length
    ? `<div class="meta-line err">image issues: ${rec.image_errors.map((e) => e.split(":")[0]).join(", ")}</div>` : "";

  const lk = rec.links && rec.links.kept ? rec.links : null;
  const linksLine = lk
    ? `<div class="links-line">
         <span class="pill">${lk.kept.internal} internal</span>
         <span class="pill cite">${lk.kept.citation} citation</span>
         ${lk.dropped && lk.dropped.length ? `<span class="muted">· dropped ${lk.dropped.length} unverified</span>` : ""}
       </div>` : "";

  card.innerHTML = `
    ${img}
    <div class="body">
      <span class="kw"></span><span class="used-badge">✓ used</span>
      <h3></h3>
      ${rec.subtitle ? `<p class="subtitle"></p>` : ""}
      <p class="desc"></p>
      <div class="tags">${tags}</div>
      <div class="downloads">${downloadLinks}</div>
      <div class="meta-line">
        ${rec.word_count} words${rec.category ? ` · ${rec.category}` : ""} · slug: <code></code>${cache}
      </div>
      ${linksLine}
      ${imgErr}
    </div>`;
  // Fill user/model text via textContent to avoid any HTML injection.
  card.querySelector(".kw").textContent = rec.keyword || "";
  card.querySelector("h3").textContent = rec.title || "";
  if (rec.subtitle) card.querySelector(".subtitle").textContent = rec.subtitle;
  card.querySelector(".desc").textContent = rec.description || "";
  card.querySelector("code").textContent = rec.slug || "";
  const tagEls = card.querySelectorAll(".tag");
  (rec.tags || []).forEach((t, i) => { if (tagEls[i]) tagEls[i].textContent = t; });
  cardsEl.appendChild(card);
}

function restoreBlogs() {
  const list = loadBlogs();
  if (!list.length) return;
  runPanel.hidden = false;
  $("#clearBlogs").hidden = false;
  list.forEach(renderCard);
}

// ── Run ──
let isRegen = false;         // single-keyword regeneration in flight
let isPersonalized = false;  // this run is a Hexa developments update
const personalizedOn = () =>
  !!(form.elements["personalized"] && form.elements["personalized"].checked);

form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (personalizedOn()) {
    // Personalized runs: keyword table is not consulted or updated.
    // Use whatever's typed in the keyword box (first one), else a default topic.
    const raw = ($("#keywordsText").value || "").split("\n").map((s) => s.trim()).filter(Boolean);
    const kw = raw[0] || "Hexa Climate developments update";
    runGeneration([kw], { personalized: true });
    return;
  }
  const pending = pendingKeywords();
  if (!pending.length) {
    alert("No unused keywords to generate. Add keywords, or click a green ✓ used to re-queue one.");
    return;
  }
  runGeneration(pending);
});

// Regenerate a fresh, different version of one keyword (works even if used).
function regenerateKeyword(kw) {
  if (runBtn.disabled) return;   // a run is already in progress
  const hit = blogsByKeyword()[kw.toLowerCase()];
  const avoid = hit && hit.rec
    ? [hit.rec.title, hit.rec.subtitle].filter(Boolean).join(" | ") : "";
  runGeneration([kw], { regenerate: true, avoid });
}

async function runGeneration(keywordList, opts = {}) {
  if (!keywordList.length) return;
  isRegen = !!opts.regenerate;
  isPersonalized = !!opts.personalized || personalizedOn();
  runBtn.disabled = true;
  runBtn.textContent = "Working…";
  runPanel.hidden = false;
  logEl.innerHTML = "";
  total = 0; completed = 0; setProgress();
  runPanel.scrollIntoView({ behavior: "smooth" });
  if (isRegen) log(`↻ Regenerating a fresh version of: ${keywordList[0]}`, "l-dim");
  if (isPersonalized) log("★ Personalized Hexa developments post — keyword table will NOT be marked used.", "l-dim");

  const fd = new FormData(form);
  fd.set("keywords", keywordList.join("\n"));
  fd.set("make_images", $("#makeImages").checked ? "true" : "false");
  if (isPersonalized) {
    fd.set("personalized", "1");
    fd.set("format", "hexa-update"); // in case the disabled <select> isn't sent
    fd.set("limit", String(keywordList.length));
  }
  if (opts.regenerate) {
    fd.set("regenerate", "1");
    fd.set("limit", String(keywordList.length));
    if (opts.avoid) fd.set("avoid", opts.avoid);
  }

  let resp;
  try {
    resp = await fetch("/api/generate", { method: "POST", body: fd });
  } catch (err) {
    log("Network error: " + err.message, "l-err");
    return reset();
  }

  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    const msg = data.error || resp.statusText || "(no body)";
    log(`Error ${resp.status}: ${msg}`, "l-err");
    if (resp.status >= 500) {
      log("→ Check the Render dashboard → Logs tab to see the exception traceback.", "l-warn");
    }
    return reset();
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop();
    for (const line of lines) {
      if (line.trim()) handleEvent(JSON.parse(line));
    }
  }
  reset();

  function reset() {
    isRegen = false;
    isPersonalized = false;
    runBtn.disabled = false;
    runBtn.textContent = "Generate blogs →";
  }
}

function handleEvent(ev) {
  switch (ev.event) {
    case "start":
      total = ev.total; setProgress();
      log(`Starting run ${ev.run_id} — ${ev.total} keyword(s).`, "l-dim");
      break;
    case "status":
      log(ev.message, "l-dim");
      break;
    case "warn":
      log("! " + ev.message, "l-warn");
      break;
    case "grounded":
      log("✓ " + ev.message, "l-ok");
      if (ev.logo_url) swapLogo(ev.logo_url);
      break;
    case "keyword_start":
      log(`→ [${ev.index}/${total}] writing: ${ev.keyword}`, "");
      break;
    case "keyword_done":
      completed = ev.done || (completed + 1); setProgress();
      log(`  ✓ [${ev.done}/${ev.total}] ${ev.title} (${ev.word_count} words)`, "l-ok");
      if (isRegen) {
        replaceBlog(ev);       // swap the prior version for this keyword
        const old = cardsEl.querySelector(
          `.card[data-keyword="${cssEscape((ev.keyword || "").toLowerCase())}"]`);
        if (old) old.remove();
      } else {
        addBlog(ev);           // persist first so its link is ready for the table
      }
      // Personalized (Hexa developments) runs are occasional posts — they must
      // NOT consume a keyword from the queue.
      if (!isPersonalized) markUsed(ev.keyword);
      renderCard(ev);
      break;
    case "keyword_error":
      completed = ev.done || (completed + 1); setProgress();
      // Failed keywords stay "not used" (red ✗) so the next run retries them.
      log(`  ✗ [${ev.done}/${ev.total}] ${ev.keyword} — ${ev.message}`, "l-err");
      break;
    case "done":
      log("● " + ev.message, "l-ok");
      break;
    case "error":
      log("✗ " + (ev.message || "(no error message)"), "l-err");
      if (ev.trace && ev.trace.length) {
        ev.trace.forEach((line) => log("    " + line, "l-err"));
      }
      break;
  }
}

// Swap the brand logo only once the fetched image actually loads, so a bad or
// slow logo URL can never blank out the header. Falls back to the bundled SVG.
function swapLogo(logoUrl) {
  const el = $("#brandLogo");
  const src = "/api/logo?url=" + encodeURIComponent(logoUrl);
  const probe = new Image();
  probe.onload = () => { el.src = src; };
  probe.onerror = () => { el.src = "/api/logo"; };  // bundled SVG fallback
  probe.src = src;
}
$("#brandLogo").addEventListener("error", function () {
  if (!this.dataset.fellBack) { this.dataset.fellBack = "1"; this.src = "/api/logo"; }
});

// ── Boot ──
restoreForm();
restoreBlogs();
fetchResources();
