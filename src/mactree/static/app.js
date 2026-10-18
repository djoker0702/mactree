"use strict";

// ---- state ---------------------------------------------------------------
const state = {
  jobId: null,
  rootPath: null,
  data: null,
  currentPath: null,
  view: "treemap",
  pollTimer: null,
  pinned: null, // path of a clicked (pinned) node for the inspector
};

// ---- dom -----------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const form = $("#scan-form");
const pathInput = $("#path-input");
const scanBtn = $("#scan-btn");
const statusEl = $("#status");
const crumbsEl = $("#breadcrumbs");
const chartEl = $("#chart");
const tooltip = $("#tooltip");
const inspectorEl = $("#inspector");
const cleanBtn = $("#clean-btn");
const cleanOverlay = $("#clean-overlay");
const cleanClose = $("#clean-close");
const cleanSummary = $("#clean-summary");
const cleanListEl = $("#clean-list");
const cleanAll = $("#clean-all");
const cleanSelected = $("#clean-selected");
const cleanDelete = $("#clean-delete");

// cleanup state
let cleanItems = []; // {name,path,size,reason,ambiguous,checked}

// ---- helpers -------------------------------------------------------------
function fmtBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  const i = Math.floor(Math.log(n) / Math.log(1024));
  const v = n / Math.pow(1024, i);
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}
const fmtNum = (n) => n.toLocaleString();

function extOf(name) {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : "";
}

// Curated categorical palette ( colorblind-friendly-ish, vivid on dark bg).
const PALETTE = [
  "#5aa0ff", "#7c5cff", "#4fd18b", "#ffb454", "#ff6b6b",
  "#2dd4bf", "#f472b6", "#a78bfa", "#60a5fa", "#facc15",
  "#fb7185", "#34d399", "#c084fc", "#38bdf8", "#fbbf24",
];
function hashIndex(name, mod) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return h % mod;
}
// Base hue chosen per top-level group; nested depth lightens it.
function baseColor(name) {
  return d3.hsl(PALETTE[hashIndex(name, PALETTE.length)]);
}
function colorForNode(topName, depth, maxDepth) {
  const c = baseColor(topName);
  const t = maxDepth > 1 ? depth / maxDepth : 0;
  c.l = Math.min(0.78, c.l + t * 0.22);     // deeper = lighter
  c.s = Math.max(0.35, c.s - t * 0.15);
  return c.toString();
}

function setStatus(msg, { error = false, busy = false } = {}) {
  statusEl.classList.toggle("error", !!error);
  statusEl.innerHTML = (busy ? `<span class="spinner"></span>` : "") +
    `<span>${msg}</span>`;
}

// ---- api -----------------------------------------------------------------
async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

async function startScan(path) {
  const res = await api("/api/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, use_allocated: true }),
  });
  state.jobId = res.id;
  state.rootPath = res.path;
  pollProgress();
}

function pollProgress() {
  clearTimeout(state.pollTimer);
  const tick = async () => {
    try {
      const p = await api(`/api/scan/${state.jobId}`);
      if (p.error) setStatus(`Error: ${p.error}`, { error: true });
      else setStatus(
        `Scanning… ${fmtNum(p.dirs)} folders · ${fmtNum(p.files)} files · ` +
        `${fmtBytes(p.bytes)} · ${p.elapsed}s`, { busy: true });

      if (p.ready) {
        setStatus(
          `Done · ${fmtNum(p.dirs)} folders · ${fmtNum(p.files)} files · ` +
          `${fmtBytes(p.bytes)} · ${p.elapsed}s`);
        scanBtn.disabled = false;
        cleanBtn.disabled = false;
        await loadTree(state.rootPath);
        return;
      }
      state.pollTimer = setTimeout(tick, 400);
    } catch (e) {
      setStatus(`Error: ${e.message}`, { error: true });
      scanBtn.disabled = false;
    }
  };
  tick();
}

async function loadTree(path) {
  const q = path ? `?path=${encodeURIComponent(path)}` : "";
  const data = await api(`/api/tree/${state.jobId}${q}`);
  state.data = data;
  state.currentPath = data.path;
  state.pinned = null;
  render();
  showInspector(data, { parentSize: data.size, isRoot: true });
}

