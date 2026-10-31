(() => {
  const A=window.DEMO;
  const examples={
    'site-b':{name:'가상 현장 B',total:62,week:6,completed:18,weekly:7,example:true,detail:false,pending:[
      {id:'cm-lighting',name:'피팅룸 조명',kind:'완료 확인 요청',tone:'request'},
      {id:'cm-inspection',name:'스프링클러 검사',kind:'추가 기간 요청',tone:'amber',extension:true,original:'02.13',end:'02.16',reason:'검사 일정 조율'}
    ]},
    'site-c':{name:'가상 현장 C',total:54,week:9,completed:24,weekly:5,example:true,detail:false,pending:[
      {id:'lv-ceiling',name:'매장 천장 마감',kind:'AI 관측 결과 검토',tone:'neutral'}
    ]}
  };
  function pending(){const s=A.read(),tasks=A.effectiveTasks();return [
    ...Object.entries(s.reviews).filter(([,r])=>['request','pending'].includes(r.state)).map(([id,r])=>({id,name:tasks.find(t=>t.id===id)?.name||'작업 '+id,kind:id==='45'?'AI 관측 결과 검토':A.labels[r.state],tone:id==='45'?'neutral':A.tone(r.state),url:'01-hq-review.html?task='+id})),
    ...Object.entries(s.extensions).filter(([,e])=>e.state==='pending').map(([id,e])=>({id,name:tasks.find(t=>t.id===id)?.name||'작업 '+id,kind:'추가 기간 요청',tone:'amber',extension:true,original:e.original.slice(5).replace('-','.'),end:e.end.slice(5).replace('-','.'),reason:e.reason,url:'00-site-overview.html?request='+id}))
  ];}
  function get(id){
    if(id==='site_a')return {id,name:'가상 현장 A',total:A.effectiveTasks().length,week:7,completed:Object.values(A.read().reviews).filter(r=>r.state==='confirmed').length,weekly:A.weekTasks(7).length,pending:pending(),detail:true,example:false};
    if(examples[id])return {id,...structuredClone(examples[id])};
    const site=window.DEMO_SITE_CATALOG?.find(s=>s.id===id)||A.read().sites.find(s=>s.id===id);return {id,name:site?.name||'매장',total:0,week:null,completed:0,weekly:0,pending:[],detail:false,example:false};
  }
  function hasDetail(id){return id==='site_a';}
  function canReview(id){return !!A.read().reviews[id]||!!A.read().extensions[id];}
  function dashboard(id){const s=get(id),esc=A.esc,extensions=s.pending.filter(p=>p.extension);const completed=Object.entries(A.read().reviews).find(([,r])=>r.state==='confirmed')?.[0];
    const metrics=[['전체 작업',s.total,'개','00-site-overview.html?view=all'],['현재 주차',s.week?'제'+s.week+'주':'—','','00-site-overview.html?week='+s.week],['완료 확인',s.completed,'개','01-hq-review.html?task='+completed],['이번 주 작업',s.weekly,'개','00-site-overview.html?week='+s.week],['검토 필요',s.pending.length,'건',s.pending[0]?.url],['추가 기간 요청',extensions.length,'건',extensions[0]?.url]];
    return '<div class="summary-heading"><div><span class="eyebrow">STORE OVERVIEW</span><h2>'+esc(s.name)+' 전체 관리</h2></div><span class="summary-source">'+(s.example?'홈·메뉴용 가상 요약 · 세부 자료 없음':s.detail?'가상 현장 A 가상 공정표 · 제7주 시연':'공정표·사진 연결 전')+'</span></div><div class="summary-metrics">'+metrics.map(([label,value,unit,href])=>{const enabled=s.detail&&!!value&&!!href;return '<'+(enabled?'a href="'+esc(href)+'"':'button disabled')+' class="summary-metric"><small>'+label+'</small><strong>'+value+'<span>'+unit+'</span></strong></'+(enabled?'a':'button')+'>';}).join('')+'</div><div class="summary-detail-grid"><section class="summary-pending"><div class="summary-section-title"><h3>검토할 항목</h3><span>'+s.pending.length+'건</span></div>'+(s.pending.length?s.pending.map(p=>'<'+(p.url&&s.detail?'a href="'+esc(p.url)+'"':'div')+' class="pending-row"><span class="pending-dot '+p.tone+'"></span><div><strong>'+esc(p.name)+'</strong><small>'+esc(p.kind)+'</small></div><span class="pending-trail">'+(s.detail?'↗':'예시')+'</span></'+(p.url&&s.detail?'a':'div')+'>').join(''):'<p class="summary-empty">검토할 항목이 없습니다.</p>')+'</section><section class="summary-extension"><div class="summary-section-title"><h3>추가 기간 요청</h3><span>'+extensions.length+'건</span></div>'+(extensions.length?extensions.map(p=>'<div class="extension-summary"><strong>'+esc(p.name)+'</strong><div><span>기존 종료 <b>'+p.original+'</b></span><i>→</i><span>요청 종료 <b>'+p.end+'</b></span></div><p>'+esc(p.reason)+'</p>'+(s.detail?'<a href="'+esc(p.url)+'">요청 검토하기 →</a>':'<small>가상 요약 · 세부 자료 미연결</small>')+'</div>').join(''):'<p class="summary-empty">추가 기간 요청이 없습니다.</p>')+'</section></div><div class="summary-footnote">'+(s.example?'해당 매장의 수치와 항목명은 홈·좌측 메뉴를 보여주기 위한 예시입니다. 세부 화면은 연결하지 않았습니다.':s.detail?'완료 수는 본사에서 확인한 작업 기준입니다. 검토 항목을 누르면 해당 증빙이나 기간 요청으로 이동합니다.':'자료가 연결되면 작업 수와 현재 주차, 완료·검토 현황이 여기에 표시됩니다.')+'</div>';
  }
  window.SiteSummary={get,pending,hasDetail,canReview,dashboard};
})();
