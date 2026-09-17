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
["wrap", "map", "toolbar", "nodeItem", "edgeItem", "diam", "btnDelete", "btnFit", "empty", "toast", "status", "hint", "coords"]
  .forEach((id) => { els[id] = mkEl(id === "nodeItem" || id === "edgeItem" ? "select" : id === "diam" ? "input" : "div"); });
const modeButtons = ["select", "route", "add", "connect"].map((m) => { const b = mkEl("button"); b.dataset.mode = m; return b; });
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
const mapObj = evented({ createPane: () => ({ style: {} }), setView() { return this; }, getContainer: () => container, invalidateSize() {},
  fitBounds(b) { this.fitted = b; }, removeLayer() {}, getBounds: () => ({ getWest: () => 2, getSouth: () => 60, getEast: () => 3, getNorth: () => 61 }) });
const markers = [], lines = [];
const L = {
  map: () => mapObj,
  tileLayer: () => ({ addTo() { return this; } }),
  control: { layers: () => ({ addTo() { return this; }, addOverlay() {}, removeLayer() {} }), scale: () => ({ addTo() { return this; } }) },
  layerGroup,
  divIcon: (o) => o,
  marker: (ll, opts) => { let pos = { lat: ll[0], lng: ll[1] }; const mk = addable(evented({ opts, getLatLng: () => pos, setLatLng(p) { pos = p; },
    setIcon(i) { this.icon = i; }, dragging: { on: true, enable() { this.on = true; }, disable() { this.on = false; } } })); markers.push(mk); return mk; },
  polyline: (ll, opts) => { const pl = addable(evented({ ll, opts, setLatLngs(x) { this.ll = x; }, setStyle(s) { this.opts = Object.assign({}, this.opts, s); } })); lines.push(pl); return pl; },
  geoJSON: (fc, o) => ({ fc, addTo() { return this; } }),
  circleMarker: () => ({}),
  DomEvent: { stopPropagation() {}, preventDefault() {} },
};
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
modeButtons[2].handlers.click[0]();
check("add mode disables dragging + crosshair", () => markers.every((m) => m.dragging.on === false) && container.classList.set.has("mode-add"));
els.nodeItem.value = "tmpl_4slot";
mapObj.fire("click", { latlng: { lat: 60.6, lng: 2.7 } });
check("map click in add mode → add_node with chosen item", () => { const v = values().pop(); return v.type === "add_node" && v.payload.item_id === "tmpl_4slot" && v.payload.lat === 60.6; });
// connect mode: allowed
modeButtons[3].handlers.click[0]();
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
modeButtons[0].handlers.click[0]();
lines[0].fire("click", { latlng: { lat: 60.51, lng: 2.61 } });
check("click edge → select edge", () => values().pop().payload.id === "J1");
modeButtons[1].handlers.click[0]();
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
console.log("protocol.test.js: " + pass + " passed, " + fail.length + " failed");
fail.forEach((f) => console.log("  FAIL " + f));
process.exit(fail.length ? 1 : 0);
