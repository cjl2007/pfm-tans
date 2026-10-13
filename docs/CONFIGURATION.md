# Configure TANS for a study

Start from [the annotated study YAML](../examples/study_config.yaml). Module
users also adapt [the shell fragment](../examples/pfm_mefmri_tans.sh). Replace
every `/path/to` and `REPLACE_WITH` placeholder before running. The templates
leave hardware and calibration identities unspecified. Choose other
settings as a study policy, record them, and test on a reviewed representative
subject before applying them across a cohort. Examples are not dosing protocols.

## Configuration precedence

1. The YAML supplies inputs, software, SM-Hand definition, and scientific defaults.
2. The driver expands its six documented `{...}` path placeholders.
3. CLI arguments override corresponding settings; subject ID/native-image paths
   are resolved from `--subject-dir`, and the driver builds the target list.
4. The pfm-mefmri wrapper supplies many CLI arguments **even when you did not
   explicitly set the shell variable**, because the wrapper has defaults.
5. `search.policy` supplies component/grid/selection rules. The resolved coverage
   fraction is written to a run-specific policy, not back into the shared JSON.

Consequently, changing `robustness.enabled` or `search.coverage_fraction` in YAML
alone will not control a module run if the wrapper supplies different values.
For module studies, explicitly set the overlapping `TANS_*` variables. Inspect
the printed driver command, `config/resolved.yaml`, `ResolvedSearchPolicy.json`,
and `MultinetworkRunManifest.json` to confirm the effective configuration.

## Common shell-to-CLI settings

Values below describe the current companion wrapper. Its installation-specific
paths must be set for your lab. This repository does not modify that wrapper.

| pfm-mefmri setting | Standalone equivalent | Meaning / default |
| --- | --- | --- |
| `TANS_ENABLE` | Launch the driver | `0` in a general upstream config; set `1` to include the module |
| `TANS_CODE_ROOT` | Path to `run_pipeline.py` | The repository's `pipeline/` directory |
| `TANS_CONFIG_FILE` | Positional YAML argument | Required study configuration |
| `TANS_PYTHON` | Launch interpreter | Wrapper default `python3`; use a configured environment |
| `TANS_OUTPUT_DIRNAME` | `--output` | Module default `tans`; module value must be one directory name |
| `TANS_PFM_DIR` | `--pfm-dir` | Empty prefers subject's `func/<func>/PFM/RidgePFM`; existing `RidgeFusion` fallback |
| `TANS_WORKERS` | `--workers` | Positive search worker count; default `6` |
| `TANS_TARGET_NETWORK` | `--target-network` | Metadata name or one-based metadata index; empty means SM-Hand only |
| `TANS_TARGET_SEARCH_SPACE` | `--target-search-space` | Named left-LPFC/MFG recipe or binary cortical CIFTI path |
| `TANS_COARSE_GRID_DENSITY` | `--coarse-grid-density` | Projected-target coverage fraction in `(0,1]`; default `0.85` |
| `TANS_INCLUDE_SM_HAND_CALIBRATION` | Omit / add `--skip-sm-hand-calibration` | Default `1`; `0` omits motor-reference simulations and cross-target dosing |
| `TANS_ROBUSTNESS_ENABLE` | `--robustness-enabled 1` or `0` | Default `1`; `0` prescribes the nominal fine winner |
| `TANS_SM_HAND_COIL_MODEL` | `--sm-hand-coil` | Physical motor-reference simulation geometry |
| `TANS_TREATMENT_COIL_MODEL` | `--treatment-coil` | Treatment simulation geometry |
| `TANS_CALIBRATION_REGISTRY` | `--calibration-registry` | Empty means bundled registry |
| `TANS_SM_HAND_DIDT_CALIBRATION` | `--sm-hand-didt-calibration` | Registry ID or explicit CSV |
| `TANS_TREATMENT_DIDT_CALIBRATION` | `--treatment-didt-calibration` | Registry ID or explicit CSV; set explicitly for ambiguous geometries |
| `TANS_ALLOW_SIMILAR_COIL_CALIBRATION` | `--allow-similar-coil-calibration` | Default `0`; substitution also requires justification |
| `TANS_CALIBRATION_SUBSTITUTION_JUSTIFICATION` | `--calibration-substitution-justification` | Recorded rationale for a deliberate substitute |
| `TANS_MAXIMUM_TREATMENT_RMT_RATIO` | `--maximum-treatment-rmt-ratio` | Default `1.20`; an explicitly empty shell value maps to `--disable-treatment-rmt-limit` |
| `TANS_HANDLE_ORIENTATION_PREFERENCE` | `--handle-orientation-preference` | `posterior_inferior` or `as_simulated` |
| `TANS_COIL_180_DEGREE_MAGNITUDE_EQUIVALENT` | `--coil-180-degree-magnitude-equivalent` | `1` asserts that the supported coil/objective allows a 180° physical flip |
| `TANS_START_FROM` | `--start-from` | `headmodel`, `targets`, `plan`, `search`, `reports`; default `headmodel` |
| `TANS_STOP_AFTER` | `--stop-after` | Same stage names; default `reports` |
| `TANS_RESUME` | `--resume` | Wrapper default `1`; standalone flag is opt-in |

