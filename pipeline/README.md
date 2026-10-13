# TANS pipeline integration

`run_pipeline.py` is called by `modules/mefmri_func_tans.sh` in the fMRI pipeline.
Its stages are `headmodel`, `targets`, `plan`, `search`, and `reports`.
`config_template_smhand.yaml` is the reusable template; the wrapper supplies
subject paths and treatment/calibration options.

`network_inputs.py` resolves WTA-only or weighted-only inputs into the internal
map pair, records conversion provenance, and checks cached maps on resume.
See [input fallback recipes](../docs/NETWORK_INPUTS.md).

Scientific search and rendering live in `../search/`.
Together these directories implement the current TANS workflow.
`validate_completed_run.py` checks existing run artifacts; `service_status.py`
supports completion receipts for background services. Tests are colocated.

See the [repository README](../README.md),
[output layout](../docs/INPUTS_AND_OUTPUTS.md),
[dose interface](../docs/dose_preview_and_landmarks.md), and
[portable exports](../docs/operator_export_package.md).
