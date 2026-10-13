# Measured-rMT dosing records

Every generated `DoseCalibrationReport.html` now includes an offline measured-rMT
workflow. Initial values and exploratory controls are **preview only**; they are
not recorded measurements, prescriptions, or evidence of treatment delivery.

![Technician-facing SAL workpage: lock rMT, inspect modeled intensity and network spread, then record and save](assets/sal-dose-workpage.png)

The illustrated page uses the same SAL target and saved field as the homepage
figure. **All displayed session inputs are hypothetical**: 50% rMT and 55%
treatment MSO. The model-selected 51.2% MSO is shown separately. This is a
walkthrough of the actual controls, not a participant's measurement record.

1. Enter the observed motor rMT (MSO or dI/dt), enter the recorder's name/initials,
   and confirm that the specified motor coil and matching SM-Hand reference pose
   were used. Click **Lock observed rMT**. This freezes measurement and target.
2. Enter the **treatment MSO actually used**, independently of the model-selected
   intensity. Confirm the treatment coil and target pose. The field is deliberately
   blank; dragging the exploration plot never populates it.
3. Click **Generate recorded-dose figure**. Download the PDF and JSON together;
   PNG is also available. Unlock to change the rMT or start a new record.

Suggested destination (browser-selected, not written automatically):

```
shared/session-records/<subject>/<target>/<session>/
  <subject>_<target>_RecordedDose_<UTC timestamp>.pdf
  <subject>_<target>_RecordedDose_<UTC timestamp>.json
  <subject>_<target>_RecordedDose_<UTC timestamp>.png
```

The figure adapts the workflow-style layout to the portable report: motor-coil
calibration at observed rMT, modeled on-target and hotspot-area curves, and four
hotspot comparisons (before onset, small hotspot, model selection, recorded
intensity). It uses the existing embedded, depth-correct cortical rasters; it does
not run FEM. Unlike the source-dependent figure-review renderer, it does not
include a motor E-field anatomy panel or weighted motor-field histogram. The PDF
is a high-resolution raster page, not a vector/SVG export.

The JSON records exact entered measurement, converted calibration values, actual
treatment intensity and computed metrics, separately labeled model selection,
policy, coil/pose identity, technician attestations, UTC recording timestamps,
and SHA-256 of the compact embedded lookup (`JSON.stringify(LOOKUP)`, UTF-8).
It is not a device log or signed audit trail; reported delivery is not verified.

No clinical decisions, calibrations, selection rules, or FEM results are changed.
Outside-calibration values are rejected without extrapolation. The configured
rMT-relative ceiling remains enforced for generating these modeled reports.
Below-area/overlap criteria, failed pose gates, or absent feasible model selection
are explicitly flagged rather than silently replaced by a different intensity.

Changing the recorded intensity or confirmations clears generated downloads;
unlocking discards the current record. Previously downloaded files remain
unchanged. Reloading starts unconfirmed: there is no persistent browser storage,
automatic clinical write-back, or silent overwrite of any earlier PDF. Browsers
choose the download destination. Save records before closing the page. Keep records
outside the published export: that package's integrity manifest fingerprints every
file, and adding session files inside it would invalidate package validation.

The implementation lives in `search/dose_session_ui.py`, included by the shared
HTML generator for both standalone pfm-tans and pfm-mefmri-driven runs. Existing
exports must be republished to receive the controls. The lookup/policy payload
does not change; the HTML version marker detects reports missing the workflow.

Browser regression (uses the maintainer's saved CinguloOpercular fixture;
private source data are not distributed with the repository):

```
node search/test_dose_session_browser.mjs /path/to/DoseCalibrationReport.html
```

All simulated confirmations and test PDFs are kept in a temporary directory, not
in the subject's export. Test values must never be interpreted as measurements.
