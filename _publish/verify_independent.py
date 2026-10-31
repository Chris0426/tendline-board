#!/usr/bin/env python3
"""Independent leak verifier for the sanitised public tree (second gate).

Written WITHOUT reading the main build script or its selftest.  Rules are
derived only from the verifier spec (see README-verify.md).

Usage
  python verify_independent.py --names N.yaml --maps M.yaml --tree DIR \
         [--policy P.yaml] [--names-exemptions [--repo R]] \
         [--exempt E.yaml] [--synthetic S.yaml] --report OUT.md
  python verify_independent.py --selftest [--names N.yaml] [--maps M.yaml]

Exit codes: 0 no hit, 4 hits, 1 usage/file error, 5 selftest failure.

The script is strictly read-only on --tree (files are opened "rb"; the only
subprocess is read-only git plumbing on a .git directory found in the tree).
The report contains real names and must never be published.  This source
file must never contain a real name (checked by --selftest when --names is
given).
"""
from __future__ import annotations

import argparse
import bisect
import fnmatch
import html
import io
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse
import zipfile
import zlib
from collections import defaultdict
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Generic patterns (no real names here, by construction).
# Each: name, regex, optional allow regex (applied to the matched text).
# ---------------------------------------------------------------------------
GENERIC_PATTERNS = [
    {
        "name": "email-non-example",
        "pattern": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}",
        # example domains (RFC 2606) plus generic no-reply addresses.
        # Public addresses listed in the name table are added at runtime.
        "allow": r"(?i)(?:@(?:[a-z0-9-]+\.)*example\.(?:com|org|net)$"
                 r"|@(?:[a-z0-9-]+\.)*[a-z0-9-]+\.example$"
                 r"|@users\.noreply\.github\.com$|^noreply@anthropic\.com$)",
    },
    {
        "name": "windows-home",
        "pattern": r"(?i)(?<![A-Za-z0-9])[A-Z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/)[A-Za-z0-9._~$-]+",
        "allow": r"(?i)Users(?:\\{1,2}|/)(?:Public|Default|you|user|username|example|<[^>]*>)$",
    },
    {
        "name": "posix-home-of-windows-drive",
        "pattern": r"(?i)(?<![A-Za-z0-9])/(?:mnt/)?[a-z]/Users/[A-Za-z0-9._~-]+",
        "allow": r"(?i)/Users/(?:Public|Default|you|user|username|example)$",
    },
    {
        "name": "unc-path",
        "pattern": r"(?<![\\A-Za-z0-9.%}])(?:\\\\){1,2}[A-Za-z][A-Za-z0-9._-]{2,}\\{1,2}[A-Za-z0-9$._-]{2,}",
    },
    {
        "name": "private-ipv4",
        "pattern": r"(?<![\d.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}"
                   r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\d.])",
    },
    {
        "name": "internal-host-suffix",
        "pattern": r"(?i)(?<![A-Za-z0-9-])[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*"
                   r"\.(?:local|intra|intranet|internal|corp|lan)(?![A-Za-z0-9_-])",
        # a trailing underscore means a Python attribute (clock.local_utc_offset), never a host
        # name (host labels have no underscore); same rule as the build-side table pattern
        "allow": r"(?i)^localhost\.local$",
    },
    {
        "name": "domain-account",
        "pattern": r"(?<![A-Za-z0-9])[A-Z][A-Z0-9-]{1,14}\\{1,2}[a-z][a-z0-9-]*\.[a-z][a-z0-9-]+",
    },
    {
        # Taiwan landline / mobile.  Round 2: space-separated and fully
        # unseparated forms added (0X + 8 digits, 0XX + 7 digits = 10 digits).
        "name": "tw-phone",
        "pattern": r"(?<![\d+-])(?:\+886[-\s]?\(?0?\d{1,2}\)?[-\s]?\d{3,4}[-\s]?\d{3,4}"
                   r"|\(0\d{1,2}\)\s?\d{3,4}-?\d{4}"
                   r"|0\d{1,2}[-\s]\d{3,4}[-\s]?\d{4}"
                   r"|09\d{2}[-\s]?\d{3}[-\s]?\d{3}"
                   # unseparated form must be a standalone token (not inside a hex hash / id)
                   r"|(?<![A-Za-z0-9])0[2-9]\d{8}(?![A-Za-z]))(?![\d-])",
    },
]

# ---------------------------------------------------------------------------
# Round 3 configuration (no real names here either: every sensitive word is
# read from the name table at run time; the lists below are generic English
# words, well-known API/header fragments and table *category names*).
# ---------------------------------------------------------------------------
# Item 1/6: categories whose short real values (3..SHORT_MAX chars, alphanumeric)
# are also searched INSIDE identifiers (embedded, e.g. "<code>weekly").
EMBED_CATEGORIES = ("cm", "stations", "org", "vendors")
EMBED_MIN = 3
# Reviewable English-word exclusion list for the embedded class.  An embedded
# occurrence is dropped only when the short word lies inside one of these exact
# words within the identifier (literal words, no wildcard rule).  Keep sorted.
EMBED_EXCLUDE_WORDS = sorted({
    "advice", "alice", "choice", "choices", "device", "devices", "incubator", "indices", "invoice",
    "licence", "license", "licensed", "notice", "noticed", "office", "plasma", "practice", "price",
    "priced", "prices", "service", "services", "slice", "sliced", "slices", "splice", "suffice",
    "suffices", "twice", "unpriced", "vertices", "nice", "vice",
})
# Item 6: host-name categories; the first fragment of a host name that looks like a
# site code (letters + 1-2 digits) becomes a short sensitive word of its own.
SITE_CODE_CATEGORIES = ("hosts",)
SITE_CODE_RX = re.compile(r"[A-Za-z]{2,4}\d{1,2}")
# Item 4: every short real (<= SHORT_MAX) is also checked as a WHOLE identifier in
# binding positions (import alias, assignment, def, for, lambda, let/const/var, $x =).
IDENT_BIND_RX = re.compile(
    r"\bimport\b[^\n#]*?\bas\s+(?P<a>[A-Za-z_]\w*)"
    r"|\b(?:with|except)\b[^\n#]*?\bas\s+(?P<c>[A-Za-z_]\w*)"
    r"|^[ \t]*(?P<d>[A-Za-z_]\w*)[ \t]*(?::[^=\n]{1,40})?=(?!=)"
    r"|\bdef\s+(?P<e>[A-Za-z_]\w*)\s*\("
    r"|\bfor\s+(?P<f>[A-Za-z_]\w*)\s+in\b"
    r"|\blambda\s+(?P<g>[A-Za-z_]\w*)"
    r"|\b(?:let|const|var)\s+(?P<h>[A-Za-z_]\w*)"
    r"|\$(?P<i>[A-Za-z_]\w*)\s*=(?!=)", re.M)
IDENT_GROUPS = ("a", "c", "d", "e", "f", "g", "h", "i")
# Item 3: numbered work-directory folders ("NN-Name") in a path context.
WORKDIR_SEG = r"\d\d-[A-Z][A-Za-z_]+"
WORKDIR_SEP = r"(?:\\\\|\\|/)"
WORKDIR_RX = re.compile(r"(?<![A-Za-z0-9_.\-])" + WORKDIR_SEG + r"(?:" + WORKDIR_SEP + WORKDIR_SEG + r")*")
# Item 5: MES vendor-schema column prefixes = three lower-case letters + upper-case.
MES_PREFIX_RX = re.compile(r"(?<![A-Za-z0-9_$])([a-z]{3})([A-Z][A-Za-z0-9_]*)")
MES_PREFIX_CATEGORIES = ("mes_schema",)     # real prefixes read from these table categories
MES_PK_SUFFIXES = ("PK",)                   # "<pfx>PK" anywhere in the tree => schema-shaped prefix
# Item 9: binary files that are never text-matched (listed only).
BINARY_SKIP_EXT = frozenset({".woff", ".woff2", ".ttf", ".otf", ".eot", ".png", ".jpg", ".jpeg", ".gif",
                             ".bmp", ".ico", ".webp", ".tif", ".tiff", ".mp4", ".mov", ".webm", ".mp3",
                             ".wav", ".ogg"})
BINARY_MAGIC = ((b"wOFF", "woff"), (b"wOF2", "woff2"), (b"OTTO", "otf"), (b"\x89PNG\r\n\x1a\n", "png"),
                (b"GIF87a", "gif"), (b"GIF89a", "gif"), (b"\xff\xd8\xff", "jpeg"), (b"\x00\x01\x00\x00\x00", "ttf"),
                (b"RIFF", "riff (webp/wav)"))
# Item 12: structural context rules for known false-positive shapes (generic; {t} = the hit).
HEADER_SUFFIXES = ("-Age", "-Length", "-Type", "-Control", "-Encoding", "-Origin", "-Modified", "-Since",
                   "-Match", "-Range", "-Forwards")
RANGE_PAIR_WORDS = ("Min", "Avg", "Mean", "Sum", "Count", "StdVal")
PERSON_CATEGORIES = ("people",)
RDONLY_FLAG_CTX = re.compile(r"(?i:sqlite|mode\s*=|\.db\b|_meta\b|read-?only)|唯讀")
WIDE_EXEMPT = 20          # an exemption that releases >= this many hits is flagged for review
# Item 2: a long one-word real (>= LOOSE_MIN alphanumerics) is also searched with one optional
# separator between any two characters ('xxxx-yyyy', 'xxxx_yyyy', 'xxxx yyyy'), word-bounded.
LOOSE_MIN = 6
LOOSE_SEP = r"[-_ .]?"

SHORT_MAX = 4            # names of length <= 4 use the relaxed boundary rule
CTX = 30                 # context characters on each side in the report
HEXRUN_MIN = 16          # round 2: only hex runs of >= 16 chars are hash-shaped
MARKER_RE = re.compile(r"^<[^>]*>$")
SEP_CHARS = " \t\r\n\f\v_.-+"
SEP_CLASS = r"[\s_.\-+]"          # separators between words of a multi-word name
FLEX_SEP = SEP_CLASS + "*"
MAX_UNPACK = 50 * 1024 * 1024     # per archive member / gunzip output
MAX_DEPTH = 2                     # container recursion depth
URL_ROUNDS = 3
AMBIG_SAMPLES = 20
WIN = 300                         # exemption window for views without line structure

ALL_SCOPES = frozenset({"names", "numbers", "generic", "tregex"})


class UsageError(Exception):
    pass


# ---------------------------------------------------------------------------
# Table loading
# ---------------------------------------------------------------------------
@dataclass
class NameForm:
    text: str            # form to search for (report key)
    origin: str          # the table's real value
    cats: set = field(default_factory=set)
    kind: str = "literal"    # literal | glob | flex | phone | tregex
    regex: re.Pattern | None = None
    short: bool = False
    pathlike: bool = False
    table_scan_false: bool = False   # informational: the table says "scan: false"
    derived: str = ""        # "", lower, capitalized, upper (short-name case forms)
    prefilter: str = ""      # lowercase token that must occur before the regex is run
    meta: dict | None = None  # tregex: value group / map semantics
    edges: tuple = (False, False)   # flex: apply the short-name boundary at the left / right edge


# Top-level table sections that are engine / scan configuration, not name entries.
# Round 3 (item 4): a "token" inside the engine section (e.g. an identifier_only rule)
# is a *real* word the engine handles specially, never a public value; collecting it
# as public silently masked every occurrence of that real word.
NON_ENTRY_SECTIONS = ("engine", "scan")


def _walk(obj, path, out):
    if isinstance(obj, dict):
        if isinstance(obj.get("real"), str) or isinstance(obj.get("real_zh"), str):
            out.append((path, obj))
        for k, v in obj.items():
            if not path and str(k) in NON_ENTRY_SECTIONS:
                continue
            _walk(v, path + (str(k),), out)
    elif isinstance(obj, list):
        for x in obj:
            _walk(x, path, out)


def _collect_public(obj, acc, top=True):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if top and str(k) in NON_ENTRY_SECTIONS:
                continue
            if isinstance(v, str) and (k.startswith("alias") or k.startswith("token")):
                acc.add(v)
            else:
                _collect_public(v, acc, False)
    elif isinstance(obj, list):
        for x in obj:
            _collect_public(x, acc, False)


def _is_keep(path, entry):
    if "keep" in path[:2] and path[:1] == ("categories",):
        return True
    alias = entry.get("alias")
    if isinstance(alias, str) and alias.strip().lower() == "<keep>":
        return True
    if str(entry.get("action", "")).lower() == "keep":
        return True
    return False


def _glob_to_regex(s, ci):
    parts = [re.escape(p) for p in s.split("*")]
    body = r"[A-Za-z0-9_.\-]*".join(parts)
    return re.compile(body, re.I if ci else 0)


def _path_forms(s):
    if "/" not in s and "\\" not in s:
        return [s]
    toks = re.split(r"[\\/]+", s)
    return sorted({"/".join(toks), "\\".join(toks), "\\\\".join(toks)})


def _has_ascii_letter(s):
    return any(("A" <= c <= "Z") or ("a" <= c <= "z") for c in s)


def _case_variants(r):
    out = []
    for label, v in (("lower", r.lower()), ("capitalized", r[:1].upper() + r[1:].lower()),
                     ("upper", r.upper())):
        if v != r and v not in [x for x, _ in out]:
            out.append((v, label))
    return out


_PHONE_SHAPE = re.compile(r"\+?[\d\s\-().]+")


def _phone_regex(r):
    """Digits of a phone-like real value, any of space/-/./() (or nothing) between them."""
    if not _PHONE_SHAPE.fullmatch(r):
        return None
    digits = re.sub(r"\D", "", r)
    if len(digits) < 8:
        return None
    return re.compile(r"(?<!\d)" + r"[\s\-().]*".join(digits) + r"(?!\d)")


def _flex_regex(r):
    """Multi-word name: internal separator runs become 'zero or more of [space _ . - +]'."""
    a = 0
    while a < len(r) and r[a] in SEP_CHARS:
        a += 1
    b = len(r)
    while b > a and r[b - 1] in SEP_CHARS:
        b -= 1
    core = r[a:b]
    toks = [t for t in re.split(SEP_CLASS + "+", core) if t]
    if len(toks) < 2:
        return None, "", (False, False)
    pat = re.escape(r[:a]) + FLEX_SEP.join(re.escape(t) for t in toks) + re.escape(r[b:])
    pre = max(toks, key=len).lower()
    edges = (a == 0 and len(toks[0]) <= SHORT_MAX, b == len(r) and len(toks[-1]) <= SHORT_MAX)
    return re.compile(pat, re.I), (pre if len(pre) >= 2 else ""), edges


# --- trie regex (fast alternation of many literals) --------------------------
_END = object()


def trie_pattern(seqs):
    """seqs: iterable of lists of regex atoms (already escaped).  Returns a pattern
    equivalent to the alternation of all sequences, greedy-longest first."""
    root = {}
    for seq in seqs:
        node = root
        for a in seq:
            node = node.setdefault(a, {})
        node[_END] = True

    def gen(node):
        end = _END in node
        alts = [a + gen(node[a]) for a in sorted(k for k in node if k is not _END)]
        if not alts:
            return ""
        if len(alts) == 1 and not end:
            return alts[0]
        g = "(?:" + "|".join(alts) + ")"
        return g + "?" if end else g
    return gen(root)


NUM_SEP_ATOM = r"[-\s]?"


def _num_atoms(key):
    m = re.match(r"([A-Za-z]+)(\d.*)$", key)
    if m:
        return [re.escape(c) for c in m.group(1).lower()] + [NUM_SEP_ATOM] + \
               [re.escape(c) for c in m.group(2).lower()]
    return [re.escape(c) for c in key.lower()]


def _norm_num(s):
    return re.sub(r"[-\s]", "", s).upper()


@dataclass
class Tables:
    names: list            # list[NameForm] (literal/glob/flex/phone)
    tregex: list           # list[NameForm] (table regex entries)
    numbers: dict          # real value -> map group
    numbers_norm: dict     # normalised real value -> real value
    group_reals: dict
    group_fakes: dict
    public: set            # public values (original case)
    public_lc: list        # lowercased, non-empty
    ambiguous_names: dict  # form -> reason
    ambiguous_numbers: dict
    public_emails: set
    skipped: dict
    regex_errors: list
    num_rx: re.Pattern | None = None
    shapes: list = field(default_factory=list)       # derived shape patterns (item 20)
    all_fakes_norm: set = field(default_factory=set)
    synthetic: dict | None = None
    _bin: dict | None = None
    # round 3
    embed_words: dict = field(default_factory=dict)    # lower word -> sorted origins (items 1/6)
    embed_rx: re.Pattern | None = None
    site_codes: dict = field(default_factory=dict)     # lower code -> host origins (item 6)
    site_forms: list = field(default_factory=list)     # NameForm list for the boundary scan of site codes
    ident_words: dict = field(default_factory=dict)    # lower word -> (origin, exact) (item 4)
    mes_known: set = field(default_factory=set)        # real MES prefixes from the table (item 5)
    mes_replaced: set = field(default_factory=set)     # prefixes of public replacement values (item 5)
    loose_rx: re.Pattern | None = None                 # separator variants of long one-word reals (item 2)
    loose_map: dict = field(default_factory=dict)      # lower real -> NameForm
    group_widths: dict = field(default_factory=dict)   # map group -> digit widths of pure-numeric keys (item 7)
    # WP-P4
    stats: dict | None = None                          # --stats: {"rx", "forms", "path", "count"} (rule 1)
    short_rx: re.Pattern | None = None                 # unpadded short forms of zero-padded map keys (rule 2)
    short_map: dict = field(default_factory=dict)      # short form -> [padded real keys]
    short_fake: set = field(default_factory=set)       # unpadded forms of public (fake) padded values
    mes_alt_rx: re.Pattern | None = None               # snake / dotted-alias / lower-case forms of real prefixes (rule 3)
    mes_alias_rx: re.Pattern | None = None             # SQL "AS <pfx>" / "FROM|JOIN <table> <pfx>" alias forms (rule 3)
    # R-P17 D1
    stems: set = field(default_factory=set)            # lower reals read as identifier stems (engine.stem)
    stem_except: tuple = ()                            # ordinary English words that start with a stem


