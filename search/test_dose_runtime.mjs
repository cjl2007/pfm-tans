import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const directory = path.dirname(fileURLToPath(import.meta.url));
const python = process.env.TANS_PYTHON || 'python3';
const lookupPath = process.argv[2];
if (!lookupPath) throw Error('Usage: TANS_PYTHON=<python> node test_dose_runtime.mjs <DoseLookup.json>');
const fixtureSource = String.raw`
import ast,json,sys
from pathlib import Path
import numpy as np
import dose_landmarks as helper
report=helper.load_report_module()
lookup=helper.enrich_lookup(json.loads(Path(sys.argv[1]).read_text()),report)
script=next(ast.literal_eval(node.value) for node in ast.parse(Path('dose_preview_ui.py').read_text()).body
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PREVIEW_SCRIPT' for t in node.targets))
cal=lookup['calibration']
motor_mso=np.array(cal['samples_mso']);motor_didt=np.array(cal['samples_didt_a_per_us'])
cases=[]
for didt in [0.00001,0.5,1.2345,84.5,93.05,93.5,100.1234,156.9]:
    if not motor_didt[0]<=didt<=motor_didt[-1]:continue
    mso=float(report.interpolate_checked(didt,motor_didt,motor_mso))
    threshold=cal['threshold_v_per_m_per_calibration_didt']*didt
    limit=None if lookup['policy']['maximum_treatment_rmt_ratio'] is None else mso*lookup['policy']['maximum_treatment_rmt_ratio']
    expected={}
    for target,source in lookup['treatment_sources'].items():
        field,mask,weights,area,reference=helper.source_context(source,report)
        grid=source['preview_metrics']['treatment_grid_mso']
        calibration=source['calibration']
        grid_didt=report.interpolate_checked(grid,np.array(calibration['samples_mso']),np.array(calibration['samples_didt_a_per_us']))
        rows=report.evaluate_doses(field,reference,threshold,mask,weights,area,grid,grid_didt,
                                  lookup['policy']['minimum_hotspot_area_mm2'],limit,mso)
        expected[target]={'selected':report.rounded_row(report.choose_dose(rows,lookup['policy']['selection_metric'])),
                          'first_feasible':report.rounded_row(next((r for r in rows if r['feasible']),None))}
    cases.append({'mso':mso,'didt':didt,'threshold':threshold,'maximum':limit,'expected':expected})
print(json.dumps({'lookup':lookup,'script':script,'cases':cases}))
`;
const {lookup, script, cases} = JSON.parse(execFileSync(python, ['-B', '-c', fixtureSource, lookupPath],
  {cwd: directory, encoding: 'utf8', maxBuffer: 96 * 1024 * 1024}));
const nodes = new Map();
function nodeFor(id) {
  if (!nodes.has(id)) nodes.set(id, {
    value: '', textContent: '', style: {}, classList: {remove() {}, toggle() {}, add() {}},
    replaceChildren() {this.cleared = true;},
    getContext() {return {clearRect() {}};}, width: 720, height: 210,
  });
  return nodes.get(id);
}
const context = {
  LOOKUP: lookup, entries: lookup.entries, validEntry: null, console,
  atob: text => Buffer.from(text, 'base64').toString('binary'),
  $: nodeFor, targetColor: target => lookup.treatment_sources[target].target_color,
  originalReadMeasurement() {throw new Error('Unexpected legacy input path');},
  clearPreview(message) {context.previewMessage = message;},
  render(entry) {context.validEntry = entry; context.rendered = entry;},
};
vm.createContext(context);
const numericFunctions = script.slice(0, script.indexOf('function clearPreview'));
const inputFunction = script.slice(script.indexOf('readMeasurement=function'), script.indexOf("$('mso').step="));
assert.ok(numericFunctions.includes('function calculateEntry'));
assert.ok(inputFunction.includes('calculateEntry(mso,didt)'));
vm.runInContext(numericFunctions + '\n' + inputFunction, context, {filename: 'dose_preview_ui.numeric.js'});

let checks = 0;
function near(actual, expected, label, tolerance = 1.1e-8) {
  assert.ok(Number.isFinite(actual) && Math.abs(actual - expected) <= tolerance,
    `${label}: got ${actual}; expected ${expected}`);
  checks++;
}
function compareRow(actual, expected, label) {
  if (expected === null) {assert.equal(actual, null, label); checks++; return;}
  assert.ok(actual, `${label}: missing row`);
  for (const key of ['treatment_mso', 'treatment_didt_a_per_us', 'hotspot_area_mm2',
                    'target_area_in_hotspot_mm2', 'binary_on_target', 'binary_target_coverage', 'weighted_on_target']) {
    near(actual[key], expected[key], `${label}.${key}`, key.endsWith('mm2') ? 1e-6 : 1.1e-8);
  }
  for (const key of ['feasible', 'geometrically_feasible', 'within_rmt_safety_limit']) {
    assert.equal(actual[key], expected[key], `${label}.${key}`); checks++;
  }
}

