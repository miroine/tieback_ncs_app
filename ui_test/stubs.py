"""Minimal Streamlit + Plotly stand-ins for headless execution of tieback_app.py."""
import sys, types, datetime as dt

class Rerun(BaseException): pass  # matches Streamlit ScriptControlException(BaseException)

class State(dict):
    def __getattr__(self, k):
        try: return self[k]
        except KeyError: raise AttributeError(k)
    def __setattr__(self, k, v): self[k] = v

class Ctx:
    def __init__(self, st): self._st = st
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __getattr__(self, name): return getattr(self._st, name)

class Harness:
    def __init__(self):
        self.session_state = State()
        self.press, self.uploads, self.event = set(), {}, None
        self.log = []   # (kind, message)
        self.pressed_log = []
        self.__version__ = "1.50.0"
        self.sidebar = Ctx(self)
        cc = types.SimpleNamespace()
        for n in ("TextColumn", "SelectboxColumn", "NumberColumn", "CheckboxColumn"):
            setattr(cc, n, lambda *a, **k: dict(a=a, k=k))
        self.column_config = cc
    # layout
    def set_page_config(self, **k): pass
    def columns(self, spec, **k): return [Ctx(self) for _ in range(spec if isinstance(spec, int) else len(spec))]
    def tabs(self, names): return [Ctx(self) for _ in names]
    def expander(self, *a, **k): return Ctx(self)
    def form(self, *a, **k): return Ctx(self)
    def spinner(self, *a, **k): return Ctx(self)
    # output
    def _out(self, kind):
        def f(*a, **k): self.log.append((kind, a[0] if a else "")); return None
        return f
    def __getattr__(self, name):
        if name in ("markdown", "caption", "subheader", "write", "metric", "dataframe", "plotly_chart", "code",
                    "download_button", "success", "info", "warning", "error", "toast"):
            return self._out(name)
        raise AttributeError(name)
    # widgets
    def _btn(self, label, *a, **k):
        if label in self.press:
            self.press.discard(label); self.pressed_log.append(label); return True
        return False
    button = form_submit_button = _btn
    def text_input(self, label, value="", **k): return value
    def number_input(self, label, min_value=None, max_value=None, value=None, step=None, **k):
        v = min_value if value is None else value
        nums = [x for x in (min_value, max_value, v, step) if x is not None]
        if any(isinstance(x, float) for x in nums) and any(isinstance(x, int) and not isinstance(x, bool) for x in nums):
            raise TypeError(f"number_input '{label}': mixed int/float arguments {nums} (StreamlitMixedNumericTypesError)")
        if min_value is not None and v < min_value: raise ValueError(f"{label}: value {v} < min {min_value}")
        if max_value is not None and v > max_value: raise ValueError(f"{label}: value {v} > max {max_value}")
        return v
    def selectbox(self, label, options, index=0, format_func=str, **k):
        options = list(options)
        for o in options: format_func(o)
        return options[index] if options else None
    def radio(self, label, options, index=0, **k): return list(options)[index]
    def multiselect(self, label, options, default=None, format_func=str, **k):
        for o in options: format_func(o)
        return list(default or [])
    def slider(self, label, min_value, max_value, value, step=None, **k): return value
    def checkbox(self, label, value=False, **k): return value
    def date_input(self, label, value=None, **k): return value
    def data_editor(self, df, **k): return df.copy()
    def file_uploader(self, label, type=None, key=None, **k): return self.uploads.pop(key, None)
    def rerun(self): raise Rerun()
    def cache_data(self, *a, **k):
        if a and callable(a[0]): return a[0]
        return lambda f: f

class UploadedFile:
    def __init__(self, name, data): self.name, self._d = name, data
    def getvalue(self): return self._d

# ── plotly stubs that verify referenced columns exist ──
class Fig:
    def __init__(self, *a, **k): self.traces = list(a)
    def __getattr__(self, n): return lambda *a, **k: self
def _px(kind):
    def f(df, **k):
        for key in ("x", "y", "color", "x_start", "x_end"):
            if k.get(key) is not None and k[key] not in df.columns:
                raise KeyError(f"px.{kind}: column '{k[key]}' missing")
        for c in k.get("hover_data") or []:
            if c not in df.columns: raise KeyError(f"px.{kind}: hover column '{c}' missing")
        return Fig()
    return f

def install(h):
    st = types.ModuleType("streamlit"); st.__dict__["_h"] = h
    st.__getattr__ = lambda name: getattr(h, name)
    comps = types.ModuleType("streamlit.components"); v1 = types.ModuleType("streamlit.components.v1")
    def declare_component(name, path=None):
        import os
        assert os.path.exists(os.path.join(path, "index.html")), "component index.html missing"
        def fn(**kw):
            import json; json.dumps({k: v for k, v in kw.items() if k != "key"}, default=str)  # must be serialisable
            h.last_component_args = kw
            return h.event
        return fn
    v1.declare_component = declare_component
    comps.v1 = v1; st.components = comps
    sys.modules.update({"streamlit": st, "streamlit.components": comps, "streamlit.components.v1": v1})
    px = types.ModuleType("plotly.express")
    for k in ("bar", "timeline", "line", "scatter"): setattr(px, k, _px(k))
    go = types.ModuleType("plotly.graph_objects")
    go.Figure, go.Histogram, go.Scatter, go.Bar = Fig, (lambda **k: k), (lambda **k: k), (lambda **k: k)
    plotly = types.ModuleType("plotly"); plotly.express, plotly.graph_objects = px, go
    sys.modules.update({"plotly": plotly, "plotly.express": px, "plotly.graph_objects": go})
