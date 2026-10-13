"""Offline dose landmarks and a fixed-camera cortical hotspot preview."""

PREVIEW_CSS = """
html{scrollbar-gutter:stable}
main{max-width:1480px}.layout{grid-template-columns:minmax(280px,.7fr) minmax(620px,1.8fr)}
.result-grid{display:grid;grid-template-columns:minmax(160px,.75fr) minmax(260px,1.25fr);gap:20px}
.result{padding:20px}.preview-pane{min-width:0}.preview-pane h3{font-size:14px;margin:0}
.preview-header{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}.zoom-tools{display:flex;gap:4px;flex:none}.zoom-tools button{width:28px;height:28px;border:1px solid #cbd2d8;background:white;color:#19222c;font:18px Arial;cursor:pointer}.zoom-tools button:disabled{opacity:.35;cursor:default}
canvas#hotspot{width:100%;max-width:380px;height:auto;aspect-ratio:1;display:block;margin:0 auto;border:0;background:white}
.preview-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;font-size:12px;margin-top:12px}
.preview-metrics b{display:block;font-size:16px;margin-top:4px}.preview-note{font-size:12px;line-height:1.45;margin:8px 0}
.network-key{display:flex;flex-wrap:wrap;gap:5px 12px;margin-top:10px;font-size:11px}
.network-key span{display:inline-flex;align-items:center;gap:5px}.network-key i{width:10px;height:10px;border:1px solid #879198;flex:none}
.landmarks{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:16px 0 0;font-size:12px}
.landmarks div{border-left:3px solid #66747d;padding-left:9px}.landmarks dt{color:#596673}.landmarks dd{margin:4px 0 0;font-size:16px;font-weight:bold}
canvas#curve{height:auto;aspect-ratio:24/7}.preview-alert{color:#a52b20;font-weight:bold}
canvas#curve[aria-disabled=false]{cursor:grab;touch-action:none}canvas#curve.dragging{cursor:grabbing}canvas#curve:focus-visible{outline:2px solid #26749a;outline-offset:3px}
.curve-controls{display:flex;align-items:center;flex-wrap:wrap;gap:10px 20px;margin-top:10px;font-size:12px}.curve-controls span{display:inline-flex;align-items:center;gap:6px}.curve-controls i{width:10px;height:10px;flex:none;border:2px solid var(--target-color,#19222c)}.curve-controls .preview-symbol{border-radius:50%;background:var(--target-color,#19222c)}.curve-controls .model-symbol{transform:rotate(45deg);background:#fff}.curve-controls button{margin-left:auto;padding:7px 10px;background:#fff;border:1px solid #cbd2d8;color:#19222c;font:12px Arial;cursor:pointer}.curve-controls button:disabled{opacity:.45;cursor:default}
@media(max-width:1050px){.layout{grid-template-columns:1fr}.result-grid{grid-template-columns:minmax(170px,.7fr) minmax(250px,1fr)}}
@media(max-width:520px){.result-grid{grid-template-columns:1fr}.landmarks{grid-template-columns:1fr 1fr}.dose{font-size:30px}.result{padding:14px}.preview-metrics b{font-size:14px}canvas#curve{aspect-ratio:1.8}}
"""

PREVIEW_MARKUP = """
<div class="preview-pane"><div class="preview-header"><h3 id="previewTitle">Modeled hotspot networks</h3><div class="zoom-tools"><button type="button" id="zoomOut" title="Zoom out" aria-label="Zoom out">-</button><button type="button" id="zoomIn" title="Zoom in on modeled peak" aria-label="Zoom in on modeled peak">+</button></div></div>
<canvas id="hotspot" width="512" height="512" aria-label="Pial surface from above the treatment coil, with networks inside the modeled hotspot"></canvas>
<div class="preview-note" id="previewState" aria-live="polite"></div>
<div class="preview-metrics"><div>Binary on-target<b id="precision"></b></div><div>Hotspot area<b id="area"></b></div><div>Target coverage<b id="coverage"></b></div></div>
<div class="network-key" id="networkKey"></div></div>
"""

