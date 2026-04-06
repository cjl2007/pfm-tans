# PFM-TANS

PFM-TANS is a MATLAB-based workflow for personalized TMS targeting and E-field modeling from subject-specific anatomy and functional network maps. The scientific targeting logic from the original repository is preserved, but the repo is now organized as a configuration-driven package with clearer entrypoints, docs, provenance logging, support for multiple ranked candidate targets, and empirical tolerability-map scoring based on external TMS discomfort data.

## Quick Start

1. Install the external dependencies described in `SOFTWARE_DEPENDENCIES.txt` and [docs/INSTALL.md](docs/INSTALL.md).
2. Edit or copy an example config from [config/tans_config_template_blank.m](config/tans_config_template_blank.m).
3. Validate inputs:

```matlab
addpath(genpath('/path/to/pfm-tans'));
report = tans_validate('/path/to/subject', 'tans_config_bd2_salience');
```

4. Run the targeting workflow:

```matlab
outputs = tans_run('/path/to/subject', 'tans_config_bd2_salience');
```

5. Enable multi-candidate mode by increasing:

```matlab
cfg.target.maxCandidateTargets = 3;
```

## Repository Layout

- `bin/`: user-facing launchers
- `config/`: example and template configs plus machine/workflow/target defaults
- `modules/`: workflow stages such as ROI generation, search grid creation, simulation, and optimization
- `lib/`: shared helpers for config loading, provenance, preflight checks, and tolerability metrics
- `docs/`: installation and workflow documentation
- `examples/`: example usage scripts
- `release/pfm-tans/`: self-contained release tree intended for GitHub publication
- `tests/`: lightweight smoke tests
- `res0urces/`: bundled reference assets used by PFM-TANS

Refresh the release tree with:

```bash
bash bin/build_release_tree.sh
```

## Main Entry Points

- [bin/tans_run.m](bin/tans_run.m): main targeting workflow
- [bin/tans_validate.m](bin/tans_validate.m): preflight validation
- [bin/tans_run_dose.m](bin/tans_run_dose.m): dose workflow

Compatibility wrappers remain at the repo root for `tans_main_workflow`, `tans_module`, and `tans_dose_workflow`.

## Multi-Candidate Targeting

The workflow no longer discards all but the single largest viable target cluster. Instead it ranks candidate clusters using the existing cluster-size logic and keeps up to `cfg.target.maxCandidateTargets`.

When `cfg.target.maxCandidateTargets = 1`, behavior stays aligned with the original single-target workflow except for the new candidate subdirectory level.

Candidate outputs are organized as:

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

`Tolerability/` contains tolerability-map outputs derived from the external TMS-SMART dataset. Trial-level pain, twitch, and visible-twitch ratings are aggregated by SMART `RefLocation`, mapped onto subject-native EEG positions from the SimNIBS head model, interpolated across the subject scalp, and sampled at the final optimized coil-center vertex.

Default study resources:
- [res0urces/meteyard-l_holmes-2018_TMS-SMART_data.txt](res0urces/meteyard-l_holmes-2018_TMS-SMART_data.txt)
- subject EEG positions such as `<Subdir>/tans/HeadModel/m2m_<Subject>/eeg_positions/EEG10-20_extended_SPM12.csv`

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

## Example Data

Example data from six healthy participants is on Box: `https://wcm.box.com/v/TANS-ExampleData`

## Notes

- The repo is not intended to change the underlying scientific scoring logic silently.
- Candidate ranking still uses the current cluster-size ordering from ROI discovery.
- If a candidate fails downstream, the run continues for the remaining candidates and records the failure in `CandidateSummary.tsv`.
