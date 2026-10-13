# Portable operator export

The distributable unit is the entire `<SubjectDir>/tans/shared/export_<subject>/` folder.
The subject comes from the run manifest; for SUB001 this is
`shared/export_SUB001/`, including when the run is in a nested `runs/` directory.
Older `shared/export/` packages remain readable until republished; publishing
archives the old package and writes the subject-named folder.
It opens offline via `index.html` without the source TANS directory, videos,
head meshes, simulation arrays, or external web resources. Do not distribute
an individual target subfolder without the shared MRI at the export root.

```
tans/
  shared/
    export_SUBJECT/
      index.html
      SUBJECT_T1w.nii.gz
      README.txt
      ExportManifest.json
      SM-Hand/
        index.html
        SUBJECT_SM-Hand_Prescribed_OptimalTrajectory_Brainsight.txt
        SUBJECT_SM-Hand_Prescribed_OptimalPoseOverview.png
        metadata/
      Salience/                  # or CinguloOpercular, or additional targets
        index.html
        SUBJECT_Salience_Prescribed_OptimalTrajectory_Brainsight.txt
        SUBJECT_Salience_Prescribed_OptimalPoseOverview.png
        metadata/
        dose-calibration/
          index.html
          DoseCalibrationReport.html
          DoseLookup.json
          DoseLookup.tsv
    work/publishing/dose-calibration/
  targets/TARGET/work/publishing/neuronavigation/
  targets/TARGET/reports/         # full scientific figures and videos
  reports/index.html             # research/QC report, links to operator export
```

## One prescribed pose per target

The technician package contains exactly one trajectory and one pose preview for
each target, including SM-Hand. There are no `fine/` or `robust/` choice folders
and no motor-reference or treatment-variant selectors in the default dose page.
The relevant target's resolved `robustness.enabled` setting determines its pose:

- Enabled: export the validated robust prescription. Missing or unvalidated
  robust output is an error; never silently substitute the fine winner.
- Disabled: export the validated nominal fine winner. Do not choose a stale
  robust result just because its files exist.

This rule is applied independently to SM-Hand and every treatment target. For
example, a robust SM-Hand reference can be paired with a fine treatment pose if
their per-target settings differ. The chosen variant and source pose identity
remain explicit in `ExportManifest.json`, each target's metadata, and the dose
lookup; the word `Prescribed` in the filename does not erase provenance.

Each treatment target has one dose report matched to the prescribed SM-Hand
reference and that treatment prescription. Its `DoseLookup.json` and
`DoseLookup.tsv` retain the original calibration, policy, reference fields,
dose-response rows, and scored prescriptions unchanged. The treatment identity
is fixed to that folder's target. The operator enters the measured rMT at the
prescribed SM-Hand pose; the page does not ask the operator to choose an
alternative pose. Without SM-Hand there are pose-only exports and no fabricated
dose calibration. If the actual rMT pose differs from the planned reference,
stop and obtain a reviewed matching calibration rather than reuse the lookup.

Scientific fine/robust comparisons and all available pose-matched dose lookups
remain in the research outputs and private `work/publishing` caches. They are
not copied into the default technician package. A deliberate alternative
prescription requires a separate reviewed package, not an ad hoc technician
switch between research files. See `pose_variant_outputs.md`.

The shared T1w is copied once; all target exports must have identical MRI hashes.
Trajectories and pose previews are copied byte-for-byte from the chosen validated variant
artifacts. No FEM simulation or pose optimization is rerun. Legacy dose-interface
upgrades may recheck recorded doses against their saved fields without changing
the selection rules or recorded prescriptions.
Matrix files and JSON provenance are separated into `metadata` to avoid confusing
the Brainsight trajectory with the internal SimNIBS matrix text file.

All operator links are relative and stay inside `shared/export_<subject>`. No symlinks are
used. The integrity validator checks file hashes, relative-link closure, embedded
HTML lookup equality, subject/target/variant identity, and dose ceilings. An
additional copy to an unrelated temporary directory is validated before publishing.
Original absolute source paths may remain in technical provenance; the operator
pages do not depend on those paths.

`publish_operator_export.py RUN_ROOT` builds and validates a staging package,
publishes it, moves old presentation inputs to internal `work/publishing` storage,
and updates the research report links. Existing source files are retained, not
deleted. Legacy export and dosing locations become small HTML pointers. The root
operator package has no duplicate trajectory or alternate-variant menu. Replaced export packages
are retained under `shared/work/publishing/previous-exports` rather than destroyed.

`publish_operator_export.py EXPORT_ROOT --validate-only` validates a copied package
without requiring the subject folder. Full processing uses this publisher at the
end of report generation. `validate_completed_run.py` includes its checks.
Updating this publishing policy does not automatically republish packages
already distributed to operators; replacement packages need explicit review.

Software checks are not clinical clearance. Import geometry, actual coils,
registration, actual rMT trajectory, and protocol-specific dose approval still
require qualified review. Packaging does not change the configured rMT-relative
increase safeguard; an empty policy still means no software upward ceiling.

## Session records are separate

The dose HTML now supports [locking observed rMT and recording treatment MSO](measured_dose_records.md).
PDF/PNG/JSON downloads are session artifacts, not changes to the prescribed
trajectory package. Store them outside the export, for example under
`shared/session-records/<subject>/<target>/<session>/`. Adding files to the
hash-validated package invalidates its integrity check. The browser chooses the
download destination and does not silently write into the pipeline output tree.
