#!/usr/bin/env python3
"""Generate the portable, top-level HTML index for a TANS run."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path

from publication_paths import existing_operator_export


def require_file(value, label):
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def read_json(path, label="JSON file"):
    value = json.loads(require_file(path, label).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def relative_url(path, output):
    return Path(os.path.relpath(Path(path).resolve(), output.parent)).as_posix()


def duration(seconds):
    if seconds is None:
        return "Not recorded"
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.1f} s"
    hours, remainder = divmod(int(round(seconds)), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d} h {minutes:02d} min"
    return f"{minutes:d} min {secs:02d} s"


def bytes_text(value):
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


def target_record(item, report, output, subject=None):
    root = Path(item["target_root"]).resolve()
    runtime_path = root / "RuntimeSummary.json"
    runtime = read_json(runtime_path, "runtime summary") if runtime_path.is_file() else {}
    pose_path = Path(report["selected_pose"])
    pose = read_json(pose_path, "selected pose") if pose_path.is_file() else {}
    artifacts = {
        "Target selection": Path(report["target_selection"]) /
            "TargetSelectionSearchSummary.png",
        "Optimal pose": Path(report["optimal_pose"]) / "OptimalPoseOverview.png",
        "Threshold sweep": Path(report["optimal_pose"]) /
            "OptimalPoseThresholdSweep.gif",
        "Search replay": Path(report["search_replay"]) /
            "SearchOptimizationReplay.mp4",
        "Neuronavigation export": Path(report["neuronavigation"]) /
            "NeuronavigationManifest.json",
    }
    comparison = root / 'reports/optimal-pose/index.html'
    package = Path(report['operator_export']) if report.get('operator_export') else (
        existing_operator_export(root.parents[1], subject) / root.name)
    if comparison.is_file():
        artifacts['Research fine / robust comparison'] = comparison
    for filename in Path(report['neuronavigation']).glob('*_OptimalTrajectory_Brainsight.txt'):
        artifacts['Brainsight trajectory'] = filename
    for filename in Path(report['neuronavigation']).glob('*_T1w.nii.gz'):
        artifacts['Navigation T1w'] = filename
    if (package / 'index.html').is_file():
        artifacts.pop('Brainsight trajectory', None)
        artifacts['Neuronavigation export'] = package / 'index.html'
        prescribed = list(package.glob('*_Prescribed_OptimalTrajectory_Brainsight.txt'))
        if len(prescribed) > 1:
            raise ValueError(f'Multiple prescribed trajectories in {package}')
        if prescribed:
            artifacts['Prescribed Brainsight trajectory'] = prescribed[0]
        for filename in package.parent.glob('*_T1w.nii.gz'):
            artifacts['Navigation T1w'] = filename
        if (package / 'dose-calibration/index.html').is_file():
            artifacts['Prescribed-pose dosing'] = package / 'dose-calibration/index.html'
    export_path = Path(report['neuronavigation']) / 'NeuronavigationManifest.json'
    if (package / 'metadata/NeuronavigationManifest.json').is_file():
        export_path = package / 'metadata/NeuronavigationManifest.json'
    prescription = (read_json(export_path).get('selected_pose', {})
                    if export_path.is_file() else {})
    links = {label: relative_url(path, output)
             for label, path in artifacts.items() if path.is_file()}
    counts = {stage: int(runtime.get(f"{stage}_poses", 0))
              for stage in ("coarse", "fine", "robustness")}
    return {
        "network": item["network"],
        "display_name": item["display_name"],
        "anatomical_zone": item.get("anatomical_zone", ""),
        "map_mode": item.get("map_mode", ""),
        "coverage_fraction": item.get("coverage_fraction"),
        "coil_hardware_name": report.get("coil_hardware_name", ""),
        "links": links,
        "runtime": runtime,
        "pose": pose,
        "prescription": prescription,
        "counts": counts,
        "simulations": sum(counts.values()),
    }


def build_document(manifest, report_index, output):
    indexed = {item["network"]: item for item in report_index["targets"]}
    targets = [target_record(item, indexed[item["network"]], output, manifest['subject'])
               for item in manifest["targets"] if item["network"] in indexed]
    timing_path = Path(manifest["configuration"]).parent.parent / "PipelineTiming.json"
    pipeline_timing = (read_json(timing_path, "pipeline timing")
                       if timing_path.is_file() else {"stages": {}})
    total_simulations = sum(item["simulations"] for item in targets)
    total_search_seconds = sum(
        float(item["runtime"].get("baseline_coarse_plus_fine_wall_seconds", 0)) +
        float(item["runtime"].get("robustness_total_wall_seconds", 0))
        for item in targets)
    dose = report_index.get("dose_calibration")
    dose_html = Path(dose) / "DoseCalibrationReport.html" if dose else None
    if dose and (Path(dose) / 'index.html').is_file():
        dose_html = Path(dose) / 'index.html'

    navigation = "".join(
        f'<a href="#target-{index}">{html.escape(item["display_name"])}</a>'
        for index, item in enumerate(targets, 1))
    navigation += '<a href="#runtime">Runtime</a>'
    export_root = report_index.get('operator_export')
    if export_root and (Path(export_root) / 'index.html').is_file():
        navigation += f'<a href="{html.escape(relative_url(Path(export_root)/"index.html", output))}">Technician export</a>'
    if dose_html and dose_html.is_file():
        navigation += f'<a href="{html.escape(relative_url(dose_html, output))}">Dose calibration</a>'

    target_sections = []
    for index, item in enumerate(targets, 1):
        runtime = item["runtime"]
        pose = item["pose"]
        links = "".join(
            f'<a class="report-link" href="{html.escape(url)}">{html.escape(label)}</a>'
            for label, url in item["links"].items())
        preview_url = item["links"].get("Optimal pose")
        preview = (f'<a href="{html.escape(preview_url)}"><img src="{html.escape(preview_url)}" '
                   f'alt="{html.escape(item["display_name"])} optimal pose"></a>'
                   if preview_url else '<div class="missing">Preview unavailable</div>')
        coverage = (f'{100 * float(item["coverage_fraction"]):.1f}%'
                    if item["coverage_fraction"] is not None else "Not recorded")
        robust = pose.get("robustness", {})
        enabled = runtime.get('robustness_enabled', True)
        prescription_label = 'Final robust prescription' if enabled else 'Final nominal fine prescription'
        guard_text = (f"{robust.get('position_error_mm', 'n/a')} mm position and "
                      f"{robust.get('orientation_error_degrees', 'n/a')} degrees orientation"
                      if enabled else 'Not evaluated (disabled by configuration)')
        overhead = runtime.get('robustness_runtime_overhead_fraction')
        overhead_text = (f'+{100 * float(overhead):.1f}% relative to coarse + fine'
                         if overhead is not None else 'Not recorded')
        target_sections.append(f"""
