(() => {
  const KEY='demo-web-demo-v1',demoNow='2026-02-19T09:00:00',clone=v=>structuredClone(v),data=window.DEMO_DATA;
  data.photos.panels='assets/metal-panels-example.jpg';data.photos.diffuser='assets/diffuser-example.jpg';
  const examplePhotos={'16':'assets/shop-drawings-example.jpg','17':'assets/preparation-example.jpg','18':'assets/preparation-example.jpg','19':'assets/preparation-example.jpg','20':'assets/electrical-example.jpg','21':'assets/floor-finish-example.jpg','22':'assets/preparation-example.jpg','23':'assets/preparation-example.jpg','24':'assets/preparation-example.jpg','25':'assets/sample-frame-after.jpg','26':'assets/ceiling-example.jpg','27':'assets/electrical-example.jpg','29':'assets/sprinkler-example.jpg','30':'assets/wall-inspection-example.jpg','31':'assets/ceiling-example.jpg','32':'assets/electrical-example.jpg','33':'assets/ceiling-example.jpg','34':'assets/electrical-example.jpg','35':'assets/shop-drawings-example.jpg','37':'assets/electrical-example.jpg','38':'assets/wall-inspection-example.jpg','42':'assets/sprinkler-example.jpg','43':'assets/cashwrap-board-example.jpg','44':'assets/sprinkler-example.jpg'};
  function initial(){
    const s={version:2,scenarioVersion:2,clockTick:0,sites:[],categories:{},reviews:{
      '39':{state:'request',attempt:2,reason:'일부 조명의 점등 상태가 보이지 않아 완료 여부를 확인하기 어렵습니다. 전체 조명이 켜진 상태로 다시 촬영해 주세요.',reviewedAt:'2026-02-18T16:40:00'},
      '45':{state:'pending',attempt:1,reason:''}
    },extensions:{'44':{state:'pending',original:'2026-02-20',end:'2026-02-23',reason:'스프링클러 배관 설치 후 압력 시험과 현장 점검이 남아 있습니다. 검사 기관의 방문 일정이 기존 종료일인 02.20에 맞지 않아, 02.23까지 검사와 보완 사항 확인을 마칠 수 있도록 추가 기간을 요청합니다.'}},submissions:[],events:[]};
    for(const t of data.tasks.filter(t=>t.finish<'2026-02-16'))s.reviews[t.id]={state:'confirmed',attempt:1,reason:'',actualFinish:t.finish,confirmedAt:t.finish+'T17:00:00',reviewedAt:t.finish+'T17:00:00'};
    for(const id of ['36','40','41'])s.reviews[id]={state:'confirmed',attempt:1,reason:'',actualFinish:'2026-02-18',confirmedAt:'2026-02-18T17:00:00',reviewedAt:'2026-02-18T17:00:00'};
    s.submissions=[
      {id:'seed-39-1',taskId:'39',attempt:1,at:'2026-02-18T16:30:00',photo:data.photos.lighting,report:'조명 설치 사진 제출',status:'rejected',completed:true},
      {id:'seed-39-2',taskId:'39',attempt:2,at:'2026-02-19T08:20:00',photo:data.photos.lighting,report:'전체 점등 사진을 다시 제출합니다.',status:'request',completed:true},
      {id:'seed-40-1',taskId:'40',attempt:1,at:'2026-02-15T10:00:00',photo:data.photos.counterBefore,report:'착수 전 참고 사진',status:'reference',completed:false},
      {id:'seed-40-2',taskId:'40',attempt:1,at:'2026-02-18T16:00:00',photo:data.photos.counterAfter,report:'계산대 벽체 골조 설치 완료',status:'confirmed',completed:true},
      {id:'seed-36-1',taskId:'36',attempt:1,at:'2026-02-18T15:00:00',photo:data.photos.panels,report:'금속 패널·유리 제작 및 반입 완료',status:'confirmed',completed:true},
      {id:'seed-41-1',taskId:'41',attempt:1,at:'2026-02-18T15:30:00',photo:data.photos.diffuser,report:'피팅룸 디퓨저 설치 완료',status:'confirmed',completed:true}
    ];
    s.events=[{taskId:'39',action:'반려',reason:s.reviews['39'].reason,at:'2026-02-18T16:40:00'},{taskId:'45',action:'AI 관측 검토 요청',reason:'원본과 분석 표시 예시 등록',at:'2026-02-19T08:00:00'}];return s;
  }
  let state=initial();
  try{const old=JSON.parse(localStorage.getItem(KEY)||'null');if(old&&old.reviews&&old.extensions&&Array.isArray(old.submissions)&&Array.isArray(old.sites)&&Array.isArray(old.events)){
    if(old.scenarioVersion===2)state=old;
    else{const seed=initial();state={...seed,...old,version:2,scenarioVersion:2,clockTick:old.events.length,reviews:{...seed.reviews,...old.reviews},submissions:clone(old.submissions)};
      for(const [id,r] of Object.entries(seed.reviews))if(r.state==='confirmed')state.reviews[id]=clone(r);
      for(const item of seed.submissions){const existing=state.submissions.find(x=>x.id===item.id);if(existing){existing.at=item.at;if(['36','40','41'].includes(item.taskId)){existing.completed=item.completed;existing.status=item.status;}}else state.submissions.push(clone(item));}
      state.submissions.forEach((x,i)=>{if(!x.id.startsWith('seed-'))x.at='2026-02-19T09:'+String(i%60).padStart(2,'0')+':00';});
      state.events=[...seed.events,...old.events.map((e,i)=>({...e,at:'2026-02-19T09:'+String(i%60).padStart(2,'0')+':00'}))];
      for(const [id,r] of Object.entries(state.reviews)){if(r.state==='confirmed'&&!r.actualFinish){r.actualFinish='2026-02-19';r.confirmedAt='2026-02-19T09:00:00';}if(r.state==='observed')r.reviewedAt='2026-02-19T09:00:00';}
      state.reviews['39'].reviewedAt=state.reviews['39'].state==='confirmed'?state.reviews['39'].confirmedAt:['observed','accepted'].includes(state.reviews['39'].state)?'2026-02-19T09:00:00':'2026-02-18T16:40:00';
    }
  }}catch{}
  if(state.extensions['44']?.reason==='검사 일정 조율')state.extensions['44'].reason=initial().extensions['44'].reason;
  function save(next){try{localStorage.setItem(KEY,JSON.stringify(next));}catch{throw Error('브라우저 저장 공간이 부족합니다. 이번 변경은 저장하지 못했습니다. 사진 크기를 줄여 다시 시도해 주세요.');}state=next;}
  const change=action=>{const next=clone(state);action(next);save(next);return clone(next);};
  function time(s){return new Date(Date.UTC(2026,1,19,9,(s.clockTick||0))).toISOString();}
  function event(s,id,action,reason=''){s.events.push({taskId:id,action,reason,at:time(s)});s.clockTick=(s.clockTick||0)+1;}
  const baseTask=id=>data.tasks.find(t=>t.id===String(id)),latest=(id,s=state)=>s.submissions.filter(x=>x.taskId===String(id)).at(-1);
  const required=(v,label)=>{if(!String(v||'').trim())throw Error(label+'를 작성해 주세요.');return String(v).trim();};
  const labels={request:'완료 확인 요청',confirmed:'완료 확인',pending:'검토 대기',rejected:'반려',observed:'관측 결과 수락',accepted:'검토 수락',reference:'착수 전 참고'};
  const tone=s=>['confirmed','observed','accepted'].includes(s)?'confirmed':s==='rejected'?'amber':s==='request'?'request':'neutral';
  const effectiveTasks=()=>data.tasks.map(t=>{const e=state.extensions[t.id],r=state.reviews[t.id];return {...t,originalFinish:t.finish,actualFinish:r?.actualFinish||null,finish:r?.state==='confirmed'&&r.actualFinish?r.actualFinish:e?.state==='approved'?e.end:t.finish};});
  const weekTasks=week=>{const from=Date.UTC(2026,0,5)+(week-1)*7*86400000,to=from+6*86400000;return effectiveTasks().filter(t=>Date.parse(t.actualStart||t.start)<=to&&Date.parse(t.finish)>=from);};
  const counts=()=>({site_a:Object.values(state.reviews).filter(r=>['request','pending'].includes(r.state)).length+Object.values(state.extensions).filter(e=>e.state==='pending').length,extensions:Object.values(state.extensions).filter(e=>e.state==='pending').length});
  const completed=()=>effectiveTasks().filter(t=>state.reviews[t.id]?.state==='confirmed');
  function reject(id,reason){reason=required(reason,'반려 사유');return change(s=>{if(!s.reviews[id])throw Error('검토할 작업을 선택해 주세요.');Object.assign(s.reviews[id],{state:'rejected',reason,reviewedAt:time(s)});const sub=latest(id,s);if(sub)sub.status='rejected';event(s,id,'반려',reason);});}
  function accept(id){return change(s=>{if(!s.reviews[id])throw Error('작업을 찾지 못했습니다.');const sub=latest(id,s),r=s.reviews[id];r.state=id==='45'?'observed':sub?.completed?'confirmed':'accepted';r.reviewedAt=time(s);if(r.state==='confirmed'){r.confirmedAt=time(s);r.actualFinish='2026-02-19';}if(sub)sub.status=r.state;event(s,id,labels[r.state]);});}
  function submit(id,p){if(!baseTask(id))throw Error('작업을 찾지 못했습니다.');if(!p.photo)throw Error('증빙 사진을 추가해 주세요.');if(!p.completed)required(p.report,'남은 작업의 사유');const original=baseTask(id).finish;
    if(p.extension){required(p.extension.reason,'추가 기간 요청 사유');if(!/^\d{4}-\d{2}-\d{2}$/.test(p.extension.end)||!Number.isFinite(Date.parse(p.extension.end))||p.extension.end<=original)throw Error('기존 종료일 이후의 요청 종료일을 선택해 주세요.');}
    return change(s=>{const r=s.reviews[id]||(s.reviews[id]={state:'pending',attempt:0,reason:''});r.attempt++;r.state=p.completed?'request':'pending';delete r.actualFinish;delete r.confirmedAt;s.submissions.push({id:crypto.randomUUID(),taskId:id,attempt:r.attempt,at:time(s),photo:p.photo,report:String(p.report||'').trim(),completed:!!p.completed,status:r.state});if(p.extension)s.extensions[id]={state:'pending',original,end:p.extension.end,reason:p.extension.reason.trim()};event(s,id,'증빙 제출',p.report||'');});}
  function decideExtension(id,approved,reason=''){if(!approved)required(reason,'반려 사유');return change(s=>{if(!s.extensions[id])throw Error('추가 기간 요청이 없습니다.');Object.assign(s.extensions[id],{state:approved?'approved':'rejected',reviewReason:reason,reviewedAt:time(s)});event(s,id,approved?'추가 기간 수락':'추가 기간 반려',reason);});}
  function addSite(site){required(site.name,'매장명');required(site.country,'국가');required(site.city,'도시');if(!Number.isFinite(site.lat)||!Number.isFinite(site.lon)||Math.abs(site.lat)>90||Math.abs(site.lon)>180)throw Error('유효한 지도 위치를 선택해 주세요.');if(site.camera){let url;try{url=new URL(site.camera);}catch{throw Error('올바른 카메라 링크를 입력해 주세요.');}if(!['http:','https:'].includes(url.protocol))throw Error('웹 주소 형식의 카메라 링크를 입력해 주세요.');}const item={...site,id:'new-'+crypto.randomUUID(),count:0};change(s=>s.sites.push(item));return item;}
  const reset=()=>{localStorage.removeItem(KEY);state=initial();};
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
  const categories={WALL:'벽',FLOOR:'바닥',CEILING:'천장',FIXTURE:'집기·조명',MEP:'설비',ADMIN:'검사·행정',OTHER:'기타'};
  function category(t){if(state.categories?.[t.id])return state.categories[t.id];if(['36','39','40','41','50','54'].includes(t.id))return 'FIXTURE';if(/검사|승인|inspection|permit|approval/i.test(t.name))return 'ADMIN';if(/전기|스프링클러|배관|electrical|sprinkler|plumb|hvac|MEP/i.test(t.name))return 'MEP';if(/ceiling|천장/i.test(t.name))return 'CEILING';if(/바닥|floor|concrete|slab/i.test(t.name))return 'FLOOR';if(/wall|drywall|벽|골조/i.test(t.name))return 'WALL';return 'OTHER';}
  function classify(id,value){if(!baseTask(id)||!categories[value])throw Error('작업과 분류를 확인해 주세요.');change(s=>{s.categories||={};s.categories[id]=value;});}
  function taskInfo(id){const task=effectiveTasks().find(t=>t.id===String(id));if(!task)return null;const review=state.reviews[id],sub=latest(id),extension=state.extensions[id],confirmed=review?.state==='confirmed';const timeline=[{at:(task.actualStart||task.start)+'T09:00:00',label:task.actualStart?'현장 착수':'계획 시작',note:''}];
    for(const x of state.submissions.filter(x=>x.taskId===id))timeline.push({at:x.at,label:x.status==='reference'?'착수 전 참고 사진':x.attempt+'차 사진 제출',note:x.report});
    for(const e of state.events.filter(e=>e.taskId===id))timeline.push({at:e.at,label:e.action,note:e.reason});
    if(review?.confirmedAt&&!timeline.some(e=>e.label==='완료 확인'&&e.at===review.confirmedAt))timeline.push({at:review.confirmedAt,label:'본사 완료 확인',note:'사진·작업 내용 확인'});
    if(review?.reviewedAt&&['observed','accepted'].includes(review.state)&&!timeline.some(e=>e.at===review.reviewedAt))timeline.push({at:review.reviewedAt,label:labels[review.state],note:''});
    timeline.sort((a,b)=>a.at.localeCompare(b.at));return {task,review:clone(review||null),sub:clone(sub||null),extension:clone(extension||null),confirmed,late:!confirmed&&task.finish<'2026-02-19',photo:sub?.photo||(id==='45'?data.photos.wallAfter:examplePhotos[id]||null),examplePhoto:!sub&&id!=='45'&&!!examplePhotos[id],timeline};
  }
  window.DEMO={read:()=>clone(state),latest:id=>clone(latest(id)||null),counts,effectiveTasks,weekTasks,completed,taskInfo,demoNow,reject,accept,submit,decideExtension,addSite,reset,labels,tone,esc,categories,category,classify};
})();
