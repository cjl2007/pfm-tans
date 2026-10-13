# Calibration-to-treatment dose

Current code: `search/generate_calibration_dose_report.py`
and the `dose_*` helpers. `pipeline/calibration_registry.py`
resolves coil/stimulator calibration identities.

## Why targeting and dose belong together

A coil coordinate identifies a location, but not the complete cortical exposure
pattern. Individualized networks can lie next to one another within the same
gyrus. TANS therefore evaluates the modeled E-field against the person's network
boundaries, linking **coil geometry** to **stimulation intensity** in one workflow.

### 1. Place the field on the intended network

The coarse-to-fine search varies coil position and orientation. It favors
high-field exposure within retained target components and applies the configured
target/global-peak proximity gate. This accounts for where the strongest field
lies and the network composition of its surrounding high-field footprint—not
just whether a coordinate falls inside a network. The gate is a configurable
proximity criterion, not a guarantee that the global peak is exactly on target.

Pose scoring uses **relative-field hotspots** across configured field percentiles.
They characterize spatial selectivity without assuming a measured rMT. They are
not yet an estimate of the cortical area exposed above a physiological reference
at the treatment intensity.

### 2. Anchor the field scale to an individual motor measurement

The observed rMT supplies an empirical output level that meets the protocol's
motor-response criterion. With the matching motor-reference coil pose and
hardware calibration, TANS scales the simulated motor field to that output and
derives a reference field strength from the hand representation. The current
report default is its probability-weighted **99th percentile**, with weights
raised to power **2**; the resolved lookup records these settings.

This connects stimulator output to an individualized modeled cortical field,
rather than assuming that a given percentage of maximum stimulator output (MSO)
produces the same cortical exposure across people, locations, or coils. Motor and
treatment hardware have their own calibration curves; they must match the setup.

### 3. Select intensity at the chosen treatment pose

Hold the selected coil geometry and motor-derived reference fixed. Scale the
treatment field over candidate intensities using the treatment calibration.
The relative field pattern stays the same, but the amount of cortex exceeding
the fixed reference can grow. This above-reference region is the **modeled
exposure hotspot** used for dosing.

At each intensity the report distinguishes:

- **Target exposure:** target cortical area above the reference.
- **Specificity:** the fraction of the total above-reference hotspot in the target.
- **Coverage:** the fraction of the target above the reference.

Low output may produce little or no target exposure. Higher output can increase
coverage while including more neighboring-network cortex, reducing specificity.
The current selector maximizes the configured selectivity metric among doses
meeting the minimum total-hotspot-area criterion, positive target overlap, and
the output ceiling; it uses the lowest intensity to break ties. Reporting
coverage does not mean the selector maximizes coverage. If no intensity meets
these criteria, the report says so rather than relaxing the study's rules.

The resulting prescription makes both **where** and **how strongly** to stimulate
explicit. In this implementation geometry search comes first and intensity
selection follows at the fixed pose; this is not a simultaneous joint optimizer.

### Interpretation and scope

Transferring a motor-derived field reference to another cortical region is an
explicit modeling assumption. It is **not a universal neuronal activation
threshold**, and exceeding it does not establish neural activation, clinical
benefit, or safety. The maps describe modeled exposure; observed engagement and
outcomes require independent measurement. A protocol ceiling is a constraint,
not clinical clearance.

SAL is an example treatment network, not a software restriction. The same logic
can be applied to other compatible individualized networks or target patterns
and cortical zones, with an appropriate anatomical search mask and feasible
coil access/field reach.
See [network/search-region setup](CONFIGURATION.md#network-and-anatomical-search-region)
and the [conceptual animation and SAL example](VISUALIZATIONS.md).

## Operator inputs and configured limits

SM-Hand/left precentral gyrus is the calibration target; the treatment target
is independently configured. Reports take measured SM-Hand rMT in `%MSO`
and use calibration curves and simulated fields to provide the treatment lookup.
Browser interaction uses precomputed data without launching a SimNIBS solve.

`dosing.maximum_treatment_rmt_ratio` is the optional upward ceiling:
`treatment_MSO <= maximum_treatment_rmt_ratio * measured_rMT_MSO`.
The working default is `1.20`: 45% MSO rMT permits at most 54% MSO treatment.
Set the YAML value to `null`, or the wrapper's
`TANS_MAXIMUM_TREATMENT_RMT_RATIO` to an empty string, to disable it.

See [dose previews and model limitations](dose_preview_and_landmarks.md),
[operator exports](operator_export_package.md), and
[calibration provenance](../res0urces/StimulatorCalibration/README.md).