def build_tables(names_doc, maps_doc):
    entries = []
    _walk(names_doc, (), entries)
    public = set()
    _collect_public(names_doc, public)
    public = {p.replace("*", "") for p in public if isinstance(p, str)}
    public = {p for p in public if p.strip() and not MARKER_RE.match(p.strip())}
    maps = (maps_doc or {}).get("maps", maps_doc or {})
    numbers = {}
    fake_vals = {}
    group_reals = defaultdict(set)
    group_fakes = defaultdict(set)
    for grp, mp in maps.items():
        if not isinstance(mp, dict):
            continue
        for k, v in mp.items():
            numbers.setdefault(str(k), grp)
            fake_vals.setdefault(str(v), grp)
            group_reals[grp].add(_norm_num(str(k)))
            group_fakes[grp].add(_norm_num(str(v)))
    public |= set(fake_vals)
    skipped = {"keep": 0}
    keep_vals = set()
    regex_errors = []
    forms: dict[tuple, NameForm] = {}
    tregex: dict[str, NameForm] = {}
    embed_words, ident_words, site_codes = {}, {}, {}
    mes_known, mes_replaced = set(), set()

    def put(fkey, nf, cat, scan_false):
        cur = forms.get(fkey)
        if cur is None:
            forms[fkey] = cur = nf
        cur.cats.add(cat)
        if scan_false:
            cur.table_scan_false = True

    for path, e in entries:
        cat = "/".join(path[:2]) if path else "?"
        scan_false = e.get("scan") is False
        if e.get("regex"):
            pat = e.get("real")
            if not isinstance(pat, str):
                continue
            try:
                rx = re.compile(pat)
            except re.error as ex:
                regex_errors.append((cat, pat, str(ex)))
                continue
            vg = e.get("value_group")
            if vg is None and "v" in rx.groupindex:
                vg = "v"
            grp = e.get("map_group")
            if grp is None and e.get("class_id") in group_reals:
                grp = e.get("class_id")
            mapped = bool(grp or e.get("strategy") == "private_map" or e.get("prefix_alias"))
            meta = {"vg": vg, "grp": grp, "mapped": mapped, "known_only": bool(e.get("known_only")),
                    "permutation": bool(e.get("permutation")), "pad_to": int(e.get("pad_to") or 0)}
            nf = tregex.get(pat)
            if nf is None:
                nf = tregex[pat] = NameForm(text=pat, origin=pat, kind="tregex", regex=rx, meta=meta)
            nf.cats.add(cat)
            # item 5: a table regex of the form "<3 lower letters>(?=[A-Z])" names a MES prefix
            mp = re.match(r"(?:\(\?<![^)]*\))?([a-z]{3})\(\?=\[A-Z\]\)", pat)
            if mp and (path[1] if len(path) > 1 else "") in MES_PREFIX_CATEGORIES:
                mes_known.add(mp.group(1))
            al = e.get("alias")
            if isinstance(al, str) and re.fullmatch(r"[a-z]{3}", al):
                mes_replaced.add(al)
            continue
        if _is_keep(path, e):
            skipped["keep"] += 1
            # extension: a kept phrase is public text, so it also masks
            # (e.g. a real word inside a kept multi-word phrase).
            if isinstance(e.get("real"), str) and len(e["real"]) > SHORT_MAX:
                keep_vals.add(e["real"])
            continue
        pathlike_entry = str(e.get("scope", "")).lower() == "path"
        exact = str(e.get("case", "")).lower() == "exact"
        leaf = path[1] if len(path) > 1 else (path[0] if path else "")
        rv = e.get("real")
        if isinstance(rv, str) and not scan_false:
            core = rv.strip("_-.")
            if EMBED_MIN <= len(core) <= SHORT_MAX and core.isascii() and core.isalnum() \
                    and leaf in EMBED_CATEGORIES:
                embed_words.setdefault(core.lower(), set()).add(rv)
            if 2 <= len(core) <= SHORT_MAX and core.isascii() and core.isalnum():
                ident_words.setdefault(core.lower(), (rv, exact))
            if leaf in SITE_CODE_CATEGORIES:
                frag = re.split(r"[-._@]", rv, maxsplit=1)[0]
                if frag != rv and SITE_CODE_RX.fullmatch(frag):
                    site_codes.setdefault(frag.lower(), set()).add(rv)
            m = re.match(r"([a-z]{3})[A-Z]", rv)
            if m and leaf in MES_PREFIX_CATEGORIES:
                mes_known.add(m.group(1))
        for k in ("alias", "token"):
            av = e.get(k)
            if isinstance(av, str):
                m = re.match(r"([a-z]{3})[A-Z]", av)
                if m:
                    mes_replaced.add(m.group(1))
        variants_r = []
        for key in ("real", "real_zh"):
            r = e.get(key)
            if not isinstance(r, str) or not r.strip():
                continue
            variants_r.append((r, "", r))
            # item 2: bare form (leading/trailing '_', '-', '.' removed), and the
            # un-numbered form of a numbered work-directory name ("NN-name" -> "name")
            bare = r.strip("_-.")
            if bare != r and len(bare) >= 3 and not pathlike_entry and not any(c in bare for c in "/\\*"):
                variants_r.append((bare, "bare", r))
            mnum = re.fullmatch(r"\d\d-(.{4,})", r)
            if mnum:
                variants_r.append((mnum.group(1), "unnumbered", r))
        for r, vlabel, r_orig in variants_r:
            is_path = "/" in r or "\\" in r
            if is_path or "*" in r:
                for form in _path_forms(r):
                    is_glob = "*" in form
                    short = len(form.replace("*", "")) <= SHORT_MAX
                    nf = NameForm(text=form, origin=r, kind="glob" if is_glob else "literal", short=short,
                                  pathlike=pathlike_entry or is_path)
                    if is_glob:
                        nf.regex = _glob_to_regex(form, not short)
                    put((form if short else form.lower(), nf.kind), nf, cat, scan_false)
                continue
            if len(r) <= SHORT_MAX:
                variants = [(r, "")]
                if not exact and _has_ascii_letter(r):
                    variants += _case_variants(r)
                for v, label in variants:
                    nf = NameForm(text=v, origin=r_orig if vlabel else r, short=True,
                                  derived=(vlabel + " " + label).strip() if vlabel else label,
                                  pathlike=pathlike_entry)
                    put((v, "short"), nf, cat, scan_false)
                continue
            prx = _phone_regex(r)
            if prx is not None:
                nf = NameForm(text=r, origin=r, kind="phone", regex=prx, pathlike=pathlike_entry,
                              prefilter=re.sub(r"\D", "", r)[-4:])
                put((r, "phone"), nf, cat, scan_false)
                continue
            frx, pre, edges = _flex_regex(r)
            if frx is not None:
                nf = NameForm(text=r, origin=r, kind="flex", regex=frx, prefilter=pre, pathlike=pathlike_entry,
                              edges=edges)
                put((r.lower(), "flex"), nf, cat, scan_false)
                continue
            nf = NameForm(text=r, origin=r_orig, pathlike=pathlike_entry, derived=vlabel)
            put((r.lower(), "literal"), nf, cat, scan_false)
    pub_lc = {p.lower() for p in public}
    amb_names = {}
    for nf in forms.values():
        t = nf.text.replace("*", "")
        if (t in public) if nf.short else (t.lower() in pub_lc):
            amb_names[nf.text] = "real value equals a public alias/token/fake value"
    amb_nums = {}
    for k, grp in numbers.items():
        if k in fake_vals:
            amb_nums[k] = f"real in map '{grp}', also fake value in map '{fake_vals[k]}'"
    emails = {p.lower() for p in public if "@" in p}
    author = names_doc.get("author") if isinstance(names_doc, dict) else None
    if isinstance(author, dict) and isinstance(author.get("email_public"), str):
        emails.add(author["email_public"].lower())
    numbers_norm = {}
    for k in numbers:
        numbers_norm.setdefault(_norm_num(k), k)
    num_rx = None
    if numbers:
        num_rx = re.compile(r"(?<!\d)" + trie_pattern(_num_atoms(k) for k in numbers) + r"(?!\d)", re.I)
    # item 1/6: one case-insensitive alternation for all embedded short words (site codes included)
    for code, hosts in site_codes.items():
        embed_words.setdefault(code, set()).update(hosts)
    embed_rx = None
    if embed_words:
        embed_rx = re.compile("|".join(re.escape(w) for w in sorted(embed_words, key=lambda x: (-len(x), x))), re.I)
    site_forms = []
    for code in sorted(site_codes):
        for v, label in [(code, "")] + _case_variants(code):
            site_forms.append(NameForm(text=v, origin=code, short=True, derived=label,
                                       cats={"site-code of " + ", ".join(sorted(site_codes[code]))}))
    widths = {}
    for grp, mp in maps.items():
        if isinstance(mp, dict):
            ws = {len(str(k)) for k in mp if str(k).isdigit()}
            if ws and all(str(k).isdigit() for k in mp):
                widths[grp] = ws
    # WP-P4 rule 2: unpadded short form of zero-padded pure-digit map keys
    short_map = {}
    for k in numbers:
        if k.isdigit() and len(k) >= 2 and k.startswith("0") and k.strip("0"):
            short_map.setdefault(k.lstrip("0"), []).append(k)
    short_rx = None
    short_fake = set()
    if short_map:
        short_rx = re.compile(r"(?<!\d)0*(" + "|".join(re.escape(x) for x in sorted(short_map, key=lambda x: (-len(x), x)))
                              + r")(?!\d)")
        short_fake = {v.lstrip("0") for v in fake_vals if v.isdigit() and v.startswith("0")}
    # WP-P4 rule 3: alternative spellings of the real MES prefixes named by the table
    real_pfx = sorted(mes_known - mes_replaced)
    mes_alt_rx = mes_alias_rx = None
    if real_pfx:
        alt = "|".join(re.escape(x) for x in real_pfx)
        mes_alt_rx = re.compile(
            r"(?<![A-Za-z0-9_$])(?P<p>" + alt + r")(?:(?P<sn>_(?=[A-Za-z]))|(?P<dt>\.(?=[A-Za-z_\[\"`]))"
            r"|(?P<lc>(?=[a-z]{3})[a-z]+(?![A-Za-z0-9_])))")
        mes_alias_rx = re.compile(
            r"(?i:\bas\s+|\b(?:from|join)\s+[\w.\[\]\"`]+\s+)(?P<p>" + alt + r")(?![A-Za-z0-9_])")
    # item 2: '-', '_', '.' or ' ' between any two characters of a long one-word real
    loose_map = {}
    for nf in forms.values():
        if nf.kind == "literal" and not nf.short and len(nf.text) >= LOOSE_MIN and nf.text.isascii() \
                and nf.text.isalnum() and nf.text.lower() not in pub_lc:
            loose_map.setdefault(nf.text.lower(), nf)
    loose_rx = None
    if loose_map:
        seqs = []
        for t in loose_map:
            atoms = []
            for i, c in enumerate(t):
                if i:
                    atoms.append(LOOSE_SEP)
                atoms.append(re.escape(c))
            seqs.append(atoms)
        loose_rx = re.compile(trie_pattern(seqs), re.I)
    # R-P17 D1: identifier stems (own reading of engine.stem): a long real name directly followed
    # by lower-case letters still counts when it is a declared stem (e.g. '<stem>weekly')
    eng = (names_doc or {}).get("engine") if isinstance(names_doc, dict) else None
    stem_cfg = (eng or {}).get("stem") or {}
    stem_cats = set(stem_cfg.get("categories") or [])
    stems = set()
    for path, e in entries:
        rv = e.get("real")
        leaf = path[1] if len(path) > 1 else ""
        if isinstance(rv, str) and re.fullmatch(r"[A-Za-z0-9]+", rv) and not e.get("regex") \
                and (e.get("stem") is True or (e.get("stem") is not False and leaf in stem_cats)):
            stems.add(rv.lower())
    return Tables(
        stems=stems, stem_except=tuple(str(w).lower() for w in (stem_cfg.get("except_words") or [])),
        embed_words={k: sorted(v) for k, v in embed_words.items()}, embed_rx=embed_rx,
        site_codes={k: sorted(v) for k, v in site_codes.items()}, site_forms=site_forms,
        ident_words=ident_words, mes_known=mes_known - mes_replaced, mes_replaced=mes_replaced,
        loose_rx=loose_rx, loose_map=loose_map,
        group_widths=widths, short_rx=short_rx, short_map=short_map, short_fake=short_fake,
        mes_alt_rx=mes_alt_rx, mes_alias_rx=mes_alias_rx,
        names=sorted(forms.values(), key=lambda f: -len(f.text)),
        tregex=list(tregex.values()),
        numbers=numbers, numbers_norm=numbers_norm,
        group_reals=dict(group_reals), group_fakes=dict(group_fakes),
        public=public,
        public_lc=sorted({p.lower() for p in (public | keep_vals) if p}, key=len, reverse=True),
        ambiguous_names=amb_names, ambiguous_numbers=amb_nums,
        public_emails=emails, skipped=skipped, regex_errors=regex_errors, num_rx=num_rx,
        shapes=derive_shapes(maps),
        all_fakes_norm=set().union(*group_fakes.values()) if group_fakes else set())


# --- shape patterns (item 20) --------------------------------------------------
_PREFIXED = re.compile(r"([A-Za-z]+)(.+)$")


def _runs(body):
    runs = []
    for ch in body:
        c = "D" if ch.isdigit() else ("U" if ch.isupper() else "L" if ch.isalpha() else "O" + ch)
        if runs and runs[-1][0] == c:
            runs[-1][1] += 1
        else:
            runs.append([c, 1])
    return tuple((c, n) for c, n in runs)


def _sig_text(sig):
    return "".join(("9" if c == "D" else "A" if c == "U" else "a" if c == "L" else c[1:]) * n for c, n in sig)


def derive_shapes(maps):
    """Per map group: real prefixes, public prefixes and body run-signatures, induced from the
    map's keys (real) and values (fake).  Pure-numeric groups (no letter prefix) are not shaped."""
    out = []
    for grp, mp in maps.items():
        if not isinstance(mp, dict):
            continue
        rp, sigs, fp = set(), set(), set()
        for k in mp:
            m = _PREFIXED.match(str(k))
            if m:
                rp.add(m.group(1).upper())
                sigs.add(_runs(m.group(2)))
        for v in mp.values():
            m = _PREFIXED.match(str(v))
            if m:
                fp.add(m.group(1).upper())
        if not rp:
            continue
        real_only = rp - fp
        bodies = set()
        for sig in sigs:
            parts = []
            for c, n in sig:
                if c == "D":
                    parts.append(r"\d{%d,%d}" % (max(1, n - 1), n + 1))
                elif c == "U":
                    parts.append(r"[A-Z]{%d}" % n)
                elif c == "L":
                    parts.append(r"[a-z]{%d}" % n)
                else:
                    parts.append(re.escape(c[1:]) * n)
            bodies.add("".join(parts))
        rx = None
        if real_only and bodies:
            # prefix case-insensitive; body keeps the induced per-position class (digit/upper/lower)
            rx = re.compile(r"(?<![A-Za-z0-9])(?P<p>(?i:" + "|".join(sorted(map(re.escape, real_only), key=lambda x: (-len(x), x)))
                            + r"))(?P<s>[-_ ]?)(?P<b>" + "|".join(sorted(bodies, key=lambda x: (-len(x), x)))
                            + r")(?![A-Za-z0-9])")
        out.append({"group": grp, "real_prefixes": sorted(rp), "public_prefixes": sorted(fp),
                    "overlap": sorted(rp & fp), "sigs": sorted(_sig_text(s) for s in sigs), "rx": rx})
    return out


def _syn_range_rule(grp, status, lo, hi, where):
    """One [lo, hi] range: ints = unprefixed numbers (digit width taken from the map group);
    'PFX123'-style strings with one shared prefix = prefixed numeric; other equal-length
    strings = lexical range on the normalised value (e.g. 'S000001'..'S0ZZZZZ')."""
    if isinstance(lo, bool) or isinstance(hi, bool):
        raise UsageError(f"{where}: range bounds must be numbers or strings")
    if isinstance(lo, int) and isinstance(hi, int):
        return (grp, status, "num", (lo, hi))
    lo, hi = _norm_shape(str(lo)), _norm_shape(str(hi))
    m1, m2 = _PREFIXED.match(lo), _PREFIXED.match(hi)
    if m1 and m2 and m1.group(1) == m2.group(1) and m1.group(2).isdigit() and m2.group(2).isdigit():
        return (grp, status, "pnum", (m1.group(1), int(m1.group(2)), int(m2.group(2))))
    if lo.isdigit() and hi.isdigit():
        return (grp, status, "num", (int(lo), int(hi)))
    if len(lo) != len(hi):
        raise UsageError(f"{where}: lexical range bounds must have the same length ({lo!r}, {hi!r})")
    return (grp, status, "lex", (lo, hi))


def _syn_regex(p, where):
    try:
        return re.compile(str(p))
    except re.error as ex:
        raise UsageError(f"{where}: bad regex {p!r}: {ex}")


def load_synthetic(path=None, policy=None):
    """Synthetic-range / placeholder rules (item 7).  Values matching a rule are reported in the
    'synthetic / placeholder' column and are never hits (shape hits and table-regex values that
    are outside the private map; a value that IS a real map key is always a hit).

    Two sources, both optional, merged:
      --synthetic FILE (verifier format, group-less):
          values: [..]; ranges: [{prefix: XX, from: N, to: M}] (prefix may be '' = unprefixed);
          patterns: [regex] (full match on the normalised value)
      --policy FILE (the builder's policy file, read as DATA only):
          synthetic_ranges: {group: [[lo, hi], ...]}     -> status 'synthetic'
          placeholders: {group: {ranges: [[lo, hi]], values: [..], regex: '..'}} -> status 'placeholder'
    Normalised value = upper case, '-', '_' and spaces removed."""
    rules, paths = [], []
    if path:
        doc = _load_yaml(path) or {}
        if not isinstance(doc, dict):
            raise UsageError("synthetic file: expected a mapping with values/ranges/patterns")
        paths.append(path)
        for v in doc.get("values") or []:
            rules.append((None, "synthetic", "value", _norm_shape(str(v))))
        for i, r in enumerate(doc.get("ranges") or []):
            try:
                pfx = str(r.get("prefix", "") or "").upper()
                a, b = int(r["from"]), int(r["to"])
            except (KeyError, TypeError, ValueError, AttributeError):
                raise UsageError(f"synthetic range #{i}: needs prefix/from/to")
            rules.append((None, "synthetic", "pnum" if pfx else "num", (pfx, a, b) if pfx else (a, b)))
        for p in doc.get("patterns") or []:
            rules.append((None, "synthetic", "regex", _syn_regex(p, "synthetic pattern")))
    if policy:
        doc = _load_yaml(policy) or {}
        if not isinstance(doc, dict):
            raise UsageError("policy file: expected a mapping")
        paths.append(policy)
        for grp, rs in (doc.get("synthetic_ranges") or {}).items():
            for i, r in enumerate(rs or []):
                if not isinstance(r, (list, tuple)) or len(r) != 2:
                    raise UsageError(f"policy synthetic_ranges.{grp}[{i}]: expected [lo, hi]")
                rules.append(_syn_range_rule(str(grp), "synthetic", r[0], r[1],
                                             f"policy synthetic_ranges.{grp}[{i}]"))
        for grp, d in (doc.get("placeholders") or {}).items():
            if not isinstance(d, dict):
                continue
            for i, r in enumerate(d.get("ranges") or []):
                if not isinstance(r, (list, tuple)) or len(r) != 2:
                    raise UsageError(f"policy placeholders.{grp}.ranges[{i}]: expected [lo, hi]")
                rules.append(_syn_range_rule(str(grp), "placeholder", r[0], r[1], f"policy placeholders.{grp}"))
            for v in d.get("values") or []:
                rules.append((str(grp), "placeholder", "value", _norm_shape(str(v))))
            if d.get("regex"):
                rules.append((str(grp), "placeholder", "regex", _syn_regex(d["regex"], f"policy placeholders.{grp}")))
    if not paths:
        return None
    return {"rules": rules, "path": ", ".join(paths)}


def prefixed_placeholders(syn, names_doc):
    """v9 (WP-N9 3-5 (a)): a map group whose table entry sets placeholder_prefix_alias has its policy
    placeholders published with the public prefix (digits unchanged).  Add that public-prefix form of each
    placeholder value rule so the shape scan still recognises it as a placeholder (read from the table as
    data; the verifier keeps its own reading)."""
    if not syn or not isinstance(names_doc, dict):
        return syn
    entries = []
    _walk(names_doc, (), entries)
    alias = defaultdict(dict)
    flagged = set()
    for _path, e in entries:
        grp = e.get("map_group") or e.get("class_id")
        if not e.get("regex") or not grp or not isinstance(e.get("prefix_alias"), dict):
            continue
        for k, v in e["prefix_alias"].items():
            if len(str(k)) == len(str(v)):
                alias[str(grp)][str(k).upper()] = str(v).upper()
        if e.get("placeholder_prefix_alias"):
            flagged.add(str(grp))
    extra = []
    for rgrp, status, kind, data in syn["rules"]:
        if status != "placeholder" or kind != "value" or rgrp not in flagged:
            continue
        pa = alias.get(rgrp) or {}
        up = max((k for k in pa if data.startswith(k)), key=len, default="")
        if up:
            extra.append((rgrp, "placeholder", "value", pa[up] + data[len(up):]))
    if not extra:
        return syn
    return dict(syn, rules=list(syn["rules"]) + extra)


def _norm_shape(s):
    return re.sub(r"[-_\s]", "", s).upper()


def synthetic_class(syn, norm, grp=None, widths=None):
    """'synthetic' | 'placeholder' | None for a normalised value of map group grp (None = any group).
    An unprefixed numeric rule of a group only applies to values of that group's digit width."""
    if not syn:
        return None
    widths = widths or {}
    for rgrp, status, kind, data in syn["rules"]:
        if grp is not None and rgrp is not None and rgrp != grp:
            continue
        if kind == "value":
            ok = norm == data
        elif kind == "num":
            w = widths.get(rgrp) if rgrp is not None else None
            ok = norm.isdigit() and (not w or len(norm) in w) and data[0] <= int(norm) <= data[1]
        elif kind == "pnum":
            m = _PREFIXED.match(norm)
            ok = bool(m and m.group(1) == data[0] and m.group(2).isdigit()
                      and data[1] <= int(m.group(2)) <= data[2])
        elif kind == "lex":
            ok = len(norm) == len(data[0]) and data[0] <= norm <= data[1]
        else:
            ok = bool(data.fullmatch(norm))
        if ok:
            return status
    return None


def find_shape_hits(text, tables):
    """(group, s, e, status) with status hit | fake | known | synthetic | placeholder."""
    out = []
    for sh in tables.shapes:
        rx = sh["rx"]
        if rx is None:
            continue
        for m in rx.finditer(text):
            norm = _norm_shape(m.group("p") + m.group("b"))
            if norm in tables.all_fakes_norm:
                st = "fake"
            elif norm in tables.numbers_norm:
                st = "known"
            else:
                st = synthetic_class(tables.synthetic, norm, sh["group"], tables.group_widths) or "hit"
            out.append((sh["group"], m.start(), m.end(), st))
    return out


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------
def _is_letter(c):
    return ("A" <= c <= "Z") or ("a" <= c <= "z")


def short_boundary_ok(text, s, e):
    """Relaxed boundary: neighbour is not an ASCII letter, or a camel/acronym transition.
    A form whose own edge character is not a letter needs no boundary on that side.
    A trailing plural 's' / 'es' (then a non-letter) also counts as a boundary."""
    return _left_ok(text, s) and _right_ok(text, s, e)


def _left_ok(text, s):
    if s > 0 and _is_letter(text[s]):
        p, c = text[s - 1], text[s]
        if _is_letter(p) and not (p.islower() and c.isupper()):
            return False
    return True


def _right_ok(text, s, e, plural=True):
    if e < len(text) and _is_letter(text[e - 1]):
        p, n = text[e - 1], text[e]
        if _is_letter(n):
            camel = p.islower() and n.isupper()
            acronym = p.isupper() and n.isupper() and e + 1 < len(text) and text[e + 1].islower()
            if not (camel or acronym):
                form_upper = text[s:e].isupper()
                # round 3 (item 12): a lower-case form of <= 2 letters + 's' is another
                # word far more often than a plural ("_ros", "wrs"); plural only for longer
                # or not-all-lower-case forms ("QXs", "ABCs", "abcs").
                if text[s:e].islower() and e - s <= 2:
                    plural = False
                for suf in (("s", "es") + (("S", "ES") if form_upper else ())) if plural else ():
                    if text.startswith(suf, e):
                        after = e + len(suf)
                        if after >= len(text) or not _is_letter(text[after]):
                            return True
                return False
    return True


class Masker:
    """Answers: does span [s,e) of text lie entirely inside an occurrence of a public value?"""

    def __init__(self, public_lc):
        self.public_lc = public_lc
        self._cache = {}

    def _candidates(self, needle_lc):
        c = self._cache.get(needle_lc)
        if c is None:
            c = [p for p in self.public_lc if needle_lc in p and len(p) >= len(needle_lc)]
            self._cache[needle_lc] = c
        return c

    def masked(self, text_lc, s, e):
        needle = text_lc[s:e]
        for p in self._candidates(needle):
            off = p.find(needle)
            while off != -1:
                ps = s - off
                if ps >= 0 and text_lc[ps:ps + len(p)] == p:
                    return p
                off = p.find(needle, off + 1)
        return None


def _stem_hit(text, s, e, word_lc, tables):
    """R-P17 D1: an identifier stem directly followed by a lower-case letter, unless the letter run
    starting at the hit is an ordinary English word listed in engine.stem.except_words."""
    if word_lc not in tables.stems or e >= len(text) or not ("a" <= text[e] <= "z"):
        return False
    j = e
    while j < len(text) and _is_letter(text[j]):
        j += 1
    run = text[s:j].lower()
    return not any(run.startswith(w) for w in tables.stem_except)


