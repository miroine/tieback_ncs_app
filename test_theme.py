"""Look and attribution: the palette, the stylesheet, and the author/licence notices."""
import sys, re, pathlib
import tb_theme as th
from _harness import Suite
S = Suite("test_theme")
ROOT = pathlib.Path(".")

# ── palette ──
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
S.check("every colour token is a hex colour",
        lambda: all(HEX.match(v) for k, v in th.COLORS.items() if k != "series"))
S.check("the chart series are distinct colours",
        lambda: len(th.chart_colors()) >= 6 and len(set(th.chart_colors())) == len(th.chart_colors())
        and all(HEX.match(c) for c in th.chart_colors()))
S.check("Equinor's red and green are the accent and the working colour",
        lambda: th.COLORS["energy_red"] == "#EB0037" and th.COLORS["moss_green"] == "#007079")
def layout_keys():
    lay = th.plotly_layout(300)
    assert lay["height"] == 300 and lay["colorway"] == th.chart_colors()
    assert lay["paper_bgcolor"] == th.COLORS["surface"] and "Equinor" in lay["font"]["family"]
    return True
S.check("the chart frame carries the palette and the font", layout_keys)
def stylesheet():
    css = th.css()
    assert css.count("<style>") == 1 and css.count("</style>") == 1
    assert css.count("{") == css.count("}"), "unbalanced braces would break the page"
    for token in ("tb-title", "tb-foot", "tb-flag", "stMetric", "stTabs", "stSidebar"):
        assert token in css, token
    assert th.COLORS["energy_red"] in css and th.FONT_STACK.split(",")[0] in css
    assert "{{" not in css and "}}" not in css, "f-string escapes must not reach the browser"
    return True
S.check("the stylesheet is one balanced block built from the tokens", stylesheet)
def config_file():
    cfg = th.streamlit_config()
    assert "[theme]" in cfg and th.COLORS["energy_red"] in cfg
    on_disk = (ROOT / ".streamlit" / "config.toml").read_text()
    return on_disk.strip() == cfg.strip()      # what ships matches what the module says
S.check("the shipped .streamlit/config.toml matches the theme", config_file)

# ── attribution ──
S.check("the author is named", lambda: th.AUTHOR == "Merouane Hamdani" and th.AUTHOR in th.COPYRIGHT)
def disclaimer_says_the_three_things():
    d = th.DISCLAIMER.lower()
    assert "merouane hamdani" in d
    assert "prototype" in d and "early-phase" in d
    assert "must not be used on commercial projects" in d
    assert "licen" in d and "credit" in d
    assert "not affiliated" in d and "equinor" in d
    short = th.DISCLAIMER_SHORT.lower()
    assert "merouane hamdani" in short and "not for commercial projects" in short and "attribution" in short
    return True
S.check("the disclaimer names the author, the prototype status, the ban on commercial use and the licence",
        disclaimer_says_the_three_things)
def footer():
    f = th.footer_html("9.9.9")
    return ("v9.9.9" in f and th.AUTHOR in f and "not for commercial projects" in f
            and "attribution" in f.lower() and f.startswith("<div") and f.endswith("</div>"))
S.check("the page footer carries the version, the author and the terms", footer)

# ── the files that travel with the code ──
def licence_file():
    txt = (ROOT / "LICENSE").read_text()
    for must in ("Merouane Hamdani", "attribution", "TieBack Studio — created by Merouane Hamdani",
                 "Commercial use", "AS IS", "PROTOTYPE"):
        assert must in txt, must
    assert "MIT License" not in txt, "the MIT licence would allow silent reuse"
    return True
S.check("LICENSE requires attribution and blocks commercial use without permission", licence_file)
S.check("NOTICE repeats the credit line",
        lambda: "TieBack Studio — created by Merouane Hamdani" in (ROOT / "NOTICE").read_text())
def readme():
    txt = (ROOT / "README.md").read_text()
    return ("Created by Merouane Hamdani" in txt and "do not use it on commercial projects" in txt
            and "LICENSE" in txt)
S.check("the README leads with the author and the prototype warning", readme)
def app_shows_it():
    src = (ROOT / "tieback_app.py").read_text()
    assert "tb_theme.css()" in src and "tb_theme.footer_html(APP_VERSION)" in src
    assert "tb_theme.DISCLAIMER" in src and "About this app" in src
    assert "tb_theme.AUTHOR" in src, "the title band must name the author"
    return True
S.check("the app injects the theme and shows the disclaimer and the author", app_shows_it)
def report_carries_it():
    import tb_report
    return tb_report.AUTHOR == th.AUTHOR and "commercial" in tb_report.AUTHOR_NOTE.lower()
S.check("the Word report carries the author and the prototype note", report_carries_it)

sys.exit(0 if S.report() else 1)
