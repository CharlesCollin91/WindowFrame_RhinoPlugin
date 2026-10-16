#! python 3
# WindowFrame_CC - builds a rectangular-section window frame along the outline of any selected geometry.
# Requires: Rhino 8+ (Windows), Python 3 (built into Rhino 8). No extra packages.
# Run: ! _-RunPythonScript "C:\path\to\WindowFrame_CC\WindowFrame_CC.py"
# Copyright (C) 2026 Charles Collin. Licensed under GPL-3.0-or-later (see LICENSE).
__version__ = "1.12"

# =============================================================================
# HEADER / IMPORTS
# =============================================================================
import os
import sys
import time
import math
import logging
import datetime
import textwrap
import traceback
from dataclasses import dataclass

import clr
import System
import System.Drawing
import Rhino
import Rhino.Geometry as rg
import Rhino.DocObjects as rdo
import scriptcontext as sc
import Eto.Forms as forms
import Eto.Drawing as drawing

# =============================================================================
# CONFIG
# =============================================================================
PLUGIN_NAME = "WindowFrame_CC"
STICKY_KEY = PLUGIN_NAME + ".ui"
DEFAULT_PLUGIN_DIR = os.path.join(os.path.expanduser("~"), "WindowFrame_CC")
LOG_LIMIT_BYTES = 500 * 1024 * 1024
TOOLTIP_WIDTH = 60
SIDE_ANGLE_DEG = 45.0          # segment within 45 deg of "up" -> side
CAMERA_DOT_MIN = 0.2           # below this the camera cannot tell front from back
BIG_RUN_WARNING = 300          # inputs above this: warn that it may take a while

DEFAULT_WIDTH_MM = 70.0
DEFAULT_DEPTH_MM = 100.0

WIDTH_MODES = ["Inside", "Outside", "Center"]
DEPTH_MODES = ["Front", "Back", "Center"]
OUTPUT_MODES = ["Solid", "Mitered 45", "Mitered 90"]
GROUP_MODES = ["Ungroup", "Group", "Group all"]

SIDE, TOP, BOTTOM = "side", "top", "bottom"


def _plugin_dir():
    try:
        return os.path.dirname(os.path.abspath(__file__)), "__file__"
    except Exception:
        return DEFAULT_PLUGIN_DIR, "default path (__file__ undefined)"


PLUGIN_DIR, PLUGIN_DIR_SOURCE = _plugin_dir()
LOG_DIR = os.path.join(PLUGIN_DIR, "logs")

# =============================================================================
# LOGGING
# =============================================================================
_logger = logging.getLogger(PLUGIN_NAME)
_logger.setLevel(logging.DEBUG)
_logger.propagate = False
_log_path = None


def _trim_log_dir(current_path):
    try:
        files = []
        for name in os.listdir(LOG_DIR):
            p = os.path.join(LOG_DIR, name)
            if os.path.isfile(p) and name.lower().endswith(".log"):
                files.append((os.path.getmtime(p), p, os.path.getsize(p)))
        total = sum(f[2] for f in files)
        files.sort()
        for _, p, size in files:
            if total <= LOG_LIMIT_BYTES:
                break
            if current_path and os.path.normcase(p) == os.path.normcase(current_path):
                continue
            try:
                os.remove(p)
                total -= size
            except Exception:
                pass
    except Exception:
        pass


def start_log_session():
    """One log file per window session. Never blocks startup."""
    global _log_path
    for h in list(_logger.handlers):
        try:
            h.close()
        except Exception:
            pass
        _logger.removeHandler(h)
    _log_path = None
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(LOG_DIR, f"{PLUGIN_NAME}_{stamp}.log")
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d | %(message)s", "%Y-%m-%d %H:%M:%S"))
        _logger.addHandler(handler)
        _log_path = path
        _trim_log_dir(path)
    except Exception:
        _log_path = None
    return _log_path


def stop_log_session():
    for h in list(_logger.handlers):
        try:
            h.flush()
            h.close()
        except Exception:
            pass
        _logger.removeHandler(h)


def log(tag, msg):
    try:
        _logger.info(f"{tag:<6} | {msg}")
    except Exception:
        pass


def log_exc(where):
    log("ERROR", f"{where}: exception\n{traceback.format_exc()}")


# =============================================================================
# THEME
# =============================================================================
TOKENS = {
    "bg_window": "#2B2D30",
    "bg_panel": "#1E1F22",
    "bg_hover": "#35373B",
    "border": "#393B40",
    "text": "#DFE1E5",
    "text_muted": "#9DA0A8",
    "text_disabled": "#5A5D63",
    "accent": "#7C5CD6",
    "accent_hover": "#8E71DE",
    "help_icon": "#3D8BFD",
    "success": "#4E9A51",
    "warning": "#C8912F",
    "error": "#D9534F",
    "font": ("Segoe UI", 9.0),
    "font_mono": ("Consolas", 9.0),
    "space": 8,
    "control_height": 28,
}


