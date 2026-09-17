"""Minimal test harness matching the FieldVista suite style (pass/fail counts)."""
import traceback
class Suite:
    def __init__(self, name): self.name, self.passed, self.failed = name, 0, []
    def check(self, label, fn):
        try:
            r = fn()
            if r is False: raise AssertionError("returned False")
            self.passed += 1
        except Exception as e:
            self.failed.append((label, f"{type(e).__name__}: {e}"))
    def raises(self, label, exc, fn):
        def _t():
            try: fn()
            except exc: return True
            raise AssertionError(f"expected {exc.__name__}")
        self.check(label, _t)
    def report(self):
        print(f"{self.name}: {self.passed} passed, {len(self.failed)} failed")
        for l, m in self.failed: print(f"  FAIL {l}: {m}")
        return not self.failed
