# Changes since the original TANS method

The [2022 paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC11446252/) introduced
coil-position and orientation optimization against an individual's functional
network boundaries. It used percentile-based E-field hotspots for pose selection
and also described modeling stimulation intensity after selecting the pose.
Its reference implementation used MATLAB.

The current Python implementation keeps that network-based objective and adds
an integrated processing, review, and dose-adjustment workflow. The software
changes below should not be read as results established by the original paper.

## Search efficiency

The current search first covers the eligible target with a coarse grid, then
refines position and orientation around the selected coarse pose. Parallel
workers and resume support reduce repeated work. Optional placement-error
robustness adds simulations and can increase runtime.

This is designed to make searches more efficient; no controlled timing comparison
against the 2022 implementation accompanies this release. Wall time depends on
head-mesh size, target extent, grid density, orientations, workers, and robustness.
See [search configuration](two_stage_search_configuration.md).

## More flexible inputs and targets

Inputs may contain categorical network labels, probability/weight maps, or both.
WTA-only inputs can supply binary stand-in weights. Weighted-only inputs need an
explicit percentile to define binary support. These conversions do not recover
missing uncertainty. Custom FC/activation maps need the documented polarity,
normalization, naming, and anatomical conventions.

Network identity and anatomical search masks are independent settings. The
bundled left-prefrontal recipe is a starting point, not a restriction to SAL or
DLPFC. A treatment-only map does not supply the SM-Hand map needed for motor-based
dose calibration. See [network inputs](NETWORK_INPUTS.md).

## Dose adjustment as a central step

The original method already considered intensity. The extension here is the
explicit connection between modeled motor reference, observed rMT, matched
motor/treatment hardware, interactive dose review, and session records.

At a fixed pose and model, E-field strength scales linearly with coil-current
slew rate. The field's relative spatial pattern stays fixed; the area above a
fixed reference can change. Console %MSO is converted through the selected
hardware calibration, rather than assumed to be exactly proportional to field
strength. See the [SimNIBS explanation](https://simnibs.github.io/simnibs/build/html/faq.html#what-is-the-di-dt-in-tms-simulations).

The report compares target exposure, target coverage, and off-target spread.
The motor-derived reference is not a universal activation threshold for other
cortical regions. Predicted dose selection, previewed intensity, and the
separately confirmed intensity actually used remain distinct records.
See [dose modeling](calibration_to_treatment_dose.md) and
[measured-dose records](measured_dose_records.md).

## Python workflow and review outputs

The current runtime needs no MATLAB. It supports the optional pfm-mefmri final
module and a standalone route using compatible derivatives. It adds optional
placement-error robustness, search replay, and a portable operator package with
one configured prescription per target and dose-review pages.

Unit and browser checks verify software behavior; they do not establish clinical
benefit or replace subject-specific review. See [migration](MIGRATION.md) for the
new layout and [installation](INSTALL.md) for dependencies and verification.
