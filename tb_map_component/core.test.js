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
console.log("core.test.js: " + pass + " passed, " + fail.length + " failed");
fail.forEach((f) => console.log("  FAIL " + f));
process.exit(fail.length ? 1 : 0);
