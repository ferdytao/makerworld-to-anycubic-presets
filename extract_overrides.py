"""Show what the maker changed in a .3mf (Bambu Studio / Orca / MakerWorld)
compared with the system presets, and compare it with your own Kobra X process preset.

Usage:  python -I extract_overrides.py file.3mf [-r "Process preset name"]

Levels read from the 3mf:
  1. process / filament / printer overrides  (project_settings.config -> different_settings_to_system)
  2. per-object / per-part / modifier settings (model_settings.config)
The comparison (level 1, process only) uses a reference preset: a user preset or an Anycubic system preset.
Read-only: nothing is written anywhere.
"""
import argparse, glob, json, os, sys, zipfile
import xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding="utf-8")

# ---- machine-specific settings: change these for another printer -------------------------------
DEFAULT_REF = "0.20mm Standard @Anycubic Kobra X 0.4 nozzle"
APPDATA = os.path.expandvars(r"%APPDATA%\AnycubicSlicerNext")
# -------------------------------------------------------------------------------------------------

# per-object metadata that are not print settings
SKIP = {"name", "extruder", "matrix", "volume_type", "mesh_stat", "face_count", "object_id", "locked",
        "source_file", "source_object_id", "source_volume_id", "source_offset_x", "source_offset_y",
        "source_offset_z", "source_in_inches", "source_in_meters", "text", "volume_id", "subtype"}


def load_ref(name, dirs=None):
    """Process preset resolved along its 'inherits' chain. None if not found.
    dirs = list of glob patterns of 'process' folders (default: Anycubic Slicer Next, user first, then system)."""
    dirs = dirs or [os.path.join(APPDATA, "user", "*", "process"), os.path.join(APPDATA, "system", "*", "process")]
    paths = [p for d in dirs for p in glob.glob(os.path.join(d, name + ".json"))]
    if not paths:
        return None
    j = json.load(open(paths[0], encoding="utf-8"))
    base = load_ref(j["inherits"], dirs) if j.get("inherits") else {}
    base = base or {}
    base.update(j)
    return base


def norm(v):
    """One-element list -> scalar, so that 'x' and ['x'] compare equal."""
    while isinstance(v, list) and len(v) == 1:
        v = v[0]
    return v


def show(v, n=70):
    s = str(v)
    return s if len(s) <= n else s[:n] + "…"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("-r", "--ref", default=DEFAULT_REF, help="process preset to compare with")
    a = ap.parse_args()

    z = zipfile.ZipFile(a.file)
    try:
        ps = json.loads(z.read("Metadata/project_settings.config"))
    except KeyError:
        sys.exit("No project_settings.config: this file was not saved by a Bambu/Orca/Anycubic slicer.")
    nf = len(ps.get("filament_settings_id", []))
    diff = ps.get("different_settings_to_system", [])
    print(f"File:      {os.path.basename(a.file)}")
    print(f"Printer:   {ps.get('printer_settings_id')}")
    print(f"Process:   {ps.get('print_settings_id')}")
    print(f"Filaments: {', '.join(sorted(set(ps.get('filament_settings_id', []))))}")

    # --- level 1: process ---
    ref = load_ref(a.ref)
    pk = [k for k in (diff[0].split(";") if diff and diff[0] else []) if k]
    print(f"\n== 1. Maker's PROCESS overrides ({len(pk)}) ==")
    if not ref:
        print(f"(reference preset '{a.ref}' not found: showing values only)")
    for k in pk:
        v = norm(ps.get(k))
        if not ref:
            st = ""
        elif k not in ref:
            st = "  [!] key missing from your preset: the slicer may ignore it"
        elif str(norm(ref[k])) == str(v):
            st = "  [= same as yours]"
        else:
            st = f"  [yours: {show(norm(ref[k]))}]"
        print(f"  {k} = {show(v)}{st}")
    if not pk:
        print("  none (the maker used the system preset as it is)")

    # --- filament / printer (informational) ---
    for i in range(nf):
        ks = [k for k in (diff[1 + i].split(";") if len(diff) > 1 + i and diff[1 + i] else []) if k]
        if ks:
            print(f"\n== FILAMENT overrides, slot {i + 1} ({ps['filament_settings_id'][i]}) ==")
            for k in ks:
                v = ps.get(k)
                v = v[i] if isinstance(v, list) and len(v) > i else norm(v)
                print(f"  {k} = {show(v)}")
    ks = [k for k in (diff[nf + 1].split(";") if len(diff) > nf + 1 and diff[nf + 1] else []) if k]
    if ks:
        print("\n== PRINTER overrides (do not transfer: your machine is different) ==")
        for k in ks:
            print(f"  {k} = {show(norm(ps.get(k)))}")

    # --- level 2: per object ---
    print("\n== 2. Per-OBJECT / PART settings ==")
    try:
        root = ET.fromstring(z.read("Metadata/model_settings.config"))
    except KeyError:
        root = None
    found = 0
    if root is not None:
        for obj in root.iter("object"):
            oname = next((m.get("value") for m in obj.findall("metadata") if m.get("key") == "name"), "?")
            # key -> {value: number of places where it appears}; place = object, part, modifier or layer range
            agg = {}

            def add(el, where):
                for m in el.findall("metadata"):
                    k = m.get("key")
                    if k and k not in SKIP:
                        agg.setdefault((k, where), {}).setdefault(m.get("value"), 0)
                        agg[(k, where)][m.get("value")] += 1

            add(obj, "object")
            for part in obj.findall("part"):
                add(part, "modifier" if "modifier" in part.get("subtype", "") else "part")
            for lr in obj.iter("range"):
                add(lr, f"layers {lr.get('min_z', '?')}-{lr.get('max_z', '?')}")
            if agg:
                found += 1
                print(f"  object '{oname}':")
                for (k, where), vals in sorted(agg.items()):
                    n = sum(vals.values())
                    vs = ", ".join(sorted(vals))
                    print(f"    {k} = {show(vs, 90)}" + (f"   [{where}{f', {n} places' if n > 1 else ''}]"))
    if not found:
        print("  none")
    print("\nNote: level 2 stays attached to the objects if you reopen the 3mf as a PROJECT in Orca; "
          "level 1 has to be transferred (or reapplied by hand) when you change printer.")


if __name__ == "__main__":
    main()
