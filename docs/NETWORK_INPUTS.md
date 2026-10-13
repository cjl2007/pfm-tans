# WTA, probability, and weighted target inputs

PFM-TANS accepts **WTA labels, weighted maps, or both**. The normal paired-input
route remains unchanged. When one counterpart is missing, target preparation
can synthesize it without editing the source files.

| What you supply | What TANS derives |
| --- | --- |
| WTA `.dlabel.nii` only | One weight map per network: **1 inside its label, 0 outside** |
| Probability/weight CIFTI only | Binary support from an **explicit user-set percentile** |
| Both maps and matching metadata | No counterpart conversion; existing behavior |

These are operational stand-ins, **not reconstructed probabilities or measured
certainty**. A value of 1 in a WTA-derived map means full binary membership, not
100% statistical confidence. Rescaled activation/FC strength is a relative
weight, not a membership probability. Conversion does not improve registration,
recover discarded information, or establish clinical suitability.

## WTA-only configuration

Merge these settings into the [study YAML](../examples/study_config.yaml); retain
the remaining anatomy, software, coil, calibration, and study settings:

```yaml
inputs:
  categorical_networks: /data/MyStudy/SUB001/maps/networks.dlabel.nii
  network_probabilities: null
  network_metadata: null  # infer names and colors from the WTA label table

input_fallbacks:
  enabled: true
```

Use the bundled metadata only if it actually matches your WTA atlas. Otherwise,
`network_metadata: null` uses the CIFTI's label table. Named categories become
consecutive internal labels; original keys and their mapping are recorded.
Label 0 and a label named `Noise` are unassigned background. Network names must
be unique after punctuation/case normalization. The source WTA is unchanged.

Select the network by its name, for example `--target-network Salience`, and
choose the appropriate anatomical search mask. Numeric selection is the
**one-based resolved metadata position**, not an arbitrary source label key;
names are safer for custom atlases.

## Weighted-only configuration: activation or FC example

```yaml
inputs:
  categorical_networks: null
  network_probabilities: /data/MyStudy/SUB001/maps/target_fc.dscalar.nii
  network_metadata: null

input_fallbacks:
  enabled: true
  weighted_to_binary_percentile: 90
  weight_polarity: positive
  weight_normalization: auto
  map_names: [MyFC]
```

Then use the same standalone driver or pfm-mefmri module configuration. For a
standalone treatment-only target, add these arguments to the documented launch:

```bash
--target-network MyFC \
--target-search-space /data/MyStudy/masks/cortical_search_zone.dscalar.nii \
--skip-sm-hand-calibration
```

A single treatment map does not supply `Somatomotor_Hand`. Either provide that
motor network as an additional map with its correct name/anatomical definition,
or skip motor calibration. Skipping it means **no motor-referenced dose-transfer
page**. In pfm-mefmri, set `TANS_INCLUDE_SM_HAND_CALIBRATION=0` for this case.

Keep `--pfm-dir` / `TANS_PFM_DIR` pointing to the existing map directory when
using the module. No pfm-mefmri code change is required; the YAML controls the
fallback inside pfm-tans.

### Naming and file correspondence

Use one CIFTI row per target/network. A `.dscalar.nii` may supply its scalar-map
names directly (`map_names: null`). A `.dtseries.nii` is also accepted as a stack
of target maps, but needs metadata or explicit row names; it is **not** treated
as an fMRI time series. `map_names` explicitly overrides names/colors from the
metadata when supplied. Metadata/map counts must agree; do not pair the bundled
multi-network metadata with a single custom FC map.

Both cortical hemispheres and the subject's compatible fs_LR indexing are
required, even for a unilateral target (the other hemisphere can contain zeros).
Weights must already be mapped to the cortical surface; this is not automatic
NIfTI-to-CIFTI resampling. Anatomical masks and vertex-area maps must correspond
to those same surface vertices. Generated metadata uses the existing MAT
resource format and requires no MATLAB code or installation.

### Exact percentile and weight rules

1. Select `positive` values by default. `negative` uses magnitudes of negative
   values; `absolute` uses either sign's magnitude. Choose explicitly for signed
   activation/FC maps; positive and negative effects are not interchangeable.
2. Within each map, compute the percentile over **finite, strictly positive
   selected-polarity values across cortex**, before the anatomical search mask.
   Zeros, nonfinite values, and the excluded sign do not enter the percentile.
   Nonfinite/opposite-sign values receive weight 0 and their counts are recorded.
3. Retain values **greater than or equal to** the cutoff. Linear percentile
   interpolation is used; ties are retained, so the support need not be exactly
   the requested fraction. `90` means approximately the highest 10% of nonzero
   selected-polarity cortical values, **not** the top 90% or a significance test.
   `0` retains all such values; `100` retains the maximum and its ties.
4. `auto` preserves weights already in [0,1], otherwise divides each map by its
   maximum. `max` always max-scales; `none` rejects weights exceeding 1. This is
   not min–max scaling, softmax, a probability calibration, or cross-map
   sum-to-one normalization. Relative values **below** the cutoff remain in the
   full weight map; the binary map defines target support separately.
5. With multiple maps, threshold each independently. At overlaps, the categorical
   stand-in assigns the **largest normalized qualifying weight**; exact ties go
   to the first input row. The resulting targets are mutually exclusive, so a
   map may lose vertices after thresholding. Threshold and final counts are
   recorded. Use separate run roots for independently overlapping targets.
6. Apply the configured anatomical mask and normal component-selection rules.
   Empty requested targets fail with an actionable error. TANS does not invent
   a missing network or reduce the percentile silently.

A single weighted target creates target-versus-unassigned labels, **not** a
whole-brain network parcellation. It can quantify target/off-target exposure,
but cannot identify the biological names of the other networks without a
supplied atlas. Per-map max scaling can also change which of several overlapping
raw activation maps wins; review the recorded scaling factors and label map.

## Outputs, auditing, and resuming

The driver writes `shared/network-inputs/NetworkInputManifest.json`, with source
fingerprints, requested settings, conversion details, and resolved file hashes.
Derived counterparts are stored alongside it as `ResolvedNetworks.dlabel.nii`,
`ResolvedNetworkWeights.dscalar.nii`, and `NetworkMetadata.mat`. For an unchanged
supplied pair, only the manifest is created. Low-level `prepare_targets.py`
invocations instead place this directory beside the requested preparation root,
with a `-network-inputs` suffix.

Resolved configuration, target/search manifests, the master report, and dose
reports/records identify synthesized counterparts. Original inputs are not
overwritten. A missing configured map file can trigger fallback when the other
exists; its missing path is printed and recorded. Prefer an explicit `null` to
make the intention clear. Missing metadata at a non-null path is an error.

Changing source content, percentile, polarity, normalization, or row names
requires a **fresh output directory**. Resume validates sources and derived
files before reusing prepared targets; it does not silently reuse a stale map.
Set `input_fallbacks.enabled: false` if your study requires both original inputs.
