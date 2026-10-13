"""Read-only checks for public documentation, examples, and curated assets.

Checks local link targets, source CLI names, YAML placeholders, known calibration
identities, shell syntax, and curated media integrity. It does not run simulations, contact
GitHub, validate a subject, or certify data-sharing permissions.
"""

from pathlib import Path
import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

import yaml
from PIL import Image


def check(root, source):
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)
    def existing(relative):
        path = root / relative
        return path if path.exists() else source / relative

    documents = [root/'README.md', root/'CHANGELOG.md', *sorted((root/'docs').glob('*.md')),
                 root/'examples/README.md']
    local_links = 0
    for document in documents:
        require(document.is_file(), f'Missing document: {document}')
        if not document.is_file():
            continue
        text = document.read_text()
        require('GALLERY: populated' not in text, f'Unfinished gallery: {document}')
        for match in re.finditer(r'!?\[[^\]]*\]\(([^)]+)\)', text):
            href = match.group(1).strip().strip('<>')
            parts = urlsplit(href)
            if parts.scheme or parts.netloc or not parts.path:
                continue
            target = (document.parent / unquote(parts.path)).resolve()
            try:
                relative = target.relative_to(root)
            except ValueError:
                errors.append(f'Local link escapes repository: {document}: {href}')
                continue
            require(existing(relative).exists(), f'Broken local link: {document}: {href}')
            local_links += 1
        if document.name in {'README.md','INSTALL.md','PFM_MEFMRI.md','STANDALONE.md','CONFIGURATION.md','INPUTS_AND_OUTPUTS.md'}:
            require(not re.search(r'/home/|/media/|/Users/', text), f'Personal installation path in onboarding: {document}')

    driver = ast.parse((source/'pipeline/run_pipeline.py').read_text())
    flags = set()
    for node in ast.walk(driver):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'add_argument':
            for value in node.args:
                if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.startswith('--'):
                    flags.add(value.value)
    reference = (root/'docs/CONFIGURATION.md').read_text()
    mentioned = set(re.findall(r'`(--[a-z][a-z0-9-]*)', reference)) - {'--help'}
    require(mentioned <= flags, f'Configuration reference has unknown driver flags: {sorted(mentioned-flags)}')

    config_path = root/'examples/study_config.yaml'
    config = yaml.safe_load(config_path.read_text())
    template = yaml.safe_load((source/'pipeline/config_template_smhand.yaml').read_text())
    require(set(config)==set(template), 'Example YAML top-level schema differs from working template')
    require(type(config['robustness']['enabled']) is bool, 'YAML robustness must be boolean')
    require(config['targets'][0]['network']=='Somatomotor_Hand', 'SM-Hand template missing')
    require(0 < config['search']['coverage_fraction'] <= 1, 'Invalid example search density')
    require(not re.search(r'/home/|/media/|/Users/', config_path.read_text()), 'Personal path in portable example')
    require(config == template, 'Public YAML templates must have the same settings')
    variables = {name: '/placeholder' for name in ('subject','subject_dir','func_dir','pfm_dir','tans_root','pfm_tans_root')}
    def walk(value):
        if isinstance(value, str):
            try:
                value.format_map(variables)
            except (KeyError, ValueError) as error:
                errors.append(f'Unsupported YAML placeholder: {error}')
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(config)
    registry = json.loads((source/'res0urces/StimulatorCalibration/calibration_registry.json').read_text())
    entries = {entry['id']: entry for entry in registry['entries']}
    for role, key in [('sm_hand', 'sm_hand_calibration_coil'), ('treatment', 'treatment_coil')]:
        identifier = config['dosing'][role+'_calibration']
        placeholder = 'REPLACE_WITH_MATCHED_' + ('SM_HAND' if role == 'sm_hand' else 'TREATMENT') + '_CALIBRATION_ID'
        placeholder_coil = '/path/to/' + ('calibration' if role == 'sm_hand' else 'treatment') + '_coil.ccd'
        if identifier == placeholder:
            require(config['simulation'][key] == placeholder_coil,
                    f'Unconfigured {role} calibration must retain the explicit coil placeholder')
            continue
        require(identifier in entries, f'Unknown example calibration: {identifier}')
        if identifier in entries:
            require(entries[identifier]['simnibs_coil_model_basename']==Path(config['simulation'][key]).name,
                    f'Example {role} calibration/coil mismatch')
    fragment = root/'examples/pfm_mefmri_tans.sh'
    parsed = subprocess.run(['bash','-n',str(fragment)], capture_output=True, text=True)
    require(parsed.returncode==0, 'Shell example syntax: '+parsed.stderr)
    assignments = dict(re.findall(r'^(TANS_[A-Z0-9_]+)=([^\n]*?)(?:\s+#.*)?$', fragment.read_text(), re.M))
    for variable in assignments:
        require(f'`{variable}`' in reference, f'Shell example variable undocumented: {variable}')

    manifest = json.loads((root/'docs/assets/manifest.json').read_text())
    curated = set()
    for item in manifest['assets']:
        name = item['file'];curated.add(name)
        path = root/'docs/assets'/name
        require(path.is_file(), f'Missing gallery asset: {name}')
        if not path.is_file():
            continue
        require(hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256'], f'Asset fingerprint mismatch: {name}')
        suffix = path.suffix.lower()
        limits = {'.png': 3, '.gif': 12, '.pdf': 8, '.mp4': 20}
        require(suffix in limits, f'Unsupported gallery asset type: {name}')
        require(path.stat().st_size < limits.get(suffix, 0)*1024*1024,
                f'Asset exceeds curated media budget: {name}')
        try:
            if suffix in ('.png', '.gif'):
                with Image.open(path) as img:
                    require(img.format==suffix[1:].upper(), f'Incorrect image format: {name}')
                    require(min(img.size)>=(500 if suffix=='.gif' else 600),
                            f'Gallery asset resolution too low: {name}')
                    metadata = json.dumps(img.info, default=str)
                    require(not re.search(r'/home/|/tmp/|BioSelect-|CIR002', metadata),
                            f'Identifying image metadata: {name}')
                    if suffix=='.gif':
                        require(img.n_frames>1, f'Expected animated preview: {name}')
                        for frame in range(img.n_frames):
                            img.seek(frame)
                            img.load()
                    else:
                        img.verify()
            elif suffix=='.pdf':
                data = path.read_bytes()
                require(data.startswith(b'%PDF-') and data.rstrip().endswith(b'%%EOF'),
                        f'Invalid PDF envelope: {name}')
            elif suffix=='.mp4':
                data = path.read_bytes()
                require(data[4:8]==b'ftyp' and b'moov' in data and b'mdat' in data,
                        f'Invalid MP4 envelope: {name}')
        except (OSError, ValueError, EOFError) as error:
            errors.append(f'Invalid gallery media: {name}: {error}')
    require({p.name for p in (root/'docs/assets').iterdir()} == curated | {'manifest.json'},
            'Unreviewed file in docs/assets')
    for relative in ('res0urces/Priors.mat', 'res0urces/SearchSpaces/HCP_MMP_L_LPFC_TMS_envelope_v1.dscalar.nii'):
        require((source/relative).is_file(), f'Missing runtime resource: {relative}')
    result = {'status': 'failed' if errors else 'passed', 'documents_checked': len(documents),
              'local_links_checked': local_links, 'driver_options_checked': len(mentioned),
              'shell_variables_documented': len(assignments), 'gallery_assets_checked': len(curated),
              'errors': errors,
              'scope': 'Static/read-only; not a simulation, subject validation, or public-sharing approval.'}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source-root', type=Path, help='Optional source tree for checking a staged docs overlay')
    args = parser.parse_args()
    result = check(args.root.resolve(), (args.source_root or args.root).resolve())
    print(json.dumps(result, indent=2))
    sys.exit(0 if result['status']=='passed' else 1)


if __name__ == '__main__':
    main()
