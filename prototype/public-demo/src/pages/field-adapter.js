(() => {
  const A=window.DEMO,U=window.DemoUI,$=s=>document.querySelector(s),id=new URLSearchParams(location.search).get('task')||'39',task=A.effectiveTasks().find(t=>t.id===id)||A.effectiveTasks().find(t=>t.id==='39');
  const taskId=task.id,r=A.read().reviews[taskId],last=A.latest(taskId);let photo=last?.photo||(taskId==='40'?window.DEMO_DATA.photos.counterAfter:taskId==='39'?window.DEMO_DATA.photos.lighting:'');
  $('.heading h1').textContent=task.name;$('.heading .tag').textContent=r?.state==='rejected'?'반려 · 보완 필요':r?.attempt?'제출 '+r.attempt+'차':'증빙 제출';$('.meta').textContent='ID '+taskId+' · 계획 '+task.start.slice(5).replace('-','.')+' - '+task.originalFinish.slice(5).replace('-','.');
  $('.crumb').textContent='가상 현장 A 매장 / 제7주 · '+(task.start<'2026-02-16'?'지난주부터 진행':'이번 주 시작');
  const note=$('#rejection-reason');if(r?.reason){note.innerHTML='<div class="review-return-head"><strong>반려 사유</strong><span>본사 검토</span></div><span></span>';note.lastElementChild.textContent=r.reason;}else note.hidden=true;
  $('#evidence').src=photo||'';$('#evidence').hidden=!photo;$('#evidence').alt='현장 제출 증빙 사진';if(!photo)$('.photo figcaption').textContent='설치 상태가 보이는 사진을 선택해 주세요.';
  $('#reason').value=last?.report||'';completed=last?.completed??false;refresh();
  $('.date strong').textContent=task.originalFinish.replaceAll('-','.');const next=new Date(task.originalFinish+'T00:00:00Z');next.setUTCDate(next.getUTCDate()+1);$('#requested-end').min=next.toISOString().slice(0,10);if($('#requested-end').value<=task.originalFinish)$('#requested-end').value=next.toISOString().slice(0,10);
  const top=document.createElement('div');top.className='field-return';top.innerHTML='<a href="01-hq-review.html?task='+taskId+'">← 본사 검토 화면</a><button class="plain-history">사진·검토 이력</button>';$('.crumb').before(top);top.querySelector('button').onclick=()=>U.history(taskId);
  $('.header .badge').textContent=r?.state==='rejected'?'1':'0';
  $('#photo-input').removeAttribute('capture');
  $('#photo-input').onchange=async e=>{try{photo=await U.photoFile(e.target.files[0]);$('#evidence').src=photo;$('#evidence').hidden=false;$('.label small').textContent='선택한 사진';U.toast('사진을 추가했습니다. 제출하면 저장합니다.');}catch(err){U.toast(err.message);}e.target.value='';};
  $('#submit').onclick=()=>{const request=$('#request-enabled').checked;try{A.submit(taskId,{photo,report:$('#reason').value,completed,extension:request?{end:$('#requested-end').value,reason:$('#extension-reason').value}:null});$('.heading .tag').textContent='본사 검토 대기';$('.heading .tag').className='tag request';$('.header .badge').textContent='0';U.toast(request?'사진과 추가 기간 요청을 제출했습니다. 본사 검토를 기다립니다.':'증빙을 제출했습니다. 본사 검토를 기다립니다.');const status=$('.footer');status.textContent='제출 완료 · 본사 검토 화면에서 사진과 요청을 확인할 수 있습니다.';}catch(err){U.toast(err.message);}};
})();
