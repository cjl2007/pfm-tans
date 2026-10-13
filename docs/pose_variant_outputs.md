# Fine and robust pose outputs

Research report generation retains both saved alternatives when robustness is enabled.
It does not change scoring, winner selection, simulation parameters, or the
pipeline-selected default. No additional FEM simulations are required.

For each target:

- `reports/optimal-pose/fine/`: nominal fine winner and threshold sweep.
- `reports/optimal-pose/robust/`: robust prescription and threshold sweep.
- `reports/optimal-pose/index.html`: research scores, sampled sensitivity, both
  views, and a link to the single prescribed technician export. Links to the
  alternative trajectories remain explicitly labeled research artifacts.
- `targets/TARGET/work/publishing/neuronavigation/fine/` and `robust/`:
  validated source trajectories, pose previews, and technical metadata. These
  source artifacts are not a technician choice menu.
- `shared/export_SUBJECT/TARGET/`: one `SUBJECT_TARGET_Prescribed_OptimalTrajectory_Brainsight.txt`
  and one `SUBJECT_TARGET_Prescribed_OptimalPoseOverview.png`, with provenance
  in `metadata/`. Paths beginning with `targets/` or `shared/` are relative to
  the run root, not the individual target's processing folder.
- `shared/export_SUBJECT/SUBJECT_T1w.nii.gz`: one shared anatomical reference for all
  exported trajectories. Distribute the entire `shared/export_SUBJECT` folder.

The default technician prescription follows each target's own resolved
`robustness.enabled` setting: true selects its validated robust pose, false its
validated fine winner. Missing robustness output in an enabled run is an error,
never a silent fallback. Stale robust files are ignored when robustness is
disabled. There are no `fine/` or `robust/` choice folders in the technician
export. Scientific report entry points still identify the pipeline-selected
pose. Legacy export locations point to the portable single-prescription package.

## Pose-matched dosing

`shared/export_SUBJECT/TARGET/dose-calibration/index.html` opens one lookup matched to
the prescribed SM-Hand reference and the target's prescribed treatment pose.
`DoseCalibrationReport.html`, `DoseLookup.json`, and `DoseLookup.tsv` are in
the same directory. There is no default pose selector. SM-Hand and treatment
settings can differ, so the chosen pairing may legitimately mix variants.

All available research pairings are retained privately, organized by treatment
variant, then motor-reference variant, under the run root:

```
shared/work/publishing/dose-calibration/fine/sm-hand-fine/
shared/work/publishing/dose-calibration/fine/sm-hand-robust/
shared/work/publishing/dose-calibration/robust/sm-hand-fine/
shared/work/publishing/dose-calibration/robust/sm-hand-robust/
```

Only available combinations are generated. These source lookups may contain
multiple treatment networks; the technician copy is restricted to its target.
The reference trajectory must match the actual rMT measurement. A deliberate
change of motor or treatment prescription requires a separately reviewed
matching lookup and export, not an operator-side switch. Source field/map, coil, pose, and calibration identities
are recorded for every combination. The configured upward rMT-relative MSO
ceiling remains unchanged, including explicit disabling of that ceiling.

The old `reports/dose-calibration` contains only a navigation pointer. Reproducible
cross-target source lookups are retained internally under
`shared/work/publishing/dose-calibration`. See `operator_export_package.md` for
the packaging contract, migration, and portability validation.

`reports/PoseVariants.json` records comparisons, artifact paths, reporting time,
and validation. Validation matches exports and renderings to exact saved pose
matrices, checks Brainsight round trips and file hashes, verifies dose reference
fields/map indices, and checks the configured dose ceiling in every lookup.
Those numerical round trips are software checks, not verification of an actual
import in Brainsight. Existing distributed packages are not automatically
replaced by this policy change.

Sensitivity scores describe sampled positioning/orientation errors around a
candidate under the configured model. They neither measure registration quality
nor justify accepting poor registration. Software validation is not clinical
clearance. Nominal changes are reported in percentage points separately from
relative percentages. Lower-tail benefits need not extend beyond the tested
error range.
