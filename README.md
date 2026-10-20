# MakerWorld / Bambu `.3mf` → Anycubic Slicer Next / OrcaSlicer process-preset importer

Two small Python scripts (standard library only) that take the print settings a maker saved inside a MakerWorld / Bambu Studio `.3mf` and turn them into a **safe** process preset for an Anycubic Kobra X, ready to load in Anycubic Slicer Next or OrcaSlicer.

| File | Purpose |
|---|---|
| `extract_overrides.py` | Read-only: shows what the maker changed, and the per-object / modifier settings. |
| `import_3mf.py` | Builds the preset + report, can install it into the slicer, and can re-centre the project on your bed. |

## What it does

1. Opens the `.3mf` (a zip) and reads `Metadata/project_settings.config`.
2. Takes only the options the maker **changed from the system preset** (`different_settings_to_system`).
3. Filters them through a whitelist, clamps them to your machine limits, and writes to `imported_presets/` (next to the scripts):
   - `<name>.json` + `<name>.info`: a user process preset that inherits from a Kobra X reference preset;
   - `<name>.report.md`: every maker option with its result (applied / limited / same / discarded) and warnings;
   - with `--project`: `<name>.3mf`, a copy of the project re-centred on your bed.
4. With `--install` it copies the preset into the slicer's user folder.

## Safety rules (the machine comes before the maker)

- **Whitelist only**: walls, shells, infill, supports, brim/skirt/raft, ironing, seam, spiral vase, bridge settings, compensation. **Never** imported: G-code, retraction, z-hop, temperatures, fans, bed/plate type, prime tower, filament, printer.
- Speeds, accelerations and jerk are **never higher** than your reference preset or the machine limits.
- Layer height and line widths are clamped to nozzle and machine limits.
- Keys unknown to the reference preset are dropped; preset names are sanitised.
- Warns if the model does not fit the 260 × 260 × 260 mm bed.
- Never overwrites an installed preset without `--force` (then it keeps a backup in `overwritten_presets_backup/`); `--install` refuses to run while the slicer is open; oversized zip members are ignored.

## Requirements

- Python 3.8+ (no extra packages) on **Windows** (uses `%APPDATA%` and `tasklist`).
- Anycubic Slicer Next installed (its system presets are read for the Kobra X limits), or OrcaSlicer with the Anycubic profiles (`-s orca`).
- The machine is hard-coded in the block marked *machine-specific settings* at the top of both scripts: `MACHINE`, `DEFAULT_REF`, `ORCA_SYS`, `NAME_SUFFIX`. For another printer, change them.

## Usage

```bat
:: 1. See what the maker changed (read-only, also lists per-object / modifier settings)
python -I extract_overrides.py MyModel.3mf

:: 2. Generate the preset + report (nothing is installed)
python -I import_3mf.py MyModel.3mf

:: 3. Same, and copy it into the slicer (the slicer must be CLOSED, one instance only)
python -I import_3mf.py MyModel.3mf --install

:: 4. Replace a preset that is already installed with the same name (a backup is kept first)
python -I import_3mf.py MyModel.3mf --install --force

:: 5. Also write a copy of the 3mf re-centred on your 260 x 260 bed
python -I import_3mf.py MyModel.3mf --project
```

Other options: `-s orca|anycubic` (target slicer), `-n "preset name"` (default: file name + `" @AC KX"`).
Installed presets land in `%APPDATA%\AnycubicSlicerNext\user\<id>\process\`. Restart the slicer and pick the preset in the Process list (user presets: uppercase names first, then lowercase). If a preset with the same name is already in `imported_presets/`, a timestamp is appended to the new one.

## Choosing the starting preset

The maker's file only stores what they *changed*, so everything else comes from the **reference preset** the new preset inherits from. By default the script picks `<maker's layer height> Standard @Anycubic Kobra X 0.4 nozzle` (or the High Quality variant if the maker's process name says Fine / High Quality / Optimal). That is the safe, fast choice, and it keeps the layer height the model was designed for.

