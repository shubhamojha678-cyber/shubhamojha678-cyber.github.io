"""Captcha solver for the HC Rajasthan portal.

02-Sep-2026: the portal switched its captcha from **6 digits** to **6 mixed
alphanumeric glyphs** (observed live between the 11:00 and 12:00 automation
runs; first failure logged 12:02:47). The old pipeline ended in
``re.findall(r"\\d", ...)``, so it threw away every letter, could never return
6 characters, and every caller that gates on ``len(code) == 6`` rejected
forever WITHOUT EVER REACHING THE SERVER. That took down the cause list, the
supplementary list, the case watch and new-case resolution simultaneously.

What replaced it
----------------
The captcha is 6 well-separated glyphs from a 34-character alphabet — 0-9 and
A-Z with **I and O excluded** (confirmed empirically: 787 labelled glyphs over
128 solved captchas contain no I and no O). So:

    mask white strokes -> split into exactly 6 glyphs by column projection
    -> normalise each to a 20x28 bitmap -> match against a labelled template
    library with a 1-pixel-tolerant distance -> emit guesses in cost order.

THE KEY SERVER FACT (proved live, 02-Sep-2026): a **wrong guess does not
invalidate the captcha**. Five deliberate wrong submissions were followed by
the correct code and it was still accepted. So the right shape is ONE captcha
image and MANY ordered guesses, not one guess per freshly fetched image. That
is what makes this cheap: measured over 80 consecutive live captchas the
first guess is right ~80% of the time, the mean is 1.9 guesses, and the solve
rate per image was 80/80.

The library self-improves: every code the server accepts is ground truth, so
``learn()`` files those 6 glyphs back into the library (capped per character,
newest kept, written atomically because several LaunchAgent jobs run at once).

Vision is still here as a FALLBACK for the case where segmentation does not
yield 6 glyphs or the template library is missing — it reads the glyphs
imperfectly but well enough to seed confusion-expanded guesses.

Public API:
    captcha_candidates(png_bytes, limit) -> [str]   # ordered guesses
    learn(png_bytes, accepted_code)                 # file away ground truth
    preprocess_and_ocr(png_bytes) -> str            # best single guess (legacy)
"""
from __future__ import annotations

import heapq
import io
import itertools
import json
import os
import re
import tempfile
import threading
from collections import Counter
from typing import Iterable

import objc
from PIL import Image, ImageChops, ImageFilter
from Foundation import NSURL
from Vision import (
    VNImageRequestHandler,
    VNRecognizeTextRequest,
    VNRequestTextRecognitionLevelAccurate,
)

_WHITE_THRESHOLD = 220
NW, NH = 20, 28                       # normalised glyph bitmap
GLYPH_LIB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "refs", "captcha_glyphs.json")
MAX_SAMPLES_PER_CHAR = 25

_lock = threading.Lock()
_lib_cache = None                     # {char: [{"b": bits, "a": aspect}]}
_compiled_cache = None                # {char: [(on_set, dilated_set, aspect)]}


