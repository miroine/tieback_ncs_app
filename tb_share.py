"""
tb_share.py — Send a colleague exactly what you have built.

A Streamlit app keeps everything in the browser session of the person using it.
Opening the same address gives a colleague an empty app, not your design. Two
ways round that, both producing an ordinary link:

1. **The design in the link** (`?design=…`). The whole project — layout,
   reservoirs, sketches, bookmarks, settings, optionally the saved concepts and
   the map view — is compressed into the address itself. Nothing is stored
   anywhere; the link *is* the design. The catch is length: a large design
   makes a long link, and past `MAX_LINK_CHARS` some mail and chat tools cut it.

2. **A short link to a stored copy** (`?share=<id>`). The same payload is saved
   and the link carries only its id. Where it is saved matters:

   * `LocalStore` — a folder next to the app. It lasts only as long as the
     server's disk: on Streamlit Community Cloud that is until the app restarts
     or redeploys, after which the link stops working.
   * `GistStore` — a *secret* GitHub gist, if a token is configured. Durable.
     "Secret" means unlisted, not private: anyone with the id can read it.

Either way the colleague gets a **copy**. Their edits stay in their session;
yours are untouched, and a later change of yours needs a new link.

What a plain link does not do is decide who may open it: anyone who has it can
open the design. A **protected link** (`protect(payload, code)`, prefix `2.`)
closes that gap. The design is encrypted with AES-256-GCM under a key derived
from a code by scrypt; the link, and any stored copy (disk or gist), then hold
only ciphertext. Opening it needs the code, which is sent separately — by a
different channel than the link. A wrong code is detected (GCM authentication),
never silently "decrypted" into rubbish.

What protection depends on, stated plainly:

* **The code's strength.** Whoever holds the link can try codes offline, at
  their own speed; scrypt only makes each guess slow (~0.1 s). A generated code
  (`generate_code`, 80 random bits) is out of reach of that; a short password
  is not, which is why custom passwords have a minimum length.
* **The two channels staying separate.** Link and code in the same e-mail is a
  plain link with extra steps.
* **The server.** Decryption happens in the app, so whoever runs the app (on
  Streamlit Community Cloud, Streamlit) could in principle see the design while
  a colleague has it open. For data that must not leave the company, host the
  app inside the company network.
"""
from __future__ import annotations

import base64
import secrets
import copy
import datetime as dt
import hashlib
import json
import re
import zlib
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

import yaml

import tb_catalog

SCHEMA = "tieback_share/1"
PREFIX = "1."                      # link-format version, so a future format can be told apart
MAX_LINK_CHARS = 6000              # beyond this some mail / chat clients truncate the address
MAX_DECODED_BYTES = 4_000_000      # refuse anything that inflates past this (zip-bomb guard)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

ENC_PREFIX = "2."                  # an encrypted (code-protected) link
ENC_VERSION = 1
FLAG_GENERATED = 1                 # the code was made by generate_code → forgiving about typing
SCRYPT_LOG2_N = 15                 # 2**15 · r=8 → 32 MiB and ~0.1 s per guess
SCRYPT_R, SCRYPT_P = 8, 1
SALT_BYTES, NONCE_BYTES = 16, 12
MIN_PASSWORD_CHARS = 10
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32: no I, L, O, U
CODE_GROUPS, CODE_GROUP_LEN = 4, 4                    # 16 symbols × 5 bits = 80 bits


class NeedsCode(ValueError):
    """The link is protected; ask for its code."""


class WrongCode(ValueError):
    """The code does not open this link (or the link was altered)."""


# ─────────────────────── catalogue as a diff to the default ────────────────

def catalog_diff(catalog: tb_catalog.Catalog) -> dict:
    """Only what differs from the built-in catalogue — a default catalogue is empty.

    The full catalogue is four times the size of a typical layout, and almost
    always unchanged; sending it would make every link four times longer.
    """
    base = tb_catalog.Catalog().to_dict()
    cur = catalog.to_dict()
    b_items = {i["item_id"]: i for i in base["items"]}
    c_items = {i["item_id"]: i for i in cur["items"]}
    b_sp = {s["key"]: s for s in base["spreads"]}
    c_sp = {s["key"]: s for s in cur["spreads"]}
    norm = lambda d: json.loads(json.dumps(d, default=list))       # noqa: E731 — tuples vs lists
    return {
        "items": [c_items[k] for k in c_items if k not in b_items or norm(c_items[k]) != norm(b_items[k])],
        "removed_items": [k for k in b_items if k not in c_items],
        "spreads": [c_sp[k] for k in c_sp if k not in b_sp or norm(c_sp[k]) != norm(b_sp[k])],
        "removed_spreads": [k for k in b_sp if k not in c_sp],
    }


