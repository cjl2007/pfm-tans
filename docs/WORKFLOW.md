# Workflow and review

The recommended pfm-mefmri module and standalone CLI both invoke
`pipeline/run_pipeline.py`. TANS consumes completed anatomical/PFM derivatives;
it does not estimate functional networks itself.

| Stage | What happens | What to review |
| --- | --- | --- |
| `headmodel` | Native T1w/T2w CHARM model, native-space fs_LR surfaces, validated exterior scalp | Segmentation, tissue boundaries, image/surface alignment, coordinate history |
| `targets` | Categorical/probabilistic maps, anatomical eligibility, components | Correct network/hemisphere, anatomical mask, retained target support |
| `plan` | Per-target configuration and target-coverage scalp grids | Adequate coverage and sensible candidate centers |
| `search` | Coarse orientation/position search, local fine refinement, optional placement-error sampling | Objective/peak-gate results, winner, sampled robustness if enabled |
| `reports` | Pose and target QC, replay, dose lookups, trajectory package, master index | Agreement of simulations, reports, prescription, and hardware |

All stages use Python orchestration plus their configured external tools. The
wrapper exposes `TANS_START_FROM`, `TANS_STOP_AFTER`, and `TANS_RESUME`; standalone
uses the corresponding CLI flags. Later stages require earlier artifacts.
`--stop-after plan` still performs real CHARM/preparation work, not a dry run.

## Selection and prescription

Coarse/fine search evaluates modeled network engagement across hotspot
percentiles, not an observed physiological dose-response. Weighted and binary
objectives differ. The retained target components used for the objective and
the eligibility/peak checks are recorded in the run artifacts.

With robustness enabled, the prescription comes from the validated robustness
selection. With it disabled, the nominal fine winner is used and robustness is
reported as not evaluated. Missing required robust results never justify an
automatic fallback. Physical handle canonicalization is a separate, explicitly
configured magnitude-equivalence choice; it does not change the winning field.

## Operator handoff

1. Review `reports/index.html` and the source anatomical/search/pose figures.
2. Run the software validation described in [standalone execution](STANDALONE.md)
   or review its recorded receipts. Resolve failures before handoff.
3. Copy the complete `shared/export_<subject>/` package, including its shared MRI,
   target trajectories, dose pages, and manifest. Validate the copied package.
4. Independently check the imported trajectory and coordinate interpretation in
   the navigation system, the actual hardware, and registration quality.
5. At the session, establish rMT at the matching exported SM-Hand reference pose
   using the specified motor coil. The lookup is not automatically valid for a
   different measured motor-reference pose.
6. Lock the observed rMT in the offline dose page. Keep modeled selection distinct
   from the treatment intensity actually used; confirm that separately before
   generating a recorded-dose figure.
7. Save the PDF and JSON outside the immutable operator package in the study's
   session records. The browser does not write back to a clinical system.

## Interpretive boundaries

Head-model fidelity, cortical registration, coil geometry, reference calibration,
and actual navigation error all affect interpretation. Sampled placement-error
robustness is not a measurement of registration accuracy. A first modeled
threshold crossing is not an established minimum effective dose. Passing a
configured ceiling is not proof of safety or efficacy.

The initial HTML rMT is an unconfirmed example. Actual-rMT locking and a
technician-confirmed intensity produce a **reported record**, not verified device
delivery or measured cortical activation. The study's approved protocol and
qualified review remain authoritative.

See [search settings](two_stage_search_configuration.md),
[robustness](robustness_optional.md), [dose records](measured_dose_records.md),
and [operator packaging](operator_export_package.md).
