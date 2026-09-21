// Executes index.html's inline script in Node with a fake DOM + fake Leaflet,
// then drives user actions and asserts the Streamlit protocol messages.
const fs = require("fs"), vm = require("vm"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join("\n");

function mkEl(tag) {
  const el = { tagName: tag.toUpperCase(), style: {}, dataset: {}, children: [], attrs: {}, handlers: {},
    textContent: "", disabled: false, value: "", className: "",
    classList: { set: new Set(), add(...c) { c.forEach((x) => this.set.add(x)); }, remove(...c) { c.forEach((x) => this.set.delete(x)); } },
    appendChild(c) { this.children.push(c); if (this.tagName === "SELECT" && !this.value) { const o = allOptions(this)[0]; if (o) this.value = o.value; } return c; },
    addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); },
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; },
    set innerHTML(v) { this.children = []; this.value = ""; }, get innerHTML() { return ""; },
    get selectedOptions() { const o = allOptions(this).find((x) => x.value === this.value) || allOptions(this)[0]; return o ? [o] : []; },
  };
  return el;
}
function allOptions(sel) { const out = []; (function walk(n) { n.children.forEach((c) => { if (c.tagName === "OPTION") out.push(c); walk(c); }); })(sel); return out; }
const els = {};
["wrap", "map", "toolbar", "nodeItem", "edgeItem", "diam", "grp", "symScale", "lineScale", "selCount", "btnDelete", "btnDup", "btnBookmark", "btnFit", "empty", "toast", "status", "hint", "coords"]
  .forEach((id) => { els[id] = mkEl(id === "nodeItem" || id === "edgeItem" ? "select" : ["diam", "grp", "symScale", "lineScale"].indexOf(id) >= 0 ? "input" : "div"); });
els.grp.checked = true;
els.symScale.value = "1"; els.lineScale.value = "1";
[els.symScale, els.lineScale].forEach((e) => { e.matches = () => false; });
const modeButtons = ["select", "move", "route", "add", "connect", "pick", "measure", "circle", "polygon", "line"].map((m) => { const b = mkEl("button"); b.dataset.mode = m; return b; });
const docHandlers = {};
const document = {
  getElementById: (id) => els[id],
  querySelectorAll: (q) => (q === "button[data-mode]" ? modeButtons : []),
  createElement: mkEl,
  addEventListener: (t, f) => { (docHandlers[t] = docHandlers[t] || []).push(f); },
};
// ── fake Leaflet ──
function evented(o) { o.h = {}; o.on = function (t, f) { (this.h[t] = this.h[t] || []).push(f); return this; }; o.fire = function (t, e) { (this.h[t] || []).forEach((f) => f(e || {})); }; return o; }
const groups = [];
function layerGroup() { const g = { layers: [], addTo() { return this; }, clearLayers() { this.layers = []; }, removeLayer(l) { this.layers = this.layers.filter((x) => x !== l); } }; groups.push(g); return g; }
function addable(o) { o.addTo = function (g) { if (g && g.layers) g.layers.push(this); return this; }; o.bindTooltip = function () { return this; }; return o; }
const container = { classList: { set: new Set(), add(c) { this.set.add(c); }, remove(...c) { c.forEach((x) => this.set.delete(x)); } } };
const mapObj = evented({ createPane: () => ({ style: {} }), setView() { return this; }, opts: null, getContainer: () => container, invalidateSize() {}, getZoom: () => 15,
  fitBounds(b) { this.fitted = b; }, removeLayer() {}, getBounds: () => ({ getWest: () => 2, getSouth: () => 60, getEast: () => 3, getNorth: () => 61 }) });
