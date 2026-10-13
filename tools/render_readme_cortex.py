"""Render a cortical-only documentation figure from an existing completed target.

Uses the production QC loader and plotting functions; does not simulate, change
inputs, export coordinates, or publish data. Direct subject identifiers and the
scalp/face panels are omitted. This is not certification of anonymization: obtain
appropriate permissions before distributing any participant-derived image.
"""

from pathlib import Path
import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--target-root', type=Path, required=True)
    parser.add_argument('--priors', type=Path)
    parser.add_argument('--output', type=Path, required=True,
                        help='New directory for documentation-only figures')
    args = parser.parse_args()
    repo = args.repo.resolve()
    sys.path[:0] = [str(repo / 'search'), str(repo / 'pipeline')]
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    import render_optimal_pose_qc as qc
    from selected_pose import selected_pose_paths
    from generate_reports import visual_inputs

    root = args.target_root.resolve()
    source = selected_pose_paths(root, root / 'CoarseRunConfig.json')
    coarse = json.loads((root / 'CoarseRunConfig.json').read_text())
    config = json.loads(source['config'].read_text())
    inflated, sulc = visual_inputs(coarse)
    output = args.output.resolve()

    class FigureComplete(Exception):
        pass

    def documentation_figure(context, row, weighted, binary, **kwargs):
        percentile, threshold = row[:2]
        hotspot = np.isfinite(context['left_field']) & (context['left_field'] > threshold)
        fig = plt.figure(figsize=(15, 6.6), facecolor='white')
        grid = fig.add_gridspec(1, 3, left=.025, right=.985, bottom=.21,
                              top=.81, wspace=.05)
        field_grid = grid[0, 0].subgridspec(2, 1, height_ratios=[.82, .18], hspace=.08)
        field_ax = fig.add_subplot(field_grid[0, 0], projection='3d')
        qc.draw_field_panel(
            field_ax, context['inflated_points'], context['inflated_review_triangles'],
            context['left_field'], context['left_target'], context['field_norm'],
            'A  Modeled cortical E-field\nwhite outline = retained target',
            crop=context['inflated_closeup_crop'], view=context['review_view'],
            peak_point=context['inflated_peak_point'],
            peak_marker=context['visualization']['peak_marker'],
            zoom=context['visualization']['closeup_zoom'])
        qc.draw_network_review_panel(fig, grid[0, 1], context, row, weighted, binary,
                                     hotspot, 'categorical', False, 'B')
        qc.draw_network_review_panel(fig, grid[0, 2], context, row, weighted, binary,
                                     hotspot, 'weighted_probability', False, 'C')
        scale_ax = fig.add_subplot(field_grid[1, 0])
        scale_ax.axis('off')
        colorbar = fig.colorbar(qc.cm.ScalarMappable(norm=context['field_norm'], cmap=qc.cm.turbo),
                               cax=scale_ax.inset_axes([.06, .42, .88, .15]), orientation='horizontal')
        colorbar.set_label('E-field magnitude at simulation reference intensity (V/m)', fontsize=8)
        colorbar.ax.tick_params(labelsize=7)
        fig.suptitle('Personalized cortical targeting | '+context['display_network'],
                     fontsize=20, weight='bold', x=.035, ha='left', y=.96)
        fig.text(.035,.88,'Example participant · '+('robust prescription' if source['mode']=='robust' else 'nominal fine selection; robustness not evaluated'),
                 fontsize=11, color='#52616b')
        fig.legend(handles=context['legend_handles'], loc='lower center',
                   bbox_to_anchor=(.52,.075), ncol=4, fontsize=8, frameon=False)
        fig.text(.035,.035,'Production rendering functions, cortical-only layout. Relative hotspot threshold; not a measured treatment dose.',
                 fontsize=9, color='#52616b')
        fig.savefig(output/'cortical-targeting.png', dpi=150, facecolor='white',
                    metadata={'Description':'Cortical-only documentation example; no subject identifier or navigation coordinates.'})
        plt.close(fig)
        raise FigureComplete()

    original_builder, original_argv = qc.build_review_figure, sys.argv
    qc.build_review_figure = documentation_figure
    sys.argv = ['render_optimal_pose_qc.py', '--config', str(source['config']),
                '--fine-result', str(source['result']), '--best-pose', str(source['best_pose']),
                '--priors', str(args.priors or repo/'res0urces/Priors.mat'),
                '--coil-stl', str(Path(config['coil']).with_suffix('.stl')),
                '--left-inflated', str(inflated), '--left-sulc', str(sulc),
                '--output', str(output)]
    try:
        qc.main()
    except FigureComplete:
        print(output/'cortical-targeting.png')
    finally:
        qc.build_review_figure, sys.argv = original_builder, original_argv


if __name__ == '__main__':
    main()
