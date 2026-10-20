"""Import the settings of a MakerWorld / Bambu .3mf into a Kobra X process preset, SAFELY.

Usage:
  python -I import_3mf.py file.3mf               # writes preset + report to ./imported_presets/
  python -I import_3mf.py file.3mf --install     # also copies the preset into the slicer (slicer must be closed)
  python -I import_3mf.py file.3mf --install --force   # overwrite a preset with the same name (a backup is kept)

Choosing the starting (reference) preset:
  python -I import_3mf.py --list-refs                  # show the Kobra X presets you can start from
  python -I import_3mf.py file.3mf --quality high      # same layer height, 'High Quality' variant (slower, nicer)
  python -I import_3mf.py file.3mf --layer 0.16        # finer layer height (shell layer counts are scaled)
  python -I import_3mf.py file.3mf -r "Your preset"    # any system or user preset, by exact name
  python -I import_3mf.py file.3mf --project           # also write a copy of the 3mf re-centred on your bed

Safety rules (the machine comes before the maker):
  * only a WHITELIST of quality/structure options is imported (walls, infill, supports, brim, ironing...);
    G-code, retraction, z-hop, temperatures, bed/plate, fans, prime tower, etc. are NEVER imported;
  * speeds, accelerations and jerk never exceed the reference Kobra X preset or the machine limits;
  * layer height and line widths are kept within nozzle/machine limits;
  * keys unknown to the Kobra X preset are discarded;
  * the maker's filament and printer are ignored: you keep using your own profiles;
  * warning if the object does not fit the 260x260x260 bed.
No slicer file is overwritten without --force.
"""
import argparse, datetime, glob, json, math, os, re, shutil, subprocess, sys, time, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # needed with python -I
from extract_overrides import APPDATA, DEFAULT_REF, load_ref, norm  # noqa: E402

MAX_BYTES = 300 * 1024 * 1024  # a single file inside the 3mf larger than this is ignored (zip bomb)
OUT = os.path.join(HERE, "imported_presets")            # generated presets and reports
BACKUP = os.path.join(HERE, "overwritten_presets_backup")  # copies made by --force

# ---- machine-specific settings: change these for another printer -------------------------------
MACHINE = "Anycubic Kobra X 0.4 nozzle"
ORCA = os.path.expandvars(r"%APPDATA%\OrcaSlicer")
ORCA_SYS = r"C:\Program Files\OrcaSlicer\resources\profiles\Anycubic"
NAME_SUFFIX = " @AC KX"   # appended to the preset name
# -------------------------------------------------------------------------------------------------

# supported slicers: same preset structure (Anycubic Slicer Next is Orca-based), different folders
SLICERS = {
    "anycubic": {"user": os.path.join(APPDATA, "user", "*", "process"),
                 "system": [os.path.join(APPDATA, "system", "*", "process")],
                 "machine": os.path.join(APPDATA, "system", "*", "machine"), "proc": "anycubic"},
    "orca": {"user": os.path.join(ORCA, "user", "*", "process"),
             "system": [os.path.join(ORCA_SYS, "process")],
             "machine": os.path.join(ORCA_SYS, "machine"), "proc": "orca"},
}
# Bambu Studio key names -> Orca/Anycubic (checked on a 3mf re-saved by Anycubic Slicer)
ALIAS = {"ironing_direction": "ironing_angle"}
VALUE_ALIAS = {"ironing_pattern": {"zig-zag": "rectilinear"}}
IRONING_FAMILY = ["ironing_flow", "ironing_spacing", "ironing_inset", "ironing_pattern", "ironing_speed",
                  "ironing_direction"]

