"""Code-protected share links: encryption, codes, passwords and stored copies."""
import sys, copy, base64, tempfile, yaml
import tb_share as sh, tb_network as n, tb_catalog as c, tb_project, tb_cost, tb_schedule
import tb_flowassurance as fa
from _harness import Suite
S = Suite("test_share")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))


def payload(note="for review"):
    lay = n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
    y = tb_project.project_to_yaml("Field A concept", lay, tb_cost.CostSettings(),
                                   tb_schedule.ScheduleSettings(), fa.FASettings())
    return sh.build_payload(y, view=[2.4, 60.4, 2.8, 60.7], note=note)


P = payload()
CODE = sh.generate_code()
TOK = sh.protect(P, CODE)

S.check("the cryptography package is available", sh.crypto_available)

# ── generated codes ─────────────────────────────────────────────────────────
def code_shape():
    codes = {sh.generate_code() for _ in range(200)}
    assert len(codes) == 200, "codes must not repeat"
    for k in codes:
        g = k.split("-")
        assert len(g) == 4 and all(len(x) == 4 for x in g), k
        assert not set(k.replace("-", "")) & set("ILOU"), f"ambiguous letter in {k}"
        assert sh.looks_generated(k)
    return True
S.check("generated codes are 4×4 unambiguous symbols and never repeat", code_shape)
S.check("a generated code carries 80 bits",
        lambda: sh.CODE_GROUPS * sh.CODE_GROUP_LEN * 5 == 80 and len(sh.CODE_ALPHABET) == 32)
S.check("typing a code is forgiving: case, spaces, dashes, O/0 and I/L/1",
        lambda: sh.normalise_code(" k7qm 9xrt-4hpw-2dnc ") == "K7QM9XRT4HPW2DNC"
        and sh.normalise_code("0O1IL") == "00111")

# ── protected round trip ────────────────────────────────────────────────────
S.check("a protected link is recognised as protected",
        lambda: sh.is_protected(TOK) and TOK.startswith("2.") and not sh.is_protected(sh.encode(P)))
S.check("the right code opens the design exactly", lambda: sh.unprotect(TOK, CODE) == P)
S.check("a code typed in lower case with spaces still opens it",
        lambda: sh.unprotect(TOK, CODE.lower().replace("-", " ")) == P)
S.check("open_token handles plain and protected links alike",
        lambda: sh.open_token(sh.encode(P)) == P and sh.open_token(TOK, CODE) == P)
S.check("the restored design is the shared one",
        lambda: "Field A concept" in sh.restore_payload(sh.unprotect(TOK, CODE))["project_yaml"])
S.raises("a wrong code is refused", sh.WrongCode, lambda: sh.unprotect(TOK, "AAAA-AAAA-AAAA-AAAA"))
S.raises("no code at all asks for one", sh.NeedsCode, lambda: sh.unprotect(TOK, ""))
S.raises("a protected link cannot be read as a plain one", ValueError, lambda: sh.decode(TOK))
S.check("wrong-code and needs-code are ValueErrors, so old callers still catch them",
        lambda: issubclass(sh.WrongCode, ValueError) and issubclass(sh.NeedsCode, ValueError))


def nothing_readable_in_the_link():
    """The whole point: without the code, the link reveals nothing of the design."""
    blob = base64.urlsafe_b64decode(TOK[2:] + "=" * (-len(TOK[2:]) % 4))
    for word in (b"Field A", b"for review", b"W1", b"schema", b"tieback"):
        assert word not in blob, word
    import zlib
    try:
        zlib.decompress(blob[3 + 16 + 12:])
    except zlib.error:
        return True
    raise AssertionError("the ciphertext must not be plain compressed data")
S.check("the protected link holds no readable trace of the design", nothing_readable_in_the_link)
S.check("the same design and code give a different link each time (fresh salt and nonce)",
        lambda: sh.protect(P, CODE) != sh.protect(P, CODE))


