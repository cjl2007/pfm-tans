# Inputs And Outputs

## Required inputs

- Subject anatomical directory with `anat/T1w`
- Subject surfaces under `anat/T1w/fsaverage_LR32k`
- Subject fsLR-derived masks under `anat/MNINonLinear/fsaverage_LR32k`
- Functional probability maps or maps referenced by config
- Search-space CIFTI file
- SimNIBS coil model

## Important config inputs

- `cfg.inputs.probMapsFile`
- `cfg.paths.searchSpace`
- `cfg.target.networkColumn`
- `cfg.target.offTargetColumn`
- `cfg.target.maxCandidateTargets`
- `cfg.tolerability.dataFile`
- `cfg.tolerability.eegPositionsFile`

## Main outputs

Target-level outputs:

- `<Subdir>/tans/<TargetName>/ROI/`
- `<Subdir>/tans/<TargetName>/CandidateSummary.tsv`
- `<Subdir>/tans/<TargetName>/CandidateSummary.mat`
- `<Subdir>/tans/<TargetName>/ResolvedConfig.mat`
- `<Subdir>/tans/<TargetName>/ResolvedConfig.txt`

Per-candidate outputs:

- `Candidate*/ROI/`
- `Candidate*/SearchGrid/`
- `Candidate*/Optimize/`
- `Candidate*/Tolerability/`

## Candidate summary fields

- candidate rank
- cluster size in mm^2
- status
- best center on-target score
- best center penalty score
- best center penalized score
- best orientation on-target score
- best orientation penalty score
- best orientation penalized score
- tolerability metric columns from the external dataset
- tolerability rank
- output directory
- error message if failed

## Tolerability outputs

`Tolerability/TolerabilitySample.txt` includes:

- sampled scalp vertex
- sampled native-space and evaluation-space coordinates
- nearest source distance and valid-domain status
- predicted tolerability metrics

Run-level model outputs live under:

- `<Subdir>/tans/<TargetName>/Tolerability/`

These include:

- the dense interpolated scalp maps
- the valid-domain mask
- model metadata
- the aggregated source data used to construct the map
- the subject-native EEG-derived source coordinate table