const markers = [], lines = [], polygons = [];
const L = {
  map: (id, opts) => { mapObj.opts = opts; return mapObj; },
  tileLayer: Object.assign((url, o) => { const l = { url, o, addTo(m) { (L._onMap = L._onMap || []).push(this); return this; } };
      L._tiles = (L._tiles || []).concat([o]); L._tileLayers = (L._tileLayers || []).concat([l]); return l; },
    { wms: (url, o) => { const l = { url, o, wms: true, addTo() { return this; } }; L._wms = (L._wms || []).concat([l]); return l; } }),
  TileLayer: { extend: (proto) => function (url, o) { this.options = o; this.getTileUrl = proto.getTileUrl;
      this.addTo = function () { return this; }; L._exports = (L._exports || []).concat([this]); } },
  control: { layers: (base, over) => { L._bases = Object.assign({}, base || {}); L._overlays = over || {};
      return { addTo() { return this; }, addOverlay(l, name) { L._added = (L._added || []).concat(name); },
               addBaseLayer(l, name) { L._bases[name] = l; L._addedBase = (L._addedBase || []).concat(name); },
               removeLayer() {} }; },
             scale: () => ({ addTo() { return this; } }) },
  layerGroup,
  divIcon: (o) => o,
  // Real Leaflet creates marker.dragging in _initInteraction() during onAdd — NOT in the
  // constructor. The fake mirrors that so "disable before add" fails here like it does live.
  marker: (ll, opts) => {
    let pos = { lat: ll[0], lng: ll[1] };
    const mk = evented({ opts, getLatLng: () => pos, setLatLng(p) { pos = p; }, setIcon(i) { this.icon = i; },
      bindTooltip(html) { this._tip = html; return this; },
      addTo(g) { if (g && g.layers) g.layers.push(this);
        this.dragging = { on: true, enable() { this.on = true; }, disable() { this.on = false; } }; return this; } });
    markers.push(mk); return mk;
  },
  polyline: (ll, opts) => { const pl = addable(evented({ ll, opts, setLatLngs(x) { this.ll = x; }, setStyle(s) { this.opts = Object.assign({}, this.opts, s); } })); lines.push(pl); return pl; },
  geoJSON: (fc, o) => { L._geo = (L._geo || []).concat([{ fc, o }]); return { fc, addTo() { return this; } }; },
  polygon: (pts, o) => { const p = addable(evented({ pts, o })); polygons.push(p); return p; },
  circleMarker: () => ({}),
  imageOverlay: (url, bounds, o) => { const lyr = { url, bounds, o, addTo() { return this; } };
    L._images = (L._images || []).concat([{ url, bounds, o, layer: lyr }]); return lyr; },
  DomEvent: { stopPropagation() {}, preventDefault() {} },
};
const C = require("./core.js");
const sent = [];
const win = { parent: { postMessage: (m) => sent.push(m) }, TBCore: require("./core.js"), L, handlers: {},
  addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); } };
const ctx = vm.createContext({ window: win, document, L, console, setTimeout: () => 0, clearTimeout: () => {}, Math, Date, JSON, Number, String, Object, Array, Set });
vm.runInContext(inline, ctx);

let pass = 0, fail = [];
const check = (label, fn) => { try { if (fn() === false) throw new Error("returned false"); pass++; } catch (e) { fail.push(label + ": " + e.message); } };
const values = () => sent.filter((m) => m.type === "streamlit:setComponentValue").map((m) => m.value);
function render(args) { win.handlers.message.forEach((f) => f({ data: { type: "streamlit:render", args } })); }

check("componentReady sent with apiVersion 1", () => sent[0].isStreamlitMessage && sent[0].type === "streamlit:componentReady" && sent[0].apiVersion === 1);
const rules = { jumper: { well: ["template"], template: ["well"] } };
const palette = { node_items: [{ id: "xt_hxt_10k", name: "Horizontal XT", kind: "well" }, { id: "tmpl_4slot", name: "4-slot template", kind: "template" }],
  edge_items: [{ id: "jumper_rigid", name: "Rigid jumper", kind: "jumper", min_d: 0, max_d: 0 }, { id: "fl_rigid_cs", name: "CS flowline", kind: "flowline", min_d: 4, max_d: 20 }] };
const payload = { nodes: [{ id: "W1", label: "A-1", item: "HXT", kind: "well", lat: 60.5, lon: 2.6, severity: "" },
  { id: "T1", label: "Template", item: "Tmpl", kind: "template", lat: 60.51, lon: 2.61, severity: "warning" }],
  edges: [{ id: "J1", label: "J1", item: "Jumper", kind: "jumper", source: "W1", target: "T1", route: [], length_m: 0, severity: "" }] };
render({ payload, palette, rules, overlays: [{ type: "FeatureCollection", features: [], title: "Fields", color: "#9DBA00" }], selected: null, height: 500, rev: "r1", fit_token: 0 });

check("frame height set to height+2", () => sent.some((m) => m.type === "streamlit:setFrameHeight" && m.height === 502));
check("2 markers and 1 line drawn", () => markers.length === 2 && lines.length === 1);
check("fitBounds on first render", () => !!mapObj.fitted);
check("palette options populated", () => allOptions(els.nodeItem).length === 2 && allOptions(els.edgeItem).length === 2);
check("jumper (no diameter) disables diameter input", () => els.diam.disabled === true);