# options that cannot harm the machine: imported if the key exists in the reference preset
SAFE = {
    "wall_loops", "top_shell_layers", "bottom_shell_layers", "top_shell_thickness", "bottom_shell_thickness",
    "sparse_infill_density", "sparse_infill_pattern", "internal_solid_infill_pattern", "top_surface_pattern",
    "bottom_surface_pattern", "infill_direction", "infill_wall_overlap", "wall_generator", "wall_sequence",
    "seam_position", "detect_thin_wall", "detect_overhang_wall", "ensure_vertical_shell_thickness",
    "enable_support", "support_type", "support_style", "support_threshold_angle", "support_on_build_plate_only",
    "support_base_pattern", "support_base_pattern_spacing", "support_interface_top_layers",
    "support_interface_bottom_layers", "support_interface_spacing", "support_interface_pattern",
    "support_top_z_distance", "support_bottom_z_distance", "support_object_xy_distance", "support_angle",
    "tree_support_branch_angle", "tree_support_branch_diameter", "tree_support_branch_distance",
    "tree_support_wall_count", "tree_support_tip_diameter",
    "raft_layers", "raft_first_layer_expansion", "raft_contact_distance", "brim_type", "brim_width",
    "brim_object_gap", "skirt_loops", "skirt_distance", "skirt_height",
    "ironing_type", "ironing_flow", "ironing_spacing", "ironing_pattern", "ironing_direction", "ironing_inset",
    "ironing_angle", "bridge_flow", "bridge_angle", "elefant_foot_compensation", "xy_hole_compensation", "xy_contour_compensation",
    "enable_overhang_speed", "only_one_wall_top", "only_one_wall_first_layer", "spiral_mode", "support_critical_regions_only",
}
# valid numeric ranges (key -> (min, max)); outside the range = limited
BOUNDS = {
    "wall_loops": (1, 10), "top_shell_layers": (0, 20), "bottom_shell_layers": (0, 20),
    "top_shell_thickness": (0, 5), "bottom_shell_thickness": (0, 5),
    "sparse_infill_density": (0, 100), "brim_width": (0, 20), "brim_object_gap": (0, 2),
    "skirt_loops": (0, 10), "skirt_distance": (0, 30), "skirt_height": (1, 10),
    "raft_layers": (0, 10), "raft_first_layer_expansion": (0, 10), "raft_contact_distance": (0, 1),
    "support_threshold_angle": (0, 90), "support_base_pattern_spacing": (0.5, 20),
    "support_top_z_distance": (0, 1), "support_bottom_z_distance": (0, 1), "support_object_xy_distance": (0, 5),
    "tree_support_branch_diameter": (1, 15), "tree_support_branch_distance": (1, 15),
    "bridge_flow": (0.5, 1.2), "elefant_foot_compensation": (0, 1.5),
    "xy_hole_compensation": (-1, 1), "xy_contour_compensation": (-1, 1),
    "ironing_flow": (0, 30), "ironing_spacing": (0.05, 1), "ironing_inset": (0, 5),
    "infill_wall_overlap": (0, 50), "support_interface_top_layers": (0, 10),
    "support_interface_bottom_layers": (0, 10), "support_interface_spacing": (0, 5),
    "tree_support_wall_count": (0, 5), "tree_support_tip_diameter": (0.5, 10),
    "bridge_angle": (0, 180), "ironing_angle": (-1, 360), "support_angle": (0, 90), "tree_support_branch_angle": (0, 90),
}
ENUM = re.compile(r"^[\w .%()-]{1,40}$")  # allowed text values (enums): no strange symbols
SPEED = re.compile(r"^(?!enable_).*_speed$|^(speed|travel_speed_z)$")
ACCEL = re.compile(r"_acceleration$")
JERK = re.compile(r"_jerk$")
LINEW = re.compile(r"line_width$")


def num(v):
    """'25%' -> 25.0 ; '0.4' -> 0.4 ; anything else (text, nan, inf, list) -> None"""
    if isinstance(norm(v), (list, dict)):
        return None
    try:
        x = float(str(norm(v)).rstrip("%"))
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def fmt(x, pct=False):
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s + ("%" if pct else "")


