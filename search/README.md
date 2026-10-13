# TANS search, simulation, and reports

Active scientific code used by `../pipeline/run_pipeline.py`.
The current orchestrators are `../pipeline/run_search.py` and
`../pipeline/generate_reports.py`.

- `extract_charm_scalp.py`: exterior-skin extraction and validation.
- `generate_target_projection_coverage_plan.py`: coarse search grids.
- `run_simulations.py` and `benchmark_pose_batch.py`: simulation and scoring.
- `generate_fine_plan.py`, `generate_robustness_plan.py`, and
  `evaluate_robust_placements.py`: refinement and placement sensitivity.
- `selected_pose.py`, `simulated_pose_geometry.py`, and `pose_prescription.py`:
  selected pose resolution and physical prescription.
- `render_*`, `dose_*`, `generate_calibration_dose_report.py`, and
  `export_neuronavigation_package.py`: reports, dosing, and exports.
- `refresh_dose_visuals.py`: maintenance of existing dose reports.

The two `default_*.json` files contain shared search and visualization defaults.
See [search configuration](../docs/two_stage_search_configuration.md) and
[operator exports](../docs/operator_export_package.md).

Tests live alongside these modules. The Python percentile compatibility helper
is part of current scoring and requires no MATLAB.
