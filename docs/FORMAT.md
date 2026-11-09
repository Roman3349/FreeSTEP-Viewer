# FreeSTEP export format

The format was determined by reverse engineering FreeSTEP data: an export with posturography
exams on a Max 50x60 plate and the demo exams of a FreeSTEP installation (`Esami/0000`, one exam
of every type, 2010–2012, Free4 plates).

FreeSTEP is a Visual Basic 6 application, so its files follow VB6 conventions:

- little-endian;
- `Integer` = int16, `Long` = int32, `Single` = float32;
- `True` = -1;
- fixed-length strings (`String * n`) padded with spaces;
- arrays include their upper bound (`Dim a(N)` has N + 1 items);
- `Put` of a record writes its members packed (no alignment); a dynamic array member is preceded by
  a descriptor: int16 number of dimensions, then per dimension int32 element count and int32 lower bound.

Legend: ✅ verified on data, ❓ unknown or a hypothesis.

## Export ZIP

ZipCrypto + Deflate, password protected.

| File           | Content |
|----------------|---------|
| `Indice.ini`   | `[CENTRO] Codice` (centre code), `[GENERALI] DataCreazione`, `Pazienti`, `Esami` (counts) |
| `Export.mdb`   | Jet4 (Access 2000) database, texts in "compressed unicode" |
| `NNNNNNNN.fre` | one exam; the name is the exam ID (8 digits) |

A FreeSTEP installation keeps the exams as the same `.fre` files in `Esami/NNNN/` (e.g.
`Esami/0000/00000007.fre`) and the database in `DBase/` (`Archivio.mdb`, `Export.mdb`) ❓.

### Export.mdb

| Table          | Meaning | Notes |
|----------------|---------|-------|
| `Pazienti`     | subjects | `ID`, `Nominativo` (name), `DataDiNascita`, `Altezza` (cm), `Peso` (kg), `Calzatura` (EU shoe size), `ShoeNum`/`ShoeTAG` (US size, W/M), `AltezzaINC` (in), `PesoLB` (lb), `Metrica` (M = metric), `Sesso` (`Femminile` / `Maschile`, often empty), `SSID`, … |
| `Esami`        | exams | `ID`, `IDPaziente`, `TipoEsame`, `FlagCalz`, `Data` (`dd.mm.yyyy`), `Ora` (`hh:mm`), `SSID`, `InfoPaziente`, `NoteEsame` |
| `Anamnestica`  | medical history | `CodPaz`, `Options` (string of 0/1 ❓), `Campo1..14` |
| `DiarioClinico`, `Referti`, `Allegati` | clinical diary, reports, attachments | |

`InfoPaziente` holds the subject's values at the time of the exam:
`[age][height][weight][EU shoe size][metric][height in][weight lb][ShoeTag][ShoeNum]`.

### TipoEsame (exam type)

| Value | Type | Files in the `.fre` | Read by the viewer |
|------:|------|---------------------|--------------------|
| 0     | Static ❓ | | |
| 1     | Static with photo | `Statica.dat`, `Impronta.bpe`, `Statica.BMP`, `VG.ini` | pressure frame ✅, photo |
| 2     | Dynamic | `Dinamica.dat`, `Old.dat`, `Impronta.bpe`, `ImprontaMedia.bpe` | passes over the plates ✅ |
| 3     | Dynamic with video | `Dinamica.dat`, `Old.dat`, `Impronta.bpe`, `Capture.avi`, `Capture.ini`, `VideoTrack_0.dat`, `Sel_Video_frame*.bmp` | passes over the plates ✅, video, selected frames |
| 4     | Posturography | `Postura.dat` (+ `Filtro.ini`, `Preview1.jpg`) | everything ✅ |
| 6     | Videography | `VG*.bmp`, `VG_*.ini`, `VG_*.dat` | images |
| 8     | Podoscanner | `Scanner.bmp`, `Originale.bmp`, `Scanner.ini`, `VG.ini` | foot scan with a scale ✅ |
| 11    | Treadmill with video | `TreadMILL.dat`, `Old.dat`, `Impronta.bpe`, `Capture.avi`, `Capture.ini`, `VideoTrack.dat` | recording ✅, video |

### FlagCalz (footwear)

0 = barefoot, 1 = shoes, 2 = shoes with orthosis.

## .fre (exam)

A ZIP with the same password as the export. The content depends on the exam type (see above);
for example, some posturography exams have no `Filtro.ini` and no `Preview1.jpg`. Member names
are matched case-insensitively (`Statica.BMP`, `VG0.bmp`).

