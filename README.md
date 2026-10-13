# PFM-TANS

TANS uses functional maps and anatomy from **an individual brain** to plan TMS
coil position, orientation, and stimulation intensity. It models how the electric
field overlaps the intended network and neighboring networks, then supports
individualized dose adjustment and neuronavigation export.

Run it as an optional final module of
[pfm-mefmri](https://github.com/cjl2007/pfm-mefmri), or use the same Python workflow
standalone with compatible anatomical derivatives and network maps.
**No MATLAB is required.**

## Why targeting and dose belong together

![TANS sequence: move the coil to concentrate the field in the target network, estimate a motor-derived reference, then balance target coverage and off-target exposure at the fixed pose](docs/assets/targeting-dose-field-scaling.gif)

TANS first searches coil position and orientation to concentrate the strongest
field in the **target network**. At the selected pose, increasing dose scales the
same spatial field pattern. A small above-reference hotspot can be highly
specific yet cover very little of the target; higher doses reach more target
cortex but can also expose **off-target networks**. TANS favors selectivity among
doses meeting the configured exposure and output limits.

*Synthetic illustration. Search comes first, then dose selection at the fixed
pose. The reference and dose choices shown are illustrative.*
[Animation details and full-resolution movie](docs/VISUALIZATIONS.md#targeting-and-dose-the-tans-sequence).

The motor-derived reference comes from a separate calibration step.
[rMT / E-field procedures](docs/calibration_to_treatment_dose.md).

## What has changed since the 2022 method

The [original TANS method](https://pmc.ncbi.nlm.nih.gov/articles/PMC11446252/)
established individualized network-based E-field targeting and included
post-search intensity modeling. This implementation extends that approach:

| Area | Current workflow |
| --- | --- |
| Search efficiency | Coarse search followed by local fine refinement, parallel workers, and reuse of completed stages. Runtime depends on the subject and search settings. |
| Input flexibility | Categorical networks, probability/weight maps, or both; network identity and anatomical search region are configured separately. |
| Runtime | Python orchestration and SimNIBS; no MATLAB runtime. |
| Dose adjustment | A central workflow step: matched motor/treatment hardware calibration, observed-rMT confirmation, interactive target/off-target exposure, and separate records of the intensity used. |
| Review and export | Optional placement-error robustness, search replay, and portable neuronavigation and dose-review packages. |

[Method changes and limits](docs/METHOD_CHANGES.md). The current features have not
all been validated by the 2022 paper; no fixed speedup over that implementation
is claimed here.

## Coil search in an individual brain

![Salience target, coarse and fine coil searches, search history, and modeled cortical electric fields](docs/assets/sal-optimization.png)

*Illustrative SAL example. The figure's history scores are schematic and its
final placement is an adjacent candidate, not the optimizer's winner.*

![Animated Salience coil-search replay showing position, orientation, cortical electric field, and recorded search scores](docs/assets/sal-search-replay.gif)

*The replay uses saved simulation scores and ends at the actual fine-search
winner: 228 coarse and 80 fine poses. Playback speed is not simulation runtime.*
[Full search movie](docs/assets/sal-search-replay.mp4).

SAL and left prefrontal cortex are examples. Other network or weighted targets
and cortical search masks can be supplied; changing the network name alone does
not change the default search region.
[Configure targets and search anatomy](docs/CONFIGURATION.md#network-and-anatomical-search-region).

## Electric-field dose adjustment

A target coordinate does not specify how much of the intended network receives
stimulation. The field also depends on individual anatomy, coil position and
orientation, and intensity. Increasing intensity can expose more target cortex
while also reaching neighboring networks.

TANS uses the individual's resting motor threshold (**rMT**) as an anchor.
Reproducing the motor coil pose and output in the head model, with matched
hardware calibration, estimates the field associated with that motor response
in the hand representation. This supplies an individual field reference for
exploring dose at the treatment target. Transferring it beyond motor cortex is
an explicit modeling assumption, not a universal activation threshold.

With the optimized treatment pose held fixed, TANS compares target and
off-target exposure across intensities. It favors selectivity among doses
meeting the configured minimum exposure and output limits, making both **where**
and **how strongly** to stimulate part of the plan.
[How the motor reference becomes a treatment dose](docs/calibration_to_treatment_dose.md).

![Motor-reference calibration, intensity-response curves, and target versus neighboring-network exposure across modeled treatment intensities](docs/assets/sal-dosing.png)

*Hypothetical rMT: 50% MSO. The displayed 55% treatment intensity is a comparison;
the model selected 51.2% under this example's criteria. The 70% panel exceeds the
example's 60% ceiling. These are modeled examples, not treatment records.*

### Dose selection page

![Dose selection page walkthrough showing rMT confirmation, interactive target and off-target exposure, and recording the intensity actually used](docs/assets/sal-dose-workpage.png)

The page supports any configured treatment target; Salience is the example shown.
Enter and confirm the observed rMT, explore modeled intensity and spread, then
separately record the intensity actually used. Downloaded session PDF/PNG and
JSON records are saved through the browser; they are not saved automatically.
[Measured-dose workflow](docs/measured_dose_records.md) ·
[Full gallery and figure provenance](docs/VISUALIZATIONS.md).

## Get started

1. Install the [dependencies](docs/INSTALL.md) and keep `pipeline/`, `search/`, and
   `res0urces/` together.
2. Copy [the study YAML](examples/study_config.yaml) into your study folder.
   Replace software and coil paths and matched calibration IDs; then review
   target, search, robustness, and dose settings. [Configuration guide](docs/CONFIGURATION.md).
3. Choose your launch route:

| Route | Starting point | Guide |
| --- | --- | --- |
| Final pfm-mefmri module | A complete study config with anatomy and PFM stages | [Module setup](docs/PFM_MEFMRI.md) |
| Standalone | Compatible native T1w/T2w, fs_LR 32k derivatives and transforms, and network/weighted maps | [Standalone setup](docs/STANDALONE.md) |

Both routes run `headmodel → targets → plan → search → reports` using the same
implementation. Standalone use does not require fMRI time series once compatible
inputs are available. New examples use **RidgePFM**; input filenames can be adapted
for other mapping tools or existing studies.
[Input requirements and single-map options](docs/INPUTS_AND_OUTPUTS.md).

## Review the results

| Location under the output folder | Contents |
| --- | --- |
| `reports/index.html` | Research/QC review and output inventory |
| `targets/<target>/reports/` | Target, pose, search replay, and available comparisons |
| `shared/export_<subject>/index.html` | Portable operator package; copy the whole folder |
| `shared/export_<subject>/<target>/dose-calibration/DoseCalibrationReport.html` | Interactive dose review and measured-rMT workflow |

Review head models, input maps, coil geometry, calibration, and outputs before
use. Model selection is distinct from an observed treatment record. Keep session
downloads outside the operator package. See [workflow review](docs/WORKFLOW.md)
and [operator exports](docs/operator_export_package.md).

[Migration](docs/MIGRATION.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) ·
[Search settings](docs/two_stage_search_configuration.md) ·
[Release checks](docs/RELEASE_CHECKLIST.md).

## Citation

Lynch CJ, Elbau IG, Ng TH, Wolk D, Zhu S, Ayaz A, Bukhari H, Power JD, Liston C.
*Automated optimization of TMS coil placement for personalized functional network
engagement.* Neuron. 2022;110(20):3263–3277.e4.
[doi:10.1016/j.neuron.2022.08.012](https://doi.org/10.1016/j.neuron.2022.08.012).

Cite the software revision and configuration used alongside the original method.
Research software; modeled exposure is not proof of clinical efficacy or safety.
