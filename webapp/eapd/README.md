# web_apd (Browser-only APD)

A standalone APD editor/parser/generator that runs fully in the browser (no backend service required).


[Live demo](https://tedlaz.github.io/eapd)

- Project path: `~/prj/web_apd`
- Main entry: `index.html`
- Styles: `styles.css`
- Core logic: `app.js`
- Data/config files: `metadata.js`, `table_options.js`, `osyk_kad.js`, `osyk_eid.js`, `topic.js`, `kpk.js`, `epidotiseis.js`

---

## 1) Run Modes

### A) Direct file mode (no server)
Open:

- `file:///home/ted/prj/web_apd/index.html`

### B) Static server mode (optional)
```bash
cd ~/prj/web_apd
python3 -m http.server 8080
```
Open:

- `http://localhost:8080`

Both modes are supported.

---

## 2) Architecture

The app is split into:

- **UI markup** in `index.html`
- **Styles** in `styles.css`
- **Business logic + parsing/generation** in `app.js`
- **Data/config JS objects** loaded by `loader.js`

`loader.js` loads in this order:
1. `metadata.js`
2. `table_options.js`
3. `osyk_kad.js`
4. `osyk_eid.js`
5. `kpk.js`
6. `epidotiseis.js`
7. `topic.js`
8. `app.js`

---

## 3) Data Files (Human Editable)

### `metadata.js`
Holds APD line/column schema (line sizes, field numbers, types, allowed values).

### `table_options.js`
General dropdown values by field number (except where overridden by dedicated files/rules).

### `osyk_kad.js`
Data source for field **No 31** search modal (KAD).

### `osyk_eid.js`
Data source for field **No 35** search modal (EID).

### `topic.js`
Data source for field **No 3** lookup (`code`, `per`).
- No 3 is selected via lookup modal (same UX pattern as No 31/35).
- No 4 is auto-filled from matched `per` and normalized to uppercase accentless Greek.

### `kpk.js`
Description lookup for field **No 37**.
Each record:
- `code`
- `description`
- `emp` (unused: contributions are entered manually)
- `tot` (unused: contributions are entered manually)

Example:
```js
window.KPK_VALUES = [
  { code: "101", description: "ΜΙΚΤΑ ΙΚΑ-ΤΕΑΜ", emp: 13.37, tot: 35.16 }
]
```

### `epidotiseis.js`
Data source for `StoixeiaEpidotiseon` fields 60–65.
Each record:
- `code`
- `per`
- `pemployee`
- `vemployee`
- `pcompany`
- `vcompany`

Example:
```js
window.EPIDOTISEIS_VALUES = [
  { code: "102", per: "Εργατική εισφορά", pemployee: 0, vemployee: "*", pcompany: 3.45, vcompany: "*" }
]
```

---

## 4) Supported Functional Areas

## 4.1 Form Builder

Supports APD hierarchy:
- `GenikaStoixeia`
- `StoixeiaAsfalismenou`
  - `StoixeiaAsfalisis`
    - `StoixeiaEpidotiseon`
- `TelosArxeiou`

Constraints in UI:
- Only one `GenikaStoixeia` (always top)
- Only one `TelosArxeiou` (always bottom)
- New `StoixeiaAsfalismenou` inserted before EOF
- Nested child creation preserved

Collapsible sections:
- `StoixeiaAsfalismenou` and `StoixeiaAsfalisis` support fold/unfold
- When multiple exist, they auto-collapse
- Folded summaries:
  - Asfalismenou: surname + name
  - Asfalisis: No 42 code + description

## 4.2 Parse Existing APD File

- Upload APD text file
- Choose parse encoding:
  - `utf-8`
  - `iso-8859-7`
  - `windows-1253`
- Parse fixed-width lines to structured data
- Auto-fill form from parsed records

Typed decode behavior:
- `F/F3` values decoded with decimal placement
- `D` dates rendered as `dd/mm/yyyy`
- numeric code fields preserve/normalize code semantics
- No37 is kept exactly as stored in the file (free code field, no dropdown)
- import performs **no recalculation**: the form shows the file as-is and flags "Needs recalculation"

## 4.3 Generate APD File

- Recalculate + validate first
- Generate fixed-width APD text
- Show text in output pane
- Download `.txt` with selected encoding:
  - `utf-8`
  - `iso-8859-7`
  - `windows-1253`

---

## 5) Field Rules & Business Logic

## 5.1 Header totals (GenikaStoixeia)

From all `StoixeiaAsfalisis` rows:
- **No 16** = sum of No 43
- **No 17** = sum of No 45
- **No 18** = sum of No 51 (**only No 51**, not subsidy totals directly)

## 5.2 Asfalisis + Epidotiseis scoping

If multiple `StoixeiaAsfalisis` exist under the same insured person:
- each `StoixeiaEpidotiseon` affects **only its parent `StoixeiaAsfalisis`**.

Per parent `StoixeiaAsfalisis`:
- **No 49** = sum of its child Epidotiseon No 62
- **No 50** = sum of its child Epidotiseon No 64
- **No 52** = sum of its child Epidotiseon No 65
- **No 51** = No 48 - No 52 - No 53

## 5.3 Contributions No 37 / No46/47/48

- **No 37** is a free code field: the code is shown as-is and, when it exists in `kpk.js`,
  its description is displayed next to it. It drives no calculation.
- **No 46** (employee) and **No 47** (employer) are entered manually.
- **No 48** = round(No46 + No47, 2) — auto-calculated and read-only.

## 5.4 Epidotiseis rules (No60–No65)

Using `epidotiseis.js` row matched by No60 code:
- No60 uses `code + per` in dropdown
- **No61** = `pemployee`
- **No62**:
  - if `vemployee == '*'` => round(No45 * pemployee / 100, 2)
  - else fixed `vemployee`
- **No63** = `pcompany`
- **No64**:
  - if `vcompany == '*'` => round(No45 * pcompany / 100, 2)
  - else fixed `vcompany`
- **No65** = round(No62 + No64, 2)

No61–65 are auto-calculated/read-only.

## 5.5 Eligibility rule to add StoixeiaEpidotiseon

`+ Στοιχεία Επιδότησης` is allowed only when parent `StoixeiaAsfalisis` has:
- No32 = 1
- No33 = 1

Otherwise an error is shown.

## 5.6 No44 / No56 rule

In `StoixeiaAsfalisis`:
- if No56 = 0 -> No44 = 0
- if No56 = 1 -> No44 > 0

## 5.7 No40/No41 conditional requirement

No40 and No41 may be empty only when No42 in:
- 002, 004, 005, 006

Otherwise both are required.

## 5.8 No36=14 conditional blank rule

When No36 = 14, the following fields may be empty (blank in output):
- No32, No33, No34, No56, No57

This is enforced in recalculation/validation and in generated fixed-width output.

## 5.9 Optional fields

- No54 and No55 are optional (`__` marker handling)
- No58 is optional (empty maps safely to default behavior)

## 5.10 Date output rule

If a date field is empty, output writes:
- `00000000`

## 5.11 Text normalization on recalculation

Text fields (`Α`, `Χ`) are normalized to:
- uppercase
- accent-insensitive Greek forms (e.g., ά -> Α, ϊ -> Ι)

## 5.12 Length validation

Recalculate performs max-length checks against metadata for all fields.
For numeric/date payload checks, length is validated on effective payload digits.

---

## 6) UI Behavior

- Recalculate button has visual pulse animation feedback
- Section status pills:
  - green = complete
  - orange = incomplete
- Detailed recalculation error popup includes field-level reasons
- KAD/EID/TOPIK modal search supports accent-insensitive Greek matching
- No3, No31 and No35 are read-only and set via modal selection
- No4 is auto-filled from topic.js (`per`) after No3 selection
- Sticky top toolbar remains visible while scrolling
- Help page opens in modal overlay
- Recalculation status indicator: red (needs recalculation) / green (ok)

---

## 7) Encoding

Both generate and parse support:
- `utf-8`
- `iso-8859-7`
- `windows-1253`

---

## 8) Important Notes

- This is a browser-only implementation. No FastAPI runtime is required.
- All formulas are rounded to 2 decimals where specified.
- If data files are manually edited, refresh the page to reload values.
- Keep `kpk.js` and `epidotiseis.js` syntactically valid JavaScript.
  - For decimal commas, use strings (e.g. `"16,82"`) or dot-decimals (`16.82`).

---

## 9) Quick Validation Checklist

1. Create one Asfalisis, type No37 (description appears next to it) and No46/No47, and verify No48 = No46 + No47.
2. Add Epidotiseon with No60 and verify No61..65 behavior.
3. Verify No49/50/52 updates only on parent Asfalisis.
4. Verify header No18 equals sum of Asfalisis No51 only.
5. Verify No3 lookup fills No4 automatically from topic.js.
6. Parse a file and confirm every field is shown exactly as stored (no recalculation on import).
7. Verify No36=14 allows blanks for No32/33/34/56/57.
8. Generate with each encoding and verify output file downloads.

---

## 10) Maintenance Tips

- Add/extend KPK codes in `kpk.js`
- Add/extend subsidy code logic in `epidotiseis.js`
- Add general dropdown lists in `table_options.js`
- Keep metadata aligned with APD specification in `metadata.js`