// select by clicking node
markers[0].fire("click", {});
check("click node → select event", () => { const v = values().pop(); return v.type === "select" && v.payload.id === "W1" && v.seq === 1 && Array.isArray(v.view); });
check("delete button enabled after select", () => els.btnDelete.disabled === false);
// drag
markers[0].setLatLng({ lat: 60.52, lng: 2.62 }); markers[0].fire("drag"); markers[0].fire("dragend");
check("drag updates connected edge live", () => lines[0].ll[0][0] === 60.52);
check("dragend → move_node with coordinates", () => { const v = values().pop(); return v.type === "move_node" && v.payload.lat === 60.52 && v.payload.lon === 2.62 && v.seq === 2; });
// add mode
modeButtons.find((b) => b.dataset.mode === "add").handlers.click[0]();
check("add mode disables dragging + crosshair", () => markers.every((m) => m.dragging.on === false) && container.classList.set.has("mode-add"));
els.nodeItem.value = "tmpl_4slot";
mapObj.fire("click", { latlng: { lat: 60.6, lng: 2.7 } });
check("map click in add mode → add_node with chosen item", () => { const v = values().pop(); return v.type === "add_node" && v.payload.item_id === "tmpl_4slot" && v.payload.lat === 60.6; });
// connect mode: allowed
modeButtons.find((b) => b.dataset.mode === "connect").handlers.click[0]();
els.edgeItem.value = "jumper_rigid";
markers[0].fire("click", {}); markers[1].fire("click", {});
check("connect well→template → add_edge", () => { const v = values().pop(); return v.type === "add_edge" && v.payload.source === "W1" && v.payload.target === "T1" && v.payload.diameter_in === 0; });
// connect disallowed (flowline not in rules) → no event, toast
const before = values().length;
els.edgeItem.value = "fl_rigid_cs"; els.edgeItem.handlers.change[0]();
markers[0].fire("click", {}); markers[1].fire("click", {});
check("disallowed connection blocked client-side with message", () => values().length === before && els.toast.style.display === "block");
check("flowline enables diameter input within range", () => els.diam.disabled === false);
// route mode on selected edge
modeButtons.find((b) => b.dataset.mode === "select").handlers.click[0]();
lines[0].fire("click", { latlng: { lat: 60.51, lng: 2.61 } });
check("click edge → select edge", () => values().pop().payload.id === "J1");
modeButtons.find((b) => b.dataset.mode === "route").handlers.click[0]();
lines[0].fire("click", { latlng: { lat: 60.515, lng: 2.615 } });
check("route mode click on selected edge → set_route with inserted vertex", () => { const v = values().pop(); return v.type === "set_route" && v.payload.route.length === 1; });
// keyboard delete
docHandlers.keydown[0]({ key: "Delete", target: { tagName: "BODY" }, preventDefault() {} });
check("Delete key → delete event for selection", () => { const v = values().pop(); return v.type === "delete" && v.payload.id === "J1"; });
check("seq strictly increasing", () => { const s = values().map((v) => v.seq); return s.every((x, i) => i === 0 || x > s[i - 1]); });
// re-render with same rev must not redraw; new rev redraws
const nm = markers.length;
render({ payload, palette, rules, overlays: [], selected: null, height: 500, rev: "r1", fit_token: 0 });
check("same rev → no redraw", () => markers.length === nm);
render({ payload: { nodes: payload.nodes.slice(0, 1), edges: [] }, palette, rules, overlays: [], selected: "W1", height: 500, rev: "r2", fit_token: 0 });
check("new rev → redraw from Python state", () => markers.length === nm + 1);
check("marker dragging disabled only after add (regression: place mode wiped the map)", () => {
  markers.length = 0; polygons.length = 0;
  modeButtons.find((b) => b.dataset.mode === "add").handlers.click[0]();                       // Place mode
  render({ payload: { nodes: payload.nodes.concat([{ id: "P1", label: "PLET", item: "PLET", kind: "plet", symbol: "plet",
    lat: 60.53, lon: 2.63, severity: "", footprint: [12, 6], heading: 0 }]), edges: payload.edges },
    palette, rules, overlays: [], selected: "P1", height: 500, rev: "r3", fit_token: 0 });
  return markers.length === 3 && markers.every((m) => m.dragging && m.dragging.on === false);
});
check("true-scale footprints drawn when zoomed in", () => polygons.length >= 1);
check("bathymetry WMS overlays registered", () => Object.keys(L._overlays).some((k) => /bathymetry/i.test(k))
  && L._overlays["Bathymetry (EMODnet)"].o.layers === "mean_multicolour");
