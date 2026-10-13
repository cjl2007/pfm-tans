#!/usr/bin/env python3
"""Publish explicit fine/robust review, navigation, and matched dosing choices."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'search'))
from selected_pose import robustness_enabled, selected_pose_paths


LABELS = {'fine': 'Fine winner', 'robust': 'Robust prescription'}


def read(path):
    return json.loads(Path(path).read_text())


def link(path, output):
    return html.escape(Path(os.path.relpath(path, output.parent)).as_posix(), quote=True)


def page(path, title, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f6f8;color:#19222c;font:16px Arial,sans-serif}}
main{{max-width:1450px;margin:auto;padding:28px}}h1{{font-size:28px;margin:0 0 20px}}h2{{font-size:21px}}
p{{line-height:1.5}}section{{padding:20px 0;border-top:1px solid #cbd2d8}}
.notice{{padding:14px;border-left:4px solid #a52b20;background:#fff}}
.choices{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}}img{{width:100%;height:auto}}
a{{color:#193d63;text-underline-offset:3px}}.links{{display:flex;gap:16px;flex-wrap:wrap}}
.table{{overflow:auto}}table{{width:100%;border-collapse:collapse}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #cbd2d8}}
th{{background:#e7ebee}}label{{display:block;font-weight:bold;margin:16px 0 6px}}select{{font:16px Arial;padding:10px;max-width:100%;min-width:240px}}
.open{{display:inline-block;background:#19222c;color:#fff;padding:12px;margin-top:20px}}.open[hidden]{{display:none}}
@media(max-width:800px){{.choices{{grid-template-columns:1fr}}main{{padding:18px}}h1{{font-size:24px}}}}
</style></head><body><main><h1>{html.escape(title)}</h1>{body}</main></body></html>''')


def available(target):
    enabled = robustness_enabled(read(target['coarse_config']))
    return ('fine', 'robust') if enabled else ('fine',)


def sensitivity(target, best, variant):
    root = Path(target['target_root'])
    analysis = root / 'search/robustness/analysis'
    summary = read(analysis / 'RobustnessSummary.json')
    with (analysis / 'RobustnessSensitivity.tsv').open() as handle:
        rows = list(csv.DictReader(handle, delimiter='\t'))
    center = int(best['fine_center_id'] if variant == 'robust' else best['center_id'])
    matches = [row for row in rows if int(row['fine_center_id']) == center
               and math.isclose(float(row['fine_orientation_degrees']), float(best['orientation_degrees']))
               and math.isclose(float(row['position_error_mm']), summary['default_position_error_mm'])
               and math.isclose(float(row['orientation_error_degrees']), summary['default_orientation_error_degrees'])]
    if len(matches) != 1:
        raise ValueError(f'Expected one matched sensitivity row: {root.name} {variant}')
    return {key: float(value) for key, value in matches[0].items()}


def comparison_data(target):
    root = Path(target['target_root'])
    variants = {}
    for variant in available(target):
        source = selected_pose_paths(root, target['coarse_config'], variant)
        best = read(source['best_pose'])
        variants[variant] = {
            'label': LABELS[variant], 'best_pose': str(source['best_pose']),
            'fine_center_id': int(best.get('fine_center_id', best['center_id'])),
            'orientation_degrees': float(best['orientation_degrees']),
            'scalp_center_xyz': [float(best[k]) for k in ('x', 'y', 'z')],
            'mean_on_target_score': float(best['mean_on_target_score']),
            'binary_score_at_99_5_percent': float(best['binary_score_at_99_5_percent']),
            'target_to_global_peak_ratio': float(best['target_to_global_peak_ratio']),
            'sensitivity': sensitivity(target, best, variant) if source['robustness_enabled'] else None,
        }
    difference = None
    if 'robust' in variants:
        fine, robust = variants['fine'], variants['robust']
        angle = abs(fine['orientation_degrees'] - robust['orientation_degrees']) % 180
        difference = {
            'scalp_center_euclidean_distance_mm': float(np.linalg.norm(
                np.array(fine['scalp_center_xyz']) - robust['scalp_center_xyz'])),
            'orientation_difference_modulo_180_degrees': min(angle, 180 - angle),
            'nominal_score_change_percentage_points': 100 * (
                robust['mean_on_target_score'] - fine['mean_on_target_score']),
            'lower_tail_change_percentage_points': 100 * (
                robust['sensitivity']['lower_tail_mean_10_percent'] -
                fine['sensitivity']['lower_tail_mean_10_percent']),
        }
    return {'subject': read(target['coarse_config'])['subject'],
            'network': target['network'], 'display_name': target['display_name'],
            'variants': variants, 'robust_minus_fine': difference,
            'interpretation': 'Sampled placement-error sensitivity, not registration accuracy or clinical clearance.'}


def render_target_variants(target, module, priors, visual):
    from generate_reports import compatibility_view, complete, load_json, require_file, run, visual_inputs
    root = Path(target['target_root'])
    report_root = root / 'reports/optimal-pose'
    view = compatibility_view(target, root / 'reports')
    config = load_json(target['coarse_config'], 'coarse configuration')
    inflated, sulc = visual_inputs(config)
    data = comparison_data(target)
    for variant, item in data['variants'].items():
        source = selected_pose_paths(root, target['coarse_config'], variant)
        report = report_root / variant
        expected = ('OptimalPoseOverview.png', 'OptimalPoseThresholdSweep.gif', 'VisualizationSummary.json')
        parent_summary = report_root / 'VisualizationSummary.json'
        if not report.exists() and parent_summary.is_file():
            previous = read(parent_summary)
            # A newly rendered pipeline-selected pose is already the exact variant.
            if (previous.get('pose_label') == LABELS[variant]
                    and Path(previous['selected_pose_source']).resolve() == source['best_pose'].resolve()):
                names = previous['created_outputs']
                if any(Path(name).name != name for name in names) or not complete(report_root, names):
                    raise ValueError('Invalid reusable optimal-pose output list')
                report.mkdir()
                for name in names:
                    (report / name).symlink_to(Path('..') / name)
        if not complete(report, expected):
            if report.exists():
                raise FileExistsError(f'Incomplete pose variant requires review: {report}')
            pose_config = read(source['config'])
            coil = require_file(Path(pose_config['coil']).with_suffix('.stl'), 'coil STL')
            run([sys.executable, str(module / 'render_optimal_pose_qc.py'),
                 '--config', str(source['config']), '--fine-result', str(source['result']),
                 '--best-pose', str(source['best_pose']), '--priors', str(priors),
                 '--coil-stl', str(coil), '--left-inflated', str(inflated),
                 '--left-sulc', str(sulc), '--visualization-config', str(visual),
                 '--pose-label', LABELS[variant], '--output', str(report)],
                f"{target['display_name']} {variant} optimal pose")
        summary = read(report / 'VisualizationSummary.json')
        if Path(summary['selected_pose_source']).resolve() != source['best_pose'].resolve():
            raise ValueError(f'Variant report references wrong pose: {report}')
        from publication_paths import navigation_source
        export = navigation_source(root) / variant
        if not (export / 'NeuronavigationManifest.json').is_file():
            if export.exists():
                raise FileExistsError(f'Incomplete pose variant export: {export}')
            run([config['simnibs_python'], str(module / 'export_neuronavigation_package.py'),
                 '--target-root', str(view), '--pose-variant', variant,
                 '--preview', str(report / 'OptimalPoseOverview.png'), '--output', str(export)],
                f"{target['display_name']} {variant} neuronavigation export")
        export_manifest = read(export / 'NeuronavigationManifest.json')
        item.update({'optimal_pose': str(report), 'neuronavigation': str(export),
                     'prescribed_orientation_degrees': export_manifest['selected_pose']['prescribed_orientation_degrees']})
    write_comparison(root, data)
    return data


def write_comparison(root, data):
    from publication_paths import existing_operator_export

    output = root / 'reports/optimal-pose/index.html'
    package = existing_operator_export(root.parents[1], data['subject']) / root.name
    items = data['variants']
    fields = [
        ('Fine-grid center', lambda v: str(v['fine_center_id'])),
        ('Prescribed handle orientation', lambda v: f"{v['prescribed_orientation_degrees']:g} degrees"),
        ('Nominal mean search score', lambda v: f"{100 * v['mean_on_target_score']:.3f}%"),
        ('Binary on-target at 99.5% hotspot', lambda v: f"{100 * v['binary_score_at_99_5_percent']:.3f}%"),
        ('Target maximum / global maximum', lambda v: f"{100 * v['target_to_global_peak_ratio']:.2f}%"),
    ]
    if 'robust' in items:
        fields.extend([
            ('Mean score with sampled errors', lambda v: f"{100 * v['sensitivity']['perturbation_mean_score']:.3f}%"),
            ('Average of worst 10% of samples', lambda v: f"{100 * v['sensitivity']['lower_tail_mean_10_percent']:.3f}%"),
            ('Worst sampled score', lambda v: f"{100 * v['sensitivity']['worst_case_score']:.3f}%"),
            ('Score standard deviation', lambda v: f"{100 * v['sensitivity']['perturbation_standard_deviation']:.3f} percentage points"),
            ('Error samples', lambda v: str(int(v['sensitivity']['perturbed_pose_samples']))),
        ])
    headers = ''.join(f'<th>{item["label"]}</th>' for item in items.values())
    rows = ''.join('<tr><th>' + label + '</th>' + ''.join(
        f'<td>{formatter(item)}</td>' for item in items.values()) + '</tr>' for label, formatter in fields)
    difference = data['robust_minus_fine']
    note = 'Robustness was disabled. Only the nominal fine winner is available.'
    if difference:
        guard = items['robust']['sensitivity']
        note = (f"Robust versus fine: {difference['scalp_center_euclidean_distance_mm']:.2f} mm center shift "
                f"(straight-line distance), {difference['orientation_difference_modulo_180_degrees']:g}-degree "
                f"orientation change modulo 180. Nominal score change "
                f"{difference['nominal_score_change_percentage_points']:+.3f} percentage points; "
                f"lower-tail change {difference['lower_tail_change_percentage_points']:+.3f} percentage points. "
                f"Sensitivity comparison: sampled displacement up to {guard['position_error_mm']:g} mm "
                f"and orientation error up to {guard['orientation_error_degrees']:g} degrees.")
    panels = []
    for variant, item in items.items():
        report, export = Path(item['optimal_pose']), Path(item['neuronavigation'])
        trajectory = next(export.glob('*_OptimalTrajectory_Brainsight.txt'))
        anatomy = next(export.glob('*_T1w.nii.gz'))
        export_manifest = export / 'NeuronavigationManifest.json'
        panels.append(f'''<section><h2>{item['label']}</h2>
<a href="{link(report / 'OptimalPoseOverview.png', output)}"><img src="{link(report / 'OptimalPoseOverview.png', output)}" alt="{item['label']}"></a>
<p class="links"><a href="{link(report / 'OptimalPoseThresholdSweep.gif', output)}">Threshold sweep</a>
<a href="{link(trajectory, output)}">Research {variant} trajectory</a>
<a href="{link(anatomy, output)}">Native T1w</a>
<a href="{link(export_manifest, output)}">Export manifest</a></p></section>''')
    prescribed_link = (
        f'<p><a href="{link(package / "index.html", output)}">Open the single prescribed technician export</a></p>'
        if (package / 'index.html').is_file() else
        '<p>The single prescribed technician export has not yet been published.</p>')
    page(output, f"{data['subject']} | {data['display_name']} | Fine and robust research comparison",
         '<p class="notice">These are research comparisons, not a technician pose-selection menu. '
         'The technician export contains one prescription per target, chosen by that target\'s robustness configuration. '
         'Using another pose requires a separately reviewed, pose-matched dose lookup. '
         'Sampled placement-error robustness does not '
         'measure registration quality or justify accepting poor registration. Verify registration and '
         'the imported trajectory independently before use. Software validation is not clinical clearance.</p>'
         f'{prescribed_link}'
         f'<p>{note}</p><div class="table"><table><tr><th>Metric</th>{headers}</tr>{rows}</table></div>'
         '<p>Nominal and error scores use the same configured search objective. Percentage-point changes '
         'are absolute differences, not relative percentage improvements. Benefits need not extend beyond '
         'the sampled error range.</p><div class="choices">' + ''.join(panels) + '</div>')
    (output.parent / 'PoseComparison.json').write_text(json.dumps(data, indent=2) + '\n')
    nav_index = root / 'export/neuronavigation/index.html'
    package_index = package / 'index.html'
    shortcut_label = ('Open the prescribed technician export' if package_index.is_file()
                      else 'View research comparison; technician export not yet published')
    page(nav_index, f"{data['display_name']} | Prescribed neuronavigation",
         f'<p><a href="{link(package_index if package_index.is_file() else output, nav_index)}">{shortcut_label}</a></p>'
         f'<p>Distribute the complete {html.escape(package.parent.name)} folder, including its single shared MRI and target-specific dosing. '
         'The matching dose lookup must use the treatment pose and the SM-Hand pose actually used for rMT.</p>')


def render_dose_variants(targets, module, output_root, priors, config):
    from generate_reports import compatibility_view, complete, run
    calibration = next((item for item in targets if item['network'] == 'Somatomotor_Hand'), None)
    treatments = [item for item in targets if item['network'] != 'Somatomotor_Hand']
    if calibration is None or not treatments:
        return []
    from publication_paths import dose_source
    root = dose_source(output_root)
    calibration_view = compatibility_view(calibration, Path(calibration['target_root']) / 'reports')
    pairings = []
    for treatment_variant in ('fine', 'robust'):
        eligible = [item for item in treatments if treatment_variant in available(item)]
        if not eligible:
            continue
        for calibration_variant in available(calibration):
            report = root / treatment_variant / f'sm-hand-{calibration_variant}'
            expected = ('DoseCalibrationReport.html', 'DoseCalibrationOverview.png', 'DoseLookup.json', 'DoseLookup.tsv')
            if not complete(report, expected):
                if report.exists():
                    raise FileExistsError(f'Incomplete matched dosing report: {report}')
                command = [sys.executable, str(module / 'generate_calibration_dose_report.py'),
                           '--calibration-target-root', str(calibration_view),
                           '--calibration-pose-variant', calibration_variant,
                           '--treatment-pose-variant', treatment_variant,
                           '--priors', str(priors), '--output', str(report)]
                maximum = config.get('dosing', {}).get('maximum_treatment_rmt_ratio', 1.20)
                if maximum in (None, ''):
                    command.append('--disable-treatment-rmt-limit')
                else:
                    command.extend(['--maximum-treatment-rmt-ratio', str(maximum)])
                for target in eligible:
                    view = compatibility_view(target, Path(target['target_root']) / 'reports')
                    command.extend(['--treatment-target-root', str(view)])
                run(command, f'Dosing: {calibration_variant} SM-Hand to {treatment_variant} treatment')
            else:
                from dose_report_defaults import ensure_report
                ensure_report(report, priors=priors)
            pairings.append({'calibration_variant': calibration_variant,
                             'treatment_variant': treatment_variant,
                             'treatment_networks': [item['network'] for item in eligible],
                             'report': str(report)})
    write_dose_navigation(root, pairings, config['subject'])
    # Keep the legacy pipeline-selected entry point explicitly identified too.
    legacy_lookup = root / 'DoseLookup.json'
    if legacy_lookup.is_file():
        from dose_report_defaults import ensure_report
        ensure_report(root, priors=priors)
    return pairings


def write_dose_navigation(root, pairings, subject):
    for variant in (None, 'fine', 'robust'):
        choices = [p for p in pairings if variant is None or p['treatment_variant'] == variant]
        if not choices:
            continue
        output = root / (variant or '') / 'index.html'
        rows = ''.join(f'''<tr><td>{p['calibration_variant'].capitalize()} SM-Hand</td>
<td>{p['treatment_variant'].capitalize()} treatment</td><td><a href="{link(Path(p['report']) / 'DoseCalibrationReport.html', output)}">Open matched lookup</a></td></tr>'''
                       for p in choices)
        options = lambda key: ''.join(f'<option value="{v}">{LABELS[v]}</option>'
                                     for v in ('fine', 'robust') if any(p[key] == v for p in choices))
        pairs = json.dumps([{'calibration': p['calibration_variant'], 'treatment': p['treatment_variant'],
                             'url': os.path.relpath(Path(p['report']) / 'DoseCalibrationReport.html', output.parent)}
                            for p in choices])
        page(output, f'{subject} | Pose-matched dose calibration', f'''
<p class="notice">The rMT reference must match the SM-Hand trajectory actually used for the measurement.
Treatment dosing must match the treatment trajectory being used. Do not transfer an rMT measurement
between different SM-Hand poses without reassessment.</p>
<label for="reference">SM-Hand pose used for rMT</label><select id="reference"><option value="">Select measured reference pose</option>{options('calibration_variant')}</select>
<label for="treatment">Treatment pose</label><select id="treatment"><option value="">Select treatment pose</option>{options('treatment_variant')}</select>
<p><a class="open" id="lookup" hidden>Open matched dose lookup</a></p>
<section><h2>Available pairings</h2><div class="table"><table><tr><th>rMT reference</th><th>Treatment</th><th>Report</th></tr>{rows}</table></div></section>
<script>const pairs={pairs};const ref=document.getElementById('reference'),treatment=document.getElementById('treatment'),a=document.getElementById('lookup');
function update(){{const p=pairs.find(p=>p.calibration===ref.value&&p.treatment===treatment.value);a.hidden=!p;if(p){{a.href=p.url;a.textContent='Open '+p.calibration+' SM-Hand / '+p.treatment+' treatment lookup';}}else{{a.removeAttribute('href');}}}}
ref.onchange=treatment.onchange=update;</script>''')


def validate_and_publish(manifest_path, index, config):
    from generate_reports import require_file, run

    interpreter = require_file(config['simulation']['simnibs_python'], 'SimNIBS Python')
    validator = Path(__file__).with_name('validate_pose_variants.py')
    reports = Path(manifest_path).resolve().parent / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    # Brainsight round-trip validation needs SimNIBS; the report driver does not.
    with tempfile.TemporaryDirectory(prefix='.pose-validation-', dir=reports) as temporary:
        stage = Path(temporary)
        staged_index = stage / 'PoseVariants.json'
        validation_file = stage / 'Validation.json'
        staged_index.write_text(json.dumps(index, indent=2) + '\n')
        run([str(interpreter), str(validator), '--manifest', str(manifest_path),
             '--index', str(staged_index), '--output', str(validation_file)],
            'validate pose variants in SimNIBS Python')
        validation = read(validation_file)
        if validation.get('status') != 'passed' or validation.get('clinical_clearance') is not False:
            raise ValueError('Pose variant validation did not pass')
        index['validation'] = validation
        staged_index.write_text(json.dumps(index, indent=2) + '\n')
        os.replace(staged_index, reports / 'PoseVariants.json')
    return index


def generate(manifest_path, priors=None, visual=None):
    from generate_reports import require_file
    started = time.perf_counter()
    manifest_path = Path(manifest_path).resolve()
    manifest = read(manifest_path)
    module = Path(__file__).resolve().parents[1] / 'search'
    config = yaml.safe_load(Path(manifest['configuration']).read_text())
    priors = require_file(priors or manifest.get('network_metadata') or
                          config.get('inputs', {}).get('network_metadata') or
                          module.parent / 'res0urces/Priors.mat', 'network metadata')
    visual = require_file(visual or module / 'default_optimal_pose_visualization.json', 'visualization configuration')
    targets = [render_target_variants(item, module, priors, visual) for item in manifest['targets']]
    doses = render_dose_variants(manifest['targets'], module, manifest_path.parent, priors, config)
    result = {'schema_version': 1, 'subject': manifest['subject'], 'targets': targets,
              'dose_pairings': doses, 'new_simulations': 0, 'report_wall_seconds': time.perf_counter() - started}
    return validate_and_publish(manifest_path, result, config)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest')
    parser.add_argument('--priors')
    parser.add_argument('--visualization-config')
    args = parser.parse_args()
    result = generate(args.manifest, args.priors, args.visualization_config)
    print(json.dumps({'targets': [item['display_name'] for item in result['targets']],
                      'dose_pairings': len(result['dose_pairings']), 'validation': result['validation']}, indent=2))


if __name__ == '__main__':
    main()