<section id="target-{index}" class="target-section">
  <div class="section-heading"><div><div class="kicker">Target {index}</div>
  <h2>{html.escape(item['display_name'])}</h2></div>
  <div class="section-meta">{html.escape(item['network'])} | {html.escape(item['map_mode'])}</div></div>
  <div class="target-grid"><div>{preview}</div><div>
    <dl><dt>Anatomical search zone</dt><dd>{html.escape(item['anatomical_zone'])}</dd>
    <dt>Coil</dt><dd>{html.escape(item['coil_hardware_name'])}</dd>
    <dt>Coarse projected-target coverage</dt><dd>{coverage}</dd>
    <dt>Simulated poses</dt><dd>{item['simulations']:,} total: {item['counts']['coarse']:,} coarse, {item['counts']['fine']:,} fine, {item['counts']['robustness']:,} robustness</dd>
    <dt>{prescription_label}</dt><dd>fine center {html.escape(str(pose.get('fine_center_id', pose.get('center_id', 'n/a'))))}; simulated {html.escape(str(pose.get('orientation_degrees', 'n/a')))} degrees, prescribed {html.escape(str(item['prescription'].get('prescribed_orientation_degrees', 'n/a')))} degrees</dd>
    <dt>Robustness guard</dt><dd>{html.escape(guard_text)}</dd>
    <dt>Search runtime</dt><dd>{duration(float(runtime.get('baseline_coarse_plus_fine_wall_seconds', 0)) + float(runtime.get('robustness_total_wall_seconds', 0)))}</dd>
    <dt>Robustness overhead</dt><dd>{duration(runtime.get('robustness_total_wall_seconds'))}; {overhead_text}</dd></dl>
    <div class="links">{links}</div>
  </div></div>
