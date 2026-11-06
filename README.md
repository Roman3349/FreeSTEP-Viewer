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
- Stabilogram, patient/exam/recording metadata and the `Preview1.jpg` preview.
- Export of the COP to CSV, of the frames to NPZ or MATLAB `.mat`, of the heat map to PNG and of all frames as
  PNGs in a ZIP.
- Batch export of all exams of an export (File › Batch export), see below.
- English and Czech UI (View › Language, switches without a restart). The default is the system language.

Decryption runs in the background, so the window stays responsive. The viewer asks for a
password if the archive uses a different one.

## CLI

```bash
uv run freestep info FreeStep_Export.zip                      # patients and exams
uv run freestep exam FreeStep_Export.zip --exam 158 --csv cop.csv --npz frames.npz --preview preview.jpg
uv run freestep exam 00000158.fre --raw Postura.dat           # a single .fre file
uv run freestep exam 00000158.fre --mat exam.mat             # MATLAB / Octave
uv run freestep batch FreeStep_Export.zip out/ --format npz --format cop-csv  # all exams
uv run freestep dump FreeStep_Export.zip out/                 # Export.mdb tables to CSV
```

The archives are opened with the built-in default password (change it with `--password`). The default encoding of
`Paziente.ini` is `cp1250` (change it with `--encoding`).

## Batch export

`freestep batch` and File › Batch export write every exam of an export into one directory as
`<birth date>_<name>_exam_<ID><suffix>`, e.g. `1998-06-06_Jan_Novak_exam_160.npz`. The name has no
diacritics and `_` instead of spaces. The formats (`--format`, repeatable) are `mat` (`.mat`), `npz`
(`.npz`) and `cop-csv` (`_cop.csv`). `index.csv` lists every exam (patient, exam, acquisitions, files,
status). `--dry-run` only prints the file names. Decrypting takes about 5 s per exam.

## MATLAB

The `.mat` files load in MATLAB and GNU Octave:

```matlab
d = load('exam.mat');
imagesc(d.acq(1).pressure(:, :, 100)); axis image   % rows × columns × frames
plot(d.acq(1).cop_total(:, 1), d.acq(1).cop_total(:, 2))
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
iscc /DAppVersion=0.1.0 packaging\windows\installer.iss
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
flatpak-builder --user --install-deps-from=flathub --force-clean --install build-flatpak packaging/flatpak/cz.romanondracek.FreeStepViewer.yml
flatpak run cz.romanondracek.FreeStepViewer
```

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

The integration tests look for the sample export in the repository root. ZIP, `.fre` and
`.mdb` files are listed in `.gitignore` because they contain personal data.

## License

[MIT](LICENSE). This project is not affiliated with the authors of FreeSTEP. The file format
was determined by reverse engineering FreeSTEP exports.
