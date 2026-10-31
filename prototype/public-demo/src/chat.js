(() => {
  const A=window.DEMO,$=s=>document.querySelector(s),widget=$('#chat-widget'),launcher=$('#chat-launcher');
  const currentWeek=()=>Math.max(1,Math.min(16,Number($('#report-week')?.value||$('.week.active')?.dataset.week||new URLSearchParams(location.search).get('week')||7)));
  function ask(question){if(!question.trim())return;$('#chat-question').textContent=question;const s=A.read(),week=currentWeek();let answer='';
    if(/다음|차주/.test(question)){const next=A.weekTasks(week+1);answer='<strong>제'+(week+1)+'주 예정 작업 '+next.length+'건입니다.</strong><br><br>'+next.map(t=>A.esc(t.name)+(s.extensions[t.id]?.state==='approved'?' · 지연·이월':'')).join('<br>')+'<br><a href="00-site-overview.html?week='+(week+1)+'">제'+(week+1)+'주 간트 보기 →</a>';}
    else if(/기간|지연|이월/.test(question)){answer='<strong>추가 기간 요청 현황</strong><br><br>'+Object.entries(s.extensions).map(([id,e])=>{const t=A.effectiveTasks().find(t=>t.id===id);return A.esc(t.name)+' · ID '+id+'<br>'+e.original+' → '+e.end+'<br>'+A.esc(e.reason)+'<br>'+({pending:'본사 검토 대기',approved:'수락 · 지연·이월',rejected:'반려'}[e.state]);}).join('<br><br>')+'<br><a href="00-site-overview.html">간트와 기간 요청 보기 →</a>';}
    else if(/공정|벽|분석|AI|관측/i.test(question)){answer='<strong>벽체 보드 설치 · ID 45</strong><br><br>왼쪽 벽 W01 78.50%<br>오른쪽 벽 W02 82.00%<br><br>설치 ÷ (설치 + 미설치)로 계산하며 인식 불가는 제외합니다.<br>현재 상태: '+A.esc(A.labels[s.reviews['45'].state])+'<br>관측 결과 수락과 작업 완료 확인은 구분합니다.<br><a href="01-hq-review.html?task=45">원본과 분석 표시 보기 →</a>';}
    else if(/검토|확인|이번|보고|사진|작업/.test(question)){const pending=Object.entries(s.reviews).filter(([,r])=>['pending','request'].includes(r.state));answer='<strong>본사 확인이 필요한 항목 '+A.counts().site_a+'건입니다.</strong><br><br>'+pending.map(([id,r])=>A.esc(A.effectiveTasks().find(t=>t.id===id).name)+' · '+A.labels[r.state]+'<br><a href="01-hq-review.html?task='+id+'">ID '+id+' 증빙 보기 →</a>').join('<br><br>')+Object.entries(s.extensions).filter(([,e])=>e.state==='pending').map(([id])=>'<br><br>'+A.esc(A.effectiveTasks().find(t=>t.id===id).name)+' · 추가 기간 요청<br><a href="00-site-overview.html">기간 요청 검토 →</a>').join('');if(!A.counts().site_a)answer+='<br>현재 대기 중인 항목이 없습니다.';}
    else answer='이 프로토타입에서는 저장된 시연 자료를 바탕으로 답합니다.<br><br>“이번 주 검토할 작업”, “추가 기간 요청”, “다음 주 할 일”, “벽체 공정률”을 물어보세요.';
    $('#chat-answer').innerHTML=answer;$('#chat-input').value='';document.querySelector('.chat-messages').scrollTop=0;
  }
  launcher.onclick=()=>{widget.hidden=false;launcher.hidden=true;ask('이번 주 검토할 작업은?');$('#chat-input').focus();};$('#chat-close').onclick=()=>{widget.hidden=true;launcher.hidden=false;launcher.focus();};
  $('#chat-form').onsubmit=e=>{e.preventDefault();ask($('#chat-input').value);};document.querySelectorAll('[data-chat-question]').forEach(b=>b.onclick=()=>ask(b.dataset.chatQuestion));
  $('.chat-disclaimer').textContent='시연 데이터 기반 응답 · 실제 AI 연결 전';$('.chat-context').innerHTML='<b>현재 시연 자료</b>가상 현장 A 제'+currentWeek()+'주 · 사진 · 검토 상태';$('.chat-head small').textContent='가상 현장 A 매장 · 시연 도우미';
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!widget.hidden){widget.hidden=true;launcher.hidden=false;}});
})();
