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

// -- tooltip --
function showTooltip(evt, data) {
  tooltip.hidden = false;
  tooltip.innerHTML =
    `<div class="t-name">${escapeHtml(data.name)}</div>` +
    `<div class="t-size">${fmtBytes(data.size)}</div>`;
  moveTooltip(evt);
}
function moveTooltip(evt) {
  const pad = 14, r = tooltip.getBoundingClientRect();
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + r.width > innerWidth) x = evt.clientX - r.width - pad;
  if (y + r.height > innerHeight) y = evt.clientY - r.height - pad;
  tooltip.style.left = x + "px"; tooltip.style.top = y + "px";
}
const hideTooltip = () => (tooltip.hidden = true);

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

/* -------------------- ANIMATED SUNBURST -------------------- */
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
  addDefs(svg);
  const g = svg.append("g");

  const topNameOf = (d) => { let a = d; while (a.depth > 1) a = a.parent; return a.data.name; };

  const arcs = g.selectAll("path.arc")
    .data(root.descendants().filter((d) => d.depth > 0 && d.y0 < radius))
    .join("path")
    .attr("class", "arc")
    .attr("fill", (d) => colorForNode(topNameOf(d), d.depth, maxDepth))
    .attr("d", arc)
    .style("opacity", 0);

  // grow-in animation
  arcs.transition().duration(500).delay((d) => d.depth * 60).style("opacity", 1);

  arcs
    .on("mousemove", (e, d) => showTooltip(e, d.data))
    .on("mouseleave", () => { hideTooltip(); if (!state.pinned) arcs.classed("sun-dim", false); })
    .on("mouseover", function (e, d) {
      arcs.classed("sun-dim", (n) => !isAncestorOrSelf(d, n));
      showInspector(d.data, {
        parentSize: d.parent ? d.parent.data.size : d.data.size,
        color: colorForNode(topNameOf(d), d.depth, maxDepth),
      });
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

// ---- init -----------------------------------------------------------------
(async function init() {
  try {
    const { home } = await api("/api/home");
    pathInput.value = home;
  } catch (_) {}
  setStatus("Enter a path and click Scan.");
})();
