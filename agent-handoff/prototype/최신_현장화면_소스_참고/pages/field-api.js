/* Explicit opt-in: demo data and live UUIDs never share storage or submissions. */
(() => {
  const apiMode = new URLSearchParams(location.search).get('mode') === 'api';
  const $ = s => document.querySelector(s), U = window.DemoUI, esc = window.ALOHA.esc;
  if (!apiMode) {
    const link = document.createElement('a');
    link.href = '02-field-submission.html?mode=api'; link.className = 'field-api-link'; link.textContent = 'API 연결';
    $('.field-return').append(link);
    return;
  }
  document.body.classList.add('field-api-mode', 'field-report-open');
  $('.field-footer').textContent = 'API 연결 모드 · 입력한 인증값은 현재 페이지 메모리에만 보관합니다.';
  $('.field-brand').href = '02-field-submission.html?mode=api';
  $('.field-return').innerHTML = '<a href="02-field-submission.html">시연 화면으로 돌아가기</a>';
  let api, sites = [], site, schedule, home, photo, photoUrl, uploadId, uploaded, submissionId, generation=0;
  const labels = {planned: '보고 전', ai_observing: 'AI 관측 중', field_confirmation_required: '현장 확인 필요',
    rejected: '보완 필요', confirmed: '완료 확인', accepted: '진행 보고 수락', review_pending: '본사 검토 중',
    completion_requested: '완료 확인 요청', observation_review_pending: '본사 AI 검토 중', ai_observation_accepted: 'AI 관측 수락'};
  $('#field-detail').innerHTML = '<p class="eyebrow">API CONNECTION</p><h2>현장 계정으로 연결</h2><p class="field-note">연결된 매장의 실제 공정표와 작업을 불러옵니다.</p><form id="api-connect"><label>API 서버 주소<input name="base" type="url" value="http://127.0.0.1:8001" required></label><label>현장 계정 인증 토큰<input name="token" type="password" autocomplete="off" required></label><button class="field-send">연결하기</button><p class="field-error" role="alert"></p></form>';
  $('#api-connect').onsubmit = async e => {
    e.preventDefault(); const form = e.currentTarget; const button = e.submitter; button.disabled = true;
    try {
      api = new window.ProjectAPI({baseUrl: form.elements.base.value, token: form.elements.token.value});
      sites = await api.sites();
      if (!sites.length) throw Error('연결된 매장이 없습니다. 계정의 매장 권한을 확인해 주세요.');
      form.elements.token.value = '';
      $('.field-return').innerHTML = '<label>매장<select id="api-site"></select></label><label>공정표<select id="api-schedule"></select></label><label>조회 주 · 월요일<input type="date" id="api-week" aria-label="조회 주 시작일"></label><a href="02-field-submission.html">시연으로</a>';
      $('#api-site').innerHTML = sites.map(s => '<option value="'+esc(s.code)+'">'+esc(s.name)+'</option>').join('');
      $('#api-site').onchange = () => loadSchedules().catch(error=>U.toast(error.message)); $('#api-schedule').onchange = loadHome; $('#api-week').onchange=loadHome;
      const requestsButton=document.createElement('button');requestsButton.textContent='요청·대화';requestsButton.type='button';requestsButton.onclick=requestList;$('.field-return').append(requestsButton);
      await loadSchedules();
    } catch (error) { if (form.isConnected) form.querySelector('[role=alert]').textContent = error.message; else U.toast(error.message); }
    finally { button.disabled = false; }
  };
  async function loadSchedules() {
    const current=++generation;site = sites.find(s => s.code === $('#api-site').value);
    home=null;$('#field-tasks').innerHTML='<p class="field-note">공정표를 불러오는 중…</p>';$('#field-stats').innerHTML='';
    $('.field-title h1').textContent=site.name;
    const rows = await api.schedules(site.code);if(current!==generation)return;
    $('#api-schedule').innerHTML = rows.map(r => '<option value="'+r.id+'">'+esc(r.version)+'</option>').join('');
    if (!rows.length) { document.body.classList.add('field-report-open');$('#field-detail').innerHTML = '<h2>'+esc(site.name)+'</h2><p class="field-note">연결된 공정표가 없습니다.</p>'; return; }
    await loadHome();
  }
  async function loadHome() {
    try {
      const current=++generation;schedule = $('#api-schedule').value;
      home=null;$('#field-tasks').innerHTML='<p class="field-note">이번 주 작업을 불러오는 중…</p>';$('#field-stats').innerHTML='';
      const loaded = await api.fieldHome(site.code, schedule, $('#api-week').value);if(current!==generation)return;home=loaded;
      $('.field-title h1').textContent = site.name;
      $('.field-title .eyebrow').textContent = 'FIELD WORKSPACE · API';
      $('.field-week').innerHTML = '<strong>이번 주</strong><span>'+esc(home.period_start)+' — '+esc(home.period_finish)+'</span><small>'+esc(home.timezone)+'</small>';
      $('#field-stats').innerHTML = [['이번 주 작업', home.counts.total], ['내 확인 필요', home.counts.needs_field_action], ['본사 검토 중', home.counts.review_pending]].map(([label,n]) => '<div><span>'+label+'</span><strong>'+n+'<small>개</small></strong></div>').join('');
      renderList('all');
      document.body.classList.remove('field-report-open');
    } catch (error) { document.body.classList.add('field-report-open');$('#field-detail').innerHTML='<h2>작업을 불러오지 못했습니다.</h2><p class="field-note">'+esc(error.message)+'</p><button class="field-send" id="api-retry">다시 불러오기</button>';$('#api-retry').onclick=loadHome; }
  }
  function renderList(filter) {
    const items = home.tasks.filter(t => filter === 'all' || (filter === 'action' ? ['planned','rejected','field_confirmation_required'].includes(t.status) : !!t.latest_submission));
    $('#task-count').textContent = items.length+'개';
    $('#field-tasks').innerHTML = items.map(t => '<button class="field-task" data-api-task="'+t.id+'"><div><span class="field-pill '+(t.status==='confirmed'?'done':t.handoffs.length||t.status==='rejected'?'amber':['completion_requested','review_pending','accepted','ai_observation_accepted'].includes(t.status)?'green':'muted')+'">'+esc(labels[t.status])+'</span><small>ID '+esc(t.source_task_id||'—')+'</small></div><h3>'+esc(t.task_name)+'</h3><p>'+esc(t.planned_start)+' — '+esc(t.planned_finish)+'</p><footer><span>'+(t.continued_from_previous_week?'지난주부터 진행':'이번 주 시작')+'</span><b>'+(t.latest_submission?t.latest_submission.details.progress_percent+'% 보고':'보고하기 →')+'</b></footer></button>').join('') || '<p class="field-note">해당하는 작업이 없습니다.</p>';
    document.querySelectorAll('[data-api-task]').forEach(b => b.onclick = () => openTask(b.dataset.apiTask));
  }
  document.querySelectorAll('[data-filter]').forEach(b => b.onclick = () => {
    if (!home) return;
    document.querySelectorAll('[data-filter]').forEach(x => x.setAttribute('aria-pressed', String(x===b)));
    renderList(b.dataset.filter);
  });
  async function openTask(id) {
    try {
      const current=generation,code=site.code,scheduleId=schedule;const task = await api.taskDetail(code, scheduleId, id, home.period_start);if(current!==generation)return;
      if (photoUrl) URL.revokeObjectURL(photoUrl);
      photo = null; uploaded = null; uploadId = crypto.randomUUID(); submissionId = crypto.randomUUID();
      document.body.classList.add('field-report-open');
      const handoff = task.handoffs[0], last = task.latest_submission;
      $('#field-detail').innerHTML = '<button type="button" id="field-back">← 이번 주 작업 목록</button><div class="field-detail-heading"><div><p class="eyebrow">WORK REPORT · ID '+esc(task.source_task_id||'—')+'</p><h2>'+esc(task.task_name)+'</h2></div><span class="field-pill">'+esc(labels[task.status])+'</span></div><p class="field-meta">계획 '+esc(task.planned_start)+' — '+esc(task.planned_finish)+'</p>'+
        (handoff?'<div class="field-handoff"><strong>AI 탐지 불가 · 현장 자동 이관</strong><b>'+esc(handoff.details.target_key)+'</b><p>'+esc(handoff.body)+'</p></div>':'')+
        (task.status==='rejected'?'<div class="field-rejection"><strong>반려 사유</strong><p>'+esc(task.latest_review.body)+'</p></div>':'')+
        (last?'<div class="field-last"><span>최근 보고 '+last.details.progress_percent+'%</span><button type="button" id="api-history">이력 보기 ↗</button></div>':'')+
        '<form id="api-report"><div class="field-label"><label for="api-photo">01 사진 증빙</label></div><div class="field-photo"><img id="api-preview" alt="제출할 사진" hidden><div id="photo-empty"><span>＋</span><strong>작업 상태를 촬영해 주세요</strong></div></div><div class="field-photo-actions"><label class="field-upload" for="api-photo">사진 촬영 · 선택<input id="api-photo" type="file" accept="image/jpeg,image/png,image/webp" capture="environment"></label></div><label class="api-captured">촬영 시각 · 기기 시간<input type="datetime-local" id="api-captured" required></label><div class="field-label"><label for="progress">02 작업 진행률</label><span>작업 전체 기준 · 기본 100%</span></div><div class="field-progress"><input id="progress" type="number" min="0" max="100" step="any" inputmode="decimal" value="100" required><span>%</span><strong id="progress-label">설치 완료 보고</strong></div><div class="field-quick">'+[0,25,50,75,100].map(n=>'<button type="button" data-percent="'+n+'" aria-pressed="'+(n===100)+'">'+n+'%</button>').join('')+'</div><div class="field-label"><label for="reason">03 작업 내용 · 남은 작업</label><span id="reason-hint">선택</span></div><textarea id="reason" rows="3" maxlength="4000"></textarea><p class="field-error" role="alert"></p><button class="field-send" id="api-send">100% 완료 확인 요청 보내기</button><p class="field-note">100% 보고는 본사 확인 후 완료 처리됩니다.</p></form>';
      $('#field-back').onclick = () => { document.body.classList.remove('field-report-open'); };
      const changeButton=document.createElement('button');changeButton.type='button';changeButton.className='field-change-button';changeButton.textContent='필요하면 일정·작업 조정 요청';changeButton.onclick=()=>composeChange(code,scheduleId,task);$('#api-report').append(changeButton);
      const local = new Date(); local.setMinutes(local.getMinutes()-local.getTimezoneOffset()); $('#api-captured').value = local.toISOString().slice(0,16);
      $('#api-history')?.addEventListener('click', async () => {
        try { const rows = await api.history(code,scheduleId,id); U.dialog('작업 이력', '<div class="history-events">'+rows.items.map(e=>'<p><b>'+esc(e.event_type)+'</b> '+esc(e.body||'')+' '+(e.details.progress_percent!==undefined?e.details.progress_percent+'%':'')+'</p>').join('')+'</div>'); } catch(error) { U.toast(error.message); }
      });
      $('#api-photo').onchange = async e => {
        const selected = e.target.files[0]; if (!selected) return;
        const send = $('#api-send'); send.disabled = true;
        try {
          const data = await U.photoFile(selected);
          const blob = await (await fetch(data)).blob();
          photo = new File([blob], 'evidence.jpg', {type:'image/jpeg'});
          if (photoUrl) URL.revokeObjectURL(photoUrl);
          photoUrl = URL.createObjectURL(photo); $('#api-preview').src = photoUrl; $('#api-preview').hidden = false; $('#photo-empty').hidden = true;
          uploaded = null; uploadId = crypto.randomUUID(); submissionId = crypto.randomUUID();
        } catch(error) { $('#api-report [role=alert]').textContent = error.message; } finally { send.disabled = false; }
      };
      const progress = $('#progress');
      function updateProgress() {
        const done = progress.valueAsNumber === 100;
        $('#reason').required = !done; $('#reason-hint').textContent = done?'선택':'남은 작업 사유 필수';
        $('#progress-label').textContent = done?'설치 완료 보고':'진행 중 보고';
        $('#api-send').textContent = done?'100% 완료 확인 요청 보내기':'진행률 '+progress.value+'% 보고 보내기';
        document.querySelectorAll('[data-percent]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.percent)===progress.valueAsNumber)));
        submissionId = crypto.randomUUID();
      }
      progress.oninput = updateProgress;
      document.querySelectorAll('[data-percent]').forEach(b=>b.onclick=()=>{progress.value=b.dataset.percent;updateProgress();});
      $('#reason').oninput = () => { submissionId=crypto.randomUUID(); };
      $('#api-captured').oninput = () => { submissionId=crypto.randomUUID(); };
      if (site.role!=='field') { $('#api-send').disabled=true; $('#api-report [role=alert]').textContent='현장 담당자 계정으로 연결하면 제출할 수 있습니다.'; }
      $('#api-report').onsubmit = async e => {
        e.preventDefault(); const button=$('#api-send'); button.disabled=true; $('#api-report [role=alert]').textContent='';
        try {
          if (!photo) throw Error('증빙 사진을 선택해 주세요.');
          uploaded ||= await api.upload(code, photo, {id:uploadId});
          await api.submit(code,scheduleId,id,{id:submissionId,photo_file_ids:[uploaded.id],progress_percent:progress.valueAsNumber,
            captured_at:new Date($('#api-captured').value).toISOString(),body:$('#reason').value,observation_id:handoff?.observation_id||null});
          U.toast('사진과 진행률을 본사에 보냈습니다.'); await loadHome();
        } catch(error) { $('#api-report [role=alert]').textContent=error.message; } finally { button.disabled=false; }
      };
      window.scrollTo({top:0,behavior:'smooth'});
    } catch(error) { U.toast(error.message); }
  }
  async function attachments(input, cache, code) {
    const files=Array.from(input.files);
    if(files.length>5)throw Error('첨부파일은 5개까지 선택하세요.');
    const ids=[];
    for(const file of files){
      if(!cache.has(file))cache.set(file,{id:crypto.randomUUID(),result:null});
      const item=cache.get(file);item.result ||= await api.upload(code,file,{id:item.id,purpose:'attachment'});ids.push(item.result.id);
    }
    return ids;
  }
  async function composeChange(code,scheduleId,task) {
    try {
      const recipients=await api.participants(code);
      if(!recipients.length)throw Error('변경 요청을 보낼 상대 회사가 연결되어 있지 않습니다.');
      const d=U.dialog('일정·작업 조정 요청','<form id="api-change" class="api-change"><label>상대 회사<select name="recipient">'+recipients.map(r=>'<option value="'+r.id+'">'+esc(r.name)+'</option>').join('')+'</select></label><label>요청 구분<select name="kind"><option value="extension">추가 기간 요청</option><option value="schedule">일정 변경</option><option value="work">작업 변경</option></select></label><p class="field-note">현재 계획 '+esc(task.planned_start)+' — '+esc(task.planned_finish)+'</p><div id="api-dates"><label>요청 시작일<input type="date" name="start" value="'+esc(task.planned_start)+'"></label><label>요청 종료일<input type="date" name="finish" value="'+esc(task.planned_finish)+'"></label></div><label id="api-work" hidden>변경할 작업 내용<textarea name="work" maxlength="4000"></textarea></label><label>변경 사유<textarea name="reason" required maxlength="4000" placeholder="남은 작업, 지연 원인, 필요한 기간과 조치 계획을 적어 주세요."></textarea></label><label>관련 파일<input name="files" type="file" multiple accept=".jpg,.jpeg,.png,.webp,.pdf,.docx,.xlsx,.txt"></label><small>최대 5개 · 파일당 10MB</small><p role="alert" class="field-error"></p><button class="field-send">상대 회사에 요청 보내기</button></form>');
      const form=d.el.querySelector('form'),id=crypto.randomUUID(),cache=new Map();
      form.elements.kind.onchange=()=>{const work=form.elements.kind.value==='work';d.el.querySelector('#api-dates').hidden=work;d.el.querySelector('#api-work').hidden=!work;form.elements.work.required=work;};
      form.onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{
        const kind=form.elements.kind.value;
        const body={id,recipient_org_id:form.elements.recipient.value,request_type:kind,expected_start:task.planned_start,expected_finish:task.planned_finish,reason:form.elements.reason.value,
          file_ids:await attachments(form.elements.files,cache,code),proposed_work:kind==='work'?form.elements.work.value:null,
          proposed_start:kind==='schedule'?form.elements.start.value:null,proposed_finish:kind==='work'?null:form.elements.finish.value};
        const created=await api.createChange(code,scheduleId,task.id,body);d.close();await requestThread(code,scheduleId,created.id);
      }catch(error){form.querySelector('[role=alert]').textContent=error.message;button.disabled=false;}};
    } catch(error){U.toast(error.message);}
  }
  async function requestList(){
    if(!site||!schedule)return;
    const code=site.code,scheduleId=schedule;
    try{
      const data=await api.changeRequests(code,scheduleId);
      const d=U.dialog('일정·작업 요청과 대화','<div class="api-request-list">'+(data.items.map(r=>'<button data-request="'+r.id+'"><strong>'+esc(r.task_name)+' · '+esc({extension:'추가 기간 요청',schedule:'일정 변경',work:'작업 변경'}[r.request_type])+'</strong><span>'+esc(r.reason)+'</span><small>'+esc({pending:'응답 대기',applied:'수락·적용 완료',rejected:'반려',accepted:'수락'}[r.status])+'</small></button>').join('')||'<p>등록된 요청이 없습니다.</p>')+'</div>');
      d.el.querySelectorAll('[data-request]').forEach(b=>b.onclick=()=>{d.close();requestThread(code,scheduleId,b.dataset.request);});
    }catch(error){U.toast(error.message);}
  }
  async function requestThread(code,scheduleId,id){
    try{
      const item=await api.requestDetail(code,scheduleId,id),cache=new Map(),messageId=crypto.randomUUID();
      const d=U.dialog('변경 요청 · 대화','<div class="api-thread"><h3>'+esc(item.task_name)+'</h3><p>'+esc(item.reason)+'</p><p class="field-note">'+esc(item.proposed_work||((item.proposed_start||'')+' → '+(item.proposed_finish||'')))+'</p>'+item.messages.map(m=>'<article><strong>'+esc({message:'메시지',request_accept:'요청 수락',request_reject:'요청 반려',schedule_apply:'일정 반영',work_apply:'작업 반영'}[m.event_type]||m.event_type)+'</strong><small>'+esc(m.occurred_at)+'</small><p>'+esc(m.body||'')+'</p>'+m.files.map(f=>'<button data-download="'+f.id+'" data-name="'+esc(f.filename)+'">📎 '+esc(f.filename)+'</button>').join('')+'</article>').join('')+'<form id="api-message"><label>답변<textarea name="body" maxlength="4000"></textarea></label><label>파일 첨부<input name="files" type="file" multiple accept=".jpg,.jpeg,.png,.webp,.pdf,.docx,.xlsx,.txt"></label><p class="field-error" role="alert"></p><button class="field-send">답변 보내기</button></form>'+(item.can_decide?'<div class="api-decisions"><label>결정 사유 · 반려 시 필수<textarea id="api-decision-reason" maxlength="4000"></textarea></label><button data-decide="reject">반려</button><button data-decide="accept">수락하고 반영</button></div>':'')+'</div>');
      d.el.querySelectorAll('[data-download]').forEach(b=>b.onclick=async()=>{try{const blob=await api.file(code,b.dataset.download),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=b.dataset.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){U.toast(error.message);}});
      const form=d.el.querySelector('form');form.onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{await api.message(code,scheduleId,id,{id:messageId,body:form.elements.body.value,file_ids:await attachments(form.elements.files,cache,code)});d.close();requestThread(code,scheduleId,id);}catch(error){form.querySelector('[role=alert]').textContent=error.message;button.disabled=false;}};
      const decisionId=crypto.randomUUID();d.el.querySelectorAll('[data-decide]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api.decide(code,scheduleId,id,{id:decisionId,outcome:b.dataset.decide,body:d.el.querySelector('#api-decision-reason').value});d.close();await loadHome();requestThread(code,scheduleId,id);}catch(error){U.toast(error.message);b.disabled=false;}});
    }catch(error){U.toast(error.message);}
  }
})();