PREVIEW_SCRIPT = r"""
const metricCache=new Map(), imageCache=new Map(), graphCurveCache=new WeakMap();
let previewSerial=0,previewZoom=1.5,curvePointer=null,curvePreviewFrame=null;
function decodeArray(value,Type){const bytes=Uint8Array.from(atob(value),c=>c.charCodeAt(0));return new Type(bytes.buffer)}
function interp(value,x,y){if(!Number.isFinite(value)||value<x[0]||value>x[x.length-1])return NaN;let i=1;while(i<x.length&&x[i]<value)i++;if(i===x.length)return y[y.length-1];return y[i-1]+(value-x[i-1])*(y[i]-y[i-1])/(x[i]-x[i-1])}
function metricsFor(target,mso,entry){
 const source=LOOKUP.treatment_sources[target],data=source.preview_metrics;
 if(!data)return null;
 if(!metricCache.has(target))metricCache.set(target,{
  f:decodeArray(data.field_f64le_base64,Float64Array),
  a:decodeArray(data.area_prefix_f64le_base64,Float64Array),
  t:decodeArray(data.target_area_prefix_f64le_base64,Float64Array),
  w:decodeArray(data.target_mass_prefix_f64le_base64,Float64Array)});
 const {f,a,t,w}=metricCache.get(target),cal=source.calibration,
 didt=interp(mso,cal.samples_mso,cal.samples_didt_a_per_us),scale=didt/source.reference_didt_a_per_us;
 if(!Number.isFinite(didt))return null;
 let lo=0,hi=f.length;
 while(lo<hi){const mid=(lo+hi)>>>1;if(f[mid]*scale>=(entry.preview_absolute_threshold_v_per_m??entry.absolute_threshold_v_per_m))hi=mid;else lo=mid+1}
 const area=Math.max(0,a[a.length-1]-a[lo]),overlap=Math.max(0,t[t.length-1]-t[lo]),mass=Math.max(0,w[w.length-1]-w[lo]),
 geometry=area>=LOOKUP.policy.minimum_hotspot_area_mm2&&overlap>0,
 within=entry.maximum_treatment_mso===null||mso<=entry.maximum_treatment_mso+1e-9;
 return {treatment_mso:mso,treatment_didt_a_per_us:didt,hotspot_area_mm2:area,target_area_in_hotspot_mm2:overlap,
 binary_on_target:area?overlap/area:0,weighted_on_target:area?mass/area:0,
 binary_target_coverage:data.total_target_area_mm2?overlap/data.total_target_area_mm2:0,
 geometrically_feasible:geometry,within_rmt_safety_limit:within,feasible:geometry&&within};
}
function onsetAt(target,entry,targetOnly=false){
 const source=LOOKUP.treatment_sources[target],data=source.preview_metrics;
 if(!data)return null;
 metricsFor(target,source.calibration.samples_mso[0],entry);
 const {f,t}=metricCache.get(target);let i=f.length-1;
 if(targetOnly)while(i>=0&&t[i+1]===t[i])i--;
 const peak=targetOnly?(data.maximum_target_reference_field??f[i]):f[i];
 if(!Number.isFinite(peak)||peak<=0)return null;
 const value=interp((entry.preview_absolute_threshold_v_per_m??entry.absolute_threshold_v_per_m)*source.reference_didt_a_per_us/peak,source.calibration.samples_didt_a_per_us,source.calibration.samples_mso);
 return Number.isFinite(value)?value:null;
}
function calculateEntry(mso,didt){
 const cal=LOOKUP.calibration,entry={rmt_mso:mso,rmt_didt_a_per_us:didt,
 absolute_threshold_v_per_m:cal.threshold_v_per_m_per_calibration_didt*didt,
 maximum_treatment_mso:LOOKUP.policy.maximum_treatment_rmt_ratio===null?null:mso*LOOKUP.policy.maximum_treatment_rmt_ratio,
 treatments:{},treatment_landmarks:{},continuous_curves:{}};
 for(const target of LOOKUP.treatment_targets){
  const source=LOOKUP.treatment_sources[target],rows=source.preview_metrics.treatment_grid_mso.map(value=>metricsFor(target,value,entry));
  const feasible=rows.filter(r=>r.feasible),metric=LOOKUP.policy.selection_metric,
  maximum=feasible.length?Math.max(...feasible.map(r=>r[metric])):null,
  selected=feasible.find(r=>Math.abs(r[metric]-maximum)<=1e-12+1e-5*Math.abs(maximum));
  const pose=source.selected_pose,ratio=Number(pose.target_to_global_peak_ratio);
  entry.treatments[target]=selected?{...selected,target,target_color:targetColor(target),peak_ratio:ratio,
   passes_peak_gate:ratio>=LOOKUP.policy.minimum_target_to_global_peak_ratio,
   pose_center_id:pose.center_id,pose_selection_mode:source.selection_mode,pose_orientation_degrees:pose.orientation_degrees}:null;
  entry.continuous_curves[target]=rows;
  entry.treatment_landmarks[target]={first_cortex_mso:onsetAt(target,entry),first_target_mso:onsetAt(target,entry,true),
   first_geometrically_feasible:rows.find(r=>r.geometrically_feasible)||null,first_feasible:rows.find(r=>r.feasible)||null};
 }
 return entry;
}
function clearPreview(message){previewSerial++;const c=$('hotspot');c.getContext('2d').clearRect(0,0,c.width,c.height);delete c.dataset.intensity;delete c.dataset.threshold;delete c.dataset.visibleNetworks;$('previewState').textContent=message;$('previewState').className='preview-note preview-alert';$('networkKey').replaceChildren();$('precision').textContent=$('coverage').textContent=$('area').textContent='-';refreshCurveControl()}
function imagePixels(url,width,height){return new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>{const c=document.createElement('canvas');c.width=width;c.height=height;const x=c.getContext('2d');x.drawImage(img,0,0,width,height);resolve({image:img,pixels:x.getImageData(0,0,width,height)})};img.onerror=()=>reject(Error('Preview image could not be decoded'));img.src=url})}
async function assetsFor(target){
 if(!imageCache.has(target)){const p=LOOKUP.hotspot_previews?.[target];if(!p)return null;
  imageCache.set(target,Promise.all([imagePixels(p.base_gray_png,p.width,p.height),imagePixels(p.network_png,p.width,p.height),imagePixels(p.coil_outline_png,p.width,p.height)]).then(([base,network,coil])=>({p,base,network,coil,field:decodeArray(p.field_f32le_base64,Float32Array),labels:decodeArray(p.network_index_u8_base64,Uint8Array)})))}
 return imageCache.get(target);
}
async function updatePreview(entry){
 const serial=++previewSerial,target=$('target').value,raw=$('planned').value,mso=Number(raw),source=LOOKUP.treatment_sources[target],cal=source.calibration;
 const supported=!!source.preview_metrics&&!!LOOKUP.hotspot_previews?.[target];$('hotspot').hidden=!supported;$('hotspot').style.display=supported?'block':'none';$('zoomIn').hidden=$('zoomOut').hidden=!supported;
 if(!supported){$('previewState').textContent='No surface preview in this saved lookup. Metrics describe the selected dose.';$('networkKey').replaceChildren();return}
 $('previewTitle').textContent='Modeled hotspot | '+(raw&&Number.isFinite(mso)?mso.toFixed(1)+'% MSO':'no intensity');
 if(!raw||!Number.isFinite(mso)||mso<cal.mso_range[0]||mso>cal.mso_range[1]){clearPreview('No preview: intensity outside calibrated range.');return}
 if(entry.maximum_treatment_mso!==null&&mso>entry.maximum_treatment_mso+1e-9){clearPreview('Preview blocked: above configured upward limit.');return}
 const row=metricsFor(target,mso,entry);
 if(row){$('precision').textContent=(100*row.binary_on_target).toFixed(1)+'%';$('coverage').textContent=(100*row.binary_target_coverage).toFixed(1)+'%';$('area').textContent=row.hotspot_area_mm2.toFixed(1)+' mm2'}
 $('previewState').textContent='Loading modeled surface...';$('previewState').className='preview-note';
 try{
  const assets=await assetsFor(target);if(serial!==previewSerial)return;
  if(!assets){clearPreview('Surface preview unavailable for this saved report.');return}
  const {p,base,network,coil,field,labels}=assets,c=$('hotspot');c.width=p.width;c.height=p.height;
  const pixels=new ImageData(new Uint8ClampedArray(base.pixels.data),p.width,p.height),scale=row.treatment_didt_a_per_us/p.reference_didt_a_per_us,visible=new Set();
  for(let i=0;i<field.length;i++)if(Number.isFinite(field[i])&&field[i]*scale>=(entry.preview_absolute_threshold_v_per_m??entry.absolute_threshold_v_per_m)){const j=i*4;pixels.data[j]=network.pixels.data[j];pixels.data[j+1]=network.pixels.data[j+1];pixels.data[j+2]=network.pixels.data[j+2];pixels.data[j+3]=255;visible.add(labels[i])}
  const composed=document.createElement('canvas');composed.width=p.width;composed.height=p.height;const cx=composed.getContext('2d');cx.putImageData(pixels,0,0);cx.drawImage(coil.image,0,0);
  const x=c.getContext('2d'),side=p.width/previewZoom,focus=previewZoom>1?(p.focus_pixel||[p.width/2,p.height/2]):[p.width/2,p.height/2];x.fillStyle='#fff';x.fillRect(0,0,c.width,c.height);x.drawImage(composed,focus[0]-side/2,focus[1]-side/2,side,side,0,0,c.width,c.height);
  $('previewState').textContent='Pial cortex, above coil | '+entry.absolute_threshold_v_per_m.toFixed(1)+' V/m cutoff. Visible surface only; scores include all cortex. '+(row.geometrically_feasible?'':'Below configured hotspot criteria. ');
  $('previewState').className='preview-note'+(row.geometrically_feasible?'':' preview-alert');
  $('networkKey').replaceChildren();for(const item of p.palette)if(visible.has(item.label)){const span=document.createElement('span'),swatch=document.createElement('i');swatch.style.background=item.color;span.append(swatch,document.createTextNode(item.name.replaceAll('_',' ')));$('networkKey').append(span)}
  c.dataset.intensity=String(mso);c.dataset.threshold=String(entry.absolute_threshold_v_per_m);c.dataset.visibleNetworks=String(visible.size);
 }catch(error){if(serial===previewSerial)clearPreview('Surface preview unavailable. Numerical dose selection is unchanged.');console.error(error)}
}
function landmarkRows(entry,target){const d=entry.treatment_landmarks?.[target],selected=entry.treatments[target];if(!d)return [];
 return [
  {name:'First modeled cortex crossing',value:d.first_cortex_mso,style:'dash',color:'#66747d'},
  {name:'First retained-target crossing',value:d.first_target_mso,style:'dot',color:targetColor(target)},
  {name:'First area-qualified dose ('+LOOKUP.policy.minimum_hotspot_area_mm2+' mm2)',value:d.first_geometrically_feasible?.treatment_mso??null,style:'square',color:'#7b8490'},
  {name:'Model-selected intensity',value:selected?.treatment_mso??null,style:'circle',color:targetColor(target)}];
}
function curveLimits(){
 if(!validEntry)return null;
 const source=LOOKUP.treatment_sources[$('target').value];if(!source.preview_metrics)return null;
 const range=source.calibration.mso_range,exactLower=range[0],exactUpper=Math.min(range[1],validEntry.maximum_treatment_mso??range[1]),
 lower=Math.ceil((exactLower-1e-9)*10)/10,upper=Math.floor((exactUpper+1e-9)*10)/10;
 return lower<=upper?{lower,upper,exactLower,exactUpper}:null;
}
function refreshCurveControl(){
 const c=$('curve'),bounds=curveLimits(),raw=$('planned').value,mso=Number(raw),selected=validEntry?.treatments[$('target').value];
 c.setAttribute('aria-disabled',bounds?'false':'true');
 if(bounds){c.setAttribute('aria-valuemin',String(bounds.exactLower));c.setAttribute('aria-valuemax',String(bounds.exactUpper))}
 else{c.removeAttribute('aria-valuemin');c.removeAttribute('aria-valuemax');cancelCurveDrag()}
 const valid=bounds&&raw!==''&&Number.isFinite(mso)&&mso>=bounds.exactLower&&mso<=bounds.exactUpper+1e-9;
 if(valid){c.setAttribute('aria-valuenow',String(mso));c.setAttribute('aria-valuetext',mso.toFixed(1)+' percent MSO; motor-derived threshold fixed')}
 else{c.removeAttribute('aria-valuenow');c.removeAttribute('aria-valuetext');delete c.dataset.markerX;delete c.dataset.markerY}
 if($('curvePreviewValue')){
  $('curveControls').style.setProperty('--target-color',targetColor($('target').value));
  $('curvePreviewValue').textContent=valid?'Preview '+mso.toFixed(1)+'% MSO':'Preview unavailable';
  $('curveModelValue').textContent=selected?'Model selection '+selected.treatment_mso.toFixed(1)+'% MSO':'No feasible model selection';
  $('curveReset').disabled=!selected;
 }
}
function updateCheckedDose(defer=false){
 if(!validEntry)return;
 checkLimit(validEntry);draw(validEntry,$('target').value,validEntry.treatments[$('target').value]);
 // Coalesce pointer movements; the image always renders the latest intensity.
 if(curvePreviewFrame!==null){cancelAnimationFrame(curvePreviewFrame);curvePreviewFrame=null}
 if(defer)curvePreviewFrame=requestAnimationFrame(()=>{curvePreviewFrame=null;if(validEntry)updatePreview(validEntry)});
 else updatePreview(validEntry);
}
function setCurveMso(value,defer=false){
 const bounds=curveLimits();if(!bounds||!Number.isFinite(value))return;
 $('planned').value=Math.min(bounds.upper,Math.max(bounds.lower,Math.round(value*10)/10)).toFixed(1);
 updateCheckedDose(defer);
}
function cancelCurveDrag(){
 const c=$('curve'),pointer=curvePointer;curvePointer=null;c.classList.remove('dragging');
 if(pointer!==null&&c.hasPointerCapture(pointer))c.releasePointerCapture(pointer);
 if(curvePreviewFrame!==null){cancelAnimationFrame(curvePreviewFrame);curvePreviewFrame=null}
}
function curvePoint(event){
 const c=$('curve'),r=c.getBoundingClientRect();
 return {x:(event.clientX-r.left-c.clientLeft)*c.width/c.clientWidth,
         y:(event.clientY-r.top-c.clientTop)*c.height/c.clientHeight};
}
function setCurveFromPointer(event){
 const c=$('curve'),p=curvePoint(event),range=LOOKUP.treatment_sources[$('target').value].calibration.mso_range;
 setCurveMso(range[0]+(p.x-65)/(c.width-65-22)*(range[1]-range[0]),true);
}
function graphRows(entry,target){
 const grid=LOOKUP.treatment_sources[target].preview_metrics?.treatment_grid_mso;
 if(!grid)return LOOKUP.dose_response_curves[target][String(entry.rmt_mso)];
 if(!graphCurveCache.has(entry))graphCurveCache.set(entry,new Map());
 const cached=graphCurveCache.get(entry);if(!cached.has(target))cached.set(target,entry.continuous_curves?.[target]||grid.map(mso=>metricsFor(target,mso,entry)));
 return cached.get(target);
}
draw=function(entry,target,selected){
 const c=$('curve'),ctx=c.getContext('2d'),range=LOOKUP.treatment_sources[target].calibration.mso_range,
 raw=$('planned').value,mso=Number(raw),preview=raw!==''&&Number.isFinite(mso)?metricsFor(target,mso,entry):null;
 let rows=graphRows(entry,target);
 if(preview)rows=[...rows.filter(row=>row.treatment_mso!==mso),preview].sort((a,b)=>a.treatment_mso-b.treatment_mso);
 c.width=Math.max(400,Math.round(c.clientWidth));c.height=Math.max(220,Math.round(c.clientHeight));
 const L=65,R=22,T=20,B=42,W=c.width-L-R,H=c.height-T-B,x=v=>L+W*(v-range[0])/(range[1]-range[0]),y=v=>T+H*(1-v),color=targetColor(target);
 ctx.clearRect(0,0,c.width,c.height);ctx.fillStyle='#fff';ctx.fillRect(0,0,c.width,c.height);ctx.font='14px Arial';
 if(entry.maximum_treatment_mso!==null){const lx=Math.min(x(range[1]),Math.max(L,x(entry.maximum_treatment_mso)));ctx.fillStyle='#fff0ee';ctx.fillRect(lx,T,x(range[1])-lx,H)}
 ctx.strokeStyle='#e0e5e9';ctx.lineWidth=1;ctx.fillStyle='#596673';ctx.textAlign='right';for(const v of [0,.5,1]){ctx.beginPath();ctx.moveTo(L,y(v));ctx.lineTo(L+W,y(v));ctx.stroke();ctx.fillText(Math.round(v*100)+'%',L-9,y(v)+5)}
 ctx.strokeStyle=color;ctx.lineWidth=2.5;ctx.beginPath();rows.forEach((r,i)=>i?ctx.lineTo(x(r.treatment_mso),y(r.binary_on_target)):ctx.moveTo(x(r.treatment_mso),y(r.binary_on_target)));ctx.stroke();
 for(const item of landmarkRows(entry,target)){if(item.value===null)continue;ctx.strokeStyle=item.color;ctx.lineWidth=item.style==='circle'?1.6:1;ctx.setLineDash(item.style==='dash'?[7,5]:item.style==='dot'?[2,5]:[]);ctx.beginPath();ctx.moveTo(x(item.value),T);ctx.lineTo(x(item.value),T+H);ctx.stroke()}
 ctx.setLineDash([]);if(selected){const sx=x(selected.treatment_mso),sy=y(selected.binary_on_target);ctx.beginPath();ctx.moveTo(sx,sy-10);ctx.lineTo(sx+10,sy);ctx.lineTo(sx,sy+10);ctx.lineTo(sx-10,sy);ctx.closePath();ctx.fillStyle='#fff';ctx.fill();ctx.strokeStyle=color;ctx.lineWidth=2;ctx.stroke()}
 if(entry.maximum_treatment_mso!==null&&entry.maximum_treatment_mso<range[1]){ctx.strokeStyle='#a52b20';ctx.setLineDash([6,4]);ctx.beginPath();ctx.moveTo(x(entry.maximum_treatment_mso),T);ctx.lineTo(x(entry.maximum_treatment_mso),T+H);ctx.stroke();ctx.setLineDash([])}
 delete c.dataset.markerX;delete c.dataset.markerY;
 if(preview&&preview.within_rmt_safety_limit){const px=x(mso),py=y(preview.binary_on_target);ctx.beginPath();ctx.arc(px,py,8,0,2*Math.PI);ctx.fillStyle=color;ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=2.5;ctx.stroke();c.dataset.markerX=String(px);c.dataset.markerY=String(py)}
 ctx.fillStyle='#596673';ctx.textAlign='center';for(const value of [0,25,50,75,100])if(value>=range[0]&&value<=range[1])ctx.fillText(value+'%',x(value),T+H+23);ctx.fillText('Treatment MSO',L+W/2,c.height-3);
 ctx.save();ctx.translate(17,T+H/2);ctx.rotate(-Math.PI/2);ctx.fillText('Binary on-target',0,0);ctx.restore();
 $('landmarks').replaceChildren();for(const item of landmarkRows(entry,target)){const div=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');div.style.borderColor=item.color;dt.textContent=item.name;dd.textContent=item.value===null?'Not reached':item.value.toFixed(1)+'% MSO';if(item.value!==null&&entry.maximum_treatment_mso!==null&&item.value>entry.maximum_treatment_mso+1e-9)dd.textContent+=' (above limit)';div.append(dt,dd);$('landmarks').append(div)}
 refreshCurveControl();
};
const previousRender=render;
render=function(entry){cancelCurveDrag();previousRender(entry);$('mso').value=Number(entry.rmt_mso.toFixed(4));$('didt').value=Number(entry.rmt_didt_a_per_us.toFixed(4));$('measurementNote').textContent='rMT '+Number(entry.rmt_mso.toFixed(4))+'% MSO = '+Number(entry.rmt_didt_a_per_us.toFixed(4))+' A/us. '+(entry.continuous_curves?'Interpolated coil calibration; fixed-pose dose grid rescored.':'');if(!entry.treatments[$('target').value]){clearPreview('No feasible modeled selection for this rMT.');return}updatePreview(entry)};
const originalReadMeasurement=readMeasurement;
readMeasurement=function(key,raw){
 const cal=LOOKUP.calibration,value=Number(raw);if(!cal.samples_mso){originalReadMeasurement(key,raw);if(!validEntry){$('landmarks').replaceChildren();clearPreview('Invalid rMT input.')}return}
 const mso=key==='rmt_mso'?value:interp(value,cal.samples_didt_a_per_us,cal.samples_mso),didt=key==='rmt_mso'?interp(value,cal.samples_mso,cal.samples_didt_a_per_us):value;
 if(raw.trim()===''||!Number.isFinite(mso)||!Number.isFinite(didt)||mso<=0||didt<=0||mso<cal.mso_range[0]||mso>cal.mso_range[1]){validEntry=null;$('dose').textContent='Invalid rMT input';$('doseDidt').textContent='';$('planned').value='';$('threshold').textContent='-';$('status').textContent='Enter a positive rMT within the motor-coil calibrated range. No extrapolation.';$('status').style.color='#a52b20';$('limit').classList.remove('ok');$('limit').textContent='No prescription available for this input.';$('pose').textContent='';$('measurementNote').textContent='';$('landmarks').replaceChildren();$('curve').getContext('2d').clearRect(0,0,$('curve').width,$('curve').height);clearPreview('No preview for invalid rMT.');return}
 const saved=entries.find(e=>Math.abs(e.rmt_mso-mso)<1e-9&&Math.abs(e.rmt_didt_a_per_us-didt)<1e-8);
 render(saved||calculateEntry(mso,didt));
};
$('mso').step='any';$('didt').step='any';
$('planned').oninput=()=>updateCheckedDose();
const curve=$('curve');
curve.setAttribute('role','slider');curve.setAttribute('tabindex','0');curve.setAttribute('aria-label','Treatment intensity preview');curve.setAttribute('aria-orientation','horizontal');curve.title='Treatment intensity preview; motor-derived threshold stays fixed';
curve.insertAdjacentHTML('afterend','<div class="curve-controls" id="curveControls"><span><i class="preview-symbol" aria-hidden="true"></i><b id="curvePreviewValue"></b></span><span><i class="model-symbol" aria-hidden="true"></i><b id="curveModelValue"></b></span><button type="button" id="curveReset">Use model selection</button></div>');
$('curveReset').onclick=()=>{const selected=validEntry?.treatments[$('target').value];if(selected)setCurveMso(selected.treatment_mso)};
curve.onpointerdown=event=>{
 if(!curveLimits()||curvePointer!==null||event.button!==0)return;
 const p=curvePoint(event);if(p.x<65-14||p.x>curve.width-22+14||p.y<20-14||p.y>curve.height-42+14)return;
 event.preventDefault();curve.focus({preventScroll:true});curvePointer=event.pointerId;curve.setPointerCapture(event.pointerId);curve.classList.add('dragging');setCurveFromPointer(event);
};
curve.onpointermove=event=>{if(event.pointerId===curvePointer){event.preventDefault();setCurveFromPointer(event)}};
curve.onpointerup=event=>{if(event.pointerId===curvePointer){setCurveFromPointer(event);cancelCurveDrag();updateCheckedDose()}};
curve.onpointercancel=curve.onlostpointercapture=event=>{if(event.pointerId===curvePointer){cancelCurveDrag();updateCheckedDose()}};
curve.onkeydown=event=>{
 const bounds=curveLimits();if(!bounds)return;let value=Number($('planned').value);if(!Number.isFinite(value))value=bounds.lower;
 const step=event.shiftKey?1:.1;
 if(event.key==='ArrowRight'||event.key==='ArrowUp')value+=step;
 else if(event.key==='ArrowLeft'||event.key==='ArrowDown')value-=step;
 else if(event.key==='PageUp')value+=10;
 else if(event.key==='PageDown')value-=10;
 else if(event.key==='Home')value=bounds.lower;
 else if(event.key==='End')value=bounds.upper;
 else return;
 event.preventDefault();setCurveMso(value);
};
for(const [id,step] of [['zoomIn',.5],['zoomOut',-.5]])$(id).onclick=()=>{previewZoom=Math.min(3,Math.max(1,previewZoom+step));$('zoomIn').disabled=previewZoom===3;$('zoomOut').disabled=previewZoom===1;if(validEntry)updatePreview(validEntry)};
window.addEventListener('resize',()=>{if(validEntry)draw(validEntry,$('target').value,validEntry.treatments[$('target').value])});
render(validEntry||nearestBy('rmt_mso',50));
"""
