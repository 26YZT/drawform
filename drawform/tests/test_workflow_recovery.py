"""Actual browser functions: compatibility and recovery keep closed choices safe."""
import json,subprocess,shutil
from drawform.service.app import PAGE

def js_between(start,end):return PAGE.split(start,1)[1].split(end,1)[0]
def node(code):
    result=subprocess.run([shutil.which('node'),'-e',code],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr

def test_datum_choices_match_engine_axis_and_unknown_can_still_submit():
    functions='function optionIssue'+js_between('function optionIssue','function updateQuestionProgress')
    functions+='function answerPayload'+js_between('function answerPayload','$("#questions").addEventListener("change"')
    node("const assert=require('node:assert/strict');"+functions+'''
    const questionList=[{id:'q',kind:'datum_choice',datum:'A',axis:'x',options:[{id:'face-x',axis:'x'},{id:'face-y',axis:'y'},{id:'face-old'},{id:'bbox-current'}]}];
    const q=questionList[0],choices=new Map([['q',{selected:'face-y',confirmed:true,note:'keep note'}]]);
    assert(optionIssue(q,q.options[1]).includes('X=常数'));assert(optionIssue(q,q.options[2]));
    assert.equal(optionIssue(q,q.options[0]),'');assert.equal(optionIssue(q,q.options[3]),'');
    assert.throws(answerPayload);choices.get('q').selected='face-x';assert.equal(answerPayload()[0].option_id,'face-x');
    choices.get('q').selected=null;assert.equal(answerPayload()[0].option_id,null);assert.equal(answerPayload()[0].free_note,'keep note');
    ''')

def test_expired_connection_disables_drawn_task_actions_without_erasing_state():
    source='function canInteract'+js_between('function canInteract','function localAnswers')
    node("const assert=require('node:assert/strict');"+'''
    let connected=true,draftPhase='ready',drawingReady=true,questionsReady=true,currentJob={state:'drawn'},answerBusy=false,pageBusy=false,viewingHistory=false,isFrozen=false,latestVersion=0;
    const headVersion=()=>0,choice={selected:'kept',confirmed:true};
    '''+source+'''
    assert(canInteract()&&canAnswer());connected=false;assert(!canInteract()&&!canAnswer());assert.equal(choice.selected,'kept');
    ''')

def test_reference_definition_error_is_not_reported_as_a_new_version():
    source='function handleError'+js_between('function handleError','$("#state-notice").addEventListener')
    node("const assert=require('node:assert/strict');"+'''
    let jobId='job',isFrozen=false,questionsReady=true,notice;
    const target={},$=()=>target,showNotice=(...args)=>notice=args,refreshControls=()=>{};
    '''+source+'''
    handleError({status:409,message:'changed',body:{reason:'reference_definition_changed',head:1}});
    assert(notice[0].includes('本次回传未生效'));assert(!notice[0].includes('版本已变化'));assert.equal(questionsReady,true);
    ''')

def test_restored_incompatible_datum_is_unconfirmed_without_losing_other_fields():
    source='function optionIssue'+js_between('function optionIssue','function updateQuestionProgress')
    source+='function restoreDraft'+js_between('function restoreDraft','function removePendingDraft')
    source+='function sameDraftContext'+js_between('function sameDraftContext','function saveDraft')
    node("const assert=require('node:assert/strict');"+'''
    let draftPhase='loading',draftMuted=false,referenceSnapshot=null,reviewDecision='',reviewPending=null;
    const choices=new Map(),referenceChoices=new Map(),questionList=[{id:'q',kind:'datum_choice',datum:'A',axis:'x',options:[{id:'face-y',axis:'y'}]}];
    const fields={},$=key=>(fields[key]??={value:'',hidden:true,textContent:''}),document={querySelectorAll:()=>[]};
    const draftKey=()=> 'key',contextKey=()=> 'same',readSession=()=>JSON.stringify({context:'same',choices:[['q',{selected:'face-y',confirmed:true,note:'preserved'}]],references:[],who:'test',note:'review note',reasons:[]});
    '''+source+'''
    restoreDraft();assert.equal(choices.get('q').selected,'face-y');assert.equal(choices.get('q').confirmed,false);assert.equal(choices.get('q').note,'preserved');
    assert(fields['#draft-message'].textContent.includes('方向不匹配'));assert.equal(fields['#review-note'].value,'review note');
    ''')

def test_old_a3_draft_keeps_full_identity_checks_when_paper_is_explicit():
    source='function sameDraftContext'+js_between('function sameDraftContext','function saveDraft')
    node("const assert=require('node:assert/strict');"+source+'''
    const old={source_sha256:'source',source_valid:true,question_basis:'q',reference_basis:'r',effective_sha:'dxf',head_version:0};
    const current={sheet:'A3',...old};assert(sameDraftContext(JSON.stringify(old),JSON.stringify(current)));
    assert(!sameDraftContext(JSON.stringify(old),JSON.stringify({...current,sheet:'A2'})));
    for(const key of ['source_sha256','question_basis','reference_basis','effective_sha','head_version']){
      assert(!sameDraftContext(JSON.stringify(old),JSON.stringify({...current,[key]:'changed'})));
    }
    assert(!sameDraftContext('broken',JSON.stringify(current)));
    ''')
