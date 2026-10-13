# Robustness Sampling on the Native Skin Mesh

`azimuth_samples_per_radius` requests up to that many distinct sample vertices
per finalist and radius (default 8). A radius is a hard upper bound on measured
scalp-geodesic displacement, not an annulus that can extend outside the bound.

The sampler considers exterior-skin vertices within the configured radius,
excluding the nominal vertex, and assigns unique vertices to equally spaced
tangent-plane directions using SciPy linear assignment. The cost combines
angular mismatch and normalized radial shortfall, favoring boundary coverage.
Candidates are ordered by vertex ID for deterministic execution.

If fewer vertices are available than requested, use each available vertex once
and record the reduced count. Do not enlarge the error disk or duplicate a pose
to reach an arbitrary count. Fail when no distinct vertex is available or the
sample directions leave a gap of 180 degrees or more.

`RobustPlanSummary.json` records each finalist/radius's requested and actual
sample counts, minimum/maximum actual radius, actual azimuths, maximum angular
gap, and a mesh-limited flag. `Perturbations.tsv` retains both requested and actual
offsets. Sensitivity evaluation and QC use the measured displacement to determine
membership in the 3/5/10 mm scenarios; duplicated centers are counted once.
These are discrete sampled perturbations, not proof of a continuous error bound.

## Example mesh-limited sampling case

After 144 coarse and 70 fine SM-Hand simulations completed, the old fixed
annulus sampler failed because it found only seven vertices near a requested
5 mm ring. Its previous +/-0.8 mm annulus could also include displacements
outside the advertised bound.

The corrected plan keeps all tested displacements within their requested bounds.
Each of the three finalists has eight directional points at 5 and 10 mm; at
3 mm the mesh supplies seven, six and seven distinct vertices. The plan records
these limitations explicitly. Coarse/fine results and the on-target score
formula are unchanged; robustness sampling and actual-radius binning are fixed.
