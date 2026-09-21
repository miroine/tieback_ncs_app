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
  // symbol → plan-view drawing + colour. Keyed by CatalogItem.symbol, falling back to category.
  const NODE_STYLE = {
    xt:          { color: "#00243D", size: 16 },
    well:        { color: "#00243D", size: 16 },
    template:    { color: "#243746", size: 30 },
    manifold:    { color: "#4A6B82", size: 26 },
    plet:        { color: "#007079", size: 18 },
    plem:        { color: "#005F66", size: 20 },
    ilt:         { color: "#3E8A91", size: 14 },
    ssiv:        { color: "#7D4EBF", size: 18 },
    pump:        { color: "#E9A23B", size: 26 },
    boosting:    { color: "#E9A23B", size: 26 },
    compression: { color: "#C4561B", size: 30 },
    separation:  { color: "#8C6D1F", size: 30 },
    riser_base:  { color: "#6F6F6F", size: 18 },
    control:     { color: "#B3801A", size: 18 },
    jacket:      { color: "#EB0037", size: 34 },
    semisub:     { color: "#EB0037", size: 36 },
    fpso:        { color: "#EB0037", size: 38 },
    host:        { color: "#EB0037", size: 34 },
  };
  const EDGE_STYLE = {
    flowline:     { color: "#00243D", dash: null,   base: 3 },
    umbilical:    { color: "#E9A23B", dash: "8 6",  base: 2.5 },
    jumper:       { color: "#6F6F6F", dash: null,   base: 4 },
    riser:        { color: "#7D4EBF", dash: null,   base: 4 },
    power_cable:  { color: "#C4561B", dash: "2 6",  base: 2.5 },
    utility_line: { color: "#9DBA00", dash: "4 4",  base: 2.5 },
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

  function edgeStyle(edge, selected, display) {
    const s = EDGE_STYLE[edge.kind] || EDGE_STYLE.flowline;
    const d = Number(edge.diameter_in) || 0;
    const scale = (display && display.line_scale) || 1;
    const byDia = !display || display.thickness_by_diameter !== false;
    let weight = (s.base + (byDia && d > 0 ? Math.min(d, 24) / 6 : 0)) * scale;
    const base = edge.color || s.color;
    const color = edge.severity === "error" ? SEVERITY_COLOR.error : base;
    if (selected) weight += 2.5 * scale;
    const dash = edge.dash !== undefined && edge.dash !== "" ? edge.dash : (edge.dash === "" && edge.color ? null : s.dash);
    return { color: color, weight: weight, dashArray: dash || null, opacity: selected ? 1 : 0.9 };
  }

  /** Plan-view symbol for one item. Shapes are schematic, not to scale — the
   *  true footprint is drawn separately when the map is zoomed in. */
  function symbolShape(symbol, W, inset) {
    const c = W / 2, p = inset || 4, z = W - 2 * p, r = z / 2;
    const S = {};
    // Christmas tree in plan: guide frame, wellhead bore, production and annulus wings
    // with their valve blocks, and the tree cap over the bore.
    S.xt = '<rect x="' + (c - r * 0.95) + '" y="' + (c - r * 0.95) + '" width="' + (r * 1.9) + '" height="' + (r * 1.9) +
      '" rx="' + (r * 0.25) + '" fill-opacity="0.35"/>' +
      '<circle cx="' + c + '" cy="' + c + '" r="' + (r * 0.52) + '"/>' +
      '<g stroke="#fff" stroke-width="' + Math.max(r * 0.18, 1) + '" stroke-linecap="round">' +
      '<line x1="' + (c - r * 0.95) + '" y1="' + c + '" x2="' + (c + r * 0.95) + '" y2="' + c + '"/>' +
      '<line x1="' + c + '" y1="' + (c - r * 0.95) + '" x2="' + c + '" y2="' + (c + r * 0.95) + '"/></g>' +
      '<g fill="#fff">' +
      '<rect x="' + (c - r * 0.92) + '" y="' + (c - r * 0.22) + '" width="' + (r * 0.34) + '" height="' + (r * 0.44) + '"/>' +
      '<rect x="' + (c + r * 0.58) + '" y="' + (c - r * 0.22) + '" width="' + (r * 0.34) + '" height="' + (r * 0.44) + '"/>' +
      '<circle cx="' + c + '" cy="' + c + '" r="' + (r * 0.2) + '"/></g>';
    // Template in plan: protection structure, four slots with guide funnels, the internal
    // header running between them, and guide posts at the corners.
    {
      const top = p + z * 0.12, h = z * 0.76, sl = z * 0.105;
      let slots = "";
      [[-0.26, -0.17], [0.06, -0.17], [-0.26, 0.17], [0.06, 0.17]].forEach(function (o) {
        const sx = c + o[0] * z + sl, sy = c + o[1] * z;
        slots += '<circle cx="' + sx + '" cy="' + sy + '" r="' + sl + '" fill="#fff" fill-opacity="0.92"/>' +
          '<circle cx="' + sx + '" cy="' + sy + '" r="' + (sl * 0.45) + '" fill="none" stroke-width="' + Math.max(z * 0.03, 0.8) + '"/>';
      });
      let posts = "";
      [[p + z * 0.06, top + h * 0.08], [p + z * 0.94, top + h * 0.08],
       [p + z * 0.06, top + h * 0.92], [p + z * 0.94, top + h * 0.92]].forEach(function (q) {
        posts += '<circle cx="' + q[0] + '" cy="' + q[1] + '" r="' + (z * 0.045) + '" fill="#fff"/>';
      });
      S.template = '<rect x="' + p + '" y="' + top + '" width="' + z + '" height="' + h + '" rx="' + (z * 0.06) + '" fill-opacity="0.9"/>' +
        '<rect x="' + (p + z * 0.05) + '" y="' + (top + h * 0.06) + '" width="' + (z * 0.9) + '" height="' + (h * 0.88) +
        '" rx="' + (z * 0.04) + '" fill="none" stroke="#fff" stroke-width="' + Math.max(z * 0.035, 1) + '"/>' +
        '<line x1="' + (p + z * 0.1) + '" y1="' + c + '" x2="' + (p + z * 0.9) + '" y2="' + c +
        '" stroke="#fff" stroke-width="' + Math.max(z * 0.05, 1.2) + '"/>' + slots + posts;
    }
    // Manifold in plan: skid frame, production header along the axis, branch spools with
    // valve blocks, and the hubs at each end.
    {
      const hh = z * 0.3;
      let branches = "";
      [-0.28, -0.1, 0.08, 0.26].forEach(function (o) {
        const bx = c + o * z + z * 0.08;
        branches += '<line x1="' + bx + '" y1="' + c + '" x2="' + bx + '" y2="' + (c - hh * 1.15) + '" stroke="#fff" stroke-width="' + Math.max(z * 0.04, 1) + '"/>' +
          '<rect x="' + (bx - z * 0.045) + '" y="' + (c - hh * 1.35) + '" width="' + (z * 0.09) + '" height="' + (z * 0.09) + '" fill="#fff"/>';
      });
      S.manifold = '<rect x="' + p + '" y="' + (c - hh) + '" width="' + z + '" height="' + (hh * 2) + '" rx="' + (z * 0.05) + '" fill-opacity="0.9"/>' +
        '<line x1="' + (p + z * 0.04) + '" y1="' + c + '" x2="' + (p + z * 0.96) + '" y2="' + c +
        '" stroke="#fff" stroke-width="' + Math.max(z * 0.09, 1.6) + '"/>' + branches +
        '<rect x="' + p + '" y="' + (c - hh * 0.45) + '" width="' + (z * 0.07) + '" height="' + (hh * 0.9) + '" fill="#fff"/>' +
        '<rect x="' + (W - p - z * 0.07) + '" y="' + (c - hh * 0.45) + '" width="' + (z * 0.07) + '" height="' + (hh * 0.9) + '" fill="#fff"/>';
    }
    S.plet = '<rect x="' + p + '" y="' + (c - z * 0.25) + '" width="' + z * 0.75 + '" height="' + z * 0.5 + '" rx="1.5"/>' +
      '<polygon points="' + (p + z * 0.75) + ',' + (c - z * 0.25) + ' ' + (W - p) + ',' + c + ' ' + (p + z * 0.75) + ',' + (c + z * 0.25) + '"/>';
    S.plem = S.plet;
    S.ilt = '<rect x="' + p + '" y="' + (c - z * 0.18) + '" width="' + z + '" height="' + z * 0.36 + '" rx="1.5"/>' +
      '<rect x="' + (c - z * 0.1) + '" y="' + p + '" width="' + z * 0.2 + '" height="' + z * 0.4 + '"/>';
    S.ssiv = '<circle cx="' + c + '" cy="' + c + '" r="' + r + '"/>' +
      '<polygon fill="#fff" points="' + (c - r * 0.55) + ',' + (c - r * 0.55) + ' ' + (c + r * 0.55) + ',' + (c + r * 0.55) + ' ' +
      (c + r * 0.55) + ',' + (c - r * 0.55) + ' ' + (c - r * 0.55) + ',' + (c + r * 0.55) + '"/>';
    S.pump = '<rect x="' + p + '" y="' + (c - z * 0.3) + '" width="' + z + '" height="' + z * 0.6 + '" rx="2"/>' +
      '<circle cx="' + (c - z * 0.18) + '" cy="' + c + '" r="' + z * 0.17 + '" fill="#fff"/>' +
      '<circle cx="' + (c + z * 0.18) + '" cy="' + c + '" r="' + z * 0.17 + '" fill="#fff"/>';
    // control / distribution unit: a diamond with a white "hub" — lines fan out from it
    S.control = '<polygon points="' + c + ',' + p + ' ' + (p + z) + ',' + c + ' ' + c + ',' + (p + z) + ' ' + p + ',' + c + '"/>' +
      '<circle cx="' + c + '" cy="' + c + '" r="' + z * 0.14 + '" fill="#fff"/>';
    S.riser_base = '<rect x="' + p + '" y="' + p + '" width="' + z + '" height="' + z + '" rx="2"/>' +
      '<circle cx="' + c + '" cy="' + c + '" r="' + z * 0.22 + '" fill="#fff"/>';
    // Markings that sit on a white deck need their own dark ink — inheriting the
    // symbol's white stroke would make them invisible.
    const ink = "#00243D";
    // Fixed steel jacket in plan: deck outline, process modules, the four jacket
    // legs as rings, and the helideck on the corner.
    S.jacket = '<rect x="' + (p + z * 0.06) + '" y="' + (p + z * 0.1) + '" width="' + (z * 0.76) +
      '" height="' + (z * 0.8) + '" rx="' + (z * 0.05) + '"/>' +
      '<g fill="#fff">' +
      '<rect x="' + (p + z * 0.14) + '" y="' + (p + z * 0.2) + '" width="' + (z * 0.26) + '" height="' + (z * 0.22) + '"/>' +
      '<rect x="' + (p + z * 0.14) + '" y="' + (p + z * 0.5) + '" width="' + (z * 0.26) + '" height="' + (z * 0.22) + '"/>' +
      '<rect x="' + (p + z * 0.48) + '" y="' + (p + z * 0.2) + '" width="' + (z * 0.22) + '" height="' + (z * 0.52) + '"/></g>' +
      '<g fill="none" stroke="' + ink + '" stroke-width="' + Math.max(z * 0.045, 1) + '">' +
      '<circle cx="' + (p + z * 0.17) + '" cy="' + (p + z * 0.21) + '" r="' + (z * 0.055) + '"/>' +
      '<circle cx="' + (p + z * 0.71) + '" cy="' + (p + z * 0.21) + '" r="' + (z * 0.055) + '"/>' +
      '<circle cx="' + (p + z * 0.17) + '" cy="' + (p + z * 0.79) + '" r="' + (z * 0.055) + '"/>' +
      '<circle cx="' + (p + z * 0.71) + '" cy="' + (p + z * 0.79) + '" r="' + (z * 0.055) + '"/></g>' +
      '<circle cx="' + (p + z * 0.84) + '" cy="' + (p + z * 0.74) + '" r="' + (z * 0.16) + '" fill="#fff" stroke="' + ink +
      '" stroke-width="' + Math.max(z * 0.035, 0.9) + '"/>' +
      '<g stroke="' + ink + '" stroke-width="' + Math.max(z * 0.045, 1) + '" stroke-linecap="round">' +
      '<line x1="' + (p + z * 0.79) + '" y1="' + (p + z * 0.68) + '" x2="' + (p + z * 0.79) + '" y2="' + (p + z * 0.8) + '"/>' +
      '<line x1="' + (p + z * 0.89) + '" y1="' + (p + z * 0.68) + '" x2="' + (p + z * 0.89) + '" y2="' + (p + z * 0.8) + '"/>' +
      '<line x1="' + (p + z * 0.79) + '" y1="' + (p + z * 0.74) + '" x2="' + (p + z * 0.89) + '" y2="' + (p + z * 0.74) + '"/></g>';
    // Semi-submersible in plan: two pontoons, four columns showing through the
    // deck box, process modules and the helideck forward.
    {
      const pw = z * 0.16, py1 = p + z * 0.05, py2 = p + z * 0.79;
      let cols = "";
      [[0.26, 0.13], [0.72, 0.13], [0.26, 0.87], [0.72, 0.87]].forEach(function (o) {
        cols += '<circle cx="' + (p + o[0] * z) + '" cy="' + (p + o[1] * z) + '" r="' + (z * 0.1) +
          '" fill="#fff" stroke="' + ink + '" stroke-width="' + Math.max(z * 0.035, 0.9) + '"/>';
      });
      S.semisub = '<rect x="' + (p + z * 0.08) + '" y="' + py1 + '" width="' + (z * 0.84) + '" height="' + pw + '" rx="' + (pw / 2) + '"/>' +
        '<rect x="' + (p + z * 0.08) + '" y="' + py2 + '" width="' + (z * 0.84) + '" height="' + pw + '" rx="' + (pw / 2) + '"/>' +
        '<rect x="' + (p + z * 0.14) + '" y="' + (p + z * 0.28) + '" width="' + (z * 0.72) + '" height="' + (z * 0.44) + '" rx="' + (z * 0.04) + '"/>' +
        cols +
        '<g fill="#fff"><rect x="' + (p + z * 0.2) + '" y="' + (p + z * 0.35) + '" width="' + (z * 0.16) + '" height="' + (z * 0.3) + '"/>' +
        '<rect x="' + (p + z * 0.4) + '" y="' + (p + z * 0.35) + '" width="' + (z * 0.12) + '" height="' + (z * 0.3) + '"/></g>' +
        '<circle cx="' + (p + z * 0.71) + '" cy="' + c + '" r="' + (z * 0.13) + '" fill="#fff" stroke="' + ink +
        '" stroke-width="' + Math.max(z * 0.035, 0.9) + '"/>' +
        '<g stroke="' + ink + '" stroke-width="' + Math.max(z * 0.04, 1) + '" stroke-linecap="round">' +
        '<line x1="' + (p + z * 0.666) + '" y1="' + (c - z * 0.055) + '" x2="' + (p + z * 0.666) + '" y2="' + (c + z * 0.055) + '"/>' +
        '<line x1="' + (p + z * 0.754) + '" y1="' + (c - z * 0.055) + '" x2="' + (p + z * 0.754) + '" y2="' + (c + z * 0.055) + '"/>' +
        '<line x1="' + (p + z * 0.666) + '" y1="' + c + '" x2="' + (p + z * 0.754) + '" y2="' + c + '"/></g>';
    }
    // FPSO in plan: hull with the bow to the right, turret and helideck forward,
    // process modules across the deck, flare stack aft.
    S.fpso = '<path d="M ' + (p + z * 0.02) + ' ' + (c - z * 0.21) + ' L ' + (p + z * 0.6) + ' ' + (c - z * 0.21) +
      ' Q ' + (W - p) + ' ' + c + ' ' + (p + z * 0.6) + ' ' + (c + z * 0.21) + ' L ' + (p + z * 0.02) + ' ' + (c + z * 0.21) + ' Z"/>' +
      '<g fill="#fff">' +
      '<rect x="' + (p + z * 0.22) + '" y="' + (c - z * 0.155) + '" width="' + (z * 0.075) + '" height="' + (z * 0.31) + '"/>' +
      '<rect x="' + (p + z * 0.33) + '" y="' + (c - z * 0.155) + '" width="' + (z * 0.075) + '" height="' + (z * 0.31) + '"/>' +
      '<rect x="' + (p + z * 0.44) + '" y="' + (c - z * 0.155) + '" width="' + (z * 0.075) + '" height="' + (z * 0.31) + '"/></g>' +
      '<circle cx="' + (p + z * 0.63) + '" cy="' + c + '" r="' + (z * 0.105) + '" fill="#fff" stroke="' + ink +
      '" stroke-width="' + Math.max(z * 0.035, 0.9) + '"/>' +
      '<circle cx="' + (p + z * 0.63) + '" cy="' + c + '" r="' + (z * 0.04) + '" fill="' + ink + '"/>' +
      '<rect x="' + (p + z * 0.05) + '" y="' + (c - z * 0.115) + '" width="' + (z * 0.11) + '" height="' + (z * 0.23) + '" fill="#fff"/>' +
      '<polygon fill="' + ink + '" points="' + (p + z * 0.105) + ',' + (c - z * 0.095) + ' ' +
      (p + z * 0.135) + ',' + (c + z * 0.075) + ' ' + (p + z * 0.075) + ',' + (c + z * 0.075) + '"/>';
    S.host = S.jacket;          // a host with no specific symbol is still a platform
    return S[symbol] || S.plet;
  }

  // `accent` is the well's main-fluid colour (oil green, gas red, condensate amber,
  // injectors blue/purple). It is drawn as a ring round the symbol so the fluid
  // reads at a glance without recolouring the equipment itself.
  function nodeSvg(symbol, severity, selected, scale, accent) {
    const s = NODE_STYLE[symbol] || NODE_STYLE.plet;
    const k = Math.max(0.2, Math.min(4, scale || 1));
    // With a fluid ring the canvas grows rather than the symbol shrinking, so a
    // ringed tree is the same size as a plain one. The ring clears the guide
    // frame's corners when pad > 0.17·size + ~2 px, hence the scaling margin.
    const base = Math.round(s.size * k);
    const pad = accent ? Math.max(6, 0.19 * base + 2.5) : 4;
    const W = Math.round(base + pad * 2);
    const inset = (W - base) / 2;
    const stroke = severity ? (SEVERITY_COLOR[severity] || "#fff") : "#FFFFFF";
    const sw = severity ? 2.5 : 1.2;
    const ring = selected
      ? '<rect x="1" y="1" width="' + (W - 2) + '" height="' + (W - 2) + '" fill="none" stroke="#EB0037" stroke-width="1.5" stroke-dasharray="3 2"/>'
      : "";
    const halo = accent
      ? '<circle cx="' + (W / 2) + '" cy="' + (W / 2) + '" r="' + (W / 2 - 1.8) + '" fill="none" stroke="' + accent +
        '" stroke-width="3"/>'
      : "";
    return {
      html: '<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="' + W + '" viewBox="0 0 ' + W + " " + W +
        '">' + halo + '<g fill="' + s.color + '" stroke="' + stroke + '" stroke-width="' + sw + '">' + symbolShape(symbol, W, inset) + "</g>" + ring + "</svg>",
      size: W,
    };
  }

  /** Lateral offsets (metres) for lines sharing the same pair of end nodes, so a
   *  flowline, umbilical and chemical line between the same points stay readable. */
  function parallelOffsets(edges, spacing) {
    const groups = {};
    (edges || []).forEach(function (e) {
      const key = [e.source, e.target].sort().join("|");
      (groups[key] = groups[key] || []).push(e.id);
    });
    const out = {};
    Object.keys(groups).forEach(function (k) {
      const ids = groups[k];
      ids.forEach(function (id, i) { out[id] = (i - (ids.length - 1) / 2) * (spacing || 60); });
    });
    return out;
  }

  function offsetLatLngs(latlngs, offsetM) {
    if (!offsetM) return latlngs;
    const out = [];
    for (let i = 0; i < latlngs.length; i++) {
      const a = latlngs[Math.max(i - 1, 0)], b = latlngs[Math.min(i + 1, latlngs.length - 1)];
      const lat0 = latlngs[i][0];
      const k = Math.cos(lat0 * Math.PI / 180);
      let dx = (b[1] - a[1]) * k * 111320, dy = (b[0] - a[0]) * 110540;
      const L = Math.hypot(dx, dy) || 1;
      const nx = -dy / L, ny = dx / L;                 // left-hand normal
      out.push([lat0 + offsetM * ny / 110540, latlngs[i][1] + offsetM * nx / (111320 * k)]);
    }
    return out;
  }

  /** True-scale plan footprint corners for a rectangular structure. */
  function footprintPolygon(lat, lon, lengthM, widthM, headingDeg) {
    if (!(lengthM > 0 && widthM > 0)) return null;
    const h = (headingDeg || 0) * Math.PI / 180;
    const k = Math.cos(lat * Math.PI / 180);
    const corners = [[lengthM / 2, widthM / 2], [lengthM / 2, -widthM / 2],
                     [-lengthM / 2, -widthM / 2], [-lengthM / 2, widthM / 2]];
    return corners.map(function (c) {
      const x = c[0] * Math.sin(h) + c[1] * Math.cos(h);     // east
      const y = c[0] * Math.cos(h) - c[1] * Math.sin(h);     // north
      return [lat + y / 110540, lon + x / (111320 * k)];
    });
  }

  /** Nodes tied to `id` by a jumper — they travel with a structure when it is moved. */
  function jumperGroup(payload, id) {
    const out = [];
    (payload.edges || []).forEach(function (e) {
      if (e.kind !== "jumper") return;
      if (e.source === id) out.push(e.target);
      else if (e.target === id) out.push(e.source);
    });
    return out.filter(function (v, i, a) { return a.indexOf(v) === i; });
  }

  /** Nodes tied to `id` by a jumper — they travel with a structure when it is moved. */
  function jumperGroup(payload, id) {
    const out = [];
    (payload.edges || []).forEach(function (e) {
      if (e.kind !== "jumper") return;
      if (e.source === id) out.push(e.target);
      else if (e.target === id) out.push(e.source);
    });
    return out.filter(function (v, i, a) { return a.indexOf(v) === i; });
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

  // ── sketch geometry: measure, circle, polygon ──
  const R_EARTH = 6371008.8;

  /** Point at `dist_m` along `bearing_deg` from (lat, lon), on the sphere. */
  function destination(lat, lon, bearing_deg, dist_m) {
    const r = Math.PI / 180, d = dist_m / R_EARTH, b = bearing_deg * r, p1 = lat * r, l1 = lon * r;
    const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
    const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
    return [p2 / r, ((l2 / r + 540) % 360) - 180];
  }

  /** Ring approximating a circle of `radius_m` — a true circle on the ground,
   *  which on a Mercator map is not a screen circle at 60°N. */
  function circleRing(center, radius_m, n) {
    const k = n || 72, out = [];
    for (let i = 0; i < k; i++) out.push(destination(center[0], center[1], (360 * i) / k, radius_m));
    return out;
  }

  /** Area of a lat/lon ring on the sphere, m² (Chamberlain & Duquette 2007). */
  function geodesicArea(ring) {
    if (!ring || ring.length < 3) return 0;
    const r = Math.PI / 180;
    let s = 0;
    for (let i = 0; i < ring.length; i++) {
      const a = ring[i], b = ring[(i + 1) % ring.length];
      s += (b[1] - a[1]) * r * (2 + Math.sin(a[0] * r) + Math.sin(b[0] * r));
    }
    return Math.abs(s * R_EARTH * R_EARTH / 2);
  }

  function formatArea(m2) {
    if (m2 >= 1e6) return (m2 / 1e6).toFixed(m2 >= 1e8 ? 0 : 2) + " km²";
    if (m2 >= 1e4) return (m2 / 1e4).toFixed(1) + " ha";
    return Math.round(m2) + " m²";
  }

  /** Web-Mercator bounds of an XYZ tile, metres — for ArcGIS dynamic `export`. */
  function tileBounds3857(x, y, z) {
    const half = 20037508.342789244, size = (2 * half) / Math.pow(2, z);
    const xmin = -half + x * size, ymax = half - y * size;
    return [xmin, ymax - size, xmin + size, ymax];
  }

  /** One 256 px tile from an ArcGIS MapServer/ImageServer that has no tile cache. */
  function arcgisExportUrl(base, x, y, z, layers) {
    const b = tileBounds3857(x, y, z);
    const root = String(base).replace(/\/+$/, "").replace(/\/(export|exportImage)$/, "");
    const isImage = /\/ImageServer$/i.test(root);
    const q = "bbox=" + b.join(",") + "&bboxSR=3857&imageSR=3857&size=256,256&format=png32&transparent=true&f=image" +
      (layers ? "&layers=show:" + encodeURIComponent(layers) : "");
    return root + (isImage ? "/exportImage?" : "/export?") + q;
  }

  // Union of imported-grid bounds — used to frame the map when no layout is drawn yet.
  function rasterBbox(rasters) {
    const list = (rasters || []).filter(function (r) { return r && r.bounds && r.bounds.length === 2; });
    if (!list.length) return null;
    let s = 90, w = 180, N = -90, e = -180;
    list.forEach(function (r) {
      s = Math.min(s, r.bounds[0][0]); w = Math.min(w, r.bounds[0][1]);
      N = Math.max(N, r.bounds[1][0]); e = Math.max(e, r.bounds[1][1]);
    });
    return [[s, w], [N, e]];
  }

  return {
    EDGE_KINDS, NODE_STYLE, EDGE_STYLE, SEVERITY_COLOR,
    makeNonce, eventFactory, edgeAllowed, nodesById, edgeLatLngs, insertVertex, removeVertex,
    moveVertex, haversine, polylineLength, edgeStyle, nodeSvg, symbolShape, parallelOffsets,
    offsetLatLngs, footprintPolygon, jumperGroup, formatLength, escapeHtml, bboxOf, rasterBbox,
    destination, circleRing, geodesicArea, formatArea, tileBounds3857, arcgisExportUrl,
  };
});