`--func-dir` sets the default functional subfolder (`rest`) for standalone use.
Run `run_pipeline.py --help` for the complete current CLI. Shell booleans are
`0`/`1`; YAML robustness uses actual `true`/`false`, not strings.

## Paths and inputs

| YAML key | Set to |
| --- | --- |
| `inputs.categorical_networks` | Subject's single-map WTA CIFTI, or `null` when deriving binary support from weights |
| `inputs.network_probabilities` | Per-network probability/weight CIFTI, or `null` for WTA-derived 0/1 weights |
| `inputs.network_metadata` | Matching names/order/colors; can be `null` for WTA-only or weighted-only name inference |
| `inputs.vertex_area` | Matching cortical vertex-area CIFTI |
| `subject_adapter.charm_binary` | Absolute CHARM executable path |
| `subject_adapter.workbench` | Absolute `wb_command` path |
| `simulation.simnibs_python` | SimNIBS-enabled Python executable |
| `simulation.sm_hand_calibration_coil`, `simulation.treatment_coil` | Actual configured coil models |

Placeholders: `{subject}` is the subject directory basename; `{subject_dir}` is
its resolved path; `{pfm_dir}` is the selected network directory; `{func_dir}` is
the functional subfolder; `{tans_root}` is the output root; `{pfm_tans_root}` is
the repository root. There is no general environment-variable expansion in YAML.
The native image/surface adapter retains the [fixed layout](INPUTS_AND_OUTPUTS.md).

### Single-representation map inputs

| YAML key | Default | Meaning |
| --- | --- | --- |
| `input_fallbacks.enabled` | `true` | Synthesize the missing map counterpart; `false` requires both |
| `input_fallbacks.weighted_to_binary_percentile` | `null` | **Required for weighted-only inputs**; e.g. `90` retains the upper 10% of positive cortical values, including ties |
| `input_fallbacks.weight_polarity` | `positive` | Use positive values, negative-value magnitudes (`negative`), or absolute magnitudes (`absolute`) |
| `input_fallbacks.weight_normalization` | `auto` | Preserve [0,1] weights; divide each map by its maximum if >1. `max` always max-scales; `none` requires [0,1] |
| `input_fallbacks.map_names` | `null` | Optional weighted-only row names, e.g. `[MyFC]`; overrides metadata naming/color selection |

The percentile is vertex-based across nonzero, finite, selected-polarity cortex,
before anatomical masking; it is not a p-value or an E-field threshold. Unknown
option names are rejected. See [complete recipes, overlap rules, and provenance](NETWORK_INPUTS.md).

## Network and anatomical search region

Select the treatment network with `TANS_TARGET_NETWORK` / `--target-network`.
Names are preferable to numeric indices when sharing configs because indices
depend on metadata ordering. The standard SM-Hand template uses
`Somatomotor_Hand` restricted to `L_G_precentral`.

The treatment region defaults to
`glasser_predefined_plus_subject_specific_mfg`: bundled left-LPFC envelope OR the
subject's `L_G_front_middle`. For another region, supply a reviewed binary
cortical CIFTI. Its vertex indexing must match the networks. Do not assume a
different network name changes the anatomical region or hemisphere.