def catalog_from_diff(diff: Optional[dict]) -> dict:
    """The full catalogue dict a project file expects, rebuilt from a diff."""
    base = tb_catalog.Catalog().to_dict()
    diff = diff or {}
    items = {i["item_id"]: i for i in base["items"]}
    for k in diff.get("removed_items", []):
        items.pop(k, None)
    for i in diff.get("items", []):
        items[i["item_id"]] = i
    spreads = {s["key"]: s for s in base["spreads"]}
    for k in diff.get("removed_spreads", []):
        spreads.pop(k, None)
    for s in diff.get("spreads", []):
        spreads[s["key"]] = s
    return {**base, "items": list(items.values()), "spreads": list(spreads.values())}


# ──────────────────────────────── payload ──────────────────────────────────

def build_payload(project_yaml: str, view=None, cases: Optional[List[dict]] = None,
                  active_case: str = "", note: str = "", author: str = "") -> dict:
    """Everything a colleague needs to see the same thing, catalogue as a diff."""
    doc = yaml.safe_load(project_yaml)
    cat = tb_catalog.Catalog.from_dict(doc.pop("catalog"))
    out = {"schema": SCHEMA, "created_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
           "project": doc, "catalog_diff": catalog_diff(cat)}
    if view and len(view) == 4:
        out["view"] = [round(float(v), 6) for v in view]
    if cases:
        slim = []
        for c in cases:
            c = copy.deepcopy(c)
            proj = c.get("project") or {}
            if "catalog" in proj:
                proj["catalog_diff"] = catalog_diff(tb_catalog.Catalog.from_dict(proj.pop("catalog")))
            slim.append(c)
        out["cases"] = slim
        out["active_case"] = active_case or ""
    if note:
        out["note"] = str(note)[:500]
    if author:
        out["author"] = str(author)[:120]
    return out


def restore_payload(payload: dict) -> dict:
    """{project_yaml, view, cases, active_case, note, author, created_utc} from a payload."""
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError("this link was not made by TieBack Studio, or by a newer version of it")
    proj = copy.deepcopy(payload.get("project") or {})
    proj["catalog"] = catalog_from_diff(payload.get("catalog_diff"))
    cases = []
    for c in payload.get("cases") or []:
        c = copy.deepcopy(c)
        p = c.get("project") or {}
        if "catalog_diff" in p:
            p["catalog"] = catalog_from_diff(p.pop("catalog_diff"))
        cases.append(c)
    return {"project_yaml": yaml.safe_dump(proj, sort_keys=False),
            "view": payload.get("view"), "cases": cases,
            "active_case": payload.get("active_case", ""), "note": payload.get("note", ""),
            "author": payload.get("author", ""), "created_utc": payload.get("created_utc", "")}


# ─────────────────────────── the design in the link ────────────────────────

