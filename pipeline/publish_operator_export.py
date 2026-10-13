#!/usr/bin/env python3
"""Publish a movable MRI/trajectory/pose-matched dosing package without simulation."""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from publication_paths import dose_source, navigation_source, operator_export


PRESCRIPTION_POLICY = 'configured-selected-pose-v1'


def validate_pose_identity(best, navigation_pose, label):
    """Match simulated coordinates before any magnitude-equivalent handle flip."""
    try:
        expected = [float(best[key]) for key in ('x', 'y', 'z')]
        actual = [float(value) for value in navigation_pose['scalp_center_xyz']]
        expected.append(float(best['orientation_degrees']))
        actual.append(float(navigation_pose['simulated_orientation_degrees']))
        vertices = [float(best['scalp_vertex']), float(navigation_pose['scalp_vertex_one_based'])]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'{label}: incomplete pose identity') from exc
    if (len(actual) != 4 or not all(math.isfinite(value) for value in expected + actual + vertices)
            or any(not value.is_integer() or value < 1 for value in vertices)
            or vertices[0] != vertices[1]
            or any(not math.isclose(a, b, rel_tol=0, abs_tol=1e-6)
                   for a, b in zip(expected, actual))):
        raise ValueError(f'{label}: pose does not match the prescribed navigation trajectory')


def validate_coil_identity(calibration, navigation, label):
    try:
        identities = [(calibration['coil_hardware_name'], navigation['coil_hardware_name']),
                      (calibration['id'], navigation['coil_calibration_id'])]
    except (KeyError, TypeError) as exc:
        raise ValueError(f'{label}: incomplete coil calibration identity') from exc
    if any(not isinstance(expected, str) or not expected or expected != actual
           for expected, actual in identities):
        raise ValueError(f'{label}: coil calibration does not match the prescribed navigation trajectory')


def validate_preview_pose(preview, navigation_pose):
    for preview_key, navigation_key in (
            ('simulated_pose_matrix', 'simulated_matsimnibs'),
            ('prescribed_pose_matrix', 'prescribed_matsimnibs')):
        try:
            expected = preview['provenance'][preview_key]
            actual = navigation_pose[navigation_key]
            if (len(expected) != 4 or len(actual) != 4
                    or any(len(row) != 4 for row in list(expected) + list(actual))):
                raise ValueError('Expected 4x4 matrices')
            pairs = [(float(a), float(b)) for expected_row, actual_row in zip(expected, actual)
                     for a, b in zip(expected_row, actual_row)]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Treatment preview has incomplete pose matrices') from exc
        if any(not math.isfinite(a) or not math.isfinite(b)
               or not math.isclose(a, b, rel_tol=0, abs_tol=1e-6) for a, b in pairs):
            raise ValueError('Treatment preview pose does not match the prescribed navigation trajectory')


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def local_name(value):
    value = str(value)
    if not value or value in ('.', '..') or '/' in value or '\\' in value:
        raise ValueError(f'Unsafe export component: {value}')
    return value


def relative(path, page):
    return html.escape(Path(os.path.relpath(path, page.parent)).as_posix(), quote=True)


def target_label(network, display):
    if network == 'CinguloOpercular':
        return f'{display} (AMN/CO)'
    if network == 'Salience':
        return f'{display} (SAL)'
    return display