| File            | Content |
|-----------------|---------|
| `Filtro.ini`    | `[FILTRI] Filtro` (0 = raw data), `Freq` |
| `Paziente.ini`  | `[Paziente]`, **ANSI code page (cp1250 on Czech Windows)**; dates `dd.mm.yyyy` or `dd/mm/yyyy`; `OraEsame` is `hh:mm` or `hh:mm:ss`; `Sesso` may be English (`Female`) |
| `PazienteW.ini` | superset of `Paziente.ini`, **UTF-8 with BOM**, with errors: `NomeFile00000158.fre` (missing `=`), `ShoeTag=WShoeNum=8,5` (missing line break) |
| `Preview1.jpg`  | preview image |
| `Thumbs.db`     | Windows thumbnail cache, not FreeSTEP data |

The other files are described per exam type below.

## Postura.dat

Recording of a **posturography** exam. One file holds **several acquisitions**, e.g. eyes open
and eyes closed. The exams in the sample export contain 2 acquisitions of 51.2 s each; the
version 1 demo exam holds 4 (two of 51.2 s, two of 10 s).

### File header

The same header (offsets 0–61) starts `Statica.dat`; `Dinamica.dat`, `TreadMILL.dat` and `Old.dat`
have one more int16 after the rows, the number of plates (see there).

| Offset | Type     | Sample       | Meaning |
|-------:|----------|--------------|---------|
| 0      | int16    | 3            | format version (1 in older files; 2 and 4 in `Dinamica.dat`) ✅ |
| 2      | char[50] | `Max 50x60`  | plate name, VB `String * 50` (`Free4 40x80`, `Free4 40x40`, `RUNTime 120 (1-2)`) ✅ |
| 52     | int16    | 120          | columns (X axis) ✅ |
| 54     | int16    | 100          | rows (Y axis) ✅ |
| 56     | int16    | 5            | cell width in mm ✅ |
| 58     | int16    | 5            | cell height in mm ✅ |
| 60     | int16    | 255          | maximum sensor value ✅ (data are 0..255; in `Statica.dat` the maximum of the frame) |
| 62     | int16[k] | see below    | version-specific settings ❓ |

The settings are k = 6 values in version 3: `-1, 512 or 650, 0, 0, 1, 1`. The 512/650 differs
between exams but not with the duration ❓. Version 1 has k = 2 values (`0, 5` in the demo exam).
The header is therefore 74 B (version 3) or 66 B (version 1). The parser detects k for unknown versions.

### Acquisition (repeated until the data end)

| Size       | Type             | Meaning |
|-----------:|------------------|---------|
| 2          | int16            | flag, -1 (VB `True`) for a stored acquisition ✅ |
| 4          | int32            | last frame index N; the acquisition has N + 1 frames ✅ |
| (N+1) × F  | Frame[N+1]       | frames ✅ |
| 2 × R × C  | uint16[R][C]     | mean of every cell over the frames where it is loaded ✅ |
| 100        | char[100]        | label, VB `String * 100`, space padded, only in version 3 (empty in the samples) ✅ |

Older files may be followed by preallocated, unused space.

### Frame (F = 2 × R × C + 30 bytes)

| Offset    | Type         | Meaning |
|----------:|--------------|---------|
| 0         | uint16[R][C] | pressure, row-major (row = Y, column = X) ✅ |
| 2RC       | int32        | timestamp in ms (system tick count) ✅ |
| 2RC + 4   | int16        | ❓ ~240..252; does not correlate with the load, the loaded cell count, the maximum or Δt |
| 2RC + 6   | float32[2]   | COP of the whole plate (x, y) in cm ✅ |
| 2RC + 14  | float32[2]   | COP of the left foot = half with x < C/2 ✅ |
| 2RC + 22  | float32[2]   | COP of the right foot = half with x ≥ C/2 ✅ |

- **Sampling rate:** in the Max 50x60 exams the timestamps are exactly 40 ms apart (25 Hz). The
  version 1 demo exam on a Free4 plate has about 43–45 Hz in the long and 30 Hz in the short
  acquisitions ✅, so the rate is taken from the timestamps.
- **COP:** computed as `Σ(p · index) / Σp · cell size`, without a half-cell offset. It matches
  the pressure data in all frames checked in all exams.
- **Left/right:** a rotated plate may swap the feet.