for (const saved of lookup.entries) {
  const computed = context.calculateEntry(saved.rmt_mso, saved.rmt_didt_a_per_us);
  near(computed.absolute_threshold_v_per_m, saved.absolute_threshold_v_per_m,
       `rMT${saved.rmt_mso}.threshold`);
  for (const target of lookup.treatment_targets) {
    const label = `rMT${saved.rmt_mso}.${target}`;
    compareRow(computed.treatments[target], saved.treatments[target], `${label}.selection`);
    compareRow(computed.treatment_landmarks[target].first_feasible,
               saved.treatment_landmarks[target].first_feasible, `${label}.firstFeasible`);
    const expected = saved.treatment_landmarks[target];
    for (const key of ['first_cortex_mso', 'first_target_mso']) {
      if (expected[key] === null) assert.equal(computed.treatment_landmarks[target][key], null);
      else near(computed.treatment_landmarks[target][key], expected[key], `${label}.${key}`, 1e-7);
    }
  }
  context.readMeasurement('rmt_mso', String(saved.rmt_mso));
  assert.strictEqual(context.validEntry, saved, `Saved rMT${saved.rmt_mso} entry must be reused unchanged`);
  checks++;
}
for (const example of cases) {
  context.readMeasurement('rmt_didt_a_per_us', String(example.didt));
  const actual = context.validEntry;
  assert.ok(actual && actual.continuous_curves, `Continuous dI/dt ${example.didt} not rescored`);
  near(actual.rmt_mso, example.mso, 'inverse motor calibration');
  near(actual.rmt_didt_a_per_us, example.didt, 'measured dI/dt preserved');
  near(actual.absolute_threshold_v_per_m, example.threshold, 'continuous threshold');
  if (example.maximum === null) assert.equal(actual.maximum_treatment_mso, null);
  else near(actual.maximum_treatment_mso, example.maximum, 'continuous ceiling');
  for (const target of lookup.treatment_targets) {
    compareRow(actual.treatments[target], example.expected[target].selected,
               `continuous${example.didt}.${target}.selection`);
    compareRow(actual.treatment_landmarks[target].first_feasible,
               example.expected[target].first_feasible, `continuous${example.didt}.${target}.firstFeasible`);
  }
}
for (const [key, values] of [
  ['rmt_mso', ['', ' ', 'NaN', 'Infinity', '-1', '0', '100.0001']],
  ['rmt_didt_a_per_us', ['', 'NaN', '-1', '0', '100000']],
]) for (const raw of values) {
  context.readMeasurement(key, raw);
  assert.equal(context.validEntry, null, `${key}=${raw} must invalidate prior entry`);
  assert.equal(nodeFor('planned').value, '', 'Invalid input clears planned intensity');
  assert.ok(context.previewMessage.includes('invalid rMT'), 'Invalid input clears preview');
  checks++;
}

const saved60 = lookup.entries.find(row => row.rmt_mso === 60) || lookup.entries[0];
for (const target of lookup.treatment_targets) {
  const calibration = lookup.treatment_sources[target].calibration;
  assert.equal(context.metricsFor(target, calibration.samples_mso[0] - 0.1, saved60), null);
  assert.equal(context.metricsFor(target, calibration.samples_mso.at(-1) + 0.1, saved60), null);
  if (saved60.maximum_treatment_mso !== null && saved60.maximum_treatment_mso + 0.1 <= calibration.samples_mso.at(-1)) {
    const row = context.metricsFor(target, saved60.maximum_treatment_mso + 0.1, saved60);
    assert.equal(row.within_rmt_safety_limit, false);
    assert.equal(row.feasible, false);
  }
  const zero = context.metricsFor(target, 0, saved60);
  if (zero) assert.equal(zero.hotspot_area_mm2, 0);
}
assert.equal(context.interp(3, [0, 1, 5], [0, 10, 20]), 15, 'Piecewise calibration inverse');
assert.ok(Number.isNaN(context.interp(-1, [0, 1], [0, 2])), 'No extrapolation');
console.log(`PASS: ${lookup.entries.length} saved entries, ${cases.length} continuous dI/dt cases, ${checks} numerical/input checks.`);
