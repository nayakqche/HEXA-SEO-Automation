// HEXA SEO Automation — front-end controller.
// Streams NDJSON progress from /api/generate and renders a live log + cards.

const $ = (sel) => document.querySelector(sel);

const form = $("#genForm");
const runBtn = $("#runBtn");
const runPanel = $("#runPanel");
const logEl = $("#log");
const cardsEl = $("#cards");
const bar = $("#progressBar");
const progressText = $("#progressText");

// ── CSV drop zone ──────────────────────────────────────────────
const dropzone = $("#dropzone");
const csvInput = $("#csvInput");
const csvName = $("#csvName");

$("#browseBtn").addEventListener("click", () => csvInput.click());
csvInput.addEventListener("change", () => {
  if (csvInput.files.length) {
    csvName.innerHTML = `Selected: <strong>${csvInput.files[0].name}</strong>`;
  }
});
["dragover", "dragenter"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("drag"); })
);
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); })
);
dropzone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) {
    csvInput.files = e.dataTransfer.files;
    csvName.innerHTML = `Selected: <strong>${e.dataTransfer.files[0].name}</strong>`;
  }
});

// ── Sample CSV download ────────────────────────────────────────
$("#sampleLink").addEventListener("click", (e) => {
  e.preventDefault();
  const sample = [
    "keyword",
    "corporate decarbonization strategy",
    "renewable energy procurement for enterprises",
    "carbon offset vs carbon avoidance",
    "net zero roadmap for manufacturers",
    "scope 3 emissions reduction",
  ].join("\n");
  const url = URL.createObjectURL(new Blob([sample], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url; a.download = "sample_keywords.csv"; a.click();
  URL.revokeObjectURL(url);
});

// ── Logging helpers ────────────────────────────────────────────
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

// ── Result card ────────────────────────────────────────────────
function renderCard(rec) {
  const card = document.createElement("div");
  card.className = "card";
  const img = rec.files.image
    ? `<img class="thumb" src="/outputs/${rec.files.image}" alt="">`
    : `<div class="thumb placeholder">${rec.image_error ? "image failed" : "no image"}</div>`;
  const tags = (rec.tags || []).map((t) => `<span class="tag">${t}</span>`).join("");
  const cache = rec.usage && rec.usage.cache_read
    ? ` · ${rec.usage.cache_read.toLocaleString()} cached tokens` : "";
  card.innerHTML = `
    ${img}
    <div class="body">
      <span class="kw">${rec.keyword}</span>
      <h3>${rec.title}</h3>
      <p class="desc">${rec.meta_description || ""}</p>
      <div class="tags">${tags}</div>
      <div class="links">
        <a href="/outputs/${rec.files.html}" target="_blank">Preview ↗</a>
        <a href="/outputs/${rec.files.markdown}" download>Markdown ↓</a>
        ${rec.files.image ? `<a href="/outputs/${rec.files.image}" download>Image ↓</a>` : ""}
      </div>
      <div class="meta-line">${rec.word_count} words · slug: <code>${rec.slug}</code>${cache}
        ${rec.image_error ? ` · <span style="color:var(--bad)">image: ${rec.image_error.slice(0,60)}</span>` : ""}</div>
    </div>`;
  cardsEl.appendChild(card);
}

// ── Run ────────────────────────────────────────────────────────
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
    log("Error: " + (data.error || resp.statusText), "l-err");
    return reset();
  }

  // Read the NDJSON stream line by line.
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
    case "grounded":
      log("✓ " + ev.message, "l-ok");
      if (ev.logo_url) $("#brandLogo").src = "/api/logo?url=" + encodeURIComponent(ev.logo_url);
      break;
    case "keyword_start":
      log(`→ [${ev.index}/${total}] writing: ${ev.keyword}`, "");
      break;
    case "keyword_done":
      completed++; setProgress();
      log(`  ✓ ${ev.title} (${ev.word_count} words)`, "l-ok");
      renderCard(ev);
      break;
    case "keyword_error":
      completed++; setProgress();
      log(`  ✗ ${ev.keyword} — ${ev.stage} failed: ${ev.message}`, "l-err");
      break;
    case "done":
      log("● " + ev.message, "l-ok");
      break;
    case "error":
      log("✗ " + ev.message, "l-err");
      break;
  }
}
