(() => {
  if(new URLSearchParams(location.search).get('mode')==='api')return;
  const A=window.ALOHA,U=window.DemoUI,esc=A.esc,$=s=>document.querySelector(s);
  let selected=new URLSearchParams(location.search).get('task'),filter='all',photo='',uploading=false;
  // shortcut: this observation is a demo fixture, replace with the AI result feed when connected.
  try{if(!A.read().fieldHandoffs?.['45'])A.handoffUnobservable('45',{target:'벽체 W-01 · 계산대 뒤쪽',reason:'자재와 작업 발판에 가려 벽체 설치 상태를 판단할 수 없습니다.',observationId:'demo-example-w7-wall-01'});}catch(err){U.toast(err.message);}
  const tasks=()=>A.weekTasks(7);
  const stamp=v=>v?.slice(5,16).replace('T',' ')||'';
  function status(id){
    const s=A.read(),r=s.reviews[id],h=s.fieldHandoffs?.[id],sub=A.latest(id);
    if(r?.state==='rejected')return {text:'보완 필요',tone:'amber',action:true};
    if(r?.state==='confirmed')return {text:'완료 확인',tone:'done',submitted:true};
    if(h?.state==='requested')return {text:'현장 확인 필요',tone:'amber',action:true};
    if(sub&&sub.status!=='reference')return {text:r?.state==='request'?'완료 확인 요청':r?.state==='accepted'?'진행 보고 수락':'본사 검토 중',tone:'green',submitted:true};
    if(A.classifiedSource(A.category(A.effectiveTasks().find(t=>t.id===id)))==='observation')return {text:'AI 관측 중',tone:'muted'};
    return {text:'보고 전',tone:'muted',action:true};
  }
  function renderList(){
    const all=tasks(),statuses=all.map(t=>status(t.id));
    $('#field-stats').innerHTML=[['이번 주 작업',all.length,'개'],['내 확인 필요',statuses.filter(s=>s.action).length,'개'],['보고 완료',statuses.filter(s=>s.submitted).length,'개']].map(x=>'<div><span>'+x[0]+'</span><strong>'+x[1]+'<small>'+x[2]+'</small></strong></div>').join('');
    const list=all.filter(t=>filter==='all'||status(t.id)[filter]);
    const priority=id=>A.read().fieldHandoffs?.[id]?.state==='requested'?0:A.read().reviews[id]?.state==='rejected'?1:status(id).action?2:3;
    list.sort((a,b)=>priority(a.id)-priority(b.id));
    $('#task-count').textContent=list.length+'개';
    $('#field-tasks').innerHTML=list.map(t=>{
      const st=status(t.id),sub=A.latest(t.id),h=A.read().fieldHandoffs?.[t.id];
      return '<button class="field-task '+(t.id===selected?'active':'')+'" data-task="'+esc(t.id)+'" aria-pressed="'+(t.id===selected)+'"><div><span class="field-pill '+st.tone+'">'+st.text+'</span><small>ID '+esc(t.id)+'</small></div><h3>'+esc(t.name)+'</h3><p>'+t.start.slice(5).replace('-','.')+' — '+t.plannedFinish.slice(5).replace('-','.')+(t.start<'2025-02-17'?' · 지난주부터 진행':' · 이번 주 시작')+'</p><footer><span>'+(h?'AI 탐지 불가 → 현장 확인':A.classifiedSource(A.category(t))==='observation'?'AI 관측 대상':'작업별 증빙 제출')+'</span><b>'+(sub?.progressPercent!==undefined?sub.progressPercent+'% 보고':sub?.completed?'100% 보고':'보고하기 →')+'</b></footer></button>';
    }).join('')||'<p class="field-note">해당하는 작업이 없습니다.</p>';
    $('#field-tasks').querySelectorAll('[data-task]').forEach(b=>b.onclick=()=>{openTask(b.dataset.task);if(matchMedia('(max-width:760px)').matches)$('#field-detail').scrollIntoView({behavior:'smooth'});});
  }
  function openTask(id){
    const t=tasks().find(t=>t.id===id);if(!t){U.toast('이번 주 작업 목록에서 선택해 주세요.');return;}
    selected=id;photo='';document.body.classList.add('field-report-open');window.scrollTo({top:0,behavior:'smooth'});const url=new URL(location.href);url.searchParams.set('task',id);history.replaceState(null,'',url);
    const s=A.read(),r=s.reviews[id],h=s.fieldHandoffs?.[id],last=A.latest(id),st=status(id),info=A.taskInfo(id);
    const example=info.photo||window.ALOHA_DATA.photos.counterAfter;
    $('#field-detail').innerHTML='<button type="button" id="field-back">← 이번 주 작업 목록</button><div class="field-detail-heading"><div><p class="eyebrow">WORK REPORT · ID '+esc(id)+'</p><h2>'+esc(t.name)+'</h2></div><span class="field-pill '+st.tone+'">'+st.text+'</span></div><p class="field-meta">계획 '+t.start.replaceAll('-','.')+' — '+t.plannedFinish.replaceAll('-','.')+'</p>'+
      (h?'<div class="field-handoff"><strong>AI 탐지 불가 · 현장에 자동 이관</strong><b>'+esc(h.target)+'</b><p>'+esc(h.reason)+'</p><small>'+stamp(h.at)+' 요청 · '+(h.state==='submitted'?'현장 보고 제출됨':'해당 위치를 확인하고 사진과 진행률을 보내 주세요.')+'</small></div>':'')+
      (r?.state==='rejected'?'<div class="field-rejection"><strong>반려 사유</strong><p>'+esc(r.reason)+'</p></div>':'')+
      (last&&last.status!=='reference'?'<div class="field-last"><span>최근 보고 '+(last.progressPercent??(last.completed?100:0))+'% · '+stamp(last.at)+'</span><button type="button" id="field-history">이력 보기 ↗</button></div>':'')+
      '<form id="field-form"><div class="field-label"><label for="photo-input">01 사진 증빙</label><span>작업 상태가 보이는 사진 1장</span></div><div class="field-photo"><img id="evidence" alt="제출할 작업 증빙 사진" hidden><div id="photo-empty"><span>＋</span><strong>사진을 추가해 주세요</strong><small>촬영하거나 저장한 사진을 선택하세요.</small></div></div><div class="field-photo-actions"><label class="field-upload" for="photo-input">사진 촬영 · 선택<input type="file" id="photo-input" accept="image/jpeg,image/png,image/webp" capture="environment"></label><button type="button" id="use-example">예시 사진으로 시연</button></div><small id="photo-state">새 사진을 선택해야 제출할 수 있습니다.</small><div class="field-label"><label for="progress">02 작업 진행률</label><span>작업 전체 기준 · 기본 100%</span></div><div class="field-progress"><input id="progress" type="number" min="0" max="100" step="any" value="100" required inputmode="decimal"><span>%</span><strong id="progress-label">설치 완료 보고</strong></div><div class="field-quick" role="group" aria-label="진행률 간편 선택">'+[0,25,50,75,100].map(n=>'<button type="button" data-progress="'+n+'" aria-pressed="'+(n===100)+'">'+n+'%</button>').join('')+'</div><div class="field-label"><label for="reason">03 작업 내용 · 남은 작업</label><span id="reason-hint">선택</span></div><textarea id="reason" rows="3" maxlength="2000" placeholder="필요한 내용만 간단히 적어 주세요."></textarea><section class="field-extension"><label><input type="checkbox" id="request-enabled"> 필요하면 추가 기간 요청</label><div id="extension-fields" hidden><label for="requested-end">요청 종료일</label><input type="date" id="requested-end" min="'+new Date(Date.parse(t.plannedFinish)+86400000).toISOString().slice(0,10)+'"><label for="extension-reason">추가 기간 요청 사유</label><textarea id="extension-reason" rows="3" maxlength="2000" placeholder="남은 작업, 지연 사유와 필요한 기간을 적어 주세요."></textarea><small>본사 수락 후 일정에 반영됩니다.</small></div></section><p id="field-error" role="alert"></p><button class="field-send" id="submit">100% 완료 확인 요청 보내기</button><p class="field-note">100% 보고는 본사 확인 후 완료 처리됩니다.<br>제출한 사진과 진행률은 이력으로 남습니다.</p><div id="field-success" role="status"></div></form>';
    renderList();$('#field-back').onclick=()=>{document.body.classList.remove('field-report-open');selected=null;const url=new URL(location.href);url.searchParams.delete('task');history.replaceState(null,'',url);renderList();window.scrollTo({top:0,behavior:'smooth'});};$('#field-history')?.addEventListener('click',()=>U.history(id));
    const progress=$('#progress');
    function update(){const value=progress.valueAsNumber,valid=Number.isFinite(value)&&value>=0&&value<=100,done=value===100;$('#progress-label').textContent=!valid?'0~100 사이로 입력해 주세요':done?'설치 완료 보고':'진행 중 보고';$('#reason').required=!done;$('#reason-hint').textContent=done?'선택':'남은 작업 사유 필수';$('#submit').textContent=done?'100% 완료 확인 요청 보내기':'진행률 '+(valid?value:'—')+'% 보고 보내기';document.querySelectorAll('[data-progress]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.progress)===value)));}
    progress.oninput=update;document.querySelectorAll('[data-progress]').forEach(b=>b.onclick=()=>{progress.value=b.dataset.progress;update();});
    function preview(src,label){photo=src;$('#evidence').src=src;$('#evidence').hidden=false;$('#photo-empty').hidden=true;$('#photo-state').textContent=label;}
    $('#use-example').onclick=()=>preview(example,'시연용 예시 사진 · 실제 제출 자료가 아닙니다.');
    $('#photo-input').onchange=async e=>{const file=e.target.files[0];if(!file)return;uploading=true;$('#submit').disabled=true;$('#field-error').textContent='';try{preview(await U.photoFile(file),'선택한 사진 · '+file.name);}catch(err){$('#field-error').textContent=err.message;}finally{uploading=false;$('#submit').disabled=false;}};
    $('#request-enabled').onchange=e=>{const enabled=e.target.checked;$('#extension-fields').hidden=!enabled;$('#requested-end').required=enabled;$('#extension-reason').required=enabled;};
    $('#field-form').onsubmit=e=>{e.preventDefault();if(uploading)return;$('#field-error').textContent='';try{
      A.submit(id,{photo,progressPercent:progress.valueAsNumber,report:$('#reason').value,extension:$('#request-enabled').checked?{end:$('#requested-end').value,reason:$('#extension-reason').value}:null});
      openTask(id);$('#field-success').textContent='보고를 보냈습니다. 본사 검토 결과를 기다려 주세요.';U.toast('사진과 진행률을 본사에 보냈습니다.');
    }catch(err){$('#field-error').textContent=err.message;}};
  }
  document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));renderList();});
  renderList();if(selected)openTask(selected);
})();