SAL / DLPFC is the README's illustration, not a required target network or
cortical zone. Set both the target name and the appropriate binary cortical
search mask using `TANS_TARGET_SEARCH_SPACE` or `--target-search-space`. The
mask must use the same cortical CIFTI indexing as the inputs; an MNI volume or
coordinate is not a substitute. Changing the name does not merge network labels
or construct a new anatomical mask automatically.

The top-level driver builds **one treatment target plus optional SM-Hand**.
It reads the SM-Hand template in `targets`, but replaces arbitrary treatment
entries there. Do not advertise YAML-only multi-target lists as a top-level CLI
feature. Run separate output roots for separate treatment targets.

## Coil center above the anatomical zone

Coil-center eligibility is enabled by default for new searches:

```yaml
coil_center_constraint:
  enabled: true
```

Set `enabled: false` to opt out. A target's `coil_center_constraint` block
overrides the study-level block; omitted settings inherit the study default.
The driver preserves the SM-Hand template's override. Driver-generated
treatment targets use the study-level setting.

For each simulated pose, the saved SimNIBS transform supplies the coil center
and inward normal. The first forward intersection with either native pial
surface must lie inside `SelectedAnatomicalMask.dscalar.nii`: precentral gyrus
for the standard SM-Hand configuration. Membership is the barycentrically
interpolated binary mask at that intersection, with a threshold of 0.5.
This tests the full anatomical zone, independently of network weights or
retained target components. Missing geometry, an empty/invalid mask, or a ray
that misses the cortex cannot qualify an enabled pose.

The constraint is a hard eligibility gate for coarse and fine winners and
robustness finalists. E-field scores still rank the eligible poses. Off-zone
perturbations remain in robustness calculations so sensitivity estimates
include placement errors. Grid construction and E-field simulation coverage
are unchanged; the check uses the exact transforms saved by the simulations.
If no nominal pose passes all enabled gates, selection fails and records the
candidate results without choosing a fallback winner.

`CoarsePoseScores.tsv` records the pass/fail flag, intersection coordinates,
hemisphere, triangle index (zero-based), distance, and mask value.
`CoilCenterConstraint.json` records the rule and input hashes. Completed
searches without a matching check cannot be silently reused with this option
enabled. Use a new output root to replan/rescore existing simulations; changing
the YAML does not replace previously exported prescriptions. Anatomical
eligibility alone does not establish whether a pose will elicit an MEP.

## Objective and search resolution

| YAML / policy setting | General default | Interpretation |
| --- | --- | --- |
| `search.map_mode` | `weighted` | Treatment objective: target weight multiplied by cortical area; `binary` uses categorical target area |
| `targets[SM-Hand].map_mode` | `weighted` | Motor target objective, independently specified |
| `search.coverage_fraction` | `0.85` | Required projected coverage, not a raw center count |
| `search.orientation_start_degrees`, `orientation_stop_degrees`, `orientation_step_degrees` | `0`, `180`, `15` | Coarse orientations `0,15,...,165`; upper endpoint excluded |
| `fine_search.radius_mm`, `spacing_mm` | `10`, `4` | Scalp-geodesic local refinement |
| `fine_search.relative_orientations_degrees` | `[-10,-5,0,5,10]` | Offsets around coarse winner |
| `simulation.coil_distance_mm` | `1` | Modeled coil-to-scalp distance; must reflect the intended setup |
| `simulation.didt_aperus` | `1000000` | Historical key name; passed to SimNIBS as **A/s** (1 A/µs reference) |
| `simulation.hotspot_percentiles` | `99.0` through `99.9` | Relative field thresholds averaged by pose scoring |

Weighted versus binary is a real scientific objective choice, not just a plot
style. The SM-Hand map mode is not automatically changed by `search.map_mode`.
Both internal map representations are used in either mode, but a missing input
can now be synthesized. With WTA-derived 0/1 weights, the weights add no
information beyond binary membership.

