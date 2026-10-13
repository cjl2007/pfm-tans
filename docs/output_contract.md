# Python workflow and output contract

Python is the current implementation called by `pfm-mefmri`, including target
preparation. The retired MATLAB source and migration configs have been removed.
Scientific logic stays in this repository; the wrapper supplies run settings to
`pipeline/run_pipeline.py`.

See [workflow](WORKFLOW.md) and [inputs/outputs](INPUTS_AND_OUTPUTS.md).
TANS creates its own CHARM model from the native T1w/T2w with `--forceqform`;
the upstream anatomical CHARM model is not a substitute.

Resolved search configurations record the retention profile (`standard`,
`debug`, or `research`). Raw simulation retention follows those settings.
Preserve incomplete artifacts needed for resume. Portable report/export
contents are described in [operator_export_package.md](operator_export_package.md).
