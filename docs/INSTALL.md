# Installation

PFM-TANS is a source-tree Python application, not currently a pip-installable
package. Keep `pipeline/`, `search/`, and `res0urces/` together. No MATLAB runtime
is needed; SciPy reads the bundled network metadata in `Priors.mat`.

## Obtain the matching source

```bash
git clone https://github.com/cjl2007/pfm-tans.git /opt/pfm-tans
```

Choose a writable location and substitute it throughout the examples. Use a
revision containing `pipeline/run_pipeline.py`; older revisions have the MATLAB
layout. For module use, separately install a pfm-mefmri revision containing
`modules/mefmri_func_tans.sh` and the documented wrapper settings. Neither
repository automatically installs or updates the other.

## External software

| Dependency | Used for | Configuration |
| --- | --- | --- |
| [SimNIBS](https://simnibs.github.io/simnibs/build/html/installation/simnibs_installer.html) (development stack: 4.5) | CHARM, FEM simulations, supplied coil models | `subject_adapter.charm_binary`, `simulation.simnibs_python`, coil paths |
| [Connectome Workbench](https://www.humanconnectome.org/software/get-connectome-workbench) | Surface transforms and E-field mapping | `subject_adapter.workbench` |
| [FSL](https://fsl.fmrib.ox.ac.uk/fsl/docs/install/index.html) | `convert_xfm` and `fslmerge` | On `PATH`, with its environment initialized |
| FFmpeg | Search-replay movies | `ffmpeg` on `PATH` |
| Python | Orchestration, scoring, rendering | Launch interpreter; `TANS_PYTHON` in pfm-mefmri |

Python needs PyYAML, NumPy, SciPy, nibabel, matplotlib, Pillow, and trimesh. The
SimNIBS Python environment is the simplest starting point; verify imports before
installing anything into it. Use Python 3.10+ for the current code; the working
SimNIBS environment has been exercised with Python 3.11. Preserve SimNIBS's own
dependency constraints rather than upgrading its packages indiscriminately.

The current commands and system tools assume Linux. Other operating systems are
not claimed to be validated deployment targets. Install external dependencies
under their own installation and licensing requirements. Example paths below
are illustrative, not software downloads.

## Verify the environment without processing a subject

```bash
/opt/SimNIBS-4.5/simnibs_env/bin/python -c \
  "import yaml, numpy, scipy, nibabel, matplotlib, PIL, trimesh, simnibs; print('imports OK')"
/opt/SimNIBS-4.5/simnibs_env/bin/python \
  /opt/pfm-tans/pipeline/run_pipeline.py --help
/opt/SimNIBS-4.5/simnibs_env/bin/charm --help
/opt/workbench/bin_linux64/wb_command -version
command -v convert_xfm
command -v fslmerge
ffmpeg -version
```

`--help` does not validate subject files or start CHARM. For headless rendering,
select an appropriate noninteractive matplotlib backend in your job environment,
for example `MPLBACKEND=Agg`.

## Create the study configuration

Copy [examples/study_config.yaml](../examples/study_config.yaml) to a study-owned
location, for example `/data/MyStudy/config/tans.yaml`. Edit the absolute CHARM,
Workbench, Python, and coil-model paths. Review calibration identities, objective,
search density, robustness, and dose-limit choices with your study team.

From the repository root:

```bash
mkdir -p /path/to/MyStudy/config
cp examples/study_config.yaml /path/to/MyStudy/config/tans.yaml
# Module users also copy this fragment into their complete pfm-mefmri config:
cp examples/pfm_mefmri_tans.sh /path/to/MyStudy/config/tans_module.sh
```

Both YAML templates are generic: `pipeline/config_template_smhand.yaml` mirrors
`examples/study_config.yaml`. Replace **every** `/path/to` and `REPLACE_WITH`
placeholder before running. Set the matched motor/treatment calibration IDs;
`simulation.coil` must match `simulation.treatment_coil`. The shell fragment
starts with TANS disabled and no hardware overrides. After editing, source it
at the end of the complete pfm-mefmri config and set `TANS_ENABLE=1` there.
Preserve existing study configs separately; do not overwrite them with a template.

Only `{subject}`, `{subject_dir}`, `{func_dir}`, `{pfm_dir}`, `{tans_root}`, and
`{pfm_tans_root}` placeholders are expanded by the driver. Shell expressions such
as `$HOME`, `$SIMNIBS`, and `$(...)` are not evaluated inside YAML. Use absolute
paths for installation-dependent software.

Continue with [pfm-mefmri setup](PFM_MEFMRI.md), the recommended route, or
[standalone setup](STANDALONE.md). Both require the same
[anatomical and network inputs](INPUTS_AND_OUTPUTS.md).

## Optional developer tools and limits

Node.js and Chrome support browser regression scripts. XlsxWriter is needed only
to regenerate calibration spreadsheet review copies, not for runtime conversion.
systemd tools support optional background-service receipts; they are not required
to launch `run_pipeline.py` in a terminal or batch job.

There is currently no automatic dependency installer or full subject preflight
command. Inspect inputs before launching: early stages can start CHARM before
all later target/report inputs have been checked.
