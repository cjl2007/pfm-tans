# Changelog

## 2026-04-06

- Reorganized the repo into `bin/`, `config/`, `modules/`, `lib/`, `docs/`, `examples/`, and `tests/`.
- Added user-facing launchers: `tans_run`, `tans_validate`, and `tans_run_dose`.
- Centralized config loading and runtime provenance helpers.
- Added candidate-aware ROI handling with `cfg.target.maxCandidateTargets`.
- Added per-candidate output layout under the existing target directory.
- Replaced the eyeball-distance proxy with TMS-SMART-based tolerability-map scoring.
- Added run-level candidate summary output and failure-tolerant per-candidate execution.