def color(token):
    h = TOKENS[token].lstrip("#")
    return drawing.Color.FromArgb(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def font(token="font", bold=False):
    fam, size = TOKENS[token]
    if bold:
        return drawing.Font(fam, size, drawing.FontStyle.Bold)
    return drawing.Font(fam, size)


def wrap_tip(text):
    return textwrap.fill(text, TOOLTIP_WIDTH)


# =============================================================================
# CORE  (pure logic, no UI)
# =============================================================================
class FrameError(Exception):
    """A contour could not be framed; message is the user-facing reason."""


class SkipError(Exception):
    """An input object is not usable; message is the user-facing reason."""


@dataclass
class Settings:
    width_side: float = DEFAULT_WIDTH_MM
    width_top: float = DEFAULT_WIDTH_MM
    width_bottom: float = DEFAULT_WIDTH_MM
    link: bool = True
    width_mode: str = "Inside"
    depth: float = DEFAULT_DEPTH_MM
    depth_mode: str = "Center"
    output: str = "Solid"            # Solid (one piece) | Mitered 45 | Mitered 90 (one piece per side)
    close_open: bool = True           # join open curves selected together into outlines

    grouping: str = "Group"          # Ungroup | Group | Group all

    @property
    def solid(self):
        return True                  # the Open output was removed (2026-10-16)

    @property
    def miter_45(self):
        return self.output in ("Mitered 45", "Solid")     # Solid on non-planar outlines = union of 45 deg members

    def widths(self):
        if self.link:
            return {SIDE: self.width_side, TOP: self.width_side, BOTTOM: self.width_side}
        return {SIDE: self.width_side, TOP: self.width_top, BOTTOM: self.width_bottom}

    def describe(self):
        w = self.widths()
        return (f"width side={w[SIDE]} top={w[TOP]} bottom={w[BOTTOM]} mm link={self.link} "
                f"offset={self.width_mode} depth={self.depth} mm dir={self.depth_mode} "
                f"output={self.output} grouping={self.grouping} close_open={self.close_open}")


@dataclass
class RunContext:
    tol: float
    ang_tol: float
    scale: float                  # mm -> document units
    cam_parallel: bool = True
    cam_dir: object = None        # Vector3d (camera direction) or None
    cam_loc: object = None        # Point3d or None


# ---- small vector helpers (avoid operator-overload ambiguity) ----------------
def v_dot(a, b):
    return a.X * b.X + a.Y * b.Y + a.Z * b.Z


def v_cross(a, b):
    return rg.Vector3d(a.Y * b.Z - a.Z * b.Y, a.Z * b.X - a.X * b.Z, a.X * b.Y - a.Y * b.X)


def v_scale(a, s):
    return rg.Vector3d(a.X * s, a.Y * s, a.Z * s)


def v_add(a, b):
    return rg.Vector3d(a.X + b.X, a.Y + b.Y, a.Z + b.Z)


def v_sub_pts(p, q):
    return rg.Vector3d(p.X - q.X, p.Y - q.Y, p.Z - q.Z)


def p_move(p, v):
    return rg.Point3d(p.X + v.X, p.Y + v.Y, p.Z + v.Z)


def v_len(a):
    return math.sqrt(v_dot(a, a))


def v_unit(a):
    n = v_len(a)
    if n < 1e-12:
        return None
    return rg.Vector3d(a.X / n, a.Y / n, a.Z / n)


def p_dist(p, q):
    return v_len(v_sub_pts(p, q))


def net_list(net_type, items):
    """Python iterable -> System.Collections.Generic.List[T] (safe for IEnumerable<T> params)."""
    lst = System.Collections.Generic.List[net_type]()
    for it in items:
        lst.Add(it)
    return lst


def join_curves(curves, tol, preserve=False):
    res = rg.Curve.JoinCurves(net_list(rg.Curve, curves), tol, preserve)
    return list(res) if res else []


# ---- API call helpers (out parameters under pythonnet) -----------------------
def try_get_plane(crv, tol):
    attempts = (lambda: crv.TryGetPlane(tol), lambda: crv.TryGetPlane(rg.Plane.Unset, tol))
    for call in attempts:
        try:
            res = call()
        except TypeError:
            continue
        if isinstance(res, tuple):
            ok, pl = res[0], res[1]
        else:
            ok, pl = bool(res), None
        log("API", f"TryGetPlane -> {ok}")
        return pl if ok else None
    log("API", "TryGetPlane -> no usable overload")
    return None


def closest_t(crv, pt):
    for call in (lambda: crv.ClosestPoint(pt), lambda: crv.ClosestPoint(pt, 0.0)):
        try:
            res = call()
        except TypeError:
            continue
        if isinstance(res, tuple) and res[0]:
            return float(res[1])
        return None
    return None


def next_discontinuity(crv, t0, t1):
    cont = rg.Continuity.G1_locus_continuous
    for call in (lambda: crv.GetNextDiscontinuity(cont, t0, t1), lambda: crv.GetNextDiscontinuity(cont, t0, t1, 0.0)):
        try:
            res = call()
        except TypeError:
            continue
        if isinstance(res, tuple) and res[0]:
            return float(res[1])
        return None
    return None


def _enum_or(enum_type, *values):
    """Bitwise OR of .NET flag enum values (pythonnet-safe)."""
    acc = values[0]
    try:
        for v in values[1:]:
            acc = acc | v
        return acc
    except Exception:
        total = 0
        for v in values:
            total |= int(v)
        return System.Enum.ToObject(clr.GetClrType(enum_type), total)


def curve_area(crv):
    try:
        amp = rg.AreaMassProperties.Compute(crv)
        return amp.Area if amp is not None else None
    except Exception:
        return None


# ---- input extraction --------------------------------------------------------
def _outer_loops(loops, tol):
    """Drop closed planar loops that lie inside another loop (holes)."""
    keep = []
    for i, a in enumerate(loops):
        inside = False
        if a.IsClosed:
            for j, b in enumerate(loops):
                if i == j or not b.IsClosed:
                    continue
                pl = try_get_plane(b, tol)
                if pl is None:
                    continue
                try:
                    rel = rg.Curve.PlanarClosedCurveRelationship(a, b, pl, tol)
                except Exception:
                    continue
                if rel == rg.RegionContainment.AInsideB:
                    inside = True
                    break
        if inside:
            log("SKIP", f"inner loop ignored (hole), length={a.GetLength():.3f}")
        else:
            keep.append(a)
    return keep


def _brep_outline(brep, tol, kind):
    if brep is None:
        raise SkipError(f"{kind}: could not read geometry")
    if brep.IsSolid:
        raise SkipError(f"{kind}: closed solid has no free edge")
    edges = brep.DuplicateNakedEdgeCurves(True, False)
    log("API", f"DuplicateNakedEdgeCurves(outer) -> {0 if edges is None else len(edges)} curves")
    if not edges:
        raise SkipError(f"{kind}: no free edge found")
    loops = join_curves(edges, tol * 2.0)
    log("API", f"JoinCurves -> {len(loops)} loops")
    if not loops:
        raise SkipError(f"{kind}: edges could not be joined")
    return _outer_loops(list(loops), tol)


def extract_contours(geom, tol):
    """Return (kind, [outline curves]) for any supported geometry, or raise SkipError."""
    if isinstance(geom, rg.Curve):
        return "curve", [geom.DuplicateCurve()]
    if isinstance(geom, rg.Extrusion):
        return "extrusion", _brep_outline(geom.ToBrep(True), tol, "extrusion")
    if isinstance(geom, rg.Brep):
        kind = "surface" if geom.Faces.Count == 1 else "polysurface"
        return kind, _brep_outline(geom, tol, kind)
    if isinstance(geom, rg.Surface):
        return "surface", _brep_outline(rg.Brep.CreateFromSurface(geom), tol, "surface")
    if isinstance(geom, rg.Mesh):
        pls = geom.GetNakedEdges()
        log("API", f"Mesh.GetNakedEdges -> {0 if pls is None else len(pls)} polylines")
        if not pls:
            raise SkipError("mesh: closed mesh has no free edge")
        loops = [rg.PolylineCurve(pl) for pl in pls]
        return "mesh", _outer_loops(loops, tol)
    if isinstance(geom, rg.SubD):
        brep = None
        try:
            brep = geom.ToBrep(rg.SubDToBrepOptions.Default)
        except Exception:
            log_exc("SubD.ToBrep")
        if brep is None:
            try:
                brep = rg.Brep.TryConvertBrep(geom)
            except Exception:
                log_exc("Brep.TryConvertBrep")
        log("API", f"SubD -> Brep {'ok' if brep is not None else 'None'}")
        return "subd", _brep_outline(brep, tol, "SubD")
    if isinstance(geom, rg.Hatch):
        crvs = geom.Get3dCurves(True)
        log("API", f"Hatch.Get3dCurves(outer) -> {0 if crvs is None else len(crvs)}")
        if not crvs:
            raise SkipError("hatch: no outer boundary")
        return "hatch", list(crvs)
    raise SkipError(f"unsupported type: {geom.GetType().Name}")


# ---- contour analysis --------------------------------------------------------
def split_segments(crv, tol):
    """Polycurve/polyline segments, then split again at kinks (G1 breaks)."""
    segs = list(crv.DuplicateSegments() or [])
    if not segs:
        segs = [crv.DuplicateCurve()]
    out = []
    for s in segs:
        params = []
        dom = s.Domain
        t = dom.T0
        guard = 0
        while guard < 10000:
            guard += 1
            nt = next_discontinuity(s, t, dom.T1)
            if nt is None or nt >= dom.T1 - 1e-12:
                break
            params.append(nt)
            t = nt
        if params:
            pieces = s.Split(net_list(System.Double, params))
            if pieces:
                out.extend(list(pieces))
                continue
        out.append(s)
    return [s for s in out if s is not None and s.GetLength() > tol]


def frame_axes(crv, ctx):
    """Return (normal_front, up, plane_origin, rule) for the contour, or raise FrameError."""
    tol = ctx.tol
    bb = crv.GetBoundingBox(True)
    center = bb.Center
    world_z = rg.Vector3d(0, 0, 1)
    planar = True
    if crv.IsLinear(tol):
        d = v_unit(v_sub_pts(crv.PointAtEnd, crv.PointAtStart))
        if d is None:
            raise FrameError("outline is too short")
        if abs(v_dot(d, world_z)) < 0.999:
            n = v_unit(v_cross(d, world_z))       # vertical plane through the line
        else:
            n = rg.Vector3d(0, -1, 0)             # vertical line: plane faces -Y
        rule_plane = "straight line: vertical plane through it"
    else:
        pl = try_get_plane(crv, tol)
        if pl is None:
            n = newell_normal(crv)
            if n is None:
                raise FrameError("outline has no main plane")
            planar = False
            rule_plane = "non-planar: average plane (members swept like a rail)"
        else:
            n = v_unit(pl.Normal)
            rule_plane = "curve plane"
    # front side: toward the active view camera when it is clear, else a world rule
    rule = None
    to_cam = None
    if ctx.cam_dir is not None:
        if ctx.cam_parallel or ctx.cam_loc is None:
            to_cam = v_unit(v_scale(ctx.cam_dir, -1.0))
        else:
            to_cam = v_unit(v_sub_pts(ctx.cam_loc, center))
    if to_cam is not None and abs(v_dot(n, to_cam)) >= CAMERA_DOT_MIN:
        if v_dot(n, to_cam) < 0:
            n = v_scale(n, -1.0)
        rule = "front faces the active view camera"
    else:
        if abs(n.Z) > 0.7:
            if n.Z < 0:
                n = v_scale(n, -1.0)
            rule = "front = world +Z (camera inconclusive)"
        elif abs(n.Y) >= abs(n.X):
            if n.Y > 0:
                n = v_scale(n, -1.0)
            rule = "front = world -Y (camera inconclusive)"
        else:
            if n.X < 0:
                n = v_scale(n, -1.0)
            rule = "front = world +X (camera inconclusive)"
    up = v_add(world_z, v_scale(n, -v_dot(world_z, n)))
    if v_len(up) < 0.3:
        world_y = rg.Vector3d(0, 1, 0)
        up = v_add(world_y, v_scale(n, -v_dot(world_y, n)))
        rule += "; up = world Y (flat outline)"
    else:
        rule += "; up = world Z"
    up = v_unit(up)
    log("ITEM", f"plane: {rule_plane}; {rule}; normal=({n.X:.3f},{n.Y:.3f},{n.Z:.3f})")
    return n, up, center, rule, planar


def classify(seg, up, center, closed_single):
    if closed_single:
        return SIDE
    chord = v_sub_pts(seg.PointAtEnd, seg.PointAtStart)
    cl = v_len(chord)
    if cl < 1e-9:
        return SIDE
    cos_up = abs(v_dot(chord, up)) / cl
    if cos_up >= math.cos(math.radians(SIDE_ANGLE_DEG)):
        return SIDE
    mid = seg.PointAt(seg.Domain.Mid)
    return TOP if v_dot(v_sub_pts(mid, center), up) > 0 else BOTTOM


def inward_sign(crv, plane, tol):
    """+1: inside is on the left (n x T), -1: on the right, 0: no inside (straight)."""
    if crv.IsLinear(tol):
        return 0
    region = crv
    if not crv.IsClosed:
        closer = rg.LineCurve(crv.PointAtEnd, crv.PointAtStart)
        joined = join_curves([crv.DuplicateCurve(), closer], tol * 2.0, True)
        if len(joined) != 1 or not joined[0].IsClosed:
            return 0
        region = joined[0]
    region = region.DuplicateCurve()
    region.Transform(rg.Transform.PlanarProjection(plane))   # exact for planar, needed for non-planar
    ori = region.ClosedCurveOrientation(plane)
    log("API", f"ClosedCurveOrientation -> {ori}")
    if ori == rg.CurveOrientation.CounterClockwise:
        return 1
    if ori == rg.CurveOrientation.Clockwise:
        return -1
    raise FrameError("cannot tell inside from outside (outline crosses itself?)")


def outward_at(seg, t, n, sign):
    tan = seg.TangentAt(t)
    left = v_cross(n, tan)
    return v_unit(v_scale(left, -float(sign)))


# ---- offsets -----------------------------------------------------------------
def _offset_single(crv, seg_for_dir, dist, n, sign, ctx):
    """Offset crv by signed dist (+ = outward). Returns one curve or raises FrameError."""
    if abs(dist) <= ctx.tol:
        return crv.DuplicateCurve()
    t = seg_for_dir.Domain.Mid
    out = outward_at(seg_for_dir, t, n, sign)
    if out is None:
        raise FrameError("degenerate segment")
    side = out if dist > 0 else v_scale(out, -1.0)
    eps = max(ctx.tol * 10.0, 1e-6)
    dp = p_move(seg_for_dir.PointAt(t), v_scale(side, eps))
    res = crv.Offset(dp, n, abs(dist), ctx.tol, rg.CurveOffsetCornerStyle.Sharp)
    log("API", f"Offset(d={dist:.4f}) -> {None if res is None else len(res)} curves")
    if not res:
        raise FrameError("offset failed: curve too tight for this width")
    if len(res) > 1:
        joined = join_curves(list(res), ctx.tol * 2.0)
        if len(joined) != 1:
            raise FrameError("offset broke into pieces: width too large for this shape")
        return joined[0]
    return res[0]


def _joint(prev_ext, next_ext, corner, ctx):
    events = rg.Intersect.Intersection.CurveCurve(prev_ext, next_ext, ctx.tol, ctx.tol)
    best = None
    if events is not None:
        for ev in events:
            d = p_dist(ev.PointA, corner)
            if best is None or d < best[0]:
                best = (d, float(ev.ParameterA), float(ev.ParameterB))
    log("API", f"CurveCurve at corner -> {0 if events is None else events.Count} events")
    return best


def offset_per_segment(segs, dists, n, sign, closed, ctx):
    """Offset each segment by its own distance and miter the joints."""
    tol = ctx.tol
    count = len(segs)
    pieces = [_offset_single(s, s, d, n, sign, ctx) for s, d in zip(segs, dists)]
    ext_len = max(abs(d) for d in dists) * 20.0 + tol * 100.0
    exts = []
    for p in pieces:
        e = p.Extend(rg.CurveEnd.Both, ext_len, rg.CurveExtensionStyle.Line)
        exts.append(e if e is not None else p)
    starts = [None] * count
    ends = [None] * count
    bridges = []
    joints = range(count) if closed else range(count - 1)
    for i in joints:
        j = (i + 1) % count
        corner = segs[i].PointAtEnd
        a_end, b_start = pieces[i].PointAtEnd, pieces[j].PointAtStart
        ang = rg.Vector3d.VectorAngle(segs[i].TangentAtEnd, segs[j].TangentAtStart)
        tangent = ang <= max(ctx.ang_tol, 1e-3)
        if tangent:
            if p_dist(a_end, b_start) > tol * 2.0:
                bridges.append(rg.LineCurve(a_end, b_start))
                log("ITEM", f"joint {i}-{j}: tangent, widths differ -> straight step")
            continue
        best = _joint(exts[i], exts[j], corner, ctx)
        if best is None:
            if p_dist(a_end, b_start) > tol * 2.0:
                bridges.append(rg.LineCurve(a_end, b_start))
            log("ITEM", f"joint {i}-{j}: no miter intersection -> straight bridge")
            continue
        ends[i] = best[1]
        starts[j] = best[2]
    trimmed = []
    for i in range(count):
        e = exts[i]
        s0 = starts[i] if starts[i] is not None else closest_t(e, pieces[i].PointAtStart)
        e0 = ends[i] if ends[i] is not None else closest_t(e, pieces[i].PointAtEnd)
        if s0 is None or e0 is None:
            raise FrameError("could not trim an offset segment")
        if e0 - s0 <= 1e-9:
            raise FrameError("a segment is shorter than the frame width")
        tr = e.Trim(float(s0), float(e0))
        if tr is None:
            raise FrameError("could not trim an offset segment")
        trimmed.append(tr)
    joined = join_curves(trimmed + bridges, tol * 2.0)
    log("API", f"JoinCurves(per-segment boundary) -> {len(joined)}")
    if len(joined) != 1:
        raise FrameError("frame outline did not close: width too large for this shape")
    return joined[0]


def _seg_count(crv):
    try:
        return len(list(crv.DuplicateSegments() or [])) or 1
    except Exception:
        return -1


def clean_boundary(crv, ctx):
    """Merge collinear lines / co-circular arcs so each frame side is one piece (no extra edges)."""
    before = _seg_count(crv)
    try:
        opts = _enum_or(rg.CurveSimplifyOptions, rg.CurveSimplifyOptions.Merge,
                        rg.CurveSimplifyOptions.RebuildLines)
        simple = crv.Simplify(opts, ctx.tol, ctx.ang_tol)
    except Exception:
        log_exc("Curve.Simplify")
        simple = None
    if simple is None or not simple.IsValid:
        log("API", f"Simplify(Merge) -> None, kept {before} segments")
        return crv
    log("API", f"Simplify(Merge) -> {before} -> {_seg_count(simple)} segments")
    return simple


def build_boundary(contour, segs, dists, n, sign, closed, ctx):
    if all(abs(d) <= ctx.tol for d in dists):
        return clean_boundary(contour.DuplicateCurve(), ctx), "contour"
    if max(dists) - min(dists) <= ctx.tol:
        crv = _offset_single(contour, segs[0], dists[0], n, sign, ctx)
        path = "uniform offset"
    else:
        crv = offset_per_segment(segs, dists, n, sign, closed, ctx)
        path = "per-segment offset"
    crv = clean_boundary(crv, ctx)
    if closed and not crv.IsClosed:
        raise FrameError("offset outline is open: width too large for this shape")
    selfx = rg.Intersect.Intersection.CurveSelf(crv, ctx.tol)
    nself = 0 if selfx is None else selfx.Count
    if nself:
        log("API", f"CurveSelf -> {nself} events")
        raise FrameError("offset outline crosses itself: width too large here")
    return crv, path


# ---- solids ------------------------------------------------------------------
def _planar(loops, tol):
    try:
        res = rg.Brep.CreatePlanarBreps(net_list(rg.Curve, loops), tol)
    except TypeError:
        res = rg.Brep.CreatePlanarBreps(net_list(rg.Curve, loops))
    log("API", f"CreatePlanarBreps -> {None if res is None else len(res)}")
    return res


def build_frame(a, b, closed, n, settings, ctx):
    depth = settings.depth * ctx.scale
    if settings.depth_mode == "Front":
        start, vec = rg.Vector3d(0, 0, 0), v_scale(n, depth)
    elif settings.depth_mode == "Back":
        start, vec = rg.Vector3d(0, 0, 0), v_scale(n, -depth)
    else:
        start, vec = v_scale(n, -depth / 2.0), v_scale(n, depth)
    a2, b2 = a.DuplicateCurve(), b.DuplicateCurve()
    a2.Translate(start)
    b2.Translate(start)
    tol = ctx.tol
    if settings.solid:
        if closed:
            planar = _planar([a2, b2], tol)
        else:
            a_end = a2.PointAtEnd
            if p_dist(a_end, b2.PointAtEnd) <= p_dist(a_end, b2.PointAtStart):
                near_end, far_end = b2.PointAtEnd, b2.PointAtStart
            else:
                near_end, far_end = b2.PointAtStart, b2.PointAtEnd
            caps = [rg.LineCurve(a_end, near_end), rg.LineCurve(a2.PointAtStart, far_end)]
            loop = join_curves([a2, b2] + caps, tol * 2.0)
            if len(loop) != 1 or not loop[0].IsClosed:
                raise FrameError("frame outline did not close")
            planar = _planar([loop[0]], tol)
        if not planar or len(planar) != 1 or planar[0].Faces.Count != 1:
            raise FrameError("could not build the frame face (outlines overlap?)")
        face = planar[0].Faces[0]
        p0 = a2.PointAtStart
        path = rg.LineCurve(p0, p_move(p0, vec))
        brep = face.CreateExtrusion(path, True)
        log("API", f"BrepFace.CreateExtrusion(cap=True) -> {'Brep' if brep is not None else None}")
        if brep is None or not brep.IsValid:
            raise FrameError("extrusion failed")
        if brep.SolidOrientation == rg.BrepSolidOrientation.Inward:
            brep.Flip()
        if not brep.IsSolid:
            log("ITEM", "warning: result is not a closed solid")
        return [brep]
    walls = []
    for crv in (a2, b2):
        segs = list(crv.DuplicateSegments() or []) or [crv]
        for s in segs:
            srf = rg.Surface.CreateExtrusion(s, vec)
            if srf is None:
                raise FrameError("wall extrusion failed")
            walls.append(rg.Brep.CreateFromSurface(srf))
    joined = rg.Brep.JoinBreps(net_list(rg.Brep, walls), tol * 2.0)
    log("API", f"JoinBreps(walls={len(walls)}) -> {None if joined is None else len(joined)}")
    out = list(joined) if joined else walls
    for br in out:
        if br is None or not br.IsValid:
            raise FrameError("wall surfaces are invalid")
    return out


# ---- depth, corners, miters (shared by planar split and non-planar members) ----
SPLIT_MIN_ANGLE_DEG = 15.0


def depth_range(settings, ctx):
    """(b0, b1) along the front normal, in document units, measured from the outline plane."""
    depth = settings.depth * ctx.scale
    if settings.depth_mode == "Front":
        return 0.0, depth
    if settings.depth_mode == "Back":
        return -depth, 0.0
    return -depth / 2.0, depth / 2.0


def perp_to(v, t):
    """Component of v perpendicular to unit vector t, unitized (None if parallel)."""
    return v_unit(v_add(v, v_scale(t, -v_dot(v, t))))


def local_dirs(tan, n, sign):
    """(t, depth_dir, outward) for a tangent: depth dir follows the main normal, kept perpendicular to t."""
    t = v_unit(tan)
    d = perp_to(n, t)
    if t is None or d is None:
        raise FrameError("segment is parallel to the frame depth direction")
    outward = v_unit(v_scale(v_cross(d, t), -float(sign)))
    return t, d, outward


def _closest_mid(p1, d1, p2, d2):
    """Midpoint of the closest points of two 3D lines (None if parallel)."""
    w0 = v_sub_pts(p1, p2)
    a, b, c = v_dot(d1, d1), v_dot(d1, d2), v_dot(d2, d2)
    d, e = v_dot(d1, w0), v_dot(d2, w0)
    den = a * c - b * b
    if abs(den) < 1e-12:
        return None
    s = (b * e - c * d) / den
    u = (a * e - b * d) / den
    q1 = p_move(p1, v_scale(d1, s))
    q2 = p_move(p2, v_scale(d2, u))
    return rg.Point3d((q1.X + q2.X) / 2.0, (q1.Y + q2.Y) / 2.0, (q1.Z + q2.Z) / 2.0)


def joint_corners(seg_i, seg_j, da_i, db_i, da_j, db_j, n, sign):
    """Outer (A) and inner (B) corner points at the joint, plus mean depth dir and a tangent flag."""
    v = seg_i.PointAtEnd
    ti, di, oi = local_dirs(seg_i.TangentAtEnd, n, sign)
    tj, dj, oj = local_dirs(seg_j.TangentAtStart, n, sign)
    davg = v_unit(v_add(di, dj)) or di
    a_pt = _closest_mid(p_move(v, v_scale(oi, da_i)), ti, p_move(v, v_scale(oj, da_j)), tj)
    b_pt = _closest_mid(p_move(v, v_scale(oi, db_i)), ti, p_move(v, v_scale(oj, db_j)), tj)
    tangent = a_pt is None or b_pt is None or rg.Vector3d.VectorAngle(ti, tj) < 1e-3
    if tangent:
        a_pt = p_move(v, v_scale(oi, da_i))
        b_pt = p_move(v, v_scale(oi, db_i))
    return a_pt, b_pt, davg, ti, tangent


def miter_plane(a_pt, b_pt, davg, t_fallback, tangent):
    """Plane through both frame corners containing the depth direction."""
    if not tangent:
        nrm = v_unit(v_cross(v_sub_pts(b_pt, a_pt), davg))
        if nrm is not None:
            return rg.Plane(a_pt, nrm)
    return rg.Plane(a_pt, t_fallback)


def _param_along(v, u, pl):
    """Distance from v along unit u to plane pl (None if parallel)."""
    den = v_dot(u, pl.Normal)
    if abs(den) < 1e-9:
        return None
    return v_dot(v_sub_pts(pl.Origin, v), pl.Normal) / den


def butt_planes(seg_i, seg_j, da_i, db_i, da_j, db_j, n, sign, i_through):
    """Square (butt) joint at the corner seg_i -> seg_j. The through member keeps the corner block,
    the other one stops against its face. Returns (end plane of i, start plane of j)."""
    v = seg_i.PointAtEnd
    ti, _di, oi = local_dirs(seg_i.TangentAtEnd, n, sign)
    tj, _dj, oj = local_dirs(seg_j.TangentAtStart, n, sign)
    if rg.Vector3d.VectorAngle(ti, tj) < math.radians(1.0):
        pl = rg.Plane(v, ti)
        return pl, pl
    faces_i = [rg.Plane(p_move(v, v_scale(oi, d)), oi) for d in (da_i, db_i)]
    faces_j = [rg.Plane(p_move(v, v_scale(oj, d)), oj) for d in (da_j, db_j)]

    def pick(faces, u, far):
        scored = [(_param_along(v, u, f), f) for f in faces]
        scored = [x for x in scored if x[0] is not None]
        if not scored:
            raise FrameError("butt joint planes are parallel")
        scored.sort(key=lambda x: x[0])
        return scored[-1][1] if far else scored[0][1]

    if i_through:
        return pick(faces_j, ti, True), pick(faces_i, tj, True)
    return pick(faces_j, ti, False), pick(faces_i, v_scale(tj, -1.0), True)


def split_joints(segs, classes, closed):
    """Joint indices where Mitered output cuts: real corners or a side/top/bottom change."""
    out = []
    count = len(segs)
    joints = range(count) if closed else range(count - 1)
    for i in joints:
        j = (i + 1) % count
        if closed and count == 1:
            break
        ang = math.degrees(rg.Vector3d.VectorAngle(segs[i].TangentAtEnd, segs[j].TangentAtStart))
        if ang >= SPLIT_MIN_ANGLE_DEG or classes[i] != classes[j]:
            out.append(i)
    return out


def newell_normal(crv):
    pts = []
    try:
        params = crv.DivideByCount(200, True)
        pts = [crv.PointAt(t) for t in params] if params else []
    except Exception:
        pts = []
    for s in list(crv.DuplicateSegments() or []):
        pts.append(s.PointAtStart)
    if len(pts) < 3:
        return None
    nx = ny = nz = 0.0
    for k in range(len(pts)):
        p, q = pts[k], pts[(k + 1) % len(pts)]
        nx += (p.Y - q.Y) * (p.Z + q.Z)
        ny += (p.Z - q.Z) * (p.X + q.X)
        nz += (p.X - q.X) * (p.Y + q.Y)
    return v_unit(rg.Vector3d(nx, ny, nz))


# ---- members: built directly with mitered ends (no boolean split, no trim) ----
MEMBER_STATIONS = 48


def member_groups(segs, classes, closed):
    """Runs of segments between cut joints. Returns (groups, ring) - ring=True: closed, no corner."""
    count = len(segs)
    joints = split_joints(segs, classes, closed)
    if closed:
        if not joints:
            return [list(range(count))], True
        groups = []
        for k in range(len(joints)):
            i = (joints[k] + 1) % count
            stop = joints[(k + 1) % len(joints)]
            idx = [i]
            while i != stop:
                i = (i + 1) % count
                idx.append(i)
            groups.append(idx)
        return groups, False
    groups, start = [], 0
    for j in joints:
        groups.append(list(range(start, j + 1)))
        start = j + 1
    groups.append(list(range(start, count)))
    return groups, False


def _group_planes(group, segs, dist_a, dist_b, n, sign, closed):
    count = len(segs)
    first, last = group[0], group[-1]
    if closed or first > 0:
        prev = (first - 1) % count
        ps = miter_plane(*joint_corners(segs[prev], segs[first], dist_a[prev], dist_b[prev],
                                        dist_a[first], dist_b[first], n, sign))
    else:
        ps = rg.Plane(segs[first].PointAtStart, segs[first].TangentAtStart)
    if closed or last < count - 1:
        nxt = (last + 1) % count
        pe = miter_plane(*joint_corners(segs[last], segs[nxt], dist_a[last], dist_b[last],
                                        dist_a[nxt], dist_b[nxt], n, sign))
    else:
        pe = rg.Plane(segs[last].PointAtEnd, segs[last].TangentAtEnd)
    return ps, pe


def _edge_curves(rail, da, db, b0, b1, n, sign, stations, periodic):
    params = rail.DivideByCount(stations, True)
    if not params or len(params) < 4:
        raise FrameError("curved segment could not be sampled")
    section = ((da, b0), (db, b0), (db, b1), (da, b1))
    pts = [[], [], [], []]
    for t in params:
        _t, d, out = local_dirs(rail.TangentAt(t), n, sign)
        p = rail.PointAt(t)
        for k, (wa, wb) in enumerate(section):
            pts[k].append(p_move(p, v_add(v_scale(out, wa), v_scale(d, wb))))
    edges = []
    for k in range(4):
        if periodic:
            e = rg.Curve.CreateInterpolatedCurve(net_list(rg.Point3d, pts[k]), 3, rg.CurveKnotStyle.ChordPeriodic)
        else:
            e = rg.Curve.CreateInterpolatedCurve(net_list(rg.Point3d, pts[k]), 3)
        if e is None:
            raise FrameError("member edge curve could not be built")
        edges.append(e)
    log("API", f"edge curves: {len(params)} stations, periodic={periodic}")
    return edges


def _cross_param(edge, pl, tol):
    ev = rg.Intersect.Intersection.CurvePlane(edge, pl, tol)
    best = None
    if ev is not None:
        for e in ev:
            dd = p_dist(e.PointA, pl.Origin)
            if best is None or dd < best[0]:
                best = (dd, float(e.ParameterA))
    if best is None:
        raise FrameError("member does not reach its miter plane (width too large for this curve?)")
    return best[1]


def _close_solid(faces, tol, what):
    joined = rg.Brep.JoinBreps(net_list(rg.Brep, faces), tol * 2.0)
    log("API", f"{what}: JoinBreps({len(faces)} faces) -> {None if joined is None else len(joined)}")
    if not joined or len(joined) != 1:
        raise FrameError(f"{what}: faces did not join")
    brep = joined[0]
    if not brep.IsSolid:
        _log_naked(brep, what + " before retry")
        try:
            n_joined = brep.JoinNakedEdges(tol * 20.0)
            log("API", f"{what}: JoinNakedEdges(tol*20) -> {n_joined}")
        except Exception:
            log_exc("JoinNakedEdges")
    if not brep.IsSolid:
        _log_naked(brep, what)
        raise FrameError(f"{what}: result is not closed")
    if brep.SolidOrientation == rg.BrepSolidOrientation.Inward:
        brep.Flip()
    return brep


def _log_naked(brep, what):
    """Log every naked edge (length, end points) so an open result can be diagnosed from the log alone."""
    try:
        naked = brep.DuplicateNakedEdgeCurves(True, True) or []
        log("ITEM", f"{what}: {len(naked)} naked edge(s), faces={brep.Faces.Count}, valid={brep.IsValid}")
        for k, c in enumerate(list(naked)[:12]):
            a, b = c.PointAtStart, c.PointAtEnd
            log("ITEM", f"  naked {k}: len={c.GetLength():.4f} ({a.X:.2f},{a.Y:.2f},{a.Z:.2f})->({b.X:.2f},{b.Y:.2f},{b.Z:.2f})")
    except Exception:
        log_exc("log naked edges")


def _ruled_sides(edges):
    sides = []
    for k in range(4):
        srf = rg.NurbsSurface.CreateRuledSurface(edges[k], edges[(k + 1) % 4])
        if srf is None:
            raise FrameError("member side surface could not be built")
        sides.append(rg.Brep.CreateFromSurface(srf))
    return sides


def _member_rail(rail, da, db, b0, b1, ps, pe, n, sign, ctx, stations):
    """Curved (or multi-segment) member: 4 edges follow the rail, cut exactly on the miter planes."""
    wmax = max(abs(da), abs(db), abs(b0), abs(b1))
    ext = rail.Extend(rg.CurveEnd.Both, wmax * 4.0 + ctx.tol * 100.0, rg.CurveExtensionStyle.Line) or rail
    edges = _edge_curves(ext, da, db, b0, b1, n, sign, stations, False)
    trimmed = []
    for e in edges:
        t0, t1 = _cross_param(e, ps, ctx.tol), _cross_param(e, pe, ctx.tol)
        if t1 - t0 <= 1e-9:
            raise FrameError("a segment is shorter than the frame width")
        tr = e.Trim(t0, t1)
        if tr is None:
            raise FrameError("member edge could not be trimmed")
        trimmed.append(tr)
    faces = _ruled_sides(trimmed)
    for pick in (lambda c: c.PointAtStart, lambda c: c.PointAtEnd):
        q = [pick(c) for c in trimmed]
        cap = rg.Brep.CreateFromCornerPoints(q[0], q[1], q[2], q[3], ctx.tol)
        if cap is None:
            raise FrameError("member end face could not be built")
        faces.append(cap)
    return _close_solid(faces, ctx.tol, "curved member")


def _member_ring(contour, da, db, b0, b1, n, sign, ctx):
    """Closed smooth outline without corners: two half members cut square at the seam and mid point.
    (A periodic ruled tube left its 4 seams misaligned and never closed.)"""
    dom = contour.Domain
    t0, tm = dom.ParameterAt(0.25), dom.ParameterAt(0.75)
    halves = contour.Split(net_list(System.Double, [t0, tm]))   # one cut on a closed curve = 1 piece; two cuts = 2
    log("API", f"ring split at 0.25 / 0.75 -> {0 if halves is None else len(halves)} piece(s)")
    if not halves or len(halves) != 2:
        raise FrameError("closed smooth outline could not be split in two")
    p0 = rg.Plane(contour.PointAt(t0), contour.TangentAt(t0))
    pm = rg.Plane(contour.PointAt(tm), contour.TangentAt(tm))
    out = []
    for half, ps, pe in ((halves[0], p0, pm), (halves[1], pm, p0)):
        out.append(_member_rail(half, da, db, b0, b1, ps, pe, n, sign, ctx, MEMBER_STATIONS))
    return out


def build_members(contour, segs, classes, dist_a, dist_b, n, sign, closed, settings, ctx):
    """One solid per member. Mitered 45: diagonal joints. Mitered 90: butt joints, top and bottom run
    full width and the sides stop between them (decision 2026-10-16); tie -> the longer member runs through.
    Returns (members, end_planes, ring) with end_planes[k] = (start plane, end plane)."""
    b0, b1 = depth_range(settings, ctx)
    groups, ring = member_groups(segs, classes, closed)
    if closed and len(groups) == 1 and not ring:
        ring = True               # one member all around: cut in two halves like a smooth ring
        if len(groups[0]) > 1:
            joined = join_curves([segs[i].DuplicateCurve() for i in groups[0]], ctx.tol * 2.0, True)
            if len(joined) != 1 or not joined[0].IsClosed:
                raise FrameError("closed outline with one member could not be rejoined")
            contour = joined[0]
    log("ITEM", f"members: {len(groups)} group(s) {groups} ring={ring} joints={'miter' if settings.miter_45 else 'butt'}")
    if ring:
        dom = contour.Domain
        p0 = rg.Plane(contour.PointAt(dom.ParameterAt(0.25)), contour.TangentAt(dom.ParameterAt(0.25)))
        pm = rg.Plane(contour.PointAt(dom.ParameterAt(0.75)), contour.TangentAt(dom.ParameterAt(0.75)))
        halves = _member_ring(contour, dist_a[0], dist_b[0], b0, b1, n, sign, ctx)
        return halves, [(p0, pm), (pm, p0)], ring
    count_g = len(groups)
    lengths = [sum(segs[i].GetLength() for i in g) for g in groups]
    ps, pe = [None] * count_g, [None] * count_g
    pairs = [(g, (g + 1) % count_g) for g in range(count_g)] if closed else [(g, g + 1) for g in range(count_g - 1)]
    for g, h in pairs:
        i, j = groups[g][-1], groups[h][0]
        if settings.miter_45 or g == h:
            pl = miter_plane(*joint_corners(segs[i], segs[j], dist_a[i], dist_b[i], dist_a[j], dist_b[j], n, sign))
            pe[g], ps[h] = pl, pl
            continue
        pri_i = 1 if classes[i] in (TOP, BOTTOM) else 0
        pri_j = 1 if classes[j] in (TOP, BOTTOM) else 0
        i_through = pri_i > pri_j or (pri_i == pri_j and lengths[g] >= lengths[h])
        pe[g], ps[h] = butt_planes(segs[i], segs[j], dist_a[i], dist_b[i], dist_a[j], dist_b[j], n, sign, i_through)
        log("ITEM", f"butt joint {g}->{h}: {classes[i]} / {classes[j]}, through = member {g if i_through else h}")
    if not closed:
        ps[0] = rg.Plane(segs[groups[0][0]].PointAtStart, segs[groups[0][0]].TangentAtStart)
        pe[-1] = rg.Plane(segs[groups[-1][-1]].PointAtEnd, segs[groups[-1][-1]].TangentAtEnd)
    members = []
    for gi, group in enumerate(groups):
        i0 = group[0]
        if len(group) == 1 and segs[i0].IsLinear(ctx.tol):
            m = _member_line(segs[i0], dist_a[i0], dist_b[i0], b0, b1, ps[gi], pe[gi], n, sign)
            kind = "straight"
        else:
            parts = [segs[i].DuplicateCurve() for i in group]
            rail = parts[0] if len(parts) == 1 else (join_curves(parts, ctx.tol * 2.0, True) or [None])[0]
            if rail is None:
                raise FrameError("member rail could not be joined")
            m = _member_rail(rail, dist_a[i0], dist_b[i0], b0, b1, ps[gi], pe[gi], n, sign, ctx,
                             max(MEMBER_STATIONS, 24 * len(group)))
            kind = "curved"
        members.append(m)
        log("ITEM", f"member {gi} ({kind}, {classes[i0]}, segs {group}): solid={m.IsSolid}")
    return members, list(zip(ps, pe)), ring


def _line_plane(q, t, pl):
    den = v_dot(t, pl.Normal)
    if abs(den) < 1e-9:
        raise FrameError("member parallel to its miter plane")
    return v_dot(v_sub_pts(pl.Origin, q), pl.Normal) / den


def _member_line(seg, da, db, b0, b1, pl_s, pl_e, n, sign):
    t, d, out = local_dirs(v_sub_pts(seg.PointAtEnd, seg.PointAtStart), n, sign)
    p = seg.PointAtStart
    section = [(da, b0), (db, b0), (db, b1), (da, b1)]
    bottom, top = [], []
    for wa, wb in section:
        q = p_move(p, v_add(v_scale(out, wa), v_scale(d, wb)))
        s0, s1 = _line_plane(q, t, pl_s), _line_plane(q, t, pl_e)
        if s1 - s0 <= 1e-9:
            raise FrameError("a segment is shorter than the frame width")
        bottom.append(p_move(q, v_scale(t, s0)))
        top.append(p_move(q, v_scale(t, s1)))
    brep = rg.Brep.CreateFromBox(net_list(rg.Point3d, bottom + top))
    log("API", f"Brep.CreateFromBox(8 pts) -> {'Brep' if brep is not None else None}")
    if brep is None or not brep.IsValid:
        raise FrameError("could not build a straight member")
    if brep.SolidOrientation == rg.BrepSolidOrientation.Inward:
        brep.Flip()
    return brep


def _offset_pt(p, o, d, wa, wb):
    return p_move(p, v_add(v_scale(o, wa), v_scale(d, wb)))


def solid_skin(contour, segs, classes, dist_a, dist_b, n, sign, closed, settings, ctx):
    """ONE closed solid for a non-planar outline, without seams at the corners: 4 edge loops
    (outer/inner x back/front) share their corner points; each pair of loops gives one ruled face
    (back, inner wall, front, outer wall). Walls are split at their kinks, front/back stay whole."""
    tol = ctx.tol
    b0, b1 = depth_range(settings, ctx)
    groups, ring = member_groups(segs, classes, closed)
    if ring or (closed and len(groups) == 1):
        dom = contour.Domain
        halves = contour.Split(net_list(System.Double, [dom.ParameterAt(0.25), dom.ParameterAt(0.75)]))
        if not halves or len(halves) != 2:
            raise FrameError("closed outline could not be split for the skin")
        rails, da, db = list(halves), [dist_a[0]] * 2, [dist_b[0]] * 2
    else:
        rails, da, db = [], [], []
        for g in groups:
            parts = [segs[i].DuplicateCurve() for i in g]
            rail = parts[0] if len(parts) == 1 else (join_curves(parts, tol * 2.0, True) or [None])[0]
            if rail is None:
                raise FrameError("skin rail could not be joined")
            rails.append(rail)
            da.append(dist_a[g[0]])
            db.append(dist_b[g[0]])
    count = len(rails)

    def sect(g):
        return ((da[g], b0), (db[g], b0), (db[g], b1), (da[g], b1))

    cs = [[None] * 4 for _ in range(count)]
    ce = [[None] * 4 for _ in range(count)]
    pairs = [(g, (g + 1) % count) for g in range(count)] if closed else [(g, g + 1) for g in range(count - 1)]
    for g, h in pairs:
        v = rails[g].PointAtEnd
        ti, di, oi = local_dirs(rails[g].TangentAtEnd, n, sign)
        tj, dj, oj = local_dirs(rails[h].TangentAtStart, n, sign)
        tangent = rg.Vector3d.VectorAngle(ti, tj) < 1e-3
        for k, ((wa_i, wb), (wa_j, _wb)) in enumerate(zip(sect(g), sect(h))):
            pi, pj = _offset_pt(v, oi, di, wa_i, wb), _offset_pt(v, oj, dj, wa_j, wb)
            m = None if tangent else _closest_mid(pi, ti, pj, tj)
            if m is None:
                m = rg.Point3d((pi.X + pj.X) / 2.0, (pi.Y + pj.Y) / 2.0, (pi.Z + pj.Z) / 2.0)
            ce[g][k], cs[h][k] = m, m
    if not closed:
        _t, d0, o0 = local_dirs(rails[0].TangentAtStart, n, sign)
        _t, d1, o1 = local_dirs(rails[-1].TangentAtEnd, n, sign)
        for k in range(4):
            cs[0][k] = _offset_pt(rails[0].PointAtStart, o0, d0, *sect(0)[k])
            ce[-1][k] = _offset_pt(rails[-1].PointAtEnd, o1, d1, *sect(count - 1)[k])
    loops = []
    for k in range(4):
        pc = rg.PolyCurve()
        for g in range(count):
            rail = rails[g]
            if rail.IsLinear(tol):
                e = rg.LineCurve(cs[g][k], ce[g][k])
            else:
                ts, te = closest_t(rail, cs[g][k]), closest_t(rail, ce[g][k])
                span = rail.Domain.Length
                pts = [cs[g][k]]
                for t in rail.DivideByCount(MEMBER_STATIONS, True) or []:
                    if ts is not None and te is not None and ts + 0.02 * span < t < te - 0.02 * span:
                        _t, d, o = local_dirs(rail.TangentAt(t), n, sign)
                        pts.append(_offset_pt(rail.PointAt(t), o, d, *sect(g)[k]))
                pts.append(ce[g][k])
                if len(pts) == 2:
                    e = rg.LineCurve(pts[0], pts[1])
                else:
                    e = rg.Curve.CreateInterpolatedCurve(net_list(rg.Point3d, pts), min(3, len(pts) - 1))
            if e is None:
                raise FrameError("skin edge could not be built")
            e = e.ToNurbsCurve()
            e.Domain = rg.Interval(float(g), float(g + 1))     # same parameter = same member on every loop
            if not pc.Append(e):
                raise FrameError("skin loop could not be assembled")
        loop = pc.ToNurbsCurve()
        if loop is None or (closed and not loop.IsClosed):
            raise FrameError("skin loop is not closed")
        loops.append(loop)
    faces = []
    for k, l, wall in ((0, 1, False), (1, 2, True), (2, 3, False), (3, 0, True)):
        srf = rg.NurbsSurface.CreateRuledSurface(loops[k], loops[l])
        if srf is None:
            raise FrameError("skin face could not be built")
        br = rg.Brep.CreateFromSurface(srf)
        if br is None:
            raise FrameError("skin face could not be converted")
        if wall:
            try:
                br.Faces.SplitKinkyFaces(math.radians(1.0), True)
            except Exception:
                log_exc("SplitKinkyFaces")
        faces.append(br)
    if not closed:
        for q in (cs[0], ce[-1]):
            cap = rg.Brep.CreateFromCornerPoints(q[0], q[1], q[2], q[3], tol)
            if cap is None:
                raise FrameError("skin end face could not be built")
            faces.append(cap)
    solid = _close_solid(faces, tol, "solid skin")
    log("ITEM", f"solid skin: {count} side(s), {solid.Faces.Count} faces, closed={solid.IsSolid}")
    return solid


def union_members(members, ctx):
    """Solid output on a non-planar outline: one piece = boolean union of the 45 deg members."""
    if len(members) == 1:
        return members, []
    res = rg.Brep.CreateBooleanUnion(net_list(rg.Brep, members), ctx.tol)
    log("API", f"CreateBooleanUnion({len(members)}) -> {None if res is None else len(res)}")
    if not res or len(res) != 1 or not res[0].IsValid:
        return members, ["solid union failed on this non-planar outline: pieces kept (grouped)"]
    union = res[0]
    try:
        union.MergeCoplanarFaces(ctx.tol)
    except Exception:
        log_exc("MergeCoplanarFaces")
    return [union], []


def closed_interp(pts):
    """Closed smooth curve through pts. The start point must be repeated at the end, otherwise
    CreateInterpolatedCurve(ChordPeriodic) returns an OPEN curve (set-1 #07/#08, v1.9)."""
    pts = list(pts)
    if p_dist(pts[0], pts[-1]) > 1e-9:
        pts.append(pts[0])
    crv = rg.Curve.CreateInterpolatedCurve(net_list(rg.Point3d, pts), 3, rg.CurveKnotStyle.ChordPeriodic)
    if crv is not None and not crv.IsClosed:
        crv.MakeClosed(1e-6)
    log("API", f"closed interpolation through {len(pts)} pts -> closed={None if crv is None else crv.IsClosed}")
    return crv


CLEAN_MAX_SEGS = 8          # above this, runs between corners are rebuilt as single curves
CLEAN_CORNER_DEG = 35.0     # a real corner when cleaning (mesh / polyline noise stays below this)


def _runs(count, joints, closed):
    """Index runs between cut joints (joint i = between segment i and i+1)."""
    if not joints:
        return [list(range(count))]
    if closed:
        runs = []
        for k in range(len(joints)):
            i = (joints[k] + 1) % count
            stop = joints[(k + 1) % len(joints)]
            idx = [i]
            while i != stop:
                i = (i + 1) % count
                idx.append(i)
            runs.append(idx)
        return runs
    runs, start = [], 0
    for j in joints:
        runs.append(list(range(start, j + 1)))
        start = j + 1
    runs.append(list(range(start, count)))
    return runs


def _seam_to_corner(c, tol):
    """A closed outline whose seam falls in the middle of a straight side keeps that side in two
    pieces after Merge (set-1 #14a: 12 faces instead of 10). Move the seam onto the sharpest corner."""
    if not c.IsClosed:
        return c
    segs = split_segments(c, tol)
    if len(segs) < 2:
        return c
    best, best_ang = None, 0.0
    for i in range(len(segs)):
        j = (i + 1) % len(segs)
        ang = rg.Vector3d.VectorAngle(segs[i].TangentAtEnd, segs[j].TangentAtStart)
        if ang > best_ang:
            best, best_ang = segs[i].PointAtEnd, ang
    seam_ang = rg.Vector3d.VectorAngle(segs[-1].TangentAtEnd, segs[0].TangentAtStart)
    if best is None or best_ang < math.radians(SPLIT_MIN_ANGLE_DEG) or seam_ang >= math.radians(SPLIT_MIN_ANGLE_DEG):
        return c
    t = closest_t(c, best)
    if t is not None and c.ChangeClosedCurveSeam(t):
        log("API", f"seam moved to a corner ({math.degrees(best_ang):.0f} deg)")
    return c


def clean_contour(crv, tol, ang_tol):
    """Clean input before building: merge collinear / co-circular pieces, then, if still many small
    segments (mesh edges, exploded polylines), rebuild each run between real corners as ONE curve:
    a line when the run is straight, a smooth curve through its vertices otherwise."""
    ctx = RunContext(tol=tol, ang_tol=ang_tol, scale=1.0)
    before = _seg_count(crv)
    c = _seam_to_corner(crv.DuplicateCurve(), tol)
    c = clean_boundary(c, ctx)
    segs = split_segments(c, tol)
    if len(segs) <= CLEAN_MAX_SEGS:
        log("ITEM", f"clean input: {before} -> {len(segs)} segment(s)")
        return c
    closed = c.IsClosed
    count = len(segs)
    joints = []
    for i in (range(count) if closed else range(count - 1)):
        j = (i + 1) % count
        ang = math.degrees(rg.Vector3d.VectorAngle(segs[i].TangentAtEnd, segs[j].TangentAtStart))
        if ang >= CLEAN_CORNER_DEG:
            joints.append(i)
    pieces = []
    for run in _runs(count, joints, closed):
        if len(run) == 1:
            pieces.append(segs[run[0]].DuplicateCurve())
            continue
        pts = [segs[run[0]].PointAtStart] + [segs[i].PointAtEnd for i in run]
        if closed and not joints:
            piece = closed_interp(pts)
        else:
            chord = rg.Line(pts[0], pts[-1])
            if max(chord.DistanceTo(q, True) for q in pts) <= tol * 10:
                piece = rg.LineCurve(pts[0], pts[-1])
            else:
                piece = rg.Curve.CreateInterpolatedCurve(net_list(rg.Point3d, pts), 3)
        if piece is None:
            log("ITEM", "clean input: a run could not be rebuilt, kept as is")
            return c
        pieces.append(piece)
    joined = pieces[0] if len(pieces) == 1 else (join_curves(pieces, tol * 2.0, True) or [None])[0]
    if joined is None or joined.IsClosed != closed:
        log("ITEM", "clean input: rebuilt runs did not join, kept merged curve")
        return c
    log("ITEM", f"clean input: {before} -> {len(pieces)} segment(s) (runs between {len(joints)} corner(s) rebuilt)")
    return joined


def close_outline(crv, tol):
    """Close an open outline: tiny gap -> move the end onto the start; otherwise add a straight segment."""
    c = crv.DuplicateCurve()
    gap = p_dist(c.PointAtStart, c.PointAtEnd)
    if gap <= max(tol * 100.0, 1e-6):
        if c.MakeClosed(max(tol * 100.0, 1e-6)) and c.IsClosed:
            return c, f"gap {gap:.4f} closed"
    j = join_curves([c, rg.LineCurve(c.PointAtEnd, c.PointAtStart)], tol * 2.0, True)
    if len(j) == 1 and j[0].IsClosed:
        return j[0], f"straight segment added, gap {gap:.3f}"
    return crv, "could not close (kept open)"


def collect_contours(items, settings, tol):
    """items: [(key, geometry)]. Returns (records, skips).
    record = {"crv", "keys", "kind"}; open curves from several inputs are joined when settings.close_open."""
    records, skips = [], {}
    for key, geom in items:
        try:
            kind, contours = extract_contours(geom, tol)
        except SkipError as ex:
            skips[key] = str(ex)
            log("SKIP", f"{key}: {ex}")
            continue
        except Exception as ex:
            skips[key] = f"unexpected error: {ex}"
            log_exc(f"{key} read outline")
            continue
        log("ITEM", f"{key}: {kind} ({geom.GetType().Name}), {len(contours)} outline(s)")
        if not contours:
            skips[key] = f"{kind}: no outline found"
            continue
        for c in contours:
            records.append({"crv": c, "keys": [key], "kind": kind})
    opens = [r for r in records if not r["crv"].IsClosed]
    if len(opens) > 1:                     # touching open curves are always joined: needed for clean junctions
        joined = join_curves([r["crv"] for r in opens], tol * 2.0)
        log("API", f"join open curves: {len(opens)} -> {len(joined)} "
                   f"(closed={sum(1 for j in joined if j.IsClosed)})")
        if joined and len(joined) < len(opens):
            kept = [r for r in records if r["crv"].IsClosed]
            for jc in joined:
                keys = []
                for r in opens:
                    t = closest_t(jc, r["crv"].PointAtStart)
                    if t is not None and p_dist(jc.PointAt(t), r["crv"].PointAtStart) <= tol * 10:
                        keys.extend(k for k in r["keys"] if k not in keys)
                kept.append({"crv": jc, "keys": keys, "kind": "joined"})
            records = kept
    if settings.close_open:
        for r in records:
            if not r["crv"].IsClosed:
                r["crv"], how = close_outline(r["crv"], tol)
                r["kind"] += " +closed"
                log("ITEM", f"{'+'.join(str(k) for k in r['keys'])}: open outline closed ({how})")
    ang_tol = math.radians(1.0)
    for r in records:
        try:
            r["crv"] = clean_contour(r["crv"], tol, ang_tol)
        except Exception:
            log_exc("clean input (raw outline kept)")
    return records, skips


def frame_for_contour(contour, settings, ctx):
    """Full pipeline for one outline. Returns (breps, notes) or raises FrameError."""
    tol = ctx.tol
    if contour is None or not contour.IsValid:
        raise FrameError("outline is invalid")
    length = contour.GetLength()
    log("ITEM", f"contour {contour.GetType().Name} closed={contour.IsClosed} length={length:.4f}")
    if length <= tol * 10:
        raise FrameError("outline is too short")
    notes = []
    n, up, center, _rule, planar = frame_axes(contour, ctx)
    xaxis = v_cross(up, n)
    plane = rg.Plane(center, xaxis, up)
    closed = contour.IsClosed
    segs = split_segments(contour, tol)
    if not segs:
        raise FrameError("outline has no usable segment")
    sign = inward_sign(contour, plane, tol)
    mode = settings.width_mode
    if sign == 0 and mode != "Center":
        notes.append("straight line has no inside: Center used")
        log("ITEM", "no inside (straight line): width mode forced to Center")
        mode = "Center"
    widths = settings.widths()
    closed_single = closed and len(segs) == 1
    dist_a, dist_b, classes = [], [], []
    for k, s in enumerate(segs):
        cls = classify(s, up, center, closed_single)
        w = widths[cls] * ctx.scale
        if mode == "Inside":
            da, db = 0.0, -w
        elif mode == "Outside":
            da, db = w, 0.0
        else:
            da, db = w / 2.0, -w / 2.0
        dist_a.append(da)
        dist_b.append(db)
        classes.append(cls)
        log("ITEM", f"seg {k} {s.GetType().Name} len={s.GetLength():.3f} class={cls} width={w:.4f}")
    side = sign if sign else 1
    if settings.output == "Solid" and planar:
        a_crv, path_a = build_boundary(contour, segs, dist_a, n, side, closed, ctx)
        b_crv, path_b = build_boundary(contour, segs, dist_b, n, side, closed, ctx)
        log("ITEM", f"solid (planar): outer={path_a} inner={path_b}")
        if closed and sign != 0:
            area_c, area_a, area_b = curve_area(contour), curve_area(a_crv), curve_area(b_crv)
            log("ITEM", f"areas contour={area_c} outer={area_a} inner={area_b}")
            if None not in (area_c, area_a, area_b):
                if min(dist_a) > 0 and area_a <= area_c:
                    raise FrameError("outer offset went inward: refused")
                if max(dist_b) < 0 and area_b >= area_c:
                    raise FrameError("inner offset went outward: refused")
        return build_frame(a_crv, b_crv, closed, n, settings, ctx), notes
    if settings.output == "Solid":
        log("ITEM", "solid (non-planar): continuous skin")
        try:
            return [solid_skin(contour, segs, classes, dist_a, dist_b, n, side, closed, settings, ctx)], notes
        except FrameError as ex:
            log("ITEM", f"solid skin failed ({ex}): falling back to the union of 45 deg pieces")
        members, planes, ring = build_members(contour, segs, classes, dist_a, dist_b, n, side, closed, settings, ctx)
        breps, mnotes = union_members(members, ctx)
        return breps, notes + ["continuous solid failed: union used"] + mnotes
    log("ITEM", f"member build: planar={planar} output={settings.output}")
    members, planes, ring = build_members(contour, segs, classes, dist_a, dist_b, n, side, closed, settings, ctx)
    if ring:
        notes.append("closed outline without corner: built as two halves")
    return members, notes


# =============================================================================
# UI
# =============================================================================
class _Report:
    """Report panel: short coloured lines. Colour is optional (falls back to plain)."""

    def __init__(self):
        self.area = forms.RichTextArea()
        self.area.ReadOnly = True
        self.area.Font = font("font_mono")
        self.area.BackgroundColor = color("bg_panel")
        self.area.TextColor = color("text")
        self.lines = []
        self._color_ok = True

    def clear(self):
        self.lines = []
        self._render()

    def add(self, text, kind="text"):
        self.lines.append((text, kind))
        self.lines = self.lines[-200:]
        self._render()

    def _render(self):
        full = "\n".join(t for t, _ in self.lines)
        self.area.Text = full
        if not self._color_ok:
            return
        try:
            pos = 0
            for text, kind in self.lines:
                if kind in ("success", "warning", "error", "text_muted") and text:
                    rng = forms.Range[int](pos, pos + len(text) - 1)
                    self.area.Buffer.SetForeground(rng, color(kind))
                pos += len(text) + 1
        except Exception:
            self._color_ok = False
            log("ERROR", "report colouring unavailable, plain text used\n" + traceback.format_exc())


def _help_icon(tip):
    d = forms.Drawable()
    d.Size = drawing.Size(16, 16)
    d.ToolTip = wrap_tip(tip)

    def paint(sender, e):
        try:
            g = e.Graphics
            g.AntiAlias = True
            c = color("help_icon")
            rect = drawing.RectangleF(1.5, 1.5, 13.0, 13.0)
            g.DrawEllipse(drawing.Pen(c, 1.2), rect)
            f = drawing.Font("Segoe UI", 7.5, drawing.FontStyle.Bold)
            try:
                size = g.MeasureString(f, "?")
                w, h = size.Width, size.Height
            except Exception:
                w, h = 5.0, 11.0
            cx, cy = rect.X + rect.Width / 2.0, rect.Y + rect.Height / 2.0
            g.DrawText(f, c, cx - w / 2.0, cy - h / 2.0, "?")
        except Exception:
            log_exc("help icon paint")

    d.Paint += paint
    return d


def _label(text, muted=False, bold=False, tip=None):
    lb = forms.Label()
    lb.Text = text
    lb.Font = font(bold=bold)
    lb.TextColor = color("text_muted" if muted else "text")
    lb.VerticalAlignment = forms.VerticalAlignment.Center
    if tip:
        lb.ToolTip = wrap_tip(tip)
    return lb


def _stepper(value):
    s = forms.NumericStepper()
    s.DecimalPlaces = 1
    s.MinValue = 0.1
    s.MaxValue = 100000.0
    s.Increment = 5.0
    s.Value = value
    s.Width = 80
    s.TextColor = color("text")
    s.BackgroundColor = color("bg_panel")
    return s


# ---- custom-drawn controls -----------------------------------------------------
# Native Eto buttons / radios / checkboxes ignore dark-theme colours in some states
# (black text, black radio dot). These are drawn by hand so every colour is ours.
class _Event:
    """Minimal event: supports `obj.Evt += handler`; handlers get (sender, e)."""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self, sender):
        for fn in list(self.handlers):
            fn(sender, None)