## Statica.dat (static exam)

One pressure frame of a static exam ✅ (12 920 B in the demo exam, Free4 40x40, 80 × 80 cells of 5 × 5 mm).

| Offset        | Type         | Meaning |
|--------------:|--------------|---------|
| 0             | header       | common header (offsets 0–61 of `Postura.dat`) ✅ |
| 62            | int16[7]     | settings ❓ (sample: `-1, 489, 0, 64, 43, 43, 42`; the first values resemble those of `Postura.dat` version 3) |
| 76            | int16        | 2: dimensions of the pressure array (VB dynamic array descriptor) ✅ |
| 78            | int32[2] × 2 | per dimension: element count and lower bound (`80, 0`, `80, 0`) ✅ |
| 94            | uint16[R][C] | pressure ✅, sized by the descriptor (equal to the plate in the header) |
| 94 + 2RC      | 26 B         | the rest of the record holding the array (plain members, zero in the sample) ❓ |

The frame has no timestamp and no COP; the viewer computes the COPs like FreeSTEP's. It is equal,
cell by cell, to the footprint `Impronta.bpe` of the same exam ✅, which confirms the offset.

## Impronta.bpe, ImprontaMedia.bpe (footprints)

6404 B in all demo exams: a 4-byte header `08 00 19 00` ❓ and `uint8[80][80]` ✅. In the static
exam the values equal the pressure frame of `Statica.dat`. Dynamic and treadmill exams store a
footprint too; `ImprontaMedia.bpe` is probably the mean footprint of the steps ❓ (FreeSTEP offers
it for designing insoles).

## Dinamica.dat, TreadMILL.dat (gait over several plates)

Pressure recordings of the dynamic exam (walks over a walkway of plates laid one after
another) and of the treadmill exam (plates built into a treadmill), 50–95 MB. They start with
the version and the plate name like `Postura.dat` (`Dinamica.dat`: version 2 or 4, `Free4 40x80`;
`TreadMILL.dat`: version 4, `RUNTime 120 (1-2)`), but have **one more int16 after the rows** ✅:

| Offset | Type  | `Dinamica.dat` | `TreadMILL.dat` | Meaning |
|-------:|-------|---------------:|----------------:|---------|
| 52     | int16 | 80             | 80              | columns of one plate (C) ✅ |
| 54     | int16 | 80             | 80              | rows (R) ✅ |
| 56     | int16 | 4              | 3               | number of plates in a row (P) ✅ |
| 58     | int16 | 5              | 5               | cell width in mm ✅ |
| 60     | int16 | 5              | 5               | cell height in mm ✅ |
| 62     | int16 | 255            | 255             | maximum sensor value ✅ |
| 64     | int16[k] |             |                 | settings ❓; the header has **90 bytes in version 2, 102 bytes in version 4** ✅ |
| H − 6  | int16 | 2 / 4          | 0               | number of passes − 1 ❓ |
| H − 4  | int32 | 251 / 391      | 2050            | last frame index of the first pass (it has N + 1 frames, like `Postura.dat`) ✅ |

Then follow ✅

1. a summary image `uint16[R][C × P]` without a trailer (all footprints of the exam ❓),
2. the frames of the first pass, then a block of unknown content ❓ (128–141 kB in the samples,
   it contains the last frame index of the next pass and small int16 values that look like
   fake timestamps), the frames of the next pass and so on,
3. unused, preallocated space.

The blocks between the passes keep the even alignment.

### Frame (2 × R × C × P bytes + trailer)

The plates are stored side by side: one frame is `uint16[R][C × P]` (80 × 320 for four plates
of 80 × 80 cells), row-major ✅. The trailer has 22 bytes in version 2 and 26 bytes in version 4:

| Offset | Type    | Meaning |
|-------:|---------|---------|
| +0     | int32   | timestamp in ms (system tick count) ✅; 160–190 frames per second |
| +4     | int16   | maximum value of the frame ✅ |
| +6     | …       | ❓; in version 2 the COP of the whole walkway in cells as float32 at +14 (x) and +18 (y) ✅ (equal to the pressure centroid); in `TreadMILL.dat` the floats are not a COP |

The viewer finds the passes by their timestamps (a frame is accepted when its time and that of
the next frame continue the recording, at most 1 s apart); the frame counts match the stored
last frame indexes. It computes the COP from the pressure.

### Old.dat ❓

`Old.dat` has the same size and header as the recording beside it but different content (an
earlier or unprocessed version?). The viewer does not read it.

