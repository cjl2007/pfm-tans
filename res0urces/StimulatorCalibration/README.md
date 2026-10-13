# Stimulator dI/dt calibration registry

These resources map MagPro X100 stimulator output (`%MSO`) to the realized
`dI/dt` reported by the stimulator. The CSV files are the canonical inputs for
TANS dose conversion. The XLSX files are formatted review copies derived from
the CSV files.

`build_calibration_workbooks.py` regenerates the XLSX review copies and
requires the optional `xlsxwriter` package. Runtime calibration selection and
dose conversion read only the registry JSON and canonical CSV files.

Calibration identity includes the physical coil, stimulator, waveform, and
current-direction configuration. A SimNIBS coil geometry alone is not a unique
calibration identifier: `Cool-B65` and `Cool-B65-A/P` currently use the same
available SimNIBS Cool-B65 geometry but have separate measured transfer tables.

## Full-curve policy

- Use an exact calibration entry whenever one is available.
- Canonical runtime tables cover 0-100% MSO in 1% increments.
- Direct observations are preserved in `observed_calibration.csv`.
- Values between observed points use piecewise-linear interpolation.
- Values above the highest observation use the measured-only linear slope,
  anchored to the highest observation for continuity. These rows are explicitly
  marked `model_extrapolated`; they are estimates, not measurements.
- The 0% row is fixed to the physical zero-output value of 0 A/us.
- Runtime interpolation is restricted to the complete canonical curve, so the
  dose module never silently extrapolates beyond its selected table.
- A missing exact calibration is a blocking condition by default.
- A similar-coil table may be used only through an explicit config override
  that identifies the substitute entry and records the user's justification.

## Included entries

- `magventure_x100_c-b60_biphasic_normal`: C-B60, used for SM-Hand motor
  threshold calibration. Source range: 5-80% MSO; 81-100% is model-estimated.
  The source 20% and 30% rows are linearly interpolated. The 5% amplitude is inferred from the acquisition
  sequence because the display had returned to 0% while retaining 7 A/us from
  the preceding pulse.
- `magventure_x100_cool-b65-ap_biphasic_normal`: Cool-B65-A/P, intended for
  BD2 treatment targeting. Photographed range: 5-85% MSO; 86-100% is
  model-estimated.
- `magventure_x100_cool-b65_biphasic_normal`: Cool-B65, intended for BioSelect
  treatment targeting. This combines the pre-existing full calibration with
  2026-09-10 revalidation points at 5%, 10%, 30%, 45%, and 60% MSO. The new
  measured phase-A values take precedence at those intensities. The observed
  curve reaches 100% MSO, so only 1-4% is interpolated from the physical zero
  anchor. `IMG_1562 2.jpeg` is intentionally excluded because its 125 A/us value
  is a stale readout retained after the amplitude was changed to 5%.

`realized_didt_A_per_us` is the scaling value used by TANS/SimNIBS. Values are
in amperes per microsecond; SimNIBS receives the equivalent value in A/s.
