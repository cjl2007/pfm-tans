# Optional Placement-Error Robustness

The Python workflow supports two explicit prescription modes. The coarse-grid
coverage, component filtering, orientation sweep, fine search, target score, and
peak-proximity eligibility checks are unchanged between them.

## Configuration

In the ME-fMRI wrapper config:

```bash
TANS_ROBUSTNESS_ENABLE=1  # default: run perturbations and sensitivity analysis
TANS_ROBUSTNESS_ENABLE=0  # select the nominal fine-search winner instead
```

For direct Python use, set `robustness.enabled: false` in YAML, or pass
`--robustness-enabled 0` to `run_pipeline.py`. Missing settings retain the prior
enabled behavior. YAML values must be booleans; wrapper values must be 0 or 1.
An existing search plan cannot silently change modes during resume.

## Disabled Mode

- No robustness planning, perturbation FEM simulations, or sensitivity analysis.
- The prescription is the existing fine-stage `BestCoarsePose.json` (the legacy
  filename for the winner of that simulation batch). No winner is re-ranked.
- Reports, dosing reference fields, and Brainsight exports resolve that exact
  fine-stage pose and benchmark. No fake robustness results are generated.
- Runtime reports record zero added simulations/time and `skipped_by_config`;
  QC describes placement-error robustness as not evaluated, never as passed.
- Native-space geometry checks, the magnitude-equivalent handle preference,
  export round-trip validation, and the configured dose ceiling remain active.
- The tradeoff is that performance under placement error has not been tested.
  Disabling this stage is not evidence of tolerance to placement error.

## Study choice and reproducibility

The general defaults and annotated study example keep robustness enabled.
Individual studies may explicitly choose nominal-only selection. Record that
decision in both the YAML and wrapper overrides when using pfm-mefmri, and use
a distinct output root for comparisons rather than changing an existing plan.

Verification includes enabled/disabled routing, strict settings, zero-overhead
accounting, report/export/dose source agreement, and regression tests for the
upward-only dosing ceiling. Software completion still requires manual QC and
trajectory import review; it is not clinical clearance.
