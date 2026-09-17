/* TieBack Studio map component — pure logic (no DOM, no Leaflet).
 * Loaded in the browser as window.TBCore and in Node tests via require().
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TBCore = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const EDGE_KINDS = ["flowline", "umbilical", "jumper", "riser", "power_cable"];

  // Equinor-derived palette; kind → symbol + colour
  const NODE_STYLE = {
    well:        { shape: "circle",   color: "#00243D", size: 14 },
    template:    { shape: "square",   color: "#243746", size: 22 },
    manifold:    { shape: "square",   color: "#4A6B82", size: 20 },
    plet:        { shape: "diamond",  color: "#007079", size: 14 },
    plem:        { shape: "diamond",  color: "#005F66", size: 18 },
    ilt:         { shape: "diamond",  color: "#3E8A91", size: 12 },
    ssiv:        { shape: "bowtie",   color: "#7D4EBF", size: 16 },
    boosting:    { shape: "hexagon",  color: "#E9A23B", size: 22 },
    compression: { shape: "hexagon",  color: "#C4561B", size: 26 },
    separation:  { shape: "hexagon",  color: "#8C6D1F", size: 26 },
    riser_base:  { shape: "square",   color: "#6F6F6F", size: 14 },
    host:        { shape: "triangle", color: "#EB0037", size: 26 },
  };
  const EDGE_STYLE = {
    flowline:    { color: "#00243D", dash: null,    base: 3 },
    umbilical:   { color: "#E9A23B", dash: "8 6",   base: 2.5 },
    jumper:      { color: "#6F6F6F", dash: null,    base: 4 },
    riser:       { color: "#7D4EBF", dash: null,    base: 4 },
    power_cable: { color: "#C4561B", dash: "2 6",   base: 2.5 },
  };
  const SEVERITY_COLOR = { error: "#EB0037", warning: "#E9A23B", info: "#3E8A91" };

  function makeNonce() {
    const a = Math.random().toString(36).slice(2, 10);
    const b = Date.now().toString(36);
    return a + b;
  }

  function eventFactory(nonce) {
    let seq = 0;
    return function next(type, payload, view) {
      seq += 1;
      return { nonce: nonce, seq: seq, type: type, payload: payload || {}, view: view || null };
    };
  }

  function edgeAllowed(rules, edgeKind, kindA, kindB) {
    const r = (rules && rules[edgeKind]) || {};
    const inA = (r[kindA] || []).indexOf(kindB) >= 0;
    const inB = (r[kindB] || []).indexOf(kindA) >= 0;
    return inA || inB;
  }

  function nodesById(payload) {
    const m = {};
    (payload.nodes || []).forEach(function (n) { m[n.id] = n; });
    return m;
  }

  function edgeLatLngs(edge, byId) {
    const a = byId[edge.source], b = byId[edge.target];
    if (!a || !b) return null;
    return [[a.lat, a.lon]].concat(edge.route || [], [[b.lat, b.lon]]);
  }

  // Local planar approximation (metres) around the segment — adequate for
  // picking the nearest segment on screen; lengths come from Python (Vincenty).
  function project(lat, lon, lat0) {
    const k = Math.cos(lat0 * Math.PI / 180);
    return [lon * 111320 * k, lat * 110540];
  }

  function distPointSeg(p, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const L2 = dx * dx + dy * dy;
    let t = L2 === 0 ? 0 : ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2;
    t = Math.max(0, Math.min(1, t));
    const x = a[0] + t * dx, y = a[1] + t * dy;
    return Math.hypot(p[0] - x, p[1] - y);
  }

  /** Insert a vertex into `route` (intermediate vertices only) at the segment of the
   *  full polyline (incl. endpoints) nearest to `click`. Returns a new route array. */
  function insertVertex(fullLatLngs, route, click) {
    const lat0 = click[0];
    const P = project(click[0], click[1], lat0);
    let best = 0, bestD = Infinity;
    for (let i = 0; i < fullLatLngs.length - 1; i++) {
      const a = project(fullLatLngs[i][0], fullLatLngs[i][1], lat0);
      const b = project(fullLatLngs[i + 1][0], fullLatLngs[i + 1][1], lat0);
      const d = distPointSeg(P, a, b);
      if (d < bestD) { bestD = d; best = i; }
    }
    const out = (route || []).map(function (v) { return [v[0], v[1]]; });
    out.splice(best, 0, [click[0], click[1]]);   // segment i sits between full[i] and full[i+1]
    return out;
  }

  function removeVertex(route, index) {
    const out = (route || []).map(function (v) { return [v[0], v[1]]; });
    if (index >= 0 && index < out.length) out.splice(index, 1);
    return out;
  }

  function moveVertex(route, index, latlng) {
    const out = (route || []).map(function (v) { return [v[0], v[1]]; });
    if (index >= 0 && index < out.length) out[index] = [latlng[0], latlng[1]];
    return out;
  }

  function haversine(a, b) {
    const R = 6371008.8, r = Math.PI / 180;
    const dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  function polylineLength(latlngs) {
    let s = 0;
    for (let i = 0; i < latlngs.length - 1; i++) s += haversine(latlngs[i], latlngs[i + 1]);
    return s;
  }

  function edgeStyle(edge, selected) {
    const s = EDGE_STYLE[edge.kind] || EDGE_STYLE.flowline;
    const d = Number(edge.diameter_in) || 0;
    let weight = s.base + (d > 0 ? Math.min(d, 24) / 6 : 0);
    const color = edge.severity === "error" ? SEVERITY_COLOR.error : s.color;
    if (selected) weight += 2.5;
    return { color: color, weight: weight, dashArray: s.dash, opacity: selected ? 1 : 0.9 };
  }

  function nodeSvg(kind, severity, selected) {
    const s = NODE_STYLE[kind] || NODE_STYLE.plet;
    const z = s.size, h = z / 2, pad = 4, W = z + pad * 2;
    const stroke = severity ? (SEVERITY_COLOR[severity] || "#fff") : "#FFFFFF";
    const sw = severity ? 3 : 1.5;
    const c = W / 2;
    let shape;
    switch (s.shape) {
      case "circle": shape = '<circle cx="' + c + '" cy="' + c + '" r="' + h + '"/>'; break;
      case "square": shape = '<rect x="' + pad + '" y="' + pad + '" width="' + z + '" height="' + z + '" rx="2"/>'; break;
      case "diamond": shape = '<polygon points="' + c + ',' + pad + ' ' + (W - pad) + ',' + c + ' ' + c + ',' + (W - pad) + ' ' + pad + ',' + c + '"/>'; break;
      case "triangle": shape = '<polygon points="' + c + ',' + pad + ' ' + (W - pad) + ',' + (W - pad) + ' ' + pad + ',' + (W - pad) + '"/>'; break;
      case "bowtie": shape = '<polygon points="' + pad + ',' + pad + ' ' + (W - pad) + ',' + (W - pad) + ' ' + (W - pad) + ',' + pad + ' ' + pad + ',' + (W - pad) + '"/>'; break;
      default: { // hexagon
        const pts = [];
        for (let i = 0; i < 6; i++) {
          const ang = Math.PI / 3 * i + Math.PI / 6;
          pts.push((c + h * Math.cos(ang)).toFixed(1) + "," + (c + h * Math.sin(ang)).toFixed(1));
        }
        shape = '<polygon points="' + pts.join(" ") + '"/>';
      }
    }
    const ring = selected ? '<circle cx="' + c + '" cy="' + c + '" r="' + (c - 0.5) + '" fill="none" stroke="#EB0037" stroke-width="1.5" stroke-dasharray="3 2"/>' : "";
    return {
      html: '<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="' + W + '" viewBox="0 0 ' + W + " " + W +
        '"><g fill="' + s.color + '" stroke="' + stroke + '" stroke-width="' + sw + '">' + shape + "</g>" + ring + "</svg>",
      size: W,
    };
  }

  function formatLength(m) {
    if (m == null || isNaN(m)) return "";
    return m >= 1000 ? (m / 1000).toFixed(2) + " km" : Math.round(m) + " m";
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function bboxOf(payload) {
    const n = payload.nodes || [];
    if (!n.length) return null;
    let s = 90, w = 180, N = -90, e = -180;
    n.forEach(function (p) { s = Math.min(s, p.lat); N = Math.max(N, p.lat); w = Math.min(w, p.lon); e = Math.max(e, p.lon); });
    (payload.edges || []).forEach(function (ed) {
      (ed.route || []).forEach(function (v) { s = Math.min(s, v[0]); N = Math.max(N, v[0]); w = Math.min(w, v[1]); e = Math.max(e, v[1]); });
    });
    return [[s, w], [N, e]];
  }

  return {
    EDGE_KINDS, NODE_STYLE, EDGE_STYLE, SEVERITY_COLOR,
    makeNonce, eventFactory, edgeAllowed, nodesById, edgeLatLngs, insertVertex, removeVertex,
    moveVertex, haversine, polylineLength, edgeStyle, nodeSvg, formatLength, escapeHtml, bboxOf,
  };
});