def machine_limits(machine_dir):
    found = glob.glob(os.path.join(machine_dir, MACHINE + ".json"))
    if not found:
        sys.exit(f"Machine preset '{MACHINE}' not found in {machine_dir}: is the slicer installed?")
    d = os.path.dirname(found[0])

    def flat(n):
        j = json.load(open(os.path.join(d, n + ".json"), encoding="utf-8"))
        b = flat(j["inherits"]) if j.get("inherits") else {}
        b.update(j)
        return b
    m = flat(MACHINE)
    g = lambda k: float(norm(m[k]))
    xs = [float(a.split("x")[0]) for a in m["printable_area"]]
    ys = [float(a.split("x")[1]) for a in m["printable_area"]]
    return {
        "speed": min(g("machine_max_speed_x"), g("machine_max_speed_y")),
        "accel": g("machine_max_acceleration_extruding"),
        "jerk": min(g("machine_max_jerk_x"), g("machine_max_jerk_y")),
        "lh_min": g("min_layer_height"), "lh_max": g("max_layer_height"),
        "nozzle": g("nozzle_diameter"),
        "bed_x": max(xs) - min(xs), "bed_y": max(ys) - min(ys), "bed_z": g("printable_height"),
    }


def classify(k, v, ref, lim):
    """-> (status, final_value|None, reason). status: applied|limited|same|discarded"""
    if k not in ref:
        return "discarded", None, "key missing from the Kobra X preset"
    refv = norm(ref[k])
    if k in ("layer_height", "initial_layer_print_height"):
        n = num(v)
        if n is None:
            return "discarded", None, "non-numeric value"
        r = min(max(n, lim["lh_min"]), lim["lh_max"])
        out, why = fmt(r), f"outside machine range {lim['lh_min']}-{lim['lh_max']} mm" if r != n else ""
    elif SPEED.search(k) or ACCEL.search(k) or JERK.search(k):
        n, rn = num(v), num(refv)
        pct, refpct = str(norm(v)).endswith("%"), str(refv).endswith("%")
        if n is None or rn is None:
            return "discarded", None, "non-numeric value"
        if pct != refpct:
            return "discarded", None, "type (% / absolute) differs from the Kobra X preset"
        if n < 0 or (n == 0 and rn != 0):
            return "discarded", None, "value <= 0 (0 means 'no limit' in the slicer)"
        cap = float("inf") if pct else lim["speed"] if SPEED.search(k) else lim["accel"] if ACCEL.search(k) else lim["jerk"]
        r = min(n, rn, cap)  # never faster than the tuned preset
        out, why = fmt(r, pct), (f"limited to the Kobra X preset value ({fmt(rn, pct)})" if r < n else "")
    elif LINEW.search(k):
        s = str(norm(v))
        n = num(v)
        if n is None:
            return "discarded", None, "non-numeric value"
        lo, hi = (60, 160) if s.endswith("%") else (lim["nozzle"] * 0.6, lim["nozzle"] * 1.6)
        r = min(max(n, lo), hi)
        out, why = fmt(r, s.endswith("%")), (f"limited to {fmt(lo)}-{fmt(hi)}" if r != n else "")
    elif k in SAFE:
        n = num(v)
        if k in BOUNDS:
            if n is None:
                return "discarded", None, "non-numeric value"
            lo, hi = BOUNDS[k]
            r = min(max(n, lo), hi)
            pct = str(norm(v)).endswith("%")
            out, why = fmt(r, pct), (f"limited to {lo}-{hi}" if r != n else "")
        else:
            s = norm(v)
            if s is None or isinstance(s, (list, dict)) or not ENUM.match(str(s)):
                return "discarded", None, "missing or invalid value"
            if num(refv) is not None and n is None:
                return "discarded", None, "a number was expected"
            out, why = str(s), ""
    else:
        return "discarded", None, "not in the safe list (machine/filament/G-code)"
    if why:
        return "limited", out, why
    if out == str(refv) or (num(out) is not None and num(refv) is not None and num(out) == num(refv)
                            and out.endswith("%") == str(refv).endswith("%")):
        return "same", out, ""
    return "applied", out, why