def _text_size(fnt, text):
    try:
        sz = fnt.MeasureString(text)
        return float(sz.Width), float(sz.Height)
    except Exception:
        return len(text) * 6.5, 14.0


def _hook_mouse(widget, d):
    def enter(sender, e):
        widget.hover = True
        d.Invalidate()

    def leave(sender, e):
        widget.hover = False
        d.Invalidate()

    def up(sender, e):
        try:
            if widget.enabled:
                widget.clicked()
        except Exception:
            log_exc(f"{type(widget).__name__} click")

    d.MouseEnter += enter
    d.MouseLeave += leave
    d.MouseUp += up
    try:
        d.Cursor = forms.Cursors.Pointer
    except Exception:
        pass


class FlatButton:
    def __init__(self, text, tip=None, bg="bg_panel", bg_hover="bg_hover"):
        self.text, self.bg, self.bg_hover = text, bg, bg_hover
        self.hover, self.enabled = False, True
        self.Click = _Event()
        self.font = font()
        w, _ = _text_size(self.font, text)
        d = forms.Drawable()
        d.Size = drawing.Size(int(w) + 28, TOKENS["control_height"])
        if tip:
            d.ToolTip = wrap_tip(tip)
        d.Paint += self._paint
        _hook_mouse(self, d)
        self.control = d

    @property
    def Enabled(self):
        return self.enabled

    @Enabled.setter
    def Enabled(self, value):
        self.enabled = bool(value)
        self.control.Invalidate()

    def clicked(self):
        self.Click.fire(self)

    def _paint(self, sender, e):
        try:
            g = e.Graphics
            w, h = float(self.control.Width), float(self.control.Height)
            bg = self.bg_hover if (self.hover and self.enabled) else self.bg
            if not self.enabled:
                bg = "bg_panel"
            g.FillRectangle(color(bg), 0.0, 0.0, w, h)
            g.DrawRectangle(color("border"), 0.0, 0.0, w - 1.0, h - 1.0)
            tw, th = _text_size(self.font, self.text)
            g.DrawText(self.font, color("text" if self.enabled else "text_disabled"),
                       (w - tw) / 2.0, (h - th) / 2.0, self.text)
        except Exception:
            log_exc("FlatButton paint")


