# Troubleshooting

Start with the failed command/log and `config/resolved.yaml`. Keep failed
artifacts for diagnosis; do not delete a run or change scientific settings just
to bypass a validation error.

| Symptom | Check / next step |
| --- | --- |
| Missing `run_pipeline.py` | `TANS_CODE_ROOT` must end in `/pipeline`; use a revision containing the Python refactor |
| Python import failure | Check both the launch interpreter (`TANS_PYTHON`) and `simulation.simnibs_python`; run the [environment checks](INSTALL.md) |
| Missing command | Initialize FSL/FFmpeg on `PATH`; verify absolute CHARM and Workbench paths |
| Missing native images | The adapter requires exact native `anat/T1w/T1w.nii.gz` and `anat/T2w/T2w.nii.gz`; do not substitute restored/ACPC images |
| Missing surface, sulc, ROI, or coil STL | Follow the full [input contract](INPUTS_AND_OUTPUTS.md), including report-stage inputs |
| CIFTI mismatch | Check both hemispheres, fs_LR vertex indexing, dense-axis ordering, probability-map count, and metadata order |
| Network not found / eligible target empty | Check categorical names versus metadata and the intersection with the intended anatomical mask |
| Only one map representation is available | Use the [WTA / weighted-only recipes](NETWORK_INPUTS.md); weighted-only inputs require an explicit percentile |
| Missing `Somatomotor_Hand` in a custom target map | Supply a real motor target or skip calibration; TANS cannot infer it from a treatment-only map |
| Network sources or fallback settings changed on resume | Keep the earlier run intact and use a fresh output directory; do not reuse stale derived targets |
| Only SM-Hand ran | Set `TANS_TARGET_NETWORK` / `--target-network`; adding a treatment YAML entry alone is insufficient |
| Search region is still frontal after changing network | Explicitly select the desired binary CIFTI via the search-space setting |
| YAML change seems ignored | Inspect CLI overrides supplied by the wrapper; see [precedence](CONFIGURATION.md#configuration-precedence) |
| Calibration is ambiguous | Set the physical hardware's registry ID; Cool-B65 and Cool-B65-A/P share geometry but not calibration |
| Similar-coil substitution rejected | Default behavior is intentional; a deliberate substitution requires an explicit entry, enable flag, and justification |
| Existing output root rejected | Use `--resume` only with the same inputs/policy, or select a new output root |
| Robustness mode mismatch on resume | Do not mix modes in an existing search plan; create a distinct run root |
| Missing dose page | Cross-target dosing requires both SM-Hand and a separate treatment target with matching validated sources |
| Dose PDF not enabled | Lock a valid observed rMT, enter actual treatment MSO, and provide both confirmations; unsupported intensities are rejected |
| Browser closes and record disappears | Records are not persisted automatically; download PDF and JSON before closing |
| Package hash validation fails after saving a session | Keep session records outside `shared/export_<subject>/`; validate the original immutable package |

## Incomplete CHARM / head-model preparation

The driver does not overwrite an incomplete headmodel directory. Inspect the
CHARM log and actual completion state. The lower-level
`prepare_subject.py --resume-completed-charm` recovery path is only for verified
completed native CHARM outputs whose later scalp/surface adaptation was interrupted.
It checks recorded identity and image content; it is not a force-resume flag for
an unfinished or incompatible model. Review the command's `--help` before use.

## Scalp geometry and provenance

Read [exterior-skin validation](exterior_skin_validation.md). A scalp mesh is not
valid just because a GIFTI file exists. Changed scalp geometry invalidates grids
and simulations derived from the old mesh. Do not mix an updated adapter with
stale downstream search products.

## Reports and operator exports

Resolve the first recorded failure, then resume the appropriate stage using the
same config and artifacts. A current complete embedded dose lookup can refresh
without recomputing FEM; an older lookup may require its original saved fields.
Missing source data must not be bypassed with another subject or another pose.

The full-run validator writes `validation/CompletedRunValidation.json`; portable
package validation checks hashes, links, and identity. These are software checks,
not a substitute for artifact review or a verified navigation import.

For a bug report, provide software revisions, the failed command, relevant error
text, and a **redacted** configuration. Do not attach real operator packages,
native MRI, participant identifiers, or session records to a public issue.