def z_height(z):
    """Largest dimensions (mm) from the mesh vertices: per object, the three extents sorted ascending,
    then the maximum over objects for each position. Ignores rotations and scales stored in the project."""
    best = [0.0, 0.0, 0.0]
    for n in z.namelist():
        if n.endswith(".model"):
            if z.getinfo(n).file_size > MAX_BYTES:
                continue
            for ob in re.findall(rb"<object\b.*?</object>", z.read(n), re.S):
                pts = [[], [], []]
                for vm in re.finditer(rb'<vertex x="([-0-9.eE+]+)" y="([-0-9.eE+]+)" z="([-0-9.eE+]+)"', ob):
                    try:
                        v = [float(g) for g in vm.groups()]
                    except ValueError:
                        continue
                    if all(math.isfinite(x) for x in v):
                        for i in range(3):
                            pts[i].append(v[i])
                if pts[0]:  # sorted extents: the smallest is the height if the part lies on its largest face
                    ext = sorted(max(p) - min(p) for p in pts)
                    best = [max(b, e) for b, e in zip(best, ext)]
    return best


def fit_warnings(z, lim):
    w = []
    for n in z.namelist():
        if re.search(r"plate_\d+\.json$", n):
            try:
                b = json.loads(z.read(n)).get("bbox_all")
            except (ValueError, AttributeError):
                continue
            if isinstance(b, list) and len(b) >= 4 and all(isinstance(x, (int, float)) for x in b[:4]):
                dx, dy = b[2] - b[0], b[3] - b[1]
                if max(dx, dy) > lim["bed_x"] - 4 or min(dx, dy) > lim["bed_y"] - 4:
                    w.append(f"{os.path.basename(n)}: footprint {dx:.0f}x{dy:.0f} mm, bed "
                             f"{lim['bed_x']:.0f}x{lim['bed_y']:.0f}: **does not fit** (or must be scaled down)")
    h = z_height(z)  # [smallest, middle, largest] dimension among the objects, whatever the orientation
    if h[0] > lim["bed_z"] - 2:
        w.append(f"smallest dimension {h[0]:.0f} mm, maximum height {lim['bed_z']:.0f} mm: **too tall**")
    elif h[1] > min(lim["bed_x"], lim["bed_y"]) - 4:
        w.append(f"two dimensions {h[1]:.0f} and {h[2]:.0f} mm: does not fit the {lim['bed_x']:.0f}x{lim['bed_y']:.0f} "
                 f"bed in any orientation (or reduce the scale)")
    return w, h


def nearest_standard(lh, dirs):
    """Layer height (mm) of the Standard preset for MACHINE closest to lh (ties: the finer one). None if none found."""
    heights = set()
    for d in dirs:
        for p in glob.glob(os.path.join(d, f"*mm Standard @{MACHINE}.json")):
            m = re.match(r"(\d+\.\d+)mm Standard @", os.path.basename(p))
            if m:
                heights.add(float(m.group(1)))
    return min(heights, key=lambda c: (abs(c - lh), c)) if heights else None


