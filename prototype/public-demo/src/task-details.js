(() => {
  const A=window.DEMO,U=window.DemoUI,esc=A.esc,short=at=>at?at.slice(5,16).replace('T',' ').replace('-','.'):'아직 없음';
  function status(info){
    if(info.confirmed)return {key:'confirmed',label:'완료 확인',tone:'confirmed'};
    if(info.review){const key=info.review.state==='pending'?(info.task.id==='45'?'observation':'pending'):info.review.state;return {key,label:key==='observation'?'AI 관측 검토':key==='pending'?'증빙 검토 대기':A.labels[key],tone:A.tone(info.review.state)};}
    if(info.extension?.state==='pending')return {key:'extension',label:'추가 기간 요청',tone:'amber'};
    return {key:info.late?'late':'planned',label:info.late?'일정 확인 필요':'계획 작업',tone:info.late?'amber':'neutral'};
  }
  function timeline(info,limit=8,brief=false){return '<ol class="task-timeline">'+info.timeline.slice(-limit).map(e=>'<li><i></i><div><time>'+short(e.at)+'</time><b>'+esc(e.label)+'</b>'+(!brief&&e.note?'<p>'+esc(e.note)+'</p>':'')+'</div></li>').join('')+'</ol>';}
  function facts(info){const t=info.task;return '<dl class="task-facts"><div><dt>계획 시작</dt><dd>'+t.start.slice(5).replace('-','.')+'</dd></div><div><dt>계획 종료</dt><dd>'+t.originalFinish.slice(5).replace('-','.')+'</dd></div>'+(info.confirmed?'<div><dt>완료일</dt><dd>'+t.actualFinish?.slice(5).replace('-','.')+'</dd></div>':'')+'<div><dt>최근 본사 검토</dt><dd>'+short(info.review?.reviewedAt||info.extension?.reviewedAt)+'</dd></div></dl>';}
  function panel(id){const info=A.taskInfo(id);if(!info)return '';const s=status(info);return '<div class="task-panel-kicker">선택한 작업 · ID '+id+'</div><div class="task-panel-heading"><h3>'+esc(info.task.name)+'</h3><span class="tag '+s.tone+'">'+s.label+'</span></div>'+facts(info)+(info.photo?'<figure class="photo panel-photo"><img src="'+esc(info.photo)+'" alt="'+esc(info.task.name)+' 시연용 증빙 사진"><figcaption>'+(info.examplePhoto?'AI 생성 참고 사진 · 실제 증빙 아님':'최근 연결 사진 · 시연 예시')+'</figcaption></figure>':'<p class="no-photo">아직 연결된 증빙 사진이 없습니다.</p>')+'<section class="task-panel-history"><h4>작업·검토 이력</h4>'+timeline(info,5)+'</section>'+(info.extension?'<section class="panel-extension">'+U.extensionHTML(id)+'</section>':'');}
  window.TaskDetails={status,timeline,facts,panel,short};
})();
