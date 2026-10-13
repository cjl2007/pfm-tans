# Input contract and output layout

Both launch routes use this contract. pfm-mefmri normally supplies these
derivatives; standalone users must supply equivalent, coordinate-consistent
files. TANS does not perform functional preprocessing or infer individualized
networks from raw fMRI.

## Anatomy: exact current layout

For a subject directory named `SUB001`, the standard full run expects:

```text
SUB001/
  anat/
    T1w/
      T1w.nii.gz                         original native T1w
      T1w_acpc.nii.gz                    reference for the surface affine
      xfms/
        acpc.mat                        native-to-ACPC FSL affine
        acpc_inv.mat                    optional; derived if absent
      fsaverage_LR32k/
        SUB001.L.white.32k_fs_LR.surf.gii
        SUB001.R.white.32k_fs_LR.surf.gii
        SUB001.L.pial.32k_fs_LR.surf.gii
        SUB001.R.pial.32k_fs_LR.surf.gii
        SUB001.L.midthickness.32k_fs_LR.surf.gii
        SUB001.R.midthickness.32k_fs_LR.surf.gii
        SUB001.L.inflated.32k_fs_LR.surf.gii
        SUB001.R.inflated.32k_fs_LR.surf.gii
        SUB001.midthickness_va.32k_fs_LR.dscalar.nii
    T2w/
      T2w.nii.gz                         original native T2w
    MNINonLinear/fsaverage_LR32k/
      SUB001.L.atlasroi.32k_fs_LR.shape.gii
      SUB001.R.atlasroi.32k_fs_LR.shape.gii
      SUB001.L.aparc.a2009s.32k_fs_LR.label.gii
      SUB001.L.sulc.32k_fs_LR.shape.gii
```

- Each surface has 32,492 vertices per hemisphere and must retain the matching
  fs_LR vertex correspondence. The adapter transforms surfaces into native
  head-model space; it does not accept arbitrary mesh topology/density.
- The native images must be the exact `anat/T1w/T1w.nii.gz` and
  `anat/T2w/T2w.nii.gz` inputs. Restored, ACPC, registered, or template-space
  images are not drop-in replacements.
- The forward affine is required. An existing inverse is checked against it;
  otherwise FSL derives the inverse in the TANS output. The transform must be
  the one relating the supplied native and ACPC references.
- The default SM-Hand mask requires `L_G_precentral`. The default treatment
  search recipe also requires `L_G_front_middle` in the left anatomical labels.
  Another mask can be configured. An appropriate SM-Hand template is required
  when including motor calibration or targeting SM-Hand itself, but not for a
  non-motor treatment-only run with calibration explicitly skipped.
- The left sulcal metric is required for the current report stage. Supplying
  only the meshes needed for FEM can still fail later during rendering.
- Vertex areas must be finite and positive with matching CIFTI indexing.

The subject ID comes from the subject directory's basename. Native-image and
surface locations are currently fixed by the adapter; changing their YAML path
strings is not a general arbitrary-layout interface. The vertex-area path and
target anatomical-mask paths are configurable.

## Functional networks

Default location:

```text
SUB001/func/rest/PFM/RidgePFM/
  RidgePFM_VTX.dlabel.nii
  RidgePFM_VTX_ProbMaps.dtseries.nii
```

| Input | Requirements |
| --- | --- |
| Categorical networks | One CIFTI label map with a label table; both cortical hemispheres; or `null` to derive from weights |
| Network probabilities / weights | One row per target/network, in metadata order; compatible cortical indexing; or `null` to derive 0/1 weights from WTA |
| Network metadata | `Priors.mat` containing `Priors.NetworkLabels` and `Priors.NetworkColors`; a final `Noise` entry is excluded. For a single supplied representation, `null` allows names/colors from WTA labels, scalar-map names, or explicit `input_fallbacks.map_names` |

The `.dtseries.nii` probability file contains **network maps**, not resting-state
time points. Its map count must match the metadata's network count. Matching file
extensions alone are not evidence of compatibility. Preserve the same dense-axis
ordering across labels, probabilities, area maps, and custom search-space masks.

Supply **at least one** representation. If only WTA is available, target weights
are 1 inside each label and 0 outside. If only probabilities/weights are available,
set `input_fallbacks.weighted_to_binary_percentile` explicitly; there is no
implicit percentile. Both supplied means no conversion. The internal pipeline
then receives a complete pair in either objective mode.

Other network atlases and activation/FC targets require compatible surfaces and
documented map names/order. The fallback does not register volumes to cortex,
recover missing uncertainty, or infer a motor network from a treatment-only map.
See [network input recipes and conversion rules](NETWORK_INPUTS.md).

Use `--pfm-dir` / `TANS_PFM_DIR` for another network directory. In YAML, change
`inputs.categorical_networks` and `inputs.network_probabilities` for custom names
or absolute paths. The bundled metadata is appropriate only when it actually
describes those maps. No MATLAB installation is needed to read it.

## Software and study resources

Supply the [configured executables](INSTALL.md), actual coil model files, and
matching dI/dt calibration identities. The current renderer also requires the
coil's sibling `.stl` file next to its configured model. A new simulation coil
without compatible render geometry/calibration is not automatically ready for
the complete reports-and-export workflow.

The repository supplies the default search-policy JSON, network metadata,
search-space envelope, and calibration registry/tables. Keep `res0urces/` with
the code. A custom treatment region is a binary cortical CIFTI, not a volume mask
silently resampled by TANS.

## Outputs

Module default: `<SubjectDir>/tans/`. Standalone: the required `--output` path.

| Path relative to output root | Contents |
| --- | --- |
| `config/requested.yaml`, `config/resolved.yaml` | Template snapshot and resolved runtime settings |
| `shared/headmodel/` | Native CHARM model, exterior scalp, adapted surfaces, adapter manifest |
| `shared/target-preparation/` | Eligible maps, components, target-preparation manifest |
| `shared/search-spaces/` | Resolved subject-specific search-space masks |
| `MultinetworkRunManifest.json`, `ResolvedSearchPolicy.json` | Target configurations and resolved search policy |
| `targets/<target>/search/` | Coarse/fine and enabled robustness simulation/scoring artifacts |
| `targets/<target>/reports/` | Target/pose review, replay, available pose comparisons |
| `reports/index.html`, `ReportIndex.json` | Master review and artifact inventory |
| `PipelineTiming.json` | Stage timing/reuse record |
| `shared/export_<subject>/` | Portable, hash-validated operator package |
| `shared/work/publishing/` | Internal dose/publication sources and archived previous exports |
| `validation/` | Software-validation receipts when the corresponding checks run |

Browser-generated measured-dose records are **not** automatic pipeline outputs.
Save their PDF/JSON together, for example under
`shared/session-records/<subject>/<target>/<session>/`, outside the immutable
operator package. That directory is a storage convention, not an automatic
clinical write-back service.

Manifests are authoritative for exact paths. See [operator exports](operator_export_package.md),
[measured-dose records](measured_dose_records.md), and [pose variants](pose_variant_outputs.md).
