import {drills,jobs} from '../api/entities.js';
import {downloadFile} from '../api/client.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';

const DRILL_STATUS={queued:'排队中',running:'裁决中',completed:'已完成',failed:'执行中断'};

function intervalText(d){return d.lo+'–'+d.hi+' dB';}

function drillCard(d,selected){
 const blocked=Boolean(d.superseded_by),approved=Boolean(d.approved);
 return '<article class="drill-card'+(d.id===selected?' selected':'')+'" data-drill="'+d.id+'">'+
  '<div class="drill-head"><strong>#'+d.id+' · v'+d.revision+' · '+esc(d.name)+'</strong><span class="status '+esc(d.status)+'">'+esc(DRILL_STATUS[d.status]||d.status)+'</span></div>'+
  '<p>共用声学区间 <b>'+esc(intervalText(d))+'</b> · '+d.segments_done+'/'+d.segments_expected+' 段已裁决</p>'+
  '<small>'+(blocked?'已作废 · 已由新版本取代':approved?'已批准 · '+esc(d.approved_by)+' · '+date(d.approved_at):d.complete?'待负责人批准':'尚未齐备')+'</small>'+
 '</article>';
}

export async function drillPage(id){
 const [list,allJobs]=await Promise.all([drills.list(),jobs.list()]);
 workspace.drills=list;
 const completed=allJobs.filter(j=>j.status==='completed'&&j.result);
 const current=list.find(d=>String(d.id)===String(id))||list[0]||null;
 const detail=current?await drills.get(current.id):null;
 const canWrite=session.user.role!=='viewer',isAdmin=session.user.role==='admin';
 const briefVisible=detail&&detail.approved&&detail.approved_result?detail.approved_result:{};
 const rows=(detail?.result?.rows)||[];
 const briefRows=briefVisible.table?.rows||[];
 const retestCount=rows.filter(r=>r.retest_required).length;
 const blocked=detail?Boolean(detail.superseded_by):false;
 const ready=detail?(!blocked&&detail.status==='completed'&&detail.complete&&rows.length>0):false;
 return heading('06 / INSTRUMENT OUTAGE','听漏仪停用演练','听漏仪停用时，用已完成试验的原规则复核验证样本：把声学读数填成一个共用区间，沿原规则检查区间内所有可能结论，可能翻转的管段列为待复测。','<button class="button" id="refresh-drills">刷新演练 ↻</button>')+
 '<div class="drill-grid"><section class="panel"><div class="section-title"><h2>发起演练</h2><span>基于已完成试验</span></div>'+
 '<form id="drill-form"><label>演练名称<input name="name" required minlength="2" value="听漏仪停用演练"></label>'+
 '<label>原试验<select name="job_id" required>'+completed.map(j=>'<option value="'+j.id+'"'+(detail&&j.id===detail.job_id?' selected':'')+'>'+esc(j.name)+' · 深度'+esc(j.params.max_depth)+'</option>').join('')+'</select></label>'+
 '<div class="parameter-grid"><label>区间下限 (dB)<input type="number" step="0.1" name="lo" value="12" min="0" max="200" required></label><label>区间上限 (dB)<input type="number" step="0.1" name="hi" value="85" min="0" max="200" required></label></div>'+
 '<button type="submit" class="primary" '+(canWrite&&completed.length?'':'disabled')+'>按共用区间发起演练 ↗</button>'+
 '<p class="hint">只替换验证样本的声学读数；其余字段沿用原记录，原规则树与训练参数不重新拟合。</p></form>'+
 '<div class="section-title"><h2>演练版本</h2><span>'+list.length+' 次</span></div>'+
 '<div class="drill-list">'+(list.map(d=>drillCard(d,detail?.id)).join('')||'<p class="empty">尚无演练记录。</p>')+'</div></section>'+
 '<section>'+(detail?detailPanel(detail,rows,briefRows,retestCount,ready,blocked,canWrite,isAdmin):'<div class="panel empty">选择一项已完成试验，填入共用声学区间后发起演练。</div>')+'</section></div>';
}