check("parallel lines between the same nodes are offset apart", () => {
  const off = C.parallelOffsets([{ id: "a", source: "X", target: "Y" }, { id: "b", source: "Y", target: "X" }], 60);
  return off.a !== off.b && Math.abs(off.a - off.b) === 60;
});
check("Sodir overlay style uses per-feature colour and pattern", () => {
  L._geo = [];
  render({ payload, palette, rules, selected: null, height: 500, rev: "r4", fit_token: 0,
    overlays: [{ type: "FeatureCollection", geometry: "polygon", title: "Discoveries", color: "#888",
      features: [{ type: "Feature", geometry: { type: "Polygon", coordinates: [[[2, 60], [2, 61], [3, 61], [2, 60]]] },
        properties: { _label: "D1", _fill: "#0BBE00", _outline: "#828282" } },
        { type: "Feature", geometry: { type: "Polygon", coordinates: [[[4, 60], [4, 61], [5, 61], [4, 60]]] },
          properties: { _label: "D2", _fill: "#FF0000", _pattern: "oil_gas" } }] }] });
  const st = L._geo[L._geo.length - 1].o.style;
  return st(L._geo[L._geo.length - 1].fc.features[0]).fillColor === "#0BBE00"
    && st(L._geo[L._geo.length - 1].fc.features[1]).className === "hatch-oil_gas";
});
check("map allows zooming past the Ocean basemap's native levels", () =>
  mapObj.opts.maxZoom === 19 && L._tiles.some((o) => o.maxNativeZoom === 13 && o.maxZoom === 19)
  && L._tiles.every((o) => !o.maxZoom || o.maxZoom >= 19));
