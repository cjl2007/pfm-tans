// Documentation-only render from a current offline report. Requires local Chrome.
// Does not confirm a real measurement, save session JSON, or change the source.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, writeFile, access} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const [report, output]=process.argv.slice(2);
if(!report||!output)throw Error('Usage: node render_readme_dose.mjs DoseCalibrationReport.html NEW_OUTPUT.png');
await access(report);
try{await access(output);throw Error('Refusing to overwrite output');}catch(e){if(e.code!=='ENOENT')throw e;}
const profile=await mkdtemp(join(tmpdir(),'tans-readme-chrome-'));
const chrome=spawn('google-chrome',['--headless','--no-sandbox','--disable-gpu',
  '--no-first-run','--no-default-browser-check','--remote-debugging-pipe',
  `--user-data-dir=${profile}`],{stdio:['ignore','ignore','pipe','pipe','pipe']});
let nextId=0,buffer='',session;
const pending=new Map();
chrome.stdio[4].on('data',data=>{
  buffer+=data.toString();let split;
  while((split=buffer.indexOf('\0'))>=0){
    const raw=buffer.slice(0,split);buffer=buffer.slice(split+1);if(!raw)continue;
    const message=JSON.parse(raw),handler=pending.get(message.id);
    if(handler){pending.delete(message.id);clearTimeout(handler.timer);
      message.error?handler.reject(Error(JSON.stringify(message.error))):handler.resolve(message.result);}
  }
});
const call=(method,params={},sessionId=session)=>new Promise((resolve,reject)=>{
  const id=++nextId,timer=setTimeout(()=>{pending.delete(id);reject(Error(`Timeout: ${method}`));},30000);
  pending.set(id,{resolve,reject,timer});
  chrome.stdio[3].write(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})})+'\0');
});
const evaluate=async expression=>{
  const result=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
  if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
};
try{
  const url=pathToFileURL(resolve(report)).href;
  const target=await call('Target.createTarget',{url});
  ({sessionId:session}=await call('Target.attachToTarget',{targetId:target.targetId,flatten:true}));
  await call('Runtime.enable');await call('Page.enable');await call('Page.navigate',{url});
  await evaluate(`new Promise(resolve=>{if(document.readyState==='complete')resolve();else window.addEventListener('load',resolve,{once:true})})`);
  assert.equal(await evaluate('lockedRmt'),null);
  const png=await evaluate(`(async()=>{
    const target=LOOKUP.treatment_targets[0],mso=50,exampleMso=52,
      didt=interp(mso,LOOKUP.calibration.samples_mso,LOOKUP.calibration.samples_didt_a_per_us),entry=calculateEntry(mso,didt),
      actual=metricsFor(target,exampleMso,entry);
    if(!Number.isFinite(didt)||!actual?.within_rmt_safety_limit)throw Error('Example outside lookup support');
    const record={subject:'Example participant',target,recorded_by:'illustrative values only',record_created_at_utc:'No session recorded',
      observed_rmt:{mso,didt_a_per_us:didt},actual_treatment:actual,
      motor_derived_threshold_v_per_m:entry.absolute_threshold_v_per_m,
      motor_coil:LOOKUP.calibration.coil_hardware_name,treatment_coil:LOOKUP.treatment_sources[target].calibration.coil_hardware_name,
      model_warnings:['DEMONSTRATION ONLY: rMT and treatment MSO below are hypothetical, not observed or delivered.']};
    let code=drawSessionFigure.toString();
    const labels=[
      ['Recorded-dose workflow','Dosing workflow example'],
      ['Observed rMT + technician-reported intensity; cortical response remains modeled','DEMONSTRATION: hypothetical inputs, no measured rMT or treatment delivery'],
      ['Recorded by ','Display context: '],
      ['OBSERVED MOTOR rMT','EXAMPLE MOTOR rMT'],
      ['TREATMENT INTENSITY USED','EXAMPLE TREATMENT INTENSITY'],
      ['Observed motor-coil calibration','Motor-coil calibration'],
      ['Gold: recorded intensity','Gold: example intensity']];
    for(const [from,to] of labels){if(!code.includes(from))throw Error('Renderer label contract changed: '+from);code=code.replaceAll(from,to);}
    code=code.replace('const {label,row}=states[i]','const row=states[i].row,label=states[i].label.replace("Recorded intensity used","Example intensity")');
    const render=(0,eval)('('+code+')'),canvas=document.createElement('canvas');canvas.width=2400;canvas.height=1600;
    await render(record,entry,canvas);return canvas.toDataURL('image/png').split(',')[1];
  })()`);
  assert.equal(await evaluate('lockedRmt===null && doseSessionRecord===null'),true);
  await writeFile(output,Buffer.from(png,'base64'),{flag:'wx'});
  process.stdout.write(JSON.stringify({output,example_rmt_mso:50,example_treatment_mso:52,
    measurements_recorded:false,new_simulations:0})+'\n');
}finally{chrome.kill();}
