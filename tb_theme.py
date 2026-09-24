"""
tb_theme.py — One place for how TieBack Studio looks, and who it belongs to.

The palette follows Equinor's own design language (the colours and the
typographic feel of the Equinor Design System), so the app sits comfortably
beside other Equinor tooling. It is **not** an Equinor product and carries no
Equinor logo or wordmark: see `DISCLAIMER` below, which the app shows on every
page and writes into every report.

Everything a reader sees is built from these tokens:

* `COLORS` — the palette, in one dict, so a chart, a map symbol and a table
  header cannot drift apart.
* `css()` — the stylesheet the app injects once, built from those tokens.
* `chart_colors()` / `plotly_layout()` — the same tokens for figures.
* `AUTHOR`, `DISCLAIMER`, `LICENCE_SHORT` — attribution and terms, used by the
  app, the Word report and the README.

No Streamlit import here: this module is pure data and strings, so the tests
and the report can use it without a browser.
"""
from __future__ import annotations

from typing import Dict, List

# ─────────────────────────────── palette ───────────────────────────────────
# Equinor-derived. Names are the ones used in the Equinor Design System where
# there is one, so a designer reading this recognises them.
COLORS: Dict[str, str] = {
    # brand
    "energy_red": "#EB0037",      # the accent: primary buttons, the active tab, alerts
    "slate_blue": "#243746",      # headings and dark surfaces
    "moss_green": "#007079",      # the working colour: links, selected state, positive
    "moss_light": "#D5EAF4",
    "lichen_green": "#DEEDEE",
    "mist_blue": "#D5EAF4",
    "spruce_wood": "#A8CED1",
    "mist": "#EAF0F4",            # page furniture: sidebar, table stripes
    "karry": "#FFE7D6",           # warning tint
    "navy": "#00243D",            # near-black text on white
    "line": "#C3CDD5",            # hairlines and borders
    "slate": "#3D3D3D",           # body text
    "grey_mid": "#6F6F6F",        # captions
    "surface": "#FFFFFF",
    "surface_alt": "#F7F9FA",
    # status
    "danger": "#EB0037",
    "warning": "#FF9200",
    "success": "#4BB748",
    "info": "#0084C4",
    # chart series, in the order they are handed out
    "series": "#00243D,#EB0037,#007079,#E9A23B,#7D4EBF,#4A6B82,#9DBA00,#C4561B",
}

# Equinor's own typeface is licensed, so it is asked for first and the app falls
# back to whatever the reader has. On an Equinor machine the real thing shows up.
FONT_STACK = ('"Equinor", "Equinor Sans", "Segoe UI", -apple-system, BlinkMacSystemFont, '
              'Roboto, Helvetica, Arial, sans-serif')


def chart_colors() -> List[str]:
    return COLORS["series"].split(",")


def plotly_layout(height: int = 380) -> dict:
    """Layout keywords shared by every figure in the app."""
    return dict(
        height=height, margin=dict(l=12, r=12, t=44, b=12),
        paper_bgcolor=COLORS["surface"], plot_bgcolor=COLORS["surface"],
        font=dict(family=FONT_STACK.replace('"', ""), color=COLORS["navy"], size=12),
        title=dict(font=dict(size=14, color=COLORS["slate_blue"])),
        colorway=chart_colors(),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        hoverlabel=dict(bgcolor=COLORS["surface"], bordercolor=COLORS["line"],
                        font=dict(family=FONT_STACK.replace('"', ""), size=12)),
    )


# ─────────────────────────── attribution and terms ──────────────────────────

AUTHOR = "Merouane Hamdani"
AUTHOR_ROLE = "Concept Development Manager"
COPYRIGHT = f"© 2026 {AUTHOR}. All rights reserved."

