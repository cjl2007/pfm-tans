"""Check numerical/report/export agreement before closing a Python TANS run."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import nibabel as nib
import numpy as np
from scipy.spatial import cKDTree

from prepare_subject import validate_search_scalp


def load(path):
    return json.loads(Path(path).read_text())


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def validate(root):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'search'))
    from pose_prescription import canonicalize_pose_matrix, resolve_handle_policy
    from simulated_pose_geometry import saved_pose_matrix
    from selected_pose import selected_pose_paths

    manifest = load(root / 'MultinetworkRunManifest.json')
    validate_search_scalp(manifest)
    result = {'status': 'software_checks_passed', 'targets': {},
              'clinical_clearance': False,
              'requires_manual_review': ['Head-model and target QC',
                                         'Trajectory imported and visually confirmed in Brainsight',
                                         'Actual rMT measured at prescribed SM-Hand pose; protocol review']}
    for target in manifest['targets']:
        target_root = Path(target['target_root'])
        cfg = load(target['coarse_config'])
        selected = selected_pose_paths(target_root, target['coarse_config'])
        skin = nib.load(cfg['skin']).get_arrays_from_intent(1008)[0].data
        tree = cKDTree(skin)
        counts = {}
        maximum_error = 0.
        stages = ('coarse', 'fine', 'robustness') if selected['robustness_enabled'] else ('coarse', 'fine')
        if not selected['robustness_enabled']:
            if (target_root / 'search/robustness').exists():
                raise ValueError('Disabled robustness has unexpected outputs')
            counts['robustness'] = 0
        for stage in stages:
            centers = sorted((target_root / f'search/{stage}/simulation').glob('Center_*/Benchmark.json'))
            if not centers:
                raise ValueError(f'Missing {stage} benchmarks for {target["display_name"]}')
            count = 0
            for path in centers:
                benchmark = load(path)
                if benchmark.get('geometry_validation', {}).get('status') != 'validated':
                    raise ValueError(f'Unvalidated center: {path}')
                if benchmark['geometry_validation'].get('surface_scope') != 'exterior_skin':
                    raise ValueError(f'Center lacks exterior-skin validation: {path}')
                requested = np.asarray(benchmark['center_xyz'])
                if tree.query(requested)[0] > .05:
                    raise ValueError(f'Off-scalp requested center: {path}')
                for angle in benchmark['orientations_degrees']:
                    matrix = saved_pose_matrix(benchmark, angle)
                    contact = matrix[:3, 3] + benchmark['distance_mm'] * matrix[:3, 2]
                    error = float(np.linalg.norm(contact - requested))
                    maximum_error = max(maximum_error, error)
                    if error > .05 or tree.query(contact)[0] > .05:
                        raise ValueError(f'Fitted coil differs from requested scalp center: {path}')
                field = nib.load(str(path.parent / 'magnE.dtseries.nii')).get_fdata()
                if (field.shape[0] != len(benchmark['orientations_degrees']) or
                        not np.isfinite(field).all() or np.min(field) < 0):
                    raise ValueError(f'Invalid saved E-field array: {path}')
                count += field.shape[0]
            counts[stage] = count
        best_path = selected['best_pose']
        best = load(best_path)
        benchmark = load(selected['result'] / f'Center_{int(best["center_id"]):05d}/Benchmark.json')
        simulated = saved_pose_matrix(benchmark, float(best['orientation_degrees']))
        expected, decision = canonicalize_pose_matrix(simulated, resolve_handle_policy(cfg))
        from publication_paths import navigation_source
        export_path = navigation_source(target_root) / 'NeuronavigationManifest.json'
        if not export_path.is_file():
            export_path = target_root / 'export/neuronavigation/NeuronavigationManifest.json'
        export = load(export_path)
        visual = load(target_root / 'reports/optimal-pose/VisualizationSummary.json')
        if not selected['robustness_enabled'] and export.get('selection_mode') != 'nominal_fine':
            raise ValueError('Export does not identify the nominal-only selection')
        if Path(visual['selected_pose_source']).resolve() != best_path.resolve():
            raise ValueError('Visualization references a different selected pose')
        for observed in (export['selected_pose']['simulated_matsimnibs'],):
            np.testing.assert_allclose(observed, simulated, atol=1e-8, rtol=0)
        for observed in (export['selected_pose']['prescribed_matsimnibs'], visual['coil_affine']):
            np.testing.assert_allclose(observed, expected, atol=1e-8, rtol=0)
        if export['validation']['brainsight_export_readback_max_abs_error'] > 1e-3:
            raise ValueError('Brainsight text roundtrip exceeds tolerance')
        if not np.isclose(visual['mean_99_0_to_99_9_primary_selection_score'],
                          float(best['mean_on_target_score']), atol=1e-10, rtol=0):
            raise ValueError('Reported on-target score differs from selected pose')
        runtime = load(target_root / 'RuntimeSummary.json')
        if runtime.get('robustness_enabled', True) != selected['robustness_enabled']:
            raise ValueError('Runtime and selection robustness modes disagree')
        for stage, count in counts.items():
            if count != runtime[f'{stage}_poses']:
                raise ValueError(f'Simulation count mismatch: {stage}')
        result['targets'][target['display_name']] = {
            'selection_mode': selected['mode'], 'robustness_status': selected['robustness_status'],
            'simulation_counts': counts, 'maximum_contact_error_mm': maximum_error,
            'selected_pose_sha256': digest(best_path), 'handle_flip_applied': decision['flip_applied'],
            'simulation_visualization_export_matrices_agree': True,
            'scoring_visualization_agree': True}
    from publication_paths import dose_source
    lookup_path = dose_source(root) / 'DoseLookup.json'
    if not lookup_path.is_file():
        lookup_path = root / 'reports/dose-calibration/DoseLookup.json'
    if lookup_path.exists():
        lookup = load(lookup_path)
        sources = [lookup['calibration'], *lookup['treatment_sources'].values()]
        for target in manifest['targets']:
            selected = selected_pose_paths(Path(target['target_root']), target['coarse_config'])
            best = load(selected['best_pose'])
            center = selected['result'] / f"Center_{int(best['center_id']):05d}"
            benchmark = load(center / 'Benchmark.json')
            indices = np.flatnonzero(np.isclose(benchmark['orientations_degrees'],
                                                best['orientation_degrees'], atol=1e-8, rtol=0))
            matches = [source for source in sources if source['network'] == target['network']]
            if len(matches) != 1 or len(indices) != 1:
                raise ValueError('Cannot uniquely match dosing to the prescribed pose')
            if (Path(matches[0]['reference_field']).resolve() != (center / 'magnE.dtseries.nii').resolve()
                    or matches[0]['reference_map_index_zero_based'] != int(indices[0])):
                raise ValueError('Dosing uses a different field from the prescribed pose')
            result['targets'][target['display_name']]['dosing_reference_matches_selected_pose'] = True
        ceiling = lookup['policy']['maximum_treatment_rmt_ratio']
        checked = 0
        for entry in lookup['entries']:
            if entry['rmt_mso'] <= 0:
                raise ValueError('Non-positive rMT in dose lookup')
            for row in entry['treatments'].values():
                if row is None:
                    continue
                if ceiling is not None and row['treatment_mso'] > entry['rmt_mso'] * ceiling + 1e-8:
                    raise ValueError('Dose exceeds configured rMT ceiling')
                if not np.isclose(row['treatment_mso'], round(row['treatment_mso'], 1), atol=1e-8):
                    raise ValueError('Displayed dose differs from evaluated dose')
                checked += 1
        result['dosing'] = {'selected_entries_checked': checked,
                            'maximum_treatment_rmt_ratio': ceiling,
                            'lookup_sha256': digest(lookup_path)}
    variant_index = root / 'reports/PoseVariants.json'
    if variant_index.is_file():
        import yaml
        from validate_pose_variants import validate_variants
        config = yaml.safe_load(Path(manifest['configuration']).read_text())
        result['pose_variants'] = validate_variants(manifest, load(variant_index), config)
    from publication_paths import existing_operator_export
    package = existing_operator_export(root, manifest['subject'])
    if (package / 'ExportManifest.json').is_file():
        from publish_operator_export import validate_package
        result['operator_export'] = validate_package(package)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    output = args.root / 'validation/CompletedRunValidation.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = validate(args.root)
    except Exception as error:
        output.write_text(json.dumps({'status': 'failed', 'error': str(error)}, indent=2) + '\n')
        raise
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
