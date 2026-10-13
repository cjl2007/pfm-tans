"""Portable measured-rMT record and offline dosing figure; no delivery control.

Uses the existing embedded calibrations, dose metrics and cortical raster data.
Nothing is recorded as measured or delivered until explicitly confirmed. Records
are downloads, not browser storage or modifications of the source export.
"""

SESSION_CSS = """
.session{margin-top:30px;background:white;padding:22px;border:1px solid #cbd2d8}
.session h2{font-size:20px}.session-steps{display:grid;grid-template-columns:1fr 1fr;gap:28px}
.session input[type=checkbox]{width:18px;height:18px;margin:0 8px 0 0;vertical-align:middle}
.session .confirm{font-weight:400;line-height:1.6}.session button,.session a.download{display:inline-block;padding:10px 14px;border:1px solid #54616c;background:white;color:#19222c;font:14px Arial;text-decoration:none;cursor:pointer;margin:12px 6px 0 0}
.session button.primary{background:#19222c;color:white}.session button:disabled{opacity:.45;cursor:default}
.session [hidden]{display:none!important}.session-note{font-size:13px;line-height:1.6;color:#596673}
#sessionState{font-size:14px;font-weight:bold;line-height:1.5}#sessionFigure{width:100%;height:auto;aspect-ratio:3/2;border:1px solid #cbd2d8}
.session-error{color:#a52b20}.session-badge{border-left:4px solid #c49100;padding:8px 12px;background:#fff8df;margin-bottom:16px;font-size:13px}
@media(max-width:800px){.session-steps{grid-template-columns:1fr}.session{padding:14px}}
"""

SESSION_MARKUP = """
<section class="session" id="doseSession">
<h2>Measured-rMT dosing record</h2>
<p class="session-note">The controls above are for exploration. The initial rMT is an example, not a recorded measurement.
Lock the observed rMT first; then enter the treatment intensity actually used. Model selection and recorded intensity stay separate.</p>
<div class="session-steps"><div>
<h3>1. Lock observed rMT</h3>
<label for="sessionOperator">Recorded by (initials or name)</label><input id="sessionOperator" maxlength="80" autocomplete="off">
<label class="confirm"><input id="rmtConfirmed" type="checkbox">I confirm the rMT above was observed using the specified motor coil at the matching exported SM-Hand reference pose.</label>
<button type="button" id="lockRmt" class="primary">Lock observed rMT</button>
<button type="button" id="unlockRmt" hidden>Unlock / start a new record</button>
</div><div><h3>2. Record treatment intensity used</h3>
<label for="actualMso">Treatment intensity used (%MSO)</label><input id="actualMso" type="number" step="any" disabled>
<label class="confirm"><input id="actualConfirmed" type="checkbox" disabled>I confirm this was the intensity used with the specified treatment coil and matching exported target pose.</label>
<button type="button" id="generateSession" class="primary" disabled>Generate recorded-dose figure</button>
</div></div>
<p id="sessionState" role="status" aria-live="polite">Preview only — no measured rMT or treatment intensity recorded.</p>
<div id="sessionOutput" hidden><div class="session-badge">Technician-reported treatment intensity; not device-verified delivery. Cortical effects are modeled, not measured.</div>
<canvas id="sessionFigure" width="2400" height="1600" aria-label="Recorded-dose workflow: confirmed rMT, modeled response curves, and comparative cortical hotspots"></canvas>
<div id="sessionDownloads"></div><p class="session-note">Save the PDF and JSON together outside the immutable export package, for example in shared/session-records/SUBJECT/TARGET/SESSION.
Downloads go to your browser's chosen location; this page cannot silently overwrite local files. Unsaved records are lost on reload.
Changing either confirmation or any recorded value clears the generated downloads. Previously downloaded files are not changed.</p></div>
</section>
"""

