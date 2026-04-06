# PFM-TANS

PFM-TANS is a MATLAB workflow for personalized TMS targeting and E-field modeling from subject-specific anatomy and functional network maps. This repository packages the original TANS method into a more configuration-driven, documented, and user-facing form, with clearer entrypoints, reproducible outputs, and support for multi-candidate targeting.

## Quick Start

1. Install the external dependencies described in [SOFTWARE_DEPENDENCIES.txt](SOFTWARE_DEPENDENCIES.txt) and [docs/INSTALL.md](docs/INSTALL.md).
2. Copy or edit a config from `config/`.
3. Add the repo to the MATLAB path.
4. Validate the subject/config before running.
5. Run the main workflow.

```matlab
addpath(genpath('/path/to/pfm-tans'));
report = tans_validate('/path/to/subject', 'tans_config_bd2_salience');
outputs = tans_run('/path/to/subject', 'tans_config_bd2_salience');
```

## Config Files

All user-editable workflow settings are intended to live in `config/`.

Main files:
- [config/tans_config_template_blank.m](config/tans_config_template_blank.m): blank starting point for a new targeting config
- [config/tans_config_multicandidate_example.m](config/tans_config_multicandidate_example.m): example showing backup candidate targets
- [config/tans_workflow_defaults.m](config/tans_workflow_defaults.m): shared default settings used by targeting configs
- [config/tans_dose_config_template_blank.m](config/tans_dose_config_template_blank.m): blank starting point for dose workflow configs

Typical fields you will edit first:
- `cfg.paths.resourcesRoot`
- `cfg.paths.mscRoot`
- `cfg.paths.simnibsRoot`
- `cfg.paths.searchSpace`
- `cfg.inputs.probMapsFile`
- `cfg.tolerability.dataFile`
- `cfg.tolerability.eegPositionsFile`
- `cfg.target.name`
- `cfg.target.networkColumn`
- `cfg.target.offTargetColumn`
- `cfg.target.maxCandidateTargets`

To enable backup targets:

```matlab
cfg.target.maxCandidateTargets = 3;
```

## Main Workflows

User-facing entrypoints live in `bin/`:
- [bin/tans_validate.m](bin/tans_validate.m): preflight validation for targeting inputs and dependencies
- [bin/tans_run.m](bin/tans_run.m): main targeting workflow
- [bin/tans_run_dose.m](bin/tans_run_dose.m): dose workflow

Compatibility wrappers remain at the repo root for `tans_main_workflow`, `tans_module`, and `tans_dose_workflow`.

Example targeting call:

```matlab
addpath(genpath('/path/to/pfm-tans'));
outputs = tans_run('/path/to/subject', 'my_subject_config');
```

Example dose call:

```matlab
addpath(genpath('/path/to/pfm-tans'));
doseOutputs = tans_run_dose('/path/to/subject', 'my_dose_config');
```

## Output Structure

The targeting workflow writes results under:

```text
<Subdir>/tans/<TargetName>/
  ROI/
  CandidateSummary.tsv
  Candidate1/
    ROI/
    SearchGrid/
    Optimize/
    Tolerability/
  Candidate2/
    ROI/
    SearchGrid/
    Optimize/
    Tolerability/
```

When `cfg.target.maxCandidateTargets = 1`, the workflow behaves like the original single-target mode aside from the candidate directory level. When it is greater than `1`, candidate-specific downstream results are written separately and summarized at the target level.

## Documentation

- [docs/INSTALL.md](docs/INSTALL.md)
- [docs/WORKFLOW.md](docs/WORKFLOW.md)
- [docs/INPUTS_AND_OUTPUTS.md](docs/INPUTS_AND_OUTPUTS.md)
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- [SOFTWARE_DEPENDENCIES.txt](SOFTWARE_DEPENDENCIES.txt)

## Citation

If you use `pfm-tans`, please cite the original TANS method paper:

Lynch CJ, Elbau IG, Ng TH, Wolk D, Zhu S, Ayaz A, Bukhari H, Power JD, Liston C. Automated optimization of TMS coil placement for personalized functional network engagement. *Neuron*. 2022;110(20):3263-3277.e4. https://doi.org/10.1016/j.neuron.2022.08.012

This repository is a maintained, usability-focused reorganization and extension of that original approach. The underlying scientific targeting framework originates from the Neuron paper above.
