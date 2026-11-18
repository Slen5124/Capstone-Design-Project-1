const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const M=require('../src/qwen-chat-model.js');
let calls=[];
const make=(code,start='2031-02-17')=>({ready:true,connected:true,role:'hq',page:'schedule',site:{code},schedule:'schedule-'+code,start,api:{copilotAsk:async(...args)=>{calls.push(args);return {answer:'작업 9개 <img src=x onerror=bad()>',site_code:args[0],week_start:args[2].week_start,sources:['공정표']}}}});
(async()=>{
 let c=make('sample-site'),r=await M.answer(c,'작업 수');assert(r.html.includes('&lt;img'));assert(!r.html.includes('<img'));assert.equal(calls[0][0],'sample-site');assert.equal(calls[0][1],'schedule-sample-site');assert.equal(calls[0][2].page,'schedule');
 await M.answer(c,'그 작업은?');assert.equal(calls.at(-1)[2].history,undefined);assert.match(calls.at(-1)[2].request_id,/^[a-f0-9-]{36}$/);
 await M.answer(make('seoul'),'작업 수');assert.equal(calls.at(-1)[0],'seoul');
 await M.answer(make('sample-site','2031-02-24'),'작업 수');assert.equal(calls.at(-1)[2].week_start,'2031-02-24');
 const before=calls.length;await assert.rejects(M.answer({...c,connected:false,name:'런던'},'작업 수'),/런던/);assert.equal(calls.length,before);
 await assert.rejects(M.answer({...c,ready:false},'작업 수'),/불러오는 중/);assert.equal(calls.length,before);
 await assert.rejects(M.answer({...c,api:{copilotAsk:async()=>({answer:'타 매장',site_code:'other',week_start:c.start})}},'질문'),/일치하지/);
 let preparing=0,attempts=0;
 const archive={ready:true,connected:true,sourceOnly:true,site:{code:'costa-mesa'},role:'hq',start:null,schedule:null,api:{sourceAsk:async(code,body)=>{assert.equal(code,'costa-mesa');assert.equal(body.week_start,null);return {site_code:code,week_start:null,answer:'사진 9918장'}}}};
 assert((await M.answer(archive,'사진 수')).html.includes('9918'));
 let retryId;c=make('warming');c.api.copilotAsk=async(code,schedule,body)=>{if(++attempts===1){retryId=body.request_id;throw Error('GPU 모델을 준비 중');}assert.equal(retryId,body.request_id);return {answer:'준비 완료',site_code:'warming',week_start:c.start}};
 r=await M.answer(c,'작업',{wait:async()=>{},onPreparing:()=>preparing++});assert.equal(attempts,2);assert.equal(preparing,1);
 let current=true;c=make('cancel');c.api.copilotAsk=async()=>{current=false;return {answer:'이전 매장 답변',site_code:'cancel',week_start:c.start}};
 await assert.rejects(M.answer(c,'질문',{isCurrent:()=>current}),{name:'AbortError'});
 c=make('stored');c.api.copilotHistory=async(code,schedule,start,before)=>({site_code:code,week_start:start,next_before:'older',turns:[{site_code:code,week_start:start,question:'이전 질문',answer:'저장 답변',suggestions:['상세 내용은?','다음 절차는?']}]});
 const stored=await M.history(c);assert.equal(stored.turns[0].answer,'저장 답변');assert.equal(stored.turns[0].suggestions.length,2);
 await assert.rejects(M.history({...c,api:{copilotHistory:async()=>({...stored,site_code:'other'})}}),/일치하지/);
 const record={type:'submission_rejection',site_code:c.site.code,schedule_id:c.schedule,
   task_name:'피팅룸 조명',task_id:crypto.randomUUID(),review_id:crypto.randomUUID(),submission_attempt:1};
 const reviewed={status:'complete',site_code:c.site.code,request_id:crypto.randomUUID(),answer:'반려 사유',
   source_records:[record,{...record,site_code:'other'},{...record,review_id:'javascript:alert(1)'}]};
 assert.match(M.format(reviewed,c),/원본 보기/);
 assert.equal(M.sourceRecords(reviewed,c).length,1);
 assert.doesNotMatch(M.format(reviewed,{...c,schedule:'different'}),/원본 보기/);
 const schedule={type:'weekly_schedule',site_code:c.site.code,schedule_id:c.schedule,week_start:'2031-02-24'};
 const combined=M.format({...reviewed,source_records:[record,schedule]},c);
 assert.equal((combined.match(/원본 보기/g)||[]).length,1);
 console.log('PASS: chat model scopes context and history, escapes output, retries and drops stale responses');
})().catch(e=>{console.error(e);process.exitCode=1});
