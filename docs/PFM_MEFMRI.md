# Recommended workflow: the pfm-mefmri TANS module

Use pfm-mefmri to prepare anatomy, process fMRI, and obtain individualized PFM
maps, then include `tans` downstream. pfm-mefmri handles subject selection and
its processing chain; pfm-tans owns the targeting implementation.

## Configure a study

1. Install both repositories and the [TANS dependencies](INSTALL.md).
2. Start from your **complete pfm-mefmri study config**, including upstream
   anatomy, functional, and PFM settings. The TANS fragment is not a replacement.
3. Copy [study_config.yaml](../examples/study_config.yaml) into the study and edit
   installation-specific software paths and scientific settings.
4. Copy [pfm_mefmri_tans.sh](../examples/pfm_mefmri_tans.sh) into the study's config,
   or source a study-owned copy at its end.
5. Confirm shell and YAML settings agree with the study plan. See
   [configuration precedence](CONFIGURATION.md#configuration-precedence).

The generic templates require software paths, coils, calibration IDs, and a
treatment network to be filled in. Set `TANS_ENABLE=1` after sourcing the edited
fragment, since the untouched fragment intentionally starts disabled.

Key settings:

```bash
PFM_ENABLE=1  # produce maps during a full processing run
TANS_ENABLE=1
TANS_CODE_ROOT="/opt/pfm-tans/pipeline"
TANS_CONFIG_FILE="/data/MyStudy/config/tans.yaml"
TANS_PYTHON="/opt/SimNIBS-4.5/simnibs_env/bin/python"
TANS_TARGET_NETWORK="CinguloOpercular"
TANS_OUTPUT_DIRNAME="tans"
STOP_AFTER_MODULE="tans"
```

`TANS_CODE_ROOT` points to **`pipeline/`**, not the repository root or `search/`.
`TANS_OUTPUT_DIRNAME` is one directory name, not an absolute path.
`TANS_OUTPUT_DIR` is no longer supported. PFM inputs default to
`<SubjectDir>/func/<FUNC_DIRNAME>/PFM/RidgePFM/`; `TANS_PFM_DIR` overrides that
directory. If `RidgePFM` is absent, an existing `RidgeFusion` directory is a
compatibility fallback. The YAML determines filenames inside it; update those
paths when using legacy maps. Neither route renames saved outputs.

## Full processing chain

Keep the study's normal upstream `START_FROM_MODULE`, enable PFM to produce the
required maps, set `STOP_AFTER_MODULE="tans"` and `TANS_ENABLE=1`, and launch:

```bash
bash /opt/pfm-mefmri/bin/mefmri_pipeline.sh \
  /data/MyStudy/SUB001 /data/MyStudy/config/mefmri.sh
```

TANS comes after PFM and an enabled PFM manual-update stage. Enabling TANS does
not repair missing upstream inputs: review the [file contract](INPUTS_AND_OUTPUTS.md).

## Existing processed subject: run only TANS

In the same complete study config:

```bash
START_FROM_MODULE="tans"
STOP_AFTER_MODULE="tans"
TANS_START_FROM="headmodel"
TANS_STOP_AFTER="reports"
TANS_RESUME=1
```

Use the same launch command. The stage controls have different scopes:

| Setting | Controls |
| --- | --- |
| `START_FROM_MODULE`, `STOP_AFTER_MODULE` | pfm-mefmri's processing chain |
| `TANS_START_FROM`, `TANS_STOP_AFTER` | Internal TANS stages |
| `TANS_RESUME` | Whether the TANS output root may already exist |

TANS builds its own native T1w/T2w CHARM model; it does not automatically reuse
pfm-mefmri's separate `anat_charm` outputs. Completed **TANS** head models can be
reused with the same run/configuration. Resume is not a configuration migration:
use a new output folder when changing scientific inputs.

## Finish and review

Open `<SubjectDir>/<TANS_OUTPUT_DIRNAME>/reports/index.html`, perform the
[review sequence](WORKFLOW.md), and distribute the complete
`shared/export_<subject>/` package only after review. Copied packages are not
updated by later code changes or report regeneration.

The wrapper prints its exact `run_pipeline.py` command. Compare it with
`config/resolved.yaml` and the manifests when troubleshooting; the YAML alone
is not necessarily authoritative because the wrapper forwards CLI overrides.

## Compatibility boundary

These instructions describe the companion wrapper exposing the `TANS_*`
interface in [the configuration reference](CONFIGURATION.md). Verify that your
pfm-mefmri revision includes it. This repository neither installs that wrapper
nor modifies the pfm-mefmri checkout. If its published release has not caught up,
use a compatible reviewed checkout or the [standalone entry point](STANDALONE.md).