def list_refs(S):
    """Print the process presets usable as a reference (-r) for the target machine."""
    dirs = [S["user"], *S["system"]]
    rows = []
    for d in dirs:
        kind = "user" if d == S["user"] else "system"
        for p in sorted(glob.glob(os.path.join(d, "*.json"))):
            n = os.path.splitext(os.path.basename(p))[0]
            if kind == "system" and not n.endswith("@" + MACHINE):
                continue
            r = load_ref(n, dirs)
            if r and num(r.get("layer_height")) is not None:
                g = lambda k: str(norm(r.get(k, "-")))
                rows.append((kind, n, g("layer_height"), g("outer_wall_speed"), g("default_acceleration"),
                             g("sparse_infill_pattern"), f"{g('top_shell_layers')}/{g('bottom_shell_layers')}"))
    rows.sort(key=lambda x: (x[0] != "system", float(x[2]), x[1]))
    print(f"{'kind':7s}{'layer':>6s}{'outer':>7s}{'accel':>7s}  {'infill':12s}{'top/bot':>8s}  name")
    for kind, n, lh, ow, ac, pat, sh in rows:
        print(f"{kind:7s}{lh:>6s}{ow:>7s}{ac:>7s}  {pat:12s}{sh:>8s}  {n}")
    print('\nUse the name with -r "...", or --quality high / --layer 0.16 to let the script pick a system preset.')


def bed_size(ps):
    """Bed width/height (mm) of the maker's printer, from printable_area (default: Bambu 256x256)."""
    try:
        pts = [tuple(float(c) for c in str(a).split("x")) for a in ps["printable_area"]]
        return max(x for x, _ in pts) - min(x for x, _ in pts), max(y for _, y in pts) - min(y for _, y in pts)
    except (KeyError, ValueError, TypeError):
        return 256.0, 256.0


def recenter_project(src, dst, sw, sh, dw, dh):
    """Copy the 3mf to dst moving every plate item from the maker's bed (sw x sh) to the centre of yours (dw x dh).
    Only the placement transforms are touched: meshes and settings stay exactly as they are.
    Plates are laid out side by side with a 20% gap (Bambu / Orca convention)."""
    psx, psy, pdx, pdy = sw * 1.2, sh * 1.2, dw * 1.2, dh * 1.2
    ox, oy = (dw - sw) / 2, (dh - sh) / 2
    moved = [0]

    def newxy(x, y):
        c, r = math.floor(x / psx), math.floor(y / psy)
        return c * pdx + (x - c * psx) + ox, r * pdy + (y - r * psy) + oy

    def fix(m):
        v = m.group(2).split()
        if len(v) != 12:
            return m.group(0)
        try:
            x, y = float(v[9]), float(v[10])
        except ValueError:
            return m.group(0)
        v[9], v[10] = (fmt(c) for c in newxy(x, y))
        moved[0] += 1
        return m.group(1) + " ".join(v) + m.group(3)
    # only the build items (plate placement): the "assemble" view in model_settings.config is unrelated to the plate
    pat = {"3D/3dmodel.model": re.compile(r'(<item\b[^>]*?\btransform=")([^"]+)(")')}
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for it in zi.infolist():
            data = zi.read(it.filename)
            if it.filename in pat and len(data) < MAX_BYTES:
                data = pat[it.filename].sub(fix, data.decode("utf-8")).encode("utf-8")
            zo.writestr(it, data)
    return moved[0]


