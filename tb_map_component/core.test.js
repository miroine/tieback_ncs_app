// node tb_map_component/core.test.js
const C = require("./core.js");
let pass = 0, fail = [];
function check(label, fn) { try { if (fn() === false) throw new Error("returned false"); pass++; } catch (e) { fail.push(label + ": " + e.message); } }
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

check("event seq increments and carries nonce", () => {
  const n = C.eventFactory("abc"); const e1 = n("select", { id: "W1" }, [1, 2, 3, 4]); const e2 = n("delete", { id: "W1" });
  return e1.seq === 1 && e2.seq === 2 && e1.nonce === "abc" && eq(e1.view, [1, 2, 3, 4]) && e2.view === null;
});
check("nonces differ", () => C.makeNonce() !== C.makeNonce());
const rules = { jumper: { well: ["template"], template: ["well"] }, riser: { riser_base: ["host"], host: ["riser_base"] } };
check("edgeAllowed forward", () => C.edgeAllowed(rules, "jumper", "well", "template"));
check("edgeAllowed reverse", () => C.edgeAllowed(rules, "riser", "host", "riser_base"));
check("edgeAllowed rejects", () => !C.edgeAllowed(rules, "riser", "well", "host"));
check("edgeAllowed unknown kind", () => !C.edgeAllowed(rules, "nope", "well", "host"));
const payload = { nodes: [{ id: "A", lat: 60, lon: 2 }, { id: "B", lat: 60, lon: 3 }], edges: [{ id: "E", source: "A", target: "B", route: [[60.2, 2.5]] }] };
check("edgeLatLngs includes endpoints and route", () => eq(C.edgeLatLngs(payload.edges[0], C.nodesById(payload)), [[60, 2], [60.2, 2.5], [60, 3]]));
check("edgeLatLngs missing node -> null", () => C.edgeLatLngs({ source: "A", target: "Z" }, C.nodesById(payload)) === null);
check("insertVertex on first segment goes to index 0", () => {
  const full = [[60, 2], [60.2, 2.5], [60, 3]];
  return eq(C.insertVertex(full, [[60.2, 2.5]], [60.1, 2.2]), [[60.1, 2.2], [60.2, 2.5]]);
});
check("insertVertex on last segment appends", () => {
  const full = [[60, 2], [60.2, 2.5], [60, 3]];
  return eq(C.insertVertex(full, [[60.2, 2.5]], [60.1, 2.8]), [[60.2, 2.5], [60.1, 2.8]]);
});
check("insertVertex into straight edge", () => eq(C.insertVertex([[60, 2], [60, 3]], [], [60.01, 2.5]), [[60.01, 2.5]]));
check("insertVertex does not mutate input", () => { const r = [[60.2, 2.5]]; C.insertVertex([[60, 2], [60.2, 2.5], [60, 3]], r, [60.1, 2.2]); return r.length === 1; });
check("removeVertex", () => eq(C.removeVertex([[1, 1], [2, 2], [3, 3]], 1), [[1, 1], [3, 3]]));
check("removeVertex out of range no-op", () => eq(C.removeVertex([[1, 1]], 5), [[1, 1]]));
check("moveVertex", () => eq(C.moveVertex([[1, 1], [2, 2]], 1, [5, 6]), [[1, 1], [5, 6]]));
check("haversine 1° meridian ≈ 111.2 km", () => Math.abs(C.haversine([60, 3], [61, 3]) - 111195) < 50);
check("polylineLength sums segments", () => Math.abs(C.polylineLength([[60, 3], [61, 3], [62, 3]]) - 2 * C.haversine([60, 3], [61, 3])) < 1e-6);
check("edgeStyle error colour + selected weight", () => {
  const s1 = C.edgeStyle({ kind: "flowline", diameter_in: 12, severity: "error" }, false);
  const s2 = C.edgeStyle({ kind: "flowline", diameter_in: 12 }, true);
  return s1.color === "#EB0037" && s2.weight === s1.weight + 2.5;
});
check("edgeStyle umbilical dashed", () => C.edgeStyle({ kind: "umbilical" }).dashArray === "8 6");
check("nodeSvg all kinds produce svg", () => Object.keys(C.NODE_STYLE).every((k) => C.nodeSvg(k, "", false).html.startsWith("<svg")));
check("nodeSvg severity stroke", () => C.nodeSvg("well", "warning", false).html.indexOf("#E9A23B") > 0);
check("nodeSvg selected ring", () => C.nodeSvg("well", "", true).html.indexOf("stroke-dasharray") > 0);
check("formatLength", () => C.formatLength(950) === "950 m" && C.formatLength(14413) === "14.41 km" && C.formatLength(null) === "");
check("escapeHtml", () => C.escapeHtml('<a href="x">&') === "&lt;a href=&quot;x&quot;&gt;&amp;");
check("bboxOf includes route vertices", () => eq(C.bboxOf(payload), [[60, 2], [60.2, 3]]));
check("bboxOf empty -> null", () => C.bboxOf({ nodes: [] }) === null);
check("symbols exist for every plan-view type", () => ["xt","template","manifold","plet","ssiv","pump","jacket","semisub","fpso","riser_base","ilt"]
  .every((k) => C.symbolShape(k, 30).length > 20));
