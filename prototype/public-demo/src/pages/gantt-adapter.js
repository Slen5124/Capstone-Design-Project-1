(() => {
  const A=window.DEMO,U=window.DemoUI,$=s=>document.querySelector(s),params=new URLSearchParams(location.search);let currentTask=params.get('request')||params.get('task')||'45';
  const desired=Number(params.get('week'));if(desired>=1&&desired<=16)week=desired;full=params.get('view')==='all';const originalRender=render;document.body.classList.add('gantt-screen');
  render=()=>{
    for(const t of tasks)Object.assign(t,A.effectiveTasks().find(x=>x.id===t.id));approved=A.read().extensions['44']?.state==='approved';originalRender();
    const rows=[...document.querySelectorAll('.chart-row[data-id]')];if(!rows.some(row=>row.dataset.id===currentTask))currentTask=rows[0]?.dataset.id||null;
    const from=full?0:(week-1)*7-1,to=full?111:(week-1)*7+6,today=idx('2026-02-19')+.375,span=to-from+1,todayVisible=today>=from&&today<=to+1,todayLeft=(today-from)/span*100;
    for(const row of rows){const id=row.dataset.id,info=A.taskInfo(id),r=info.review;row.classList.remove('selected','no-evidence');row.removeAttribute('aria-disabled');row.classList.toggle('is-selected',id===currentTask);row.classList.add(info.confirmed?'state-confirmed':r?.state==='request'?'state-request':'state-planned');
      const meta=row.querySelector('.row-meta'),origin=Date.parse(info.task.actualStart||info.task.start)<Date.parse('2026-01-05')+(week-1)*7*86400000?'지난주부터':'이번 주 시작';meta.textContent=(full?info.task.start.slice(5).replace('-','.')+' - '+info.task.finish.slice(5).replace('-','.'):origin)+(info.confirmed?' · 완료 '+info.task.actualFinish.slice(5).replace('-','.'):r?.state==='request'?' · 완료 확인 요청':info.late?' · 일정 확인 필요':'');
      if(info.extension?.state!=='pending')row.querySelector('.requested')?.remove();if(info.extension?.state==='approved'){meta.classList.add('delay');meta.textContent='지연·이월 · '+info.extension.original.slice(5).replace('-','.')+' → '+info.extension.end.slice(5).replace('-','.');}
      row.tabIndex=0;row.setAttribute('role','button');row.setAttribute('aria-label',info.task.name+' 일정과 이력 보기');const choose=()=>{const scroll=$('#chart').scrollTop;currentTask=id;render();$('#chart').scrollTop=scroll;if(innerWidth<950)$('.panel').scrollIntoView({behavior:'smooth',block:'nearest'});};row.onclick=choose;row.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose();}};
    }
    if(todayVisible)document.querySelectorAll('#chart .track').forEach(track=>{const marker=document.createElement('span');marker.className='today-marker';marker.style.left=todayLeft+'%';marker.setAttribute('aria-hidden','true');track.append(marker);if(track.closest('.chart-header')){const label=document.createElement('span');label.className='today-label';label.style.left=todayLeft+'%';label.textContent='오늘 02.19';track.append(label);}});
    if(todayVisible){const cells=document.querySelectorAll('.chart-header .day'),cell=cells[full?6:Math.floor(today-from)];if(cell){cell.classList.add('day-today');cell.textContent=full?'W7 · 오늘':'02.19 · 오늘';}}
    $('.legend').innerHTML='<span><i class="swatch-planned"></i>계획 작업</span><span><i class="swatch-selected"></i>선택 작업</span><span><i class="swatch-extension"></i>추가 기간 요청</span><span><i class="swatch-request"></i>완료 확인 요청</span><span><i class="swatch-confirmed"></i>완료 확인</span>';
    const panel=$('.panel');panel.id='task-context-panel';panel.innerHTML=currentTask?TaskDetails.panel(currentTask):'<p>선택한 주차의 작업이 없습니다.</p>';U.bindExtension(panel,render);
    document.querySelectorAll('a[href^="03-weekly-report.html"]').forEach(a=>a.href='03-weekly-report.html?week='+week);$('#open-report').onclick=()=>location.href='03-weekly-report.html?week='+week;
    const hint=$('.bottom');hint.innerHTML='<span>작업을 누르면 옆에서 일정·사진·검토 이력을 확인할 수 있습니다.</span><span class="current-date-note">시연 기준 02.19 오전 9시</span>';U.badges();
  };
  const classify=document.createElement('button');classify.className='secondary';classify.id='classify-tasks';classify.textContent='작업 분류';classify.onclick=U.classifyDialog;$('.head-actions').prepend(classify);
  const todayBadge=document.createElement('span');todayBadge.className='demo-today';todayBadge.textContent='오늘 2026.02.19 · 오전 9시';$('.timeline-title').append(todayBadge);render();
})();