class _Option:
    """One radio option or a checkbox, drawn by hand."""

    def __init__(self, owner, index, text, kind):
        self.owner, self.index, self.text, self.kind = owner, index, text, kind
        self.hover, self.enabled = False, True
        self.font = font()
        w, _ = _text_size(self.font, text)
        d = forms.Drawable()
        d.Size = drawing.Size(int(w) + 26, TOKENS["control_height"] - 4)
        d.Paint += self._paint
        _hook_mouse(self, d)
        self.control = d

    def clicked(self):
        self.owner._clicked(self.index)

    def _paint(self, sender, e):
        try:
            g = e.Graphics
            g.AntiAlias = True
            h = float(self.control.Height)
            g.FillRectangle(color("bg_window"), 0.0, 0.0, float(self.control.Width), h)
            on = self.owner._is_on(self.index)
            edge = color("accent" if on else ("text" if self.hover else "text_muted"))
            box = drawing.RectangleF(2.0, h / 2.0 - 6.5, 13.0, 13.0)
            if self.kind == "radio":
                g.DrawEllipse(drawing.Pen(edge, 1.2), box)
                if on:
                    g.FillEllipse(color("accent"), drawing.RectangleF(5.5, h / 2.0 - 3.0, 6.0, 6.0))
            else:
                if on:
                    g.FillRectangle(color("accent"), box)
                    pen = drawing.Pen(color("text"), 1.6)
                    g.DrawLine(pen, 5.0, h / 2.0, 7.5, h / 2.0 + 2.5)
                    g.DrawLine(pen, 7.5, h / 2.0 + 2.5, 12.0, h / 2.0 - 3.0)
                else:
                    g.DrawRectangle(drawing.Pen(edge, 1.2), box)
            tw, th = _text_size(self.font, self.text)
            g.DrawText(self.font, color("text"), 21.0, (h - th) / 2.0, self.text)
        except Exception:
            log_exc("option paint")


