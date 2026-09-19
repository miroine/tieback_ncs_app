"""
Self-test for PVT Studio's nodal.py — detects the Beggs & Brill errors found
during the TieBack Studio port. Run it inside the PVT Studio repo:

    python pvt_studio_selftest.py path/to/nodal.py

It does two things:
  1. Scans the source for the four buggy patterns (works whatever the function
     names are).
  2. If it can import the module, exercises any Beggs-Brill helper it finds and
     compares the result with the published equations.

Nothing is modified. Fixes are in docs/PVT_STUDIO_NODAL_FIXES.md.
"""
from __future__ import annotations

import importlib.util
import math
import re
import sys

CHECKS = [
    ("C1a", r"ln\(2\.2\s*\*?\s*y\s*-\s*1\.2\)|log\(\s*2\.2\s*\*\s*y\s*-\s*1\.2\s*\)",
     "friction-multiplier branch present — verify it is used ONLY for 1 < y < 1.2",
     r"(1(\.0)?\s*<\s*y\s*<\s*1\.2)|(y\s*>\s*1\.0?\s*and\s*y\s*<\s*1\.2)"),
    ("C1b", r"max\(\s*(denom|denominator|d)\s*,\s*1e-6\s*\)",
     "denominator clipped with max(..., 1e-6): S → −∞ and two-phase friction vanishes for y < 1", None),
    ("C2", r"lambda_l\s*<\s*0\.01\s*:\s*\n\s*(pattern|flow_pattern|regime)\s*=\s*[\"']segregated",
     "λ < 0.01 forced to 'segregated' regardless of Froude number", None),
    ("C3", r"rho_l\s*/\s*\(\s*G_C\s*\*\s*sigma|/\s*\(\s*32\.174\s*\*\s*sigma",
     "liquid velocity number not the published 1.938·v_SL·(ρ_L/σ)^¼", None),
    ("C4a", r"\*\*\s*1\.2048",
     "Standing Rs: γg must multiply the bracket, not be raised to 1.2048 — check the parentheses",
     r"gas_sg\s*\*\s*\(\("),
    ("C4b", r"api\s*/\s*131\.5\s*\+\s*141\.5\s*/\s*131\.5|\(\s*api\s*\+\s*141\.5\s*\)\s*/\s*131\.5",
     "oil specific gravity should be 141.5/(API + 131.5)", None),
]


def s_published(y: float) -> float:
    if 1.0 < y < 1.2:
        return math.log(2.2 * y - 1.2)
    x = math.log(y)
    return x / (-0.0523 + 3.182 * x - 0.8725 * x ** 2 + 0.01853 * x ** 4)


def scan(path: str) -> int:
    src = open(path, encoding="utf-8").read()
    print(f"Scanning {path} ({len(src.splitlines())} lines)\n")
    hits = 0
    for code, pattern, message, guard in CHECKS:
        found = re.search(pattern, src, re.I | re.M)
        if not found:
            print(f"  [{code}] not found — either already fixed or written differently")
            continue
        if guard and re.search(guard, src, re.I):
            print(f"  [{code}] OK — {message.split('—')[0].strip()} appears correctly guarded")
            continue
        line = src[:found.start()].count("\n") + 1
        print(f"  [{code}] LINE {line}: {message}")
        hits += 1
    print(f"\n{hits} suspect pattern(s). See docs/PVT_STUDIO_NODAL_FIXES.md for the corrected code.")
    return hits


def exercise(path: str):
    spec = importlib.util.spec_from_file_location("pvt_nodal", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not import the module ({exc}); source scan only.")
        return
    cand = [n for n in dir(mod) if re.search(r"s_factor|friction_mult|two_phase", n, re.I)]
    if not cand:
        print("\nNo standalone S-factor helper to exercise (it is probably inline).")
        return
    fn = getattr(mod, cand[0])
    print(f"\nExercising {cand[0]}():")
    for y in (0.5, 0.9, 1.1, 2.0, 4.0, 20.0):
        try:
            got, want = fn(y), s_published(y)
            flag = "OK " if abs(got - want) < 1e-6 else "DIFF"
            print(f"  {flag} y={y:<5} got S={got: .4f}  published S={want: .4f}")
        except Exception as exc:  # noqa: BLE001
            print(f"  y={y}: raised {exc}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    n = scan(sys.argv[1])
    exercise(sys.argv[1])
    sys.exit(1 if n else 0)