// ---- navigation ----------------------------------------------------------
async function navigateTo(node) {
  if (!node || node.bucket) return;
  if (!node.isDir) return;
  if (!node.children && !node.hasChildren) return;
  try { await loadTree(node.path); }
  catch (e) { setStatus(`Error: ${e.message}`, { error: true }); }
}

function renderBreadcrumbs() {
  crumbsEl.innerHTML = "";
  if (!state.rootPath || !state.currentPath) return;
  const rootPath = state.rootPath.replace(/\/+$/, "");
  const cur = state.currentPath.replace(/\/+$/, "");

  const chain = [{
    label: rootPath === "/" ? "/" : (rootPath.split("/").pop() || rootPath),
    path: state.rootPath,
  }];
  if (cur.startsWith(rootPath) && cur !== rootPath) {
    let acc = rootPath;
    for (const part of cur.slice(rootPath.length).replace(/^\/+/, "").split("/")) {
      acc += "/" + part;
      chain.push({ label: part, path: acc });
    }
  }
  chain.forEach((c, i) => {
    if (i > 0) {
      const sep = document.createElement("span");
      sep.className = "sep"; sep.textContent = "›";
      crumbsEl.appendChild(sep);
    }
    const el = document.createElement("span");
    const isCurrent = i === chain.length - 1;
    el.className = "crumb" + (isCurrent ? " current" : "");
    el.textContent = c.label || c.path;
    if (!isCurrent) el.onclick = () =>
      loadTree(c.path).catch((e) => setStatus(e.message, { error: true }));
    crumbsEl.appendChild(el);
  });
}

