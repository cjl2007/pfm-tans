# Two-stage search configuration

This page records the current Python two-stage TANS search contract. These are
configurable working defaults, not fixed scientific requirements. The fMRI
pipeline calls `pipeline/run_pipeline.py`, which uses
the implementation in `search/`.

## Target and component scope

| Option | Default | Meaning |
| --- | ---: | --- |
| `map_mode` | `weighted` | `weighted` optimizes mean native target probability in the hotspot; `binary` optimizes the cortical surface-area fraction assigned to the target network. |
| `minimum_design_cluster_vertices` | `10` | Absolute minimum connected-component vertex count. |
| `minimum_design_cluster_area_mm2` | `25` | Absolute minimum component cortical area. |
| `minimum_design_cluster_relative_weighted_area_fraction` | `0.10` | Minimum area-times-target-weight mass relative to the largest eligible component. |
| `scoring_target_scope` | `retained_eligible_components` | Use the same retained components for grid construction and primary scoring. |
| `report_full_network_scores` | `true` | Also record whole-network weighted and binary scores as diagnostics. |

A component must pass all three component thresholds. If none passes, the
largest weighted component is retained as an explicit fallback. Set the relative
threshold to `0` to recover the earlier absolute-only component filter. Set the
scoring scope to `full_network` to reproduce the earlier grid/objective mismatch;
this is available for comparison but is not recommended as a default.

Anatomical eligibility is target-specific. SM-Hand uses the left precentral
gyrus. Current LPFC targets use the named
`glasser_predefined_plus_subject_specific_mfg` recipe: TANS unions its bundled
Glasser left-LPFC TMS envelope with the current subject's `L_G_front_middle`
label and writes the resolved binary CIFTI into
`shared/search-spaces/`. This is the default treatment-target search space. A
user-supplied binary cortical CIFTI filepath overrides the recipe.
`restrict_weights_to_binary_support` can additionally limit a weighted map to
its categorical network support.

Binary mode is a true alternative objective, not only a reporting toggle. The
binary support map drives coarse-grid coverage and pose scoring. The weighted map
is retained as a companion QC layer so the two definitions can be compared
without ambiguity.

## Coarse search

| Option | Default | Meaning |
| --- | ---: | --- |
| `target_weighted_coverage_fraction` | `0.85` | Required fraction of retained projected target weight covered by centers. |
| `coverage_radius_mm` | `5` | A projected target point is covered when a center is within this scalp-geodesic radius. |
| `candidate_margin_mm` | `5` | Display/support margin around projected target territory. |
| `minimum_centers` | `1` | Lower center-count bound. |
| `maximum_centers` | `40` | Upper center-count bound. |
| `mandatory_centroid_anchor` | weighted centroid per retained component | Guarantees the direct scalp projection of each retained component centroid is sampled. |
| coarse orientation step | `15 degrees` | Produces orientations `0:15:165`. |

Centers use deterministic farthest-first scalp-geodesic coverage followed by
deterministic refinement. Density is expressed as target coverage rather than a
raw center count, so it scales with target extent. Classic, global-coarse,
cluster-anchored, and legacy dense grids remain comparison modes.

The historical configuration key remains
`target_weighted_coverage_fraction` for compatibility. Its interpretation follows
`map_mode`: probability-weighted surface-area coverage in `weighted` mode and
binary target surface-area coverage in `binary` mode. Each run records the
resolved interpretation as `coverage_measure`. Configured density changes use
a run-specific resolved policy file; they do not silently mutate the working
default. A study can, for example, explicitly select a coverage fraction of `0.90`.

## Nominal pose selection

| Option | Default | Meaning |
| --- | ---: | --- |
| `minimum_target_to_global_peak_ratio` | `0.95` | Requires `max(E in retained target) / max(E globally) >= 0.95`. |
| `enforce_target_peak_ratio_for_nominal_selection` | `true` | Applies the ratio as a coarse/fine eligibility gate. |
| hotspot percentiles | `99.0:0.1:99.9` | Thresholds averaged by the primary score. |
| primary score | retained-component weighted certainty | Area-weighted target probability within each suprathreshold hotspot. |

The 0.95 ratio does not require the single global maximum vertex to be in the
target. It tolerates a small numerical or boundary difference while rejecting a
pose whose strongest field is materially elsewhere. Set the minimum to `0` or
disable enforcement to remove this gate. Robustness simulations record the ratio
but do not discard perturbations that fail it, because those failures are part of
the sensitivity being measured.