def slicer_running(word):
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    return word in out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?")
    ap.add_argument("-r", "--ref", default=None,
                    help="reference process preset, exact name, system or user "
                         "(default: '<maker layer height>mm Standard', see --quality / --layer)")
    ap.add_argument("--quality", choices=("auto", "standard", "high"), default="auto",
                    help="auto: 'High Quality' only if the maker's process says so; high: use the High Quality "
                         "variant of the layer height when it exists; standard: always Standard")
    ap.add_argument("--layer", type=float, help="start from another layer height (mm), e.g. 0.16; "
                                                "shell layer counts are scaled to keep the same thickness")
    ap.add_argument("--list-refs", action="store_true", help="list the presets you can start from and exit")
    ap.add_argument("--project", action="store_true",
                    help="also write a copy of the 3mf re-centred on your bed (settings untouched)")
    ap.add_argument("-s", "--slicer", choices=sorted(SLICERS), default="anycubic",
                    help="target slicer: system presets and user folder (default: anycubic)")
    ap.add_argument("-n", "--name", help="preset name (default: file name)")
    ap.add_argument("--install", action="store_true", help="copy the preset into the slicer (it must be closed)")
    ap.add_argument("--force", action="store_true", help="overwrite a preset that is already installed (backup kept)")
    a = ap.parse_args()
    S = SLICERS[a.slicer]
    if a.list_refs:
        list_refs(S)
        return
    if not a.file:
        ap.error("the following arguments are required: file")

    try:
        z = zipfile.ZipFile(a.file)
        if z.getinfo("Metadata/project_settings.config").file_size > MAX_BYTES:
            sys.exit("project_settings.config is too large: suspicious file.")
        ps = json.loads(z.read("Metadata/project_settings.config"))
        if not isinstance(ps, dict):
            raise ValueError
    except KeyError:
        sys.exit("No project_settings.config: nothing to import (geometry only).")
    except (zipfile.BadZipFile, ValueError, OSError) as e:
        sys.exit(f"Invalid file ({a.file}): {e or 'unrecognised format'}")
    dirs = [S["user"], *S["system"]]
    auto_ref = a.ref is None
    ref_note = ""
    if not auto_ref and (a.layer or a.quality != "auto"):
        print("NOTE: -r was given, so --layer / --quality are ignored.")
    layer_override = False  # True when no preset exists at the maker's layer height: the height is kept as an override
    if auto_ref:  # the maker's system-preset layer height is NOT in the overrides: the reference carries it
        a.ref = DEFAULT_REF
        ref_note = " (default: the maker's layer height could not be read)"
        try:
            lh = a.layer or float(norm(ps["layer_height"]))
            hq_maker = bool(re.search(r"Fine|High Quality|Optimal", str(ps.get("print_settings_id", ""))))
            want_hq = a.quality == "high" or (a.quality == "auto" and hq_maker)
            var = (["High Quality"] if want_hq else []) + ["Standard"]
            found = next((c for c in (f"{lh:.2f}mm {v} @{MACHINE}" for v in var) if load_ref(c, dirs)), None)
            if found:
                a.ref = found
                if a.quality == "high" and "High Quality" not in a.ref:
                    ref_note = f" (no High Quality preset at {lh:.2f} mm, Standard used)"
                elif a.layer:
                    ref_note = f" (layer height chosen with --layer {a.layer})"
                elif a.quality == "high":
                    ref_note = " (High Quality variant requested)"
                else:
                    ref_note = " (chosen from the maker's layer height)"
            else:  # no preset at this height (e.g. 0.15 or 0.10): nearest Standard, keep the real height as an override
                near = nearest_standard(lh, dirs)
                if near is not None:
                    a.ref = f"{near:.2f}mm Standard @{MACHINE}"
                    layer_override = not a.layer
                    ref_note = (f" (no preset at {lh:.2f} mm: nearest is {near:.2f} mm Standard"
                                + ("; the maker's layer height is kept" if layer_override else "") + ")")
        except (KeyError, ValueError, TypeError):
            pass
    ref = load_ref(a.ref, dirs)
    if not ref:
        sys.exit(f"Reference preset '{a.ref}' not found ({a.slicer}). Use --list-refs to see the available ones.")
    lim = machine_limits(S["machine"])
    try:
        maker_lh = float(norm(ps["layer_height"]))
    except (KeyError, ValueError, TypeError):
        maker_lh = None
    ref_lh = num(ref.get("layer_height"))
    # shell layer counts are tuned for the maker's layer height: keep the same thickness on another one
    scale = (maker_lh / ref_lh if maker_lh and ref_lh and abs(maker_lh - ref_lh) > 1e-6 and not layer_override
             else 1.0)  # with the layer height kept as an override the shell counts already fit it

    diff = ps.get("different_settings_to_system", [])
    keys = [k for k in (diff[0].split(";") if isinstance(diff, list) and diff and isinstance(diff[0], str) else [])
            if k]
    extra = {}  # key -> note: options imported even if equal to the Bambu default
    if layer_override and "layer_height" not in keys:
        keys.append("layer_height")
        extra["layer_height"] = "no preset at this height: maker's value kept on the nearest preset"
    if str(norm(ps.get("ironing_type", "no ironing"))) != "no ironing":
        for k in IRONING_FAMILY:  # default Bambu ironing parameters do not appear among the overrides
            if k not in keys and k in ps:
                keys.append(k)
                extra[k] = "Bambu default: imported because ironing is on"
    stem = os.path.splitext(os.path.basename(a.file))[0]
    base = a.name or stem.replace("+", " ")[:40] + NAME_SUFFIX
    name = re.sub(r"[^\w .+@-]", "", base).strip(" .")  # no / \ : .. in the file name
    if not name or name == a.ref:
        sys.exit("Invalid preset name, or same as the reference preset: use -n with another name.")
    if os.path.exists(os.path.join(OUT, name + ".json")):  # never overwrite a previous import
        name += " " + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    rows, applied = [], {}
    for k in keys:
        t = ALIAS[k] if k not in ref and ALIAS.get(k) in ref else k  # key name in the target preset
        v = ps.get(k)
        if k in VALUE_ALIAS and norm(v) in VALUE_ALIAS[k]:
            v = VALUE_ALIAS[k][norm(v)]
        if scale != 1.0 and k in ("top_shell_layers", "bottom_shell_layers") and num(v) is not None:
            v = str(max(1, round(num(v) * scale)))
            extra[k] = f"scaled x{scale:.2f}: maker {maker_lh} mm -> {ref_lh} mm layers"
        st, val, why = classify(t, v, ref, lim)
        why = "; ".join(x for x in (why, extra.get(k, ""), f"key renamed to {t}" if t != k else "") if x)
        rows.append((k, norm(ps.get(k)), st, val, why))
        if st in ("applied", "limited"):
            applied[t] = val
    warn, h = fit_warnings(z, lim)
    custom = not keys and "- Copy" in str(ps.get("print_settings_id", ""))

    # user preset: inherits from the system one; if the reference is a user preset, copy its own keys
    ref_user = glob.glob(os.path.join(S["user"], a.ref + ".json"))
    if ref_user:
        out = json.load(open(ref_user[0], encoding="utf-8"))
        out.setdefault("inherits", a.ref)
    else:
        out = {"inherits": a.ref, "version": ref.get("version", "")}
    out.update(applied)
    out.update({"name": name, "print_settings_id": name, "from": "User", "is_custom_defined": "0",
                "print_timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    info = f"sync_info = create\nuser_id = \nsetting_id = \nbase_id = {out['inherits']}\nupdated_time = {int(time.time())}\n"

    os.makedirs(OUT, exist_ok=True)
    jp = os.path.join(OUT, name + ".json")
    json.dump(out, open(jp, "w", encoding="utf-8", newline="\n"), indent=4, ensure_ascii=False, sort_keys=True)
    open(os.path.join(OUT, name + ".info"), "w", encoding="utf-8", newline="\n").write(info)

    proj_line = []
    if a.project:
        sw, sh = bed_size(ps)
        pj = os.path.join(OUT, name + ".3mf")
        n_moved = recenter_project(a.file, pj, sw, sh, lim["bed_x"], lim["bed_y"])
        proj_line = [f"- Re-centred copy of the project: `{os.path.basename(pj)}` "
                     f"(maker's bed {sw:.0f}x{sh:.0f} -> {lim['bed_x']:.0f}x{lim['bed_y']:.0f}, {n_moved} placements moved; "
                     f"meshes and settings untouched, the slicer will still ask to switch printer on open)"]

    # report
    L = [f"# Import {os.path.basename(a.file)}", "",
         f"- Maker's printer: {ps.get('printer_settings_id')}",
         f"- Maker's process: {ps.get('print_settings_id')}",
         f"- Kobra X reference: `{a.ref}`" + (ref_note if auto_ref else " (chosen with -r)"),
         f"- Target slicer: {a.slicer}", f"- Generated preset: `{name}`", *proj_line, "",
         f"Machine limits: speed {lim['speed']:.0f} mm/s, accel {lim['accel']:.0f} mm/s², jerk {lim['jerk']:.0f}, "
         f"layer {lim['lh_min']}-{lim['lh_max']} mm, bed {lim['bed_x']:.0f}x{lim['bed_y']:.0f}x{lim['bed_z']:.0f} mm.", ""]
    if warn:
        L += ["## ⚠ Warnings", *[f"- {x}" for x in warn], ""]
    if custom:
        L += ["## ⚠ Custom maker preset",
              "- The process is a modified copy (`- Copy`): the differences are not tracked, nothing was imported. "
              "Check by hand or use `extract_overrides.py`.", ""]
    L += [f"## Maker's options ({len(rows)})", "", "| Option | Maker | Applied | Result | Note |", "|---|---|---|---|---|"]
    for k, v, st, val, why in rows:
        L.append(f"| `{k}` | {v} | {val if val is not None else '—'} | {st} | {why} |")
    if not rows:
        L.append("| _no process overrides_ | | | | |")
    L += ["", "## Not imported (on purpose)", "- The maker's filament and printer: use your own profiles.",
          "- Per-object settings (ironing per part, modifiers): they stay in the 3mf, open it as a **project** in the slicer.",
          f"- Size of the largest object (ignoring rotations/scale stored in the project): {h[0]:.0f} x {h[1]:.0f} x {h[2]:.0f} mm.",
          "- Volumetric flow: the per-filament limit (Bambu/Anycubic profiles) is applied by the slicer when slicing."]
    rp = os.path.join(OUT, name + ".report.md")
    open(rp, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")

    n = {s: sum(1 for r in rows if r[2] == s) for s in ("applied", "limited", "same", "discarded")}
    print(f"Preset: {jp}\nReport: {rp}" + (f"\nProject: {pj}" if a.project else ""))
    print(f"Reference: {a.ref}")
    print(f"Maker options {len(rows)}: applied {n['applied']}, limited {n['limited']}, "
          f"already the same {n['same']}, discarded {n['discarded']}")
    for x in warn:
        print("WARNING:", x.replace("**", ""))
    for k, v, st, val, why in rows:
        if st in ("limited", "discarded"):
            print(f"  {st:9s} {k} = {v}" + (f" -> {val}" if val else "") + f"  ({why})")

    if a.install:
        # account folder: the one holding the reference preset, otherwise the only non-"default" one
        cand = [os.path.dirname(p) for p in ref_user] or \
               [p for p in glob.glob(S["user"])
                if os.path.basename(os.path.dirname(p)).lower() != "default"]
        if len(cand) != 1:
            sys.exit(f"Slicer user folder not determined: copy the files by hand from {OUT}")
        d = cand[0]
        if slicer_running(S["proc"]):
            sys.exit(f"{a.slicer} slicer is running: close it (one instance only) and run again with --install.")
        dst = os.path.join(d, name + ".json")
        if os.path.exists(dst):
            if not a.force:
                sys.exit(f"{dst} already exists: use --force to overwrite it (a backup is made first).")
            os.makedirs(BACKUP, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            for ext in (".json", ".info"):
                if os.path.exists(os.path.join(d, name + ext)):
                    shutil.copy2(os.path.join(d, name + ext), os.path.join(BACKUP, f"{name} {stamp}{ext}"))
        for ext in (".json", ".info"):
            shutil.copy2(os.path.join(OUT, name + ext), d)
        print("Installed in the slicer:", dst)


if __name__ == "__main__":
    main()
