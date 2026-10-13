# Local publication review

This checklist separates preparing a repository from authorizing publication.
No commit, push, tag, release, or pfm-mefmri modification is implied by editing
these files. Obtain maintainer approval for those actions.

## Source and configuration

- Review the entire local diff, including intentional removal of the earlier
  MATLAB implementation and all new/untracked `pipeline/` and `search/` files.
  A README-only commit would not publish the implementation it documents.
- Preserve the original paper citation and review attribution for adapted code,
  bundled priors, search masks, and calibration resources.
- Decide the repository's distribution license and resource permissions. No new
  top-level license is invented by this documentation update; maintainer review
  is required before describing the release as licensed for unrestricted reuse.
- Confirm generic templates contain installation placeholders and explicit hardware
  selections, with no personal paths. Keep configured study files outside the repository.
- Confirm `res0urces/Priors.mat`, search envelopes, and canonical calibration
  CSV/JSON files are included despite general data ignore rules.
- Exclude participant images, head meshes, raw network maps, clinical records,
  local logs, credentials, archives, and real operator export packages.

## Documentation and visuals

- Run `python -B tools/check_documentation.py` for local links, config examples,
  shell syntax, required resources, and curated gallery assets.
- Inspect the README rendering, alt text, image readability, and relative links.
- Review gallery permissions before public distribution. Removing identifiers
  from a cortical rendering is not a blanket guarantee of anonymization.
- Review the selected SAL figures/movie for sharing permission. Preserve the
  optimization figure's schematic-history and adjacent-candidate disclosures,
  and the dosing figure's hypothetical-rMT disclosure.
- Keep source-data provenance in restricted local records; publish only the
  explicitly selected documentation assets and their nonidentifying captions.
- Label simulated/session demonstration inputs as examples, never as observed
  rMT or actual delivered treatment. Do not publish generated session JSON that
  contains source paths or participant-specific technical provenance.
- Distinguish automatic production outputs from separately rendered figure-review
  layouts. Do not promise that the latter appear at every run's end.

## Software and integration

- Run the `pipeline`, `search`, and documentation unit suites in the configured environment:

  ```bash
  python -B -m unittest discover -s pipeline -p 'test_*.py'
  python -B -m unittest discover -s search -p 'test_*.py'
  python -B -m unittest discover -s tools -p 'test_*.py'
  ```

  The optional saved-dose reference test is skipped unless `TANS_TEST_DOSE_LOOKUP`
  points to an explicitly selected external fixture. No participant data is bundled.
- Run the relevant browser checks against a copied report using test-only inputs.
- Verify a representative completed subject and portable export, including a
  relocated package. Unit tests alone are not a full integration run.
- Verify the pfm-mefmri companion version has the documented wrapper/CLI
  contract before advertising that version pair. Any needed upstream changes
  require their own approval; do not alter that repository as part of this review.
- Record tested interpreter/external-tool versions and the exact revisions.
- Review known limitations: fixed anatomy layout, derived-map assumptions and
  explicit weighted-only percentile settings,
  explicit calibration matching, one treatment network per top-level invocation,
  local download handling, and no claim of clinical clearance.

Only after approval: choose the intended commit scope, inspect it for data and
secrets, create the agreed commit/release, and push to the confirmed remote.
