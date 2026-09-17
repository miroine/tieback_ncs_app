# PVT Studio `nodal.py` — corrections found during the TieBack Studio port

Reviewed version: `nodal.py` as created in the PVT Studio development chat (May 2026). Each item
is corrected in `tb_multiphase.py` and covered by `test_multiphase.py`. Apply the same changes to
PVT Studio so both tools give the same answer.

| # | Function | Impact | Severity |
|---|---|---|---|
| C1 | `_beggs_brill_dpdL` — friction exponent S | Two-phase friction ×4.6 too high at y = 4; **zero** friction for y < 1 | High |
| C2 | `_beggs_brill_holdup` — flow pattern | Very dry gas (λ < 0.01) always "segregated", even at high velocity | Medium (gas/condensate lines) |
| C3 | `_beggs_brill_holdup` — N_Lv | Inclination correction C uses N_Lv ≈ 20 % high | Medium (inclined lines) |
| C4 | `march_pressure` — Standing Rs and Bo | γg raised to 1.2048; oil SG term wrong in Bo | Low–medium |
| A1 | Module docstring vs code | Payne (1979) holdup correction documented but not applied | Low |

## C1 — two-phase friction multiplier
Published (Beggs & Brill 1973): `S = ln(y) / (−0.0523 + 3.182 ln y − 0.8725 (ln y)² + 0.01853 (ln y)⁴)`,
except for `1 < y < 1.2` where `S = ln(2.2y − 1.2)`; `y = λ_L / H_L²`.

```python
# replace the S block in _beggs_brill_dpdL
if H_L > 0 and 0 < lam < 1:
    y = lam / H_L ** 2
    if 1.0 < y < 1.2:
        s = math.log(2.2 * y - 1.2)
    else:
        x = math.log(y)
        denom = -0.0523 + 3.182 * x - 0.8725 * x ** 2 + 0.01853 * x ** 4
        s = x / denom if abs(denom) > 1e-9 else 0.0
    f_tp = f_n * math.exp(s)
```
The original `max(denom, 1e-6)` turned the (legitimately negative) denominator for y < 1 into
1e-6, so `exp(s)` → 0.

## C2 — flow pattern for λ < 0.01
Delete the early `if lambda_l < 0.01: pattern = "segregated"` branch; the following
`(lambda_l < 0.01 and Fr < L1)` test is the published rule, and everything else falls through to
"distributed".

## C3 — liquid velocity number
```python
Nlv = 1.938 * v_sl * (rho_l / sigma) ** 0.25      # ft/s, lb/ft³, dyn/cm
```

## C4 — Standing correlations
```python
Rs = gas_sg * ((P / 18.2 + 1.4) * 10 ** (0.0125 * api - 0.00091 * T)) ** 1.2048
gamma_o = 141.5 / (api + 131.5)
Bo = 0.9759 + 0.00012 * (Rs * (gas_sg / gamma_o) ** 0.5 + 1.25 * T) ** 1.2
```

## A1 — Payne correction
After the inclination correction: `H_L *= 0.924` for uphill, `0.685` for downhill, then clip to `[λ_L, 1]`.

## Why `validate_nodal.py` did not catch these
Its 16 checks test asymptotic behaviour (J-curve shape, holdup bounds, monotonicity). C1–C3 change
magnitudes, not shapes. `test_multiphase.py` adds equation-level checks (published S branches and
continuity, single-phase Darcy limits, N_Lv, Standing hand calculations, Z against
Dranchuk–Abou-Kassem) — worth copying into PVT Studio's suite.