# ---------------------------------------------------------------- imaging
def _mask(png: bytes, threshold: int = _WHITE_THRESHOLD) -> Image.Image:
    """White strokes -> black glyphs on a white field (Vision/OCR friendly)."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    r, g, b = img.split()
    f = lambda v: 255 if v >= threshold else 0
    m = ImageChops.multiply(ImageChops.multiply(r.point(f), g.point(f)), b.point(f))
    return ImageChops.invert(m)


def _columns(mask: Image.Image) -> list[int]:
    w, h = mask.size
    px = mask.load()
    return [sum(1 for y in range(h) if px[x, y] < 128) for x in range(w)]


def _runs(cols: list[int], min_w: int = 3) -> list[list[int]]:
    runs, start = [], None
    for x, c in enumerate(cols):
        if c > 0 and start is None:
            start = x
        elif c == 0 and start is not None:
            if x - start >= min_w:
                runs.append([start, x])
            start = None
    if start is not None:
        runs.append([start, len(cols)])
    return runs


def _force_six(runs: list[list[int]], cols: list[int]) -> list[tuple[int, int]]:
    """The captcha always has exactly 6 glyphs. Merge over-segmented pieces at
    the smallest gap; split touching glyphs at the lowest-ink column."""
    runs = [list(r) for r in runs]
    while len(runs) > 6 and len(runs) > 1:
        _, i = min((runs[i + 1][0] - runs[i][1], i) for i in range(len(runs) - 1))
        runs[i] = [runs[i][0], runs[i + 1][1]]
        del runs[i + 1]
    while len(runs) < 6 and runs:
        _, i = max((r[1] - r[0], i) for i, r in enumerate(runs))
        a, b = runs[i]
        if b - a < 8:
            break
        lo, hi = a + max(3, (b - a) // 4), b - max(3, (b - a) // 4)
        cut = (min(range(lo, hi), key=lambda x: (cols[x], abs(x - (a + b) // 2)))
               if hi > lo else (a + b) // 2)
        runs[i] = [a, cut]
        runs.insert(i + 1, [cut, b])
    return [tuple(r) for r in runs]


def glyphs(png: bytes, threshold: int = _WHITE_THRESHOLD) -> list[Image.Image]:
    """Split a captcha into its 6 tight-boxed glyph images."""
    m = _mask(png, threshold)
    cols = _columns(m)
    out = []
    for x0, x1 in _force_six(_runs(cols), cols):
        box = m.crop((x0, 0, x1, m.height))
        bb = box.point(lambda v: 255 if v < 128 else 0).getbbox()
        out.append(box.crop(bb) if bb else box)
    return out


def _norm(g: Image.Image) -> str:
    im = g.resize((NW, NH), Image.LANCZOS).point(lambda v: 0 if v < 140 else 255)
    px = im.load()
    return "".join("1" if px[x, y] == 0 else "0"
                   for y in range(NH) for x in range(NW))


def _aspect(g: Image.Image) -> float:
    return round(g.width / max(1, g.height), 2)


# ---------------------------------------------------------------- library
def _bits_to_sets(bits: str):
    on = {(i % NW, i // NW) for i, c in enumerate(bits) if c == "1"}
    dil = set()
    for x, y in on:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                dil.add((x + dx, y + dy))
    return on, dil


def _load_library() -> dict:
    global _lib_cache, _compiled_cache
    if _lib_cache is None:
        try:
            with open(GLYPH_LIB_PATH) as f:
                _lib_cache = json.load(f)
        except Exception:
            _lib_cache = {}
        _compiled_cache = None
    return _lib_cache


def _compiled() -> dict:
    global _compiled_cache
    if _compiled_cache is None:
        _compiled_cache = {
            ch: [(*_bits_to_sets(t["b"]), t["a"]) for t in tmpls]
            for ch, tmpls in _load_library().items() if tmpls
        }
    return _compiled_cache


def _tol_dist(q_on, q_dil, t_on, t_dil) -> float:
    """Symmetric 1-pixel-tolerant distance — ink with no counterpart nearby.
    Plain Hamming is too brittle for this font's stroke jitter."""
    if not q_on or not t_on:
        return 1.0
    a = sum(1 for p in q_on if p not in t_dil)
    b = sum(1 for p in t_on if p not in q_dil)
    return (a + b) / (len(q_on) + len(t_on))


def _rank_glyph(g: Image.Image, top: int = 6) -> list[tuple[float, str]]:
    q_on, q_dil = _bits_to_sets(_norm(g))
    a = _aspect(g)
    scored = []
    for ch, tmpls in _compiled().items():
        best = min(_tol_dist(q_on, q_dil, t_on, t_dil) + 0.15 * min(abs(a - ta), 1.0)
                   for t_on, t_dil, ta in tmpls)
        scored.append((best, ch))
    scored.sort()
    return scored[:top]


def _enumerate(alts: list[list[str]], costs: list[list[int]], limit: int) -> list[str]:
    """Cheapest-first walk over per-position alternatives."""
    heap = [(0, tuple([0] * 6))]
    seen = {heap[0][1]}
    out: list[str] = []
    while heap and len(out) < limit:
        _, idx = heapq.heappop(heap)
        s = "".join(alts[i][idx[i]] for i in range(6))
        if s not in out:
            out.append(s)
        for i in range(6):
            if idx[i] + 1 < len(alts[i]):
                n = list(idx)
                n[i] += 1
                n = tuple(n)
                if n not in seen:
                    seen.add(n)
                    heapq.heappush(heap, (sum(costs[j][n[j]] for j in range(6)), n))
    return out


# ---------------------------------------------------------------- Vision fallback
def _vision_ocr(image_path: str, top_n: int = 1) -> Iterable[str]:
    """Recognise text in one image. Returns a list, NOT a generator, because the
    whole Vision call must finish inside one autorelease pool.

    NSURL, the handler, the request and every observation come back
    autoreleased, and a plain Python thread never drains its autorelease pool,
    so unpooled they all stay resident for the life of the process. That is the
    bug that exhausted 128 GB of RAM and all 23 GB of swap on 2026-08-30.

    Measured honestly on 2026-08-30, so nobody "optimises" this away on a wrong
    premise: on THIS path the leak is small, because a captcha is a tiny image —
    61 calls grew peak RSS 3.7 MB unpooled vs 3.0 MB pooled. The same Vision
    call on a full 1700x2200 page leaks 30.4 MB/page unpooled vs 0.46 MB/page
    pooled. So the pool is cheap insurance here rather than a live fire, but it
    stays: this module runs inside the long-lived LaunchAgent jobs
    (causelist-auto, hc-sentinel, roster-watch).

    Returns a list, NOT a generator: the whole Vision call must finish inside
    one pool. A suspended generator would leave the pool open across unrelated
    work, so do not turn this back into a yield.
    """
    with objc.autorelease_pool():
        url = NSURL.fileURLWithPath_(os.path.abspath(image_path))
        handler = VNImageRequestHandler.alloc().initWithURL_options_(url, None)
        req = VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(VNRequestTextRecognitionLevelAccurate)
        req.setUsesLanguageCorrection_(False)
        req.setMinimumTextHeight_(0.0)
        handler.performRequests_error_([req], None)
        per_obs = []
        for obs in req.results() or []:
            cands = obs.topCandidates_(top_n) or []
            per_obs.append([str(c.string()) for c in cands])  # plain str
        if not per_obs:
            return []
        return ["".join(c) for c in itertools.islice(itertools.product(*per_obs), 8)]