check("smoothed as-laid shape is drawn when Python supplies it", () => {
  lines.length = 0;
  render({ payload: { nodes: payload.nodes, edges: [Object.assign({}, payload.edges[0],
    { shape: [[60.5, 2.6], [60.505, 2.605], [60.51, 2.61]], smooth: true })] },
    palette, rules, overlays: [], selected: null, height: 500, rev: "r9", fit_token: 0 });
  return lines[0].ll.length === 3;
});
check("Move mode keeps markers draggable", () => {
  markers.length = 0;
  render({ payload, palette, rules, overlays: [], selected: null, height: 500, rev: "rmove", fit_token: 0 });
  modeButtons.find((b) => b.dataset.mode === "move").handlers.click[0]();
  return markers.length === 2 && markers.every((m) => m.dragging && m.dragging.on === true);
});
check("dragging a template emits move_group and carries its wells on screen", () => {
  const payload2 = { nodes: [
      { id: "T1", label: "Template", item: "T", kind: "template", symbol: "template", lat: 60.5, lon: 2.6, severity: "", footprint: [30, 20], heading: 0 },
      { id: "W1", label: "A-1", item: "XT", kind: "well", symbol: "xt", lat: 60.501, lon: 2.601, severity: "", footprint: [5, 5], heading: 0 }],
    edges: [{ id: "J1", label: "J1", item: "Jumper", kind: "jumper", source: "W1", target: "T1", route: [], length_m: 0, severity: "" }] };
  markers.length = 0;
  render({ payload: payload2, palette, rules, overlays: [], selected: null, height: 500, rev: "rg", fit_token: 0 });
  const tmpl = markers[0];
  tmpl.fire("dragstart");
  tmpl.setLatLng({ lat: 60.52, lng: 2.62 });
  tmpl.fire("drag");
  const wellMoved = Math.abs(markers[1].getLatLng().lat - 60.521) < 1e-9;
  tmpl.fire("dragend");
  const v = values().pop();
  return wellMoved && v.type === "move_group" && v.payload.id === "T1";
});
check("with-wells unticked falls back to a plain move", () => {
  els.grp.checked = false;
  const tmpl = markers[0];
  tmpl.fire("dragstart"); tmpl.setLatLng({ lat: 60.53, lng: 2.63 }); tmpl.fire("drag"); tmpl.fire("dragend");
  els.grp.checked = true;
els.symScale.value = "1"; els.lineScale.value = "1";
[els.symScale, els.lineScale].forEach((e) => { e.matches = () => false; });
  return values().pop().type === "move_node";
});
check("Pick point emits a pick event with the clicked position", () => {
  modeButtons.find((b) => b.dataset.mode === "pick").handlers.click[0]();
  mapObj.fire("click", { latlng: { lat: 61.1, lng: 3.3 } });
  const v = values().pop();
  return v.type === "pick" && v.payload.lat === 61.1 && v.payload.lon === 3.3;
});
check("size sliders redraw locally and emit one display event on release", () => {
  markers.length = 0;
  render({ payload: Object.assign({}, payload, { display: { symbol_scale: 1, line_scale: 1, thickness_by_diameter: true } }),
    palette, rules, overlays: [], selected: null, height: 500, rev: "rdisp", fit_token: 0 });
  const before = values().length;
  els.symScale.value = "2.2";
  els.symScale.handlers.input[0]();                       // live redraw, no event
  const redrew = markers.length === 4;                    // two more markers drawn
  const noEvent = values().length === before;
  els.symScale.handlers.change[0]();                      // released
  const v = values().pop();
  return redrew && noEvent && v.type === "display" && v.payload.symbol_scale === 2.2;
});
check("payload display settings prime the sliders", () => {
  render({ payload: Object.assign({}, payload, { display: { symbol_scale: 0.6, line_scale: 1.8, thickness_by_diameter: true } }),
    palette, rules, overlays: [], selected: null, height: 500, rev: "rdisp2", fit_token: 0 });
  return Number(els.symScale.value) === 0.6 && Number(els.lineScale.value) === 1.8;
});
check("shift-click builds a multi-selection and dragging it emits move_many", () => {
  markers.length = 0;
  modeButtons.find((b) => b.dataset.mode === "select").handlers.click[0]();
  render({ payload, palette, rules, overlays: [], selected: null, height: 500, rev: "rmulti", fit_token: 0 });
  markers[0].fire("click", {});
  markers[1].fire("click", { originalEvent: { shiftKey: true } });
  const counted = els.selCount.textContent === "2 selected";
  markers[1].fire("dragstart");
  markers[1].setLatLng({ lat: 60.7, lng: 2.8 });
  markers[1].fire("drag");
  markers[1].fire("dragend");
  const v = values().pop();
  return counted && v.type === "move_many" && v.payload.ids.length === 2 && v.payload.anchor === "T1";
});
check("a click on an overlay polygon reaches the map instead of being swallowed", () => {
  L._geo = [];
  modeButtons.find((b) => b.dataset.mode === "add").handlers.click[0]();
  render({ payload, palette, rules, selected: null, height: 500, rev: "rclick", fit_token: 0,
    overlays: [{ type: "FeatureCollection", geometry: "polygon", title: "Discoveries", color: "#0BBE00",
      features: [{ type: "Feature", geometry: { type: "Polygon", coordinates: [[[2, 60], [2, 61], [3, 61], [2, 60]]] },
                   properties: { _label: "D1" } }] }] });
  const layer = { on: (t, f) => { layer._h = f; }, bindTooltip() { return this; } };
  L._geo[L._geo.length - 1].o.onEachFeature({ properties: { _label: "D1" } }, layer);
  layer._h({ latlng: { lat: 60.9, lng: 2.9 } });
  const v = values().pop();
  return v.type === "add_node" && v.payload.lat === 60.9;
});
check("hidden elements are not drawn", () => {
  markers.length = 0; lines.length = 0;
  render({ payload: { nodes: payload.nodes.map((n, i) => Object.assign({}, n, { hidden: i === 0 })),
                      edges: payload.edges.map((e) => Object.assign({}, e, { hidden: true })) },
    palette, rules, overlays: [], selected: null, height: 500, rev: "rhide", fit_token: 0 });
  return markers.length === 1 && lines.length === 0;
});
check("an imported grid is added as an image overlay in its own pane", () => {
  L._images = [];
  L._added = [];
  render({ payload, palette, rules, overlays: [], selected: null, height: 500, rev: "rgrid1", fit_token: 0,
    rasters: [{ title: "seabed.grd", url: "data:image/png;base64,AAAA", bounds: [[60.4, 2.5], [60.6, 2.8]], opacity: 0.7 }] });
  const im = L._images[0];
  return L._images.length === 1 && im.o.pane === "rasters" && im.o.opacity === 0.7
    && im.o.interactive === false && im.bounds[1][1] === 2.8
    && (L._added || []).indexOf("seabed.grd") >= 0;
});
check("a grid with no url or bounds is skipped, not drawn empty", () => {
  L._images = [];
  render({ payload, palette, rules, overlays: [], selected: null, height: 500, rev: "rgrid2", fit_token: 0,
    rasters: [{ title: "broken" }, { title: "ok", url: "data:image/png;base64,AAAA", bounds: [[60, 2], [61, 3]] }] });
  return L._images.length === 1 && L._images[0].o.opacity === 1;
});
check("grid images are cleared before redrawing, not stacked", () => {
  const removed = [];
  mapObj.removeLayer = (l) => removed.push(l);
  L._images = [];
  render({ payload, palette, rules, overlays: [], selected: null, height: 500, rev: "rgrid3", fit_token: 0,
    rasters: [{ title: "a.grd", url: "u", bounds: [[60, 2], [61, 3]] }] });
  const first = L._images[0];
  render({ payload, palette, rules, overlays: [], selected: null, height: 500, rev: "rgrid4", fit_token: 0,
    rasters: [{ title: "b.grd", url: "u2", bounds: [[60, 2], [61, 3]] }] });
  mapObj.removeLayer = () => {};
  return L._images.length === 2 && removed.indexOf(first.layer) >= 0;
});
check("with nothing drawn yet the map frames the imported grid", () => {
  mapObj.fitted = null;
  render({ payload: { nodes: [], edges: [] }, palette, rules, overlays: [], selected: null, height: 500,
    rev: "rgrid5", fit_token: 99,
    rasters: [{ title: "a.grd", url: "u", bounds: [[59.5, 1.5], [60.5, 3.5]] }] });
  return !!mapObj.fitted && mapObj.fitted[0][0] === 59.5 && mapObj.fitted[1][1] === 3.5;
});

