# Study setup examples

- [study_config.yaml](study_config.yaml): annotated TANS YAML for either launch
  route. Edit installation paths and hardware/scientific settings before running.
- [pfm_mefmri_tans.sh](pfm_mefmri_tans.sh): TANS settings to merge into or source
  from a complete pfm-mefmri study configuration. It is not a standalone launcher
  or a complete upstream processing configuration.

No hardware is selected by default. Replace every `/path/to` path and
`REPLACE_WITH` calibration ID in the YAML using your actual motor and treatment
coils, stimulator, waveform, and current direction. Blank shell hardware overrides
retain the YAML settings. Enable `TANS_ENABLE=1` only after configuring them.

Target names, subject paths, search-space selection, stage controls, and workers
are supplied via the wrapper/CLI as documented. The YAML is not evaluated as a
shell script and does not support arbitrary environment variables.

Have only WTA labels or a probability/activation/FC target map? Merge the
[single-representation input settings](../docs/NETWORK_INPUTS.md) into the same
YAML. Weighted-only input requires an explicit percentile; a treatment-only map
does not supply the SM-Hand network required for motor-referenced dosing.

See [recommended integration](../docs/PFM_MEFMRI.md),
[standalone execution](../docs/STANDALONE.md), and
[the configuration reference](../docs/CONFIGURATION.md). No participant data or
real session measurements are included in these configuration examples.