function detailPanel(d,rows,briefRows,retestCount,ready,blocked,canWrite,isAdmin){
 const progress=d.segments_expected?Math.round(d.segments_done/d.segments_expected*100):0;
 const possible=d.result?.possible_text||'';
 return '<section class="panel drill-detail"><div class="section-title"><h2>演练 #'+d.id+' · v'+d.revision+'</h2><span>区间 '+esc(intervalText(d))+'</span></div>'+
  (blocked?'<p class="warning">该演练已被新版本取代：旧区间不得批准，也不进入新版裁决。</p>':'')+
  (d.error?'<p class="warning">第 '+d.attempts+' 次执行中断：'+esc(d.error)+'。重试会从已有裁决继续，不重复记管段。</p>':'')+
  '<div class="drill-progress"><div><span>已裁决</span><strong>'+d.segments_done+' / '+d.segments_expected+'</strong></div><div class="bar"><i style="width:'+progress+'%"></i></div>'+
  '<div class="drill-actions">'+
   (canWrite&&(d.status==='failed'||(d.status==='queued'&&d.attempts>0))?'<button id="retry-drill" class="button small">恢复原次演练 ↻</button>':'')+
   (canWrite&&!blocked&&!d.approved?'<button id="revise-drill" class="button small">改区间（新建版本）</button>':'')+
   (isAdmin&&ready&&!d.approved?'<button id="approve-drill" class="primary small">批准整批裁决</button>':'')+
  '</div></div>'+
  '<form id="revise-form" class="hidden"><div class="parameter-grid"><label>新区间下限 (dB)<input type="number" step="0.1" name="lo" value="'+d.lo+'" min="0" max="200" required></label><label>新区间上限 (dB)<input type="number" step="0.1" name="hi" value="'+d.hi+'" min="0" max="200" required></label></div><button type="submit" class="primary small">保存为新版本 ↗</button><p class="hint">改区间后本版本作废，不得批准；新版本沿用同一份冻结样本，迟到记录不进入。</p></form>'+
  '<div class="drill-tiles"><div><span>待复测</span><strong id="retest-count">'+retestCount+'</strong><small>段结论可能翻转</small></div><div><span>内部翻转</span><strong>'+rows.filter(r=>r.interior_only_flip).length+'</strong><small>两端相同、中间翻转</small></div><div><span>候选声学值</span><strong>'+(rows[0]?.checked_values||0)+'</strong><small>含全部分叉阈值</small></div></div>'+
  (d.approved?'<p class="hint">已批准：简报冻结于 v'+d.revision+'，下载只取这次批准的裁决。</p><button id="download-brief" class="button small">下载待复测简报 CSV ↓</button>':'')+
  '<div class="section-title"><h2>待复测管段</h2><span>'+(d.approved?'批准版本':'当前裁决')+'</span></div>'+
  '<div id="brief-wrap">'+briefTable(d,briefRows)+'</div>'+
  '<div class="section-title"><h2>全部验证样本裁决</h2><span>'+rows.length+' 段</span></div>'+
  table([{key:'segment_id',label:'管段'},{key:'baseline_triage',label:'原分诊'},{key:'possible_text',label:'区间内可能结论'},{key:'risk_text',label:'叶节点漏点率区间(%)'},{key:'acoustic_recorded',label:'原声学读数(dB)'},{key:'retest_required',label:'待复测'},{key:'trigger',label:'翻转触发条件'}],rows.map(r=>({...r,retest_required:r.retest_required?'是':'否'})))+
  '</section>';
}

function briefTable(d,briefRows){
 if(!d.approved)return '<p class="empty">批准后这里展示冻结的待复测清单。</p>';
 if(!briefRows.length)return '<p class="empty">本次批准版本没有可能翻转的管段。</p>';
 return table([{key:'segment_id',label:'待复测管段'},{key:'possible_text',label:'区间内可能结论'},{key:'risk_text',label:'叶节点漏点率区间(%)'},{key:'acoustic_recorded',label:'原声学读数(dB)'},{key:'trigger',label:'触发条件'}],briefRows);
}

export function bindDrills(refresh,routeId){
 const id=Number(routeId)||Number(document.querySelector('[data-drill].selected')?.dataset.drill||document.querySelector('[data-drill]')?.dataset.drill||0);
 document.querySelector('#refresh-drills').onclick=refresh;
 document.querySelectorAll('[data-drill]').forEach(card=>card.onclick=()=>{location.hash='#/drills/'+card.dataset.drill;});
 submit(document.querySelector('#drill-form'),async f=>{const created=await drills.create({name:f.get('name'),job_id:Number(f.get('job_id')),lo:Number(f.get('lo')),hi:Number(f.get('hi'))});notify('演练已提交，后台逐段裁决');location.hash='#/drills/'+created.id;});
 if(!id)return;
 document.querySelector('#retry-drill')?.addEventListener('click',async()=>{try{await drills.retry(id);notify('已恢复原次演练，从已有裁决继续');await refresh();}catch(e){notify(e.message,true);}});
 const reviseForm=document.querySelector('#revise-form');
 document.querySelector('#revise-drill')?.addEventListener('click',()=>{reviseForm.classList.toggle('hidden');});
 if(reviseForm)submit(reviseForm,async f=>{const r=await drills.revise(id,{lo:Number(f.get('lo')),hi:Number(f.get('hi'))});notify('新区间已保存；旧版本作废，不得批准');location.hash='#/drills/'+r.drill.id;});
 document.querySelector('#approve-drill')?.addEventListener('click',async()=>{try{await drills.approve(id);notify('整批裁决已批准，简报可下载');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelector('#download-brief')?.addEventListener('click',()=>{downloadFile('/drills/'+id+'/brief.csv','待复测简报-drill-'+id+'.csv').catch(e=>notify(e.message,true));});
}