// ---- inspector (metadata panel) ------------------------------------------
function showInspector(data, { parentSize, isRoot = false, color } = {}) {
  if (!data) return;
  const pct = parentSize ? (100 * data.size / parentSize) : 100;
  const type = data.bucket ? "Group of small items"
    : data.isDir ? "Folder" : `File${extOf(data.name) ? " · ." + extOf(data.name) : ""}`;
  const childCount = data.children ? data.children.length : 0;

  const swatch = color
    ? `<div class="insp-swatch" style="background:${color}"></div>` : "";

  const rows = [];
  rows.push(["Type", type]);
  if (data.isDir && !data.bucket)
    rows.push(["Direct children", childCount ? fmtNum(childCount) : (data.hasChildren ? "collapsed" : "0")]);
  rows.push(["Share of parent", `${pct.toFixed(pct < 10 ? 1 : 0)}%`]);

  inspectorEl.innerHTML = `
    ${swatch}
    <div>
      <div class="insp-name">${escapeHtml(data.name)}</div>
      <div class="insp-type">${type}</div>
    </div>
    <div>
      <div class="insp-size">${fmtBytes(data.size)}</div>
      <div class="insp-sub">${fmtNum(data.size)} bytes</div>
    </div>
    <div class="insp-bar-wrap">
      <div class="insp-bar-label"><span>share of ${isRoot ? "scan" : "parent"}</span><span>${pct.toFixed(1)}%</span></div>
      <div class="insp-bar"><span style="width:${Math.min(100, pct)}%"></span></div>
    </div>
    <div class="insp-rows">
      ${rows.map(([k, v]) => `<div class="insp-row"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("")}
    </div>
    <div>
      <div class="insp-bar-label"><span>path</span></div>
      <div class="insp-path">${escapeHtml(data.path)}</div>
    </div>
    ${data.isDir && !data.bucket && (childCount || data.hasChildren)
      ? `<div class="insp-hint">Click to zoom into this folder.</div>` : ""}
  `;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- rendering ------------------------------------------------------------
function render() {
  renderBreadcrumbs();
  chartEl.querySelectorAll("svg").forEach((s) => s.remove());
  if (!state.data) return;
  ensureDefs();
  if (state.view === "treemap") renderTreemap();
  else renderSunburst();
}

function dims() {
  const r = chartEl.getBoundingClientRect();
  return { w: Math.max(120, r.width - 36), h: Math.max(120, r.height - 32) };
}

let svgDefsAdded = false;
function ensureDefs() { svgDefsAdded = false; } // defs added per-svg below

function makeHierarchy() {
  return d3.hierarchy(state.data)
    .sum((d) => (d.children ? 0 : d.size))
    .sort((a, b) => b.value - a.value);
}

// -- tooltip (optimized: rAF-throttled, transform-based, no per-move layout) --
let _ttData = null;      // last data shown (skip innerHTML churn)
let _ttX = 0, _ttY = 0;  // last pointer position
let _ttRaf = 0;          // pending animation frame
let _ttW = 0, _ttH = 0;  // cached tooltip size

function showTooltip(evt, data) {
  if (tooltip.hidden) tooltip.hidden = false;
  if (data !== _ttData) {
    _ttData = data;
    tooltip.innerHTML =
      `<div class="t-name">${escapeHtml(data.name)}</div>` +
      `<div class="t-size">${fmtBytes(data.size)}</div>`;
    // size changes only when content changes — measure once here
    _ttW = tooltip.offsetWidth;
    _ttH = tooltip.offsetHeight;
  }
  moveTooltip(evt);
}

function moveTooltip(evt) {
  _ttX = evt.clientX;
  _ttY = evt.clientY;
  if (_ttRaf) return;              // coalesce bursts of mousemove into one frame
  _ttRaf = requestAnimationFrame(() => {
    _ttRaf = 0;
    const pad = 14;
    let x = _ttX + pad, y = _ttY + pad;
    if (x + _ttW > innerWidth) x = _ttX - _ttW - pad;
    if (y + _ttH > innerHeight) y = _ttY - _ttH - pad;
    // translate3d keeps this on the compositor (no reflow)
    tooltip.style.transform = `translate3d(${x}px, ${y}px, 0)`;
  });
}

function hideTooltip() {
  if (_ttRaf) { cancelAnimationFrame(_ttRaf); _ttRaf = 0; }
  _ttData = null;
  tooltip.hidden = true;
}

// -- shared defs: soft shadow + gradients --
function addDefs(svg) {
  const defs = svg.append("defs");
  const f = defs.append("filter").attr("id", "softshadow")
    .attr("x", "-20%").attr("y", "-20%").attr("width", "140%").attr("height", "140%");
  f.append("feDropShadow").attr("dx", 0).attr("dy", 1)
    .attr("stdDeviation", 1.5).attr("flood-color", "#000").attr("flood-opacity", 0.35);
}

/* -------------------- NESTED TREEMAP -------------------- */
function renderTreemap() {
  const { w, h } = dims();
  const root = makeHierarchy();
  const maxDepth = d3.max(root.descendants(), (d) => d.depth) || 1;

  d3.treemap()
    .size([w, h])
    .paddingOuter(3)
    .paddingTop((d) => (d.depth === 0 ? 0 : 18)) // room for group header
    .paddingInner(3)
    .round(true)
    .tile(d3.treemapSquarify.ratio(1))(root);

  const svg = d3.select(chartEl).append("svg").attr("viewBox", `0 0 ${w} ${h}`);
  addDefs(svg);

  const topNameOf = (d) => {
    let a = d; while (a.parent && a.parent.parent) a = a.parent;
    return a.data.name;
  };

  // Draw EVERY node (not just leaves) so folders become visible containers.
  const nodes = root.descendants().filter((d) => d.depth > 0);

  const g = svg.selectAll("g.cell")
    .data(nodes, (d) => d.data.path + ":" + d.depth)
    .join("g")
    .attr("class", (d) => "cell" + (d.children ? " parent" : ""))
    .attr("transform", (d) => `translate(${d.x0},${d.y0})`);

  g.append("rect")
    .attr("rx", 5).attr("ry", 5)
    .attr("width", (d) => Math.max(0, d.x1 - d.x0))
    .attr("height", (d) => Math.max(0, d.y1 - d.y0))
    .attr("fill", (d) => {
      const col = colorForNode(topNameOf(d), d.depth, maxDepth);
      // containers slightly translucent so children read as "inside"
      if (d.children) { const c = d3.color(col); c.opacity = 0.55; return c + ""; }
      return col;
    })
    .attr("filter", (d) => (d.children ? null : "url(#softshadow)"));

  // Group header labels (for folders big enough).
  g.filter((d) => d.children && (d.x1 - d.x0) > 46 && (d.y1 - d.y0) > 22)
    .append("text")
    .attr("class", "group-title")
    .attr("x", 7).attr("y", 13)
    .text((d) => clip(d.data.name, (d.x1 - d.x0) - 14));

  // Leaf labels (name + size when there's room).
  const leaves = g.filter((d) => !d.children);
  leaves.filter((d) => (d.x1 - d.x0) > 46 && (d.y1 - d.y0) > 26)
    .append("text")
    .attr("class", "cell-label")
    .attr("x", 6).attr("y", 15)
    .style("font-size", "11px")
    .text((d) => clip(d.data.name, (d.x1 - d.x0) - 12));
  leaves.filter((d) => (d.x1 - d.x0) > 56 && (d.y1 - d.y0) > 40)
    .append("text")
    .attr("class", "cell-sub")
    .attr("x", 6).attr("y", 29)
    .text((d) => fmtBytes(d.data.size));

  // Interactions.
  g.on("mousemove", (e, d) => showTooltip(e, d.data))
    .on("mouseleave", () => { hideTooltip(); if (!state.pinned) clearHighlight(); })
    .on("mouseover", function (e, d) {
      highlightTreemap(d);
      showInspector(d.data, {
        parentSize: d.parent ? d.parent.data.size : d.data.size,
        isRoot: !d.parent || d.depth === 1 && !d.parent.parent ? false : false,
        color: colorForNode(topNameOf(d), d.depth, maxDepth),
      });
    })
    .on("click", (e, d) => {
      hideTooltip();
      // click a folder (or climb into the folder that owns a file) to zoom
      const target = d.data.isDir ? d.data : (d.parent && d.parent.data);
      if (target && target.isDir) navigateTo(target);
    });

  function highlightTreemap(d) {
    g.classed("dim", (n) => !isAncestorOrSelf(d, n))
      .classed("highlight", (n) => n === d);
  }
}
function isAncestorOrSelf(target, n) {
  let x = target;
  while (x) { if (x === n) return true; x = x.parent; }
  return false;
}
function clearHighlight() {
  d3.select(chartEl).selectAll("g.cell").classed("dim", false).classed("highlight", false);
}
function clip(label, px) {
  const max = Math.max(1, Math.floor(px / 6.6));
  return label.length > max ? label.slice(0, max - 1) + "…" : label;
}

/* -------------------- ANIMATED SUNBURST (optimized) -------------------- */
function renderSunburst() {
  const { w, h } = dims();
  const radius = Math.min(w, h) / 2 - 6;
  const root = makeHierarchy();
  const maxDepth = d3.max(root.descendants(), (d) => d.depth) || 1;
  d3.partition().size([2 * Math.PI, radius])(root);

  const arc = d3.arc()
    .startAngle((d) => d.x0).endAngle((d) => d.x1)
    .padAngle(0.005).padRadius(radius / 3)
    .innerRadius((d) => d.y0).outerRadius((d) => Math.max(d.y0, d.y1 - 1.5));

  const svg = d3.select(chartEl).append("svg")
    .attr("viewBox", `${-w / 2} ${-h / 2} ${w} ${h}`);
  const g = svg.append("g");

  const topNameOf = (d) => { let a = d; while (a.depth > 1) a = a.parent; return a.data.name; };

  const nodes = root.descendants().filter((d) => d.depth > 0 && d.y0 < radius);

  // --- precompute once: a stable id, fill color, and the set of ancestor ids.
  // This turns hover dimming from O(nodes × depth) tree-walks into O(1) lookups.
  nodes.forEach((d, i) => { d._id = i; });
  root._id = -1;
  nodes.forEach((d) => {
    d._fill = colorForNode(topNameOf(d), d.depth, maxDepth);
    const anc = new Set();
    for (let a = d; a; a = a.parent) anc.add(a._id);
    d._anc = anc;              // ids of self + all ancestors
  });

  const arcs = g.selectAll("path.arc")
    .data(nodes)
    .join("path")
    .attr("class", "arc")
    .attr("fill", (d) => d._fill)
    .attr("d", arc);

  // Lightweight grow-in: a single short fade on the whole group (no per-node
  // staggered transitions, which were forcing many repaints).
  g.style("opacity", 0)
    .transition().duration(260).ease(d3.easeCubicOut)
    .style("opacity", 1);

  // --- fast hover: coalesce to one rAF, flip 'sun-dim' via a cheap set test.
  let hoverRaf = 0;
  let hoverTarget = null;
  const domArcs = arcs.nodes();   // raw DOM list for direct className writes

  function applyHover() {
    hoverRaf = 0;
    const t = hoverTarget;
    if (t == null) {
      // clear all dim
      for (const el of domArcs) el.classList.remove("sun-dim");
      return;
    }
    const anc = t._anc;
    for (let i = 0; i < domArcs.length; i++) {
      const el = domArcs[i];
      // dim everything that is NOT an ancestor-or-self of the hovered node
      const keep = anc.has(el.__data__._id);
      // toggle only when needed (classList.toggle with force is cheap but we
      // still avoid layout; className writes don't reflow on their own)
      if (keep) el.classList.remove("sun-dim");
      else el.classList.add("sun-dim");
    }
  }
  function scheduleHover(target) {
    hoverTarget = target;
    if (!hoverRaf) hoverRaf = requestAnimationFrame(applyHover);
  }

  arcs
    .on("mousemove", (e, d) => showTooltip(e, d.data))
    .on("mouseenter", function (e, d) {
      scheduleHover(d);
      showInspector(d.data, {
        parentSize: d.parent ? d.parent.data.size : d.data.size,
        color: d._fill,
      });
    })
    .on("mouseleave", () => {
      hideTooltip();
      if (!state.pinned) scheduleHover(null);
    })
    .on("click", (e, d) => {
      hideTooltip();
      const target = d.data.isDir ? d.data : (d.parent && d.parent.data);
      if (target && target.isDir) navigateTo(target);
    });

  // center: click to go up one level
  const centerR = root.y1 ? root.y1 : radius * 0.12;
  const center = svg.append("g").attr("class", "sun-hit")
    .on("click", () => {
      const cur = state.currentPath.replace(/\/+$/, "");
      const parent = cur.slice(0, cur.lastIndexOf("/")) || "/";
      if (cur !== state.rootPath.replace(/\/+$/, "") && parent.startsWith(state.rootPath.replace(/\/+$/, "").slice(0, parent.length)))
        loadTree(parent).catch(() => {});
    });
  center.append("circle").attr("r", Math.max(28, centerR))
    .attr("fill", "rgba(255,255,255,0.03)").attr("stroke", "var(--border)");
  center.append("text").attr("class", "sun-center-label")
    .attr("text-anchor", "middle").attr("dy", "-0.1em")
    .style("font-size", "13px").text(clip(state.data.name, 120));
  center.append("text").attr("class", "sun-center-sub")
    .attr("text-anchor", "middle").attr("dy", "1.3em")
    .style("font-size", "11px").text(fmtBytes(state.data.size));
}

// ---- events ---------------------------------------------------------------
form.addEventListener("submit", (e) => {
  e.preventDefault();
  const path = pathInput.value.trim();
  if (!path) return;
  scanBtn.disabled = true;
  setStatus("Starting scan…", { busy: true });
  chartEl.querySelectorAll("svg").forEach((s) => s.remove());
  crumbsEl.innerHTML = "";
  cleanBtn.disabled = true;
  startScan(path).catch((err) => {
    setStatus(`Error: ${err.message}`, { error: true });
    scanBtn.disabled = false;
  });
});

document.querySelectorAll(".view-toggle button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".view-toggle button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.view = btn.dataset.view;
    render();
  });
});

let resizeTimer;
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (state.data) render(); }, 150);
});

tooltip.addEventListener("mousemove", (e) => moveTooltip(e));

// ---- cleanup (residual dev artifacts) ------------------------------------
function escapeHtmlC(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function openClean() {
  if (!state.jobId) return;
  cleanOverlay.hidden = false;
  cleanListEl.innerHTML = `<div class="clean-empty">Scanning for artifacts…</div>`;
  cleanSummary.textContent = "";
  cleanAll.checked = false;
  cleanDelete.disabled = true;
  cleanSelected.textContent = "";
  try {
    const res = await api(`/api/artifacts/${state.jobId}`);
    cleanItems = res.items.map((it) => ({ ...it, checked: false }));
    renderCleanList(res);
  } catch (e) {
    cleanListEl.innerHTML =
      `<div class="clean-empty">Error: ${escapeHtmlC(e.message)}</div>`;
  }
}

function renderCleanList(res) {
  if (!cleanItems.length) {
    cleanSummary.innerHTML = "";
    cleanListEl.innerHTML =
      `<div class="clean-empty">✓ No known residual artifacts found.<br>` +
      `<span style="color:var(--muted);font-size:13px">` +
      `(looked for node_modules, .terraform, .npm, .yarn, __pycache__, target, caches, build dirs…)` +
      `</span></div>`;
    return;
  }
  cleanSummary.innerHTML =
    `Found <b>${cleanItems.length}</b> item(s) totalling <b>${fmtBytes(res.total)}</b>. ` +
    `Tick items to delete. These are regenerable, but deletion is permanent.`;

  cleanListEl.innerHTML = cleanItems.map((it, i) => `
    <label class="clean-row" data-i="${i}">
      <input type="checkbox" ${it.checked ? "checked" : ""} />
      <span class="cr-size">${fmtBytes(it.size)}</span>
      <span class="cr-meta">
        <span class="cr-name ${it.ambiguous ? "amb" : ""}">${escapeHtmlC(it.name)}${
          it.ambiguous ? `<span class="clean-badge">verify</span>` : ""}</span>
        <div class="cr-path">${escapeHtmlC(it.path)}</div>
        <div class="cr-why">${escapeHtmlC(it.reason || "")}</div>
      </span>
    </label>`).join("");

  cleanListEl.querySelectorAll(".clean-row input").forEach((cb, i) => {
    cb.addEventListener("change", () => {
      cleanItems[i].checked = cb.checked;
      updateCleanFooter();
    });
  });
  updateCleanFooter();
}

function updateCleanFooter() {
  const sel = cleanItems.filter((it) => it.checked);
  const freed = sel.reduce((a, it) => a + it.size, 0);
  cleanSelected.textContent = sel.length
    ? `${sel.length} selected · ${fmtBytes(freed)}` : "";
  cleanDelete.disabled = sel.length === 0;
  cleanAll.checked = cleanItems.length > 0 && sel.length === cleanItems.length;
}

function closeClean() { cleanOverlay.hidden = true; }

async function deleteSelected() {
  const sel = cleanItems.filter((it) => it.checked);
  if (!sel.length) return;
  const freed = sel.reduce((a, it) => a + it.size, 0);
  const amb = sel.filter((it) => it.ambiguous).length;
  let msg = `Permanently delete ${sel.length} item(s), freeing about ${fmtBytes(freed)}?\n\n`;
  msg += sel.slice(0, 10).map((it) => `• ${it.name}  (${fmtBytes(it.size)})`).join("\n");
  if (sel.length > 10) msg += `\n… and ${sel.length - 10} more`;
  if (amb) msg += `\n\n⚠ ${amb} ambiguous item(s) (build/dist/vendor/venv) selected.`;
  if (!window.confirm(msg)) return;

  cleanDelete.disabled = true;
  cleanDelete.textContent = "Deleting…";
  try {
    const res = await api(`/api/artifacts/${state.jobId}/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: sel.map((it) => it.path) }),
    });
    const errs = res.errors && res.errors.length
      ? `\n${res.errors.length} error(s):\n` +
        res.errors.map((e) => `• ${e.path}: ${e.message}`).join("\n")
      : "";
    window.alert(`Deleted ${res.deleted.length} item(s), freed ${fmtBytes(res.freed)}.${errs}`);
    closeClean();
    if (res.needsRescan) {
      // tree sizes are stale — rescan the same root
      setStatus("Rescanning after cleanup…", { busy: true });
      cleanBtn.disabled = true;
      await startScan(state.rootPath);
    }
  } catch (e) {
    window.alert(`Error: ${e.message}`);
  } finally {
    cleanDelete.textContent = "Delete selected";
    updateCleanFooter();
  }
}

cleanBtn.addEventListener("click", openClean);
cleanClose.addEventListener("click", closeClean);
cleanOverlay.addEventListener("click", (e) => { if (e.target === cleanOverlay) closeClean(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !cleanOverlay.hidden) closeClean();
});
cleanAll.addEventListener("change", () => {
  cleanItems.forEach((it) => (it.checked = cleanAll.checked));
  cleanListEl.querySelectorAll(".clean-row input").forEach((cb) => (cb.checked = cleanAll.checked));
  updateCleanFooter();
});
cleanDelete.addEventListener("click", deleteSelected);

// ---- init -----------------------------------------------------------------
(async function init() {
  try {
    const { home } = await api("/api/home");
    pathInput.value = home;
  } catch (_) {}
  setStatus("Enter a path and click Scan.");
})();