SESSION_SCRIPT = r"""
let lockedRmt=null, doseSessionRecord=null, sessionGeneration=0, sessionUrls=[], sessionMeasurement=null;
const sessionClone=value=>JSON.parse(JSON.stringify(value));
function sessionMessage(text,error=false){$('sessionState').textContent=text;$('sessionState').className=error?'session-error':''}
function clearSessionOutput(){
 sessionGeneration++;doseSessionRecord=null;$('sessionOutput').hidden=true;$('sessionDownloads').replaceChildren();
 for(const url of sessionUrls)URL.revokeObjectURL(url);sessionUrls=[];
}
function invalidateSession(){
 clearSessionOutput();$('actualConfirmed').checked=false;
 if(lockedRmt)sessionMessage('rMT locked at '+lockedRmt.entry.rmt_mso+'% MSO. Confirm the treatment intensity used to generate a new record.');
}
function unlockSession(){
 clearSessionOutput();lockedRmt=null;
 for(const id of ['mso','didt','slider','target','sessionOperator','rmtConfirmed'])$(id).disabled=false;
 $('rmtConfirmed').checked=false;$('actualConfirmed').checked=false;$('actualMso').value='';
 for(const id of ['actualMso','actualConfirmed','generateSession'])$(id).disabled=true;
 $('lockRmt').hidden=false;$('unlockRmt').hidden=true;
 sessionMessage('Preview only — no measured rMT or treatment intensity recorded.');
}
function lockObservedRmt(){
 clearSessionOutput();
 const cal=LOOKUP.calibration,operator=$('sessionOperator').value.trim(),target=$('target').value;
 const measured=sessionMeasurement||{key:'rmt_mso',raw:$('mso').value};
 const raw=measured.raw,value=Number(raw),mso=measured.key==='rmt_mso'?value:interp(value,cal.samples_didt_a_per_us,cal.samples_mso);
 // Re-evaluate the visible input, never a cached entry awaiting a blur event.
 if(!raw.trim()||!Number.isFinite(mso)||mso<=0||mso<cal.mso_range[0]||mso>cal.mso_range[1]){sessionMessage('Enter a positive observed rMT inside the motor-coil calibrated range.',true);return}
 if(!operator||!$('rmtConfirmed').checked){sessionMessage('Enter who recorded the measurement and confirm the observed rMT, coil and reference pose.',true);return}
 if(!LOOKUP.treatment_sources[target]?.preview_metrics||!LOOKUP.hotspot_previews?.[target]){sessionMessage('This report lacks the embedded data required for a recorded-dose figure.',true);return}
 const didt=measured.key==='rmt_mso'?interp(mso,cal.samples_mso,cal.samples_didt_a_per_us):value,entry=calculateEntry(mso,didt);
 lockedRmt={entry:sessionClone(entry),target,operator,locked_at_utc:new Date().toISOString()};
 readMeasurement(measured.key,raw);
 for(const id of ['mso','didt','slider','target','sessionOperator','rmtConfirmed'])$(id).disabled=true;
 for(const id of ['actualMso','actualConfirmed','generateSession'])$(id).disabled=false;
 $('actualMso').value='';$('actualConfirmed').checked=false;
 $('lockRmt').hidden=true;$('unlockRmt').hidden=false;
 const range=LOOKUP.treatment_sources[target].calibration.mso_range;
 $('actualMso').min=range[0];$('actualMso').max=Math.min(range[1],entry.maximum_treatment_mso??range[1]);
 sessionMessage('Observed rMT locked: '+mso+'% MSO. The treatment-intensity field is deliberately blank; the modeled selection is not a delivery record.');
}
function recordedRow(raw,lock){
 if(!lock)throw Error('Lock the observed rMT first.');
 if(!raw.trim()||!Number.isFinite(Number(raw))||Number(raw)<=0)throw Error('Enter a positive treatment intensity actually used.');
 const row=metricsFor(lock.target,Number(raw),lock.entry);
 if(!row)throw Error('Treatment intensity is outside the calibrated range; no extrapolation.');
 if(!row.within_rmt_safety_limit)throw Error('Cannot generate this modeled record: treatment intensity exceeds the configured rMT-relative limit.');
 return row;
}
async function lookupDigest(){
 if(!globalThis.crypto?.subtle)throw Error('This browser cannot create the lookup provenance hash. Open the local report in a current browser.');
 const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(LOOKUP)));
 return Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');
}
function sessionStates(entry,target,actual){
 const rows=graphRows(entry,target).filter(r=>r.within_rmt_safety_limit),minimum=LOOKUP.policy.minimum_hotspot_area_mm2;
 const below=rows.filter(r=>r.hotspot_area_mm2===0).at(-1)||null;
 const small=rows.filter(r=>r.hotspot_area_mm2>0&&r.hotspot_area_mm2<minimum).at(-1)||null;
 return [{label:'Before modeled onset',row:below},{label:'Small modeled hotspot',row:small},
  {label:'Model-selected intensity',row:entry.treatments[target]},
  {label:'Recorded intensity used',row:actual}];
}
function sessionText(ctx,text,x,y,size=24,color='#19222c',weight='normal',maxWidth){
 ctx.fillStyle=color;ctx.font=weight+' '+size+'px Arial';ctx.textAlign='left';ctx.fillText(text,x,y,maxWidth);
}
function sessionWrap(ctx,text,x,y,width,size=21,line=30){
 ctx.font=size+'px Arial';let current='',yy=y;
 for(const word of text.split(' ')){const next=current?current+' '+word:word;
  if(current&&ctx.measureText(next).width>width){sessionText(ctx,current,x,yy,size);yy+=line;current=word}else current=next}
 if(current)sessionText(ctx,current,x,yy,size);return yy+line;
}
function sessionPlot(ctx,box,xrange,yrange,series,xlabel,ylabel,markers=[],shade=null){
 const [left,top,width,height]=box,L=left+72,T=top+24,W=width-95,H=height-92,
 x=v=>L+W*(v-xrange[0])/(xrange[1]-xrange[0]),y=v=>T+H*(1-(v-yrange[0])/(yrange[1]-yrange[0]));
 ctx.save();ctx.beginPath();ctx.rect(L,T,W,H);ctx.clip();
 if(shade!==null){ctx.fillStyle='#fff0ee';ctx.fillRect(Math.max(L,x(shade)),T,Math.max(0,L+W-x(shade)),H)}
 ctx.lineWidth=1;ctx.strokeStyle='#dce2e5';
 for(let i=0;i<3;i++){const v=yrange[0]+i*(yrange[1]-yrange[0])/2;ctx.beginPath();ctx.moveTo(L,y(v));ctx.lineTo(L+W,y(v));ctx.stroke()}
 for(const s of series){ctx.strokeStyle=s.color;ctx.lineWidth=3;ctx.beginPath();s.points.forEach(([vx,vy],i)=>i?ctx.lineTo(x(vx),y(vy)):ctx.moveTo(x(vx),y(vy)));ctx.stroke()}
 for(const m of markers){ctx.strokeStyle=m.color;ctx.lineWidth=2;ctx.setLineDash(m.dash?[7,6]:[]);ctx.beginPath();ctx.moveTo(x(m.x),T);ctx.lineTo(x(m.x),T+H);ctx.stroke();ctx.setLineDash([]);
  if(Number.isFinite(m.y)){ctx.fillStyle=m.color;ctx.beginPath();ctx.arc(x(m.x),y(m.y),6,0,2*Math.PI);ctx.fill()}}
 ctx.restore();
 for(let i=0;i<3;i++){const v=yrange[0]+i*(yrange[1]-yrange[0])/2;sessionText(ctx,Number(v.toFixed(1)).toString(),left+4,y(v)+6,18,'#596673')}
 for(let i=0;i<5;i++){const v=xrange[0]+i*(xrange[1]-xrange[0])/4;sessionText(ctx,Number(v.toFixed(1)).toString(),x(v)-12,T+H+28,18,'#596673')}
 sessionText(ctx,xlabel,L+W*.25,top+height-8,20);sessionText(ctx,ylabel,left+4,top+9,20);
}
async function drawSessionFigure(record,entry,canvas){
 const target=record.target,source=LOOKUP.treatment_sources[target],assets=await assetsFor(target);
 if(!assets)throw Error('Cortical preview unavailable.');
 const ctx=canvas.getContext('2d'),ink='#19222c',muted='#596673',gold='#a57700',color=targetColor(target),actual=record.actual_treatment;
 ctx.fillStyle='white';ctx.fillRect(0,0,canvas.width,canvas.height);
 sessionText(ctx,record.subject+' | '+target+' | Recorded-dose workflow',55,65,38,ink,'bold',2290);
 sessionText(ctx,'Observed rMT + technician-reported intensity; cortical response remains modeled',55,105,25,muted);
 sessionText(ctx,'Recorded by '+record.recorded_by+' | '+record.record_created_at_utc,55,143,21,muted,'normal',2290);
 const headings=['OBSERVED MOTOR rMT','MOTOR-DERIVED THRESHOLD','TREATMENT INTENSITY USED'];
 const values=[record.observed_rmt.mso+'% MSO',record.motor_derived_threshold_v_per_m.toFixed(2)+' V/m',actual.treatment_mso+'% MSO'];
 const notes=[record.observed_rmt.didt_a_per_us.toFixed(3)+' A/us | '+record.motor_coil,
  'Weighted Q'+(100*LOOKUP.policy.threshold_quantile)+' | fixed saved motor pose',
  actual.treatment_didt_a_per_us.toFixed(3)+' A/us | '+record.treatment_coil];
 for(let i=0;i<3;i++){const x=55+780*i;ctx.fillStyle=i===2?'#fff8df':'#f4f6f8';ctx.fillRect(x,175,730,145);
  sessionText(ctx,headings[i],x+18,207,20,muted,'bold');sessionText(ctx,values[i],x+18,252,36,i===2?gold:ink,'bold');sessionText(ctx,notes[i],x+18,291,20,muted,'normal',694)}
 sessionText(ctx,'A  Observed motor-coil calibration',55,368,25,ink,'bold');
 const cal=LOOKUP.calibration;
 sessionPlot(ctx,[55,392,730,320],cal.mso_range,[cal.samples_didt_a_per_us[0],cal.samples_didt_a_per_us.at(-1)],
  [{color:'#008c95',points:cal.samples_mso.map((v,i)=>[v,cal.samples_didt_a_per_us[i]])}],
  'Motor output (% MSO)','dI/dt (A/us)',[{x:entry.rmt_mso,y:entry.rmt_didt_a_per_us,color:gold}]);
 sessionText(ctx,'B  Modeled on-target response',835,368,25,ink,'bold');
 sessionText(ctx,'C  Modeled hotspot area',1615,368,25,ink,'bold');
 const rows=graphRows(entry,target),range=source.calibration.mso_range,selected=entry.treatments[target];
 const metric=LOOKUP.policy.selection_metric;
 const marks=(key,factor=1)=>[
  ...(selected?[{x:selected.treatment_mso,y:selected[key]*factor,color,dash:true}]:[]),
  {x:actual.treatment_mso,y:actual[key]*factor,color:gold},
  ...(entry.maximum_treatment_mso!==null?[{x:entry.maximum_treatment_mso,color:'#a52b20',dash:true}]:[])];
 sessionPlot(ctx,[835,392,730,320],range,[0,100],[{color,points:rows.map(r=>[r.treatment_mso,100*r[metric]])}],
  'Treatment output (% MSO)',metric==='binary_on_target'?'Binary on-target (%)':'Weighted on-target (%)',marks(metric,100),entry.maximum_treatment_mso);
 const ymax=Math.max(1,LOOKUP.policy.minimum_hotspot_area_mm2,...rows.map(r=>r.hotspot_area_mm2))*1.05;
 sessionPlot(ctx,[1615,392,730,320],range,[0,ymax],[{color:'#596673',points:rows.map(r=>[r.treatment_mso,r.hotspot_area_mm2])},
  {color:'#a52b20',points:range.map(v=>[v,LOOKUP.policy.minimum_hotspot_area_mm2])}],
  'Treatment output (% MSO)','Hotspot area (mm2)',marks('hotspot_area_mm2'),entry.maximum_treatment_mso);
 sessionText(ctx,'Gold: recorded intensity | dashed target color: model selection | red: configured limits / area criterion',55,762,22,muted);
 const {p,base,network,coil,field}=assets;
 const states=sessionStates(entry,target,actual);
 for(let i=0;i<states.length;i++){
  const {label,row}=states[i],x=55+580*i;
  sessionText(ctx,String(i+1)+'  '+label,x,820,24,i===3?gold:ink,'bold',545);
  if(!row){sessionText(ctx,'Not available on saved grid',x+20,1000,23,muted);continue}
  const pixels=new ImageData(new Uint8ClampedArray(base.pixels.data),p.width,p.height),scale=row.treatment_didt_a_per_us/p.reference_didt_a_per_us;
  for(let k=0;k<field.length;k++)if(Number.isFinite(field[k])&&field[k]*scale>=record.motor_derived_threshold_v_per_m){
   const j=k*4;pixels.data[j]=network.pixels.data[j];pixels.data[j+1]=network.pixels.data[j+1];pixels.data[j+2]=network.pixels.data[j+2];pixels.data[j+3]=255}
  const surface=document.createElement('canvas');surface.width=p.width;surface.height=p.height;
  const sx=surface.getContext('2d');sx.putImageData(pixels,0,0);sx.drawImage(coil.image,0,0);
  const side=p.width/1.5,focus=p.focus_pixel||[p.width/2,p.height/2];
  ctx.drawImage(surface,focus[0]-side/2,focus[1]-side/2,side,side,x+40,845,455,455);
  sessionText(ctx,Number(row.treatment_mso.toFixed(4))+'% MSO',x+12,1334,29,i===3?gold:ink,'bold');
  sessionText(ctx,(100*row.binary_on_target).toFixed(1)+'% binary on-target | '+row.hotspot_area_mm2.toFixed(1)+' mm2',x+12,1366,21,muted);
 }
 let keyX=55,keyY=1410;
 for(const item of p.palette){const name=item.name.replaceAll('_',' ');ctx.font='17px Arial';const w=ctx.measureText(name).width+36;
  if(keyX+w>2340){keyX=55;keyY+=26}if(keyY>1462)break;
  ctx.fillStyle=item.color;ctx.fillRect(keyX,keyY-13,14,14);sessionText(ctx,name,keyX+21,keyY,17,muted);keyX+=w}
 const warnings=record.model_warnings.length?'Model flags: '+record.model_warnings.join(' '):'Configured hotspot and peak-proximity checks pass; this does not establish clinical safety or efficacy.';
 sessionWrap(ctx,warnings,55,1500,2290,20,25);
 sessionText(ctx,'Fixed saved poses; no new FEM. Visible-surface hotspots are display approximations; numerical scores use all saved cortical samples.',55,1565,20,muted);
}
// A single high-resolution raster page in a self-contained PDF. No CDN, print
// service, browser extension or network access; offsets are actual byte offsets.
function sessionPdf(canvas){
 const jpeg=Uint8Array.from(atob(canvas.toDataURL('image/jpeg',.96).split(',')[1]),c=>c.charCodeAt(0));
 const enc=new TextEncoder(),parts=[],offsets=[0];let length=0;
 const add=value=>{const bytes=typeof value==='string'?enc.encode(value):value;parts.push(bytes);length+=bytes.length};
 const object=(id,value)=>{offsets[id]=length;add(id+' 0 obj\n'+value+'\nendobj\n')};
 add('%PDF-1.4\n');object(1,'<< /Type /Catalog /Pages 2 0 R >>');object(2,'<< /Type /Pages /Kids [3 0 R] /Count 1 >>');
 object(3,'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1080 720] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>');
 offsets[4]=length;add('4 0 obj\n<< /Type /XObject /Subtype /Image /Width '+canvas.width+' /Height '+canvas.height+' /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length '+jpeg.length+' >>\nstream\n');add(jpeg);add('\nendstream\nendobj\n');
 const content='q 1080 0 0 720 0 0 cm /Im0 Do Q\n';object(5,'<< /Length '+enc.encode(content).length+' >>\nstream\n'+content+'endstream');
 const xref=length;add('xref\n0 6\n0000000000 65535 f \n');for(let i=1;i<=5;i++)add(String(offsets[i]).padStart(10,'0')+' 00000 n \n');
 add('trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+xref+'\n%%EOF\n');return new Blob(parts,{type:'application/pdf'});
}
function sessionDownload(blob,name,label){const a=document.createElement('a'),url=URL.createObjectURL(blob);sessionUrls.push(url);a.href=url;a.download=name;a.className='download';a.textContent=label;$('sessionDownloads').append(a)}
async function generateRecordedDose(){
 clearSessionOutput();const serial=sessionGeneration,lock=lockedRmt;
 try{
  const row=recordedRow($('actualMso').value,lock);
  if(!$('actualConfirmed').checked)throw Error('Confirm the treatment intensity, treatment coil and target pose actually used.');
  $('generateSession').disabled=true;sessionMessage('Generating recorded-dose figure and provenance...');
  const entry=lock.entry,target=lock.target,source=LOOKUP.treatment_sources[target],selected=entry.treatments[target],warnings=[];
  const inputModes=new Set(Object.values(LOOKUP.network_input_provenance||{}).map(item=>item.mode));
  if(inputModes.has('wta_only'))warnings.push('Input maps include WTA-derived 0/1 stand-in weights, not measured probabilities.');
  if(inputModes.has('weighted_only'))warnings.push('Input maps include percentile-derived support and relative weights, not calibrated probabilities.');
  if(!row.geometrically_feasible)warnings.push('Recorded intensity does not satisfy the configured hotspot criteria.');
  if(Number(source.selected_pose.target_to_global_peak_ratio)<LOOKUP.policy.minimum_target_to_global_peak_ratio)warnings.push('Saved treatment pose fails the peak-proximity gate.');
  if(!selected)warnings.push('No model-selected dose meets the configured selection criteria.');
  const record={schema_version:'tans-measured-dose-v1',subject:LOOKUP.subject,target,record_created_at_utc:new Date().toISOString(),
   recorded_by:lock.operator,rmt_locked_at_utc:lock.locked_at_utc,
   observed_rmt:{mso:entry.rmt_mso,didt_a_per_us:entry.rmt_didt_a_per_us,entered_as:sessionClone(sessionMeasurement||{key:'rmt_mso',raw:String(entry.rmt_mso)}),technician_confirmed:true},
   actual_treatment:{...sessionClone(row),technician_confirmed:true,delivery_verified_by_device:false},
   motor_derived_threshold_v_per_m:entry.absolute_threshold_v_per_m,
   model_selected_treatment:sessionClone(selected),model_warnings:warnings,
   network_input_provenance:sessionClone(LOOKUP.network_input_provenance||{}),
   motor_coil:LOOKUP.calibration.coil_hardware_name,treatment_coil:source.calibration.coil_hardware_name,
   motor_reference_pose:sessionClone(LOOKUP.calibration.selected_pose),treatment_pose:sessionClone(source.selected_pose),
   motor_selection_mode:LOOKUP.calibration.selection_mode,treatment_selection_mode:source.selection_mode,
   motor_calibration:{samples_mso:LOOKUP.calibration.samples_mso,samples_didt_a_per_us:LOOKUP.calibration.samples_didt_a_per_us},
   treatment_calibration:sessionClone(source.calibration),policy:sessionClone(LOOKUP.policy),
   operator_prescription:sessionClone(LOOKUP.operator_prescription||null),
   preview_provenance:sessionClone(LOOKUP.hotspot_previews[target].provenance),
   lookup_sha256:await lookupDigest(),lookup_hash_encoding:'UTF-8 JSON.stringify(LOOKUP), as embedded in DoseCalibrationReport.html',
   new_simulations:0,interpretation:'Technician-reported measurements; modeled effects are not verified cortical activation or a clinical dosing recommendation.'};
  const canvas=document.createElement('canvas');canvas.width=2400;canvas.height=1600;
  await drawSessionFigure(record,entry,canvas);
  const png=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!png)throw Error('Could not encode the dosing figure.');
  const pdf=sessionPdf(canvas);
  if(serial!==sessionGeneration||lockedRmt!==lock)return;
  const safe=value=>String(value).replace(/[^a-zA-Z0-9._-]/g,'_');
  const stem=safe(record.subject)+'_'+safe(target)+'_RecordedDose_'+safe(record.record_created_at_utc);
  $('sessionFigure').getContext('2d').drawImage(canvas,0,0);doseSessionRecord=record;
  sessionDownload(pdf,stem+'.pdf','Download dosing PDF');sessionDownload(png,stem+'.png','Download PNG');
  sessionDownload(new Blob([JSON.stringify(record,null,2)+'\n'],{type:'application/json'}),stem+'.json','Download measurement record (JSON)');
  $('sessionOutput').hidden=false;
  sessionMessage('Generated from locked rMT '+entry.rmt_mso+'% MSO and recorded treatment intensity '+row.treatment_mso+'% MSO. Save the PDF and JSON; this page does not save automatically.');
 }catch(error){if(serial===sessionGeneration)sessionMessage(error.message,true)}
 finally{$('generateSession').disabled=!lockedRmt}
}
$('lockRmt').onclick=lockObservedRmt;$('unlockRmt').onclick=unlockSession;$('generateSession').onclick=generateRecordedDose;
for(const id of ['mso','didt','slider','target'])$(id).addEventListener('input',()=>{if(lockedRmt)unlockSession();$('rmtConfirmed').checked=false;clearSessionOutput()});
// Capture exact typed values before the existing preview rounds its display.
for(const id of ['mso','didt','slider'])for(const event of ['input','change'])$(id).addEventListener(event,()=>{
 sessionMeasurement={key:id==='didt'?'rmt_didt_a_per_us':'rmt_mso',raw:$(id).value};
},true);
$('rmtConfirmed').addEventListener('change',()=>{if(lockedRmt)unlockSession();else clearSessionOutput()});
$('actualMso').addEventListener('input',invalidateSession);
$('actualConfirmed').addEventListener('change',()=>{clearSessionOutput();if(lockedRmt)sessionMessage('rMT remains locked. Generate a new record after confirming the treatment intensity used.')});
window.addEventListener('beforeunload',event=>{if(lockedRmt){event.preventDefault();event.returnValue=''}});
// Keep the example/exploration status adjacent to the numerical output as well.
const sessionBadge=document.createElement('div');sessionBadge.className='session-badge';sessionBadge.id='rmtRecordBadge';
sessionBadge.textContent='Preview only until observed rMT is locked below. A modeled selection is not a record of treatment delivered.';
$('dose').closest('.result').before(sessionBadge);
const originalSessionMessage=sessionMessage;
sessionMessage=function(text,error=false){originalSessionMessage(text,error);sessionBadge.textContent=lockedRmt?'Observed rMT locked: '+lockedRmt.entry.rmt_mso+'% MSO. Modeled selection below; recorded-dose figure is separate.':'Preview only until observed rMT is locked below. A modeled selection is not a record of treatment delivered.'};
"""