class OptionGroup:
    """Horizontal radio group; selected dot in accent colour."""

    def __init__(self, items, selected=0):
        self.items = list(items)
        self._index = selected
        self.SelectedIndexChanged = _Event()
        self.options = [_Option(self, i, t, "radio") for i, t in enumerate(self.items)]
        lay = forms.StackLayout()
        lay.Orientation = forms.Orientation.Horizontal
        lay.Spacing = TOKENS["space"]
        for op in self.options:
            lay.Items.Add(forms.StackLayoutItem(op.control, False))
        self.control = lay

    @property
    def SelectedIndex(self):
        return self._index

    def _is_on(self, i):
        return i == self._index

    def _clicked(self, i):
        if i != self._index:
            self._index = i
            for op in self.options:
                op.control.Invalidate()
            self.SelectedIndexChanged.fire(self)


class Toggle:
    """Checkbox drawn by hand; checked box filled with the accent colour."""

    def __init__(self, text, checked=True):
        self._checked = checked
        self.CheckedChanged = _Event()
        self.option = _Option(self, 0, text, "check")
        self.control = self.option.control

    @property
    def Checked(self):
        return self._checked

    def _is_on(self, i):
        return self._checked

    def _clicked(self, i):
        self._checked = not self._checked
        self.control.Invalidate()
        self.CheckedChanged.fire(self)