def find_name_hits(text, tables, masker, multi_only=False, lc=None):
    """Return list of (NameForm, s, e, masked_by)."""
    if lc is None:
        lc = text.lower()
    raw = []
    for nf in tables.names:
        if multi_only and nf.kind not in ("flex", "phone"):
            continue
        if nf.kind == "glob":
            for m in nf.regex.finditer(text):
                s, e = m.span()
                if e == s:
                    continue
                if nf.short and not short_boundary_ok(text, s, e):
                    continue
                raw.append((nf, s, e))
        elif nf.kind in ("flex", "phone"):
            if nf.prefilter and nf.prefilter not in lc:
                continue
            le, re_ = nf.edges
            for m in nf.regex.finditer(text):
                s, e = m.span()
                if e == s:
                    continue
                # an edge word of <= SHORT_MAX letters follows the short-name boundary rule
                if (le and not _left_ok(text, s)) or (re_ and not _right_ok(text, s, e, plural=False)):
                    continue
                raw.append((nf, s, e))
        elif nf.short:
            t = nf.text
            i = text.find(t)
            while i != -1:
                if short_boundary_ok(text, i, i + len(t)):
                    raw.append((nf, i, i + len(t)))
                i = text.find(t, i + 1)
        else:
            t = nf.text.lower()
            i = lc.find(t)
            while i != -1:
                # R-P17 D1: a long-name substring hit also needs a right boundary (not directly followed
                # by a letter of the same word); a declared identifier stem followed by lower case still hits
                e2 = i + len(t)
                caps_end = (e2 >= 2 and e2 < len(text) and text[e2 - 1].isupper() and text[e2 - 2].isupper()
                            and "a" <= text[e2] <= "z")      # an all-caps form ends where lower case starts
                if _right_ok(text, i, e2) or caps_end or _stem_hit(text, i, e2, t, tables):
                    raw.append((nf, i, i + len(t)))
                i = lc.find(t, i + 1)
    # keep the longest hit when one real-name hit is nested inside another
    raw.sort(key=lambda h: (h[1], -(h[2] - h[1])))
    kept = []
    maxend = -1
    for h in raw:
        if h[2] <= maxend:
            continue
        kept.append(h)
        maxend = max(maxend, h[2])
    return [(nf, s, e, masker.masked(lc, s, e)) for nf, s, e in kept]


_HEX = set("0123456789abcdefABCDEF")


def hex_shape(text, s, e):
    a, b = s, e
    while a > 0 and text[a - 1] in _HEX:
        a -= 1
    while b < len(text) and text[b] in _HEX:
        b += 1
    run = text[a:b]
    if len(run) >= HEXRUN_MIN and all(c in _HEX for c in text[s:e]):
        return run
    return None


def find_number_hits(text, tables, masker, lc=None):
    rx = tables.num_rx
    if rx is None:
        return []
    if lc is None:
        lc = text.lower()
    out = []
    for m in rx.finditer(text):
        s, e = m.span()
        key = tables.numbers_norm.get(_norm_num(m.group(0)))
        if key is None:
            continue
        out.append((key, s, e, hex_shape(text, s, e), masker.masked(lc, s, e)))
    return out


def find_tregex_hits(text, tables):
    """Table regex entries.  Returns (NameForm, s, e, status, value).
    status: hit | fake | ambiguous | dup-number | unmapped | ignore | synthetic | placeholder."""
    out = []
    for nf in tables.tregex:
        meta = nf.meta
        for m in nf.regex.finditer(text):
            s, e = m.span()
            if e == s:
                continue
            if not meta["mapped"]:
                out.append((nf, s, e, "hit", m.group(0)))
                continue
            v = None
            if meta["vg"] is not None:
                try:
                    v = m.group(meta["vg"])
                except (IndexError, re.error):
                    v = None
            if v is None:
                v = m.group(0)
            grp = meta["grp"]
            reals = tables.group_reals.get(grp) if grp else None
            fakes = tables.group_fakes.get(grp) if grp else None
            if reals is None:
                reals = set(tables.numbers_norm)
            if fakes is None:
                fakes = set().union(*tables.group_fakes.values()) if tables.group_fakes else set()
            nv = _norm_num(v)
            cands = {nv}
            if meta["pad_to"] and nv.isdigit():
                cands.add(nv.zfill(meta["pad_to"]))
            in_real = bool(cands & reals)
            in_fake = bool(cands & fakes)
            if in_real and not in_fake and any(
                    synthetic_class(tables.synthetic, c, grp, tables.group_widths) == "placeholder" for c in cands):
                in_real = False     # v9 (WP-N9 §1): declared placeholder -> not a real value (falls through below)
            if in_real and in_fake:
                st = "ambiguous"
            elif in_fake:
                st = "fake"
            elif in_real:
                st = "dup-number" if nv in tables.numbers_norm else "hit"
            elif any(synthetic_class(tables.synthetic, c, grp, tables.group_widths) for c in cands):
                # item 7: value outside the map that lies in a synthetic range / placeholder rule
                st = next(synthetic_class(tables.synthetic, c, grp, tables.group_widths)
                          for c in cands if synthetic_class(tables.synthetic, c, grp, tables.group_widths))
            elif meta["known_only"]:
                st = "ignore"
            elif meta["permutation"]:
                st = "unmapped"
            else:
                st = "hit"
            out.append((nf, s, e, st, v))
    return out


def find_generic_hits(text, tables):
    out = []
    for gp in GENERIC_PATTERNS:
        rx = gp.setdefault("_rx", re.compile(gp["pattern"]))
        allow = gp.get("allow")
        arx = gp.setdefault("_arx", re.compile(allow)) if allow else None
        for m in rx.finditer(text):
            v = m.group(0)
            if arx and arx.search(v):
                continue
            if gp["name"].startswith("email") and v.lower() in tables.public_emails:
                continue
            out.append((gp["name"], m.start(), m.end()))
    return out


# ---------------------------------------------------------------------------
# Round 3: structural context rules (item 12) and the new detectors (items 1-6)
# ---------------------------------------------------------------------------
CONTEXT_RULE_DOC = {
    "http-header-name": "capitalised short form immediately followed by a known HTTP header / cookie "
                        "attribute suffix (" + ", ".join(HEADER_SUFFIXES) + ")",
    "inside-longer-alphanumeric-code": "a letter+digit form (e.g. a two-character model code) with a letter or digit "
                                       "directly next to it: it is part of a longer code, not the code itself",
    "enumerated-label": "a letter+digit form in parentheses when the same file also has the same letter with "
                        "other numbers in parentheses (test-case labels such as (x1) (x2) (x3))",
    "read-only-mode-flag": "a two-letter lower-case form with sqlite / mode= / .db / *_meta / read-only within "
                           "60 characters before it on the same line (the sqlite read-only open flag)",
    "person-name-as-identifier-part": "a person-category short name reached only through a camel-case / acronym "
                                      "boundary (a word part of a longer identifier such as <Name>RoundMinutes)",
    "expression-operand": "a person-category short name directly followed by '(', '=', '<' or '>' "
                          "(function / SQL aggregate / operand)",
    "range-pair-word": "a person-category short name next to " + "/".join(RANGE_PAIR_WORDS) + " (column pairs)",
    "powershell-comparison-operator": "a short literal that is a PowerShell comparison/logical operator "
                                      "written '-<op>' between whitespace ('$a -eq 1')",
    "css-selector-combinator": "a multi-word name whose separator run is whitespace followed by '.' or '#' "
                               "(CSS descendant selector such as '.a-x .b-y')",
}


ROUND3_SECTIONS = (
    ("embedded", "embedded short-word hits (items 1/6)"),
    ("ident", "whole-identifier hits (item 4)"),
    ("sitecode", "site-code hits (item 6)"),
    ("workdir", "work-directory path hits (item 3)"),
    ("mesprefix", "MES schema prefix hits (item 5)"),
    ("real_stat", "real-statistic value hits (WP-P4 rule 1)"),
)
ROUND3_DOC = {
    "embedded": "A short sensitive word (<= %d chars, table categories %s, plus site codes) inside a longer "
                "identifier where the boundary rule does not see it.  Occurrences inside a word of the "
                "exclusion list are dropped and listed under 'Embedded: dropped occurrences'."
                % (SHORT_MAX, ", ".join(EMBED_CATEGORIES)),
    "ident": "A binding identifier (import alias, assignment, def, for, lambda, let/const/var, $x =) that "
             "is exactly a short real word of the table.",
    "sitecode": "The site-code fragment of a table host name as a standalone word.",
    "workdir": "A numbered work-directory folder name ('NN-Name') inside a path (/, \\ or escaped \\\\).",
    "mesprefix": "An identifier whose 3-letter lower-case prefix is a real MES schema prefix (table) or "
                 "schema-shaped (the tree contains '<prefix>PK').  WP-P4 rule 3 adds, for table-named prefixes, the "
                 "snake_case form (`<pfx>_x`), the dotted SQL alias form (`<pfx>.x`), the SQL alias declaration "
                 "(`AS <pfx>`, `FROM|JOIN <table> <pfx>`) and the all-lower-case form (`<pfx>xyz`, only when the "
                 "camel form of the same identifier is in the tree or the line is SQL).  Full census in "
                 "'MES schema prefix census'.",
    "real_stat": "A value of the private real-statistics list (--stats; >= 4 digits or with a thousands separator) "
                 "as its original, thousands-stripped or thousands-grouped spelling, not adjacent to another digit "
                 "or a decimal fraction.",
}

# WP-P4 rule 2 / 3 configuration (shapes only; every sensitive value is read from the private files at run time)
EID_CONTEXT_RX = re.compile(r"EID[-_ ]?|eid[=:\"' ]|\"eid\"")
SQL_CONTEXT_RX = re.compile(r"(?i)\b(?:select|from|join|where|group\s+by|order\s+by)\b")
PS_OPERATORS = frozenset(("eq", "ne", "gt", "ge", "lt", "le", "ieq", "ine", "ceq", "cne", "like", "notlike",
                          "match", "notmatch", "contains", "notcontains", "in", "notin", "is", "isnot", "as",
                          "and", "or", "not", "xor", "replace", "split", "join", "band", "bor", "bxor", "shl", "shr"))
STAT_MIN_DIGITS = 4


def _cat_leaves(nf):
    return {c.split("/")[-1] for c in nf.cats}


def context_reject(nf, text, s, e):
    """Structural false-positive rules (item 12).  Returns a rule name or None.  No rule
    names a specific word: each rule is a shape (context) that applies to every form."""
    t = text[s:e]
    if nf.kind == "flex":
        return "css-selector-combinator" if re.search(r"\s[.#]", t) else None
    if not nf.short or nf.kind != "literal":
        return None
    n = len(text)
    if t.lower() in PS_OPERATORS and s > 0 and text[s - 1] == "-" and (s < 2 or text[s - 2] in " \t(") \
            and (e >= n or text[e] in " \t)"):
        return "powershell-comparison-operator"
    if t[:1].isupper():
        for suf in HEADER_SUFFIXES:
            if text.startswith(suf, e) and (e + len(suf) >= n or not text[e + len(suf)].isalnum()):
                return "http-header-name"
    if any(c.isdigit() for c in t) and any(c.isalpha() for c in t):
        if (s > 0 and text[s - 1].isascii() and text[s - 1].isalnum()) or \
                (e < n and text[e].isascii() and text[e].isalnum()):
            return "inside-longer-alphanumeric-code"
        m = re.fullmatch(r"([A-Za-z])(\d+)", t)
        if m and s > 0 and text[s - 1] in "(（" and e < n and text[e] in ")）":
            sib = re.compile(r"[(（]" + re.escape(m.group(1)) + r"(?!" + m.group(2) + r"[)）])\d+[)）]")
            if sib.search(text):
                return "enumerated-label"
    if len(t) == 2 and t.islower():
        ls = text.rfind("\n", 0, s) + 1
        if RDONLY_FLAG_CTX.search(text[max(ls, s - 60):s]) and (e >= n or not text[e].isalnum()):
            return "read-only-mode-flag"
    if _cat_leaves(nf) & set(PERSON_CATEGORIES):
        p = text[s - 1] if s > 0 else ""
        q = text[e] if e < n else ""
        if _is_letter(p) or _is_letter(q):
            return "person-name-as-identifier-part"
        if re.match(r"\s?[(=<>]", text[e:e + 2]):
            return "expression-operand"
        pair = "|".join(map(re.escape, RANGE_PAIR_WORDS))
        if re.search(r"(?:^|[^A-Za-z])(?:%s)\s+$" % pair, text[max(0, s - 12):s]) or \
                re.match(r"\s+(?:%s)(?![A-Za-z])" % pair, text[e:e + 12]):
            return "range-pair-word"
    return None


def _token_bounds(text, s, e):
    a, b = s, e
    while a > 0 and text[a - 1].isascii() and (text[a - 1].isalnum() or text[a - 1] == "_"):
        a -= 1
    while b < len(text) and text[b].isascii() and (text[b].isalnum() or text[b] == "_"):
        b += 1
    return a, b


def _short_texts(tables):
    st = getattr(tables, "_short_set", None)
    if st is None:
        st = {nf.text for nf in tables.names if nf.short and nf.kind == "literal"}
        tables._short_set = st
    return st


def find_embedded_hits(text, tables, masker, lc):
    """Items 1/6: short sensitive words INSIDE an identifier, where the regular short-name
    boundary rule does not see them (e.g. '<code>weekly').  Returns (word, s, e, token, status)
    with status hit | excluded:<word> | masked:<public>."""
    rx = tables.embed_rx
    out = []
    if rx is None:
        return out
    short_set = _short_texts(tables)
    for m in rx.finditer(text):
        s, e = m.span()
        a, b = _token_bounds(text, s, e)
        if a == s and b == e:
            continue                      # the whole token is the word: regular scan / ident class
        if text[s:e] in short_set and short_boundary_ok(text, s, e):
            continue                      # a regular short-name hit already reports it
        if text[s:e].lower() in tables.site_codes and _standalone(text, s, e):
            continue                      # the site-code class reports it
        if any(text[i - 1].islower() and text[i].isupper() for i in range(s + 1, e)):
            continue                      # spans a camel-case word boundary ('...apMaterial')
        if (text[e - 1].isdigit() and e < len(text) and text[e].isdigit()) or \
                (text[s].isdigit() and s > 0 and text[s - 1].isdigit()):
            continue                      # a digit-edged code continuing into more digits is another code
        tok = text[a:b]
        if len(tok) >= HEXRUN_MIN and all(c in _HEX for c in tok):
            continue                      # hash-shaped token
        w = m.group(0).lower()
        excl = None
        for word in EMBED_EXCLUDE_WORDS:
            off = word.find(w)
            while off != -1 and excl is None:
                ps = s - off
                if ps >= a and ps + len(word) <= b and lc[ps:ps + len(word)] == word:
                    excl = word
                off = word.find(w, off + 1)
            if excl:
                break
        if excl:
            out.append((w, s, e, tok, "excluded:" + excl))
            continue
        mb = masker.masked(lc, s, e)
        if mb:
            out.append((w, s, e, tok, "masked:" + mb))
            continue
        out.append((w, s, e, tok, "hit"))
    return out


def find_ident_hits(text, tables):
    """Item 4: a binding identifier that IS a short real word (import alias, assignment,
    def, for, lambda, let/const/var, PowerShell $x =).  Returns (origin, s, e, identifier)."""
    out = []
    if not tables.ident_words:
        return out
    amb = {k.lower() for k in tables.ambiguous_names}
    for m in IDENT_BIND_RX.finditer(text):
        for g in IDENT_GROUPS:
            v = m.group(g)
            if not v:
                continue
            info = tables.ident_words.get(v.lower())
            if info is None or v.lower() in amb:
                continue
            origin, exact = info
            if exact and v != origin.strip("_-."):
                continue
            out.append((origin, m.start(g), m.end(g), v))
    return out


def _standalone(text, s, e):
    return not (s > 0 and text[s - 1].isascii() and text[s - 1].isalnum()) \
        and not (e < len(text) and text[e].isascii() and text[e].isalnum())


def find_site_hits(text, tables):
    """Item 6: site codes (first fragment of a table host name) as standalone words."""
    out = []
    for nf in tables.site_forms:
        t = nf.text
        i = text.find(t)
        while i != -1:
            j = i + len(t)
            # standalone only: no letter/digit on either side (inside an identifier = embedded class)
            if _standalone(text, i, j):
                out.append((nf, i, j))
            i = text.find(t, i + 1)
    return out


def find_loose_hits(text, tables):
    """Item 2: separator variants of long one-word reals ('xxxx-yyyy', 'xxxx_yyyy', 'xxxx yyyy',
    any case).  The match must contain a separator (the plain form is the regular scan) and
    be word-bounded (camel / acronym boundaries and a plural 's' count, as for short names)."""
    rx = tables.loose_rx
    out = []
    if rx is None:
        return out
    for m in rx.finditer(text):
        s, e = m.span()
        t = m.group(0)
        if not any(c in "-_ ." for c in t):
            continue
        nf = tables.loose_map.get(re.sub(r"[-_ .]", "", t).lower())
        if nf is None or not _left_ok(text, s):
            continue
        # right edge: word boundary, or a single plural 's' then a non-letter ('-es' is not
        # accepted here: 'xxx_lin' + 'es' is another word far more often than a plural)
        if not _right_ok(text, s, e, plural=False) and not (
                text[e:e + 1] in ("s", "S") and (e + 1 >= len(text) or not _is_letter(text[e + 1]))):
            continue
        out.append((nf, s, e))
    return out


_SEP_END = re.compile(WORKDIR_SEP + r"$")
_SEP_START = re.compile(WORKDIR_SEP)


def find_workdir_hits(text):
    """Item 3: numbered work-directory folders ('NN-Name') in a path context (a '/', '\\' or
    escaped '\\\\' directly before, after or inside the matched folder chain).  Returns
    (in_path: list, bare: list) of (text, s, e)."""
    hits, bare = [], []
    for m in WORKDIR_RX.finditer(text):
        s, e = m.span()
        t = m.group(0)
        in_path = bool(_SEP_END.search(text[max(0, s - 2):s]) or _SEP_START.match(text, e)
                       or re.search(WORKDIR_SEP, t))
        (hits if in_path else bare).append((t, s, e))
    return hits, bare


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
_NL = re.compile("\n")


class LineIndex:
    __slots__ = ("text", "starts")

    def __init__(self, text):
        self.text = text
        self.starts = None

    def line(self, pos):
        if self.starts is None:
            self.starts = [0] + [m.end() for m in _NL.finditer(self.text)]
        return bisect.bisect_right(self.starts, pos)


class MappedLines:
    """Line numbers for a view whose lines were joined (maps view offset -> original line)."""
    __slots__ = ("starts", "lines")

    def __init__(self, starts, lines):
        self.starts = starts
        self.lines = lines

    def line(self, pos):
        i = bisect.bisect_right(self.starts, pos) - 1
        return self.lines[max(i, 0)] if self.lines else 1


def _json_unescape(t):
    def rep(m):
        return chr(int(m.group(1), 16))
    t2 = re.sub(r"\\u([0-9a-fA-F]{4})", rep, t)
    try:
        return t2.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeError:
        return t2


_HEXESC_RUN = re.compile(r"(?:\\x[0-9A-Fa-f]{2})+")
_OCTESC = re.compile(r"\\([0-7]{1,3})")


def _escape_decode(t):
    def hexrun(m):
        b = bytes(int(h, 16) for h in re.findall(r"\\x([0-9A-Fa-f]{2})", m.group(0)))
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("latin-1")
    t = _HEXESC_RUN.sub(hexrun, t)
    return _OCTESC.sub(lambda m: chr(int(m.group(1), 8) & 0xFF), t)


_FW_RX = re.compile("[\uff01-\uff5e\u3000]")


def _fullwidth(t):
    return _FW_RX.sub(lambda m: " " if m.group(0) == "\u3000" else chr(ord(m.group(0)) - 0xFEE0), t)


_CF_RX = None


def _cf_rx():
    global _CF_RX
    if _CF_RX is None:
        cps = [cp for cp in range(0x110000) if unicodedata.category(chr(cp)) == "Cf"]
        ranges = []
        for cp in cps:
            if ranges and ranges[-1][1] == cp - 1:
                ranges[-1][1] = cp
            else:
                ranges.append([cp, cp])
        body = "".join(re.escape(chr(a)) if a == b else re.escape(chr(a)) + "-" + re.escape(chr(b))
                       for a, b in ranges)
        _CF_RX = re.compile("[" + body + "]")
    return _CF_RX


_WS_RX = re.compile(r"\s+")
_COMMENT_PREFIX = re.compile(r"^\s*(?:#+|//+|\*+)")


def _ws_normalize(text):
    """All Unicode whitespace -> one space; lines joined; line-leading comment prefixes dropped."""
    parts, starts, lines = [], [], []
    pos = 0
    for i, line in enumerate(text.split("\n")):
        line = _COMMENT_PREFIX.sub("", line, count=1)
        line = _WS_RX.sub(" ", line).strip()
        if not line:
            continue
        if parts:
            pos += 1
        starts.append(pos)
        lines.append(i + 1)
        parts.append(line)
        pos += len(line)
    return " ".join(parts), MappedLines(starts, lines)


_DIGIT_GROUP = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def views_of(text):
    """Yield (view name, view text, scopes, line mapper or None)."""
    yield "raw", text, ALL_SCOPES, None
    if "%" in text:
        v = text
        for _ in range(URL_ROUNDS):
            v2 = urllib.parse.unquote_plus(v, errors="replace")
            if v2 == v:
                break
            v = v2
        if v != text:
            yield "url-decoded", v, ALL_SCOPES, None
    elif "+" in text:
        # plus-as-space only: '+' already separates words of multi-word names and
        # both are non-letters for short names, so only numbers/generic/regex can change.
        yield "url-decoded", text.replace("+", " "), frozenset({"numbers", "generic", "tregex"}), None
    if "&" in text:
        v = html.unescape(text)
        if v != text:
            yield "html-unescaped", v, ALL_SCOPES, None
    if "\\u" in text:
        v = _json_unescape(text)
        if v != text:
            yield "json-unescaped", v, ALL_SCOPES, None
    if "\\" in text:
        v = _escape_decode(text)
        if v != text:
            yield "escape-decoded", v, ALL_SCOPES, None
    if _FW_RX.search(text):
        yield "fullwidth-folded", _fullwidth(text), ALL_SCOPES, None
    if _cf_rx().search(text):
        yield "format-stripped", _cf_rx().sub("", text), ALL_SCOPES, None
    v, ml = _ws_normalize(text)
    if v != text:
        # only matches that can span whitespace/comment prefixes are new here
        yield "whitespace-normalized", v, frozenset({"multi", "generic", "tregex"}), ml
    if _DIGIT_GROUP.search(text):
        yield "digit-grouping-stripped", _DIGIT_GROUP.sub("", text), frozenset({"numbers", "tregex"}), None


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------
_BAD_ASCII = re.compile(r"[\x00-\x08\x0e-\x1a\x1c-\x1f\x7f]")