## Video (dynamic and treadmill exams with video)

| File               | Content |
|--------------------|---------|
| `Capture.avi`      | XviD (MPEG-4 Advanced Simple Profile) with packed B-frames, 640 × 480 or 1280 × 960, nominally 25 fps ✅ |
| `Capture.ini`      | `[CAPINFO] COMPRESSIONE` (1 ❓), `ROTAZIONE`: clockwise rotation for display in degrees ✅ (90 in the treadmill exam) |
| `VideoTrack.dat`, `VideoTrack_0.dat` | a few kB, see below |
| `Sel_Video_frame*.bmp` | frames selected from the video, 24-bit BMP without a resolution ✅ |

`VideoTrack*.dat` starts with int16 1 and three int32 ✅:

| Offset | Type  | Sample (dynamic / treadmill) | Meaning |
|-------:|-------|------------------------------|---------|
| 2      | int32 | 89 / 0                       | video frame at the first pressure frame ✅ (dynamic exam: the recording time `t` is at video frame `89 + t × 30`, checked on the foot contacts of all three passes) |
| 6      | int32 | 153 / 569                    | end of the analysed range ❓ |
| 10     | int32 | 674 / 569                    | number of video frames ✅ |

Then one record per video frame with the frame index and values of unknown meaning (markers?) ❓,
and at the end six int32 frame numbers of the frames selected as `Sel_Video_frame*.bmp`
(99999 = none) ✅.

The frame rate stored in the AVI is not always the real one: the treadmill video claims 70 fps,
but the step frequency in the video and in the pressure (2.73 Hz) gives about 26 fps ❓. The
viewer therefore lets the user correct the frame rate.

## Videography

| File        | Content |
|-------------|---------|
| `VG<n>.bmp` | photo, 24-bit BMP (600 × 800, 300 dpi in the sample); **may be a JPEG despite the extension** ✅ |
| `VG_<n>.ini`| calibration and measurements of a photo (see below); `IDEsame` may name another exam the settings come from ✅ |
| `VG_<n>.dat`| ❓ a few kB; starts like `VideoTrack.dat` (`1, 0, 369, 369`) |

`VG_<n>.ini` (also `VG.ini` of static and podoscanner exams):

| Key | Meaning |
|-----|---------|
| `[COMMON] Release` | version of the module (1.1.01, 1.1.14) |
| `IDPaziente`, `IDEsame`, `TipoEsame` | the exam the settings belong to |
| `CalibrazionePx` | mm per pixel = `Lunghmm` / (`pxBot` − `pxTop`) ✅; decimal comma or point; 0 when not calibrated |
| `pxTop`, `pxBot`, `Lunghmm` | two vertical positions and their distance in mm ✅: 1700 = the subject's height on full-body photos, 500 on foot images |
| `[CUSTOM] PtNum_<k>_Type`, `_Name`, `_Val1`, `_Val2`, `_X1`…`_Y3` | user measurements, e.g. type 5 "Angolo tra due linee" (angle between two lines) ❓ |
| `[MARKERS_<m>] Active`, `PtNum_<k>_X`, `_Y` | anatomical markers ❓ |

## Podoscanner

| File            | Content |
|-----------------|---------|
| `Scanner.bmp`   | foot scan, 24-bit BMP, 1159 × 1652 at 100 dpi (0.254 mm per pixel) ✅ |
| `Originale.bmp` | the unedited scan (identical to `Scanner.bmp` in the sample) |
| `Scanner.ini`   | `[SCANNER] Filtro` (0 ❓) |
| `VG.ini`        | calibration and markers like videography; the marker coordinates exceed the image size, so they use another coordinate system ❓ |

## Open questions

1. Meaning of the header settings (version 3: `-1, 512/650, 0, 0, 1, 1`; `Statica.dat`: 7 values;
   `Dinamica.dat` / `TreadMILL.dat`).
2. The per-frame int16 (~246).
3. Conversion of the raw values 0..255 to pressure (kPa or N/cm²) and the calibration.
4. Meaning of the acquisitions (which one is eyes open / eyes closed, other test types).
5. The blocks between the passes and the rest of the frame trailer of `Dinamica.dat` and
   `TreadMILL.dat`, the meaning of `Old.dat`.
6. The per-frame records of `VideoTrack*.dat`, `VG_*.dat`, the header of `.bpe` and the marker coordinates of `VG*.ini`.
