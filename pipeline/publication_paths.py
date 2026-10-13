"""Keep reproducible publication inputs out of technician-facing output folders."""
import json
from pathlib import Path


def navigation_source(target_root):
    return Path(target_root) / 'work/publishing/neuronavigation'


def dose_source(run_root):
    return Path(run_root) / 'shared/work/publishing/dose-calibration'


def operator_export(run_root, subject=None):
    """Canonical package name uses manifest identity, including nested runs."""
    root = Path(run_root)
    if subject is None:
        subject = json.loads((root / 'MultinetworkRunManifest.json').read_text())['subject']
    if (not isinstance(subject, str) or not subject or subject in ('.', '..')
            or any(char in subject for char in ('/', '\\', '\x00', '\n', '\r'))):
        raise ValueError(f'Unsafe export subject: {subject!r}')
    return root / 'shared' / f'export_{subject}'


def existing_operator_export(run_root, subject=None):
    """Read older packages until they are republished under the subject name."""
    root = Path(run_root)
    legacy = root / 'shared/export'
    if subject is None and not (root / 'MultinetworkRunManifest.json').is_file():
        return legacy
    named = operator_export(root, subject)
    return named if named.exists() or not legacy.exists() else legacy