def page(path, title, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>*{{box-sizing:border-box;letter-spacing:0}}body{{margin:0;background:#f4f6f8;color:#19222c;font:16px/1.5 Arial,sans-serif}}
main{{max-width:1250px;margin:auto;padding:28px}}h1{{font-size:28px;line-height:1.2}}h2{{font-size:21px}}a{{color:#174e6b;text-underline-offset:3px}}section{{padding:22px 0;border-top:1px solid #cbd2d8}}img{{width:100%;height:auto;display:block}}
.notice{{background:#fff;padding:14px;border-left:4px solid #aa283c}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:28px}}.links{{display:flex;flex-wrap:wrap;gap:16px}}.small{{font-size:13px;color:#4e5d66}}code{{font:13px Arial;overflow-wrap:anywhere}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #cbd2d8;overflow-wrap:anywhere}}th{{background:#e7ecef}}label{{display:block;margin:16px 0 5px;font-weight:bold}}select{{max-width:100%;min-width:250px;font:16px Arial;padding:10px}}.open{{display:inline-block;background:#19222c;color:white;padding:12px;margin:20px 0}}[hidden]{{display:none!important}}
@media(max-width:700px){{main{{padding:18px}}h1{{font-size:24px}}.grid{{grid-template-columns:1fr}}table{{font-size:13px}}th,td{{padding:8px}}}}
</style></head><body><main><h1>{html.escape(title)}</h1>{body}</main></body></html>''')


class Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('href', 'src', 'action', 'poster') and value:
                self.urls.append(value)


def is_relative_to(path, root):
    """Path.is_relative_to equivalent for the supported Python 3.7 driver."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def inside(root, relative_path):
    path = (root / relative_path).resolve()
    if not is_relative_to(path, root.resolve()):
        raise ValueError(f'Package path escapes export: {relative_path}')
    return path


def validate_package(root):
    root = Path(root).resolve()
    manifest = read(root / 'ExportManifest.json')
    single_prescription = manifest.get('schema_version') == 'tans-operator-export-v2'
    if single_prescription:
        if manifest.get('prescription_policy') != PRESCRIPTION_POLICY:
            raise ValueError('Missing configured prescription policy')
        networks = [target['network'] for target in manifest['targets']]
        if len(set(networks)) != len(networks):
            raise ValueError('Duplicate prescribed targets')
    if manifest['clinical_clearance'] is not False:
        raise ValueError('Package cannot assert clinical clearance')
    checked_links = 0
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError(f'Portable export contains a symlink: {path}')
        if path.suffix.lower() in ('.mp4', '.gif', '.msh', '.hdf5'):
            raise ValueError(f'Unexpected large/research artifact: {path}')
        if path.is_file() and path.name != 'ExportManifest.json':
            key = path.relative_to(root).as_posix()
            if key not in manifest['files'] or sha(path) != manifest['files'][key]['sha256']:
                raise ValueError(f'Export integrity mismatch: {key}')
        if path.suffix.lower() != '.html':
            continue
        parser = Resources()
        parser.feed(path.read_text())
        for url in parser.urls:
            parts = urlsplit(url)
            if parts.scheme or parts.netloc or parts.path.startswith('/'):
                raise ValueError(f'Nonportable URL in {path}: {url}')
            target = (path.parent / unquote(parts.path)).resolve() if parts.path else path
            if not is_relative_to(target, root) or not target.is_file():
                raise ValueError(f'Broken or external package link in {path}: {url}')
            checked_links += 1
    for name, metadata in manifest['files'].items():
        path = inside(root, name)
        if not path.is_file() or sha(path) != metadata['sha256']:
            raise ValueError(f'Missing/changed package artifact: {name}')
    prescribed_navigation = {}
    for target in manifest['targets']:
        if single_prescription:
            enabled = target['robustness_enabled']
            if not isinstance(enabled, bool):
                raise ValueError('Invalid recorded robustness setting')
            expected_variant = 'robust' if enabled else 'fine'
            if (target['prescribed_variant'] != expected_variant or
                    set(target['variants']) != {expected_variant}):
                raise ValueError('Export must contain only the configured prescribed pose')
            trajectories = list((root / target['name']).rglob('*_OptimalTrajectory_Brainsight.txt'))
            if len(trajectories) != 1:
                raise ValueError('Expected exactly one technician trajectory per target')
        for variant, item in target['variants'].items():
            for artifact in ('trajectory', 'preview'):
                if sha(inside(root, item[artifact])) != item[f'source_{artifact}_sha256']:
                    raise ValueError(f'Published {artifact} differs from original source')
            nav_path = inside(root, item['metadata'])
            nav = read(nav_path)
            if nav['subject'] != manifest['subject'] or nav['pose_variant'] != variant:
                raise ValueError('Packaged pose metadata has a different identity')
            if single_prescription and nav['selection_mode'] != ('robust' if variant == 'robust' else 'nominal_fine'):
                raise ValueError('Packaged navigation selection mode disagrees with prescription')
            if single_prescription:
                prescribed_navigation[target['network']] = nav
            for name, metadata in nav['files'].items():
                path = inside(root, nav_path.parent.relative_to(root) / name)
                if not path.is_file() or sha(path) != metadata['sha256']:
                    raise ValueError('Packaged pose metadata has a broken file reference')
    if manifest['dose_pairings']:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'search'))
        from dose_report_defaults import validate_html, validate_lookup
    for dose in manifest['dose_pairings']:
        path = inside(root, dose['lookup'])
        lookup = read(path)
        validate_lookup(lookup)
        if lookup['subject'] != manifest['subject'] or lookup['treatment_targets'] != [dose['target_label']]:
            raise ValueError('Dose lookup belongs to another subject/target')
        expected = 'nominal_fine' if dose['motor_variant'] == 'fine' else 'robust'
        if lookup['calibration']['selection_mode'] != expected:
            raise ValueError('Motor-reference variant mismatch')
        expected = 'nominal_fine' if dose['treatment_variant'] == 'fine' else 'robust'
        source = lookup['treatment_sources'][dose['target_label']]
        if source['selection_mode'] != expected or source['network'] != dose['network']:
            raise ValueError('Treatment variant mismatch')
        if single_prescription:
            if 'Somatomotor_Hand' not in prescribed_navigation:
                raise ValueError('Dose lookup has no prescribed motor navigation reference')
            motor_nav = prescribed_navigation['Somatomotor_Hand']
            treatment_nav = prescribed_navigation[dose['network']]
            validate_pose_identity(lookup['calibration']['selected_pose'], motor_nav['selected_pose'],
                                   'Motor-reference dosing')
            validate_coil_identity(lookup['calibration'], motor_nav, 'Motor-reference dosing')
            validate_pose_identity(source['selected_pose'], treatment_nav['selected_pose'],
                                   'Treatment dosing')
            validate_coil_identity(source['calibration'], treatment_nav, 'Treatment dosing')
            validate_preview_pose(lookup['hotspot_previews'][dose['target_label']],
                                  treatment_nav['selected_pose'])
        for entry in lookup['entries']:
            if set(entry['treatments']) != {dose['target_label']}:
                raise ValueError('Other targets leaked into target-specific lookup')
            row = entry['treatments'][dose['target_label']]
            ceiling = entry['maximum_treatment_mso']
            if row and ceiling is not None and row['treatment_mso'] > ceiling + 1e-8:
                raise ValueError('Dose above unchanged safeguard')
        document = inside(root, dose['html']).read_text()
        validate_html(document, lookup)
    if single_prescription:
        calibration = next((t for t in manifest['targets'] if t['network'] == 'Somatomotor_Hand'), None)
        treatment_networks = {t['network'] for t in manifest['targets'] if t != calibration}
        expected_networks = treatment_networks if calibration else set()
        pairs = manifest['dose_pairings']
        if {p['network'] for p in pairs} != expected_networks or len(pairs) != len(expected_networks):
            raise ValueError('Expected exactly one prescribed dose pairing per treatment target')
        for pair in pairs:
            target = next(t for t in manifest['targets'] if t['network'] == pair['network'])
            if (pair['motor_variant'] != calibration['prescribed_variant'] or
                    pair['treatment_variant'] != target['prescribed_variant']):
                raise ValueError('Dosing does not match the exported prescribed trajectories')
            lookup = read(inside(root, pair['lookup']))
            if lookup.get('operator_prescription') != {
                    'policy': PRESCRIPTION_POLICY, 'calibration_variant': pair['motor_variant'],
                    'treatment_variant': pair['treatment_variant']}:
                raise ValueError('Dose page lacks its prescribed-pose identity')
    return {'status': 'passed', 'clinical_clearance': False,
            'files_checked': len(manifest['files']), 'local_links_checked': checked_links,
            'pose_variants': sum(len(target['variants']) for target in manifest['targets']),
            'target_specific_dose_pairings': len(manifest['dose_pairings']),
            'bytes': sum(item['bytes'] for item in manifest['files'].values())}


def filter_lookup(lookup, network):
    labels = [name for name, source in lookup['treatment_sources'].items() if source['network'] == network]
    if len(labels) != 1:
        raise ValueError(f'Cannot uniquely identify target {network} in dose source')
    label = labels[0]
    output = copy.deepcopy(lookup)
    output['treatment_targets'] = [label]
    output['treatment_sources'] = {label: output['treatment_sources'][label]}
    output['dose_response_curves'] = {label: output['dose_response_curves'][label]}
    if 'hotspot_previews' in output:
        output['hotspot_previews'] = {label: output['hotspot_previews'][label]}
    for entry in output['entries']:
        entry['treatments'] = {label: entry['treatments'][label]}
        if 'treatment_landmarks' in entry:
            entry['treatment_landmarks'] = {label: entry['treatment_landmarks'][label]}
    return output, label


def configured_prescriptions(manifest, variants):
    from selected_pose import selected_pose_paths

    records = {item['network']: item for item in variants['targets']}
    networks = {item['network'] for item in manifest['targets']}
    if (len(records) != len(variants['targets']) or len(networks) != len(manifest['targets']) or
            set(records) != networks):
        raise ValueError('Prescription sources must uniquely cover all run targets')
    selected = {}
    for target in manifest['targets']:
        config = read(target['coarse_config'])
        if config['subject'] != manifest['subject']:
            raise ValueError('Prescription configuration belongs to another subject')
        if config['network'] != target['network']:
            raise ValueError('Prescription configuration belongs to another network')
        source = selected_pose_paths(target['target_root'], target['coarse_config'])
        record = records[target['network']]
        variant = source['variant']
        if variant not in record['variants']:
            raise ValueError(f"Missing configured {variant} prescription for {target['network']}; no fallback")
        if Path(record['variants'][variant]['best_pose']).resolve() != source['best_pose'].resolve():
            raise ValueError('Research pose source disagrees with configured prescription')
        selected[target['network']] = source
    return selected


def build_package(stage, manifest, variants):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'search'))
    from generate_calibration_dose_report import html_report, write_tsv
    from dose_report_defaults import ensure_lookup
    subject = local_name(manifest['subject'])
    items = {item['network']: item for item in manifest['targets']}
    selected = configured_prescriptions(manifest, variants)
    motor_selection = selected.get('Somatomotor_Hand')
    selected_pairs = {}
    for network, source in selected.items():
        if network == 'Somatomotor_Hand' or motor_selection is None:
            continue
        matches = [pair for pair in variants['dose_pairings']
                   if network in pair['treatment_networks'] and
                   pair['treatment_variant'] == source['variant'] and
                   pair['calibration_variant'] == motor_selection['variant']]
        if len(matches) != 1:
            raise ValueError(f'Expected one matching prescribed dose pairing for {network}, found {len(matches)}')
        selected_pairs[network] = matches[0]
    output = {'schema_version': 'tans-operator-export-v2', 'subject': subject,
              'network_input_provenance': manifest.get('network_input_provenance', {}),
              'prescription_policy': PRESCRIPTION_POLICY,
              'clinical_clearance': False, 'new_simulations': 0, 'targets': [], 'dose_pairings': [],
              'anatomical_image': f'{subject}_T1w.nii.gz', 'files': {}}
    t1_hash = None
    dose_lookups = {}
    for target in variants['targets']:
        name = local_name(Path(items[target['network']]['target_root']).name)
        if name in ('index.html', 'README.txt', 'ExportManifest.json', output['anatomical_image']):
            raise ValueError('Target folder conflicts with shared export artifact')
        label = target_label(target['network'], target['display_name'])
        prescription = selected[target['network']]
        prescribed_variant = prescription['variant']
        record = {'network': target['network'], 'name': name, 'label': label, 'variants': {},
                  'prescribed_variant': prescribed_variant,
                  'robustness_enabled': prescription['robustness_enabled'],
                  'source_configuration_sha256': sha(items[target['network']]['coarse_config']),
                  'source_best_pose_sha256': sha(prescription['best_pose'])}
        for variant, item in [(prescribed_variant, target['variants'][prescribed_variant])]:
            if variant not in ('fine', 'robust'):
                raise ValueError(f'Unsupported variant: {variant}')
            source = Path(item['neuronavigation'])
            nav = read(source / 'NeuronavigationManifest.json')
            if nav['subject'] != subject or nav['pose_variant'] != variant:
                raise ValueError('Wrong subject/variant export source')
            if (nav['selection_mode'] != prescription['mode'] or
                    Path(nav['sources']['selected_pose']).resolve() != prescription['best_pose'].resolve()):
                raise ValueError('Navigation source does not match the configured prescription')
            validate_pose_identity(read(prescription['best_pose']), nav['selected_pose'],
                                   'Configured prescription')
            for filename, meta in nav['files'].items():
                local_name(filename)
                if sha(source / filename) != meta['sha256']:
                    raise ValueError(f'Invalid source export fingerprint: {source/filename}')
            anatomy = source / output['anatomical_image']
            digest = sha(anatomy)
            if t1_hash is not None and digest != t1_hash:
                raise ValueError('Target exports reference different MRI images')
            if t1_hash is None:
                shutil.copy2(anatomy, stage / output['anatomical_image'])
                t1_hash = digest
            destination = stage / name
            metadata = destination / 'metadata'
            metadata.mkdir(parents=True)
            trajectory, = source.glob('*_OptimalTrajectory_Brainsight.txt')
            preview, = source.glob('*_OptimalPoseOverview.png')
            if f'_{variant.capitalize()}_' not in trajectory.name:
                raise ValueError('Expected explicitly named variant trajectory')
            trajectory_name = f'{subject}_{name}_Prescribed_OptimalTrajectory_Brainsight.txt'
            preview_name = f'{subject}_{name}_Prescribed_OptimalPoseOverview.png'
            shutil.copy2(trajectory, destination / trajectory_name)
            shutil.copy2(preview, destination / preview_name)
            for technical in (*source.glob('*_OptimalPose.json'), *source.glob('*_matsimnibs.txt')):
                shutil.copy2(technical, metadata / technical.name)
            copied_nav = copy.deepcopy(nav)
            copied_nav['source_manifest_sha256'] = sha(source / 'NeuronavigationManifest.json')
            copied_nav['files'] = {}
            copied = [destination / trajectory_name, destination / preview_name,
                      *(p for p in metadata.iterdir() if p.is_file()), stage / output['anatomical_image']]
            for path in copied:
                copied_nav['files'][os.path.relpath(path, metadata)] = {'sha256': sha(path), 'bytes': path.stat().st_size}
            write(metadata / 'NeuronavigationManifest.json', copied_nav)
            record['variants'][variant] = {
                'trajectory': (destination / trajectory_name).relative_to(stage).as_posix(),
                'preview': (destination / preview_name).relative_to(stage).as_posix(),
                'metadata': (metadata / 'NeuronavigationManifest.json').relative_to(stage).as_posix(),
                'hardware_coil': nav['coil_hardware_name'],
                'source_trajectory_sha256': sha(trajectory), 'source_preview_sha256': sha(preview)}
        for pair in ([selected_pairs[target['network']]] if target['network'] in selected_pairs else []):
            variant, motor = pair['treatment_variant'], pair['calibration_variant']
            if variant not in record['variants'] or motor not in ('fine', 'robust'):
                raise ValueError('Dose pairing has no matching exported treatment pose')
            source_lookup = Path(pair['report']) / 'DoseLookup.json'
            if source_lookup not in dose_lookups:
                dose_lookups[source_lookup] = ensure_lookup(read(source_lookup))
            lookup, dose_label = filter_lookup(dose_lookups[source_lookup], target['network'])
            lookup['operator_prescription'] = {
                'policy': PRESCRIPTION_POLICY, 'calibration_variant': motor,
                'treatment_variant': variant}
            dose_dir = stage / name / 'dose-calibration'
            dose_dir.mkdir(parents=True)
            write(dose_dir / 'DoseLookup.json', lookup)
            write_tsv(dose_dir / 'DoseLookup.tsv', lookup)
            html_report(dose_dir / 'DoseCalibrationReport.html', lookup)
            output['dose_pairings'].append({
                'network': target['network'], 'target_name': name, 'target_label': dose_label,
                'motor_variant': motor, 'treatment_variant': variant,
                'html': (dose_dir / 'DoseCalibrationReport.html').relative_to(stage).as_posix(),
                'lookup': (dose_dir / 'DoseLookup.json').relative_to(stage).as_posix(),
                'source_lookup_sha256': sha(Path(pair['report']) / 'DoseLookup.json')})
        output['targets'].append(record)
    if not output['targets']:
        raise ValueError('Cannot publish an empty target package')
    calibration = next((t for t in output['targets'] if t['network'] == 'Somatomotor_Hand'), None)
    for pair in output['dose_pairings']:
        if calibration is None or pair['motor_variant'] not in calibration['variants']:
            raise ValueError('Dose reference lacks an exported motor trajectory')
    rows = []
    for target in output['targets']:
        base = stage / target['name']
        title = f'{subject} | {target["label"]}'
        target_page = base / 'index.html'
        pairs = [p for p in output['dose_pairings'] if p['network'] == target['network']]
        variant = target['prescribed_variant']
        item = target['variants'][variant]
        doses = ''
        if pairs:
            dose_page = base / 'dose-calibration/index.html'
            dose_url = relative(stage / pairs[0]['html'], dose_page)
            page(dose_page, title + ' | Dose calibration',
                 f'<p class="links"><a href="../index.html">Prescribed target pose</a>'
                 f'<a href="../../{calibration["name"]}/index.html">SM-Hand motor reference</a></p>'
                 '<p class="notice">This lookup is matched to the prescribed SM-Hand and treatment trajectories '
                 'in this package. Use only an rMT measured at that SM-Hand pose with its specified coil. '
                 'A different motor pose or coil requires reassessment.</p>'
                 f'<p><a class="open" href="{dose_url}">Open treatment dose calculator</a></p>'
                 '<p>Registration and protocol-specific dosing require independent qualified review.</p>')
            doses = f'<a href="{relative(stage / pairs[0]["html"], target_page)}">Open treatment dose calculator</a>'
        role_note = ('Motor calibration reference. Measure rMT at this prescribed SM-Hand pose with the specified coil.'
                     if target['network'] == 'Somatomotor_Hand' else
                     ('Dosing is matched to this treatment pose and the prescribed SM-Hand motor reference.'
                      if pairs else 'Pose-only export: no matched SM-Hand calibration is available.'))
        body = (
            '<p class="links"><a href="../index.html">Export contents</a>'
            f'<a href="../{output["anatomical_image"]}">Subject MRI</a>{doses}</p>'
            f'<p class="notice">{role_note} Confirm registration, physical coil, imported orientation '
            'and protocol before use.</p>'
            f'<p><strong>Coil:</strong> {html.escape(item["hardware_coil"])}</p>'
            f'<p><a href="{relative(stage / item["trajectory"], target_page)}">Download prescribed Brainsight trajectory</a>'
            f'<br><code>{Path(item["trajectory"]).name}</code></p>'
            f'<img src="{relative(stage / item["preview"], target_page)}" alt="Prescribed pose overview">'
            '<p class="small"><a href="metadata/NeuronavigationManifest.json">Technical provenance</a>. '
            'The matrix text file in metadata is not the Brainsight trajectory to import.</p>')
        page(target_page, title, body)
        role = 'Motor reference' if target['network'] == 'Somatomotor_Hand' else 'Treatment target'
        dosing_link = f'<a href="{pairs[0]["html"]}">Dose calculator</a>' if pairs else ('Reference for treatment dosing' if calibration == target else 'Not available')
        rows.append(f'<tr><td><a href="{target["name"]}/index.html">{html.escape(target["label"])}</a></td>'
                    f'<td>{role}</td><td><a href="{item["trajectory"]}">Prescribed trajectory</a></td><td>{dosing_link}</td></tr>')
    page(stage / 'index.html', f'{subject} | Neuronavigation and dosing',
         f'<p class="notice">Model-based research outputs; not clinical clearance. Confirm subject identity, '
         'prescribed pose, physical coil, registration and measured rMT before use.</p>'
         '<p>One prescribed trajectory per network. Transfer this <strong>entire export folder</strong> '
         'with its shared MRI, matching pose previews and dose calculators.</p>'
         f'<p><a href="{output["anatomical_image"]}">Subject MRI: {output["anatomical_image"]}</a></p>'
         '<section><table><tr><th>Target</th><th>Role</th><th>Trajectory</th><th>Dosing</th></tr>' + ''.join(rows) +
         '</table></section>' +
         ('<p>Measure rMT at the prescribed SM-Hand trajectory first. Then open the treatment target\'s '
          'dose calculator and enter that measured rMT. The motor and treatment poses are already matched.</p>'
          if calibration else '<p>No SM-Hand reference was provided. These are pose-only exports.</p>') +
         '<p>Do not substitute coils or MRI images. A green dose safeguard is not safety clearance. '
         'These HTML pages do not control the stimulator.</p><p class="small">Keep folder contents together. '
         'Technical metadata records computational provenance; operator pages work without the original '
         'TANS directory. Restricted subject-specific information.</p>'
         '<p><a href="README.txt">Readme</a> | <a href="ExportManifest.json">Package fingerprint manifest</a></p>')
    (stage / 'README.txt').write_text(
        f'{subject} | TANS operator export\n\nCopy the ENTIRE export folder, then open index.html.\n'
        'The single subject-named T1w at the root is shared by every target.\n'
        'Each target has ONE prescribed trajectory and its matching preview, with no variant choice.\n'
        'Robustness enabled: robust prescription. Robustness disabled: nominal fine winner.\n'
        'A missing required robustness result blocks publication; it never falls back silently.\n'
        'The source variant is recorded in metadata. Fine/robust alternatives stay in research reports.\n'
        'Treatment targets have dose-calibration/DoseCalibrationReport.html, already matched to\n'
        'the prescribed SM-Hand trajectory. Measure rMT there, with the specified motor coil.\n'
        'Without SM-Hand these are pose-only exports; no dose calculator is supplied.\n'
        'BioSelect AMN/CO is named CinguloOpercular; SAL is named Salience.\n'
        'HTML works offline. Brainsight and the correct physical coils are required for navigation.\n'
        'This is model-based research output, not clinical clearance or a signed treatment order.\n'
        'The protocol supervisor must verify imports, registration, pose, coil and dosing.\n'
        'Preserve the MRI and trajectory files unchanged.\n')
    for path in stage.rglob('*'):
        if path.is_file():
            output['files'][path.relative_to(stage).as_posix()] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
    write(stage / 'ExportManifest.json', output)
    return output


def migrate_sources(root):
    """Move legacy presentation inputs to work storage, preserving all data."""
    manifest = read(root / 'MultinetworkRunManifest.json')
    moves = [(root / 'reports/dose-calibration', dose_source(root), 'DoseLookup.json')]
    moves += [(Path(t['target_root']) / 'export/neuronavigation', navigation_source(t['target_root']), 'NeuronavigationManifest.json') for t in manifest['targets']]
    replacements = {}
    for old, new, sentinel in moves:
        if (old / sentinel).is_file():
            if new.exists():
                raise FileExistsError(f'Cannot migrate over existing source storage: {new}')
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
        if new.is_dir():
            replacements[str(old)] = str(new)
    def rewrite(value):
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, str):
            for old, new in replacements.items():
                if value == old or value.startswith(old + '/'):
                    return new + value[len(old):]
        return value
    metadata = [root / 'ReportIndex.json', root / 'reports/PoseVariants.json']
    metadata += [Path(t['target_root']) / 'reports/optimal-pose/PoseComparison.json' for t in manifest['targets']]
    for path in metadata:
        if path.is_file():
            write(path, rewrite(read(path)))


def research_links(root, package):
    manifest = read(root / 'MultinetworkRunManifest.json')
    index_path = root / 'ReportIndex.json'
    report_index = read(index_path) if index_path.is_file() else None
    if report_index:
        report_index['operator_export'] = str(package)
        report_index['dose_calibration'] = str(package) if read(package/'ExportManifest.json')['dose_pairings'] else None
        for item in report_index['targets']:
            target = next(t for t in manifest['targets'] if t['network'] == item['network'])
            item['operator_export'] = str(package / Path(target['target_root']).name)
        write(index_path, report_index)
    for target in manifest['targets']:
        target_root = Path(target['target_root'])
        exported = package / target_root.name
        # Keep a small compatibility entry point, not duplicate MRI/trajectory files.
        legacy = target_root / 'export/neuronavigation/index.html'
        page(legacy, f'{manifest["subject"]} | Export location', f'<p>Technician files live in shared/{html.escape(package.name)}.</p><p><a href="{relative(exported/"index.html",legacy)}">Open this target\'s portable export</a></p><p>Distribute the whole {html.escape(package.name)} folder so the MRI and dose pages travel together.</p>')
        shortcut = target_root / 'reports/dose-calibration'
        if shortcut.is_symlink():
            shortcut.unlink()
        if (exported / 'dose-calibration/index.html').is_file():
            page(shortcut / 'index.html', f'{target["display_name"]} | Dosing export', f'<p><a href="{relative(exported/"dose-calibration/index.html",shortcut/"index.html")}">Open target-specific, pose-matched dosing</a></p>')
    legacy = root / 'reports/dose-calibration/index.html'
    page(legacy, f'{manifest["subject"]} | Dosing location', f'<p>Dose lookups live with their treatment target in the portable export.</p><p><a href="{relative(package/"index.html",legacy)}">Open {html.escape(package.name)} and select a treatment target</a></p>')
    variants = read(root / 'reports/PoseVariants.json')
    from generate_pose_variants import write_comparison
    for target in variants['targets']:
        item = next(t for t in manifest['targets'] if t['network'] == target['network'])
        write_comparison(Path(item['target_root']), target)
    if report_index:
        from generate_master_report import build_document
        destination = root / 'reports/index.html'
        destination.write_text(build_document(manifest, report_index, destination))


def publish(root, migrate=True):
    root = Path(root).resolve()
    manifest = read(root / 'MultinetworkRunManifest.json')
    variants = read(root / 'reports/PoseVariants.json')
    if variants['subject'] != manifest['subject'] or variants.get('validation', {}).get('status') != 'passed':
        raise ValueError('Publish only validated, subject-matched pose variants')
    destination = operator_export(root, manifest['subject'])
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.operator-export-building-', dir=destination.parent))
    try:
        build_package(stage, manifest, variants)
        validation = validate_package(stage)
        # Exercise the same closure/integrity checks with a different parent path.
        with tempfile.TemporaryDirectory(prefix='tans-portability-check-') as relocated:
            moved = Path(relocated) / 'export'
            shutil.copytree(stage, moved)
            validation['relocated_copy'] = validate_package(moved)['status']
        for previous in (destination, root / 'shared/export'):
            if previous.exists():
                archive = root / 'shared/work/publishing/previous-exports' / uuid4().hex
                archive.parent.mkdir(parents=True, exist_ok=True)
                previous.rename(archive)
        stage.rename(destination)
        if migrate:
            migrate_sources(root)
        research_links(root, destination)
        write(root / 'validation/OperatorExportValidation.json', validation)
        return {'export': str(destination), 'validation': validation, 'new_simulations': 0}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(validate_package(args.root) if args.validate_only else publish(args.root), indent=2))


if __name__ == '__main__':
    main()