DISCLAIMER = (
    f"**TieBack Studio was designed and built by {AUTHOR}.** It is a **prototype for early-phase "
    "concept planning and screening only** — a way to compare tie-back concepts quickly, not a design "
    "or engineering tool. **It must not be used on commercial projects**, nor as a basis for "
    "investment, procurement, operational or safety decisions. Every result comes from simplified "
    "correlations and indicative placeholder rates, and must be confirmed with proper engineering "
    "tools and the operator's own data before it means anything. The author accepts no liability for "
    "any use of this app or its output.\n\n"
    f"**{COPYRIGHT}** The source code is licensed: it may not be copied, reused or published, in whole "
    f"or in part, without clearly crediting {AUTHOR} as its author and keeping the licence notice with "
    "it. Commercial use needs the author's written permission. See the LICENSE file.\n\n"
    "Independent personal project. Not an Equinor product: not affiliated with, endorsed by, or "
    "representing Equinor, Sodir, EMODnet or any other organisation whose public data it reads."
)

DISCLAIMER_SHORT = (
    f"Prototype by {AUTHOR} for early-phase concept screening only — not for commercial projects, "
    f"and not a substitute for engineering design. {COPYRIGHT} Re-use requires attribution."
)

LICENCE_SHORT = f"{COPYRIGHT} Attribution required · non-commercial · see LICENSE"


def footer_html(version: str) -> str:
    return (f'<div class="tb-foot"><strong>TieBack Studio v{version}</strong> — built by {AUTHOR}. '
            f'Prototype for early-phase concept screening only: <strong>not for commercial projects</strong> '
            f'and not a substitute for engineering design. Cost rates are indicative placeholders. '
            f'{COPYRIGHT} Re-use of the source code requires attribution to the author; commercial use '
            f'requires written permission. Not affiliated with or endorsed by Equinor or Sodir.</div>')


# ──────────────────────────────── stylesheet ───────────────────────────────

