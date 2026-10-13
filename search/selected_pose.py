"""Resolve the prescribed pose without treating missing robustness as a bypass."""

import json
from pathlib import Path

from coil_center_constraint import validate_reused_constraint


def robustness_enabled(config):
    value = config.get('robustness', {}).get('enabled', True)
    if not isinstance(value, bool):
        raise ValueError('robustness.enabled must be a boolean')
    return value


def selected_pose_paths(target_root, coarse_config=None, variant='selected'):
    root = Path(target_root)
    semantic = coarse_config is not None
    coarse_config = Path(coarse_config) if semantic else root / 'CoarseRunConfig.json'
    config = json.loads(coarse_config.read_text())
    enabled = robustness_enabled(config)
    if variant not in ('selected', 'fine', 'robust'):
        raise ValueError(f'Unknown pose variant: {variant}')
    if variant == 'robust' and not enabled:
        raise ValueError('Robust pose requested but robustness was disabled')
    use_robust = enabled if variant == 'selected' else variant == 'robust'
    if semantic:
        if use_robust:
            plan = root / 'search/robustness/plan/RobustRunConfig.json'
            result = root / 'search/robustness/simulation'
            best = root / 'search/robustness/analysis/BestRobustPose.json'
        else:
            plan = root / 'search/fine/plan/FineRunConfig.json'
            result = root / 'search/fine/simulation'
            best = result / 'BestCoarsePose.json'
    elif use_robust:
        plan = root / '05_RobustnessPlan/RobustRunConfig.json'
        result = root / '06_RobustnessSimulation'
        best = root / '07_RobustnessAnalysis/BestRobustPose.json'
    else:
        plan = root / '03_FinePlan/FineRunConfig.json'
        result = root / '04_FineSimulation'
        best = result / 'BestCoarsePose.json'
    for path in (plan, best):
        if not path.is_file():
            raise FileNotFoundError(f'Missing selected-pose source: {path}')
    validate_reused_constraint(config, result, best)
    return {'config': plan, 'result': result, 'best_pose': best,
            'robustness_enabled': enabled,
            'variant': 'robust' if use_robust else 'fine',
            'mode': 'robust' if use_robust else 'nominal_fine',
            'robustness_status': 'evaluated' if enabled else 'not_evaluated_disabled_by_config'}
