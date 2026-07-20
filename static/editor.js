// HEXA SEO — CMS-style blog editor.
// Loads a generated post's JSON, lets a human edit text (bold / italic / link),
// images, and tables, then saves back so every export (JSON/MD/HTML/PDF/DOCX)
// is re-rendered on the server and can be downloaded again.

const $ = (s, r = document) => r.querySelector(s);
const REL = window.__REL__;
const API = "/api/edit/" + REL;
const ASSET_API = "/api/edit-asset/" + REL;

let SLUG = "post";
let dirty = false;

// ── Theme (shared with the main app) ────────────────────────────────────────
const THEME_KEY = "hexa-seo-theme";
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  $("#themeToggle").textContent = t === "dark" ? "☀️" : "🌙";
}
applyTheme(localStorage.getItem(THEME_KEY) ||
  (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
$("#themeToggle").addEventListener("click", () => {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
});

// ── Small helpers ───────────────────────────────────────────────────────────
const esc = (s) => (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const escAttr = (s) => esc(s).replace(/"/g, "&quot;");
const basename = (s) => (s || "").split("/").pop();
const assetUrl = (file) => "/outputs/" + REL + "/" + basename(file) + "?t=" + Date.now();
function markDirty() { dirty = true; setSaveState("Unsaved changes"); }
function setSaveState(msg) { $("#saveState").textContent = msg; }

// Keep track of the last rich-text field the caret was in, so the toolbar
// (which lives outside the field) can still format the right selection.
let lastRich = null;
document.addEventListener("selectionchange", () => {
  const a = document.activeElement;
  if (a && a.classList && a.classList.contains("rich")) lastRich = a;
});

// ── Rich-text toolbar ───────────────────────────────────────────────────────
$("#fmtToolbar").querySelectorAll("[data-cmd]").forEach((btn) => {
  btn.addEventListener("mousedown", (e) => e.preventDefault()); // keep selection
  btn.addEventListener("click", () => { document.execCommand(btn.dataset.cmd, false); markDirty(); });
});
$("#linkBtn").addEventListener("mousedown", (e) => e.preventDefault());
$("#linkBtn").addEventListener("click", () => {
  const url = prompt("Link URL (https://…)");
  if (url) { document.execCommand("createLink", false, url.trim()); markDirty(); }
});
$("#unlinkBtn").addEventListener("mousedown", (e) => e.preventDefault());
$("#unlinkBtn").addEventListener("click", () => { document.execCommand("unlink", false); markDirty(); });

// ── Inline cleaner (paste → only bold/italic/link/br survive) ───────────────
function cleanInline(node) {
  let out = "";
  node.childNodes.forEach((n) => {
    if (n.nodeType === 3) { out += esc(n.nodeValue); return; }
    if (n.nodeType !== 1) return;
    const tag = n.tagName.toLowerCase();
    const inner = cleanInline(n);
    const st = n.style || {};
    if (tag === "b" || tag === "strong" || st.fontWeight === "bold" || +st.fontWeight >= 600)
      out += `<strong>${inner}</strong>`;
    else if (tag === "i" || tag === "em" || st.fontStyle === "italic")
      out += `<em>${inner}</em>`;
    else if (tag === "a") {
      const h = n.getAttribute("href") || "";
      out += h ? `<a href="${escAttr(h)}">${inner}</a>` : inner;
    } else if (tag === "br") out += "<br>";
    else out += inner;
  });
  return out;
}

// ── Block builders ──────────────────────────────────────────────────────────
const blocksEl = $("#blocks");

function blockShell(type, label) {
  const el = document.createElement("div");
  el.className = "block";
  el.dataset.type = type;
  el.innerHTML =
    `<div class="block-head">
       <span class="block-type">${label}</span>
       <div class="block-ctrls">
         <button type="button" class="mv" data-dir="up" title="Move up">↑</button>
         <button type="button" class="mv" data-dir="down" title="Move down">↓</button>
         <button type="button" class="del" title="Delete block">🗑</button>
       </div>
     </div>
     <div class="block-body"></div>`;
  el.querySelector('[data-dir="up"]').addEventListener("click", () => {
    if (el.previousElementSibling) { blocksEl.insertBefore(el, el.previousElementSibling); markDirty(); }
  });
  el.querySelector('[data-dir="down"]').addEventListener("click", () => {
    if (el.nextElementSibling) { blocksEl.insertBefore(el.nextElementSibling, el); markDirty(); }
  });
  el.querySelector(".del").addEventListener("click", () => {
    if (confirm("Delete this block?")) { el.remove(); markDirty(); }
  });
  return el;
}

function richDiv(html) {
  const d = document.createElement("div");
  d.className = "rich";
  d.contentEditable = "true";
  d.innerHTML = html || "";
  d.addEventListener("input", markDirty);
  return d;
}

function makeHeading(b) {
  const el = blockShell("heading", "Heading");
  const body = el.querySelector(".block-body");
  const sel = document.createElement("select");
  sel.className = "lvl";
  sel.innerHTML = `<option value="2">H2</option><option value="3">H3</option>`;
  sel.value = String(b.level === 3 ? 3 : 2);
  sel.addEventListener("change", markDirty);
  const inp = document.createElement("input");
  inp.type = "text"; inp.className = "heading-text"; inp.value = b.text || "";
  inp.addEventListener("input", markDirty);
  const row = document.createElement("div"); row.className = "head-row";
  row.append(sel, inp);
  body.appendChild(row);
  return el;
}

function makeParagraph(b) {
  const el = blockShell("paragraph", "Paragraph");
  el.querySelector(".block-body").appendChild(
    richDiv(b.html || esc(b.text || "")));
  return el;
}

function makeList(b) {
  const el = blockShell("list", "List");
  const body = el.querySelector(".block-body");
  const sel = document.createElement("select");
  sel.className = "list-style";
  sel.innerHTML = `<option value="unordered">Bulleted</option><option value="ordered">Numbered</option>`;
  sel.value = b.style === "ordered" ? "ordered" : "unordered";
  sel.addEventListener("change", markDirty);
  const items = document.createElement("div"); items.className = "list-items";
  const src = b.itemsHtml || (b.items || []).map(esc);
  (src.length ? src : [""]).forEach((h) => items.appendChild(listItem(h)));
  const add = document.createElement("button");
  add.type = "button"; add.className = "btn small"; add.textContent = "+ Item";
  add.addEventListener("click", () => { items.appendChild(listItem("")); markDirty(); });
  body.append(sel, items, add);
  return el;
}
function listItem(html) {
  const row = document.createElement("div"); row.className = "li-row";
  const rich = richDiv(html);
  const rm = document.createElement("button");
  rm.type = "button"; rm.className = "li-del"; rm.textContent = "×"; rm.title = "Remove item";
  rm.addEventListener("click", () => { row.remove(); markDirty(); });
  row.append(rich, rm);
  return row;
}

function makeImage(b) {
  const el = blockShell("image", "Image");
  const body = el.querySelector(".block-body");
  const img = document.createElement("img");
  img.className = "img-preview";
  img.dataset.src = b.src || "";
  if (b.src) img.src = assetUrl(b.src);
  img.onerror = () => { img.classList.add("missing"); };
  const file = document.createElement("input"); file.type = "file"; file.accept = "image/*"; file.hidden = true;
  const replace = document.createElement("button");
  replace.type = "button"; replace.className = "btn small"; replace.textContent = "Replace / upload image…";
  replace.addEventListener("click", () => file.click());
  file.addEventListener("change", async () => {
    if (!file.files.length) return;
    const name = await uploadAsset(file.files[0]);
    if (name) {
      img.dataset.src = `/assets/blogs/${SLUG}/${name}`;
      img.classList.remove("missing");
      img.src = assetUrl(name);
      markDirty();
    }
  });
  const alt = fieldInput("Alt text (describe the photo)", b.alt || "");
  const cap = fieldInput("Caption (shown under the image)", b.caption || "");
  const href = fieldInput("Link when clicked (optional)", b.href || "");
  alt.classList.add("img-alt"); cap.classList.add("img-cap"); href.classList.add("img-href");
  body.append(img, replace, file, alt, cap, href);
  return el;
}
function fieldInput(placeholder, value) {
  const i = document.createElement("input");
  i.type = "text"; i.placeholder = placeholder; i.value = value || "";
  i.addEventListener("input", markDirty);
  return i;
}

function makeTable(b) {
  const el = blockShell("table", "Table");
  const body = el.querySelector(".block-body");
  const wrap = document.createElement("div"); wrap.className = "tbl-wrap";
  const table = document.createElement("table"); table.className = "tbl-edit";
  wrap.appendChild(table);
  // Build a single grid: header row (if any) + data rows.
  let grid = [];
  if (b.headers && b.headers.length) grid.push(b.headers.slice());
  (b.rows || []).forEach((r) => grid.push(r.slice()));
  if (!grid.length) grid = [["", ""], ["", ""]];
  grid.forEach((r, ri) => addTableRow(table, r, ri === 0));

  const controls = document.createElement("div"); controls.className = "tbl-ctrls";
  controls.innerHTML =
    `<button type="button" data-t="row">+ Row</button>
     <button type="button" data-t="col">+ Column</button>
     <button type="button" data-t="delrow">− Row</button>
     <button type="button" data-t="delcol">− Column</button>`;
  controls.querySelector('[data-t="row"]').addEventListener("click", () => {
    const cols = table.rows[0] ? table.rows[0].cells.length : 2;
    addTableRow(table, new Array(cols).fill(""), false); markDirty();
  });
  controls.querySelector('[data-t="col"]').addEventListener("click", () => {
    [...table.rows].forEach((row, i) => addCell(row, "", i === 0)); markDirty();
  });
  controls.querySelector('[data-t="delrow"]').addEventListener("click", () => {
    if (table.rows.length > 1) { table.deleteRow(table.rows.length - 1); markDirty(); }
  });
  controls.querySelector('[data-t="delcol"]').addEventListener("click", () => {
    [...table.rows].forEach((row) => { if (row.cells.length > 1) row.deleteCell(row.cells.length - 1); });
    markDirty();
  });
  const cap = fieldInput("Table caption (optional)", b.caption || "");
  cap.classList.add("tbl-cap");
  body.append(wrap, controls, cap);
  return el;
}
function addTableRow(table, values, header) {
  const row = table.insertRow();
  values.forEach((v) => addCell(row, v, header));
}
function addCell(row, value, header) {
  const cell = row.insertCell();
  cell.contentEditable = "true";
  cell.textContent = value || "";
  if (header) cell.classList.add("th");
  cell.addEventListener("input", markDirty);
}

const BUILDERS = { heading: makeHeading, paragraph: makeParagraph, list: makeList, image: makeImage, table: makeTable };

// ── Add-block bar ───────────────────────────────────────────────────────────
document.querySelectorAll("[data-add]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const t = btn.dataset.add;
    const defaults = { heading: { level: 2, text: "" }, paragraph: { html: "" },
      list: { style: "unordered", itemsHtml: [""] }, image: {}, table: {} };
    blocksEl.appendChild(BUILDERS[t](defaults[t]));
    markDirty();
    blocksEl.lastElementChild.scrollIntoView({ behavior: "smooth", block: "center" });
  });
});

// ── Serialize DOM → post JSON ───────────────────────────────────────────────
function serialize(basePost) {
  const post = JSON.parse(JSON.stringify(basePost || {}));
  post.slug = SLUG;
  post.seo = post.seo || {};
  post.meta = post.meta || {};
  post.meta.title = $("#metaTitle").value.trim();
  post.seo.title = post.meta.title;
  post.meta.subtitle = $("#metaSubtitle").value.trim();
  post.meta.category = $("#metaCategory").value.trim();
  post.meta.tags = $("#metaTags").value.split(",").map((s) => s.trim()).filter(Boolean);
  post.seo.description = $("#seoDesc").value.trim();
  post.hero = post.hero || {};
  post.hero.image = Object.assign({}, post.hero.image, {
    src: $("#heroPreview").dataset.src || (post.hero.image || {}).src || "",
    alt: $("#heroAlt").value.trim(),
  });

  const content = [];
  [...blocksEl.children].forEach((el, i) => {
    const t = el.dataset.type;
    const id = "block-" + (i + 1);
    if (t === "heading") {
      content.push({ id, type: "heading", level: +el.querySelector(".lvl").value,
        text: el.querySelector(".heading-text").value });
    } else if (t === "paragraph") {
      content.push({ id, type: "paragraph", html: el.querySelector(".rich").innerHTML });
    } else if (t === "list") {
      const itemsHtml = [...el.querySelectorAll(".li-row .rich")].map((r) => r.innerHTML);
      content.push({ id, type: "list", style: el.querySelector(".list-style").value, itemsHtml });
    } else if (t === "image") {
      content.push({ id, type: "image",
        src: el.querySelector(".img-preview").dataset.src,
        alt: el.querySelector(".img-alt").value,
        caption: el.querySelector(".img-cap").value,
        href: el.querySelector(".img-href").value });
    } else if (t === "table") {
      const rows = [...el.querySelectorAll(".tbl-edit tr")].map(
        (tr) => [...tr.cells].map((c) => c.textContent.trim()));
      content.push({ id, type: "table", rows, caption: el.querySelector(".tbl-cap").value });
    }
  });
  post.content = content;
  return post;
}

// ── Load ────────────────────────────────────────────────────────────────────
let BASE_POST = {};
async function load() {
  setSaveState("Loading…");
  const resp = await fetch(API);
  const data = await resp.json();
  if (!resp.ok) { setSaveState("Could not load post."); alert(data.error || "Load failed"); return; }
  BASE_POST = data.post || {};
  SLUG = BASE_POST.slug || "post";
  $("#slugLabel").textContent = SLUG;
  const meta = BASE_POST.meta || {}, seo = BASE_POST.seo || {}, hero = (BASE_POST.hero || {}).image || {};
  $("#metaTitle").value = meta.title || seo.title || "";
  $("#metaSubtitle").value = meta.subtitle || "";
  $("#metaCategory").value = meta.category || "";
  $("#metaTags").value = (meta.tags || []).join(", ");
  $("#seoDesc").value = seo.description || "";
  $("#heroAlt").value = hero.alt || "";
  $("#heroPreview").dataset.src = hero.src || "";
  if (hero.src) $("#heroPreview").src = assetUrl(hero.src);
  ["metaTitle", "metaSubtitle", "metaCategory", "metaTags", "seoDesc", "heroAlt"]
    .forEach((id) => $("#" + id).addEventListener("input", markDirty));

  blocksEl.innerHTML = "";
  (BASE_POST.content || []).forEach((b) => {
    const fn = BUILDERS[b.type];
    if (fn) blocksEl.appendChild(fn(b));
  });
  dirty = false; setSaveState("Loaded.");
}

// ── Hero replace ────────────────────────────────────────────────────────────
$("#heroReplace").addEventListener("click", () => $("#heroFile").click());
$("#heroFile").addEventListener("change", async () => {
  const f = $("#heroFile").files[0];
  if (!f) return;
  const name = await uploadAsset(f);
  if (name) {
    $("#heroPreview").dataset.src = `/assets/blogs/${SLUG}/${name}`;
    $("#heroPreview").src = assetUrl(name);
    markDirty();
  }
});

async function uploadAsset(file) {
  const fd = new FormData(); fd.append("file", file);
  try {
    const resp = await fetch(ASSET_API, { method: "POST", body: fd });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    return data.filename;
  } catch (err) { alert("Image upload failed: " + err.message); return null; }
}

// ── Save + download ─────────────────────────────────────────────────────────
$("#saveBtn").addEventListener("click", save);
async function save() {
  setSaveState("Saving…");
  $("#saveBtn").disabled = true;
  try {
    const resp = await fetch(API, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ post: serialize(BASE_POST) }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    BASE_POST = data.post;
    dirty = false;
    setSaveState("Saved ✓ — downloads updated.");
  } catch (err) {
    setSaveState("Save failed.");
    alert("Save failed: " + err.message);
  } finally {
    $("#saveBtn").disabled = false;
  }
}

$("#dlBtn").addEventListener("click", () => { $("#dlList").hidden = !$("#dlList").hidden; });
$("#dlList").querySelectorAll("a").forEach((a) => {
  const map = { json: "post.json", md: "post.md", html: "post.html", pdf: "post.pdf", docx: "post.docx" };
  a.addEventListener("click", (e) => {
    if (dirty && !confirm("You have unsaved changes. Download the last saved version anyway?")) {
      e.preventDefault(); return;
    }
    const f = map[a.dataset.f];
    a.href = "/outputs/" + REL + "/" + f + "?t=" + Date.now();
    if (a.dataset.f !== "html") a.setAttribute("download", "");
    $("#dlList").hidden = true;
  });
});

// ── Back ────────────────────────────────────────────────────────────────────
$("#backBtn").addEventListener("click", () => {
  if (dirty && !confirm("You have unsaved changes. Leave without saving?")) return;
  window.location = "/";
});
window.addEventListener("beforeunload", (e) => {
  if (dirty) { e.preventDefault(); e.returnValue = ""; }
});

// ── Paste from Word ─────────────────────────────────────────────────────────
const pasteModal = $("#pasteModal");
$("#pasteWordBtn").addEventListener("click", () => {
  pasteModal.hidden = false;
  const box = $("#pasteCatch"); box.innerHTML = ""; box.focus();
});
$("#pasteCancel").addEventListener("click", () => { pasteModal.hidden = true; });
pasteModal.addEventListener("click", (e) => { if (e.target === pasteModal) pasteModal.hidden = true; });

$("#pasteCatch").addEventListener("paste", (e) => {
  e.preventDefault();
  const html = e.clipboardData.getData("text/html");
  const text = e.clipboardData.getData("text/plain");
  const added = html ? blocksFromHtml(html) : blocksFromText(text);
  added.forEach((el) => blocksEl.appendChild(el));
  pasteModal.hidden = true;
  if (added.length) { markDirty(); blocksEl.lastElementChild.scrollIntoView({ behavior: "smooth" }); }
});

function blocksFromText(text) {
  return (text || "").split(/\n{1,}/).map((l) => l.trim()).filter(Boolean)
    .map((l) => makeParagraph({ html: esc(l) }));
}

function blocksFromHtml(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const out = [];
  const walk = (nodes) => {
    nodes.forEach((n) => {
      if (n.nodeType === 3) {
        const t = n.nodeValue.trim();
        if (t) out.push(makeParagraph({ html: esc(t) }));
        return;
      }
      if (n.nodeType !== 1) return;
      const tag = n.tagName.toLowerCase();
      if (/^h[1-6]$/.test(tag)) {
        out.push(makeHeading({ level: tag === "h2" || tag === "h1" ? 2 : 3, text: n.textContent.trim() }));
      } else if (tag === "p") {
        const h = cleanInline(n);
        if (h.trim()) out.push(makeParagraph({ html: h }));
      } else if (tag === "ul" || tag === "ol") {
        const itemsHtml = [...n.querySelectorAll(":scope > li")].map((li) => cleanInline(li)).filter((x) => x.trim());
        if (itemsHtml.length) out.push(makeList({ style: tag === "ol" ? "ordered" : "unordered", itemsHtml }));
      } else if (tag === "table") {
        const rows = [...n.querySelectorAll("tr")].map(
          (tr) => [...tr.querySelectorAll("th,td")].map((c) => c.textContent.trim()));
        if (rows.length) out.push(makeTable({ rows }));
      } else if (["div", "section", "article", "body"].includes(tag)) {
        walk([...n.childNodes]);
      } else {
        const h = cleanInline(n);
        if (h.trim()) out.push(makeParagraph({ html: h }));
      }
    });
  };
  walk([...doc.body.childNodes]);
  return out;
}

load();