check("a saved concept is drawn in its own pane, dashed, over the active routes", () => {
  L._geo = [];
  render({ payload, palette, rules, selected: null, height: 500, rev: "rconc1", fit_token: 0,
    overlays: [{ type: "FeatureCollection", title: "Concept: B", color: "#7D4EBF", kind: "concept",
      geometry: "line", dash: "6 5", weight: 3, point_radius: 5,
      features: [{ type: "Feature", geometry: { type: "LineString", coordinates: [[2.6, 60.5], [2.7, 60.6]] },
                   properties: { _fill: "#7D4EBF", _label: "FL1 — B" } },
                 { type: "Feature", geometry: { type: "Point", coordinates: [2.6, 60.5] },
                   properties: { _fill: "#7D4EBF", _label: "W1 — B" } }] }] });
  const g = L._geo[L._geo.length - 1];
  const st = g.o.style(g.fc.features[0]);
  return g.o.pane === "concepts" && st.dashArray === "6 5" && st.weight === 3
    && st.color === "#7D4EBF" && st.fillOpacity === 0;
});
check("an ordinary imported layer keeps the overlays pane and no dash", () => {
  L._geo = [];
  render({ payload, palette, rules, selected: null, height: 500, rev: "rconc2", fit_token: 0,
    overlays: [{ type: "FeatureCollection", title: "Blocks", color: "#4A6B82", geometry: "polygon",
      features: [{ type: "Feature", geometry: { type: "Polygon", coordinates: [[[2, 60], [2, 61], [3, 61], [2, 60]]] },
                   properties: {} }] }] });
  const g = L._geo[L._geo.length - 1];
  const st = g.o.style(g.fc.features[0]);
  return g.o.pane === "overlays" && !st.dashArray && st.weight === 1;
});
check("concept points are drawn bigger than ordinary layer points", () => {
  let big = null, small = null;
  L.circleMarker = (ll, o) => { big = o; return {}; };
  render({ payload, palette, rules, selected: null, height: 500, rev: "rconc3", fit_token: 0,
    overlays: [{ type: "FeatureCollection", title: "C", kind: "concept", point_radius: 5, features: [] }] });
  const g1 = L._geo[L._geo.length - 1];
  g1.o.pointToLayer({ properties: {} }, [60, 2]);
  const concept = big;
  render({ payload, palette, rules, selected: null, height: 500, rev: "rconc4", fit_token: 0,
    overlays: [{ type: "FeatureCollection", title: "L", features: [] }] });
  const g2 = L._geo[L._geo.length - 1];
  g2.o.pointToLayer({ properties: {} }, [60, 2]);
  small = big;
  return concept.radius === 5 && concept.pane === "concepts"
    && small.radius === 4 && small.pane === "overlays";
});

check("a well's tooltip names its fluid, where it came from, and its reservoir", () => {
  markers.length = 0;
  render({ payload: { nodes: [{ id: "W9", label: "G-1", item: "HXT", kind: "well", lat: 60.5, lon: 2.6,
      severity: "", fluid: "gas", fluid_source: "from reservoir Garn", fluid_color: "#EB0037", reservoir: "Garn" }],
      edges: [] }, palette, rules, overlays: [], selected: null, height: 500, rev: "rfluid1", fit_token: 0 });
  const tip = markers[markers.length - 1]._tip || "";
  return tip.indexOf("Main fluid: <b>gas</b>") >= 0 && tip.indexOf("from reservoir Garn") >= 0
    && tip.indexOf("Reservoir: Garn") >= 0;
});
check("the well icon is drawn with its fluid ring", () => {
  markers.length = 0;
  render({ payload: { nodes: [{ id: "W9", label: "G-1", item: "HXT", kind: "well", symbol: "xt", lat: 60.5, lon: 2.6,
      severity: "", fluid: "gas", fluid_color: "#EB0037" }], edges: [] },
    palette, rules, overlays: [], selected: null, height: 500, rev: "rfluid2", fit_token: 0 });
  const mk = markers[markers.length - 1];
  return mk.opts.icon.html.indexOf('stroke="#EB0037"') >= 0;
});

