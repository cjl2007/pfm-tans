# Exterior Skin Validation

TANS scalp projection must use the external head surface, not every interface
labelled as skin in a CHARM mesh. This is an input-validity requirement, not a
target-network or scoring option.

## Two Distinct Failure Modes

1. SimNIBS `crop_mesh` combines multiple selection criteria with OR. Selecting
   triangles and tag 1005 in one call can retain other tissue surfaces. Apply
   the filters separately.
2. Tag 1005 itself includes skin facing internal air cavities. A tag-only
   selection still permits invalid candidate coil centers. Use the full native
   CHARM mesh with `Msh.partition_skin_surface`, retaining its external faces
   with `assume_single_outside_component=True`.

The partition is SimNIBS's normal-direction visibility classification with a
largest-connected-exterior-component constraint. It is an estimate and does not
replace visual head-model review. The original tetrahedral FEM mesh and its
conductivity labels remain unchanged; only the placement surface is extracted.

## Enforced Checks

- `extract_charm_scalp.py` writes a v3 provenance sidecar certifying exterior
  skin, source mesh/surface SHA-256 hashes, the partition method, and counts.
- Preparation rejects older tag-only or unverified scalp provenance.
- Search manifests must match the validated surface checksum.
- Each new simulation batch refits using the same exterior surface, validates
  its GIFTI against the extraction, and rejects centers more than 0.05 mm from
  exterior skin before any FEM execution.
- Exact simulated transforms are saved. Navigation export refits against
  exterior skin and checks agreement with those saved transforms.
- Resume rejects cached batches lacking the matching exterior surface checksum
  and matching coordinates, vertex IDs, orientations, coil and intensity inputs.
- Completion checks require exterior-skin validation for coarse, fine and
  robustness batches, and agreement between simulation, visualization and export.

## Historical correction example

The tag-only SAL domain included one island derived from nine vertices in its
largest retained cortical component. The island contained SAL coarse center 3,
approximately 19.75 mm from the external skin identified by SimNIBS. It was not
a separately retained tiny cortical component. The source vertices represented
approximately 3.32% of retained probability-weighted target area.

Exterior-only projection removes the island without discarding those cortical
vertices. SAL has one connected scalp domain, 10 coarse centers and 91.50%
weighted coverage within 5 mm. SM-Hand retains the same 12 centers and 90.61%
coverage. All 144 completed SM-Hand coarse transforms were refitted and matched
exactly (maximum matrix difference zero), permitting explicit, audited reuse of
those fields. Old plans and original metadata were archived, target inputs were
regenerated, and reused fields were rescored under the rebuilt plans. Runtime
accounting retains the original validated simulation time.

Artifacts in the subject's `tans/validation` folder record the grid comparison,
field checksums and per-pose geometry revalidation. Invalid earlier plans remain
under `old_tans` and must not be resumed or used for treatment. Exterior-skin
validation alone is not clinical clearance or a coil-collision/tolerability check.
