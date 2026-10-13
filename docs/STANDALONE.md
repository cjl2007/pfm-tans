# Standalone execution

Standalone uses the same `pipeline/run_pipeline.py` called by pfm-mefmri. It
does not need pfm-mefmri code, raw fMRI, or a running upstream pipeline. It does
require completed derivatives matching the [input contract](INPUTS_AND_OUTPUTS.md).

## Prepare the subject

Use a subject directory named for the subject identifier, with the documented
`anat/T1w`, `anat/T2w`, and `anat/MNINonLinear` structure. The driver derives the
subject ID from the final directory name; surface filenames must use that ID.

Both native T1w **and** native T2w are required. Surfaces alone do not replace
head-model inputs. Original images and the native-to-ACPC affine must represent
the same subject and coordinate history. Renaming an unrelated transform or
resampled image is not a valid conversion.

Network derivatives may live elsewhere. Supply their folder with `--pfm-dir`,
and edit `inputs.categorical_networks` / `inputs.network_probabilities` if their
filenames differ from the defaults. Preserve fs_LR 32k cortical correspondence,
categorical names, weighted-map ordering, and matching metadata. Either WTA labels
or probability/weight maps alone are sufficient with the documented
[input fallbacks](NETWORK_INPUTS.md): WTA produces 0/1 weights; weighted-only
input needs a user-set percentile to produce binary support. Activation/FC maps
must already be represented as compatible cortical CIFTIs, not arbitrary NIfTI volumes.

## Configure and launch

Copy [the study YAML](../examples/study_config.yaml), edit software paths and
study settings, then select the target on the command line:

```bash
/opt/SimNIBS-4.5/simnibs_env/bin/python \
  /opt/pfm-tans/pipeline/run_pipeline.py \
  /data/MyStudy/config/tans.yaml \
  --subject-dir /data/MyStudy/SUB001 \
  --pfm-dir /data/MyStudy/SUB001/func/rest/PFM/RidgePFM \
  --output /data/MyStudy/SUB001/tans \
  --target-network CinguloOpercular \
  --target-search-space glasser_predefined_plus_subject_specific_mfg \
  --workers 6
```

This runs SM-Hand calibration plus one treatment network. Omit
`--target-network` to run **SM-Hand only** with the normal template. The driver
constructs its target list from the SM-Hand template and CLI selection; adding
treatment entries to YAML alone does not select them. The top-level CLI accepts
one treatment network per invocation. Use separate run roots for separate study
targets or comparisons, retaining their provenance.

The default search space is the bundled left-LPFC envelope united with that
subject's left middle-frontal label. Supply an absolute path to a compatible
binary cortical CIFTI via `--target-search-space` for another region. Changing
the network name does **not** automatically select appropriate search anatomy.

`--skip-sm-hand-calibration` produces treatment-only targeting and pose exports;
it does not fabricate motor-reference dosing. A motor template is not needed
when skipping calibration for a non-motor treatment target. Treatment calibration resolution still
occurs, so configure those inputs explicitly.

## Stage-by-stage execution and resume

Append `--stop-after plan` to stop after head modeling, target preparation, and
search planning. This is **not a dry run**: CHARM and preparation actually run.
Review head-model and target/search anatomy before simulation.

Continue by repeating the same config, subject, network, coil/calibration,
search-space, and output arguments, adding:

```bash
--resume --start-from search --stop-after reports
```

For reports after a completed search, use the same settings with:

```bash
--resume --start-from reports --stop-after reports
```

Existing output roots require `--resume`. Later stages require validated earlier
artifacts. Incomplete directories can stop resume and require review; they are
not automatically deleted. Changes to networks, geometry, coils, objectives,
or policy should use a fresh root. Resume does not guarantee automatic
invalidation of every changed setting.

## Resource planning

`--workers` is search concurrency, not a guarantee of CPU or memory use. CHARM
and each FEM job have their own demands. Choose concurrency for your machine
and monitor memory/disk. More target coverage, finer sampling, and robustness
increase work; no universal runtime or RAM requirement is claimed.

Set `robustness.enabled: false` or pass `--robustness-enabled 0` only when the
study intentionally chooses nominal fine selection. It changes the prescribed
pose and omits placement-error simulations; it is not just a rendering option.
The general example keeps robustness enabled.

## Review and validate

Open `reports/index.html`. With the configured interpreter:

```bash
python /opt/pfm-tans/pipeline/validate_completed_run.py /data/MyStudy/SUB001/tans
python /opt/pfm-tans/pipeline/publish_operator_export.py \
  /data/MyStudy/SUB001/tans/shared/export_SUB001 --validate-only
```

The first command writes a receipt under `validation/` and requires the saved
run artifacts; the second validates an existing portable package without
republishing it. Neither proves registration accuracy or grants clinical
clearance. See [workflow review](WORKFLOW.md) and [troubleshooting](TROUBLESHOOTING.md).

## Existing RidgeFusion maps

Without `--pfm-dir`, the driver prefers `PFM/RidgePFM` and falls back to an
existing `PFM/RidgeFusion` directory. An explicit `--pfm-dir` always wins.
The input filenames in your YAML must match the saved maps; selecting a legacy
folder does not automatically rewrite `RidgePFM_*` filenames.
