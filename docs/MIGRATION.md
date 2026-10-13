# Migration to the current Python layout

The current implementation replaces the earlier MATLAB entry points. Do not
combine the previous `tans_run.m` / `tans_module.m` instructions with this driver.
MATLAB configs are not automatically translated into YAML.

| Earlier location or concept | Current location or behavior |
| --- | --- |
| MATLAB entry points and configuration functions | `pipeline/run_pipeline.py` with study YAML and CLI/wrapper settings |
| Earlier Python development-directory paths | `pipeline/` for orchestration, `search/` for scientific implementation |
| Wrapper code-root setting | `TANS_CODE_ROOT=/path/to/pfm-tans/pipeline` |
| Subject-specific scripts | Study-owned YAML, shell overrides, and recorded resolved manifests |
| Legacy `shared/export/` | Subject-named `shared/export_<subject>/` on republishing |
| Operator chooses fine/robust variants | One configured prescription per target; research comparisons stay separate |
| Example rMT treated as a usable dose record | Explicit measured-rMT lock and separate actual-intensity confirmation |

## Existing studies

Keep existing raw inputs, run directories, configurations, and validation receipts
intact. Make a backup of the prior code/configuration before adopting the new
layout. Compare study policy, target definition, calibration identity, units,
and output contracts rather than assuming filenames alone imply equivalence.

The former site deployment template `pipeline/config_template_smhand.yaml` is now
a generic copy of the portable [study example](../examples/study_config.yaml).
Existing studies should keep their own configured YAML; new installations must
fill in software paths, coil models, and calibration identities.

New examples use `RidgePFM` directories and filenames. When no explicit PFM
directory is given, the driver prefers `RidgePFM` and falls back to an existing
`RidgeFusion` folder. Legacy YAML must still name the legacy files; no existing
maps are renamed or recomputed.
Existing pfm-mefmri configs may require code-root/config updates in that separate
repository or study directory; this repository does not make those edits.

Do not mass-rewrite historical manifests to new paths. They are records of the
run that produced the outputs. If a report regeneration requires paths that no
longer exist, preserve the originals, inspect the actual dependencies, and use
a reviewed migration or regenerate from a compatible source tree. A new study
configuration should not resume into a run with different scientific assumptions.

## Existing operator packages

Portable packages are self-contained snapshots. Updating the repository does not
alter copies already distributed. To receive current report controls, a package
must be deliberately rebuilt/reviewed and redistributed. The publisher stages
and validates replacements, archives prior exports, and updates local research
links; it does not rerun FEM merely to repackage a complete current lookup.

Legacy dose lookups may need their saved source fields to build missing embedded
preview data. Missing or incompatible source data is an explicit failure, not a
reason to fabricate a new selection. See [operator exports](operator_export_package.md).
