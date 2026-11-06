# FreeStepViewer

[![CI](https://github.com/Roman3349/FreeSTEP-Viewer/actions/workflows/ci.yml/badge.svg)](https://github.com/Roman3349/FreeSTEP-Viewer/actions/workflows/ci.yml)

Parser and viewer for data exported from the FreeSTEP pressure-plate software. Pure Python:
no ODBC or MS Access drivers needed, so it runs the same on Windows, Linux and macOS. The file
format, determined by reverse engineering FreeSTEP exports, is described in
[docs/FORMAT.md](docs/FORMAT.md).

## Installation

```bash
uv sync                 # library and CLI
uv sync --extra gui     # + the Qt viewer (PySide6)
```

## GUI

```bash
uv run freestep-viewer [FreeStep_Export.zip | 00000158.fre]
```

- Patient/exam tree read from `Export.mdb`. A single `.fre` file can be opened too, via the menu or drag & drop.
- Pressure heat map with the COP of the whole plate and of both halves, plus a COP trail.
- Playback (Space, arrow keys, PageUp/PageDown) and frame / maximum / mean display modes.
- COP trajectory plot with the origin in the plate corner (as stored), the plate centre or the mean position and
  the 95 % confidence ellipse, and a stabilogram (ML and AP sway over time; click to seek), patient/exam/recording
  metadata and the `Preview1.jpg` preview.
- Stabilometric parameters and load distribution of every acquisition (Stabilometry tab, see below) and evaluation
  of the Romberg test.
- Export of the COP to CSV, of the frames to NPZ, of everything (frames, COP, parameters) or only the recorded
  (raw) data to MATLAB `.mat`, of the parameters to CSV, of the heat map to PNG and of all frames as PNGs in a ZIP.
- History of a patient: one parameter (or Romberg quotient) over all their exams, per eye condition; click a
  patient in the tree or open the History tab, click an exam in the plot to show it, export everything to CSV.
- Batch export of all exams of an export (File › Batch export), see below.
- English and Czech UI (View › Language, switches without a restart). The default is the system language.

Decryption runs in the background, so the window stays responsive. The viewer asks for a
password if the archive uses a different one.

## CLI

```bash
uv run freestep info FreeStep_Export.zip                      # patients and exams
uv run freestep exam FreeStep_Export.zip --exam 158 --csv cop.csv --npz frames.npz --preview preview.jpg
uv run freestep exam 00000158.fre --raw Postura.dat           # a single .fre file
uv run freestep exam 00000158.fre --mat exam.mat --mat-raw raw.mat --stats stats.csv --lowpass 5
uv run freestep batch FreeStep_Export.zip out/ --format npz --format cop-csv  # all exams
uv run freestep history FreeStep_Export.zip 46 --parameter ellipse_area_mm2 --csv history.csv  # patient 46
uv run freestep dump FreeStep_Export.zip out/                 # Export.mdb tables to CSV
```

The archives are opened with the built-in default password (change it with `--password`). The default encoding of
`Paziente.ini` is `cp1250` (change it with `--encoding`).

## Batch export

`freestep batch` and File › Batch export write every exam of an export into one directory as
`<birth date>_<name>_<exam date>_<HH-MM><suffix>`, e.g. `1998-06-06_Jan_Novak_2025-03-10_09-37.npz`
(the same names are proposed when exporting a single exam). The name has no diacritics and `_` instead of
spaces. The formats (`--format`, repeatable) are `mat` (`.mat`, data and
parameters), `mat-raw` (`_raw.mat`, recorded data only), `npz` (`.npz`), `cop-csv` (`_cop.csv`) and
`stats-csv` (`_stabilometry.csv`). `index.csv` lists every exam (patient, exam, acquisitions, files,
status) and `stabilometry_summary.csv` holds the parameters of all acquisitions, one row each (turn it
off with `--no-summary`). `--dry-run` only prints the file names. Decrypting takes about 5 s per exam.

## Stabilometry

`freestep.stabilometry` computes the parameters from the COP of the whole plate (x = medio-lateral,
y = antero-posterior) after a zero-phase 4th order Butterworth low-pass filter (5 Hz by default,
`--lowpass 0` / "unfiltered" turns it off). Unloaded frames at the start and end are left out,
those in between are interpolated. Parameters: mean position, range, standard deviation, mean and
RMS distance, path length and mean velocity (total, ML, AP), 95 % confidence ellipse, convex hull,
sway area per second and, from Welch's power spectrum in 0.15–5 Hz, mean, median and 95 %
frequency and total power (Prieto et al. 1996, Duarte & Freitas 2010). `--detrend` / "Remove trend"
subtracts the linear trend (slow drift) instead of only the mean; it is off by default.

The load distribution gives the mean share of the load on the left and right half of the plate
(split like FreeSTEP's COP of the feet), its asymmetry and variability over time, and the share on
the front and rear half of each foot's length. The toes are assumed to point to row 0 (the top of
the heat map).

A posturography exam with two acquisitions of the same duration is evaluated as a Romberg test:
the Romberg quotient (eyes closed / eyes open) of path length, velocity, ellipse area, sway area,
SD and range. FreeSTEP does not store which acquisition is with eyes closed (unless the labels
say so), so the order is a setting: eyes open first by default, `--eyes-closed-first` or the
"Eyes closed first" check box otherwise. No normative values are applied.

In MATLAB / Octave:

```matlab
d = load('exam.mat');
imagesc(d.acq(1).pressure(:, :, 100)); axis image   % rows × columns × frames
plot(d.acq(1).cop_total(:, 1), d.acq(1).cop_total(:, 2))
d.acq(2).stabilometry.ellipse_area_mm2
d.romberg.quotients.path_length_mm
```

## Library

```python
from freestep import FreeStepExport

with FreeStepExport("FreeStep_Export.zip") as export:
    for patient in export.patients:
        for exam in export.exams_of(patient.id):
            with export.open_fre(exam) as fre:
                post = fre.postura  # decryption takes ~4 s
                post.header.platform  # "Max 50x60"
                post.pressure  # numpy (frames, 100, 120), uint16
                post.cop_total  # numpy (frames, 2), cm
```

ZipCrypto decryption in pure Python takes about 2 s per archive layer, so data are loaded
lazily, only when accessed.

## Translations

GUI texts are standard gettext catalogues in `src/freestep/gui/locale/`. The template is
`freestep.pot` and each language has a `<code>.po` file. Source strings are English and are
marked with `tr("…")` or `ntr("singular", "plural", n)` in the code.

After changing texts in the sources, regenerate the template and merge it into the
translations (a test fails while they are out of date):

```bash
uv run python -m freestep.gui.po update
```

### Weblate

Create a component with these settings:

| Setting                       | Value                                  |
|-------------------------------|----------------------------------------|
| File format                   | gettext PO file                        |
| File mask                     | `src/freestep/gui/locale/*.po`         |
| Template for new translations | `src/freestep/gui/locale/freestep.pot` |

Recommended add-ons:

- **Customize gettext output** with long lines not wrapped. The project's own tooling does not wrap lines, so diffs stay minimal.
- **Update PO files to match POT (msgmerge)**, if the template is regenerated in CI instead of locally.

Placeholders are marked `python-brace-format`, so Weblate checks that translations keep
`{name}` etc. A new language needs only a new `.po` file and appears in the menu
automatically. Qt's own texts (dialog buttons) use Qt's bundled `qtbase_<lang>.qm`.

## Packages

GitHub Actions build all packages on every push to `master`/`main` (as workflow artifacts).
For a tag `v<version>` they are attached to a GitHub release; the tag must match the version
in `pyproject.toml`.

| Package | Content | Built by |
|---------|---------|----------|
| `FreeStepViewer-<version>-setup.exe` | Windows installer (per-user or all users; optional `.fre` association and `freestep` in `PATH`) | PyInstaller + Inno Setup |
| `FreeStepViewer-<version>-windows-portable.zip` | the same without installation | PyInstaller |
| `FreeStepViewer-<version>-macos-arm64.dmg` | macOS app for Apple Silicon (drag to Applications) | PyInstaller + hdiutil |
| `FreeStepViewer-<arch>.flatpak` | Linux bundle for x86_64 and aarch64; install with `flatpak install --user FreeStepViewer-x86_64.flatpak` | flatpak-builder |

The macOS app is only ad-hoc signed: notarization needs an Apple Developer ID. On the first
start, macOS blocks it; open it with right-click › Open (or allow it in System Settings ›
Privacy & Security).

### Windows (locally)

```bash
uv sync --extra gui
uv run --with pillow python packaging/make_icons.py build/icons
uv run --with pyinstaller pyinstaller --noconfirm --distpath dist --workpath build/pyinstaller packaging/pyinstaller/freestep.spec
iscc /DAppVersion=0.2.1 packaging\windows\installer.iss
```

### macOS (locally)

```bash
uv sync --extra gui
uv run python packaging/make_icons.py build/icons
uv run --with pyinstaller pyinstaller --noconfirm --distpath dist --workpath build/pyinstaller packaging/pyinstaller/freestep.spec
open "dist/FreeSTEP Viewer.app"
```

### Flatpak (locally)

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak-builder --user --install-deps-from=flathub --force-clean --install build-flatpak packaging/flatpak/cz.romanondracek.FreeStepViewer.yml
flatpak run cz.romanondracek.FreeStepViewer
```

The build installs the SDK into the user installation, which needs its own Flathub remote even
when Flathub is already set up system-wide (otherwise it fails with "No remote refs found for
'flathub'").

PySide6 comes from the `io.qt.PySide.BaseApp` base. The other Python dependencies are pinned
with checksums in `packaging/flatpak/python3-dependencies.json`, so the build works offline as
Flathub requires. After changing dependencies run `uv lock` and
`uv run python packaging/flatpak/generate_sources.py`; CI fails while the file is out of date.

## Development

```bash
uv sync --extra gui     # mypy also checks the GUI and needs PySide6
uv run ruff check
uv run ruff format
uv run mypy             # strict
```

CI (`.github/workflows/ci.yml`) runs ruff, mypy, the translation and Flatpak source checks
and pytest on Linux, Windows and macOS with Python 3.12 and 3.13.

## Tests

```bash
uv run pytest                          # unit tests + 2 exams of the sample export
FREESTEP_ALL_EXAMS=1 uv run pytest     # all 22 exams (~1.5 min)
```

If GNU Octave (`octave-cli`) is installed, a test also checks that it reads the exported `.mat` file.
The integration tests look for the sample export in the repository root. ZIP, `.fre` and
`.mdb` files are listed in `.gitignore` because they contain personal data. The only exceptions are
`tests/data/00000160.fre` and `00000161.fre`, anonymised Romberg exams of one patient used by the
tests in CI; they were created by `tests/data/anonymize_fre.py`.

## License

[MIT](LICENSE). This project is not affiliated with the authors of FreeSTEP. The file format
was determined by reverse engineering FreeSTEP exports.