def encode(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    return PREFIX + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")


def decode(token: str) -> dict:
    t = str(token or "").strip()
    if not t.startswith(PREFIX):
        raise ValueError("unrecognised link format")
    body = t[len(PREFIX):]
    # base64, zlib, UTF-8 and JSON errors are all what a link cut short in an
    # e-mail looks like — binascii and JSON errors are ValueError subclasses, so
    # they are caught together and turned into one message a user can act on.
    try:
        comp = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        d = zlib.decompressobj()
        raw = d.decompress(comp, MAX_DECODED_BYTES)
        too_big = bool(d.unconsumed_tail)
        data = None if too_big else json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("the link is damaged or was cut short — ask for it again, or for the "
                         "project file") from exc
    if too_big:
        raise ValueError("the design in this link is too large to open")
    return data


# ───────────────────────── code-protected (encrypted) links ────────────────

def crypto_available() -> bool:
    """True when the `cryptography` package is installed (it is in requirements.txt)."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def generate_code() -> str:
    """A random 80-bit access code, e.g. ``K7QM-9XRT-4HPW-2DNC`` — easy to read out, hard to guess."""
    sym = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_GROUPS * CODE_GROUP_LEN))
    return "-".join(sym[i:i + CODE_GROUP_LEN] for i in range(0, len(sym), CODE_GROUP_LEN))


def normalise_code(code: str) -> str:
    """How a generated code is compared: case, spaces and dashes do not matter; O→0, I/L→1."""
    c = re.sub(r"[\s\-_.]", "", str(code or "")).upper()
    return c.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


def looks_generated(code: str) -> bool:
    c = normalise_code(code)
    return len(c) == CODE_GROUPS * CODE_GROUP_LEN and all(ch in CODE_ALPHABET for ch in c)


def password_problem(pw: str) -> str:
    """Why a chosen password is too weak to protect a link, or '' if it will do."""
    pw = str(pw or "")
    if len(pw) < MIN_PASSWORD_CHARS:
        return (f"use at least {MIN_PASSWORD_CHARS} characters — anyone holding the link can try "
                f"passwords offline, as fast as their computer allows")
    if len(set(pw)) < 5:
        return "too repetitive — use a phrase of several words, or a generated code"
    if pw.lower() in ("password1234", "passwordpassword", "123456789012", "qwertyuiopas"):
        return "too common"
    return ""


def _kdf(code_bytes: bytes, salt: bytes, log2_n: int) -> bytes:
    if not 10 <= log2_n <= 20:
        raise WrongCode("unsupported key-derivation setting")
    return hashlib.scrypt(code_bytes, salt=salt, n=2 ** log2_n, r=SCRYPT_R, p=SCRYPT_P,
                          maxmem=256 * 1024 * 1024, dklen=32)


def _code_bytes(code: str, generated: bool) -> bytes:
    return (normalise_code(code) if generated else str(code or "")).encode("utf-8")


def is_protected(token: str) -> bool:
    return str(token or "").strip().startswith(ENC_PREFIX)


def protect(payload: dict, code: str, generated: Optional[bool] = None) -> str:
    """The payload encrypted under `code`: ``2.`` + base64url(header | salt | nonce | ciphertext+tag).

    The header (format version, flags, scrypt cost) is authenticated along with
    the ciphertext, so it cannot be altered to weaken the key derivation.
    """
    if not crypto_available():
        raise RuntimeError("protected links need the 'cryptography' package — add it to requirements.txt")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if generated is None:
        generated = looks_generated(code)
    if not generated:
        why = password_problem(code)
        if why:
            raise ValueError(f"password not accepted: {why}")
    header = bytes([ENC_VERSION, FLAG_GENERATED if generated else 0, SCRYPT_LOG2_N])
    salt, nonce = secrets.token_bytes(SALT_BYTES), secrets.token_bytes(NONCE_BYTES)
    key = _kdf(_code_bytes(code, generated), salt, SCRYPT_LOG2_N)
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    ct = AESGCM(key).encrypt(nonce, zlib.compress(raw, 9), header)
    blob = header + salt + nonce + ct
    return ENC_PREFIX + base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")


def unprotect(token: str, code: str) -> dict:
    """Decrypt a protected token. `WrongCode` if the code does not open it."""
    t = str(token or "").strip()
    if not t.startswith(ENC_PREFIX):
        raise ValueError("this link is not protected")
    if not code:
        raise NeedsCode("this design is protected — enter the code you were given")
    if not crypto_available():
        raise RuntimeError("this app cannot open protected links: the 'cryptography' package is missing")
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    body = t[len(ENC_PREFIX):]
    try:
        blob = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("the link is damaged or was cut short — ask for it again") from exc
    hl = 3
    if len(blob) < hl + SALT_BYTES + NONCE_BYTES + 16:
        raise ValueError("the link is damaged or was cut short — ask for it again")
    header = blob[:hl]
    if header[0] != ENC_VERSION:
        raise ValueError("this protected link was made by a newer version of TieBack Studio")
    salt = blob[hl:hl + SALT_BYTES]
    nonce = blob[hl + SALT_BYTES:hl + SALT_BYTES + NONCE_BYTES]
    ct = blob[hl + SALT_BYTES + NONCE_BYTES:]
    key = _kdf(_code_bytes(code, bool(header[1] & FLAG_GENERATED)), salt, header[2])
    try:
        comp = AESGCM(key).decrypt(nonce, ct, header)
    except InvalidTag:
        raise WrongCode("that code does not open this design — check it with the sender "
                        "(a link that was cut short fails the same way)") from None
    d = zlib.decompressobj()
    raw = d.decompress(comp, MAX_DECODED_BYTES)
    if d.unconsumed_tail:
        raise ValueError("the design in this link is too large to open")
    return json.loads(raw.decode("utf-8"))


def open_token(token: str, code: str = "") -> dict:
    """A payload from any link token: plain ones directly, protected ones with their code."""
    return unprotect(token, code) if is_protected(token) else decode(token)


def design_link(base_url: str, token: str) -> str:
    return base_url.split("?")[0].rstrip("/") + "/?design=" + token


def link_ok(link: str) -> bool:
    return len(link) <= MAX_LINK_CHARS


# ─────────────────────────── short links to a stored copy ──────────────────

class LocalStore:
    """Payloads as files beside the app. Lives as long as the server's disk does."""
    durable = False
    label = "this server's disk"

    def __init__(self, folder):
        self.folder = Path(folder)

    def save(self, payload: dict) -> str:
        return self.save_token(encode(payload))

    def save_token(self, token: str) -> str:
        """Store a ready-made token (plain or protected — a protected one is kept encrypted)."""
        self.folder.mkdir(parents=True, exist_ok=True)
        sid = hashlib.sha256(token.encode()).hexdigest()[:16]
        (self.folder / f"{sid}.txt").write_text(token, encoding="ascii")
        return sid

    def load(self, sid: str) -> dict:
        return decode(self.load_token(sid))

    def load_token(self, sid: str) -> str:
        if not ID_RE.match(str(sid or "")):
            raise ValueError("not a share id")
        f = self.folder / f"{sid}.txt"
        if not f.exists():
            raise ValueError("this shared design is no longer on the server — it was probably "
                             "cleared when the app restarted. Ask for a new link or the project file.")
        return f.read_text(encoding="ascii").strip()


class GistStore:
    """Payloads as secret GitHub gists. Durable; readable by anyone with the id."""
    durable = True
    label = "a secret GitHub gist"
    API = "https://api.github.com/gists"
    FILE = "tieback_design.txt"

    def __init__(self, token: str, session):
        if not token:
            raise ValueError("a GitHub token with the 'gist' scope is needed")
        self.session = session
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28"}

    def save(self, payload: dict) -> str:
        return self.save_token(encode(payload),
                               f"TieBack Studio design — {payload.get('project', {}).get('name', '')}")

    def save_token(self, token: str, description: str = "TieBack Studio design") -> str:
        if is_protected(token):
            description = "TieBack Studio design (protected)"   # no project name in the clear
        body = {"description": description, "public": False, "files": {self.FILE: {"content": token}}}
        r = self.session.post(self.API, json=body, headers=self.headers, timeout=30)
        if r.status_code >= 300:
            raise ValueError(f"GitHub refused the upload (HTTP {r.status_code})")
        return r.json()["id"]

    def load(self, sid: str) -> dict:
        return decode(self.load_token(sid))

    def load_token(self, sid: str) -> str:
        if not ID_RE.match(str(sid or "")):
            raise ValueError("not a share id")
        r = self.session.get(f"{self.API}/{sid}", headers=self.headers, timeout=30)
        if r.status_code == 404:
            raise ValueError("this shared design was deleted, or the link is wrong")
        if r.status_code >= 300:
            raise ValueError(f"GitHub refused the download (HTTP {r.status_code})")
        f = (r.json().get("files") or {}).get(self.FILE) or {}
        content = f.get("content")
        if not content and f.get("raw_url"):
            content = self.session.get(f["raw_url"], timeout=30).text
        if not content:
            raise ValueError("the gist has no TieBack design in it")
        return content.strip()


def short_link(base_url: str, sid: str) -> str:
    return base_url.split("?")[0].rstrip("/") + "/?share=" + sid


CONFIDENTIALITY = (
    "Without a code, anyone who has the link — and can reach this app — can open the design. On a public "
    "deployment (Streamlit Community Cloud, a public GitHub gist) that means anyone on the internet. "
    "Do not share real field data this way; use anonymised names, or an instance hosted inside "
    "your company network.")


PROTECTED_NOTE = (
    "The design is encrypted with the code (AES-256-GCM); the link and any stored copy hold only "
    "ciphertext. Send the code by a different channel than the link — e.g. link by e-mail, code by "
    "Teams or phone. The app decrypts on its server while your colleague has it open, so for data "
    "that must stay inside the company, host the app on the company network.")
