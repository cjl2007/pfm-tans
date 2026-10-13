// Capture a documentation-only walkthrough of an authorized offline dose page.
// Uses real UI code with hypothetical confirmations in an isolated browser.
// No session downloads are clicked; source HTML and participant files are read-only.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, mkdir, writeFile, readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';

const [report, directory]=process.argv.slice(2);
if(!report||!directory)throw Error('Usage: node render_dose_workpage.mjs DoseCalibrationReport.html NEW_OUTPUT_DIRECTORY');
await mkdir(directory,{recursive:false});
const original=await readFile(report), hash=createHash('sha256').update(original).digest('hex');
const profile=await mkdtemp(join(tmpdir(),'tans-workpage-chrome-'));
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
const images=[];
try{
  const url=pathToFileURL(resolve(report)).href;
  const target=await call('Target.createTarget',{url});
  ({sessionId:session}=await call('Target.attachToTarget',{targetId:target.targetId,flatten:true}));
  await call('Runtime.enable');await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1500,height:1600,deviceScaleFactor:1,mobile:false});
  for(let step=1;step<=3;step++){
    await call('Page.navigate',{url});
    await evaluate(`new Promise(resolve=>{if(document.readyState==='complete')resolve();else window.addEventListener('load',resolve,{once:true})})`);
    assert.equal(await evaluate('lockedRmt===null && doseSessionRecord===null'),true);
    assert.equal(await evaluate(`LOOKUP.treatment_targets.join(',')`),'Salience');
    // Redact the documentation view, never the lookup, source, or runtime logic.
    await evaluate(`document.querySelector('h1').textContent='Example participant | Dose selection page';
      $('sessionOperator').value='DEMO ONLY'; $('planned').value='55';
      $('planned').dispatchEvent(new Event('input',{bubbles:true}));
      updatePreview(validEntry)`);
    assert.equal(await evaluate('Number($("hotspot").dataset.intensity)'),55);
    if(step>=2){
      await evaluate(`$('rmtConfirmed').checked=true; lockObservedRmt();`);
      assert.equal(await evaluate('lockedRmt.entry.rmt_mso'),50);
      await evaluate(`$('planned').value='55';$('planned').dispatchEvent(new Event('input',{bubbles:true}));updatePreview(validEntry)`);
    }
    if(step===3){
      await evaluate(`$('actualMso').value='55';$('actualConfirmed').checked=true;generateRecordedDose()`);
      assert.equal(await evaluate('doseSessionRecord.actual_treatment.treatment_mso'),55);
      assert.equal(await evaluate(`document.querySelectorAll('#sessionDownloads a').length`),3);
      // The native generated figure includes participant/time text. Re-render
      // just its documentation preview with generic, explicitly hypothetical labels.
      await evaluate(`(async()=>{
        const record={...doseSessionRecord,subject:'Example participant',recorded_by:'DEMO ONLY',
          record_created_at_utc:'Illustration; no session recorded',
          model_warnings:['Hypothetical inputs only. No observed rMT or treatment delivery.']};
        let code=drawSessionFigure.toString();
        for(const [from,to] of [
          ['Recorded-dose workflow','Dosing workflow example'],
          ['Observed rMT + technician-reported intensity; cortical response remains modeled','DEMONSTRATION: hypothetical inputs; no observed session or delivered treatment'],
          ['OBSERVED MOTOR rMT','EXAMPLE MOTOR rMT'],
          ['TREATMENT INTENSITY USED','EXAMPLE TREATMENT INTENSITY'],
          ['Gold: recorded intensity','Gold: example intensity']]){
          if(!code.includes(from))throw Error('Renderer label changed: '+from);code=code.replaceAll(from,to);
        }
        await (0,eval)('('+code+')')(record,lockedRmt.entry,$('sessionFigure'));
      })()`);
    }
    const selections=step===1?['.layout > section:first-child','.session-steps > div:first-child']:
      step===2?['.layout > section:nth-child(2)']:
      ['.session-steps > div:nth-child(2)','#sessionState','#sessionOutput'];
    const height=await evaluate(`(()=>{
      const panel=document.createElement('div');panel.id='documentationCapture';
      for(const selector of ${JSON.stringify(selections)})panel.append(document.querySelector(selector));
      document.body.append(panel);const style=document.createElement('style');style.textContent=
        'body{visibility:hidden;background:white}#documentationCapture{visibility:visible;position:absolute;left:0;top:0;width:640px;padding:20px;background:white;box-sizing:border-box}'+
        '#documentationCapture *{box-sizing:border-box}#documentationCapture section{border:0;padding:0}'+
        '#documentationCapture .result-grid{grid-template-columns:180px 1fr;gap:12px}'+
        '#documentationCapture .session-steps{display:block}'+
        '#documentationCapture input[type=checkbox]{width:18px;height:18px;vertical-align:middle}'+
        '#documentationCapture button,#documentationCapture a.download{padding:10px;border:1px solid #89939a;background:white;color:#19222c;margin:7px 5px 7px 0;display:inline-block;font:14px Arial;text-decoration:none}'+
        '#documentationCapture button.primary{background:#19222c;color:white}'+
        '#documentationCapture h3{font:700 18px Arial}'+
        '#documentationCapture [hidden]{display:none!important}';
      document.head.append(style);return Math.ceil(panel.getBoundingClientRect().height);
    })()`);
    const shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:640,height,scale:1}});
    images.push({data:shot.data,width:640,height});
    await writeFile(join(directory,`step-${step}.png`),Buffer.from(shot.data,'base64'),{flag:'wx'});
  }
  const titles=['1 · Enter and lock rMT','2 · Explore intensity and spread','3 · Record and save'];
  const notes=['Verify the motor coil and reference pose.','Preview dose independently of model selection.','Confirm the intensity used; save PDF + JSON.'];
  const html=`<!doctype html><html><head><meta charset="utf-8"><style>
    *{box-sizing:border-box}body{margin:0;padding:38px;background:#f4f6f8;font-family:Arial;color:#19222c;width:2100px}
    h1{font-size:42px;margin:0 0 10px}header p{font-size:24px;color:#596673;margin:0 0 26px}
    .badge{background:#fff2cf;border-left:6px solid #c49100;padding:14px 18px;font-size:23px;margin-bottom:30px}
    .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}.card{background:white;border:1px solid #cbd2d8;border-radius:12px;overflow:hidden}
    h2{font-size:25px;margin:0;padding:20px 18px 8px}.card p{padding:0 18px;font-size:18px;color:#596673;margin:0 0 18px}
    img{width:100%;display:block}.foot{font-size:22px;margin:24px 0 0;line-height:1.4}
    </style></head><body><header><h1>Dose selection page</h1>
    <p>Enter rMT, explore network exposure, and record the intensity used. Salience is the example shown.</p></header>
    <div class="badge">DEMONSTRATION ONLY · hypothetical rMT 50% MSO · illustrative treatment 55% MSO · no observed session</div>
    <div class="grid">${images.map((item,i)=>`<article class="card"><h2>${titles[i]}</h2><p>${notes[i]}</p><img src="data:image/png;base64,${item.data}" alt="Dosing page step ${i+1}"></article>`).join('')}</div>
    <p class="foot">The model selects 51.2% MSO in this example; the 55% preview and example record remain separate.<br>
    Nothing is saved automatically. Session records are technician reports, not device-verified delivery.</p></body></html>`;
  const storyboard=join(directory,'walkthrough.html');await writeFile(storyboard,html,{flag:'wx'});
  await call('Emulation.setDeviceMetricsOverride',{width:2100,height:1800,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:pathToFileURL(resolve(storyboard)).href});
  await evaluate(`Promise.all([...document.images].map(image=>image.decode()))`);
  const height=await evaluate('Math.ceil(document.body.getBoundingClientRect().height)');
  const shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:2100,height,scale:1}});
  await writeFile(join(directory,'sal-dose-workpage.png'),Buffer.from(shot.data,'base64'),{flag:'wx'});
  assert.equal(createHash('sha256').update(await readFile(report)).digest('hex'),hash);
  const validation={source_unchanged:true,source_sha256:hash,hypothetical_rmt_mso:50,example_treatment_mso:55,
    model_selected_mso:51.2,subject_identifiers_redacted_from_display:true,
    browser_confirmations:'documentation-only simulation',session_downloads_clicked:0,new_simulations:0};
  await writeFile(join(directory,'Validation.json'),JSON.stringify(validation,null,2)+'\n',{flag:'wx'});
  process.stdout.write(JSON.stringify(validation)+'\n');
}finally{chrome.kill();}
