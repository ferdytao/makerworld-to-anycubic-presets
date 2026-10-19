# MakerWorld / Bambu `.3mf` → Anycubic Slicer Next / OrcaSlicer process-preset importer

Two small Python scripts (standard library only) that take the print settings a maker saved inside a MakerWorld / Bambu Studio `.3mf` and turn them into a **safe** process preset for an Anycubic Kobra X, ready to load in Anycubic Slicer Next or OrcaSlicer.

| File | Purpose |
|---|---|
| `extract_overrides.py` | Read-only: shows what the maker changed, and the per-object / modifier settings. |
| `import_3mf.py` | Builds the preset + report, and can install it into the slicer. |

## What it does

1. Opens the `.3mf` (a zip) and reads `Metadata/project_settings.config`.
2. Takes only the options the maker **changed from the system preset** (`different_settings_to_system`).
3. Filters them through a whitelist, clamps them to your machine limits, and writes to `imported_presets/` (next to the scripts):
   - `<name>.json` + `<name>.info`: a user process preset that inherits from a Kobra X reference preset (default: `<maker layer height> Standard @Anycubic Kobra X 0.4 nozzle`);
   - `<name>.report.md`: every maker option with its result (applied / limited / same / discarded) and warnings.
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

:: Other options
::   -s orca|anycubic    target slicer (default: anycubic)
::   -n "preset name"    name of the preset (default: file name + " @AC KX")
::   -r "reference"      reference process preset (default: chosen from the maker's layer height)
```

Installed presets land in `%APPDATA%\AnycubicSlicerNext\user\<id>\process\`. Restart the slicer and pick the preset in the Process list (user presets: uppercase names first, then lowercase). If a preset with the same name is already in `imported_presets/`, a timestamp is appended to the new one.

## What it does NOT do

- It does not convert per-object settings or modifiers. Open the `.3mf` as a **project** to keep them, and check them: a modifier larger than the bed makes the slicer report "object laid over the boundary" (and the object ends up under "Outside" in the list).
- The size warning ignores the scale stored in the project, so check the real size in the slicer.
- It does not touch filament settings: pick your own filament profile.

## Privacy

The scripts contain no credentials, IP addresses or user names, and make no network access. They only read the `.3mf` you pass and the slicer's preset folder. If you share them, do not include the `__pycache__` folder (the `.pyc` files embed local paths) or your `imported_presets/` output.

## License

MIT, see `LICENSE`.

## Disclaimer

Written with the help of an AI coding assistant for one printer. Test it on a copy first; the report tells you what was changed. No warranty.