## Fine and robust search

| Option | Default | Meaning |
| --- | ---: | --- |
| fine radius | `10 mm` | Scalp-geodesic neighborhood around the coarse winner. |
| fine spacing | `4 mm` | Fine center spacing. |
| relative fine orientations | `[-10,-5,0,5,10]` | Offsets from the coarse winning orientation. |
| `nominal_relative_floor` | `0.98` | Robustness finalists must be within 98% of the nominal fine maximum. |
| `maximum_finalists` | `3` | Maximum spatially distinct poses subjected to sensitivity simulations. |
| `minimum_finalist_separation_mm` | `4` | Spatial diversity requirement. |
| position errors | `[3,5,10] mm` | Sampled displacement radii. |
| azimuth samples | `8` | Directions sampled at each displacement radius. |
| orientation errors | `[0,5,10] degrees` | Sensitivity tolerances. |
| prescription condition | `<=5 mm, <=5 degrees` | Condition used to select the robust prescription. |
| selection metric | lower-tail mean of worst 10% | Prefers a stable neighborhood over a fragile nominal edge maximum. |

`RuntimeOverhead.json` records planning, simulation, and analysis time for the
robustness stage separately from coarse plus fine search.

## Practical handle orientation

The magnitude-based search resolves orientations over 180 degrees because the
supported figure-eight coils are treated as 180-degree equivalent for `|E|`.
The final physical prescription can then choose the ergonomic member of that
equivalence pair without changing the selected center, E-field map, nominal
score, or robustness score.

| Option | Default | Meaning |
| --- | ---: | --- |
| `prescription.handle_orientation.mode` | `prefer_posterior_inferior` | Choose between the simulated pose and its 180-degree alternative using the physical handle direction. Set `as_simulated` to disable canonicalization. |
| `coil_180_degree_magnitude_equivalent` | `true` for the configured C-B60/Cool-B65 workflows | Explicit safety assertion required before any automatic flip. It must be false for a coil/objective where 180-degree equivalence is not valid. |
| `physical_handle_local_axis` | `negative_y` | SimNIBS model-axis convention for the physical handle in the supported coil STL files. |
| `posterior_weight` | `1.0` | Preference for the handle's RAS Y component to point posteriorly. |
| `inferior_weight` | `1.0` | Preference for the handle's RAS Z component to point inferiorly. |

The neuronavigation manifest records the simulated and prescribed orientations,
both handle vectors in NIfTI RAS coordinates, whether a 180-degree flip was
applied, and both SimNIBS transforms. The Brainsight trajectory and high-level
pose rendering use the prescribed transform. Search-replay frames continue to
show the orientations actually simulated.

This policy applies only to magnitude-based targeting. A future objective that
uses signed/vector E-field direction must disable this equivalence rule and
evaluate the full physical orientation explicitly.

## Surface and mapping invariants

Current runs use subject-transformed `fs_LR 32k` surfaces only. Volume-to-surface
mapping remains the historical Workbench ribbon-constrained method with
`ENCLOSING_VOXEL`; the present search changes do not alter that mapping strategy.

## Calibration and treatment roles

The dose workflow treats SM-Hand/left precentral gyrus as the calibration target.
The treatment target is independently configured and may be AMN, SAL, FPN, or
another network. A technician-facing report must never label the measured motor
threshold as the treatment target.

The intended report contract is:

1. Enter the measured SM-Hand resting motor threshold as `%MSO`, or use recorded
   `dI/dt` when available.
2. Interpolate the coil/stimulator calibration curve and estimate the absolute
   cortical activation threshold from the SM-Hand calibration pose.
3. Select the configured treatment target and return its prescribed pose and
   treatment `%MSO`/`dI/dt`.
4. Display calibration bounds, extrapolation state, hardware or safety limits,
   and the target name beside the prescription.

The working upward safety limit is configured as
`dosing.maximum_treatment_rmt_ratio: 1.20`. It constrains treatment `%MSO` to at
most 120% of measured SM-Hand rMT `%MSO`; it places no limit on decreases. Set
the YAML value to `null` (or the corresponding shell variable to an empty
string) to disable the safeguard. The resolved setting and each entry's
calculated ceiling are written to the dose lookup artifacts.

The HTML interaction consumes precomputed pose/dose lookup JSON.
Changing the rMT input does not invoke SimNIBS in the browser.

The equations, lookup fields, and implementation boundary are specified in
[`calibration_to_treatment_dose.md`](calibration_to_treatment_dose.md).