If you want a nicer result for one specific project, start from a better preset:

```bat
python -I import_3mf.py --list-refs                    :: table of presets you can start from (system + yours)
python -I import_3mf.py MyModel.3mf --quality high     :: same layer height, "High Quality" variant
python -I import_3mf.py MyModel.3mf --layer 0.16       :: finer layer height
python -I import_3mf.py MyModel.3mf --layer 0.12 --quality high
python -I import_3mf.py MyModel.3mf -r "My own tuned preset"   :: any system or user preset, exact name
```

What the system presets for the Kobra X 0.4 change (values read from the Anycubic Slicer Next profiles):

| Preset | Outer wall | Accel. | Infill | Top / bottom layers | Use it for |
|---|---|---|---|---|---|
| 0.20 Standard | 200 mm/s | 6000 | grid | 5 / 3 | fast, good enough for most parts |
| 0.20 High Quality | 60 mm/s | 4000 | gyroid | 5 / 3 | **same layers, better surfaces** (slower walls, less ringing): the first upgrade to try |
| 0.16 Standard | 200 mm/s | 6000 | grid | 6 / 4 | finer layers, same speed profile |
| 0.16 High Quality | 60 mm/s | 4000 | gyroid | 6 / 4 | visible parts |
| 0.12 Standard / High Quality | 60 mm/s | 6000 / 5000 | grid / 3D honeycomb | 7 / 5 and 5 / 5 | small, detailed parts |
| 0.08 Standard | 60 mm/s | 6000 | grid | 9 / 7 | miniatures, very slow |

Notes:
- With `--layer`, the maker's top/bottom shell **layer counts** are scaled by the layer-height ratio so the shells keep their thickness (e.g. 10 bottom layers at 0.20 mm → 17 at 0.12 mm). The report marks them.
- The maker's own overrides still win over the reference (they are clamped to the reference speeds), so a maker who set 3 walls keeps 3 walls on any base.
- If the chosen layer height has no High Quality preset (0.08, 0.24, 0.28), Standard is used and the report says so.
- Layer height changes print time a lot; High Quality at the same layer height is usually the cheapest upgrade.

### Doing it by hand (no script options)

1. **In the slicer**: open the `.3mf` as a project, in the Process tab choose the preset you want to start from (for example `0.20mm High Quality @Anycubic Kobra X 0.4 nozzle`), re-apply by hand the changes listed by `extract_overrides.py`, then *Save as* a new preset.
2. **Editing the generated preset**: open `imported_presets/<name>.json` and change the value of `"inherits"` to the exact name of another system preset (see `--list-refs`), keep the other keys, copy it to the slicer's `process` folder and restart the slicer. If you also change the layer height, remember the shell layer counts.

## What it does NOT do

- It does not convert per-object settings or modifiers. Open the `.3mf` as a **project** to keep them, and check them: a modifier larger than the bed makes the slicer report "object laid over the boundary" (and the object ends up under "Outside" in the list).
- The size warning ignores the scale stored in the project, so check the real size in the slicer.
- It does not touch filament settings, plate temperatures or pressure advance: pick your own filament profile. (Some online converters copy the maker's Bambu filament overrides into the converted file: flow limits, temperatures and pressure advance tuned for another printer.)
- `--project` only moves the plate placement of the objects (and leaves meshes and settings byte-for-byte as they are): the slicer will still ask to switch printer when you open it, and you select the preset yourself.

## Privacy

The scripts contain no credentials, IP addresses or user names, and make no network access. They only read the `.3mf` you pass and the slicer's preset folder. If you share them, do not include the `__pycache__` folder (the `.pyc` files embed local paths) or your `imported_presets/` output.

## License

MIT, see `LICENSE`.

## Disclaimer

Written with the help of an AI coding assistant for one printer. Test it on a copy first; the report tells you what was changed. No warranty.
