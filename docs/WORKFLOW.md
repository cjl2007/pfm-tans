# Workflow

## Citation

If you use this workflow in scientific work, cite the original TANS method paper:

Lynch CJ, Elbau IG, Ng TH, Wolk D, Zhu S, Ayaz A, Bukhari H, Power JD, Liston C. Automated optimization of TMS coil placement for personalized functional network engagement. *Neuron*. 2022;110(20):3263-3277.e4. https://doi.org/10.1016/j.neuron.2022.08.012

`pfm-tans` is a modernization of the software packaging, configuration, documentation, and practical workflow support around that original method.

## Main stages

1. Preflight validation
2. Head model generation or reuse
3. Native-surface preparation
4. Target-network thresholding
5. Candidate discovery and ranking
6. Per-candidate search-grid construction
7. Per-candidate SimNIBS E-field modeling
8. Per-candidate coil optimization
9. Per-candidate tolerability estimation
10. Run-level candidate summary

## Candidate discovery

The ROI stage applies:

- search-space masking
- sulcal masking
- medial-wall exclusion
- cluster finding
- ranking by cluster surface area

The new parameter is:

```matlab
cfg.target.maxCandidateTargets
```

This keeps the top `K` ranked clusters, up to the number of viable clusters available.

## Per-candidate execution

For each retained candidate, PFM-TANS runs:

- `ROI/`
- `SearchGrid/`
- `Optimize/`
- `Tolerability/`

under:

```text
<Subdir>/tans/<TargetName>/Candidate1/
<Subdir>/tans/<TargetName>/Candidate2/
...
```

## Tolerability metric

The active practical tolerability metric is based on the external TMS-SMART dataset rather than an eye-distance proxy.

Implementation details:

- source ratings are aggregated from trial-level SMART data by `RefLocation`
- homologous study labels are mapped onto subject-native EEG positions exported with the SimNIBS head model
- tolerability values are interpolated across scalp vertices with inverse-distance weighting by default
- final candidate scores are sampled at the optimized coil-center scalp vertex
- output location: `Candidate*/Tolerability/TolerabilitySample.txt`

Coordinate assumptions:

- subject-native EEG positions are read from `m2m_<Subject>/eeg_positions/EEG10-20_extended_SPM12.csv`
- interpolation is performed directly on the native subject scalp mesh used downstream by search-grid and optimization
- the active tolerability implementation does not use MNI-space transforms

Current study-specific assumption:

- SMART trial-level data are aggregated by `RefLocation`
- pooled homologous labels are expanded onto one or more subject EEG labels
- the current native-EEG mapping uses `Inion -> Iz`, `TPJ -> TP7/TP8`, `LatIn -> I1/I2`, `LatOc -> PO9/PO10`, `V5 -> PO7/PO8`, and `ATL -> FT9/FT10`

## Failure handling

If a candidate fails in a downstream stage, PFM-TANS records the error and continues to the next candidate. The final status appears in:

- `CandidateSummary.tsv`
- `CandidateSummary.mat`