def _table_row(*items):
    """TableRow from controls or (control, expand); None = empty cell."""
    row = forms.TableRow()
    for it in items:
        ctrl, expand = it if isinstance(it, tuple) else (it, False)
        ctrl = getattr(ctrl, "control", ctrl)
        row.Cells.Add(forms.TableCell(ctrl, expand))
    return row


def _hrow(*items):
    """Single-row table; items are controls or (control, expand)."""
    t = forms.TableLayout()
    t.Spacing = drawing.Size(TOKENS["space"], 0)
    t.Rows.Add(_table_row(*items))
    return t


class WindowFrameUI:
    def __init__(self):
        self.version = __version__
        self.ids = []
        self.cancel_requested = False
        self.running = False
        self.is_open = False
        self._esc_handler = self._safe("Escape", self._on_escape)
        start_log_session()
        self._log_header()
        self._build()

    # ---- logging ----
    def _log_header(self):
        doc = sc.doc
        try:
            nsel = len(list(doc.Objects.GetSelectedObjects(False, False)))
        except Exception:
            nsel = -1
        log("START", f"{PLUGIN_NAME} v{__version__}")
        log("START", f"Rhino {Rhino.RhinoApp.Version} | Python {sys.version.split()[0]}")
        log("START", f"plugin dir {PLUGIN_DIR} (from {PLUGIN_DIR_SOURCE})")
        log("START", f"file {doc.Path or '(unsaved)'} | units {doc.ModelUnitSystem} | "
                     f"abs tol {doc.ModelAbsoluteTolerance} | ang tol {doc.ModelAngleToleranceDegrees} deg")
        log("START", f"selected objects at open: {nsel}")

    def _safe(self, name, fn):
        def handler(sender, e):
            try:
                fn(sender, e)
            except Exception as ex:
                log_exc(name)
                try:
                    self.report.add(f"Error in {name}: {ex} (see log)", "error")
                except Exception:
                    pass
        return handler

    # ---- layout ----
    def _build(self):
        f = forms.Form()
        self.form = f
        f.Title = PLUGIN_NAME
        f.Resizable = True
        f.Minimizable = False
        f.Maximizable = False
        f.ClientSize = drawing.Size(420, 560)
        f.BackgroundColor = color("bg_window")
        f.Padding = drawing.Padding(TOKENS["space"])
        try:
            f.Owner = Rhino.UI.RhinoEtoApp.MainWindow
        except Exception:
            log_exc("set owner")

        badge = _label(f"v{__version__}", muted=True)
        self.btn_log = FlatButton("Open log folder", "Open the logs folder of this plugin (one file per window session).")
        self.btn_log.Click += self._safe("Log", self.on_log)

        self.btn_select = FlatButton("Select", "Pick curves, surfaces, polysurfaces, extrusions, meshes, "
                                            "SubD or hatches. Only the outer edge of each is used.")
        self.btn_select.Click += self._safe("Select", self.on_select)
        self.lbl_count = _label("0 objects", muted=True)
        self.chk_join = Toggle("Close open outlines", True)
        self.chk_join.CheckedChanged += self._safe("Join", self.on_setting("Close open outlines", lambda: self.chk_join.Checked))

        self.st_side = _stepper(DEFAULT_WIDTH_MM)
        self.st_top = _stepper(DEFAULT_WIDTH_MM)
        self.st_bottom = _stepper(DEFAULT_WIDTH_MM)
        self.chk_link = Toggle("Link", True)
        self.rb_width = OptionGroup(WIDTH_MODES, 0)
        self.st_depth = _stepper(DEFAULT_DEPTH_MM)
        self.rb_depth = OptionGroup(DEPTH_MODES, 2)
        self.rb_output = OptionGroup(OUTPUT_MODES, 0)
        self.rb_group = OptionGroup(GROUP_MODES, 1)
        self.rb_group.SelectedIndexChanged += self._safe(
            "Grouping", self.on_setting("Grouping", lambda: GROUP_MODES[max(0, self.rb_group.SelectedIndex)]))

        self.st_side.ValueChanged += self._safe("Sides", self.on_side_changed)
        self.st_top.ValueChanged += self._safe("Top", self.on_setting("Top", lambda: self.st_top.Value))
        self.st_bottom.ValueChanged += self._safe("Bottom", self.on_setting("Bottom", lambda: self.st_bottom.Value))
        self.chk_link.CheckedChanged += self._safe("Link", self.on_link_changed)
        self.rb_width.SelectedIndexChanged += self._safe(
            "Offset", self.on_setting("Offset", lambda: WIDTH_MODES[max(0, self.rb_width.SelectedIndex)]))
        self.st_depth.ValueChanged += self._safe("Depth", self.on_setting("Depth", lambda: self.st_depth.Value))
        self.rb_depth.SelectedIndexChanged += self._safe(
            "Direction", self.on_setting("Direction", lambda: DEPTH_MODES[max(0, self.rb_depth.SelectedIndex)]))
        self.rb_output.SelectedIndexChanged += self._safe(
            "Output", self.on_setting("Output", lambda: OUTPUT_MODES[max(0, self.rb_output.SelectedIndex)]))

        self.btn_run = FlatButton("Run", "Build a frame on every selected outline. One Ctrl+Z undoes the whole run.",
                               bg="accent", bg_hover="accent_hover")
        self.btn_run.Click += self._safe("Run", self.on_run)
        self.btn_cancel = FlatButton("Cancel", "Stop the run. What is already built is kept.")
        self.btn_cancel.Enabled = False
        self.btn_cancel.Click += self._safe("Cancel", self.on_cancel)

        self.report = _Report()

        def mm():
            return _label("mm", muted=True)

        widths = forms.TableLayout()
        widths.Spacing = drawing.Size(TOKENS["space"], 4)
        link_help = _help_icon("Link: one value (Sides) for sides, top and bottom. "
                               "Unlink to set top and bottom separately.")
        widths.Rows.Add(_table_row(_label("Sides"), self.st_side, mm(), self.chk_link, link_help, (None, True)))
        widths.Rows.Add(_table_row(_label("Top"), self.st_top, mm(), None, None, (None, True)))
        widths.Rows.Add(_table_row(_label("Bottom"), self.st_bottom, mm(), None, None, (None, True)))

        main = forms.TableLayout()
        main.Spacing = drawing.Size(TOKENS["space"], TOKENS["space"])

        def add(ctrl, scale_height=False):
            row = _table_row((ctrl, True))
            row.ScaleHeight = scale_height
            main.Rows.Add(row)

        add(_hrow((None, True), badge))
        add(_label("Inputs", bold=True, tip="Objects whose outer edge becomes the frame outline. "
                                            "The active Rhino selection is also taken at open and at Run."))
        add(_hrow(self.btn_select, (self.lbl_count, True)))
        add(_hrow(self.chk_join, _help_icon("Touching curves are always joined first (clean junctions). Checked: an "
                                             "outline that is still open is closed (tiny gap: snapped; else a straight "
                                             "segment). Unchecked: it gets an open frame (ends cut square)."),
                  (None, True)))
        add(_label("Width", bold=True, tip="Frame member width, measured in the plane of the outline. "
                                           "Sides, top and bottom are recognised automatically."))
        add(widths)
        add(_hrow(_label("Offset"), self.rb_width,
                  _help_icon("Inside: outline is the outer face of the frame. Outside: outline is the inner face. "
                             "Center: frame straddles the outline."), (None, True)))
        add(_label("Depth", bold=True, tip="Frame thickness, perpendicular to the plane of the outline."))
        add(_hrow(_label("Depth"), self.st_depth, mm(), self.rb_depth,
                  _help_icon("Front: toward the active view camera (or world -Y / +Z when unclear). "
                             "Back: away from it. Center: half each side."), (None, True)))
        add(_label("Output", bold=True, tip="Solid: one single piece. Mitered 45: one piece per side, diagonal joints "
                                            "(45 deg with equal widths). Mitered 90: one piece per side, square joints "
                                            "(top and bottom run full width, sides stop between them)."))
        add(_hrow(self.rb_output, (None, True)))
        add(_hrow(self.rb_group, _help_icon("Ungroup: every piece on its own. Group: the pieces of each frame are grouped. "
                                             "Group all: same, plus one outer group around all frames of the run."),
                  (None, True)))
        add(_hrow(self.btn_run, self.btn_cancel, (None, True)))
        add(_label("Report", bold=True, tip="Short result of the last action. Full detail is in the log."))
        add(self.report.area, scale_height=True)
        add(_hrow(self.btn_log, (None, True)))

        f.Content = main
        f.Closed += self._safe("Closed", self.on_closed)
        self._apply_link()
        self._take_active_selection("open")

    # ---- window ----
    def show(self):
        self.form.Show()
        self.is_open = True
        log("START", "window shown")

    def bring_to_front(self):
        try:
            self.form.BringToFront()
            self.form.Focus()
        except Exception:
            log_exc("bring_to_front")

    def close(self):
        try:
            self.form.Close()
        except Exception:
            log_exc("close")

    def on_closed(self, sender, e):
        self.is_open = False
        log("CLOSE", "window closed")
        if sc.sticky.get(STICKY_KEY) is self:
            sc.sticky.pop(STICKY_KEY, None)
        stop_log_session()

    # ---- settings ----
    def read_settings(self):
        return Settings(
            width_side=float(self.st_side.Value),
            width_top=float(self.st_top.Value),
            width_bottom=float(self.st_bottom.Value),
            link=bool(self.chk_link.Checked),
            width_mode=WIDTH_MODES[max(0, self.rb_width.SelectedIndex)],
            depth=float(self.st_depth.Value),
            depth_mode=DEPTH_MODES[max(0, self.rb_depth.SelectedIndex)],
            output=OUTPUT_MODES[max(0, self.rb_output.SelectedIndex)],
            close_open=bool(self.chk_join.Checked),
            grouping=GROUP_MODES[max(0, self.rb_group.SelectedIndex)],
        )

    def on_setting(self, name, getter):
        def h(sender, e):
            log("SETTING", f"{name} = {getter()}")
        return h

    def _apply_link(self):
        linked = bool(self.chk_link.Checked)
        self.st_top.Enabled = not linked
        self.st_bottom.Enabled = not linked
        for st in (self.st_top, self.st_bottom):
            st.TextColor = color("text_disabled" if linked else "text")
        if linked:
            self.st_top.Value = self.st_side.Value
            self.st_bottom.Value = self.st_side.Value

    def on_side_changed(self, sender, e):
        log("SETTING", f"Sides = {self.st_side.Value}")
        if self.chk_link.Checked:
            self.st_top.Value = self.st_side.Value
            self.st_bottom.Value = self.st_side.Value

    def on_link_changed(self, sender, e):
        log("SETTING", f"Link = {self.chk_link.Checked}")
        self._apply_link()

    # ---- inputs ----
    def _describe_ids(self, ids):
        counts = {}
        for oid in ids:
            obj = sc.doc.Objects.FindId(oid)
            if obj is None:
                continue
            name = obj.Geometry.GetType().Name if obj.Geometry is not None else "?"
            counts[name] = counts.get(name, 0) + 1
        if not counts:
            return "0 objects"
        parts = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        return f"{len(ids)} objects ({parts})"

    def _set_ids(self, ids, source):
        self.ids = list(ids)
        self.lbl_count.Text = self._describe_ids(self.ids)
        log("SELECT", f"{len(self.ids)} ids from {source}: {self.lbl_count.Text}")

    def _take_active_selection(self, source):
        objs = list(sc.doc.Objects.GetSelectedObjects(False, False))
        if objs:
            self._set_ids([o.Id for o in objs], f"active selection ({source})")
            return True
        return False

    def on_select(self, sender, e):
        log("SELECT", "Select clicked")
        self.form.Visible = False
        try:
            go = Rhino.Input.Custom.GetObject()
            go.SetCommandPrompt("Select outlines (curves, surfaces, polysurfaces, meshes, SubD, hatches)")
            try:
                OT = rdo.ObjectType
                go.GeometryFilter = _enum_or(OT, OT.Curve, OT.Surface, OT.PolysrfFilter, OT.Mesh,
                                             OT.Extrusion, OT.SubD, OT.Hatch)
            except Exception:
                log_exc("geometry filter (selection left unfiltered)")
            go.SubObjectSelect = False
            go.GroupSelect = True
            go.EnablePreSelect(True, True)
            go.DeselectAllBeforePostSelect = False
            res = go.GetMultiple(1, 0)
            log("API", f"GetMultiple -> {res}, count={go.ObjectCount}")
            if res == Rhino.Input.GetResult.Object:
                self._set_ids([go.Object(i).ObjectId for i in range(go.ObjectCount)], "Select")
                self.report.add(f"Selected: {self.lbl_count.Text}", "text")
            else:
                self.report.add("Selection cancelled: previous inputs kept.", "text_muted")
        finally:
            self.form.Visible = True
            self.bring_to_front()

    def on_log(self, sender, e):
        log("SELECT", "Log clicked")
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            os.startfile(LOG_DIR)
        except Exception:
            log_exc("open log folder")
            self.report.add(f"Cannot open {LOG_DIR}", "error")

    def on_cancel(self, sender, e):
        if self.running:
            self.cancel_requested = True
            log("CANCEL", "Cancel clicked")

    # ---- run ----
    def _check_input(self, obj):
        if obj is None:
            raise SkipError("object no longer exists")
        if isinstance(obj, rdo.InstanceObject):
            raise SkipError("block instance not supported")
        lay = sc.doc.Layers[obj.Attributes.LayerIndex]
        if obj.IsLocked or (lay is not None and lay.IsLocked):
            raise SkipError("object or layer is locked")
        if obj.IsHidden or (lay is not None and not lay.IsVisible):
            raise SkipError("object or layer is hidden")
        geom = obj.Geometry
        if geom is None or not geom.IsValid:
            raise SkipError("invalid geometry")
        return geom

    def _output_layer(self):
        lay = sc.doc.Layers.FindName("Default")
        if lay is not None and not lay.IsDeleted and lay.IsVisible and not lay.IsLocked:
            return lay.Index, None
        idx = sc.doc.Layers.CurrentLayerIndex
        return idx, "Default layer missing, hidden or locked: current layer used"

    def _make_context(self):
        doc = sc.doc
        scale = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Millimeters, doc.ModelUnitSystem)
        ctx = RunContext(tol=doc.ModelAbsoluteTolerance, ang_tol=doc.ModelAngleToleranceRadians, scale=scale)
        try:
            view = doc.Views.ActiveView
            if view is not None:
                vp = view.ActiveViewport
                ctx.cam_parallel = bool(vp.IsParallelProjection)
                ctx.cam_dir = vp.CameraDirection
                ctx.cam_loc = vp.CameraLocation
        except Exception:
            log_exc("read active view camera")
        log("RUN", f"tol={ctx.tol} angtol={ctx.ang_tol:.6f} rad mm->doc scale={scale} "
                   f"camera parallel={ctx.cam_parallel}")
        return ctx

    def _progress(self, action, *args):
        try:
            sn = sc.doc.RuntimeSerialNumber
            if action == "show":
                try:
                    Rhino.UI.StatusBar.ShowProgressMeter(sn, 0, args[0], PLUGIN_NAME, True, True)
                except Exception:
                    Rhino.UI.StatusBar.ShowProgressMeter(0, args[0], PLUGIN_NAME, True, True)
            elif action == "update":
                try:
                    Rhino.UI.StatusBar.UpdateProgressMeter(sn, args[0], True)
                except Exception:
                    Rhino.UI.StatusBar.UpdateProgressMeter(args[0], True)
            else:
                try:
                    Rhino.UI.StatusBar.HideProgressMeter(sn)
                except Exception:
                    Rhino.UI.StatusBar.HideProgressMeter()
        except Exception:
            pass

    def _on_escape(self, sender, e):
        # Only an Escape pressed while this run is going counts (sc.escape_test kept stale presses).
        if self.running and not self.cancel_requested:
            self.cancel_requested = True
            log("CANCEL", "Escape pressed during run")

    def _cancelled(self):
        try:
            Rhino.RhinoApp.Wait()
        except Exception:
            pass
        return self.cancel_requested

    def on_run(self, sender, e):
        if self.running:
            return
        if not self._take_active_selection("run") and not self.ids:
            self.report.add("Nothing selected. Use Select or pre-select objects.", "warning")
            log("RUN", "no input")
            return
        settings = self.read_settings()
        log("RUN", f"inputs={len(self.ids)} | {settings.describe()}")
        self.report.clear()
        if len(self.ids) > BIG_RUN_WARNING:
            self.report.add(f"{len(self.ids)} objects, may take a while.", "warning")
        ctx = self._make_context()
        layer_idx, layer_note = self._output_layer()
        log("RUN", f"output layer index={layer_idx} note={layer_note}")

        self.running = True
        self.cancel_requested = False
        self.btn_run.Enabled = False
        self.btn_cancel.Enabled = True
        t_start = time.time()
        created, failed, notes = [], {}, []
        frames = 0
        cancelled = False
        undo_sn = 0
        sc.doc.Views.RedrawEnabled = False
        self._progress("show", len(self.ids))
        esc_hooked = False
        try:
            Rhino.RhinoApp.EscapeKeyPressed += self._esc_handler
            esc_hooked = True
        except Exception:
            log_exc("hook Escape (Cancel button still works)")
        try:
            undo_sn = sc.doc.BeginUndoRecord(f"{PLUGIN_NAME} Run")
            log("UNDO", f"begin sn={undo_sn}")
            items = []
            for oid in self.ids:
                try:
                    items.append((oid, self._check_input(sc.doc.Objects.FindId(oid))))
                except SkipError as ex:
                    failed[oid] = str(ex)
                    log("SKIP", f"{oid}: {ex}")
            records, skips = collect_contours(items, settings, ctx.tol)
            failed.update(skips)
            self._progress("hide")
            self._progress("show", max(1, len(records)))
            for k, rec in enumerate(records):
                if self._cancelled():
                    cancelled = True
                    break
                self._progress("update", k + 1)
                tag = "+".join(str(x) for x in rec["keys"])
                try:
                    breps, cnotes = frame_for_contour(rec["crv"], settings, ctx)
                except FrameError as ex:
                    for oid in rec["keys"]:
                        failed[oid] = str(ex)
                    log("SKIP", f"{tag}: {ex}")
                    continue
                except Exception as ex:
                    for oid in rec["keys"]:
                        failed[oid] = f"unexpected error: {ex}"
                    log_exc(f"{tag} outline")
                    continue
                notes.extend(cnotes)
                group_idx = -1
                if settings.grouping != "Ungroup" and len(breps) > 1:
                    group_idx = sc.doc.Groups.Add()
                    log("API", f"Groups.Add -> {group_idx} ({len(breps)} pieces of one frame)")
                for br in breps:
                    attrs = sc.doc.CreateDefaultAttributes()
                    attrs.LayerIndex = layer_idx
                    if group_idx >= 0:
                        attrs.AddToGroup(group_idx)
                    nid = sc.doc.Objects.AddBrep(br, attrs)
                    log("API", f"AddBrep -> {nid}")
                    if nid != System.Guid.Empty:
                        created.append(nid)
                frames += 1
                log("ITEM", f"{tag} ({rec['kind']}): {len(breps)} object(s) created")
            if settings.grouping == "Group all" and len(created) > 1:
                gall = sc.doc.Groups.Add()
                n_in = sum(1 for oid in created if sc.doc.Groups.AddToGroup(gall, oid))
                log("API", f"Group all: Groups.Add -> {gall}, {n_in}/{len(created)} object(s) added (outer group)")
        finally:
            if esc_hooked:
                try:
                    Rhino.RhinoApp.EscapeKeyPressed -= self._esc_handler
                except Exception:
                    log_exc("unhook Escape")
            if undo_sn:
                sc.doc.EndUndoRecord(undo_sn)
                log("UNDO", f"end sn={undo_sn}")
            self._progress("hide")
            sc.doc.Views.RedrawEnabled = True
            self.running = False
            self.btn_run.Enabled = True
            self.btn_cancel.Enabled = False

        if failed:
            sc.doc.Objects.UnselectAll()
            for oid in failed:
                sc.doc.Objects.Select(oid)
        sc.doc.Views.Redraw()
        for oid in self.ids:
            obj = sc.doc.Objects.FindId(oid)
            kept = obj.Geometry.GetType().Name if obj is not None and obj.Geometry is not None else "MISSING"
            log("ITEM", f"input {oid} after run: {kept}")
        dur = time.time() - t_start
        log("DONE", f"inputs={len(self.ids)} frames={frames} objects={len(created)} failed={len(failed)} "
                    f"cancelled={cancelled} in {dur:.2f} s")

        kind = "success" if not failed else ("warning" if created else "error")
        self.report.add(f"{frames} frame(s) built, {len(created)} object(s) created, {len(failed)} input(s) failed "
                        f"({dur:.1f} s)", kind)
        if cancelled:
            self.report.add("Cancelled: what was built is kept.", "warning")
        reasons = {}
        for r in failed.values():
            reasons[r] = reasons.get(r, 0) + 1
        for r, c in reasons.items():
            self.report.add(f"  {c} x {r}", "error")
        if failed:
            self.report.add("Failed inputs are now selected.", "text_muted")
        for nt in sorted(set(notes + ([layer_note] if layer_note and created else []))):
            self.report.add(f"Note: {nt}", "warning")


# =============================================================================
# ENTRY POINT
# =============================================================================
def main():
    if Rhino.RhinoApp.ExeVersion < 8 or sys.version_info[0] < 3:
        msg = "WindowFrame_CC needs Rhino 8+ with Python 3. Install Rhino 8+."
        Rhino.RhinoApp.WriteLine(msg)
        try:
            Rhino.UI.Dialogs.ShowMessage(msg, PLUGIN_NAME)
        except Exception:
            pass
        return
    existing = sc.sticky.get(STICKY_KEY)
    if existing is not None and getattr(existing, "is_open", False):
        if getattr(existing, "version", None) == __version__:
            existing.bring_to_front()
            return
        existing.close()
    ui = WindowFrameUI()
    sc.sticky[STICKY_KEY] = ui
    ui.show()


try:
    main()
except Exception:
    log_exc("main")
    Rhino.RhinoApp.WriteLine(f"{PLUGIN_NAME}: startup error, see {LOG_DIR}")
