# FreeSTEP export format

The format was determined by reverse engineering FreeSTEP exports.

FreeSTEP is a Visual Basic 6 application, so its files follow VB6 conventions:

- little-endian;
- `Integer` = int16, `Long` = int32, `Single` = float32;
- `True` = -1;
- fixed-length strings padded with spaces;
- arrays include their upper bound (`Dim a(N)` has N + 1 items).

Legend: ✅ verified on data, ❓ unknown or a hypothesis.

## Export ZIP

ZipCrypto + Deflate, password protected.

| File           | Content |
|----------------|---------|
| `Indice.ini`   | `[CENTRO] Codice` (centre code), `[GENERALI] DataCreazione`, `Pazienti`, `Esami` (counts) |
| `Export.mdb`   | Jet4 (Access 2000) database, texts in "compressed unicode" |
| `NNNNNNNN.fre` | one exam; the name is the exam ID (8 digits) |

### Export.mdb

| Table          | Meaning | Notes |
|----------------|---------|-------|
| `Pazienti`     | patients | `ID`, `Nominativo` (name), `DataDiNascita`, `Altezza` (cm), `Peso` (kg), `Calzatura` (EU shoe size), `ShoeNum`/`ShoeTAG` (US size, W/M), `AltezzaINC` (in), `PesoLB` (lb), `Metrica` (M = metric), `SSID`, … |
| `Esami`        | exams | `ID`, `IDPaziente`, `TipoEsame`, `FlagCalz`, `Data` (`dd.mm.yyyy`), `Ora` (`hh:mm`), `SSID`, `InfoPaziente`, `NoteEsame` |
| `Anamnestica`  | medical history | `CodPaz`, `Options` (string of 0/1 ❓), `Campo1..14` |
| `DiarioClinico`, `Referti`, `Allegati` | clinical diary, reports, attachments | |

`InfoPaziente` holds the patient's values at the time of the exam:
`[age][height][weight][EU shoe size][metric][height in][weight lb][ShoeTag][ShoeNum]`.

### TipoEsame (exam type)

| Value | Type | Files in the `.fre` |
|------:|------|---------------------|
| 0     | Static ❓ | |
| 1     | Static with photo | `Statica.dat`, `Statica.BMP` |
| 2     | Dynamic | `Dinamica.dat` |
| 3     | Dynamic with video | `Dinamica.dat`, `Capture.avi` |
| 4     | Posturography | `Postura.dat` (+ `Filtro.ini`, `Preview1.jpg`) |
| 6     | Videography | `VG*.bmp`, `VG_*.ini` |
| 8     | Podoscanner | `Scanner.bmp` |
| 11    | Treadmill with video | `TreadMILL.dat`, `Capture.avi` |

Only `Postura.dat` is decoded so far.

### FlagCalz (footwear)

0 = barefoot, 1 = shoes, 2 = shoes with orthosis.

## .fre (exam)

A ZIP with the same password as the export. The content depends on the exam type. For example,
some posturography exams have no `Filtro.ini` and no `Preview1.jpg`.

| File            | Content |
|-----------------|---------|
| `Filtro.ini`    | `[FILTRI] Filtro` (0 = raw data), `Freq` |
| `Paziente.ini`  | `[Paziente]`, **ANSI code page (cp1250 on Czech Windows)**; dates `dd.mm.yyyy` or `dd/mm/yyyy` |
| `PazienteW.ini` | superset of `Paziente.ini`, **UTF-8 with BOM**, with errors: `NomeFile00000158.fre` (missing `=`), `ShoeTag=WShoeNum=8,5` (missing line break) |
| `Postura.dat`   | posturography recording (see below) |
| `Preview1.jpg`  | preview image |

## Postura.dat

One file holds **several acquisitions**, e.g. eyes open and eyes closed. The posturography
exams in the sample export contain 2 acquisitions of 51.2 s each.

### File header

| Offset | Type     | Sample       | Meaning |
|-------:|----------|--------------|---------|
| 0      | int16    | 3            | format version (1 in older files) ✅ |
| 2      | char[50] | `Max 50x60`  | plate name ✅ |
| 52     | int16    | 120          | columns (X axis) ✅ |
| 54     | int16    | 100          | rows (Y axis) ✅ |
| 56     | int16    | 5            | cell width in mm ✅ |
| 58     | int16    | 5            | cell height in mm ✅ |
| 60     | int16    | 255          | maximum sensor value ✅ (data are 0..255) |
| 62     | int16[k] | see below    | version-specific settings ❓ |

The settings are k = 6 values in version 3: `-1, 512 or 650, 0, 0, 1, 1`. The 512/650 differs
between exams but not with the duration ❓. Version 1 has k = 2 values. The header is
therefore 74 B (version 3) or 66 B (version 1). The parser detects k for unknown versions.

### Acquisition (repeated until the data end)

| Size       | Type             | Meaning |
|-----------:|------------------|---------|
| 2          | int16            | flag, -1 (VB `True`) for a stored acquisition ✅ |
| 4          | int32            | last frame index N; the acquisition has N + 1 frames ✅ |
| (N+1) × F  | Frame[N+1]       | frames ✅ |
| 2 × R × C  | uint16[R][C]     | mean of every cell over the frames where it is loaded ✅ |
| 100        | char[100]        | label, space padded, only in version 3 (empty in the samples) ✅ |

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

- **Sampling rate:** timestamps are exactly 40 ms apart, i.e. 25 Hz.
- **COP:** computed as `Σ(p · index) / Σp · cell size`, without a half-cell offset. It matches
  the pressure data in all frames checked in all exams.
- **Left/right:** a rotated plate may swap the feet.

## Open questions

1. Meaning of the header settings (version 3: `-1, 512/650, 0, 0, 1, 1`).
2. The per-frame int16 (~246).
3. Conversion of the raw values 0..255 to pressure (kPa or N/cm²) and the calibration.
4. Meaning of the acquisitions (which one is eyes open / eyes closed, other test types).
5. The formats of the other exam types (`Statica.dat`, `Dinamica.dat`, `TreadMILL.dat`, …).
