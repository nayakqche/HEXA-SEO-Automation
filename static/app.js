// HEXA SEO Automation — front-end controller.
// Streams NDJSON progress from /api/generate and renders log + result cards.

const $ = (sel) => document.querySelector(sel);

const form = $("#genForm");
const runBtn = $("#runBtn");
const runPanel = $("#runPanel");
const logEl = $("#log");
const cardsEl = $("#cards");
const bar = $("#progressBar");
const progressText = $("#progressText");

// ── Form persistence (localStorage) — survive reloads, no re-uploading ──
const PERSIST_FIELDS = ["website", "primary_sources", "secondary_sources",
                        "keywords", "extra", "format", "target_words", "limit"];
const STORE_KEY = "hexa-seo-form-v1";

function saveForm() {
  const data = {};
  for (const name of PERSIST_FIELDS) {
    const el = form.elements[name];
    if (el) data[name] = el.value;
  }
  localStorage.setItem(STORE_KEY, JSON.stringify(data));
  updateQueueInfo();
}

function restoreForm() {
  let data = {};
  try { data = JSON.parse(localStorage.getItem(STORE_KEY) || "{}"); } catch {}
  for (const name of PERSIST_FIELDS) {
    const el = form.elements[name];
    if (el && data[name] != null && data[name] !== "") el.value = data[name];
  }
  updateQueueInfo();
}

function queueKeywords() {
  return $("#keywordsText").value.split("\n").map(l => l.trim()).filter(Boolean);
}

function updateQueueInfo() {
  const n = queueKeywords().length;
  const per = parseInt(form.elements["limit"]?.value || "2", 10) || 2;
  const info = $("#queueInfo");
  if (!info) return;
  info.textContent = n
    ? `Queue: ${n} keyword(s) — next run generates ${Math.min(per, n)}. Completed ones leave the queue automatically.`
    : "";
}

form.addEventListener("input", saveForm);
restoreForm();

// Remove a finished keyword from the queue so the next run continues after it.
function consumeKeyword(kw) {
  const ta = $("#keywordsText");
  const lines = ta.value.split("\n");
  const idx = lines.findIndex(l => l.trim().toLowerCase() === kw.trim().toLowerCase());
  if (idx !== -1) {
    lines.splice(idx, 1);
    ta.value = lines.join("\n");
    saveForm();
  }
}

// ── CSV/Excel drop zone → one-time import into the saved queue ──
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
    $("#keywordsText").value = data.keywords.join("\n");
    csvInput.value = "";           // file no longer needed — queue is the source
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
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("drag"); })
);
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); })
);
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
  const img = rec.hero_image
    ? `<img class="thumb" src="/outputs/${rec.hero_image}" alt="">`
    : `<div class="thumb placeholder">${rec.image_errors.length ? "image gen failed" : "no image"}</div>`;
  const tags = (rec.tags || []).map((t) => `<span class="tag">${t}</span>`).join("");
  const cache = rec.usage && rec.usage.cache_read
    ? ` · ${rec.usage.cache_read.toLocaleString()} cached tokens` : "";

  const dl = rec.downloads;
  const downloadLinks = `
    <a class="dl dl-json" href="/outputs/${dl.json}" download>JSON</a>
    <a class="dl dl-md"   href="/outputs/${dl.markdown}" download>MD</a>
    <a class="dl dl-pdf"  href="/outputs/${dl.pdf}" download>PDF</a>
    <a class="dl dl-doc"  href="/outputs/${dl.docx}" download>DOCX</a>
    <a class="dl dl-html" href="/outputs/${dl.html}" target="_blank">Preview ↗</a>
  `;

  const imgErr = rec.image_errors && rec.image_errors.length
    ? `<div class="meta-line err">image issues: ${rec.image_errors.map(e => e.split(":")[0]).join(", ")}</div>` : "";

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
      <span class="kw">${rec.keyword}</span>
      <h3>${rec.title}</h3>
      ${rec.subtitle ? `<p class="subtitle">${rec.subtitle}</p>` : ""}
      <p class="desc">${rec.description}</p>
      <div class="tags">${tags}</div>
      <div class="downloads">${downloadLinks}</div>
      <div class="meta-line">
        ${rec.word_count} words${rec.category ? ` · ${rec.category}` : ""} · slug: <code>${rec.slug}</code>${cache}
      </div>
      ${linksLine}
      ${imgErr}
    </div>`;
  cardsEl.appendChild(card);
}

// ── Run ──
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  runBtn.disabled = true;
  runBtn.textContent = "Working…";
  runPanel.hidden = false;
  logEl.innerHTML = "";
  cardsEl.innerHTML = "";
  total = 0; completed = 0; setProgress();
  runPanel.scrollIntoView({ behavior: "smooth" });

  const fd = new FormData(form);
  fd.set("make_images", $("#makeImages").checked ? "true" : "false");

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
    runBtn.disabled = false;
    runBtn.textContent = "Generate blogs →";
  }
});

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
      if (ev.logo_url) $("#brandLogo").src = "/api/logo?url=" + encodeURIComponent(ev.logo_url);
      break;
    case "keyword_start":
      log(`→ [${ev.index}/${total}] writing: ${ev.keyword}`, "");
      break;
    case "keyword_done":
      completed = ev.done || (completed + 1); setProgress();
      log(`  ✓ [${ev.done}/${ev.total}] ${ev.title} (${ev.word_count} words)`, "l-ok");
      consumeKeyword(ev.keyword);   // drop it from the saved queue
      renderCard(ev);
      break;
    case "keyword_error":
      completed = ev.done || (completed + 1); setProgress();
      // Failed keywords STAY in the queue so the next run retries them.
      log(`  ✗ [${ev.done}/${ev.total}] ${ev.keyword} — ${ev.message}`, "l-err");
      break;
    case "done":
      log("● " + ev.message, "l-ok");
      break;
    case "error":
      log("✗ " + (ev.message || "(no error message)"), "l-err");
      if (ev.trace && ev.trace.length) {
        ev.trace.forEach(line => log("    " + line, "l-err"));
      }
      break;
  }
}
