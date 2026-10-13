# Changelog

## 2026.10.13 — Python release preparation

- Shortened the README around individual-network targeting, conceptual visuals,
  and electric-field dose adjustment; documented changes since the 2022 method.
- Updated default PFM inputs to RidgePFM with an existing-directory fallback for
  RidgeFusion; explicit paths and study YAML filenames remain authoritative.
- Replaced site-specific deployment and hardware examples with generic templates,
  setup instructions, and optional shell overrides aligned with pfm-mefmri.
- Kept the recent input-staging and Python-version fixes in this release tree.
- External participant-reference tests now require an explicit environment setting.

This entry describes the prepared source; it does not assert a GitHub tag or
publication. No new subject simulation or performance benchmark was run.

## Earlier uncommitted runtime fixes

- Keep the network-input staging directory owned by its cleanup context while
  atomically publishing its contents, preventing a missing-directory error after
  successful manifest creation. Valid existing manifests remain reusable.
- Check the documented Python 3.10+ requirement before loading workflow inputs,
  with guidance to select a compatible `TANS_PYTHON` interpreter.

## 2026.09.21 — frozen local checkpoint

- Preserved the current working Python workflow and reviewed gallery in a dated,
  checksummed source snapshot for publication review.
- Added explicit field-scaling explanation and generic optimization heading.
- Paired with the local pfm-mefmri release draft's TANS integration and routing
  checks. Scientific/runtime settings in pfm-tans are unchanged by this checkpoint.
- No Git commit, tag, release, or push performed.

See [checkpoint notes](docs/RELEASE_2026_09_21.md).

## Current Python refactor — local release preparation

- Replaced the earlier MATLAB runtime with Python orchestration, native-image
  subject adaptation, individualized target preparation, and coarse/fine search.
- Added optional placement-error robustness and explicit nominal-only selection.
- Consolidated active code under `pipeline/` and `search/`, with packaged priors,
  search-space resources, and stimulator calibration tables under `res0urces/`.
- Added prescribed-pose identity checks, exterior-skin provenance validation,
  matched motor/treatment calibration, and portable subject-named exports.
- Added offline dose/hotspot exploration, continuous calibration interpolation,
  measured-rMT locking, and separately confirmed treatment-intensity records.
- Documented pfm-mefmri as the recommended integration and the same driver's
  standalone route, including exact input requirements and study configuration.
- Featured the selected SAL grant figures and a matching SAL search movie in the
  local README gallery, with PDFs, animated preview, and explicit provenance.
  Runtime settings are unchanged.
- Clarified the linked targeting-and-dose rationale, labeled the example rMT as
  hypothetical, and added a vector-generated peg-and-hole conceptual animation.
- Updated the SAL dosing illustration to the network-colored off-target version
  and added a matching technician-facing workpage walkthrough. Removed the
  separate DMN gallery example; clarified that neither SAL nor DLPFC restricts
  the configurable target network/pattern or cortical search zone.
- Added WTA-only 0/1 weight synthesis and weighted-only percentile-derived binary
  support, with polarity/normalization settings, custom map names, source hashes,
  report provenance, and checks against reusing changed inputs on resume.

This is not a claim that a GitHub release has already been published. See the
[migration guide](docs/MIGRATION.md) and [publication checklist](docs/RELEASE_CHECKLIST.md).
