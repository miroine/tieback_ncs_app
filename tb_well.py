"""
tb_well.py — Well inflow/outflow (nodal) for TieBack Studio.

* IPR: straight-line PI, Vogel below the bubble point, or a gas back-pressure
  equation q = C (P_r² − P_wf²)^n.
* VLP: tubing pressure drop from the perforations to the wellhead using the
  same Beggs-Brill gradient as the flowlines (vertical, θ = 90°).
* Operating point: the rate where the pressure the well can deliver at the
  wellhead equals the pressure the network demands there.

Field units (psia, °F, ft, in, STB/d, Mscf/d) as in tb_multiphase.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

import tb_multiphase as mp
import tb_thermal as th

IPR_KINDS = ("pi", "vogel", "gas_backpressure")


@dataclass
class IPR:
    kind: str = "pi"
    reservoir_pressure_psia: float = 5000.0
    productivity_index: float = 5.0        # STB/d/psi (pi, vogel)
    bubble_point_psia: float = 3000.0      # vogel
    gas_c: float = 0.002                   # Mscf/d / psi^(2n)
    gas_n: float = 0.85
    max_rate_stb_d: float = 60000.0

    def __post_init__(self):
        if self.kind not in IPR_KINDS:
            raise ValueError(f"unknown IPR kind '{self.kind}'")
        if self.reservoir_pressure_psia <= 0:
            raise ValueError("reservoir pressure must be > 0")
        if self.kind in ("pi", "vogel") and self.productivity_index <= 0:
            raise ValueError("productivity index must be > 0")

    def rate(self, p_wf_psia: float) -> float:
        """Inflow at a flowing bottom-hole pressure (STB/d, or Mscf/d for gas)."""
        pr = self.reservoir_pressure_psia
        p = min(max(p_wf_psia, 0.0), pr)
        if self.kind == "pi":
            return self.productivity_index * (pr - p)
        if self.kind == "vogel":
            pb = min(self.bubble_point_psia, pr)
            q_b = self.productivity_index * (pr - pb)              # above the bubble point
            if p >= pb:
                return self.productivity_index * (pr - p)
            q_max_below = self.productivity_index * pb / 1.8
            x = p / pb if pb > 0 else 0.0
            return q_b + q_max_below * (1 - 0.2 * x - 0.8 * x ** 2)
        return self.gas_c * max(pr ** 2 - p ** 2, 0.0) ** self.gas_n

    def bhp(self, rate: float) -> float:
        """Flowing bottom-hole pressure for a rate (inverse of `rate`)."""
        lo, hi = 0.0, self.reservoir_pressure_psia
        if rate <= 0:
            return hi
        if rate >= self.rate(0.0):
            return 0.0
        for _ in range(80):
            mid = (lo + hi) / 2
            if self.rate(mid) > rate:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2


@dataclass
class Tubing:
    depth_ft: float = 8000.0           # measured depth, perforations to wellhead
    tvd_ft: float = 0.0                # true vertical depth (0 → same as measured)
    id_in: float = 4.892               # 5½" tubing
    roughness_in: float = 0.0006
    geothermal_f: float = 180.0        # reservoir temperature
    wellhead_temp_f: float = 0.0       # 0 → computed from the tubing profile

    def inclination_deg(self) -> float:
        tvd = self.tvd_ft or self.depth_ft
        return math.degrees(math.asin(min(max(tvd / max(self.depth_ft, 1e-9), 0.0), 1.0)))


def wellhead_pressure(fluid: mp.Fluid, q_oil_stb_d: float, q_water_stb_d: float, ipr: IPR,
                      tubing: Tubing, segments: int = 20) -> dict:
    """March the tubing from the perforations to the wellhead (VLP)."""
    p = ipr.bhp(q_oil_stb_d + q_water_stb_d if ipr.kind != "gas_backpressure"
                else q_oil_stb_d * fluid.gor_scf_stb / 1000.0)
    theta = tubing.inclination_deg()
    dl = tubing.depth_ft / max(segments, 1)
    t_top = tubing.wellhead_temp_f or max(tubing.geothermal_f - 0.012 * (tubing.tvd_ft or tubing.depth_ft), 40.0)
    profile = []
    for i in range(segments):
        f = (i + 0.5) / segments
        t = tubing.geothermal_f + (t_top - tubing.geothermal_f) * f       # linear geothermal profile
        g = mp.segment_gradient(fluid, q_oil_stb_d, q_water_stb_d, p, t, tubing.id_in, theta,
                                tubing.roughness_in / tubing.id_in)
        p = max(p - g["dpdl"] * dl, 1.0)                                  # flowing upward: pressure falls
        profile.append(dict(md_ft=(i + 1) * dl, p_psia=p, t_f=t, holdup=g["holdup"], pattern=g["pattern"]))
    return dict(whp_psia=p, t_wh_f=t_top, profile=profile)


def operating_point(fluid: mp.Fluid, ipr: IPR, tubing: Tubing, required_whp: Callable[[float], float],
                    water_cut: float = 0.0, q_max: Optional[float] = None, tol: float = 1.0,
                    iterations: int = 40) -> dict:
    """Rate where deliverable wellhead pressure meets the pressure the network needs.

    `required_whp(q_oil_stb_d)` returns the wellhead pressure the system demands
    at that rate (rising with rate); the VLP/IPR side falls with rate.
    """
    def excess(q):
        q_w = q * water_cut / (1 - water_cut) if water_cut < 1 else 0.0
        avail = wellhead_pressure(fluid, q, q_w, ipr, tubing)["whp_psia"]
        return avail - required_whp(q), avail

    hi = q_max or min(ipr.rate(0.0) * (1 - water_cut) if ipr.kind != "gas_backpressure" else 50000.0,
                      ipr.max_rate_stb_d)
    lo = max(hi * 1e-4, 1.0)
    e_lo, _ = excess(lo)
    e_hi, _ = excess(hi)
    if e_lo < 0:
        return dict(rate_stb_d=0.0, whp_psia=float("nan"), converged=True,
                    note="Well cannot flow to the host at any rate with this IPR and tubing.")
    if e_hi > 0:
        a, avail = excess(hi)
        return dict(rate_stb_d=hi, whp_psia=avail, converged=True,
                    note="Deliverability above the search limit — rate is constrained by something else.")
    for _ in range(iterations):
        mid = (lo + hi) / 2
        e, avail = excess(mid)
        if abs(e) < tol:
            return dict(rate_stb_d=mid, whp_psia=avail, converged=True, note="")
        if e > 0:
            lo = mid
        else:
            hi = mid
    q = (lo + hi) / 2
    _, avail = excess(q)
    return dict(rate_stb_d=q, whp_psia=avail, converged=False, note="Did not converge to the tolerance.")
