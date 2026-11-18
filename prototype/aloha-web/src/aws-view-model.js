/* Presentation rules only; all evidence, decisions and dates come from the API. */
((root)=>{
 const groups=[
  {key:'review',label:'확인·보완',hint:'완료 요청 · AI 관측 · 증빙 검토 · 반려'},
  {key:'schedule',label:'일정·계획',hint:'계획 작업 · 일정 확인 · 추가 기간 요청'},
  {key:'done',label:'처리 완료',hint:'완료 확인 · 관측 결과 수락 · 검토 수락'}
 ];
 const labels={planned:'계획 작업',ai_observing:'AI 관측 대기',field_confirmation_required:'현장 확인 필요',rejected:'반려',confirmed:'완료 확인',accepted:'검토 수락',review_pending:'증빙 검토 대기',completion_requested:'완료 확인 요청',observation_review_pending:'AI 관측 검토',ai_observation_accepted:'관측 결과 수락'};
 function group(t){return ['confirmed','accepted','ai_observation_accepted'].includes(t.status)?'done':['rejected','field_confirmation_required','review_pending','completion_requested','observation_review_pending'].includes(t.status)?'review':'schedule'}
 function status(t,requests=[]){
  if(group(t)==='schedule'&&requests.some(r=>r.task_id===t.id&&r.status==='pending'))return '추가 기간 요청';
  return labels[t.status]||t.status;
 }
 function source(t){return t.latest_submission?'field':t.processing_route==='ai'?'ai':'field'}
 function baseline(task,events,request){
  let start=task.start,finish=task.finish;
  const applied=events.filter(e=>e.event_type==='schedule_apply'&&e.request_id===request.id).at(-1);
  if(applied?.details?.before_finish)return {start:applied.details.before_start,finish:applied.details.before_finish};
  for(const e of events.filter(e=>e.event_type==='schedule_apply'&&e.occurred_at<request.created_at).sort((a,b)=>a.occurred_at.localeCompare(b.occurred_at))){
   start=e.details?.after_start||null;finish=e.details?.after_finish||null;
  }
  return {start,finish};
 }
 const value={groups,labels,group,status,source,baseline};
 if(typeof module!=='undefined')module.exports=value;else root.AWSView=value;
})(typeof window==='undefined'?globalThis:window);
