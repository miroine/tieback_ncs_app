"""Run all TieBack Studio test suites (Python engine, bridge, JS component, headless UI)."""
import shutil, subprocess, sys
PY = ["test_geo.py", "test_catalog.py", "test_network.py", "test_schedule.py", "test_cost.py",
      "test_ncs.py", "test_import.py", "test_grid.py", "test_map.py", "test_multiphase.py", "test_thermal.py", "test_bathymetry.py", "test_well.py", "test_costio.py", "test_cases.py", "test_report.py", "test_tiein.py", "test_basis.py", "test_viability.py", "test_chemistry.py", "test_fluids.py", "test_mapextras.py", "test_share.py",
      "test_flowassurance.py", "ui_test/test_ui_smoke.py"]
JS = ["tb_map_component/core.test.js", "tb_map_component/protocol.test.js"]
ok = True
for t in PY:
    r = subprocess.run([sys.executable, t], capture_output=True, text=True)
    print((r.stdout.strip() or r.stderr.strip()).splitlines()[-1] if r.returncode else r.stdout.strip())
    if r.returncode:
        print(r.stdout, r.stderr)
    ok &= r.returncode == 0
node = shutil.which("node")
for t in JS:
    if not node:
        print(f"{t}: SKIPPED (node not installed)")
        continue
    r = subprocess.run([node, t], capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    ok &= r.returncode == 0
print("ALL OK" if ok else "FAILURES")
sys.exit(0 if ok else 1)