</section>""")

    stage_rows = []
    for name, label in (("headmodel", "Native CHARM head model"),
                        ("targets", "Target preparation"),
                        ("plan", "Search planning"),
                        ("search", "Search orchestration"),
                        ("reports", "Reports and exports")):
        entry = pipeline_timing.get("stages", {}).get(name, {})
        stage_rows.append(
            f"<tr><td>{label}</td><td>{html.escape(entry.get('status', 'Not recorded'))}</td>"
            f"<td>{duration(entry.get('wall_seconds'))}</td></tr>")
    for item in targets:
        runtime = item["runtime"]
        for key, label in (("coarse_wall_seconds", "coarse search"),
                           ("fine_wall_seconds", "fine search"),
                           ("robustness_planning_wall_seconds", "robustness planning"),
                           ("robustness_simulation_wall_seconds", "robustness simulation"),
                           ("robustness_analysis_wall_seconds", "robustness analysis")):
            stage_status = ('skipped by configuration' if key.startswith('robustness_')
                            and not runtime.get('robustness_enabled', True) else 'complete')
            stage_rows.append(
                f"<tr><td>{html.escape(item['display_name'])}: {label}</td><td>{stage_status}</td>"
                f"<td>{duration(runtime.get(key))}</td></tr>")

    dose_callout = ""
    if dose_html and dose_html.is_file():
        dose_url = html.escape(relative_url(dose_html, output))
        dose_callout = f"""<section class="dose-band"><div><div class="kicker">Operator workflow</div>