const esc = () => (docHandlers.keydown || []).forEach((f) => f({ key: "Escape", target: {} }));
check("Duplicate is disabled with nothing selected", () => {
  esc();
  render({ payload, palette, rules, overlays: [], selected: null, height: 500, rev: "rdup0", fit_token: 0 });
  return els.btnDup.disabled === true;
});
check("Duplicate sends the selected structure and the 'with wells' choice", () => {
  esc();
  render({ payload, palette, rules, overlays: [], selected: "T1", height: 500, rev: "rdup1", fit_token: 0 });
  els.grp.checked = true;
  els.btnDup.handlers.click[0]();
  const v = values().pop();
  return v.type === "duplicate" && v.payload.ids.length === 1 && v.payload.ids[0] === "T1"
    && v.payload.with_group === true;
});
check("Duplicate sends a multi-selection as one event", () => {
  esc();
  markers.length = 0;
  render({ payload, palette, rules, overlays: [], selected: null, height: 500, rev: "rdup2", fit_token: 0 });
  markers.forEach((mk) => mk.fire("click", { originalEvent: { shiftKey: true } }));
  els.btnDup.handlers.click[0]();
  const v = values().pop();
  return v.type === "duplicate" && v.payload.ids.length === 2;
});
check("a plain click replaces a shift-selection, so Duplicate copies only what you see selected", () => {
  esc();
  markers.length = 0;
  render({ payload, palette, rules, overlays: [], selected: null, height: 500, rev: "rdup2b", fit_token: 0 });
  markers.forEach((mk) => mk.fire("click", { originalEvent: { shiftKey: true } }));   // select both
  markers[0].fire("click", {});                                                    // then plain-click one
  els.btnDup.handlers.click[0]();
  const v = values().pop();
  return v.type === "duplicate" && v.payload.ids.length === 1;
});
check("freshly duplicated items arrive selected, ready to drag", () => {
  esc();
  const p2 = { nodes: payload.nodes.concat([{ id: "T9", label: "Template (2)", item: "Tmpl", kind: "template",
      lat: 60.51, lon: 2.65, severity: "" }, { id: "W9", label: "A-1 (2)", item: "HXT", kind: "well",
      lat: 60.5, lon: 2.64, severity: "" }]), edges: payload.edges, select_many: ["T9", "W9"] };
  render({ payload: p2, palette, rules, overlays: [], selected: "T9", height: 500, rev: "rdup3", fit_token: 0 });
  return els.selCount.textContent === "2 selected" && els.btnDup.disabled === false;
});
// ── map tools: measure, circle, polygon, line, bookmark, base maps, URL layers ──
const modeBtn = (m) => modeButtons.find((b) => b.dataset.mode === m).handlers.click[0]();
const clickAt = (lat, lng) => mapObj.fire("click", { latlng: { lat, lng } });
const dbl = () => mapObj.fire("dblclick", { latlng: { lat: 0, lng: 0 } });
function freshMap(extra) {
  esc();
  render(Object.assign({ payload: Object.assign({}, payload, { annotations: [] }), palette, rules, overlays: [],
    selected: null, height: 500, rev: "rtools" + Math.random(), fit_token: 0 }, extra || {}));
}
check("measure reports the distance and saves nothing", () => {
  freshMap();
  modeBtn("measure");
  const n0 = values().length;
  clickAt(60.0, 2.0); clickAt(60.0, 2.1); clickAt(60.0, 2.1); dbl();       // a double-click lands twice
  const expect = C.haversine([60, 2], [60, 2.1]);
  return values().length === n0 && els.hint.textContent.indexOf(C.formatLength(expect)) >= 0
    && els.hint.textContent.indexOf("1 leg") >= 0;
});
check("a circle is saved with its ground radius", () => {
  freshMap();
  modeBtn("circle");
  clickAt(60.0, 2.0); clickAt(60.0, 2.018);
  const v = values().pop();
  const r = C.haversine([60, 2], [60, 2.018]);
  return v.type === "add_annotation" && v.payload.kind === "circle" && Math.abs(v.payload.radius_m - r) < 1e-6
    && v.payload.center[0] === 60;
});
check("a polygon is saved on double-click, without the double-click's repeated corner", () => {
  freshMap();
  modeBtn("polygon");
  clickAt(60.0, 2.0); clickAt(60.0, 2.1); clickAt(60.05, 2.05); clickAt(60.05, 2.05); dbl();
  const v = values().pop();
  return v.type === "add_annotation" && v.payload.kind === "polygon" && v.payload.coords.length === 3;
});
check("a polygon needs three corners", () => {
  freshMap();
  modeBtn("polygon");
  const n0 = values().length;
  clickAt(60.0, 2.0); clickAt(60.0, 2.1); dbl();
  return values().length === n0;
});
check("a line is saved with its points", () => {
  freshMap();
  modeBtn("line");
  clickAt(60.0, 2.0); clickAt(60.02, 2.05); clickAt(60.04, 2.07); dbl();
  const v = values().pop();
  return v.type === "add_annotation" && v.payload.kind === "line" && v.payload.coords.length === 3;
});
check("Esc abandons a half-drawn polygon", () => {
  freshMap();
  modeBtn("polygon");
  const n0 = values().length;
  clickAt(60.0, 2.0); clickAt(60.0, 2.1);
  esc();
  modeBtn("polygon"); clickAt(60.3, 2.3); dbl();
  return values().length === n0;                     // nothing from the abandoned one, nothing from 1 point
});
check("Bookmark sends the current view and base map", () => {
  freshMap();
  els.btnBookmark.handlers.click[0]();
  const v = values().pop();
  return v.type === "bookmark" && v.payload.view.length === 4 && v.payload.basemap === "Ocean (Esri)";
});
check("saved sketches are drawn, and a selected one is deleted with Delete", () => {
  polygons.length = 0;
  esc();
  render({ payload: Object.assign({}, payload, { annotations: [
      { id: "SK1", kind: "circle", center: [60.5, 2.6], radius_m: 500, label: "500 m zone", color: "#C4561B", measure: "circle r = 500 m" },
      { id: "SK2", kind: "polygon", coords: [[60, 2], [60, 2.1], [60.1, 2.1]], label: "", measure: "polygon" }] }),
    palette, rules, overlays: [], selected: null, height: 500, rev: "rsk1", fit_token: 0 });
  const circle = polygons.find((p) => p.pts.length === 72);
  if (!circle || polygons.length < 2) return false;
  circle.fire("click", {});
  els.btnDelete.handlers.click[0]();
  const v = values().pop();
  return v.type === "delete_annotation" && v.payload.id === "SK1";
});
check("placing equipment on top of a sketch still places it", () => {
  polygons.length = 0;
  esc();
  render({ payload: Object.assign({}, payload, { annotations: [
      { id: "SK1", kind: "polygon", coords: [[60, 2], [60, 2.2], [60.2, 2.2]], measure: "polygon" }] }),
    palette, rules, overlays: [], selected: null, height: 500, rev: "rsk2", fit_token: 0 });
  modeBtn("add");
  polygons[polygons.length - 1].fire("click", { latlng: { lat: 60.1, lng: 2.15 } });
  const v = values().pop();
  return v.type === "add_node" && v.payload.lat === 60.1;
});
check("changing the base map is remembered", () => {
  freshMap();
  mapObj.fire("baselayerchange", { name: "Sjøkart (Kartverket)" });
  const v = values().pop();
  return v.type === "basemap" && v.payload.name === "Sjøkart (Kartverket)";
});
check("Kartverket nautical chart and Esri base maps are offered", () => {
  return ["Sjøkart (Kartverket)", "Topographic (Esri)", "Light grey (Esri)", "Dark grey (Esri)", "Satellite (Esri)"]
    .every((n) => n in L._bases);
});
check("maps added by URL: XYZ and WMS as overlays, ArcGIS service drawn with export", () => {
  L._wms = []; L._exports = []; L._added = [];
  freshMap({ custom_layers: [
    { kind: "xyz", url: "https://tiles.example.com/{z}/{x}/{y}.png", name: "My tiles", overlay: true, opacity: 0.8 },
    { kind: "wms", url: "https://example.com/wms", layers: "depth", name: "Depth WMS", overlay: true },
    { kind: "arcgis_export", url: "https://example.com/arcgis/rest/services/Pipes/MapServer", name: "Pipes", overlay: true, layers: "" }] });
  const ex = L._exports[L._exports.length - 1];
  const tileUrl = ex.getTileUrl.call(ex, { x: 1, y: 1, z: 1 });
  return (L._added || []).indexOf("My tiles") >= 0 && (L._added || []).indexOf("Depth WMS") >= 0
    && L._wms.some((w) => w.o.layers === "depth")
    && tileUrl.indexOf("https://example.com/arcgis/rest/services/Pipes/MapServer/export?bbox=") === 0;
});
check("a map added by URL as a base map joins the base-map list", () => {
  L._addedBase = [];
  freshMap({ custom_layers: [{ kind: "arcgis_tile", url: "https://x.com/arcgis/rest/services/B/MapServer/tile/{z}/{y}/{x}",
                               name: "Company base", overlay: false }] });
  return (L._addedBase || []).indexOf("Company base") >= 0;
});
check("a bookmark's view is flown to, and wins over fitting the layout", () => {
  mapObj.fitted = null;
  freshMap({ view_target: { token: 77, bbox: [1.0, 59.0, 3.0, 61.0] }, fit_token: 5 });
  const b = mapObj.fitted;
  return b && b[0][0] === 59.0 && b[0][1] === 1.0 && b[1][0] === 61.0 && b[1][1] === 3.0;
});
check("the same view target is not flown to twice", () => {
  mapObj.fitted = null;
  freshMap({ view_target: { token: 77, bbox: [1.0, 59.0, 3.0, 61.0] }, fit_token: 5 });   // same fit token too
  return mapObj.fitted === null;
});

console.log("protocol.test.js: " + pass + " passed, " + fail.length + " failed");
fail.forEach((f) => console.log("  FAIL " + f));
process.exit(fail.length ? 1 : 0);