check("unknown symbol falls back to PLET shape", () => C.symbolShape("nope", 30) === C.symbolShape("plet", 30));
check("footprint: 100 m × 50 m, heading 0 → ±50 m north, ±25 m east", () => {
  const fp = C.footprintPolygon(60.5, 2.6, 100, 50, 0);
  const dLat = Math.abs(fp[0][0] - 60.5) * 110540, dLon = Math.abs(fp[0][1] - 2.6) * 111320 * Math.cos(60.5 * Math.PI / 180);
  return Math.abs(dLat - 50) < 0.5 && Math.abs(dLon - 25) < 0.5;
});
check("footprint rotates with heading 90°", () => {
  const fp = C.footprintPolygon(60.5, 2.6, 100, 50, 90);
  return Math.abs(Math.abs(fp[0][0] - 60.5) * 110540 - 25) < 0.5;
});
check("footprint needs both dimensions", () => C.footprintPolygon(60, 3, 0, 20, 0) === null);
check("offsetLatLngs shifts perpendicular by the given metres", () => {
  const off = C.offsetLatLngs([[60, 3], [60.1, 3]], 100);
  return Math.abs(Math.abs(off[0][1] - 3) * 111320 * Math.cos(60 * Math.PI / 180) - 100) < 1;
});
check("zero offset returns the same geometry", () => C.offsetLatLngs([[60, 3], [61, 3]], 0).length === 2);
check("three lines on one corridor spread symmetrically", () => {
  const o = C.parallelOffsets([{ id: "a", source: "X", target: "Y" }, { id: "b", source: "X", target: "Y" },
    { id: "c", source: "X", target: "Y" }], 60);
  return o.a === -60 && o.b === 0 && o.c === 60;
});
check("edge colour/dash from the catalog override the category default", () => {
  const st = C.edgeStyle({ kind: "utility_line", color: "#9DBA00", dash: "4 4", diameter_in: 0 }, false);
  return st.color === "#9DBA00" && st.dashArray === "4 4";
});
check("error severity still wins over the item colour", () =>
  C.edgeStyle({ kind: "flowline", color: "#00243D", severity: "error" }, false).color === "#EB0037");
check("symbol scale changes the icon size", () => C.nodeSvg("template", "", false, 2).size
  > C.nodeSvg("template", "", false, 1).size);
