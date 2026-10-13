"""Check every published pose and calibration pairing against saved simulations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'search'))
from selected_pose import selected_pose_paths, robustness_enabled


def read(path):
    return json.loads(Path(path).read_text())


def check_dose_source(source, target, variant):
    selected = selected_pose_paths(target['target_root'], target['coarse_config'], variant)
    best = read(selected['best_pose'])
    center = selected['result'] / f"Center_{int(best['center_id']):05d}"
    benchmark = read(center / 'Benchmark.json')
    indices = np.flatnonzero(np.isclose(benchmark['orientations_degrees'],
                                        float(best['orientation_degrees']), atol=1e-8, rtol=0))
    if (source['network'] != target['network'] or source['selection_mode'] != selected['mode']
            or source['selected_pose'] != best or len(indices) != 1
            or Path(source['reference_field']).resolve() != (center / 'magnE.dtseries.nii').resolve()
            or source['reference_map_index_zero_based'] != int(indices[0])):
        raise ValueError(f'Dosing source does not match {target["network"]} {variant}')
    np.testing.assert_allclose(source['reference_didt_a_per_us'],
                               float(benchmark['didt_aperus']) / 1e6, atol=1e-10, rtol=0)


def validate_variants(manifest, index, config):
    from export_neuronavigation_package import sha256, validate_brainsight_roundtrip
    from simulated_pose_geometry import saved_pose_matrix
    from pose_prescription import canonicalize_pose_matrix, resolve_handle_policy
    targets = {item['network']: item for item in manifest['targets']}
    expected_variants = {network: {'fine', 'robust'} if robustness_enabled(read(target['coarse_config']))
                         else {'fine'} for network, target in targets.items()}
    if {item['network'] for item in index['targets']} != set(targets):
        raise ValueError('Pose variant index does not cover all targets')
    result = {'status': 'passed', 'clinical_clearance': False, 'pose_variants_checked': 0,
              'dose_pairings_checked': 0, 'feasible_dose_entries_checked': 0}
    for record in index['targets']:
        target = targets[record['network']]
        if set(record['variants']) != expected_variants[record['network']]:
            raise ValueError('Missing or unexpected pose variants')
        trajectory_names = set()
        for variant, item in record['variants'].items():
            selected = selected_pose_paths(target['target_root'], target['coarse_config'], variant)
            best, cfg = read(selected['best_pose']), read(selected['config'])
            benchmark = read(selected['result'] / f"Center_{int(best['center_id']):05d}/Benchmark.json")
            simulated = saved_pose_matrix(benchmark, float(best['orientation_degrees']))
            expected, _ = canonicalize_pose_matrix(simulated, resolve_handle_policy(cfg))
            report = Path(item['optimal_pose'])
            visual = read(report / 'VisualizationSummary.json')
            export_dir = Path(item['neuronavigation'])
            export = read(export_dir / 'NeuronavigationManifest.json')
            if (export['selection_mode'] != selected['mode'] or export['pose_variant'] != variant
                    or Path(export['sources']['selected_pose']).resolve() != selected['best_pose'].resolve()
                    or Path(visual['selected_pose_source']).resolve() != selected['best_pose'].resolve()):
                raise ValueError('Pose identity mismatch between export, report, and source')
            np.testing.assert_allclose(export['selected_pose']['simulated_matsimnibs'], simulated, atol=1e-8, rtol=0)
            for matrix in (visual['coil_affine'], export['selected_pose']['prescribed_matsimnibs']):
                np.testing.assert_allclose(matrix, expected, atol=1e-8, rtol=0)
            np.testing.assert_allclose(visual['mean_99_0_to_99_9_primary_selection_score'],
                                       float(best['mean_on_target_score']), atol=1e-10, rtol=0)
            for name, metadata in export['files'].items():
                if sha256(export_dir / name) != metadata['sha256']:
                    raise ValueError(f'Export integrity mismatch: {name}')
            trajectory, = export_dir.glob('*_OptimalTrajectory_Brainsight.txt')
            if f'_{variant.capitalize()}_' not in trajectory.name or trajectory.name in trajectory_names:
                raise ValueError('Trajectory variant names are not distinct')
            trajectory_names.add(trajectory.name)
            validate_brainsight_roundtrip(trajectory, expected)
            preview, = export_dir.glob('*_OptimalPoseOverview.png')
            if sha256(preview) != sha256(report / 'OptimalPoseOverview.png'):
                raise ValueError('Neuronavigation preview does not match pose variant')
            result['pose_variants_checked'] += 1
    calibration = targets.get('Somatomotor_Hand')
    treatments = {name: target for name, target in targets.items() if name != 'Somatomotor_Hand'}
    expected_pairs = set()
    if calibration and treatments:
        expected_pairs = {(reference, treatment) for reference in expected_variants['Somatomotor_Hand']
                          for treatment in ('fine', 'robust')
                          if any(treatment in expected_variants[name] for name in treatments)}
    pairs = [(p['calibration_variant'], p['treatment_variant']) for p in index['dose_pairings']]
    if set(pairs) != expected_pairs or len(set(pairs)) != len(pairs):
        raise ValueError('Missing, duplicate, or unavailable dose pairings')
    limit = config.get('dosing', {}).get('maximum_treatment_rmt_ratio', 1.20)
    limit = None if limit in (None, '') else float(limit)
    for pair in index['dose_pairings']:
        lookup = read(Path(pair['report']) / 'DoseLookup.json')
        check_dose_source(lookup['calibration'], calibration, pair['calibration_variant'])
        eligible = {name for name in treatments if pair['treatment_variant'] in expected_variants[name]}
        sources = lookup['treatment_sources']
        if {s['network'] for s in sources.values()} != eligible:
            raise ValueError('Dose pairing has missing or unexpected treatment targets')
        for source in sources.values():
            check_dose_source(source, targets[source['network']], pair['treatment_variant'])
        if lookup['policy']['maximum_treatment_rmt_ratio'] != limit:
            raise ValueError('Dose pairing changed the configured intensity safeguard')
        for entry in lookup['entries']:
            if entry['rmt_mso'] <= 0 or not np.isfinite(entry['absolute_threshold_v_per_m']):
                raise ValueError('Invalid rMT/threshold in dose pairing')
            if set(entry['treatments']) != set(sources):
                raise ValueError('Dose row does not match its declared targets')
            expected_maximum = None if limit is None else entry['rmt_mso'] * limit
            if expected_maximum is None:
                if entry['maximum_treatment_mso'] is not None:
                    raise ValueError('Unexpected dose ceiling')
            else:
                np.testing.assert_allclose(entry['maximum_treatment_mso'], expected_maximum, atol=1e-8, rtol=0)
            for label, row in entry['treatments'].items():
                if row is None:
                    continue
                source = sources[label]
                if (row['pose_selection_mode'] != source['selection_mode']
                        or row['pose_center_id'] != int(source['selected_pose']['center_id'])
                        or row['pose_orientation_degrees'] != float(source['selected_pose']['orientation_degrees'])):
                    raise ValueError('Dose row has mismatched treatment-pose identity')
                mso = row['treatment_mso']
                if not np.isfinite(mso) or (limit is not None and mso > expected_maximum + 1e-8):
                    raise ValueError('Dose exceeds configured upward safeguard')
                np.testing.assert_allclose(mso, round(mso, 1), atol=1e-8, rtol=0)
                result['feasible_dose_entries_checked'] += 1
        result['dose_pairings_checked'] += 1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--index', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    manifest = read(args.manifest)
    index = read(args.index)
    config = yaml.safe_load(Path(manifest['configuration']).read_text())
    result = validate_variants(manifest, index, config)
    args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
