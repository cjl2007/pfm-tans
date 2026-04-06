# Troubleshooting

## Preflight fails immediately

Cause:
- missing subject files
- bad config path

Check:
- `tans_validate(...)`
- config values under `cfg.paths.*` and `cfg.inputs.*`

## `wb_command` or FSL command fails

Cause:
- tool not installed or not on `PATH`

Check:
- `wb_command -version`
- `fslmaths -version`

## SimNIBS head model step fails

Cause:
- `charm` missing
- incompatible SimNIBS install
- subject anatomy problem

Check:
- `charm --version`
- `cfg.paths.simnibsRoot`

## No candidate targets found

Cause:
- target threshold too strict
- search space too restrictive
- sulcal/medial-wall masking removed all viable vertices

Actions:
- lower `cfg.target.thresholdPercentile`
- inspect `ROI/TargetNetwork+SearchSpace+SulcalMask.dtseries.nii`
- verify search-space definition

## Tolerability metric missing

Cause:
- missing tolerability dataset
- malformed columns
- unmappable SMART/EEG labels
- missing subject EEG positions file

Result:
- PFM-TANS fails clearly during tolerability-model setup instead of silently skipping scoring

## One candidate fails but others continue

Expected behavior:
- multi-candidate mode is failure-tolerant by design
- inspect `CandidateSummary.tsv` and the failed candidate directory for details
