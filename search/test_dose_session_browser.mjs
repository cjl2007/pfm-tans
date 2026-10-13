import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const profile=await mkdtemp(join(tmpdir(),'tans-figure-review-chrome-'));
const artifacts=await mkdtemp(join(tmpdir(),'tans-measured-dose-test-'));
if(!process.argv[2])throw Error('Usage: node test_dose_session_browser.mjs /path/to/BioSelect-23005/CinguloOpercular/DoseCalibrationReport.html');
const pageURL=pathToFileURL(resolve(process.argv[2])).href;
const chrome=spawn('google-chrome',['--headless','--no-sandbox','--disable-gpu',
  '--no-first-run','--no-default-browser-check','--remote-debugging-pipe',
  `--user-data-dir=${profile}`],{stdio:['ignore','ignore','pipe','pipe','pipe']});
let nextId=0, buffer='', session;
const pending=new Map(), errors=[];
chrome.stdio[4].on('data',data=>{
  buffer+=data.toString();let split;
  while((split=buffer.indexOf('\0'))>=0){
    const raw=buffer.slice(0,split);buffer=buffer.slice(split+1);if(!raw)continue;
    const message=JSON.parse(raw);
    if(message.method==='Runtime.exceptionThrown')errors.push(message.params);
    const handler=pending.get(message.id);
    if(handler){pending.delete(message.id);clearTimeout(handler.timer);
      message.error?handler.reject(Error(JSON.stringify(message.error))):handler.resolve(message.result);}
  }
});
const call=(method,params={},sessionId=session)=>new Promise((resolve,reject)=>{
  const id=++nextId;
  const timer=setTimeout(()=>{pending.delete(id);reject(Error(`Timeout: ${method}`));},30000);
  pending.set(id,{resolve,reject,timer});
  chrome.stdio[3].write(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})})+'\0');
});
const evaluate=async expression=>{
  const result=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
  if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
};
try{
  const target=await call('Target.createTarget',{url:pageURL});
  ({sessionId:session}=await call('Target.attachToTarget',{targetId:target.targetId,flatten:true}));
  await call('Runtime.enable');await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:pageURL});
  await evaluate(`new Promise(resolve=>{if(document.readyState==='complete')resolve();else window.addEventListener('load',resolve,{once:true})})`);
  assert.equal(await evaluate('lockedRmt'),null);
  assert.equal(await evaluate("$('sessionOutput').hidden && $('generateSession').disabled"),true);
  await evaluate("lockObservedRmt()");
  assert.equal(await evaluate('lockedRmt'),null,'Cannot lock without attestation');
  await evaluate("$('sessionOperator').value='BROWSER TEST - NOT CLINICAL';$('rmtConfirmed').checked=true;$('mso').value='';lockObservedRmt()");
  assert.equal(await evaluate('lockedRmt'),null,'Blank rMT blocked');
  await evaluate("$('mso').value='101';lockObservedRmt()");
  assert.equal(await evaluate('lockedRmt'),null,'Outside calibration blocked');
  await evaluate("$('mso').value='47.3';lockObservedRmt()");
  assert.equal(await evaluate('lockedRmt.entry.rmt_mso'),47.3);
  assert.equal(await evaluate("['mso','didt','slider','target'].every(id=>$(id).disabled)"),true);
  assert.equal(await evaluate("$('actualMso').value"),'','Actual dose is never autofilled');
  await evaluate('generateRecordedDose()');
  assert.equal(await evaluate('doseSessionRecord'),null,'Blank actual dose blocked');
  await evaluate("$('actualMso').value='51';generateRecordedDose()");
  assert.equal(await evaluate('doseSessionRecord'),null,'Actual dose attestation required');
  await evaluate("$('actualMso').value='101';$('actualConfirmed').checked=true;generateRecordedDose()");
  assert.equal(await evaluate('doseSessionRecord'),null,'Uncalibrated actual dose blocked');
  if(await evaluate('lockedRmt.entry.maximum_treatment_mso!==null')){
    await evaluate("$('actualMso').value=lockedRmt.entry.maximum_treatment_mso+1;generateRecordedDose()");
    assert.equal(await evaluate('doseSessionRecord'),null,'Ceiling enforced');
  }
  await evaluate("$('actualMso').value='48.7';generateRecordedDose()");
  const record=await evaluate('doseSessionRecord');
  assert.ok(record,'Record generated');assert.equal(record.observed_rmt.mso,47.3);
  assert.equal(record.actual_treatment.treatment_mso,48.7);assert.equal(record.actual_treatment.delivery_verified_by_device,false);
  assert.notEqual(record.model_selected_treatment?.treatment_mso,48.7,'Recorded value differs from model selection in this fixture');
  assert.equal(record.lookup_sha256.length,64);assert.equal(record.new_simulations,0);
  assert.equal(await evaluate("$('sessionDownloads').children.length"),3);
  // Export test artifacts only under /tmp; never pretend these are real measurements.
  for(const [suffix,selector] of [['pdf','pdf'],['png','png'],['json','json']]){
    const bytes=await evaluate(`(async()=>{const a=$('sessionDownloads').querySelector('a[download$=".${selector}"]');const b=await(await fetch(a.href)).blob();return await new Promise(resolve=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.readAsDataURL(b)})})()`);
    await writeFile(join(artifacts,'TEST_ONLY_RecordedDose.'+suffix),Buffer.from(bytes,'base64'));
  }
  // Editing clears downloads and confirmation, preventing a stale export.
  await evaluate("$('actualMso').value='49';$('actualMso').dispatchEvent(new Event('input'))");
  assert.equal(await evaluate("doseSessionRecord===null && $('sessionOutput').hidden && !$('actualConfirmed').checked && $('sessionDownloads').children.length===0"),true);
  await evaluate("$('actualConfirmed').checked=true;generateRecordedDose()");
  assert.equal(await evaluate('doseSessionRecord.actual_treatment.treatment_mso'),49);
  // Start asynchronous generation and immediately unlock: completion cannot restore it.
  await evaluate("(async()=>{const pending=generateRecordedDose();unlockSession();await pending})()");
  assert.equal(await evaluate("lockedRmt===null && doseSessionRecord===null && $('sessionOutput').hidden && $('sessionDownloads').children.length===0"),true);
  assert.equal(await evaluate("['mso','didt','slider','target'].every(id=>!$(id).disabled)"),true);
  // dI/dt entry must retain exact input, not its rounded four-decimal MSO display.
  await evaluate("$('didt').value='84.512345';$('didt').dispatchEvent(new Event('input'));$('didt').dispatchEvent(new Event('change'));$('rmtConfirmed').checked=true;lockObservedRmt()");
  assert.equal(await evaluate('lockedRmt.entry.rmt_didt_a_per_us'),84.512345);
  assert.equal(await evaluate("lockedRmt.entry.rmt_mso===interp(84.512345,LOOKUP.calibration.samples_didt_a_per_us,LOOKUP.calibration.samples_mso)"),true);
  await evaluate('unlockSession()');
  await evaluate("$('doseSession').scrollIntoView()");
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true);
  const shot=await call('Page.captureScreenshot',{format:'png'});
  await writeFile(join(artifacts,'session-controls-check.png'),Buffer.from(shot.data,'base64'));
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true);
  assert.deepEqual(errors,[]);
  const result={status:'passed',example_input_is_unlocked:true,measured_rmt:47.3,test_actual_mso:48.7,
    confirmations_required:true,calibration_and_ceiling_enforced:true,actual_dose_not_model_selection:true,
    edits_and_unlock_clear_stale_downloads:true,async_unlock_race_checked:true,
    exact_didt_input_preserved:true,pdf_png_json_generated:true,desktop_and_mobile_overflow:false,runtime_errors:0,artifacts};
  await writeFile(join(artifacts,'BrowserValidation.json'),JSON.stringify(result,null,2)+'\n');
  process.stdout.write(JSON.stringify(result,null,2)+'\n');
}finally{chrome.kill();}
