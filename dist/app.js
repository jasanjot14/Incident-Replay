const $ = id => document.getElementById(id);
let selected = 'healthy';
let activeTrace = null;
const config = {
  healthy: {code:200, count:3, title:'Request completed', detail:'All three recommendations made it back to the client.', root:'ok', db:'ok', rec:'ok', fetch:'ok', ms:54},
  slow: {code:200, count:3, title:'Slow dependency found', detail:'The database step dominated the request time.', root:'warning', db:'warning', rec:'ok', fetch:'warning', ms:287},
  database: {code:502, count:0, title:'Database failure', detail:'The recommendation service returned 503 after its database step failed.', root:'error', db:'error', rec:'error', fetch:'error', ms:38},
  empty: {code:200, count:0, title:'Silent empty result', detail:'The transport succeeded, but the recommendation list was empty.', root:'warning', db:'ok', rec:'warning', fetch:'warning', ms:47}
};
const descriptions = {
  healthy: ['Request accepted','Downstream returned 3 results','Query returned 3 recommendations','Response delivered'],
  slow: ['Response completed after a latency spike','Database call took 250 ms','250 ms delay injected before SQLite query','Response delivered with elevated latency'],
  database: ['Gateway returned 502','Downstream HTTP 503','Database unavailable (injected)','Database query failed; returned 503'],
  empty: ['HTTP 200, but no usable recommendations','HTTP 200 but recommendation list is empty','Query returned 0 rows','Returned 0 recommendations; suspicious empty result']
};
function makeDemo(fault) {
  const c = config[fault], start = Date.now(), id = Math.random().toString(16).slice(2, 18).padEnd(16,'0');
  const intervals = fault === 'slow' ? [[0,287],[5,276],[10,260],[7,278]] : [[0,c.ms],[5,c.ms-10],[10,c.ms-20],[7,c.ms-14]];
  const names = ['GET /api/run','fetch recommendations','query recommendations','GET /recommendations'];
  const services = ['api-gateway','api-gateway','sqlite','recommendation-service'];
  const statuses = [c.root,c.fetch,c.db,c.rec];
  const spans = intervals.map(([offset,duration],i) => ({id:`${id}-${i}`,name:names[i],service:services[i],start:(start+offset)/1000,end:(start+offset+duration)/1000,status:statuses[i],detail:descriptions[fault][i],parent:i===0?null:i===1?`${id}-0`:i===2?`${id}-3`:`${id}-1`}));
  return {trace_id:id, scenario:fault, result:c.code===502?{error:'recommendation service failed'}:{recommendations:Array.from({length:c.count},(_,i)=>['The Station','Signal Lost','Paper City'][i])}, trace:{id,spans},demo:true,http_code:c.code};
}
function statusFrom(data) {
  const spans = data.trace.spans;
  return spans.find(s=>s.parent===null) || spans[0];
}
function render(data) {
  activeTrace = data;
  const spans = data.trace.spans, root = statusFrom(data), duration = Math.round((root.end-root.start)*1000), count = data.result.recommendations?.length || 0;
  const fault = data.scenario, c = config[fault];
  $('finding').textContent = c.title;
  $('findingDetail').textContent = c.detail;
  const badge = $('statusBadge');badge.textContent = root.status==='error'?'ACTION NEEDED':root.status==='warning'?'INVESTIGATE':'HEALTHY';badge.className = `badge ${root.status==='error'?'err':root.status==='warning'?'warn':'ok'}`;
  $('httpStatus').textContent = data.http_code || (fault==='database' ? 502 : 200);
  $('duration').textContent = `${duration} ms`;
  $('traceId').textContent = data.trace_id.slice(0,16);
  $('traceId').title = data.trace_id;
  $('count').textContent = String(count);
  $('traceMode').textContent = data.demo ? 'DETERMINISTIC SAMPLE TRACE' : 'RECORDED LOCAL HTTP TRACE';
  const begin = Math.min(...spans.map(s=>s.start));const end = Math.max(...spans.map(s=>s.end));const whole = Math.max(.001,end-begin);
  $('spans').replaceChildren(...spans.map(span=>{
    const button = document.createElement('button');button.className='span-row';button.type='button';
    const name=document.createElement('div');name.className='span-name';const title=document.createElement('strong');title.textContent=span.name;const service=document.createElement('small');service.textContent=span.service;name.append(title,service);
    const lane=document.createElement('div');lane.className='bar-lane';const bar=document.createElement('span');bar.className=`bar ${span.status==='error'?'err':span.status==='warning'?'warn':''}`;bar.style.left=`${100*(span.start-begin)/whole}%`;bar.style.width=`${Math.max(2,100*(span.end-span.start)/whole)}%`;lane.append(bar);
    const status=document.createElement('span');status.className=`span-status ${span.status}`;status.textContent=`${Math.round((span.end-span.start)*1000)} ms`;
    button.append(name,lane,status);button.addEventListener('click',()=>showSpan(span,button));return button;
  }));
  const suspect = spans.find(s=>s.status==='error'&&s.service==='sqlite') || spans.find(s=>s.status==='warning'&&s.name==='fetch recommendations') || spans[0];
  const row = Array.from($('spans').children)[spans.indexOf(suspect)];showSpan(suspect,row);
}
function showSpan(span,row){document.querySelectorAll('.span-row').forEach(r=>r.classList.toggle('active',r===row));$('spanDetail').replaceChildren();const label=document.createElement('span');label.className='small-label';label.textContent=`${span.service.toUpperCase()} / ${span.status.toUpperCase()} / ${Math.round((span.end-span.start)*1000)} MS`;const detail=document.createElement('p');detail.textContent=span.detail||'Completed without an error.';$('spanDetail').append(label,detail);}
document.querySelectorAll('[data-fault]').forEach(button=>button.addEventListener('click',()=>{selected=button.dataset.fault;$('faultName').textContent=selected;document.querySelectorAll('[data-fault]').forEach(b=>b.classList.toggle('chosen',b===button));}));
$('run').addEventListener('click',async()=>{
  $('run').disabled=true;$('run').textContent='Running…';
  try{
    const response=await fetch(`/api/run?fault=${selected}`);
    const type=response.headers.get('content-type')||'';
    if(!type.includes('application/json')) throw new Error('Sample interface');
    const data=await response.json();if(!data.trace) throw new Error('No trace in response');
    data.http_code=response.status;render(data);
  }catch(_){render(makeDemo(selected));}
  finally{$('run').disabled=false;$('run').innerHTML='▶ &nbsp; Run scenario';}
});