check("symbol scale is clamped", () => C.nodeSvg("template", "", false, 99).size === C.nodeSvg("template", "", false, 4).size);
check("line scale multiplies weight", () => {
  const a = C.edgeStyle({ kind: "flowline", diameter_in: 12 }, false, { line_scale: 1 });
  const b = C.edgeStyle({ kind: "flowline", diameter_in: 12 }, false, { line_scale: 2 });
  return Math.abs(b.weight - a.weight * 2) < 1e-9;
});
check("thickness_by_diameter off removes the bore term", () => {
  const a = C.edgeStyle({ kind: "flowline", diameter_in: 24 }, false, { line_scale: 1 });
  const b = C.edgeStyle({ kind: "flowline", diameter_in: 24 }, false, { line_scale: 1, thickness_by_diameter: false });
  return b.weight < a.weight && b.weight === C.EDGE_STYLE.flowline.base;
});
check("selection highlight scales too", () => {
  const sel = C.edgeStyle({ kind: "flowline", diameter_in: 8 }, true, { line_scale: 2 });
  const un = C.edgeStyle({ kind: "flowline", diameter_in: 8 }, false, { line_scale: 2 });
  return Math.abs(sel.weight - un.weight - 5) < 1e-9;
});
check("jumperGroup lists what is tied to a structure", () => {
  const pay = { edges: [{ id: "J1", kind: "jumper", source: "W1", target: "T1" },
                        { id: "J2", kind: "jumper", source: "T1", target: "W2" },
                        { id: "F1", kind: "flowline", source: "T1", target: "H1" }] };
  const g = C.jumperGroup(pay, "T1");
  return g.length === 2 && g.indexOf("W1") >= 0 && g.indexOf("W2") >= 0 && g.indexOf("H1") < 0;
});
check("plan-view symbols carry real equipment detail, not a plain shape", () => {
  const xt = C.nodeSvg("xt", "", false, 1).html;
  const tmpl = C.nodeSvg("template", "", false, 1).html;
  const mf = C.nodeSvg("manifold", "", false, 1).html;
  return (xt.match(/<rect/g) || []).length >= 3 && xt.indexOf("<circle") >= 0
    && (tmpl.match(/<circle/g) || []).length >= 8
    && (mf.match(/<line/g) || []).length >= 5;
});
check("rasterBbox unions imported grid bounds", () => {
  const b = C.rasterBbox([{ bounds: [[60.0, 2.0], [60.5, 2.5]] }, { bounds: [[59.5, 2.2], [60.2, 3.0]] }]);
  return b[0][0] === 59.5 && b[0][1] === 2.0 && b[1][0] === 60.5 && b[1][1] === 3.0;
});
check("rasterBbox ignores entries without bounds", () => {
  const b = C.rasterBbox([{ title: "no bounds" }, { bounds: [[60, 2], [61, 3]] }]);
  return b[0][0] === 60 && b[1][1] === 3;
});
check("rasterBbox with nothing to frame returns null", () => {
  return C.rasterBbox([]) === null && C.rasterBbox(undefined) === null && C.rasterBbox([{}]) === null;
});

check("host symbols read as platforms, not boxes", () => {
  const jacket = C.nodeSvg("jacket", "", false, 1).html;
  const semi = C.nodeSvg("semisub", "", false, 1).html;
  const fpso = C.nodeSvg("fpso", "", false, 1).html;
  // jacket: deck, modules, four legs and a helideck with an H
  const legs = (jacket.match(/<circle/g) || []).length;
  const hBars = (jacket.match(/<line/g) || []).length;
  return legs >= 5 && hBars >= 3
    // semi-sub: two pontoons + deck box, four columns
    && (semi.match(/<rect/g) || []).length >= 5 && (semi.match(/<circle/g) || []).length >= 5
    // FPSO: a hull outline with a bow, plus turret
    && fpso.indexOf("<path") >= 0 && fpso.indexOf("Q") >= 0 && (fpso.match(/<circle/g) || []).length >= 2;
});
check("deck markings are drawn in ink, not the white outline stroke", () => {
  // an H drawn with the symbol's own white stroke is invisible on a white deck
  return C.nodeSvg("jacket", "", false, 1).html.indexOf('stroke="#00243D"') >= 0
    && C.nodeSvg("semisub", "", false, 1).html.indexOf('stroke="#00243D"') >= 0;
});
check("a host with no specific symbol still draws as a platform", () => {
  return C.nodeSvg("host", "", false, 1).html === C.nodeSvg("jacket", "", false, 1).html;
});

console.log("core.test.js: " + pass + " passed, " + fail.length + " failed");
fail.forEach((f) => console.log("  FAIL " + f));
process.exit(fail.length ? 1 : 0);
