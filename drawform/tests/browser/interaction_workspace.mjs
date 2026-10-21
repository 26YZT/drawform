// Run only against run_browser_fixture.py and its isolated qa-* jobs.
// Faults are injected in the client; no engineering answers are sent to the pilot service.
export async function verifyWorkspace(page,{url,artifacts}) {
  const results=[];
  const check=(ok,name,detail={})=>{if(!ok)throw new Error(name+': '+JSON.stringify(detail));results.push({name,passed:true,...detail});console.log("PASS",name)};
  const read=()=>page.evaluate(()=>({selected:document.querySelectorAll('#reference-questions input:checked').length,
    progress:document.querySelector('#reference-progress').textContent,disabled:document.querySelector('#submit-references').disabled}));
  async function open(id){
    await page.click('#workspace-link');await page.fill('#job-search','');
    await page.click(`#jobs li[data-job="${id}"] button`);
    await page.waitForFunction(id=>document.querySelector('#task-draft-status').textContent.includes(id)
      && !document.querySelector('#who').disabled,id);
  }
  if(url!=='http://127.0.0.1:8844/')throw new Error('Only the isolated fixture origin is allowed');
  await page.goto(url+'?job=qa-refused');await page.waitForFunction(()=>document.querySelector('#drawing').textContent.includes('此前已收到'));
  await page.evaluate(()=>{for(const key of Object.keys(sessionStorage))if(key.startsWith('dfDraft:v2:')&&/:qa-/.test(key))sessionStorage.removeItem(key)});
  await page.goto(url+'?job=qa-reference');
  await page.waitForFunction(()=>document.querySelectorAll('[data-reference-question]').length===16);
  await page.fill('#who','');
  await page.click('#reference-nav [data-index="1"]');
  await page.click('[data-reference-question]:not([hidden]) .reference-option:first-child button');
  let state=await read();check(state.selected===0,'preview_does_not_select',state);
  for(let i=0;i<16;i++){
    await page.click(`#reference-nav [data-index="${i}"]`);
    await page.click('[data-reference-question]:not([hidden]) .reference-option:first-child input[type=radio]');
  }
  state=await read();check(state.selected===16&&state.disabled&&state.progress.includes('姓名'),'name_requirement_is_explained',state);
  await page.focus('#who');await page.fill('#who','隔离交互验证（非制图审核）');
  await page.evaluate(()=>{
    window.__uxFetch=window.fetch.bind(window);window.__uxPostCount=0;
    window.fetch=async(path,options={})=>{
      if(String(path).includes('/reference-answers')&&options.method==='POST'){
        window.__uxPostCount++;return new Response(JSON.stringify({error:'隔离测试：校验失败'}),{status:400,headers:{'Content-Type':'application/json'}});
      }return window.__uxFetch(path,options);
    };
  });
  await page.click('#submit-references');
  await page.waitForFunction(()=>document.querySelector('#answer-receipt').textContent.includes('校验失败'));
  state=await read();check(state.selected===16&&!state.disabled,'400_retains_reference_choices',state);
  await page.evaluate(()=>{window.fetch=async(path,options={})=>{
    if(String(path).includes('/reference-answers')&&options.method==='POST')throw new TypeError('隔离网络故障');
    return window.__uxFetch(path,options);
  }});
  await page.click('#submit-references');
  await page.waitForFunction(()=>document.querySelector('#answer-receipt').textContent.includes('隔离网络故障'));
  check((await read()).selected===16,'network_error_retains_reference_choices');
  await page.evaluate(()=>{window.fetch=window.__uxFetch});
  await open('qa-refused');
  check(await page.evaluate(()=>document.querySelector('#drawing').textContent.includes('此前已收到 4 项')&&document.querySelector('#drawing').textContent.includes('重复上传同一模型不会解决')),'refusal_keeps_reference_receipt_and_next_step');
  await open('qa-reference');check((await read()).selected===16,'navigation_restores_reference_draft');
  await page.goto(url+'?job=qa-reference');
  await page.waitForFunction(()=>document.querySelectorAll('#reference-questions input:checked').length===16);
  check(true,'reload_restores_reference_draft');
  await open('qa-refused');
  await page.evaluate(()=>{const key=Object.keys(sessionStorage).find(k=>k.startsWith('dfDraft:v2:')&&k.endsWith(':qa-reference'));const raw=JSON.parse(sessionStorage.getItem(key));raw.context='obsolete';sessionStorage.setItem(key,JSON.stringify(raw));window.__uxStaleKey=key});
  await open('qa-reference');
  check(await page.evaluate(()=>!document.querySelector('#draft-banner').hidden&&document.querySelector('#draft-message').textContent.includes('不会被覆盖')&&document.querySelector('#reference-questions input').disabled),'stale_draft_quarantined');
  await open('qa-refused');
  check(await page.evaluate(()=>JSON.parse(sessionStorage.getItem(window.__uxStaleKey)).context==='obsolete'),'navigation_does_not_erase_stale_draft');
  await page.evaluate(()=>sessionStorage.setItem(window.__uxStaleKey,'broken json'));
  await open('qa-reference');
  check(await page.evaluate(()=>!document.querySelector('#draft-banner').hidden&&document.querySelector('#draft-message').textContent.includes('无法读取')),'damaged_draft_has_visible_recovery');
  await page.click('#discard-draft');
  await page.waitForFunction(()=>!!document.querySelector('#reference-questions input')&&!document.querySelector('#reference-questions input').disabled);
  check((await read()).selected===0,'explicit_discard_clears_draft');
  await open('qa-drawn-a');
  await page.waitForFunction(()=>!!document.querySelector('#drawing svg'));
  const questionCount=await page.evaluate(()=>document.querySelectorAll('[data-question-card]').length);
  if(questionCount){
    await page.click('[data-question-card]:first-child [data-options] .opt:first-child input[type=radio]');
    await page.fill('[data-question-card]:first-child textarea','隔离草稿说明');
    await page.click('[data-question-card]:first-child [data-action=confirm]');
    await page.goto(url+'?job=qa-drawn-a');
    await page.waitForFunction(()=>document.querySelector('[data-question-card] textarea')?.value==='隔离草稿说明');
    check(await page.evaluate(()=>document.querySelector('[data-question-card] [data-question-status]').textContent.includes('已确认')),'answer_choice_note_and_confirmation_restore');
    await page.click('#go-review');
    check(await page.evaluate(()=>document.querySelector('#review-guidance').textContent.includes('未提交的选择')),'review_explains_unsent_answer_draft');
    await page.click('#discard-draft');await page.waitForFunction(()=>!!document.querySelector('#drawing svg')&&!document.querySelector('#who').disabled);
  }
  await page.focus('#who');await page.fill('#who','隔离交互验证（非制图审核）');
  await page.click('#go-review');
  await page.evaluate(()=>{window.__uxFetch=window.fetch.bind(window);window.__uxReviewPosts=0;window.fetch=async(path,options={})=>{if(String(path).endsWith('/review')&&options.method==='POST')window.__uxReviewPosts++;return window.__uxFetch(path,options)}});
  await page.click('#reasons input[value=wrong_value]');
  check(await page.evaluate(()=>document.querySelector('#rvBtns [data-d=accepted]').disabled&&document.querySelector('#reasons input[value=wrong_value]').checked),'checked_problem_is_not_silently_cleared');
  await page.click('#rvBtns [data-d=amended]');
  check(await page.evaluate(()=>!document.querySelector('#review-confirm').hidden&&window.__uxReviewPosts===0&&document.querySelector('#review-summary').textContent.includes('尚未回传 CAD')),'review_intent_only_opens_summary');
  await page.click('#cancel-review');
  check(await page.evaluate(()=>window.__uxReviewPosts===0&&document.querySelector('#reasons input[value=wrong_value]').checked),'cancel_posts_nothing_and_preserves_reasons');
  await page.click('#reasons input[value=wrong_value]');await page.click('#rvBtns [data-d=accepted]');
  await page.screenshot({path:artifacts+'/browser-review-summary-1440.png'});
  await page.evaluate(()=>{window.fetch=async(path,options={})=>{
    if(String(path).endsWith('/review')&&options.method==='POST'){window.__uxReviewPosts++;const result=await window.__uxFetch(path,options);if(result.ok)throw new TypeError('隔离测试：服务已写入，回包丢失');return result}
    return window.__uxFetch(path,options);
  }});
  await page.click('#confirm-review');
  await page.waitForFunction(()=>document.querySelector('#rvState').textContent.includes('回包丢失'));
  check(await page.evaluate(()=>document.querySelector('#confirm-review').textContent.includes('核对结果')&&!document.querySelector('#confirm-review').disabled),'lost_response_exposes_reconcile_action');
  await page.click('#confirm-review');
  await page.waitForFunction(()=>document.querySelector('#rvState').textContent.startsWith('已记录'));
  check(await page.evaluate(()=>window.__uxReviewPosts===1),'reconcile_does_not_duplicate_review_post');
  await page.evaluate(()=>{window.fetch=window.__uxFetch});
  return results;
}