def tampering_detected():
    blob = bytearray(base64.urlsafe_b64decode(TOK[2:] + "=" * (-len(TOK[2:]) % 4)))
    for pos in (1, 2, 5, 3 + 16 + 3, len(blob) - 1):        # flags, cost, salt, nonce, tag
        b = bytearray(blob); b[pos] ^= 0x01
        t = "2." + base64.urlsafe_b64encode(bytes(b)).decode().rstrip("=")
        try:
            sh.unprotect(t, CODE)
        except ValueError:
            continue
        raise AssertionError(f"a change at byte {pos} went unnoticed")
    return True
S.check("any change to the link — header included — is detected", tampering_detected)


def truncated_protected():
    try:
        sh.unprotect(TOK[: len(TOK) // 2], CODE)
    except ValueError:
        return True
    return False
S.check("a protected link cut short is refused", truncated_protected)
S.raises("a stub of a link is called damaged", ValueError, lambda: sh.unprotect("2.AAAA", CODE))


def weak_kdf_header_refused():
    blob = bytearray(base64.urlsafe_b64decode(TOK[2:] + "=" * (-len(TOK[2:]) % 4)))
    blob[2] = 4                                   # try to make guessing cheap
    t = "2." + base64.urlsafe_b64encode(bytes(blob)).decode().rstrip("=")
    try:
        sh.unprotect(t, CODE)
    except ValueError:
        return True
    return False
S.check("a header lowered to cheap key derivation is refused", weak_kdf_header_refused)

# ── own passwords ───────────────────────────────────────────────────────────
PW = "blue whale trench 42"
S.check("a phrase password protects and opens", lambda: sh.unprotect(sh.protect(P, PW), PW) == P)
S.raises("an own password is case-sensitive", sh.WrongCode,
         lambda: sh.unprotect(sh.protect(P, PW), PW.upper()))
S.raises("a short password is refused", ValueError, lambda: sh.protect(P, "abc123"))
S.raises("a repetitive password is refused", ValueError, lambda: sh.protect(P, "aaaaaaaaaaaaaa"))
S.check("password_problem explains itself",
        lambda: "10 characters" in sh.password_problem("short") and sh.password_problem(PW) == "")

# ── stored copies stay encrypted ────────────────────────────────────────────
def local_store_keeps_ciphertext():
    d = tempfile.mkdtemp()
    store = sh.LocalStore(d)
    sid = store.save_token(TOK)
    import pathlib
    on_disk = (pathlib.Path(d) / f"{sid}.txt").read_text()
    assert on_disk == TOK and "Field A" not in on_disk
    assert sh.is_protected(store.load_token(sid))
    assert sh.open_token(store.load_token(sid), CODE) == P
    return True
S.check("a protected short link stores only ciphertext on disk", local_store_keeps_ciphertext)


class _Resp:
    def __init__(self, d, code=200): self.d, self.status_code = d, code
    def json(self): return self.d


class _GistSess:
    def __init__(self): self.store, self.desc = {}, {}
    def post(self, url, json=None, headers=None, timeout=None):
        gid = "p" * 20
        self.store[gid], self.desc[gid] = json["files"], json["description"]
        return _Resp({"id": gid}, 201)
    def get(self, url, headers=None, timeout=None):
        gid = url.rsplit("/", 1)[-1]
        return _Resp({"files": self.store[gid]}) if gid in self.store else _Resp({}, 404)


def gist_keeps_ciphertext():
    s = _GistSess()
    store = sh.GistStore("tok", s)
    sid = store.save_token(TOK, "TieBack Studio design — Field A concept")
    assert "Field A" not in s.desc[sid], "a protected gist must not name the project in the clear"
    assert sh.open_token(store.load_token(sid), CODE) == P
    plain = store.save(P)
    assert sh.restore_payload(store.load(plain))["note"] == "for review"
    return True
S.check("a protected gist holds only ciphertext and no project name", gist_keeps_ciphertext)
S.check("the protected note tells people to send the code separately",
        lambda: "different channel" in sh.PROTECTED_NOTE)

sys.exit(0 if S.report() else 1)
