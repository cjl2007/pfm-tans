# Installation

## Overview

PFM-TANS depends on MATLAB plus several external neuroimaging tools. The workflow shells out to these tools directly, so they must be installed and visible in your environment before you run a subject.

## Required Software

- MATLAB
- SimNIBS 4.x
- Connectome Workbench
- FreeSurfer
- FSL

See [SOFTWARE_DEPENDENCIES.txt](../SOFTWARE_DEPENDENCIES.txt) for the concise checklist.

## Basic Setup

1. Clone the repository.
2. Open MATLAB.
3. Add the repo to the MATLAB path:

```matlab
addpath(genpath('/path/to/pfm-tans'));
```

4. Copy or edit a config under `config/`.
5. Run a preflight check before any expensive computation:

```matlab
report = tans_validate('/path/to/subject', 'tans_config_template_blank');
```

The default PFM-TANS tolerability workflow also expects:

- a tolerability data file such as `meteyard-l_holmes-2018_TMS-SMART_data.txt`
- subject EEG positions such as `tans/HeadModel/m2m_<Subject>/eeg_positions/EEG10-20_extended_SPM12.csv`

## Expected Upstream Data Layout

PFM-TANS assumes the subject directory is already organized in the way the workflow expects. The intended upstream source is [pfm-mefmri](https://github.com/cjl2007/pfm-mefmri), or another pipeline that produces a similar anatomy/functional-output layout.

For new users, [examples/tans_example_use.m](../examples/tans_example_use.m) is the best compact orientation for how the repo is called from MATLAB once the subject data and config are in place.

## Config Editing

At minimum, set:

- `cfg.paths.resourcesRoot`
- `cfg.paths.mscRoot`
- `cfg.paths.simnibsRoot`
- `cfg.paths.tansRoot`
- `cfg.paths.searchSpace`
- `cfg.inputs.probMapsFile`
- `cfg.tolerability.dataFile`
- `cfg.tolerability.eegPositionsFile`
- target-specific fields such as network columns and `cfg.target.maxCandidateTargets`

## Validation Commands

From a shell:

```bash
wb_command -version
charm --version
fslmaths -version
```

From MATLAB:

```matlab
addpath(genpath('/path/to/pfm-tans'));
cfg = tans_config_template_blank(pwd);
disp(cfg.target.maxCandidateTargets);
```

## Common Setup Problems

- `wb_command` missing: Connectome Workbench is not on `PATH`.
- `mesh_load_gmsh4` missing: SimNIBS MATLAB integration or `cfg.paths.simnibsRoot` is wrong.
- BrainSight export fails: the configured Python executable does not have SimNIBS available.
