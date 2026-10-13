import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {cp, mkdir, writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';

const source = process.argv[2];
if (!source) throw new Error('Expected path to CIR002 review directory');
const out = '/tmp/tans-dose-drag/browser';
await mkdir(out, {recursive: true});
await cp(source, `${out}/relocated`, {recursive: true});
const chrome = spawn('google-chrome', [
  '--headless', '--no-sandbox', '--disable-gpu', '--remote-debugging-port=9260',
  `--user-data-dir=${out}/profile`, 'about:blank',
], {stdio: 'ignore'});
let socket;
const checks = [], errors = [];
const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
try {
  let tabs;
  for (let i = 0; i < 100; i++) {
    try {tabs = await (await fetch('http://127.0.0.1:9260/json')).json(); break;}
    catch {await sleep(100);}
  }
  assert.ok(tabs, 'Browser failed to start');
  socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => socket.addEventListener('open', resolve, {once: true}));
  let id = 0;
  const pending = new Map();
  socket.addEventListener('message', ({data}) => {
    const message = JSON.parse(data);
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params);
    if (pending.has(message.id)) {
      const {resolve, reject} = pending.get(message.id);
      pending.delete(message.id);
      message.error ? reject(Error(JSON.stringify(message.error))) : resolve(message.result);
    }
  });
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const next = ++id;
    pending.set(next, {resolve, reject});
    socket.send(JSON.stringify({id: next, method, params}));
  });
  const evaluate = async expression => {
    const response = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (response.exceptionDetails) throw Error(JSON.stringify(response.exceptionDetails));
    return response.result.value;
  };
  const input = (id, value, event = 'change') => evaluate(`(() => {
    const element=document.getElementById(${JSON.stringify(id)});
    element.value=${JSON.stringify(String(value))};
    element.dispatchEvent(new Event(${JSON.stringify(event)}, {bubbles:true}));
  })()`);
  const snapshot = () => evaluate(`(() => {
    const curve=$('curve'),entry=validEntry;
    return {rmt:entry?.rmt_mso,didt:entry?.rmt_didt_a_per_us,
      threshold:entry?.preview_absolute_threshold_v_per_m??entry?.absolute_threshold_v_per_m,
      selections:entry?JSON.stringify(entry.treatments):null,
      modelDose:$('dose').textContent,planned:$('planned').value,
      marker:Number(curve.getAttribute('aria-valuenow')),
      markerX:Number(curve.dataset.markerX),markerY:Number(curve.dataset.markerY),
      role:curve.getAttribute('role'),tabIndex:curve.tabIndex,disabled:curve.getAttribute('aria-disabled'),
      minimum:Number(curve.getAttribute('aria-valuemin')),maximum:Number(curve.getAttribute('aria-valuemax')),
      previewIntensity:$('hotspot').dataset.intensity,
      area:$('area').textContent,state:$('previewState').textContent,
      warning:$('previewState').classList.contains('preview-alert'),
      canvasWidth:curve.width,overflow:document.documentElement.scrollWidth>innerWidth};
  })()`);
  async function waitPreview() {
    for (let i = 0; i < 100; i++) {
      if (await evaluate(`(() => {
        const actual=$('hotspot').dataset.intensity,expected=$('planned').value;
        return actual!==undefined&&expected!==''&&Math.abs(Number(actual)-Number(expected))<1e-8
          &&!$('previewState').textContent.includes('Loading');
      })()`)) return;
      await sleep(100);
    }
    throw Error(`Preview did not settle: ${JSON.stringify(await snapshot())}`);
  }
  const pixelSum = () => evaluate("$('hotspot').getContext('2d').getImageData(0,0,$('hotspot').width,$('hotspot').height).data.reduce((total,value)=>total+value,0)");
  const screenshot = async name => {
    const shot = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: true});
    await writeFile(`${out}/${name}.png`, Buffer.from(shot.data, 'base64'));
    await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    await sleep(150);
  };
  async function geometry() {
    await evaluate("$('curve').scrollIntoView({block:'center',inline:'nearest'})");
    return evaluate(`(() => {const c=$('curve'),r=c.getBoundingClientRect();
      return {left:r.left+c.clientLeft,top:r.top+c.clientTop,width:c.clientWidth,height:c.clientHeight,w:c.width,h:c.height,
        markerX:Number(c.dataset.markerX),markerY:Number(c.dataset.markerY),innerWidth,scrollX};})()`);
  }
  const position = (geometry, mso, fractionY = 0.5) => ({
    x: geometry.left + (65 + (geometry.w - 65 - 22) * mso / 100) * geometry.width / geometry.w,
    y: geometry.top + (20 + (geometry.h - 20 - 42) * fractionY) * geometry.height / geometry.h,
  });
  const markerPosition = geometry => ({
    x: geometry.left + geometry.markerX * geometry.width / geometry.w,
    y: geometry.top + geometry.markerY * geometry.height / geometry.h,
  });
  async function mouseDrag(mso) {
    let bounds = await geometry(), start = markerPosition(bounds), end = position(bounds, mso);
    const trace = [{phase:'before-hover',bounds, start, end}];
    await call('Input.dispatchMouseEvent', {type: 'mouseMoved', ...start});
    await evaluate('new Promise(resolve=>requestAnimationFrame(resolve))');
    trace.push(await evaluate("(()=>{const c=$('curve'),r=c.getBoundingClientRect();return {phase:'after-hover',left:r.left,clientWidth:document.documentElement.clientWidth,scrollHeight:document.documentElement.scrollHeight,innerHeight}})()"));
    bounds = await geometry(); start = markerPosition(bounds); end = position(bounds, mso);
    await call('Input.dispatchMouseEvent', {type: 'mousePressed', ...start, button: 'left', buttons: 1, clickCount: 1});
    trace.push(await evaluate("(()=>{const c=$('curve'),r=c.getBoundingClientRect();return {phase:'after-press',planned:$('planned').value,left:r.left,clientWidth:document.documentElement.clientWidth,scrollHeight:document.documentElement.scrollHeight,innerHeight}})()"));
    for (let i = 1; i <= 8; i++) {
      await call('Input.dispatchMouseEvent', {
        type: 'mouseMoved', x: start.x + (end.x - start.x) * i / 8,
        y: start.y + (end.y - start.y) * i / 8, button: 'left', buttons: 1,
      });
      trace.push(await evaluate("(()=>{const c=$('curve'),r=c.getBoundingClientRect();return {planned:$('planned').value,left:r.left,width:r.width,client:c.clientWidth,buffer:c.width,pointer:curvePointer,innerWidth,scrollX}})()"));
    }
    await call('Input.dispatchMouseEvent', {type: 'mouseReleased', ...end, button: 'left', buttons: 0, clickCount: 1});
    await waitPreview();
    const actual = Number((await snapshot()).planned);
    if (Math.abs(actual - Math.min(mso, (await snapshot()).maximum)) > 0.11) console.log('Drag diagnostic:', JSON.stringify(trace));
  }
  async function mouseClick(mso) {
    let location = position(await geometry(), mso);
    await call('Input.dispatchMouseEvent', {type: 'mouseMoved', ...location});
    await evaluate('new Promise(resolve=>requestAnimationFrame(resolve))');
    location = position(await geometry(), mso);
    await call('Input.dispatchMouseEvent', {type: 'mousePressed', ...location, button: 'left', buttons: 1, clickCount: 1});
    await call('Input.dispatchMouseEvent', {type: 'mouseReleased', ...location, button: 'left', buttons: 0, clickCount: 1});
    await waitPreview();
  }
  async function key(key, virtualKey) {
    await evaluate("$('curve').focus()");
    await call('Input.dispatchKeyEvent', {type: 'keyDown', key, code: key, windowsVirtualKeyCode: virtualKey, nativeVirtualKeyCode: virtualKey});
    await call('Input.dispatchKeyEvent', {type: 'keyUp', key, code: key, windowsVirtualKeyCode: virtualKey, nativeVirtualKeyCode: virtualKey});
  }
  function fixedThreshold(actual, baseline, name) {
    for (const field of ['rmt', 'didt', 'threshold', 'selections', 'modelDose']) {
      assert.equal(actual[field], baseline[field], `${name}: ${field} changed while checking intensity`);
    }
  }
  async function checked(name, expected, baseline) {
    await waitPreview();
    const state = await snapshot();
    fixedThreshold(state, baseline, name);
    assert.ok(Math.abs(Number(state.planned) - expected) < 1e-8, `${name}: planned ${state.planned}, expected ${expected}`);
    assert.ok(Math.abs(state.marker - expected) < 1e-8, `${name}: marker ${state.marker}, expected ${expected}`);
    assert.ok(Math.abs(Number(state.previewIntensity) - expected) < 1e-8, `${name}: hotspot is stale`);
    const expectedX = 65 + (state.canvasWidth - 65 - 22) * expected / 100;
    assert.ok(Math.abs(state.markerX - expectedX) < 0.01, `${name}: marker position is stale`);
    assert.ok(Number.isFinite(state.markerY), `${name}: invalid marker ordinate`);
    checks.push({name, planned: state.planned, marker: state.marker, area: state.area, state: state.state});
    return state;
  }

  await call('Page.enable');
  await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride', {width: 1500, height: 1200, deviceScaleFactor: 1, mobile: false});
  await call('Page.navigate', {url: pathToFileURL(`${out}/relocated/DoseCalibrationReport.html`).href});
  let ready = false;
  for (let i = 0; i < 200; i++) {
    if (await evaluate("typeof validEntry!=='undefined'&&validEntry!==null&&!!document.getElementById('curveReset')")) {ready = true; break;}
    await sleep(100);
  }
  assert.ok(ready, 'Draggable-curve report was not loaded');
  await input('mso', 60);
  await waitPreview();
  const baseline = await snapshot();
  assert.equal(baseline.modelDose, '60.2% MSO');
  assert.equal(baseline.role, 'slider');
  assert.equal(baseline.tabIndex, 0);
  assert.equal(baseline.maximum, 72);
  assert.equal(baseline.minimum, 0);
  assert.equal(baseline.overflow, false);
  await checked('initial-marker', 60.2, baseline);
  const selectedPixels = await pixelSum();
  assert.ok(selectedPixels > 0, 'Initial hotspot canvas is blank');

  await mouseDrag(57.4);
  const midpoint = await checked('mouse-drag-midpoint', 57.4, baseline);
  assert.equal(midpoint.area, '32.7 mm2');
  assert.ok(midpoint.warning, 'Below-area intensity lacks warning');
  assert.notEqual(await pixelSum(), selectedPixels, 'Dragging did not update hotspot pixels');
  await screenshot('desktop-dragged-midpoint');

  await mouseClick(65.1);
  await checked('click-curve', 65.1, baseline);
  await input('planned', 61.3, 'input');
  await checked('typed-input-sync', 61.3, baseline);
  await key('ArrowRight', 39);
  await checked('keyboard-right', 61.4, baseline);
  await key('ArrowLeft', 37);
  await checked('keyboard-left', 61.3, baseline);
  await key('Home', 36);
  const minimum = await checked('keyboard-home', 0, baseline);
  assert.equal(minimum.area, '0.0 mm2');
  assert.ok(minimum.warning, 'Zero-intensity curve state lacks warning');
  await key('End', 35);
  await checked('keyboard-end-ceiling', 72, baseline);
  await mouseDrag(95);
  await checked('mouse-clamps-at-ceiling', 72, baseline);
  await mouseClick(50);
  const belowOnset = await checked('below-modeled-onset', 50, baseline);
  assert.equal(belowOnset.area, '0.0 mm2');
  assert.ok(belowOnset.warning, 'Below-onset intensity lacks warning');
  assert.match(belowOnset.state, /Below configured|No modeled|No supra/i);
  await screenshot('desktop-below-onset');
  await evaluate("$('curveReset').click()");
  await checked('reset-model-selection', 60.2, baseline);

  await input('didt', 93.5);
  await waitPreview();
  const fractional = await snapshot();
  assert.equal(fractional.rmt, 60.3125);
  assert.equal(fractional.maximum, 72.375);
  await key('End', 35);
  await checked('fractional-ceiling-keyboard-floor', 72.3, fractional);
  await mouseDrag(95);
  await checked('fractional-ceiling-drag-floor', 72.3, fractional);
  await input('planned', 72.35, 'input');
  await checked('typed-intensity-under-exact-fractional-ceiling', 72.35, fractional);
  await input('mso', 60);
  await waitPreview();

  await evaluate("LOOKUP.policy.maximum_treatment_rmt_ratio=null;render(calculateEntry(60,93))");
  await waitPreview();
  const noLimit = await snapshot();
  assert.equal(noLimit.maximum, 100);
  await key('End', 35);
  await checked('disabled-limit-allows-100', 100, noLimit);
  await evaluate("LOOKUP.policy.maximum_treatment_rmt_ratio=1.2;readMeasurement('rmt_mso','60')");
  await waitPreview();

  await call('Emulation.setDeviceMetricsOverride', {width: 390, height: 1100, deviceScaleFactor: 1, mobile: true});
  await call('Emulation.setTouchEmulationEnabled', {enabled: true, maxTouchPoints: 1});
  await evaluate("$('curveReset').click()");
  await waitPreview();
  const mobileBaseline = await snapshot();
  assert.equal(mobileBaseline.overflow, false, 'Mobile horizontal overflow');
  const bounds = await geometry(), start = markerPosition(bounds), end = position(bounds, 57.4);
  const touch = point => ({...point, id: 1, radiusX: 3, radiusY: 3, force: 1});
  await call('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [touch(start)]});
  for (let i = 1; i <= 8; i++) await call('Input.dispatchTouchEvent', {
    type: 'touchMove', touchPoints: [touch({x: start.x + (end.x - start.x) * i / 8,
      y: start.y + (end.y - start.y) * i / 8})],
  });
  await call('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
  await checked('mobile-touch-drag', 57.4, mobileBaseline);
  await screenshot('mobile-touch-drag');
  await call('Emulation.setTouchEmulationEnabled', {enabled: false});

  await input('didt', 2000);
  const invalid = await snapshot();
  assert.equal(invalid.modelDose, 'Invalid rMT input');
  assert.equal(invalid.planned, '');
  assert.equal(invalid.disabled, 'true', 'Graph slider remains enabled after invalid rMT');
  assert.equal(invalid.previewIntensity, undefined, 'Invalid rMT retains hotspot preview');
  await key('End', 35);
  const afterKey = await snapshot();
  assert.equal(afterKey.planned, '', 'Keyboard restored a dose after invalid rMT');
  assert.equal(await evaluate('validEntry'), null);
  checks.push({name: 'invalid-rmt-disables-graph', status: 'passed'});
  assert.equal(errors.length, 0, JSON.stringify(errors));
  await writeFile(`${out}/Validation.json`, JSON.stringify({status: 'passed', checks}, null, 2));
  console.log(JSON.stringify({status: 'passed', checks}, null, 2));
} finally {
  socket?.close();
  chrome.kill('SIGTERM');
  await Promise.race([new Promise(resolve => chrome.once('exit', resolve)), sleep(3000)]);
}