def _reasonable(t):
    if not t:
        return True
    if t.isascii():
        return _BAD_ASCII.search(t) is None
    bad = 0
    for ch in t:
        if ch.isascii():
            if _BAD_ASCII.match(ch):
                bad += 1
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cs", "Co", "Cn"):
            bad += 1
    return bad <= len(t) // 200


def decode_candidates(data):
    """Every decoding that yields reasonable text: [(encoding, text)], primary first."""
    out = []

    def add(enc, fn):
        try:
            t = fn()
        except (UnicodeDecodeError, UnicodeError, LookupError):
            return False
        if not _reasonable(t) or any(t == x for _, x in out):
            return False
        out.append((enc, t))
        return True
    if data[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
        add("utf-32", lambda: data.decode("utf-32"))
    elif data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        add("utf-16", lambda: data.decode("utf-16"))
    utf8_ok = add("utf-8", lambda: data.decode("utf-8-sig"))
    if len(data) % 2 == 0 and data[:2] not in (b"\xff\xfe", b"\xfe\xff"):
        nul = data.count(b"\x00")
        if not utf8_ok or nul * 20 >= len(data):
            add("utf-16-le", lambda: data.decode("utf-16-le"))
            add("utf-16-be", lambda: data.decode("utf-16-be"))
    if not utf8_ok:
        add("cp950", lambda: data.decode("cp950"))
    return out


# ---------------------------------------------------------------------------
# Exemptions
# ---------------------------------------------------------------------------
EXEMPT_FIELDS = ("name", "path", "context", "reason", "approver")


def load_exempt(path):
    if not path:
        return []
    import yaml
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    items = doc.get("exemptions", []) if isinstance(doc, dict) else (doc or [])
    if items is None:
        items = []
    if not isinstance(items, list):
        raise UsageError("exempt file: 'exemptions' must be a list")
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise UsageError(f"exempt entry #{i}: not a mapping")
        missing = [k for k in EXEMPT_FIELDS if not it.get(k)]
        if missing:
            raise UsageError(f"exempt entry #{i}: missing field(s) {missing}")
        paths = it["path"] if isinstance(it["path"], list) else [it["path"]]
        try:
            rx = re.compile(it["context"])
        except re.error as ex:
            raise UsageError(f"exempt entry #{i}: bad context regex: {ex}")
        out.append({"i": i, "name": str(it["name"]), "paths": [str(p) for p in paths], "rx": rx,
                    "reason": it["reason"], "approver": it["approver"], "used": 0,
                    "context": it["context"], "src": "exempt-file"})
    return out


_ROUND3_KINDS = ("embedded", "ident", "sitecode", "mesprefix", "real_stat")


def _ex_norm(x):
    return re.sub(r"[\s_\-]", "", x.lower())


def exempt_name_matches(exname, key):
    """Exemption name vs hit key: case- and separator-insensitive (a table exemption for
    'X Y' also covers the derived 'x_y' / 'XY' forms); a round-3 key '<kind>:<word>' is also
    matched by the bare word."""
    a, b = _ex_norm(exname), _ex_norm(key)
    if a == b:
        return True
    kind, _, rest = key.partition(":")
    return kind in _ROUND3_KINDS and a == _ex_norm(rest)


NAMES_EXEMPT_FIELDS = ("real", "paths", "reason", "approver")


def load_names_exemptions(names_doc, repo=None):
    """Item 8: the name table's own scan.exemptions, read as data.  Fields real / paths /
    reason / approver are required; context (regex, optional; its t* group or the whole
    match must cover the hit) defaults to the whole line; repo (str or list) limits the entry
    to that repo when --repo is given."""
    scan = (names_doc or {}).get("scan") if isinstance(names_doc, dict) else None
    items = (scan or {}).get("exemptions") or []
    if not isinstance(items, list):
        raise UsageError("names scan.exemptions must be a list")
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise UsageError(f"names scan.exemptions #{i}: not a mapping")
        missing = [k for k in NAMES_EXEMPT_FIELDS if not it.get(k)]
        if missing:
            raise UsageError(f"names scan.exemptions #{i}: missing field(s) {missing}")
        repos = it.get("repo")
        repos = [repos] if isinstance(repos, str) else list(repos or [])
        if repo and repos and repo not in repos:
            continue
        ctx = it.get("context") or r"[^\n]+"
        try:
            rx = re.compile(ctx)
        except re.error as ex:
            raise UsageError(f"names scan.exemptions #{i}: bad context regex: {ex}")
        paths = it["paths"] if isinstance(it["paths"], list) else [it["paths"]]
        out.append({"i": f"N{i}", "name": str(it["real"]), "paths": [str(p) for p in paths], "rx": rx,
                    "reason": it["reason"], "approver": it["approver"], "used": 0,
                    "context": it.get("context") or "(whole line)", "src": "names"})
    return out


POLICY_EXEMPT_FIELDS = ("real", "paths", "context", "reason", "approver")


def load_policy_exemptions(policy_path):
    """WP-P4 rule 4: the builder policy's scan_exemptions, read as data.  Same shape as the name
    table's scan.exemptions (real / paths / context / reason / approver), but here the exemption
    must be bound to a literal: a file-path glob AND a context regex are both required.  Every
    release is listed in the report ('Exemption usage' and the exempted hit list), never silent."""
    if not policy_path:
        return []
    doc = _load_yaml(policy_path)
    items = (doc or {}).get("scan_exemptions") if isinstance(doc, dict) else None
    items = items or []
    if not isinstance(items, list):
        raise UsageError("policy scan_exemptions must be a list")
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise UsageError(f"policy scan_exemptions #{i}: not a mapping")
        missing = [k for k in POLICY_EXEMPT_FIELDS if not it.get(k)]
        if missing:
            raise UsageError(f"policy scan_exemptions #{i}: missing field(s) {missing} "
                             f"(an exemption must be bound to a path and a context pattern)")
        try:
            rx = re.compile(it["context"])
        except re.error as ex:
            raise UsageError(f"policy scan_exemptions #{i}: bad context regex: {ex}")
        paths = it["paths"] if isinstance(it["paths"], list) else [it["paths"]]
        out.append({"i": f"P{i}", "name": str(it["real"]), "paths": [str(x) for x in paths], "rx": rx,
                    "reason": it["reason"], "approver": it["approver"], "used": 0,
                    "context": it["context"], "src": "policy"})
    return out


_STAT_LINE_COMMENT = re.compile(r"(?:^|\s)#")
_STAT_PLAIN = re.compile(r"\d+(?:\.\d+)?")
_STAT_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def _group_thousands(canon):
    ip, dot, fp = canon.partition(".")
    return "{:,}".format(int(ip)) + dot + fp


def load_stats(path):
    """WP-P4 rule 1: one real statistic per line ('#' comments; thousands separators allowed).
    Only values with >= STAT_MIN_DIGITS integer digits or a thousands separator are kept (a lower bar
    would match ordinary numbers thousands of times).  An empty list disables the rule (returns None)."""
    if not path:
        return None
    if not os.path.isfile(path):
        raise UsageError(f"stats file not found: {path}")
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("utf-16")
    forms, orig, dropped = {}, {}, 0
    for n, ln in enumerate(text.splitlines(), 1):
        v = _STAT_LINE_COMMENT.split(ln, 1)[0].strip()
        if not v:
            continue
        if not (_STAT_PLAIN.fullmatch(v) or _STAT_GROUPED.fullmatch(v)):
            raise UsageError(f"stats file line {n}: not a plain or thousands-grouped number")
        canon = v.replace(",", "")
        if "," not in v and len(canon.partition(".")[0]) < STAT_MIN_DIGITS:
            dropped += 1
            continue
        orig.setdefault(canon, v)
        for form in {v, canon, _group_thousands(canon)}:
            forms.setdefault(form, canon)
    if not forms:
        return None
    alts = sorted(forms, key=lambda x: (-len(x), x))
    rx = re.compile(r"(?<!\d)(?<!\d\.)(?:" + "|".join(re.escape(x) for x in alts) + r")(?!\d)(?!\.\d)")
    return {"rx": rx, "forms": forms, "orig": orig, "path": path, "count": len(set(forms.values())),
            "dropped": dropped}


def exempted(exempts, name, relpath, line_text, ls, le):
    for ex in exempts:
        if not exempt_name_matches(ex["name"], name):
            continue
        if not any(fnmatch.fnmatchcase(relpath, p) or fnmatch.fnmatchcase(os.path.basename(relpath), p)
                   for p in ex["paths"]):
            continue
        for m in ex["rx"].finditer(line_text):
            if "t" in ex["rx"].groupindex and m.group("t") is not None:
                a, b = m.span("t")
            else:
                a, b = m.span()
            if a <= ls and le <= b:
                ex["used"] += 1
                return ex
    return None


# ---------------------------------------------------------------------------
# Tree scan
# ---------------------------------------------------------------------------
@dataclass
class Hit:
    kind: str       # name | number | generic
    key: str        # the real form / number / pattern name
    file: str
    line: int       # 0 = path, -1 = binary
    view: str
    before: str
    match: str
    after: str
    note: str = ""
    token: str = ""     # round 3: the whole identifier (embedded / mesprefix classes)


def _clip(t):
    return t.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t").replace("|", "\\|").replace("`", "'")


@dataclass
class Result:
    hits: list = field(default_factory=list)
    hash_skips: list = field(default_factory=list)
    hash_seen: set = field(default_factory=set)
    ambiguous_seen: dict = field(default_factory=lambda: defaultdict(int))
    ambiguous_samples: list = field(default_factory=list)
    masked: dict = field(default_factory=lambda: defaultdict(int))
    exempt_hits: list = field(default_factory=list)
    unmapped: list = field(default_factory=list)
    unscanned: list = field(default_factory=list)
    shape_skips: dict = field(default_factory=lambda: defaultdict(int))
    dedupe: set = field(default_factory=set)
    files: int = 0
    binaries: int = 0
    members: int = 0
    decodings: dict = field(default_factory=lambda: defaultdict(int))
    # round 3
    context_rejects: dict = field(default_factory=lambda: defaultdict(int))   # (rule, key) -> n
    context_samples: list = field(default_factory=list)
    embed_skips: dict = field(default_factory=lambda: defaultdict(int))       # (word, token, why) -> n
    workdir_bare: dict = field(default_factory=lambda: defaultdict(int))      # folder text -> n
    mes_occ: list = field(default_factory=list)
    skipped_binaries: list = field(default_factory=list)                     # (path, reason, size)
    syn_values: dict = field(default_factory=lambda: defaultdict(int))        # (label, status, value) -> n
    mes_census: list = field(default_factory=list)
    # WP-P4
    mes_alt: list = field(default_factory=list)             # alternative-spelling occurrences of real MES prefixes
    mes_alt_skipped: int = 0                                # lower-case form without census/SQL corroboration
    short_noctx: int = 0                                    # unpadded short forms with no EID context on the line
    short_ambig: int = 0


def _note_masked(res, key, pub, relpath, line, always):
    # the primary raw view counts every occurrence; other views/decodings only add
    # occurrences on lines the raw view did not already account for
    k = ("masked", key, pub, relpath, line)
    if not always and k in res.dedupe:
        return
    res.dedupe.add(k)
    res.masked[(key, pub)] += 1


def _note_ambiguous(res, key, relpath, line, text, s, e, always=True):
    k = ("ambiguous", key, relpath, line)
    if not always and k in res.dedupe:
        return
    res.dedupe.add(k)
    res.ambiguous_seen[key] += 1
    if len(res.ambiguous_samples) < AMBIG_SAMPLES:
        res.ambiguous_samples.append((key, relpath, line, _clip(text[max(0, s - CTX):s]), _clip(text[s:e]),
                                      _clip(text[e:e + CTX])))


def _name_note(nf):
    n = ",".join(sorted(nf.cats))
    if nf.derived:
        n += f" [derived {nf.derived} of '{nf.origin}']"
    if nf.kind in ("flex", "phone"):
        n += f" [{nf.kind}]"
    if nf.table_scan_false:
        n += " [table: scan:false]"
    return n


def scan_text(text, relpath, tables, masker, exempts, res, is_path=False, seen=None, enc_label="", primary=True):
    if seen is None:
        seen = set()
    ident_keys = set()
    for view, vt, scope, lmap in views_of(text):
        label = enc_label + view
        always = primary and view == "raw"
        li = lmap or LineIndex(vt)
        win = lmap is not None
        lc = vt.lower()
        if view == "raw" and primary:
            ident_keys = scan_round3(vt, lc, relpath, tables, masker, exempts, res, is_path, seen, label, li)
        if "names" in scope or "multi" in scope:
            for nf, s, e, mb in find_name_hits(vt, tables, masker, multi_only="names" not in scope, lc=lc):
                if nf.text in tables.ambiguous_names:
                    _note_ambiguous(res, "name:" + nf.text, relpath, 0 if is_path else li.line(s), vt, s, e, always)
                    continue
                if mb:
                    _note_masked(res, nf.text, mb, relpath, 0 if is_path else li.line(s), always)
                    continue
                if (0 if is_path else li.line(s), nf.text.lower()) in ident_keys:
                    continue          # reported once, in the whole-identifier class
                rule = context_reject(nf, vt, s, e)
                if rule:
                    _note_context(res, rule, nf.text, relpath, 0 if is_path else li.line(s), vt, s, e, always)
                    continue
                _emit(res, "name", nf.text, vt, s, e, relpath, label, seen, exempts, is_path,
                      _name_note(nf), li, always, win)
        if "numbers" in scope:
            for val, s, e, hx, mb in find_number_hits(vt, tables, masker, lc=lc):
                if synthetic_class(tables.synthetic, _norm_shape(val), tables.numbers.get(val),
                                   tables.group_widths) == "placeholder":
                    # v9 (WP-N9 §1): a value the policy declares as a placeholder is not a real value, even
                    # when the private map also holds it as a key (conservative submission) -- the builder keeps
                    # placeholders verbatim, so all three scans subtract them
                    if always:
                        res.syn_values[("number:" + val, "placeholder", val)] += 1
                    continue
                if val in tables.ambiguous_numbers:
                    _note_ambiguous(res, "number:" + val, relpath, 0 if is_path else li.line(s), vt, s, e, always)
                    continue
                if mb:
                    _note_masked(res, val, mb, relpath, 0 if is_path else li.line(s), always)
                    continue
                if hx:
                    ln = 0 if is_path else li.line(s)
                    hk = (val, relpath, ln, hx.lower())
                    if hk not in res.hash_seen:
                        res.hash_seen.add(hk)
                        res.hash_skips.append((val, relpath, ln, hx, label))
                    continue
                _emit(res, "number", val, vt, s, e, relpath, label, seen, exempts, is_path,
                      tables.numbers.get(val, ""), li, always, win)
        if "numbers" in scope and tables.shapes:
            for grp, s, e, st in find_shape_hits(vt, tables):
                if st != "hit":
                    res.shape_skips[(grp, st)] += 1
                    if st in ("synthetic", "placeholder") and always:
                        res.syn_values[("shape:" + grp, st, vt[s:e])] += 1
                    continue
                mb = masker.masked(lc, s, e)
                if mb:
                    _note_masked(res, "shape:" + grp, mb, relpath, 0 if is_path else li.line(s), always)
                    continue
                _emit(res, "shape", "shape:" + grp, vt, s, e, relpath, label, seen, exempts, is_path,
                      "shape of map '%s', value not in the private map" % grp, li, always, win)
        if "tregex" in scope and tables.tregex:
            for nf, s, e, st, v in find_tregex_hits(vt, tables):
                if st in ("dup-number", "ignore"):
                    continue
                if st in ("synthetic", "placeholder"):
                    if always:
                        res.syn_values[(nf.text, st, str(v))] += 1
                    continue
                if st == "fake":
                    _note_masked(res, nf.text, "private-map fake value", relpath, 0 if is_path else li.line(s), always)
                    continue
                if st == "ambiguous":
                    _note_ambiguous(res, "regex:" + nf.text, relpath, 0 if is_path else li.line(s), vt, s, e, always)
                    continue
                if st == "unmapped":
                    uk = (nf.text, relpath, 0 if is_path else li.line(s), v)
                    if uk not in seen:
                        seen.add(uk)
                        res.unmapped.append(uk)
                    continue
                mb = masker.masked(lc, s, e)
                if mb:
                    _note_masked(res, nf.text, mb, relpath, 0 if is_path else li.line(s), always)
                    continue
                _emit(res, "name", nf.text, vt, s, e, relpath, label, seen, exempts, is_path,
                      ",".join(sorted(nf.cats)) + " [table regex]", li, always, win)
        if "generic" in scope:
            for pname, s, e in find_generic_hits(vt, tables):
                _emit(res, "generic", pname, vt, s, e, relpath, label, seen, exempts, is_path, "", li, always, win)


def _emit(res, kind, key, text, s, e, relpath, view, seen, exempts, is_path, note, li, always, win, token=""):
    ln = 0 if is_path else li.line(s)
    dkey = (kind, key.lower(), ln)
    if not always and dkey in seen:
        return
    seen.add(dkey)
    if win:
        ls, le = max(0, s - WIN), min(len(text), e + WIN)
    else:
        ls = text.rfind("\n", 0, s) + 1
        le = text.find("\n", e)
        le = len(text) if le == -1 else le
    ex = exempted(exempts, key, relpath, text[ls:le], s - ls, e - ls)
    if ex is None and kind == "number" and note:
        # R-P17 X3: an exemption named 'real-value:<map group>' (bound to paths + context) also
        # releases a mapped real value of that group, same as the builder's two scans
        ex = exempted(exempts, "real-value:" + note, relpath, text[ls:le], s - ls, e - ls)
    h = Hit(kind, key, relpath, ln, view,
            _clip(text[max(0, s - CTX):s]), _clip(text[s:e]), _clip(text[e:e + CTX]), note, token)
    if ex:
        res.exempt_hits.append((ex["i"], h))
    else:
        res.hits.append(h)


def _note_context(res, rule, key, relpath, line, text, s, e, always):
    k = ("context", rule, key, relpath, line)
    if not always and k in res.dedupe:
        return
    res.dedupe.add(k)
    res.context_rejects[(rule, key)] += 1
    if sum(1 for r, *_ in res.context_samples if r == rule) < 5:
        res.context_samples.append((rule, key, relpath, line, _clip(text[max(0, s - CTX):s]), _clip(text[s:e]),
                                    _clip(text[e:e + CTX])))


def scan_round3(vt, lc, relpath, tables, masker, exempts, res, is_path, seen, label, li):
    """Items 1-6 on the primary raw view.  Returns (line, word) keys reported as whole
    identifiers (item 4) so the plain name scan does not report the same occurrence twice."""
    spans, keys = set(), set()
    for origin, s, e, v in find_ident_hits(vt, tables):
        spans.add((s, e))
        keys.add((0 if is_path else li.line(s), v.lower()))
        _emit(res, "ident", "ident:" + v.lower(), vt, s, e, relpath, label, seen, exempts, is_path,
              f"binding identifier equals the short real '{origin}'", li, True, False, token=v)
    for nf, s, e in find_loose_hits(vt, tables):
        mb = masker.masked(lc, s, e)
        if mb:
            _note_masked(res, nf.text, mb, relpath, 0 if is_path else li.line(s), True)
            continue
        _emit(res, "name", nf.text, vt, s, e, relpath, label, seen, exempts, is_path,
              _name_note(nf) + " [separator variant]", li, True, False)
    for w, s, e, tok, st in find_embedded_hits(vt, tables, masker, lc):
        if (s, e) in spans:
            continue
        if st != "hit":
            res.embed_skips[(w, tok, st)] += 1
            continue
        _emit(res, "embedded", "embedded:" + w, vt, s, e, relpath, label, seen, exempts, is_path,
              "short real inside an identifier: " + ", ".join(tables.embed_words.get(w, [])), li, True, False,
              token=tok)
    short_set = _short_texts(tables)
    for nf, s, e in find_site_hits(vt, tables):
        if masker.masked(lc, s, e) or (s, e) in spans or vt[s:e] in short_set:
            # (a site code that the table also lists as a short real is reported as a name hit)
            continue
        _emit(res, "sitecode", "sitecode:" + nf.origin, vt, s, e, relpath, label, seen, exempts, is_path,
              ",".join(sorted(nf.cats)), li, True, False)
    hits, bare = find_workdir_hits(vt)
    for t, s, e in hits:
        _emit(res, "workdir", "workdir", vt, s, e, relpath, label, seen, exempts, is_path,
              "numbered work-directory folder in a path", li, True, False, token=t)
    for t, s, e in bare:
        res.workdir_bare[t] += 1
    for m in MES_PREFIX_RX.finditer(vt):
        s, e = m.span()
        ls = vt.rfind("\n", 0, s) + 1
        le = vt.find("\n", e)
        le = len(vt) if le == -1 else le
        res.mes_occ.append((m.group(1), m.group(0), relpath, 0 if is_path else li.line(s), label,
                            vt[ls:le] if le - ls <= 2000 else vt[max(ls, s - 300):min(le, e + 300)],
                            s - (ls if le - ls <= 2000 else max(ls, s - 300)),
                            _clip(vt[max(0, s - CTX):s]), _clip(vt[s:e]), _clip(vt[e:e + CTX]),
                            bool(masker.masked(lc, s, e))))
    if not is_path:
        scan_p4(vt, lc, relpath, tables, masker, exempts, res, seen, label, li)
    return keys


def _line_bounds(vt, s, e):
    ls = vt.rfind("\n", 0, s) + 1
    le = vt.find("\n", e)
    le = len(vt) if le == -1 else le
    return ls, le


def scan_p4(vt, lc, relpath, tables, masker, exempts, res, seen, label, li):
    """WP-P4 rules 1-3 on the primary raw view of a text body."""
    st = tables.stats
    if st:
        for m in st["rx"].finditer(vt):
            s, e = m.span()
            val = st["forms"].get(m.group(0))
            if val is None:
                continue
            hx = hex_shape(vt, s, e)
            if hx:
                ln = li.line(s)
                hk = (val, relpath, ln, hx.lower())
                if hk not in res.hash_seen:
                    res.hash_seen.add(hk)
                    res.hash_skips.append((val, relpath, ln, hx, label))
                continue
            form = ("original" if m.group(0) == st["orig"].get(val)
                    else ("thousands-grouped" if "," in m.group(0) else "thousands-stripped"))
            _emit(res, "real_stat", "real_stat:" + val, vt, s, e, relpath, label, seen, exempts, False,
                  "listed real statistic (%s form)" % form, li, True, False)
    if tables.short_rx:
        spans = None
        for m in tables.short_rx.finditer(vt):
            s, e = m.span()
            short = m.group(1)
            if spans is None:
                spans = [(a, b) for _, a, b, _, _ in find_number_hits(vt, tables, masker, lc)]
            if any(a < e and s < b for a, b in spans):
                continue                       # the full padded key: the number scan owns it
            if masker.masked(lc, s, e):
                _note_masked(res, "short:" + short, masker.masked(lc, s, e), relpath, li.line(s), True)
                continue
            hx = hex_shape(vt, s, e)
            if hx:                             # inside a >= HEXRUN_MIN hex run: hash-shaped, skipped and listed (as rule 1)
                pkey = tables.short_map[short][0]
                ln = li.line(s)
                hk = (pkey, relpath, ln, hx.lower())
                if hk not in res.hash_seen:
                    res.hash_seen.add(hk)
                    res.hash_skips.append((pkey, relpath, ln, hx, label))
                continue
            if short in tables.short_fake:
                res.short_ambig += 1
                continue
            ls, le = _line_bounds(vt, s, e)
            if not (EID_CONTEXT_RX.search(vt[ls:le]) or any(ls <= a and b <= le for a, b in spans)):
                res.short_noctx += 1
                continue
            _emit(res, "number", tables.short_map[short][0], vt, s, e, relpath, label, seen, exempts, False,
                  "unpadded short form of a zero-padded map key (EID context on the line)", li, True, False)
    if tables.mes_alt_rx:
        found = []
        for m in tables.mes_alt_rx.finditer(vt):
            form = "snake" if m.group("sn") else ("alias-dot" if m.group("dt") else "lower")
            s = m.start("p")
            e = s
            while e < len(vt) and (vt[e].isalnum() or vt[e] == "_") and e - s < 80:
                e += 1
            found.append((m.group("p"), form, s, max(e, m.end())))
        for m in tables.mes_alias_rx.finditer(vt):
            found.append((m.group("p"), "sql-alias", m.start("p"), m.end("p")))
        for pfx, form, s, e in found:
            ls, le = _line_bounds(vt, s, e)
            if le - ls <= 2000:
                lt, off = vt[ls:le], s - ls
            else:
                lo = max(ls, s - 300)
                lt, off = vt[lo:min(le, e + 300)], s - lo
            res.mes_alt.append((pfx, form, vt[s:e], relpath, li.line(s), label, lt, off,
                                _clip(vt[max(0, s - CTX):s]), _clip(vt[s:e]), _clip(vt[e:e + CTX]),
                                bool(masker.masked(lc, s, e))))


def finish_mes_prefixes(res, tables, exempts):
    """Item 5, second pass: a prefix is a hit when the table names it as a real MES prefix
    or when the tree contains '<prefix>PK' (schema primary-key convention); prefixes of the
    public replacement values never are.  Returns the census rows."""
    census = defaultdict(lambda: {"n": 0, "ids": defaultdict(int)})
    for pfx, ident, *_ in res.mes_occ:
        census[pfx]["n"] += 1
        census[pfx]["ids"][ident] += 1
    pk = {p for p, c in census.items() if any(p + suf in c["ids"] for suf in MES_PK_SUFFIXES)}
    hit_pfx = (tables.mes_known | pk) - tables.mes_replaced
    rows = []
    for p, c in census.items():
        if p in tables.mes_replaced:
            cls = "replaced (public prefix)"
        elif p in tables.mes_known:
            cls = "HIT: real MES prefix (table)"
        elif p in pk:
            cls = "HIT: schema-shaped (<pfx>PK present)"
        else:
            cls = "other (census only)"
        rows.append((p, c["n"], len(c["ids"]), cls, sorted(c["ids"].items(), key=lambda kv: -kv[1])[:6]))
    for pfx, ident, relpath, ln, label, line_text, off, b, mt, a, masked in res.mes_occ:
        if pfx not in hit_pfx or masked:
            continue
        key = "mesprefix:" + pfx
        ex = exempted(exempts, key, relpath, line_text, off, off + len(ident))
        h = Hit("mesprefix", key, relpath, ln, label, b, mt, a, "MES vendor-schema column prefix", ident)
        if ex:
            res.exempt_hits.append((ex["i"], h))
        else:
            res.hits.append(h)
    res.mes_occ = []
    camel_lc = {ident.lower() for c in census.values() for ident in c["ids"]}
    for pfx, form, ident, relpath, ln, label, line_text, off, b, mt, a, masked in res.mes_alt:
        if masked:
            continue
        if form == "lower" and not (ident.lower() in camel_lc or SQL_CONTEXT_RX.search(line_text)):
            res.mes_alt_skipped += 1
            continue
        key = "mesprefix:" + pfx
        ex = exempted(exempts, key, relpath, line_text, off, off + len(ident))
        h = Hit("mesprefix", key, relpath, ln, label, b, mt, a, "MES vendor-schema prefix, %s form" % form, ident)
        if ex:
            res.exempt_hits.append((ex["i"], h))
        else:
            res.hits.append(h)
    res.mes_alt = []
    return sorted(rows, key=lambda r: (not r[3].startswith("HIT"), -r[1], r[0]))


# --- binary -----------------------------------------------------------------
def _bin_patterns(tables):
    if tables._bin is not None:
        return tables._bin
    short = [nf for nf in tables.names if nf.short and nf.kind == "literal"]
    encs = {
        "ascii/utf-8": (lambda s: s.encode("utf-8"), rb"(?<![A-Za-z])", rb"(?:(?![A-Za-z])|(?=e?s(?![A-Za-z])))",
                        rb"(?<![0-9])", rb"(?![0-9])"),
        "utf-16le": (lambda s: s.encode("utf-16-le"), rb"(?<![A-Za-z]\x00)",
                     rb"(?:(?![A-Za-z]\x00)|(?=(?:e\x00)?s\x00(?![A-Za-z]\x00)))",
                     rb"(?<![0-9]\x00)", rb"(?![0-9]\x00)"),
        "utf-16be": (lambda s: s.encode("utf-16-be"), rb"(?<!\x00[A-Za-z])",
                     rb"(?:(?!\x00[A-Za-z])|(?=(?:\x00e)?\x00s(?!\x00[A-Za-z])))",
                     rb"(?<!\x00[0-9])", rb"(?!\x00[0-9])"),
    }
    out = {}
    for enc, (fn, sb, sa, nb, na) in encs.items():
        smap = {}
        for nf in short:
            smap.setdefault(fn(nf.text), nf)
        nmap = {}
        for k in tables.numbers:
            for v in {k, k.lower()}:
                nmap.setdefault(fn(v), k)

        def pat(keys):
            return trie_pattern(["\\x%02x" % b for b in bk] for bk in keys).encode("latin-1")
        srx = re.compile(sb + b"(" + pat(smap) + b")" + sa, re.S) if smap else None
        nrx = re.compile(nb + b"(" + pat(nmap) + b")" + na, re.S) if nmap else None
        out[enc] = (srx, smap, nrx, nmap)
    tables._bin = out
    return out


def scan_binary(data, relpath, tables, res):
    low = data.lower()
    for nf in tables.names:
        if nf.short or nf.kind != "literal":
            continue
        t = nf.text.lower()
        forms = [("ascii/utf-8", t.encode("utf-8")),
                 ("utf-16le", t.encode("utf-16-le")),
                 ("utf-16be", t.encode("utf-16-be"))]
        for fname, b in forms:
            i = low.find(b)
            while i != -1:
                if nf.text not in tables.ambiguous_names:
                    res.hits.append(Hit("name", nf.text, relpath, -1, "binary:" + fname,
                                        f"offset {i}", nf.text, "", _name_note(nf)))
                i = low.find(b, i + 1)
    for enc, (srx, smap, nrx, nmap) in _bin_patterns(tables).items():
        if srx is not None:
            for m in srx.finditer(data):
                nf = smap.get(m.group(1))
                if nf is None:
                    continue
                if nf.text in tables.ambiguous_names:
                    res.ambiguous_seen["name:" + nf.text] += 1
                    continue
                res.hits.append(Hit("name", nf.text, relpath, -1, "binary:" + enc,
                                    f"offset {m.start(1)}", nf.text, "", _name_note(nf)))
        if nrx is not None:
            for m in nrx.finditer(data):
                k = nmap.get(m.group(1))
                if k is None:
                    continue
                if k in tables.ambiguous_numbers:
                    res.ambiguous_seen["number:" + k] += 1
                    continue
                res.hits.append(Hit("number", k, relpath, -1, "binary:" + enc,
                                    f"offset {m.start(1)}", k, "", tables.numbers.get(k, "")))


# --- containers ---------------------------------------------------------------
def _container_kind(data):
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if data[:2] == b"\x1f\x8b":
        return "gzip"
    return None


def _gunzip(data, limit):
    out = bytearray()
    buf = data
    while buf:
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out += d.decompress(buf, limit + 1 - len(out))
        if len(out) > limit:
            return None
        if d.unconsumed_tail:
            return None
        buf = d.unused_data
        if buf and buf[:2] != b"\x1f\x8b":
            break
    return bytes(out)


def scan_blob(data, relpath, tables, masker, exempts, res, depth=0):
    kind = _container_kind(data)
    if kind == "zip":
        if depth >= MAX_DEPTH:
            res.unscanned.append((relpath, f"archive nested deeper than {MAX_DEPTH} levels"))
            res.binaries += 1
            scan_binary(data, relpath, tables, res)
            return
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            infos = zf.infolist()
        except (zipfile.BadZipFile, OSError, ValueError) as ex:
            res.unscanned.append((relpath, f"zip not readable ({ex}); scanned as binary"))
            res.binaries += 1
            scan_binary(data, relpath, tables, res)
            return
        for zi in infos:
            if zi.is_dir():
                continue
            mrel = relpath + "!/" + zi.filename
            scan_text(zi.filename, mrel, tables, masker, exempts, res, is_path=True)
            if zi.flag_bits & 0x1:
                res.unscanned.append((mrel, "encrypted member"))
                continue
            if zi.file_size > MAX_UNPACK:
                res.unscanned.append((mrel, f"member larger than {MAX_UNPACK} bytes ({zi.file_size})"))
                continue
            try:
                with zf.open(zi) as fh:
                    mdata = fh.read(MAX_UNPACK + 1)
            except Exception as ex:  # corrupt / unsupported compression
                res.unscanned.append((mrel, f"member not readable ({ex})"))
                continue
            if len(mdata) > MAX_UNPACK:
                res.unscanned.append((mrel, f"member larger than {MAX_UNPACK} bytes"))
                continue
            res.members += 1
            why = binary_skip_reason(zi.filename, mdata[:16])
            if why:
                res.skipped_binaries.append((mrel, why, len(mdata)))
                continue
            scan_blob(mdata, mrel, tables, masker, exempts, res, depth + 1)
        return
    if kind == "gzip":
        if depth >= MAX_DEPTH:
            res.unscanned.append((relpath, f"archive nested deeper than {MAX_DEPTH} levels"))
            res.binaries += 1
            scan_binary(data, relpath, tables, res)
            return
        try:
            out = _gunzip(data, MAX_UNPACK)
        except zlib.error as ex:
            res.unscanned.append((relpath, f"gzip not readable ({ex}); scanned as binary"))
            res.binaries += 1
            scan_binary(data, relpath, tables, res)
            return
        if out is None:
            res.unscanned.append((relpath, f"gunzip output larger than {MAX_UNPACK} bytes"))
            return
        res.members += 1
        scan_blob(out, relpath + "!/(gunzip)", tables, masker, exempts, res, depth + 1)
        return
    cands = decode_candidates(data)
    if not cands:
        res.binaries += 1
        scan_binary(data, relpath, tables, res)
        return
    seen = set()
    for i, (enc, t) in enumerate(cands):
        res.decodings[enc] += 1
        lab = "" if enc == "utf-8" else enc + ":"
        scan_text(t, relpath, tables, masker, exempts, res, seen=seen, enc_label=lab, primary=(i == 0))


# --- .git ---------------------------------------------------------------------
GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat"}


def git_metadata(gitdir):
    """Authors, e-mails, messages and ref names via read-only git plumbing. Returns (text, error)."""
    env = dict(os.environ, **GIT_ENV)
    base = ["git", "--no-pager", "--git-dir=" + gitdir, "-c", "safe.directory=*", "-c", "core.fsmonitor=false"]
    cmds = [
        ["log", "--all", "--no-color",
         "--format=commit %H%nauthor %an <%ae>%ncommitter %cn <%ce>%n%B%n--end--"],
        ["for-each-ref",
         "--format=ref %(refname)%ntagger %(taggername) %(taggeremail)%n%(contents)%n--end--"],
    ]
    out = []
    for c in cmds:
        try:
            p = subprocess.run(base + c, capture_output=True, env=env, timeout=300, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as ex:
            return None, f"git not runnable ({ex})"
        if p.returncode != 0:
            err = p.stderr.decode("utf-8", "replace").strip().splitlines()
            return None, f"git {c[0]} failed: {err[0] if err else p.returncode}"
        out.append(p.stdout.decode("utf-8", "replace"))
    return "\n".join(out), None


def long_path(p):
    """Item 10: Windows paths longer than MAX_PATH (260) need the extended-length prefix."""
    p = os.path.abspath(p)
    if os.name != "nt" or p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def binary_skip_reason(name, head):
    """Item 9: fonts / images / media are never text-matched; returns a reason or None."""
    ext = os.path.splitext(name)[1].lower()
    if ext in BINARY_SKIP_EXT:
        return "extension " + ext
    for magic, label in BINARY_MAGIC:
        if head.startswith(magic):
            if label and label.startswith("riff") and head[8:12] not in (b"WEBP", b"WAVE", b"AVI "):
                continue
            return "magic " + label
    return None


def scan_tree(tree, tables, exempts):
    masker = Masker(tables.public_lc)
    res = Result()
    gitdirs = []
    tree_fs = long_path(tree)
    for root, dirs, files in os.walk(tree_fs):
        rel_root = os.path.relpath(root, tree_fs).replace(os.sep, "/")
        in_git = ".git" in rel_root.split("/")
        # inside .git, object storage is read through git plumbing, not as files
        dirs[:] = sorted(d for d in dirs if not (in_git and d == "objects" and rel_root.endswith(".git")))
        for d in dirs:
            rel = os.path.relpath(os.path.join(root, d), tree_fs).replace(os.sep, "/")
            if d == ".git":
                gitdirs.append((os.path.join(os.path.abspath(tree), rel.replace("/", os.sep)), rel))
            scan_text(rel + "/", rel + "/", tables, masker, exempts, res, is_path=True)
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, tree_fs).replace(os.sep, "/")
            scan_text(rel, rel, tables, masker, exempts, res, is_path=True)
            with open(fp, "rb") as f:   # read-only
                data = f.read()
            res.files += 1
            why = binary_skip_reason(fn, data[:16])
            if why:
                res.skipped_binaries.append((rel, why, len(data)))
                continue
            scan_blob(data, rel, tables, masker, exempts, res)
    for gd, rel in gitdirs:
        text, err = git_metadata(gd)
        if err:
            res.unscanned.append((rel + "!/git-metadata", err))
            continue
        scan_text(text, rel + "!/git-metadata", tables, masker, exempts, res)
    res.mes_census = finish_mes_prefixes(res, tables, exempts)
    return res


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def write_report(path, res, tables, exempts, args, max_per_key=0):
    by_key = defaultdict(list)
    for h in res.hits:
        by_key[(h.kind, h.key)].append(h)
    amb_total = sum(res.ambiguous_seen.values())
    L = []
    A = L.append
    warn = []
    if amb_total:
        warn.append(f"{amb_total} ambiguous occurrence(s) (real value that is also a public value) "
                    f"- review the Ambiguous section")
    if res.unscanned:
        warn.append(f"{len(res.unscanned)} item(s) not scanned - see Not scanned")
    if tables.regex_errors:
        warn.append(f"{len(tables.regex_errors)} table regex entr(y/ies) failed to compile")
    if res.unmapped:
        warn.append(f"{len(res.unmapped)} table-regex match(es) with a value outside the private map")
    if warn:
        A("> WARNING: " + "; ".join(warn) + ".")
        A("")
    A("# Independent verification report")
    A("")
    A("> PRIVATE. This report contains real names. Never publish or commit it to a public repo.")
    A("")
    A(f"- tree: `{args.tree}`")
    A(f"- names: `{args.names}`  maps: `{args.maps}`  exempt: `{args.exempt or '(none)'}`")
    A(f"- policy: `{getattr(args, 'policy', None) or '(none)'}`  synthetic: "
      f"`{getattr(args, 'synthetic', None) or '(none)'}`  table scan.exemptions: "
      f"{'applied' if getattr(args, 'names_exemptions', False) else 'not applied'}"
      f"{' (repo ' + args.repo + ')' if getattr(args, 'repo', None) else ''}")
    st = tables.stats
    A(f"- real-statistics list: `{getattr(args, 'stats', None) or '(none)'}` "
      + (f"({st['count']} values active, {st['dropped']} dropped for < {STAT_MIN_DIGITS} digits)" if st
         else "(rule off)")
      + f"; policy scan_exemptions loaded: {sum(1 for e in exempts if e.get('src') == 'policy')}")
    A(f"- unpadded short forms: {res.short_noctx} without EID/map-key context on the line (not hits), "
      f"{res.short_ambig} ambiguous (also a public value); lower-case MES prefix forms without corroboration: "
      f"{res.mes_alt_skipped}")
    A(f"- files scanned: {res.files} (binary: {res.binaries}; archive members: {res.members}; "
      f"decodings: {dict(res.decodings)})")
    A(f"- real name forms: {len(tables.names)}  table regex entries: {len(tables.tregex)} "
      f"(failed: {len(tables.regex_errors)})  mapped real values: {len(tables.numbers)}  "
      f"public values: {len(tables.public)}")
    A(f"- table entries skipped: {dict(tables.skipped)}")
    A("")
    A("## Summary")
    A("")
    A("| section | count |")
    A("|---|---|")
    A(f"| name hits | {sum(1 for h in res.hits if h.kind == 'name')} |")
    A(f"| number hits | {sum(1 for h in res.hits if h.kind == 'number')} |")
    A(f"| generic pattern hits | {sum(1 for h in res.hits if h.kind == 'generic')} |")
    A(f"| shape hits | {sum(1 for h in res.hits if h.kind == 'shape')} |")
    for kind, label in ROUND3_SECTIONS:
        A(f"| {label} | {sum(1 for h in res.hits if h.kind == kind)} |")
    A(f"| context-rejected (structural, item 12) | {sum(res.context_rejects.values())} |")
    A(f"| embedded: excluded English word / masked | "
      f"{sum(n for (w, t, why), n in res.embed_skips.items() if why.startswith('excluded'))} / "
      f"{sum(n for (w, t, why), n in res.embed_skips.items() if why.startswith('masked'))} |")
    A(f"| synthetic / placeholder values (not hits) | "
      f"{sum(n for (l, st, v), n in res.syn_values.items() if st == 'synthetic')} / "
      f"{sum(n for (l, st, v), n in res.syn_values.items() if st == 'placeholder')} |")
    A(f"| skipped binary files (not text-matched) | {len(res.skipped_binaries)} |")
    A(f"| hash-shaped skipped | {len(res.hash_skips)} |")
    A(f"| ambiguous occurrences (total) | {amb_total} |")
    A(f"| masked by public value | {sum(res.masked.values())} |")
    A(f"| exempted | {len(res.exempt_hits)} |")
    A(f"| table-regex unmapped values (warning) | {len(res.unmapped)} |")
    A(f"| not scanned (warning) | {len(res.unscanned)} |")
    A(f"| table regex compile errors | {len(tables.regex_errors)} |")
    A("")
    A("### Hits before / after exemptions")
    A("")
    A("| kind | before exemptions | exempted | after exemptions |")
    A("|---|---|---|---|")
    ex_by_kind = defaultdict(int)
    for _, h in res.exempt_hits:
        ex_by_kind[h.kind] += 1
    kinds_all = ["name", "number", "generic", "shape"] + [k for k, _ in ROUND3_SECTIONS]
    for kind in kinds_all:
        after = sum(1 for h in res.hits if h.kind == kind)
        A(f"| {kind} | {after + ex_by_kind[kind]} | {ex_by_kind[kind]} | {after} |")
    A(f"| **total** | {len(res.hits) + len(res.exempt_hits)} | {len(res.exempt_hits)} | {len(res.hits)} |")
    A("")
    A(f"Ambiguous samples (first {AMBIG_SAMPLES}):")
    A("")
    A("| key | file | line | context |")
    A("|---|---|---|---|")
    for k, f, ln, b, m, a in res.ambiguous_samples:
        A(f"| `{_clip(k)}` | `{f}` | {ln or 'path'} | `{b}` **`{m}`** `{a}` |")
    if not res.ambiguous_samples:
        A("| - | | | (none) |")
    A("")
    A("### Hits per key")
    A("")
    A("| kind | key | hits | files | views | table category / map |")
    A("|---|---|---|---|---|---|")
    for (kind, key), hs in sorted(by_key.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        A(f"| {kind} | `{_clip(key)}` | {len(hs)} | {len({h.file for h in hs})} | "
          f"{', '.join(sorted({h.view for h in hs}))} | {hs[0].note} |")
    for title, kind in (("## Name hits", "name"), ("## Number hits", "number"), ("## Generic pattern hits", "generic"),
                        ("## Shape hits", "shape")) + tuple(("## " + lab[0].upper() + lab[1:], k)
                                                            for k, lab in ROUND3_SECTIONS):
        A("")
        A(title)
        if kind in ROUND3_DOC:
            A("")
            A(ROUND3_DOC[kind])
        for (knd, key), hs in sorted(by_key.items(), key=lambda kv: kv[0]):
            if knd != kind:
                continue
            A("")
            A(f"### `{_clip(key)}` ({len(hs)})")
            A("")
            if kind in ("embedded", "mesprefix", "workdir", "ident"):
                per_tok = defaultdict(int)
                for h in hs:
                    per_tok[h.token] += 1
                A("| distinct identifier / text | occurrences |")
                A("|---|---|")
                for tok, n in sorted(per_tok.items(), key=lambda kv: (-kv[1], kv[0])):
                    A(f"| `{_clip(tok)}` | {n} |")
                A("")
            A("| file | line | view | context |")
            A("|---|---|---|---|")
            shown = hs if not max_per_key else hs[:max_per_key]
            for h in shown:
                ln = "path" if h.line == 0 else ("bin" if h.line < 0 else h.line)
                A(f"| `{h.file}` | {ln} | {h.view} | `{h.before}` **`{h.match}`** `{h.after}` |")
            if len(shown) < len(hs):
                A(f"| … | | | {len(hs) - len(shown)} more |")
    A("")
    A("## Shape patterns (induced from the private map)")
    A("")
    A(f"synthetic/placeholder list: `{tables.synthetic['path'] if tables.synthetic else '(none)'}`")
    A("")
    A("| map group | real prefixes | public prefixes | overlap (not shaped) | body shapes | hits | fake | known | synthetic / placeholder |")
    A("|---|---|---|---|---|---|---|---|---|")
    for sh in tables.shapes:
        g = sh["group"]
        nh = sum(1 for h in res.hits if h.key == "shape:" + g)
        A(f"| {g} | {', '.join(sh['real_prefixes'])} | {', '.join(sh['public_prefixes'])} | "
          f"{', '.join(sh['overlap']) or '-'} | {', '.join(sh['sigs'][:6])}{' ...' if len(sh['sigs']) > 6 else ''} | "
          f"{nh} | {res.shape_skips.get((g, 'fake'), 0)} | {res.shape_skips.get((g, 'known'), 0)} | "
          f"{res.shape_skips.get((g, 'synthetic'), 0)} / {res.shape_skips.get((g, 'placeholder'), 0)} |")
    if not tables.shapes:
        A("| - | (no prefixed map group) | | | | | | | |")
    A("")
    A("## Hash-shaped skipped (samples)")
    A("")
    A(f"total {len(res.hash_skips)}")
    A("")
    A("| value | file | line | hex run | view |")
    A("|---|---|---|---|---|")
    per = defaultdict(int)
    for v, f, ln, hx, view in res.hash_skips:
        per[v] += 1
        if per[v] <= 3:
            A(f"| {v} | `{f}` | {ln or 'path'} | `{hx}` | {view} |")
    A("")
    A("## Ambiguous (real value is also a public value)")
    A("")
    A(f"total occurrences: {amb_total}")
    A("")
    A("| value | reason | occurrences in tree |")
    A("|---|---|---|")
    for k, why in sorted(tables.ambiguous_names.items()):
        A(f"| `{_clip(k)}` | {why} | {res.ambiguous_seen.get('name:' + k, 0)} |")
    for k, why in sorted(tables.ambiguous_numbers.items()):
        A(f"| `{k}` | {why} | {res.ambiguous_seen.get('number:' + k, 0)} |")
    for k, c in sorted(res.ambiguous_seen.items()):
        if k.startswith("regex:"):
            A(f"| `{_clip(k)}` | table regex value is both real and fake | {c} |")
    A("")
    A("## Table regex: values outside the private map (warning, not counted as hits)")
    A("")
    A("Entries marked `permutation` may legitimately publish values that are not in the map.")
    A("")
    A("| pattern | file | line | value |")
    A("|---|---|---|---|")
    for pat, f, ln, v in res.unmapped[:200]:
        A(f"| `{_clip(pat)}` | `{f}` | {ln or 'path'} | `{_clip(str(v))}` |")
    if len(res.unmapped) > 200:
        A(f"| … | | | {len(res.unmapped) - 200} more |")
    A("")
    A("## Table regex compile errors")
    A("")
    A("| category | pattern | error |")
    A("|---|---|---|")
    for cat, pat, err in tables.regex_errors:
        A(f"| {cat} | `{_clip(pat)}` | {_clip(err)} |")
    if not tables.regex_errors:
        A("| - | (none) | |")
    A("")
    A("## Not scanned (warning, not counted as hits)")
    A("")
    A("| item | reason |")
    A("|---|---|")
    for item, why in res.unscanned:
        A(f"| `{_clip(item)}` | {_clip(why)} |")
    if not res.unscanned:
        A("| - | (none) |")
    A("")
    A("## Masked by public value (top 40)")
    A("")
    A("| real form | public value | count |")
    A("|---|---|---|")
    for (k, p), c in sorted(res.masked.items(), key=lambda kv: -kv[1])[:40]:
        A(f"| `{_clip(k)}` | `{_clip(p)}` | {c} |")
    A("")
    A("## Exemption usage (item 8)")
    A("")
    A(f"Every exemption with the number of hits it actually released.  **REVIEW** marks an exemption "
      f"that released >= {WIDE_EXEMPT} hits or that has no context regex (whole line) with a '*' path: "
      f"check that the exemption itself is justified, not only its reason text.")
    A("")
    A("| # | source | name | path | context | reason | approver | released | share of key | review |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    key_total = defaultdict(int)
    for h in res.hits:
        key_total[_ex_norm(h.key)] += 1
    for _, h in res.exempt_hits:
        key_total[_ex_norm(h.key)] += 1
    for ex in sorted(exempts, key=lambda x: -x["used"]):
        tot = sum(n for k, n in key_total.items() if exempt_name_matches(ex["name"], k))
        share = f"{ex['used']}/{tot}" if tot else "0/0"
        wide = ex["used"] >= WIDE_EXEMPT or (str(ex.get("context", "")).startswith("(whole")
                                            and any(p == "*" for p in ex["paths"]))
        A(f"| {ex['i']} | {ex.get('src', '')} | `{_clip(ex['name'])}` | `{_clip(', '.join(ex['paths']))}` | "
          f"`{_clip(str(ex.get('context', '')))}` | {_clip(str(ex['reason']))} | {_clip(str(ex['approver']))} | "
          f"{ex['used']} | {share} | {'**REVIEW**' if wide else ''} |")
    if not exempts:
        A("| - | | (no exemptions) | | | | | 0 | | |")
    A("")
    for i, h in res.exempt_hits[:200]:
        A(f"- exemption #{i}: `{h.file}`:{h.line} `{h.before}` **`{h.match}`** `{h.after}`")
    if len(res.exempt_hits) > 200:
        A(f"- … {len(res.exempt_hits) - 200} more")
    A("")
    A("## Context-rejected occurrences (item 12, structural false-positive handling)")
    A("")
    A("Known false-positive shapes are not solved by exempting a whole word.  A real-name occurrence is "
      "dropped only when one of these context rules holds at that position (each rule is generic: it "
      "names a context shape, never a word); every drop is counted here with samples:")
    A("")
    for rule, doc in CONTEXT_RULE_DOC.items():
        A(f"- `{rule}`: {doc}")
    A("- (engine) a lower-case form of <= 2 letters + 's' is not treated as a plural; a table "
      "`engine`/`scan` section is never read as a source of public values.")
    A("")
    A("| rule | key | dropped |")
    A("|---|---|---|")
    for (rule, key), n in sorted(res.context_rejects.items(), key=lambda kv: (-kv[1], kv[0])):
        A(f"| {rule} | `{_clip(key)}` | {n} |")
    if not res.context_rejects:
        A("| - | (none) | 0 |")
    A("")
    A("| rule | key | file | line | context |")
    A("|---|---|---|---|---|")
    for rule, key, f, ln, b, m, a in res.context_samples:
        A(f"| {rule} | `{_clip(key)}` | `{f}` | {ln or 'path'} | `{b}` **`{m}`** `{a}` |")
    A("")
    A("## Embedded: dropped occurrences (review the exclusion list)")
    A("")
    A(f"Exclusion list (EMBED_EXCLUDE_WORDS, {len(EMBED_EXCLUDE_WORDS)} literal words): "
      + ", ".join(f"`{w}`" for w in EMBED_EXCLUDE_WORDS))
    A("")
    A(f"Embedded words from the table (categories {', '.join(EMBED_CATEGORIES)} + site codes): "
      f"{len(tables.embed_words)}")
    A("")
    A("| word | identifier | why | occurrences |")
    A("|---|---|---|---|")
    for (w, tok, why), n in sorted(res.embed_skips.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1])):
        A(f"| `{w}` | `{_clip(tok)}` | {_clip(why)} | {n} |")
    if not res.embed_skips:
        A("| - | | (none) | 0 |")
    A("")
    A("## MES schema prefix census (item 5)")
    A("")
    A(f"Every identifier of the shape <3 lower-case letters><Upper...>.  Real prefixes from the table "
      f"(categories {', '.join(MES_PREFIX_CATEGORIES)}): `{', '.join(sorted(tables.mes_known)) or '-'}`; "
      f"replacement prefixes (public, never hits): `{', '.join(sorted(tables.mes_replaced)) or '-'}`.")
    A("")
    A("| prefix | occurrences | distinct identifiers | class | top identifiers |")
    A("|---|---|---|---|---|")
    for p, n, nid, cls, top in res.mes_census:
        A(f"| `{p}` | {n} | {nid} | {cls} | {', '.join(f'`{_clip(i)}`×{c}' for i, c in top)} |")
    if not res.mes_census:
        A("| - | 0 | 0 | (none) | |")
    A("")
    A("## Numbered folder names outside a path context (information, not hits)")
    A("")
    A("| text | occurrences |")
    A("|---|---|")
    for t, n in sorted(res.workdir_bare.items(), key=lambda kv: (-kv[1], kv[0])):
        A(f"| `{_clip(t)}` | {n} |")
    if not res.workdir_bare:
        A("| - | 0 |")
    A("")
    A("## Synthetic / placeholder values (item 7, not counted as hits)")
    A("")
    A(f"sources: `{tables.synthetic['path'] if tables.synthetic else '(none)'}`")
    A("")
    A("| pattern / shape | status | value | occurrences |")
    A("|---|---|---|---|")
    for (lab, st, v), n in sorted(res.syn_values.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[0][2]))[:300]:
        A(f"| `{_clip(lab)}` | {st} | `{_clip(v)}` | {n} |")
    if not res.syn_values:
        A("| - | | (none) | 0 |")
    A("")
    A("## Skipped binary files (item 9, not text-matched; the file NAME is still scanned)")
    A("")
    A("| file | reason | bytes |")
    A("|---|---|---|")
    for f, why, n in res.skipped_binaries:
        A(f"| `{_clip(f)}` | {why} | {n} |")
    if not res.skipped_binaries:
        A("| - | (none) | |")
    A("")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_yaml(p):
    import yaml
    if not os.path.isfile(p):
        raise UsageError(f"file not found: {p}")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _inside(child, parent):
    c = os.path.normcase(os.path.realpath(os.path.abspath(child)))
    p = os.path.normcase(os.path.realpath(os.path.abspath(parent)))
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:
        return False


def run_verify(names, maps, tree, exempt, report, max_per_key=0, quiet=False, synthetic=None,
               policy=None, names_exemptions=False, repo=None, stats=None):
    if not os.path.isdir(long_path(tree)):
        raise UsageError(f"tree is not a directory: {tree}")
    if _inside(report, tree):
        raise UsageError("report path must not be inside --tree")
    names_doc = _load_yaml(names)
    tables = build_tables(names_doc, _load_yaml(maps))
    tables.synthetic = prefixed_placeholders(load_synthetic(synthetic, policy), names_doc)
    tables.stats = load_stats(stats)
    exempts = load_exempt(exempt)
    if names_exemptions:
        exempts += load_names_exemptions(names_doc, repo)
    exempts += load_policy_exemptions(policy)
    res = scan_tree(tree, tables, exempts)

    class _A:
        pass
    a = _A()
    a.tree, a.names, a.maps, a.exempt = tree, names, maps, exempt
    a.policy, a.synthetic, a.names_exemptions, a.repo = policy, synthetic, names_exemptions, repo
    a.stats = stats
    write_report(report, res, tables, exempts, a, max_per_key)
    if not quiet:
        kinds = defaultdict(int)
        for h in res.hits:
            kinds[h.kind] += 1
        before = defaultdict(int)
        for h in res.hits:
            before[h.kind] += 1
        for _, h in res.exempt_hits:
            before[h.kind] += 1
        print(f"scanned {res.files} files (+{res.members} archive members; {len(res.skipped_binaries)} binary "
              f"files skipped); hits before exemptions: {dict(before)} = {sum(before.values())}")
        print(f"hits after exemptions: {dict(kinds)} = {len(res.hits)}; exempted {len(res.exempt_hits)} "
              f"by {sum(1 for e in exempts if e['used'])}/{len(exempts)} exemption(s) "
              f"({sum(1 for e in exempts if e['used'] >= WIDE_EXEMPT)} flagged wide)")
        print(f"context-rejected {sum(res.context_rejects.values())}; embedded dropped "
              f"{sum(res.embed_skips.values())}; synthetic/placeholder {sum(res.syn_values.values())}; "
              f"hash-skipped {len(res.hash_skips)}; ambiguous {sum(res.ambiguous_seen.values())}; "
              f"not scanned {len(res.unscanned)}; report -> {report}")
    return res


def _utf8_stdio(streams=None):
    """Item 11: never crash on a cp1252 console; re-open stdout/stderr as UTF-8."""
    for st in streams if streams is not None else (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, io.UnsupportedOperation):
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="Independent leak verifier (second gate).")
    ap.add_argument("--names")
    ap.add_argument("--maps")
    ap.add_argument("--tree")
    ap.add_argument("--exempt")
    ap.add_argument("--synthetic", help="YAML list of synthetic ranges / placeholders (never shape hits)")
    ap.add_argument("--policy", help="builder policy YAML; its synthetic_ranges / placeholders are read as data")
    ap.add_argument("--names-exemptions", action="store_true",
                    help="also apply the name table's own scan.exemptions (reported per exemption)")
    ap.add_argument("--repo", help="with --names-exemptions: only entries whose repo list includes this repo")
    ap.add_argument("--stats", help="private real-statistics list, one value per line (rule real_stat; empty = off)")
    ap.add_argument("--report")
    ap.add_argument("--max-per-key", type=int, default=0, help="cap rows per key in the report (0 = all)")
    ap.add_argument("--selftest", action="store_true")
    _utf8_stdio()
    try:
        a = ap.parse_args(argv)
    except SystemExit as ex:
        return 1 if ex.code else 0
    try:
        if a.selftest:
            return 0 if selftest(a.names, a.maps) else 5
        if not (a.names and a.maps and a.tree and a.report):
            raise UsageError("--names, --maps, --tree and --report are required")
        res = run_verify(a.names, a.maps, a.tree, a.exempt, a.report, a.max_per_key, synthetic=a.synthetic,
                         policy=a.policy, names_exemptions=a.names_exemptions, repo=a.repo, stats=a.stats)
        return 4 if res.hits else 0
    except UsageError as ex:
        print(f"usage error: {ex}", file=sys.stderr)
        return 1
    except OSError as ex:
        print(f"file error: {ex}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Selftest (built-in fake table and tree; never reads the real table except
# for the source-cleanliness check when --names is given)
# ---------------------------------------------------------------------------
FAKE_NAMES = {
    "categories": {
        "org": [
            {"real": "Zorblaxia", "alias": "Quennport Works", "token": "QUENNPORT"},
            {"real": "Zorblaxia Garden", "alias": "<keep>"},
            {"real": "QX", "alias": "Vy"},
            {"real": "vault/zeta-dir", "alias": "store/omega-dir", "scope": "path"},
            {"real": "(?i)zzregex\\d+", "alias": "x", "regex": True},
            {"real": "([unclosed", "alias": "x", "regex": True},
            {"real": "[a-z]+\\.tunnelfake\\.test", "alias": "t.example.com", "regex": True},
            {"real": "(?i:UNITX)-?(\\d{5})(?!\\d)", "alias": "<map>", "regex": True, "map_group": "a",
             "value_group": 1, "permutation": "fake"},
            {"real": "Plumtor", "alias": "Zorblaxia Parkway"},
            {"real": "zq9-app-db07", "alias": "db.example.com"},
        ],
        "people": [
            {"real": "Brindle Ostrow", "alias": "Quill Varne"},
            {"real": "Ivo", "alias": "Ede"},
            {"real": "Vek", "alias": "Oda", "case": "exact"},
            {"real": "Tamsin Ko", "alias": "Pell Ard"},
        ],
        "zh": [{"real_zh": "蘑菇", "alias_zh": "香草"}],
        "contacts": [{"real": "07-7654321", "alias": "02-5550100"}],
        "keep": [{"real": "Keepwordia", "alias": "<keep>"}],
        # --- round 3 (all invented) ---
        "cm": [{"real": "VEX", "alias": "Pondbridge Tools", "token": "PONDBRIDGE"}],
        "products": [{"real": "_7QK2ZZW", "alias": "_3AB9XXQ"}],
        "sister": [{"real": "ZQV", "alias": "Diagx"}, {"real": "ZV", "alias": "Dg"}],
        "machines": [{"real": "Q7", "alias": "VX9"}],
        "hosts": [{"real": "zk7-mes-sql02", "alias": "db2.example.com"}],
        "mes_schema": [{"real": "qzxDescription", "alias": "FailDesc"},
                       {"real": "(?<![A-Za-z0-9])kvw(?=[A-Z])", "alias": "nmb", "regex": True}],
    },
    "author": {"email_public": "hello@fakepub.test"},
    # engine tokens are real words the engine treats specially - never public values
    "engine": {"identifier_only": [{"id": "zqv-lower", "token": "zqv", "context": "x"}]},
    "scan": {"exemptions": [
        {"real": "QX", "paths": ["nx_*.txt"], "context": "ctxN (?P<t>QX)", "reason": "st", "approver": "st",
         "repo": ["alpha"]},
        {"real": "Ivo", "paths": ["*"], "reason": "st wide", "approver": "st", "repo": "alpha"},
        {"real": "QX", "paths": ["*"], "reason": "other repo only", "approver": "st", "repo": ["beta"]},
    ]},
}
FAKE_MAPS = {"maps": {
    "a": {"31871": "52963", "41729": "58306"},
    "b": {"58306": "69147"},
    "w": {"807413": "902516"},
    "doc": {"QT731904": "RT880412"},
    "serial": {"K79315P": "L64280Q"},
}}


def _zip_bytes(members):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in members.items():
            z.writestr(n, d)
    return bio.getvalue()


def selftest(names_path=None, maps_path=None):
    import gzip
    import shutil
    global MAX_UNPACK, EMBED_EXCLUDE_WORDS
    saved_excl = list(EMBED_EXCLUDE_WORDS)
    results = []

    def check(label, cond):
        results.append((label, bool(cond)))

    tables = build_tables(FAKE_NAMES, FAKE_MAPS)
    tmp = tempfile.mkdtemp(prefix="vi_selftest_")
    saved_unpack = MAX_UNPACK
    try:
        tree = os.path.join(tmp, "tree")
        os.makedirs(os.path.join(tree, "sub"))
        files = {
            "long_pos.txt": "fooZORBLAXIAbar\n",
            "long_neg.txt": "Zorblaxi only\n",
            "d1_pos.txt": "x_zorblaxia_y\nZorblaxiaCo\nzorblaxias here\n",
            "d1_neg.txt": "see zorblaxiax here\n",
            "zorblaxia_notes.txt": "clean\n",
            "zorb.txt": "clean\n",
            "short_pos.txt": "x_QX_1\nfooQX\nQXWorld\n",
            "short_neg.txt": "AQX\nQXz\nBQXC\n",
            "path_pos.txt": "vault/zeta-dir\nvault\\zeta-dir\nvault\\\\zeta-dir\n",
            "path_neg.txt": "vault_zeta-dir\n",
            "mask_pos.txt": "see Zorblaxia Parkway today\n",
            "mask_neg.txt": "see Zorblaxia Lane today\n",
            "keep_mask.txt": "the Zorblaxia Garden gate\n",
            "num_pos.txt": "_t31871\nx31871_\na31871b\n",
            "num_neg.txt": "131871\n318712\n",
            "hex.txt": "deadbe31871f\nzz31871\nfeedfacecafe31871f00\n",
            "amb.txt": "value 58306 here\n",
            "view_url.txt": "%5A%6F%72blaxia\n",
            "view_html.txt": "&#90;orblaxia\n",
            "view_json.txt": "\\u005aorblaxia\n",
            "view_fw.txt": "\uff3a\uff4f\uff52\uff42\uff4c\uff41\uff58\uff49\uff41\n",
            "view_zw.txt": "Zor\u200bblaxia\n",
            "view_neg.txt": "plain words %20 &amp; only\n",
            "g_email.txt": "mail bob@acme-fake.test\n",
            "g_email_neg.txt": "mail a@example.com and hello@fakepub.test\n",
            "g_home.txt": "C:\\Users\\someone\\x\n",
            "g_home_neg.txt": "C:\\Users\\Public\\x\n",
            "g_unc.txt": "open \\\\fileserv\\share\\x\n",
            "g_unc_neg.txt": "open .\\\\dir\\\\x\n",
            "g_ip.txt": "host 10.1.2.3 up\n",
            "g_ip_neg.txt": "dns 8.8.8.8 up\n",
            "g_host.txt": "build01.corp\n",
            "g_host_neg.txt": "www.example.com\nsaved = C.local_utc_offset\n",
            "g_acct.txt": "ACME\\jane.doe\n",
            "g_acct_neg.txt": "a\\b\n",
            "g_phone.txt": "call 0912-345-678 or 02-2345-6789\n",
            "g_phone_neg.txt": "date 2026-09-29\n",
            "keep.txt": "Keepwordia stays\n",
            "regex_hit.txt": "zzregex42\nsee foo.tunnelfake.test now\n",
            "ex_pos.txt": "ctxA QX end\n",
            "ex_neg.txt": "ctxB QX end\n",
            "wo.txt": "Board807413\n",
            # --- round 2 ---
            "r1_derived.txt": "x qx_1\nQx.\n",
            "r1_exact.txt": "vek here\nVek there\n",
            "r2_plural.txt": "the QXs run\nQXes.\n",
            "r2_plural_neg.txt": "QXsomething\nQXest\n",
            "r3_multi.txt": "brindle_ostrow\nBrindle-Ostrow\nbrindleostrow\nBrindle.Ostrow\n"
                            "BRINDLE+OSTROW\nBrindle  Ostrow\nBRINDLE OSTROW\n",
            "r3_multi_neg.txt": "Brindle Oak\nBrindle / Ostrow\n",
            "r3_edge.txt": "user Tamsin_Ko\nTAMSINKO.\ngetTamsinKoRate()\n",
            "r3_edge_neg.txt": "tamsin_kobalt\nMAX_TAMSIN_KOBE\nmax_tamsin_koes\n",
            "r16_phone_hash.txt": "hash d0819637042aee35 x\n",
            "r4_person.txt": "OWNER: IVO\nlogin ivo\n",
            "r4_person_neg.txt": "ivory\nIVORY\n",
            "r5_url_plus.txt": "%5Aorblaxia+Parkway\n",
            "r5_url_double.txt": "q=%255Aorblaxia\n",
            "r6_ws.py": "# made by Brindle\n# Ostrow here\n",
            "r6_nbsp.html": "<p>Brindle&nbsp;Ostrow</p>\n",
            "r6_ws_neg.txt": "Brindle\n\nfoo Ostrow\n",
            "r7_cf.txt": "Zor\u2063blaxia\nZor\u202eblaxia\n",
            "r8_esc.py": 's = "\\x5aorblaxia"\nt = "\\132orblaxia"\n',
            "r8_esc_neg.py": 's = "\\x5"\n',
            "r9_thousands.txt": "id 31,871 x\n",
            "r9_thousands_neg.txt": "3,1871\n",
            "r14_hex_neg.txt": "cafe31871\n",
            "r15_ids.txt": "qt731904\nQT-731904\nQT 731904\nk79315p\n",
            "r15_ids_neg.txt": "QT7319045\n",
            "r16_phone.txt": "call 05 1234 5678\ncall 0512345678\n",
            "r16_phone_neg.txt": "id 1234567890\nts 2026-09-29\n",
            "r16_phone_name.txt": "fax 07 765 4321\n",
            "r17_host.txt": "ZQ9_APP_DB07\n",
            "r17_host_neg.txt": "zq9-app-db08\n",
            "r18_unit.txt": "UNITX-31871\nUNITX-52963\nUNITX-62626\n",
            "r20_shape.txt": "part QT552211 here\nqt-55221 there\nlot K73952Z ok\n",
            "r20_shape_neg.txt": "part RT552211 here\nSQT552211\nid 55221 x\n",
            "r20_shape_syn.txt": "QT999000\nQT880001\n",
            # --- round 3 ---
            "t1_emb.py": 'vexweekly = 0\nimport board_vexweekly as bv\nwith k(("vexweekly", key)):\n',
            "t1_emb_neg.py": "convexhull = 1\nVEX_tools\nmcpVexTransport\n",
            "t2_bare.py": 'x = "7QK2ZZW"\n',
            "t2_bare_neg.py": 'x = "7QK2ZZ"\n',
            "t2_sep.txt": "plum-tor\nPLUM_TOR\na plum tor b\nPlum.torS\nthe plum-tors\n",
            "t2_sep_neg.txt": "plum, tor\nplum / tor\nplum tore\nsaplum tor\nplum_tores\n",
            "t3_wd.txt": "C:\\...\\17-Zeta\\x\n\"a/17-Zeta/b\"\nC:\\\\17-Zeta\\\\sub\n",
            "t3_wd_neg.txt": "_NEST-PROBE-41-README.md\nsee 17-Zeta alone\n",
            "t4_ident.py": "import foo as zqv\nzqv = 3\n",
            "t4_ident_neg.py": "zqvx = 3\nimport a as zt\n",
            "t4_mask.txt": "use zqv here\n",
            "t5_mes.sql": "select qzxPK, qzxClassID, jhyPK, jhyName from t\nkvwStart\n",
            "t5_mes_neg.js": "getName setItem nmbStart nmbPK\n",
            "t6_site.py": 'assert "zk7" not in x\nhost zk7mes\n',
            "t6_site_neg.txt": "zk71 x\n",
            "t7_syn.txt": "UNITX-70550\n",
            "t12_person.txt": "VekRoundMinutes = 1\nSet-Cookie: a; Vek-Age=3\nselect Vek(ts)\nMin Vek Pass\n"
                              "ask Vek today\nqtyVek>1\n",
            "t12_code.txt": "cnt 10Q70 x\ntail_10Q7=1\nunit Q7 down\n",
            "t12_label.py": 't("(q1) a")\nt("(q7) b")\nt("(q3) c")\n',
            "t12_label_neg.py": 't("(q7) alone")\n',
            "t12_flag.txt": "open mode=zv\nsqlite zv）\nwo_meta（zv）\n_zvs = 1\n",
            "t12_flag_neg.txt": "the zv tool\n",
            "t12_css.css": ".ld-brindle .ostrow-kpi { x }\n",
        }
        for n, c in files.items():
            with open(os.path.join(tree, n), "w", encoding="utf-8", newline="") as f:
                f.write(c)
        blobs = {
            "sub/bin_pos.bin": b"\x00\x01ZORBLAXIA\x00" + "zorblaxia".encode("utf-16-le") + b"\x00",
            "sub/bin_neg.bin": b"\x00\x01nothing\x00",
            "r10_u32.txt": "vendor Zorblaxia\n".encode("utf-32"),
            "r10_u16le.txt": "filler\nid 31871 down\n".encode("utf-16-le"),
            "r10_u16be.txt": "filler\nvendor Zorblaxia\n".encode("utf-16-be"),
            "r10_big5.csv": "品項,數量\n蘑菇,3\n".encode("cp950"),
            "r11_bin.bin": b"\x00\x01\xffsite=QX;\x00\xff" + "QX".encode("utf-16-le") + b"\x00\x00"
                           + b"\xffid=31871;\x00\xff",
            "r11_bin_neg.bin": b"\x00\xffAQXB\x00\xff" + b"\x00\x01131871\x00\xff",
            "r12_arch.zip": _zip_bytes({"inner/a.txt": b"vendor Zorblaxia\n",
                                        "nested.zip": _zip_bytes({"deep.txt": b"id 31871\n"})}),
            "r12_deep.zip": _zip_bytes({"l1.zip": _zip_bytes({"l2.zip": _zip_bytes({"l3.txt": b"QX\n"})})}),
            "r12_doc.xlsx": _zip_bytes({"xl/sharedStrings.xml": b"<sst><si><t>Zorblaxia</t></si></sst>"}),
            "r12_z.gz": gzip.compress(b"vendor Zorblaxia shipped\n"),
            "r12_big.zip": _zip_bytes({"big.txt": b"a" * 5000}),
            "r12_clean.zip": _zip_bytes({"ok.txt": b"nothing here\n"}),
            # round 3 item 9: fonts / images are listed, never text-matched
            "static/f.woff2": b"wOF2\x00\x01ZORBLAXIA QX id=31871\x00",
            "pic.dat": b"\x89PNG\r\n\x1a\n\x00ZORBLAXIA QX\x00",
        }
        os.makedirs(os.path.join(tree, "static"))
        for n, b in blobs.items():
            with open(os.path.join(tree, n), "wb") as f:
                f.write(b)
        # round 3 item 10: a file whose absolute path is longer than 260 characters
        deep = os.path.join(tree, "long", *(["d" * 60] * 5))
        os.makedirs(long_path(deep))
        with open(long_path(os.path.join(deep, "deep.txt")), "w", encoding="utf-8") as f:
            f.write("vendor Zorblaxia\n")
        deep_ok = len(os.path.abspath(os.path.join(deep, "deep.txt"))) > 260
        # .git with metadata that only git plumbing can see (commit author/message)
        git_ok = False
        try:
            genv = dict(os.environ, GIT_AUTHOR_NAME="Plumtor Bot", GIT_AUTHOR_EMAIL="bot@example.com",
                        GIT_COMMITTER_NAME="ci", GIT_COMMITTER_EMAIL="ci@example.com",
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
            gd = os.path.join(tree, ".git")
            subprocess.run(["git", "init", "-q", tree], check=True, env=genv, capture_output=True)
            subprocess.run(["git", "-C", tree, "commit", "-q", "--allow-empty", "--no-verify",
                            "-m", "touch the QX line"], check=True, env=genv, capture_output=True)
            with open(os.path.join(gd, "description"), "w", encoding="utf-8") as f:
                f.write("vendor Zorblaxia\n")
            git_ok = True
        except (OSError, subprocess.SubprocessError):
            git_ok = False
        ex_path = os.path.join(tmp, "ex.yaml")
        with open(ex_path, "w", encoding="utf-8") as f:
            f.write("exemptions:\n  - name: QX\n    path: ex_*.txt\n    context: 'ctxA (?P<t>QX)'\n"
                    "    reason: selftest\n    approver: selftest\n")
        bad_ex = os.path.join(tmp, "bad.yaml")
        with open(bad_ex, "w", encoding="utf-8") as f:
            f.write("exemptions:\n  - name: QX\n    path: '*'\n    context: 'x'\n")
        syn_p = os.path.join(tmp, "syn.yaml")
        with open(syn_p, "w", encoding="utf-8") as f:
            f.write("values: [QT999000]\nranges:\n  - {prefix: QT, from: 880000, to: 880999}\n"
                    "  - {prefix: '', from: 70500, to: 70599}\n")
        names_p = os.path.join(tmp, "n.yaml")
        maps_p = os.path.join(tmp, "m.yaml")
        import yaml
        with open(names_p, "w", encoding="utf-8") as f:
            yaml.safe_dump(FAKE_NAMES, f, allow_unicode=True)
        with open(maps_p, "w", encoding="utf-8") as f:
            yaml.safe_dump(FAKE_MAPS, f)

        def snapshot():
            st = {}
            for r, _, fs in os.walk(long_path(tree)):
                for fn in fs:
                    p = os.path.join(r, fn)
                    with open(p, "rb") as fh:
                        st[p] = (os.path.getmtime(p), fh.read())
            return st
        before = snapshot()
        report = os.path.join(tmp, "report.md")
        MAX_UNPACK = 1000
        EMBED_EXCLUDE_WORDS = sorted(saved_excl + ["convex"])   # selftest-only English word
        res = run_verify(names_p, maps_p, tree, ex_path, report, quiet=True, synthetic=syn_p)
        MAX_UNPACK = saved_unpack
        EMBED_EXCLUDE_WORDS = saved_excl
        after = snapshot()

        def hits(fname, kind=None, key=None, view=None, line=None):
            return [h for h in res.hits if h.file == fname and (kind is None or h.kind == kind)
                    and (key is None or h.key == key) and (view is None or h.view == view)
                    and (line is None or h.line == line)]

        def lines(fname, kind=None, key=None):
            return {h.line for h in hits(fname, kind, key)}

        check("R1+ long name, case-insensitive substring", hits("long_pos.txt", "name", "Zorblaxia"))
        check("R1- partial long name not hit", not hits("long_neg.txt"))
        check("D1+ long name with a right boundary (non-letter, camel, plural) is a hit",
              lines("d1_pos.txt", "name", "Zorblaxia") == {1, 2, 3})
        check("D1- long name directly followed by a letter of the same word is not a hit", not hits("d1_neg.txt"))
        xr = Result()
        xt = "var k=31871;\n"
        xs = xt.index("31871")
        xex = [{"i": "T", "name": "real-value:a", "paths": ["x3.js"], "rx": re.compile(r"k=(?P<t>\d{5})"),
                "reason": "t", "approver": "t", "used": 0, "context": "", "src": "policy"}]
        _emit(xr, "number", "31871", xt, xs, xs + 5, "x3.js", "raw", set(), xex, False, "a", LineIndex(xt), True, False)
        _emit(xr, "number", "31871", xt, xs, xs + 5, "x4.js", "raw", set(), xex, False, "a", LineIndex(xt), True, False)
        check("X3 a 'real-value:<group>' exemption bound to path + context releases a mapped number; other paths stay hits",
              len(xr.exempt_hits) == 1 and len(xr.hits) == 1 and xr.hits[0].file == "x4.js")
        # v9 (WP-N9 §1): a mapped value the policy declares as a placeholder is not a hit (number scan and
        # table regex); without the policy it stays a hit.  3-5 (a): a placeholder of a flagged group is also
        # recognised under its public prefix.
        ph_pol = os.path.join(tmp, "ph_policy.yaml")
        with open(ph_pol, "w", encoding="utf-8") as f:
            f.write("placeholders:\n  a:\n    values: ['31871']\n  doc:\n    values: ['QT00100']\n")
        n9_names = {"categories": {"x": [
            {"real": "(?i)(?<![A-Za-z0-9])(?P<p>QT)(?P<d>\\d{5})(?![A-Za-z0-9])", "alias": "<map>", "regex": True,
             "map_group": "doc", "prefix_alias": {"QT": "RT"}, "placeholder_prefix_alias": True}]}}
        t9 = build_tables(FAKE_NAMES, FAKE_MAPS)
        t9.synthetic = prefixed_placeholders(load_synthetic(None, ph_pol), n9_names)
        r_on, r_off = Result(), Result()
        scan_text("k = 31871;\n", "n9.txt", t9, Masker(t9.public_lc), [], r_on)
        scan_text("k = 31871;\n", "n9.txt", tables, Masker(tables.public_lc), [], r_off)
        check("V9-1 a mapped value declared as a policy placeholder is not a number hit (without the policy it is)",
              not [h for h in r_on.hits if h.kind == "number"] and [h for h in r_off.hits if h.kind == "number"])
        check("V9-2 placeholder of a placeholder_prefix_alias group is recognised under its public prefix",
              synthetic_class(t9.synthetic, "RT00100", "doc", t9.group_widths) == "placeholder"
              and synthetic_class(t9.synthetic, "RT00101", "doc", t9.group_widths) is None)
        check("R2+ long name in path", hits("zorblaxia_notes.txt", "name", line=0))
        check("R2- clean path", not hits("zorb.txt"))
        check("R3+ short name: non-letter boundary", hits("short_pos.txt", "name", "QX", line=1))
        check("R3+ short name: camel boundary before", hits("short_pos.txt", "name", "QX", line=2))
        check("R3+ short name: acronym boundary after", hits("short_pos.txt", "name", "QX", line=3))
        check("R3- short name inside letters", not hits("short_neg.txt"))
        pp = hits("path_pos.txt", "name")
        check("R4+ path forms: slash, backslash, escaped backslash", {h.line for h in pp} == {1, 2, 3})
        check("R4- path with other separator", not hits("path_neg.txt"))
        check("R5+ masked inside public value", not hits("mask_pos.txt"))
        check("R5- not masked outside public value", hits("mask_neg.txt", "name", "Zorblaxia"))
        check("R5+ masked inside a kept phrase (extension)", not hits("keep_mask.txt"))
        check("R6+ number with letter/underscore neighbours", lines("num_pos.txt", "number", "31871") == {1, 2, 3})
        check("R6- number inside longer digit run", not hits("num_neg.txt"))
        check("R7+ hash-shaped run (>=16) skipped and listed",
              any(v == "31871" and f == "hex.txt" and ln == 3 for v, f, ln, *_ in res.hash_skips)
              and not hits("hex.txt", line=3))
        check("R7- short hex-ish runs (<16) are hits", lines("hex.txt", "number") == {1, 2})
        check("R8+ ambiguous value listed not counted",
              "58306" in tables.ambiguous_numbers and not hits("amb.txt")
              and res.ambiguous_seen.get("number:58306") == 1)
        check("R8- non-ambiguous real not listed", "31871" not in tables.ambiguous_numbers)
        for fn, view in (("view_url.txt", "url-decoded"), ("view_html.txt", "html-unescaped"),
                         ("view_json.txt", "json-unescaped"), ("view_fw.txt", "fullwidth-folded"),
                         ("view_zw.txt", "format-stripped")):
            check(f"R9+ encoded variant found in view {view}", hits(fn, "name", "Zorblaxia", view=view))
        check("R9- plain text yields no view hit", not hits("view_neg.txt"))
        for pn, pos, neg in (("email-non-example", "g_email.txt", "g_email_neg.txt"),
                             ("windows-home", "g_home.txt", "g_home_neg.txt"),
                             ("unc-path", "g_unc.txt", "g_unc_neg.txt"),
                             ("private-ipv4", "g_ip.txt", "g_ip_neg.txt"),
                             ("internal-host-suffix", "g_host.txt", "g_host_neg.txt"),
                             ("domain-account", "g_acct.txt", "g_acct_neg.txt"),
                             ("tw-phone", "g_phone.txt", "g_phone_neg.txt")):
            check(f"R10+ generic {pn}", hits(pos, "generic", pn))
            check(f"R10- generic {pn}", not hits(neg, "generic", pn))
        check("R10+ tw-phone both mobile and landline", len(hits("g_phone.txt", "generic", "tw-phone")) == 2)
        bp = [h for h in res.hits if h.file == "sub/bin_pos.bin"]
        check("R11+ binary ASCII form", any(h.view == "binary:ascii/utf-8" for h in bp))
        check("R11+ binary UTF-16 form", any(h.view == "binary:utf-16le" for h in bp))
        check("R11- clean binary", not [h for h in res.hits if h.file == "sub/bin_neg.bin"])
        check("R12+ exemption releases hit and is counted",
              not hits("ex_pos.txt") and any(i == 0 for i, _ in res.exempt_hits))
        check("R12- exemption context does not match elsewhere", hits("ex_neg.txt", "name", "QX"))
        try:
            load_exempt(bad_ex)
            check("R12+ exemption missing field is an error", False)
        except UsageError:
            check("R12+ exemption missing field is an error", True)
        check("R13+ keep entries are not real names", not hits("keep.txt"))
        check("R13- non-keep real is a real name", any(nf.text == "Zorblaxia" for nf in tables.names))
        check("R15+ number after replaced prefix", hits("wo.txt", "number", "807413"))
        check("R16 tree untouched (read-only, incl. .git)", before == after)
        try:
            run_verify(names_p, maps_p, tree, None, os.path.join(tree, "r.md"), quiet=True)
            check("R17 report inside tree rejected", False)
        except UsageError:
            check("R17 report inside tree rejected", not os.path.exists(os.path.join(tree, "r.md")))
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            rc18 = main(["--names", names_p])
        check("R18 exit code 1 on missing args", rc18 == 1)
        rep = open(report, encoding="utf-8").read()
        check("R19 report has all sections",
              all(s in rep for s in ("## Summary", "## Name hits", "## Number hits", "## Generic pattern hits",
                                     "## Hash-shaped skipped", "## Ambiguous", "## Exemption usage",
                                     "## Not scanned", "## Table regex compile errors", "Ambiguous samples")))

        # ---------------- round 2 items ----------------
        check("N1+ short name derived lower/capitalized forms", lines("r1_derived.txt", "name") == {1, 2})
        check("N1+ derived forms keep the boundary rule", not hits("short_neg.txt"))
        check("N1- case-exact short entry gets no derived forms", lines("r1_exact.txt", "name") == {2})
        check("N2+ short name + s / es plural", lines("r2_plural.txt", "name", "QX") == {1, 2})
        check("N2- s/es followed by letters is not a plural", not hits("r2_plural_neg.txt"))
        check("N3+ multi-word: _ - . + none, double space, all caps",
              lines("r3_multi.txt", "name", "Brindle Ostrow") == {1, 2, 3, 4, 5, 6, 7})
        check("N3- other words / other separators", not hits("r3_multi_neg.txt"))
        check("N3+ multi-word with a short edge word: boundary incl. camel",
              lines("r3_edge.txt", "name", "Tamsin Ko") == {1, 2, 3})
        check("N3- short edge word continuing into a longer word", not hits("r3_edge_neg.txt"))
        check("N16- unseparated phone inside a hex hash", not hits("r16_phone_hash.txt", "generic"))
        check("N4+ person short name all-caps and lower-case account", lines("r4_person.txt", "name") == {1, 2})
        check("N4- person short name inside a longer word", not hits("r4_person_neg.txt"))
        check("N5+ URL decode: plus as space (masked by public value)", not hits("r5_url_plus.txt"))
        check("N5+ URL decode repeated (double encoding)",
              hits("r5_url_double.txt", "name", "Zorblaxia", view="url-decoded"))
        check("N6+ whitespace view: comment prefixes dropped across lines",
              hits("r6_ws.py", "name", "Brindle Ostrow", view="whitespace-normalized", line=1))
        check("N6+ non-breaking space (html) as separator", hits("r6_nbsp.html", "name", "Brindle Ostrow"))
        check("N6- blank line + other word between words", not hits("r6_ws_neg.txt"))
        check("N7+ all Unicode format (Cf) characters stripped",
              {h.line for h in hits("r7_cf.txt", "name", "Zorblaxia", view="format-stripped")} == {1, 2})
        check("N8+ \\xHH and octal escapes decoded",
              {h.line for h in hits("r8_esc.py", "name", "Zorblaxia", view="escape-decoded")} == {1, 2})
        check("N8- incomplete escape", not hits("r8_esc_neg.py"))
        check("N9+ thousands separator removed", hits("r9_thousands.txt", "number", "31871",
                                                       view="digit-grouping-stripped"))
        check("N9- comma not a thousands separator", not hits("r9_thousands_neg.txt"))
        check("N10+ UTF-32 with BOM", hits("r10_u32.txt", "name", "Zorblaxia"))
        check("N10+ UTF-16LE without BOM", hits("r10_u16le.txt", "number", "31871"))
        check("N10+ UTF-16BE without BOM", hits("r10_u16be.txt", "name", "Zorblaxia"))
        check("N10+ cp950 text", hits("r10_big5.csv", "name", "蘑菇"))
        bv = {(h.kind, h.view) for h in hits("r11_bin.bin")}
        check("N11+ binary short name ASCII + UTF-16", {("name", "binary:ascii/utf-8"),
                                                        ("name", "binary:utf-16le")} <= bv)
        check("N11+ binary mapped number", ("number", "binary:ascii/utf-8") in bv)
        check("N11- binary short name in letters / number in digits", not hits("r11_bin_neg.bin"))
        check("N12+ zip member", hits("r12_arch.zip!/inner/a.txt", "name", "Zorblaxia"))
        check("N12+ nested zip (depth 2)", hits("r12_arch.zip!/nested.zip!/deep.txt", "number", "31871"))
        check("N12+ depth 3 archive listed as not scanned",
              any(i.startswith("r12_deep.zip!/l1.zip!/l2.zip") for i, _ in res.unscanned)
              and not hits("r12_deep.zip!/l1.zip!/l2.zip!/l3.txt"))
        check("N12+ xlsx (zip) member", hits("r12_doc.xlsx!/xl/sharedStrings.xml", "name", "Zorblaxia"))
        check("N12+ gzip content", hits("r12_z.gz!/(gunzip)", "name", "Zorblaxia"))
        check("N12+ oversize member listed as not scanned", any(i == "r12_big.zip!/big.txt" for i, _ in res.unscanned))
        check("N12- clean archive", not [h for h in res.hits if h.file.startswith("r12_clean.zip")])
        if git_ok:
            check("N13+ .git plain file scanned", hits(".git/description", "name", "Zorblaxia"))
            check("N13+ commit author/message via git plumbing",
                  hits(".git!/git-metadata", "name", "Plumtor") and hits(".git!/git-metadata", "name", "QX"))
            check("N13- .git/objects not scanned as files",
                  not [h for h in res.hits if h.file.startswith(".git/objects/")])
        else:
            check("N13 git available for the .git test", False)
        check("N14- hex run shorter than 16 is a hit", hits("r14_hex_neg.txt", "number", "31871"))
        check("N15+ id lower-case / hyphen / space forms",
              lines("r15_ids.txt", "number") == {1, 2, 3, 4})
        check("N15- id inside longer digit run", not hits("r15_ids_neg.txt", "number"))
        check("N16+ phone space-separated and unseparated",
              lines("r16_phone.txt", "generic", "tw-phone") == {1, 2})
        check("N16- plain long number / date", not hits("r16_phone_neg.txt", "generic"))
        check("N16+ table phone with other separators", hits("r16_phone_name.txt", "name", "07-7654321"))
        check("N17+ host with underscores, upper case", hits("r17_host.txt", "name", "zq9-app-db07"))
        check("N17- other host", not hits("r17_host_neg.txt"))
        check("N18+ table regex entry scanned", lines("regex_hit.txt", "name") == {1, 2})
        check("N18+ regex compile failure listed",
              any(p == "([unclosed" for _, p, _ in tables.regex_errors) and "([unclosed" in rep)
        check("N18+ mapped regex: real hit (as number), fake not a hit, unknown value is a warning",
              lines("r18_unit.txt", "number") == {1} and not hits("r18_unit.txt", "name")
              and any(f == "r18_unit.txt" and v == "62626" for _, f, _, v in res.unmapped))
        check("N20+ shape: unmapped value with a real prefix (tolerant digits/sep/case)",
              lines("r20_shape.txt", "shape") == {1, 2, 3}
              and {h.key for h in hits("r20_shape.txt")} == {"shape:doc", "shape:serial"})
        check("N20- shape: public prefix / glued prefix / pure-numeric groups not shaped",
              not hits("r20_shape_neg.txt") and {sh["group"] for sh in tables.shapes} == {"doc", "serial"})
        check("N20+ shape: synthetic list (value and range) suppresses",
              not hits("r20_shape_syn.txt") and res.shape_skips.get(("doc", "synthetic")) == 2)
        check("N20+ report has the shape sections", "## Shape hits" in rep and "## Shape patterns" in rep)
        first = rep.splitlines()[0] if rep else ""
        check("N19+ ambiguous: report first line warns, samples listed",
              first.startswith("> WARNING:") and "ambiguous" in first
              and any(f == "amb.txt" for _, f, *_ in res.ambiguous_samples)
              and "| ambiguous occurrences (total) | 1 |" in rep)

        # ---------------- round 3 items (T<n> = task item n; + must catch, - must not flag) ----
        check("T1+ embedded short word inside identifiers (bare, prefixed, string key)",
              lines("t1_emb.py", "embedded", "embedded:vex") == {1, 2, 3}
              and {h.token for h in hits("t1_emb.py", "embedded")} == {"vexweekly", "board_vexweekly"})
        check("T1- excluded English word / boundary hit / camel part are not embedded",
              not hits("t1_emb_neg.py", "embedded")
              and any(w == "vex" and t == "convexhull" and why == "excluded:convex"
                      for (w, t, why) in res.embed_skips)
              and lines("t1_emb_neg.py", "name") == {2, 3})
        check("T1+ report lists distinct identifiers and the exclusion list",
              "| `board_vexweekly` | 1 |" in rep and "Exclusion list (EMBED_EXCLUDE_WORDS" in rep)
        check("T2+ bare form of a '_'-wrapped real", hits("t2_bare.py", "name", "7QK2ZZW"))
        check("T2- partial bare form", not hits("t2_bare_neg.py"))
        check("T2+ separator variants (- _ space .) and case, word-bounded",
              lines("t2_sep.txt", "name", "Plumtor") == {1, 2, 3, 4, 5})
        check("T2- other separators / continuing letters", not hits("t2_sep_neg.txt"))
        check("T3+ numbered folder in a path (/, \\, escaped \\\\)",
              lines("t3_wd.txt", "workdir") == {1, 2, 3})
        check("T3- numbered token inside a name / outside a path",
              not hits("t3_wd_neg.txt") and res.workdir_bare.get("17-Zeta") == 1)
        check("T4+ import alias / assignment equal to a short real (reported once, as ident)",
              lines("t4_ident.py", "ident", "ident:zqv") == {1, 2} and not hits("t4_ident.py", "name"))
        check("T4- longer identifier / other alias", not hits("t4_ident_neg.py", "ident"))
        check("T4+ an engine-section token is not a public value (no silent masking)",
              hits("t4_mask.txt", "name", "zqv") and "zqv" not in tables.public_lc)
        mk = {h.key: 0 for h in hits("t5_mes.sql", "mesprefix")}
        for h in hits("t5_mes.sql", "mesprefix"):
            mk[h.key] += 1
        check("T5+ MES prefix: table real, table regex, <pfx>PK-shaped",
              mk == {"mesprefix:qzx": 2, "mesprefix:jhy": 2, "mesprefix:kvw": 1})
        check("T5- replaced prefix (even with PK) and ordinary camelCase are census only",
              not hits("t5_mes_neg.js", "mesprefix")
              and any(p == "nmb" and cls.startswith("replaced") for p, _, _, cls, _ in res.mes_census)
              and any(p == "get" and cls.startswith("other") for p, _, _, cls, _ in res.mes_census))
        check("T6+ site code as a word and inside an identifier",
              lines("t6_site.py", "sitecode", "sitecode:zk7") == {1}
              and lines("t6_site.py", "embedded", "embedded:zk7") == {2})
        check("T6- site code continuing into digits", not hits("t6_site_neg.txt"))
        check("T7+ unprefixed five-digit synthetic range (verifier format)",
              not hits("t7_syn.txt") and not any(f == "t7_syn.txt" for _, f, _, _ in res.unmapped)
              and any(v == "70550" and st == "synthetic" for (_, st, v) in res.syn_values))
        check("T9+ font / image files are listed and never text-matched",
              not hits("static/f.woff2") and not hits("pic.dat")
              and {f for f, *_ in res.skipped_binaries} >= {"static/f.woff2", "pic.dat"})
        check("T9- other binaries are still scanned", hits("sub/bin_pos.bin"))
        check("T10+ path longer than 260 characters is scanned",
              deep_ok and any(h.file.startswith("long/") and h.key == "Zorblaxia" for h in res.hits))
        bio = io.BytesIO()
        w = io.TextIOWrapper(bio, encoding="cp1252")
        try:
            _utf8_stdio([w])
            w.write("蘑菇 ✓\n")
            w.flush()
            ok11 = "蘑菇".encode("utf-8") in bio.getvalue()
        except UnicodeError:
            ok11 = False
        check("T11+ console stream re-opened as UTF-8", ok11)
        check("T12+ person short name: camel part / header / call / range pair dropped, word kept",
              lines("t12_person.txt", "name", "Vek") == {5}
              and all(res.context_rejects.get((r, "Vek")) for r in
                      ("person-name-as-identifier-part", "http-header-name", "expression-operand",
                       "range-pair-word")))
        check("T12+ alphanumeric code inside a longer code dropped, standalone kept",
              lines("t12_code.txt", "name", "Q7") == {3}
              and res.context_rejects.get(("inside-longer-alphanumeric-code", "Q7")) == 2)
        check("T12+ enumerated (x1)(x2) test label dropped", not hits("t12_label.py"))
        check("T12- lone parenthesised code still a hit", hits("t12_label_neg.py", "name", "q7"))
        check("T12+ read-only flag / no 2-letter lower-case plural", not hits("t12_flag.txt")
              and res.context_rejects.get(("read-only-mode-flag", "zv")) == 3)
        check("T12- the same 2-letter form in prose is a hit", hits("t12_flag_neg.txt", "name", "zv"))
        check("T12+ CSS descendant selector is not a multi-word name", not hits("t12_css.css"))
        check("T12+ report documents every context rule",
              all(f"`{r}`" in rep for r in CONTEXT_RULE_DOC) and "## Context-rejected" in rep)

        # ---- second tree: table scan.exemptions (item 8) + policy file (item 7) ----
        tree2 = os.path.join(tmp, "tree2")
        os.makedirs(tree2)
        files2 = {
            "nx_pos.txt": "ctxN QX end\n",
            "nx_neg.txt": "ctxM QX end\n",
            "wide.txt": "".join(f"person Ivo {i}\n" for i in range(25)),
            "p_syn.txt": "UNITX-71234\nUNITX-00042\nUNITX-12345\nUNITX-62626\nQT770123\nQT771123\n",
        }
        for n, c in files2.items():
            with open(os.path.join(tree2, n), "w", encoding="utf-8", newline="") as f:
                f.write(c)
        pol_p = os.path.join(tmp, "policy.yaml")
        with open(pol_p, "w", encoding="utf-8") as f:
            f.write("synthetic_ranges:\n  a:\n    - [70000, 79999]\n  doc:\n    - ['QT770000', 'QT770999']\n"
                    "placeholders:\n  a:\n    ranges: [[0, 99]]\n    values: ['12345']\n")
        report2 = os.path.join(tmp, "report2.md")
        res2 = run_verify(names_p, maps_p, tree2, None, report2, quiet=True, policy=pol_p,
                          names_exemptions=True, repo="alpha")
        rep2 = open(report2, encoding="utf-8").read()

        def hits2(fname, kind=None):
            return [h for h in res2.hits if h.file == fname and (kind is None or h.kind == kind)]
        exu = {i: sum(1 for j, _ in res2.exempt_hits if j == i) for i in ("N0", "N1", "N2")}
        check("T8+ table scan.exemptions applied (context-bound) and counted per exemption",
              not hits2("nx_pos.txt") and not hits2("wide.txt") and exu["N0"] == 1 and exu["N1"] == 25)
        check("T8- context mismatch / other-repo exemption does not release",
              hits2("nx_neg.txt", "name") and exu["N2"] == 0)
        check("T8+ report: before/after numbers and a REVIEW flag on the wide exemption",
              "### Hits before / after exemptions" in rep2 and "| **total** | " in rep2
              and any("`Ivo`" in ln and "**REVIEW**" in ln and "| 25 |" in ln for ln in rep2.splitlines()))
        try:
            load_names_exemptions({"scan": {"exemptions": [{"real": "x", "paths": ["*"], "reason": "r"}]}})
            check("T8- table exemption without approver is an error", False)
        except UsageError:
            check("T8- table exemption without approver is an error", True)
        sv = {(st, v) for (_, st, v) in res2.syn_values}
        check("T7+ policy file: unprefixed range, placeholder range and value, prefixed string range",
              {("synthetic", "71234"), ("placeholder", "00042"), ("placeholder", "12345"),
               ("synthetic", "QT770123")} <= sv)
        check("T7- value outside every rule stays unmapped / a shape hit",
              any(v == "62626" for _, _, _, v in res2.unmapped)
              and [h.match for h in hits2("p_syn.txt", "shape")] == ["QT771123"])
        check("T7- a synthetic number rule of a group ignores other digit widths",
              synthetic_class(load_synthetic(None, pol_p), "0071234", "a", {"a": {5}}) is None)

        # ------------------------------------------------------------------
        # WP-P4: real-statistics list, unpadded short forms, prefix spellings,
        # policy scan_exemptions, the two rule-level false-positive fixes
        # (all values invented; nothing here is a real value)
        # ------------------------------------------------------------------
        import copy
        names4 = copy.deepcopy(FAKE_NAMES)
        names4["categories"]["org"].append({"real": "EQ", "alias": "Pw"})
        maps4 = {"maps": dict(FAKE_MAPS["maps"], e={"00735": "07551", "04105": "08830", "00918": "07001"},
                                       f={"31111": "0918"})}
        names4_p, maps4_p = os.path.join(tmp, "n4.yaml"), os.path.join(tmp, "m4.yaml")
        with open(names4_p, "w", encoding="utf-8") as f:
            yaml.safe_dump(names4, f, allow_unicode=True)
        with open(maps4_p, "w", encoding="utf-8") as f:
            yaml.safe_dump(maps4, f)
        stats_p = os.path.join(tmp, "stats.txt")
        with open(stats_p, "w", encoding="utf-8") as f:
            f.write("# invented values\n7,777\n12345   # inline comment\n\n88\n2,500.5\n")
        empty_stats_p = os.path.join(tmp, "stats_empty.txt")
        with open(empty_stats_p, "w", encoding="utf-8") as f:
            f.write("# nothing yet\n\n")
        bad_stats_p = os.path.join(tmp, "stats_bad.txt")
        with open(bad_stats_p, "w", encoding="utf-8") as f:
            f.write("12x45\n")
        pol4_p = os.path.join(tmp, "policy4.yaml")
        with open(pol4_p, "w", encoding="utf-8") as f:
            f.write("scan_exemptions:\n"
                    "  - {real: QX, paths: ['pol_*.txt'], context: 'ctxP (?P<t>QX)', reason: st, approver: st}\n"
                    "  - {real: '7777', paths: ['pol_stat.txt'], context: 'released (?P<t>7,777)', reason: st, approver: st}\n")
        tree4 = os.path.join(tmp, "tree4")
        os.makedirs(tree4)
        files4 = {
            "st_pos.txt": "a 7,777 b\na 7777 b\nx 12,345 y\n12345.\nv 2,500.5\n2500.5\nlist 5,7777,9\n",
            "st_neg.txt": "17777\n77771\n7777.5\n1234.5\n2,500.55\n88 and 88\n112,345\n",
            "st_hex.txt": "deadbeef7777cafef00d\nab7777\n",
            "pol_stat.txt": "released 7,777\nother 7,777\n",
            "s_pos.txt": "EID 735 is down\neid=\"4105\"\nx = {\"eid\": 735}\nrow 735 of 31871\nEID735\nEID 00735\n",
            "s_neg.txt": "735 rows\nmax 4105 lines\nEID 7359\nEID 1735\neid 918 ok\n",
            "s_hex.txt": "EID deadbeef" + "735" + "cafef00d\nEID ab735\n",   # short form as its own literal: a renaming build rewrites it with the key
            "m_pos.sql": "SELECT kvw.StartTime FROM tbl kvw\ncol = row['qzx_class_id']\n"
                         "FROM dbo.SomeTable AS kvw WHERE 1\nselect kvwstarttime from t\n",
            "m_census.py": "x = kvwStartTime\ny = kvwstarttime\n",
            "m_neg.txt": "nmb_start nmb.x\nxkvw_a\nkvwx\nthe kvwlater thing\nkvw_ \nas kvwx\nKvw_x\n",
            "pol_pos.txt": "ctxP QX end\n",
            "pol_neg.txt": "ctxQ QX end\n",
            "ps_pos.ps1": "if ($a -eq 1) { }\n$b -EQ 2\n",
            "ps_neg.txt": "EQ stays\nfoo-eq_bar\n--eq x\n",
            "mail4.txt": "mail a@corp.example ok\nmail b@acme-fake.test\n",
        }
        for n, c in files4.items():
            with open(os.path.join(tree4, n), "w", encoding="utf-8", newline="") as f:
                f.write(c)
        report4 = os.path.join(tmp, "report4.md")
        res4 = run_verify(names4_p, maps4_p, tree4, None, report4, quiet=True, policy=pol4_p, stats=stats_p)
        rep4 = open(report4, encoding="utf-8").read()

        def h4(fname, kind=None, key=None):
            return [h for h in res4.hits if h.file == fname and (kind is None or h.kind == kind)
                    and (key is None or h.key == key)]

        def l4(fname, kind=None, key=None):
            return {h.line for h in h4(fname, kind, key)}
        # -- rule 1: real-statistics list
        check("P1+ stats: original, stripped and grouped forms; decimal; list neighbour",
              l4("st_pos.txt", "real_stat") == {1, 2, 3, 4, 5, 6, 7})
        check("P1+ stats: category real_stat, key carries the canonical value",
              {h.key for h in h4("st_pos.txt", "real_stat")} == {"real_stat:7777", "real_stat:12345", "real_stat:2500.5"})
        check("P1- stats: digit neighbours, decimal continuation, sub-4-digit value are not hits",
              not h4("st_neg.txt"))
        check("P1+ stats: hash-shaped run skipped and listed, letter neighbour still a hit",
              l4("st_hex.txt", "real_stat") == {2}
              and any(f == "st_hex.txt" and ln == 1 for _, f, ln, *_ in res4.hash_skips))
        check("P1+ stats report: section, count line and dropped-value note",
              "real-statistic value hits" in rep4 and "3 values active, 1 dropped" in rep4)
        check("P1- empty stats list switches the rule off", load_stats(empty_stats_p) is None and load_stats(None) is None)
        try:
            load_stats(bad_stats_p)
            check("P1- a non-numeric stats line is an error", False)
        except UsageError:
            check("P1- a non-numeric stats line is an error", True)
        # -- rule 2: unpadded short forms of zero-padded keys
        check("P2+ short form with EID context (EID space, eid=, \"eid\", EIDnnn) and next to another map key",
              {h.line for h in h4("s_pos.txt", "number") if h.key == "00735" and h.match != "00735"} == {1, 3, 4, 5}
              and any(h.line == 2 and h.match == "4105" and h.key == "04105" for h in h4("s_pos.txt", "number")))
        check("P2- the full padded key stays the number scan's (one hit, not two)",
              len([h for h in h4("s_pos.txt", "number") if h.line == 6]) == 1)
        check("P2- no EID context / digit neighbour / short form of a public value: not hits",
              not h4("s_neg.txt", "number") and res4.short_noctx == 2 and res4.short_ambig == 1)
        check("P2+ short form inside a >=16-char hex run is skipped and listed (as rule 1), a short hex-ish run is still a hit",
              l4("s_hex.txt", "number") == {2}
              and any(f == "s_hex.txt" and ln == 1 and v == "00735" for v, f, ln, *_ in res4.hash_skips))
        check("P2- hash skip does not touch the neighbouring rule-2 cases (context / padded / public-value counters unchanged)",
              res4.short_noctx == 2 and res4.short_ambig == 1 and len([h for h in h4("s_pos.txt", "number") if h.line == 6]) == 1)
        # -- rule 3: real MES prefix spellings
        check("P3+ dotted alias, SQL alias declaration (FROM t x / AS x), snake form",
              {1, 3} <= l4("m_pos.sql", "mesprefix", "mesprefix:kvw") and 2 in l4("m_pos.sql", "mesprefix", "mesprefix:qzx")
              and len([h for h in h4("m_pos.sql", "mesprefix") if h.line == 1]) == 2)
        check("P3+ lower-case form when the line is SQL", 4 in l4("m_pos.sql", "mesprefix", "mesprefix:kvw"))
        check("P3+ lower-case form when the camel form of the same identifier is in the tree",
              2 in l4("m_census.py", "mesprefix", "mesprefix:kvw"))
        check("P3- replaced prefix, embedded, too short, no corroboration, capitalised: not hits",
              not h4("m_neg.txt") and res4.mes_alt_skipped >= 1)
        # -- rule 4: policy scan_exemptions
        pex = [(i, h) for i, h in res4.exempt_hits if str(i).startswith("P")]
        check("P4+ policy exemption bound to path + context releases exactly that occurrence and is listed",
              not h4("pol_pos.txt") and h4("pol_neg.txt", "name", "QX")
              and any(i == "P0" and h.file == "pol_pos.txt" for i, h in pex))
        check("P4+ policy exemption of a real_stat value by bare value, other line still a hit",
              l4("pol_stat.txt", "real_stat") == {2} and any(i == "P1" and h.file == "pol_stat.txt" for i, h in pex))
        check("P4+ report lists policy exemptions (source, per-exemption count)",
              "| P0 | policy |" in rep4 and "| P1 | policy |" in rep4 and "policy scan_exemptions loaded: 2" in rep4)
        for bad in ("scan_exemptions:\n  - {real: QX, paths: ['*'], reason: st, approver: st}\n",
                    "scan_exemptions:\n  - {real: QX, context: 'x', reason: st, approver: st}\n"):
            bp = os.path.join(tmp, "polbad.yaml")
            with open(bp, "w", encoding="utf-8") as f:
                f.write(bad)
            try:
                load_policy_exemptions(bp)
                check("P4- policy exemption without a path or without a context pattern is an error", False)
            except UsageError:
                check("P4- policy exemption without a path or without a context pattern is an error", True)
        check("P4- a policy without scan_exemptions loads nothing", load_policy_exemptions(names4_p) == [])
        # -- rule 5: the two rule-level false positives
        check("P5+ reserved .example TLD e-mail is not a hit; another reserved TLD still is",
              l4("mail4.txt", "generic", "email-non-example") == {2})
        check("P5+ PowerShell -eq / -EQ operator is context-rejected and counted",
              not h4("ps_pos.ps1")
              and sum(n for (r, _), n in res4.context_rejects.items() if r == "powershell-comparison-operator") == 2)
        check("P5- the same short name outside an operator shape stays a hit",
              {h.line for h in h4("ps_neg.txt", "name") if h.key.lower() == "eq"} == {1, 2, 3})

        if names_path:
            real = build_tables(_load_yaml(names_path), {"maps": {}})
            with open(os.path.abspath(__file__), encoding="utf-8") as f:
                src = f.read()
            bad = sorted({nf.text for nf, *_ in find_name_hits(src, real, Masker([]))})
            badr = sorted({nf.text for nf, s, e, st, v in find_tregex_hits(src, real) if st == "hit"})
            if bad or badr:
                print("  source contains real name forms (listed in count only): %d" % (len(bad) + len(badr)))
            check("R20 source contains no real name from the table", not bad and not badr)
            if maps_path:
                mt = build_tables({}, _load_yaml(maps_path))
                badn = {v for v, *_ in find_number_hits(src, mt, Masker([]))}
                if badn:
                    print("  source contains mapped real values: %d" % len(badn))
                check("R21 source contains no mapped real value", not badn)
        else:
            print("  R20 skipped (needs --names)")
    finally:
        MAX_UNPACK = saved_unpack
        EMBED_EXCLUDE_WORDS = saved_excl
        shutil.rmtree(long_path(tmp), ignore_errors=True)
    ok = all(c for _, c in results)
    for label, c in results:
        print(("PASS " if c else "FAIL ") + label)
    print(f"selftest: {sum(c for _, c in results)}/{len(results)} passed")
    return ok


if __name__ == "__main__":
    sys.exit(main())
