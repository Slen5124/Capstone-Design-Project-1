/* Durable server-owned conversations, scoped to the authenticated user/site/week. */
((root) => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const scopeKey = ctx => [ctx?.role, ctx?.site?.code, ctx?.schedule, ctx?.start].join('|');
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const cancelled = () => Object.assign(new Error('조회 범위가 변경되었습니다.'), {name:'AbortError'});
  const available = ctx => ctx?.connected !== false && ctx?.ready && ctx.site?.code && (ctx.sourceOnly || (ctx.schedule && ctx.start));
  function validate(ctx, result) {
    if(result.site_code!==ctx.site.code || result.week_start!==(ctx.sourceOnly?null:ctx.start))throw new Error('답변의 매장·주차가 현재 선택 범위와 일치하지 않습니다. 다시 질문해 주세요.');
  }
  const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
  const week = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value);
  function sourceRecords(turn, ctx) {
    if (turn.status !== 'complete' || !ctx?.schedule || turn.site_code !== ctx.site?.code || !Array.isArray(turn.source_records)) return [];
    return turn.source_records.filter(record => {
      if (record?.site_code !== ctx.site.code || record.schedule_id !== ctx.schedule) return false;
      switch (record.type) {
        case 'submission_rejection': return uuid(record.task_id) && uuid(record.review_id);
        case 'manager_submission':
        case 'ai_observation': return uuid(record.task_id) && uuid(record.record_id);
        case 'change_request': return uuid(record.record_id);
        case 'report_pdf': return uuid(record.record_id) && week(record.week_start);
        case 'task_schedule': return uuid(record.task_id) && week(record.week_start);
        case 'weekly_schedule':
        case 'report_readiness': return week(record.week_start);
        default: return false;
      }
    });
  }
  function format(turn, ctx) {
    const sources=Array.isArray(turn.sources)?turn.sources:[];
    const records = sourceRecords(turn, ctx);
    const button = records.length ? '<div class="chat-source-actions"><button class="chat-source-button" type="button" ' +
      'aria-haspopup="dialog" data-source-turn="' + esc(turn.request_id) + '">원본 보기</button></div>' : '';
    return '<div class="qwen-answer-text" style="white-space:pre-wrap">'+esc(turn.answer||turn.error||'답변을 준비하고 있습니다.')+'</div>'+(sources.length?'<p class="qwen-answer-sources">근거: '+sources.map(esc).join(' · ')+'</p>':'')+
      button;
  }
  async function history(ctx, options={}) {
    if(!available(ctx))return {turns:[],next_before:null};
    const result=ctx.sourceOnly?await ctx.api.sourceHistory(ctx.site.code,options.before):await ctx.api.copilotHistory(ctx.site.code,ctx.schedule,ctx.start,options.before);
    if(options.isCurrent && !options.isCurrent())throw cancelled();
    validate(ctx,result);
    if(!Array.isArray(result.turns))throw Error('대화 내역을 불러오지 못했습니다.');
    result.turns.forEach(t=>validate(ctx,t));
    return result;
  }
  async function answer(ctx, question, options = {}) {
    if(ctx?.connected===false)throw Error(ctx.reason||((ctx.name||'선택한 매장')+'의 자료는 아직 연결되지 않았습니다.'));
    if(!available(ctx))throw Error('현장 자료를 불러오는 중입니다. 잠시 후 질문해 주세요.');
    const isCurrent=options.isCurrent||(()=>true);
    const body={message:question,week_start:ctx.sourceOnly?null:ctx.start,request_id:options.requestId||crypto.randomUUID(),page:ctx.page||(ctx.role==='field'?'field':'home')};
    for(let attempt=0;attempt<24;attempt++){
      if(!isCurrent())throw cancelled();
      try{
        const result=ctx.sourceOnly?await ctx.api.sourceAsk(ctx.site.code,body):await ctx.api.copilotAsk(ctx.site.code,ctx.schedule,body);
        if(!isCurrent())throw cancelled();
        validate(ctx,result);
        if(typeof result.answer!=='string'||!result.answer.trim())throw Error('답변을 받지 못했습니다. 다시 질문해 주세요.');
        return {...result,html:format(result,ctx)};
      }catch(error){
        if(!isCurrent())throw cancelled();
        if(!/GPU 모델을 준비 중|GPU 할당|GPU.*대기/.test(String(error.message))||attempt===23)throw error;
        options.onPreparing?.('AI 답변을 준비하고 있습니다. 준비가 끝나면 자동으로 이어서 답변합니다.');
        await(options.wait||wait)(7000);
      }
    }
  }
  const model={answer,history,scopeKey,format,sourceRecords,esc,available};
  if(typeof module!=='undefined')module.exports=model;else root.QwenChatModel=model;
})(typeof window==='undefined'?globalThis:window);