<h2>SM-Hand rMT to treatment dose</h2><p>Use the prescribed SM-Hand pose for rMT, then open the treatment target's matched dose report. One planned motor reference and one treatment prescription are preselected; the actual rMT pose must match that reference.</p></div>
<a class="primary" href="{dose_url}">Open dose calibration</a></section>"""

    input_note = ""
    provenance = manifest.get("network_input_provenance", {})
    if provenance.get("mode") in ("wta_only", "weighted_only"):
        input_note = ('<section class="input-note"><div class="kicker">Input-map provenance</div>'
                      '<h2>Derived map counterpart</h2><p>' +
                      html.escape(provenance["summary"]) + '</p></section>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(manifest['subject'])} TANS report</title><style>
:root{{--paper:#f4f6f8;--ink:#19222c;--muted:#5e6b78;--line:#cbd2d8;--cyan:#52dce2;--gold:#d7ad36}}
*{{box-sizing:border-box}} html{{scroll-behavior:smooth}} body{{margin:0;background:var(--paper);color:var(--ink);font-family:Arial,sans-serif}}
header{{max-width:1500px;margin:auto;padding:34px 42px 24px}} h1{{font-size:38px;margin:0 0 8px}} h2{{font-size:25px;margin:2px 0 8px}} p{{line-height:1.5}}
.subtitle,.section-meta{{color:var(--muted)}} nav{{display:flex;gap:8px;flex-wrap:wrap;margin-top:22px}} nav a,.report-link,.primary{{color:var(--ink);text-decoration:none;border:1px solid #8e99a3;background:#fff;padding:9px 12px;font-weight:700;font-size:13px}}
.summary{{border-top:1px solid var(--line);border-bottom:1px solid var(--line)}} .summary-inner{{max-width:1500px;margin:auto;padding:24px 42px;display:grid;grid-template-columns:repeat(4,1fr);gap:28px}} .metric span{{display:block;color:var(--muted);font-size:12px;font-weight:700;text-transform:uppercase}} .metric b{{font-size:25px;display:block;margin-top:5px}}
main{{max-width:1500px;margin:auto;padding:0 42px 50px}} section{{padding:34px 0;border-bottom:1px solid var(--line)}} .section-heading{{display:flex;align-items:end;justify-content:space-between;margin-bottom:18px}} .kicker{{font-size:11px;text-transform:uppercase;color:var(--muted);font-weight:800}}
.target-grid{{display:grid;grid-template-columns:minmax(460px,1.15fr) minmax(340px,.85fr);gap:34px;align-items:start}} img{{width:100%;display:block;background:var(--paper)}} dl{{display:grid;grid-template-columns:190px 1fr;margin:0}} dt,dd{{padding:9px 0;border-bottom:1px solid #dce1e5}} dt{{font-weight:700}} dd{{margin:0}} .links{{display:flex;flex-wrap:wrap;gap:8px;margin-top:20px}}
.dose-band{{display:flex;align-items:center;justify-content:space-between;gap:20px}} .dose-band p{{margin:0;color:var(--muted)}} .primary{{background:var(--ink);color:#fff;border-color:var(--ink);font-size:15px}}
table{{width:100%;border-collapse:collapse;background:#fff}} th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line)}} th{{background:#e7ebee;font-size:12px;text-transform:uppercase}} .foot{{font-size:12px;color:var(--muted);margin-top:18px}}
@media(max-width:850px){{header,main,.summary-inner{{padding-left:20px;padding-right:20px}} .summary-inner{{grid-template-columns:1fr 1fr}} .target-grid{{grid-template-columns:1fr}} dl{{grid-template-columns:1fr}} .section-heading,.dose-band{{align-items:flex-start;flex-direction:column}}}}
</style></head><body><header><div class="kicker">TANS production report</div><h1>{html.escape(manifest['subject'])}</h1>
<div class="subtitle">Target selection, two-stage search, configured robustness checks, neuronavigation export, and dose transfer</div><nav>{navigation}</nav></header>
<div class="summary"><div class="summary-inner"><div class="metric"><span>Targets</span><b>{len(targets)}</b></div><div class="metric"><span>Simulated poses</span><b>{total_simulations:,}</b></div><div class="metric"><span>Accumulated search compute</span><b>{duration(total_search_seconds)}</b></div><div class="metric"><span>Target-map mode</span><b>{html.escape(str(manifest.get('map_mode', 'n/a')))}</b></div></div></div>
<main>{input_note}{dose_callout}{''.join(target_sections)}<section id="runtime"><div class="section-heading"><div><div class="kicker">Provenance</div><h2>Runtime by stage</h2></div></div><table><thead><tr><th>Stage</th><th>Status</th><th>Wall time</th></tr></thead><tbody>{''.join(stage_rows)}</tbody></table>
<div class="foot">Accumulated search compute sums target-specific wall times and can exceed elapsed clock time when targets or workers run concurrently. Raw simulation outputs pruned after extraction: {bytes_text(sum(float(item['runtime'].get('raw_simulation_bytes_pruned', 0)) for item in targets))}.</div></section></main></body></html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--report-index", required=True)
    parser.add_argument("--priors", required=True,
                        help="Reserved as the canonical network-color provenance")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    require_file(args.priors, "Priors.mat")
    manifest = read_json(args.manifest, "run manifest")
    report_index = read_json(args.report_index, "report index")
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_document(manifest, report_index, output))
    print(json.dumps({"master_report": str(output)}, indent=2))


if __name__ == "__main__":
    main()