def css() -> str:
    """The whole app's stylesheet, built from the tokens above."""
    c = COLORS
    return f"""
<style>
  :root {{
    --tb-red: {c['energy_red']}; --tb-navy: {c['navy']}; --tb-slate: {c['slate_blue']};
    --tb-moss: {c['moss_green']}; --tb-mist: {c['mist']}; --tb-line: {c['line']};
    --tb-surface-alt: {c['surface_alt']};
  }}
  html, body, [class*="css"], [data-testid="stAppViewContainer"] {{ font-family: {FONT_STACK}; }}
  [data-testid="stAppViewContainer"] {{ background: {c['surface']}; color: {c['slate']}; }}
  .block-container {{ padding-top: 2.4rem; padding-bottom: 1rem; max-width: 1500px; }}

  /* ── headings ── */
  h1, h2, h3, h4 {{ font-family: {FONT_STACK}; color: {c['slate_blue']}; letter-spacing: -0.01em; }}
  h3 {{ font-size: 1.12rem; margin-top: 0.6rem; }}
  h4 {{ font-size: 1.0rem; color: {c['navy']}; margin: 1.1rem 0 0.2rem; }}
  hr {{ border-color: {c['line']}; }}

  /* ── the title band ── */
  .tb-title {{ display:flex; align-items:center; gap:16px; flex-wrap:wrap;
               border-left: 6px solid {c['energy_red']}; background: linear-gradient(90deg,
               {c['surface_alt']} 0%, {c['surface']} 70%);
               padding: 12px 18px; margin: 0 0 14px; border-radius: 3px; }}
  .tb-title h1 {{ font-size: 1.72rem; margin: 0; color: {c['slate_blue']}; font-weight: 600; }}
  .tb-title .tb-sub {{ color: {c['grey_mid']}; font-size: 0.93rem; }}
  .tb-title .tb-proj {{ color: {c['moss_green']}; font-weight: 600; font-size: 0.95rem; }}
  .tb-chip {{ display:inline-block; background: {c['lichen_green']}; color: {c['moss_green']};
              border-radius: 10px; padding: 1px 10px; font-size: 0.72rem; font-weight: 600;
              letter-spacing: 0.02em; }}
  .tb-by {{ margin-left:auto; text-align:right; color: {c['grey_mid']}; font-size: 0.76rem; line-height: 1.35; }}
  .tb-by b {{ color: {c['slate_blue']}; }}

  /* ── metrics as cards ── */
  [data-testid="stMetric"] {{ background: {c['surface_alt']}; border: 1px solid {c['line']};
      border-left: 4px solid {c['moss_green']}; border-radius: 4px; padding: 10px 14px; }}
  [data-testid="stMetricValue"] {{ color: {c['slate_blue']}; font-size: 1.5rem; font-weight: 600; }}
  [data-testid="stMetricLabel"] p {{ color: {c['grey_mid']}; font-size: 0.8rem;
      text-transform: uppercase; letter-spacing: 0.04em; }}

  /* ── tabs ── */
  [data-testid="stTabs"] [role="tablist"] {{ gap: 2px; border-bottom: 1px solid {c['line']}; }}
  [data-testid="stTabs"] [role="tab"] {{ padding: 8px 14px; color: {c['grey_mid']};
      font-weight: 500; border-radius: 3px 3px 0 0; }}
  [data-testid="stTabs"] [role="tab"]:hover {{ background: {c['surface_alt']}; color: {c['slate_blue']}; }}
  [data-testid="stTabs"] [aria-selected="true"] {{ color: {c['slate_blue']}; font-weight: 600;
      border-bottom: 3px solid {c['energy_red']}; }}

  /* ── sidebar ── */
  [data-testid="stSidebar"] {{ background: {c['mist']}; border-right: 1px solid {c['line']}; }}
  [data-testid="stSidebar"] .block-container {{ padding-top: 1.2rem; }}
  [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {{ font-size: 0.95rem;
      text-transform: uppercase; letter-spacing: 0.05em; color: {c['slate_blue']}; }}

  /* ── buttons ── */
  .stButton > button, .stDownloadButton > button, .stLinkButton > a {{
      border-radius: 3px; font-weight: 600; border: 1px solid {c['line']};
      transition: background 0.12s ease, border-color 0.12s ease; }}
  .stButton > button:hover, .stDownloadButton > button:hover {{ border-color: {c['moss_green']};
      color: {c['moss_green']}; }}
  .stButton > button[kind="primary"] {{ background: {c['energy_red']}; border-color: {c['energy_red']}; }}
  .stButton > button[kind="primary"]:hover {{ background: #C90030; border-color: #C90030; color: #fff; }}

  /* ── panels ── */
  [data-testid="stExpander"] {{ border: 1px solid {c['line']}; border-radius: 4px;
      background: {c['surface']}; }}
  [data-testid="stExpander"] summary {{ font-weight: 600; color: {c['slate_blue']}; }}
  [data-testid="stExpander"] summary:hover {{ color: {c['moss_green']}; }}
  [data-testid="stForm"] {{ border: 1px solid {c['line']}; border-radius: 4px; padding: 12px 14px;
      background: {c['surface_alt']}; }}

  /* ── tables ── */
  [data-testid="stDataFrame"] {{ border: 1px solid {c['line']}; border-radius: 4px; }}
  [data-testid="stDataFrame"] thead tr th {{ background: {c['surface_alt']} !important;
      color: {c['slate_blue']} !important; font-weight: 600; }}

  /* ── notices ── */
  .tb-flag {{ border-left: 4px solid {c['energy_red']}; background: {c['karry']}; padding: 9px 13px;
      color: {c['navy']}; font-size: 0.89rem; border-radius: 0 3px 3px 0; }}
  .tb-note {{ border-left: 4px solid {c['moss_green']}; background: {c['lichen_green']};
      padding: 9px 13px; color: {c['navy']}; font-size: 0.89rem; border-radius: 0 3px 3px 0; }}
  .tb-foot {{ color: {c['grey_mid']}; font-size: 0.76rem; line-height: 1.5;
      border-top: 2px solid {c['line']}; margin-top: 26px; padding-top: 10px; }}
  .tb-foot strong {{ color: {c['slate_blue']}; }}
</style>
"""


def streamlit_config() -> str:
    """`.streamlit/config.toml` — the widget colours Streamlit itself draws."""
    c = COLORS
    return (f"[theme]\n"
            f'primaryColor = "{c["energy_red"]}"\n'
            f'backgroundColor = "{c["surface"]}"\n'
            f'secondaryBackgroundColor = "{c["mist"]}"\n'
            f'textColor = "{c["slate"]}"\n'
            f'font = "sans serif"\n\n'
            f"[server]\n"
            f"maxUploadSize = 200\n")