For advanced component/grid/gate changes, copy
[`default_search_policy.json`](../search/default_search_policy.json) into the
study and point `search.policy` to it. Important defaults are minimum component
size `10` vertices / `25 mm²`, relative weighted mass `0.10`, maximum `40` coarse
centers, and minimum target/global peak ratio `0.95`. If no component passes,
the largest weighted component is retained as an explicit fallback. Review that
case rather than assuming every threshold was satisfied. See the
[scientific search reference](two_stage_search_configuration.md).

## Robustness and prescription

`robustness.enabled: true` runs placement-error sampling and selects the robust
prescription. Defaults include up to three near-optimal finalists, positional
radii `[3,5,10] mm`, eight directions per radius, and angular errors `[0,5,10]°`.
The selection condition uses `5 mm / 5°` and the lower-tail mean of the worst
10%. These are configurable study assumptions, not guarantees about registration.

Disabled mode uses the nominal fine winner, skips the additional FEM work, and
marks robustness as not evaluated. Never change this mode inside a previously
planned run: the driver explicitly rejects that mismatch. Use a new output root.

The default supported-coil handle policy prefers posterior/inferior orientation
between magnitude-equivalent physical poses. Automatic flipping requires an
explicit 180° magnitude-equivalence assertion. For another coil or a signed/vector
objective, do not carry this assumption forward without justification. Set
`prescription.handle_orientation.mode: as_simulated` and review the equivalence
setting. The CLI spelling is `posterior_inferior`; the YAML spelling for that
mode is `prefer_posterior_inferior`.

## Hardware calibration and dosing

Choose calibration by **physical coil, stimulator, waveform, and current
direction**, not just a matching geometry filename. The bundled Cool-B65 and
Cool-B65-A/P tables are distinct even though they share a SimNIBS geometry.
Ambiguous automatic matching stops until an ID is specified.

Supported inputs are registry IDs or explicit CSV paths. CSV columns require
`amplitude_pct_mso` and `realized_didt_A_per_us` (or `didt_A_A_per_us`), at least
two finite rows, and strictly increasing MSO and dI/dt. An explicit CSV is treated
as your assertion that it belongs to the configured hardware; the software does
not independently establish that identity. Prefer a study registry with complete
hardware metadata when distributing a protocol.

Review [calibration provenance](../res0urces/StimulatorCalibration/README.md):
some canonical table rows are model-estimated beyond the original observations.
Runtime interpolation stays inside the selected **table** range; this does not
mean every row in that range was directly measured.

`dosing.maximum_treatment_rmt_ratio` defaults to `1.20`: an upward bound on
treatment MSO relative to motor-coil rMT MSO, not a fixed treatment intensity and
not a universal safety claim. YAML `null`, an explicitly empty shell value, or
`--disable-treatment-rmt-limit` disables it. Disabling a software limit does not
establish that an intensity is safe or appropriate for a study.

Observed rMT and actual treatment MSO are **session-time inputs** to the offline
HTML. Do not put hypothetical values in a study config and treat them as patient
measurements. [Measured-dose records](measured_dose_records.md) explain the lock,
confirmation, and PDF/JSON download workflow.

The report generator also has lower-level modeling options (weighted motor
quantile, minimum hotspot area, dose-selection metric). The top-level report
orchestrator currently forwards the rMT ceiling and resolved coil calibrations,
not arbitrary extra `dosing:` keys. Adding a YAML key does not make it active.
Changes to those modeling rules need a deliberate generator/integration review,
not an undocumented study override.

## Retention, compute, and reproducibility

`retention.profile` accepts `standard`, `debug`, or `research`. Standard and
debug prune per-center raw `Simulation/` work directories after successful
mapping; research retains them. Saved mapped fields and benchmarks support
reporting/validation. The profiles also record finalization preferences, but
do not assume a global disk-quota manager or automatic archival service.

Use `research` if subsequent work needs the raw FEM work products, and budget
disk accordingly. `--workers` governs search concurrency; it does not cap every
external program's threading or memory consumption.

Freeze the study YAML, shell overrides, policy, calibration tables, input
derivatives, and software revision. Inspect effective run manifests before
scaling up. For changed scientific settings, prefer a new output folder over
reusing artifacts through `--resume`.
