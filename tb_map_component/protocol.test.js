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
["wrap", "map", "toolbar", "nodeItem", "edgeItem", "diam", "grp", "symScale", "lineScale", "selCount", "btnDelete", "btnFit", "empty", "toast", "status", "hint", "coords"]
  .forEach((id) => { els[id] = mkEl(id === "nodeItem" || id === "edgeItem" ? "select" : ["diam", "grp", "symScale", "lineScale"].indexOf(id) >= 0 ? "input" : "div"); });
els.grp.checked = true;
els.symScale.value = "1"; els.lineScale.value = "1";
[els.symScale, els.lineScale].forEach((e) => { e.matches = () => false; });
const modeButtons = ["select", "move", "route", "add", "connect", "pick"].map((m) => { const b = mkEl("button"); b.dataset.mode = m; return b; });
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
  tileLayer: Object.assign((url, o) => { L._tiles = (L._tiles || []).concat([o]); return { addTo() { return this; } }; },
    { wms: (url, o) => ({ url, o, addTo() { return this; } }) }),
  control: { layers: (base, over) => { L._overlays = over || {}; return { addTo() { return this; }, addOverlay(l, name) { L._added = (L._added || []).concat(name); }, removeLayer() {} }; },
             scale: () => ({ addTo() { return this; } }) },
  layerGroup,
  divIcon: (o) => o,
  // Real Leaflet creates marker.dragging in _initInteraction() during onAdd — NOT in the
  // constructor. The fake mirrors that so "disable before add" fails here like it does live.
  marker: (ll, opts) => {
    let pos = { lat: ll[0], lng: ll[1] };
    const mk = evented({ opts, getLatLng: () => pos, setLatLng(p) { pos = p; }, setIcon(i) { this.icon = i; },
      bindTooltip() { return this; },
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

console.log("protocol.test.js: " + pass + " passed, " + fail.length + " failed");
fail.forEach((f) => console.log("  FAIL " + f));
process.exit(fail.length ? 1 : 0);