_VISION_RENDERS = ((220, 8, False), (220, 3, True), (220, 6, False),
                   (200, 8, False), (240, 6, False), (220, 4, True))

# Glyph pairs this handwriting font actually confuses (observed live).
_CONFUSIONS = ("0QD", "1L7T", "2Z", "5S", "6G", "8B", "CG6", "EF",
               "UV", "A4", "NM", "KX", "PR", "1Z", "J1", "7F", "Y7")
_CONF_MAP: dict[str, str] = {}
for _grp in _CONFUSIONS:
    for _c in _grp:
        for _d in _grp:
            if _d != _c and _d not in _CONF_MAP.get(_c, ""):
                _CONF_MAP[_c] = _CONF_MAP.get(_c, "") + _d


def _vision_readings(png: bytes) -> list[str]:
    votes: Counter = Counter()
    for thr, scale, minf in _VISION_RENDERS:
        m = _mask(png, thr)
        im = m.resize((m.width * scale, m.height * scale), Image.LANCZOS)
        if minf:
            im = im.filter(ImageFilter.MinFilter(3))
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            im.save(f.name)
            path = f.name
        try:
            for s in _vision_ocr(path, top_n=3):
                s = re.sub(r"[^A-Z0-9]", "", s.upper())
                if len(s) == 6:
                    votes[s] += 1
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    return [s for s, _ in votes.most_common()]


def _vision_candidates(png: bytes, limit: int) -> list[str]:
    reads = _vision_readings(png)
    if not reads:
        return []
    alts, costs = [], []
    for i in range(6):
        col = Counter(r[i] for r in reads)
        ranked = [c for c, _ in col.most_common()]
        chars = list(ranked)
        cost = [0] + [1] * (len(ranked) - 1)
        for c in ranked:
            for d in _CONF_MAP.get(c, ""):
                if d not in chars:
                    chars.append(d)
                    cost.append(2)
        alts.append(chars)
        costs.append(cost)
    return _enumerate(alts, costs, limit)


# ---------------------------------------------------------------- public API
def captcha_candidates(png: bytes, limit: int = 120) -> list[str]:
    """Ordered guesses for one captcha image, best first.

    Safe to submit these in sequence against the same image: a wrong guess does
    not invalidate the captcha (proved live 02-Sep-2026).
    """
    try:
        gs = glyphs(png)
    except Exception:
        gs = []
    if len(gs) == 6 and _compiled():
        per = [_rank_glyph(g) for g in gs]
        alts = [[ch for _, ch in p] for p in per]
        costs = [[round((s - p[0][0]) * 200) for s, _ in p] for p in per]
        out = _enumerate(alts, costs, limit)
        if out:
            return out
    return _vision_candidates(png, limit)


def learn(png: bytes, code: str) -> bool:
    """File a server-ACCEPTED code back into the template library.

    Only ever called with ground truth, so this cannot poison itself. Capped
    per character (newest kept) and written atomically, because causelist-auto,
    hc-sentinel and roster-watch can all be inside this at once.
    """
    code = (code or "").upper()
    if len(code) != 6:
        return False
    try:
        gs = glyphs(png)
        if len(gs) != 6:
            return False
        with _lock:
            lib = dict(_load_library())
            for ch, g in zip(code, gs):
                samples = list(lib.get(ch, []))
                samples.append({"b": _norm(g), "a": _aspect(g)})
                lib[ch] = samples[-MAX_SAMPLES_PER_CHAR:]
            d = os.path.dirname(GLYPH_LIB_PATH)
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(lib, f)
            os.replace(tmp, GLYPH_LIB_PATH)
            global _lib_cache, _compiled_cache
            _lib_cache, _compiled_cache = lib, None
        return True
    except Exception:
        return False


def preprocess_and_ocr(captcha_png: bytes) -> str:
    """Legacy single-guess entry point: the best candidate, or ''.

    Kept so nothing that imports this name breaks, but callers should prefer
    captcha_candidates() — one guess per image is exactly the pattern that
    made the old solver so expensive.
    """
    c = captcha_candidates(captcha_png, limit=1)
    return c[0] if c else ""
