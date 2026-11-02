(() => {
  const A=window.DEMO,U=window.DemoUI,S=window.SiteSummary,$=s=>document.querySelector(s);
  sites.push(...A.read().sites);
  const originalRender=render;
  render=()=>{
    sites.forEach(site=>site.count=S.get(site.id).pending.length);originalRender();const s=S.get(selected);
    const stats=document.querySelectorAll('.home-stat strong');stats[1].textContent=['site_a','site-b','site-c'].reduce((sum,id)=>sum+S.get(id).pending.length,0);stats[2].textContent=['site_a','site-b','site-c'].reduce((sum,id)=>sum+S.get(id).pending.filter(p=>p.extension).length,0);
    const management=$('.site_a-management');management.className='site_a-management store-summary';management.id='store-summary';management.innerHTML=S.dashboard(selected);
    $('.selected-meta').innerHTML='<label>진행 주차<strong id="selected-week">'+(s.week?'제'+s.week+'주':'자료 연결 전')+'</strong></label><label>이번 주 작업<strong>'+s.weekly+'개</strong></label><label>검토할 항목<strong id="selected-gantt">'+s.pending.length+'건</strong></label><label>추가 기간 요청<strong>'+s.pending.filter(p=>p.extension).length+'건</strong></label>';
    const sitePhoto=$('.selected-photo');if(s.example){sitePhoto.src=selected==='site-b'?window.DEMO_DATA.photos.counterAfter:window.DEMO_DATA.photos.lighting;sitePhoto.style.display='block';sitePhoto.alt=s.name+' · 시연용 예시 사진';}else if(s.detail){sitePhoto.src=window.DEMO_DATA.photos.wallAfter;sitePhoto.style.display='block';sitePhoto.alt='가상 현장 A · 관측 예시 사진';}else sitePhoto.style.display='none';
    $('#selected-status').textContent=s.example?'요약 예시':s.detail?'검토 '+s.pending.length+'건':'자료 미연결';$('#selected-status').className='tag '+(s.pending.length?'request':'neutral');
    $('#selected-hint').textContent=s.example?'홈과 좌측 메뉴를 위한 가상 요약입니다. 이 매장의 세부 공정표와 작업 증빙은 연결하지 않았습니다.':s.detail?'이번 주 일정과 검토할 항목, 추가 기간 요청을 하단에서 확인하세요.':'이 매장의 공정표와 사진을 연결하면 상세 화면을 사용할 수 있습니다.';
    const open=$('#open-site');if(s.detail){open.href='00-site-overview.html';open.removeAttribute('aria-disabled');open.textContent='매장 열기 →';open.tabIndex=0;}else{open.removeAttribute('href');open.setAttribute('aria-disabled','true');open.textContent='세부 자료 없음';open.tabIndex=-1;}
    const chat=$('#chat-launcher');if(chat){chat.disabled=!s.detail;chat.title=s.detail?'프로젝트 도우미 열기':'이 매장의 세부 자료가 없습니다.';}
    window.AlohaNavigation.select(selected);
  };
  window.selectHomeSite=id=>{selected=id;render();};
  $('#new-site-form').onsubmit=e=>{e.preventDefault();if(!pendingLocation){$('#form-error').textContent='지도에서 매장 위치를 선택해 주세요.';return;}try{const f=id=>$('#'+id).value.trim();const site=A.addSite({name:f('site-name'),country:f('site-country'),city:f('site-city'),english:f('site-city'),managerId:f('site-manager-id'),managerContact:f('site-manager-contact'),camera:f('site-camera'),ganttFile:$('#site-gantt').files[0]?.name||'',...pendingLocation});sites.push(site);selected=site.id;closeDialog();render();$('#new-site-form').reset();pendingLocation=null;$('#location-status').textContent='선택 전';$('#form-error').textContent='';U.toast('새 매장을 이 브라우저에 저장했습니다.');}catch(err){$('#form-error').textContent=err.message;}};
  $('#open-site').onclick=e=>{if(!S.hasDetail(selected)){e.preventDefault();return;} };
  render();
})();
