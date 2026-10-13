# Dose Preview and Diagnostic Landmarks

## Workflow Default

This is the current TANS dose interface (`tans-interactive-dose-v1`).
Fresh report generation, resumed report stages and portable operator publication
all use it. No extra configuration switch or simulations are required.

Complete embedded lookups can be refreshed without their simulation source files.
Legacy lookups are upgraded from recorded fields with all original selections
checked for equality. Missing sources, incompatible payloads or changed numerical
results stop the upgrade explicitly, instead of publishing a partially working
page. Private report changes are staged and validated before replacement;
portable export publication retains its existing whole-package validation and
archival behavior. Previously distributed copies are not remotely updated.

The offline dose page shows a fixed-camera technician view of the prescribed
treatment coil outline over folded pial cortex. Only the visible cortex meeting
the motor-derived absolute E-field threshold is colored by individualized
network labels. Grayscale cortex supplies anatomical context; hidden cortex is
not projected through foreground surfaces. Both hemispheres share a depth buffer.

The preview updates with the checked treatment intensity. The graph's filled
dot can be dragged, clicked to a new position, or adjusted with the keyboard.
It controls treatment MSO only: the measured motor rMT, motor-derived V/m cutoff,
pose and model-selected dose stay fixed. The latter remains marked by a hollow
diamond; a reset command returns the preview to that selection. The graph uses
0.1% MSO steps and clamps upward movement to the configured ceiling (rounded down
to an allowed step). Direct intensity entry remains synchronized with the dot.

The model-selected intensity remains separately visible. Preview metrics use the full original
cortical samples and their areas, not pixels. Surface colors use interpolated
field values for display only. A first threshold crossing is not an established
minimum effective dose or a clinically validated treatment range.

## Input and Selection

- Either motor-coil rMT MSO or dI/dt may be entered within the calibration range.
- dI/dt is converted to MSO by piecewise interpolation of the motor-coil table.
  The entered value is not rounded to the nearest precomputed rMT row.
- Exact existing rMT entries retain the recorded selection. In-between inputs
  rescore the same fixed-pose treatment-intensity grid using the same area,
  selectivity, tie-breaking and upward-limit rules. This requires no new FEM.
- Selection can change discretely as vertices enter the hotspot. This is a
  sampled-surface model, not a smooth physiological dose-response estimate.
- Out-of-calibration inputs are rejected; extrapolation is not allowed.
- The optional upper limit remains relative to the inferred motor-coil rMT MSO.
  Above-limit previews are blocked. Passing this limit check does not establish
  clinical safety or supersede the approved treatment protocol.

## Landmarks

The plot records separate first crossings for any modeled cortex and for retained
target cortex. It also shows the first sampled intensity meeting the configured
minimum total hotspot area plus positive target overlap, and the selected dose.
Exact diagnostic rows are inserted into the previously downsampled curve.

No midpoint prescription has been introduced. In one saved robust/robust example
at a hypothetical rMT of 60% MSO, onset is about 54.6%, first area-qualified sample
is 58.4%, and selection remains 60.2%. The onset-to-selection midpoint, 57.4%, has
only 32.7 mm2 above threshold and fails the current 50 mm2 criterion.

## Outputs and Provenance

`DoseLookup.json` now embeds the calibrated intensity grid, cumulative area
statistics, diagnostic landmarks, and compact raster preview assets. The HTML
embeds the lookup and needs no server, external libraries or anatomical files
at viewing time. Per-target operator exports filter all previews and landmarks
to their matching target. Existing trajectories, FEM fields and pose choices are
unchanged.

`refresh_dose_visuals.py --lookup <DoseLookup.json> --output <new-directory>`
creates a separate review without replacing an existing directory. It first
reproduces all recorded selections and displayed curve rows from their source
fields when upgrading a legacy lookup, failing on differences. Already embedded
payloads are validated and reused. The optional `--priors` selects the priors color
table; otherwise the categorical CIFTI label table supplies network colors.

The preview represents the planned simulated pose, not measured registration
accuracy, delivered treatment or a verified Brainsight import. It does not resolve
the separately documented neuronavigation coordinate-label validation issue.

## Confirmed session records

The preview is explicitly unconfirmed until the technician locks the observed
rMT with the matching motor-reference coil/pose attestation. The actual treatment
intensity is entered and confirmed separately; it is never autofilled from the
model selection. The offline page then creates a workflow-style PDF/PNG and a
JSON record. Input edits or unlocking clear pending downloads; reload does not
restore a confirmed record. See [measured-dose records](measured_dose_records.md).
