"""Run actual page functions with delayed responses to catch cross-task UI writes."""
import shutil
import subprocess

import pytest

from drawform.service.app import PAGE


def function_between(start, end):
    return PAGE[PAGE.index(start):PAGE.index(end, PAGE.index(start))]


PRELUDE = """
const assert=require('node:assert/strict');
const defer=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
"""


@pytest.mark.parametrize("reject", [False, True])
def test_history_response_cannot_write_another_tasks_cache_or_error(reject):
    source = function_between("async function viewVersion(number){", "function resetDrawing(id){")
    run_node(PRELUDE + "(async()=>{\n" + """
let pageBusy=false,answerBusy=false,loadEpoch=1,previewEpoch=0,selectedVersion=1;
let versions=[{number:0,files:{svg:'A.svg',json:'A.json'}}],versionCache=new Map();
const pending=defer(),painted=[],errors=[];
const api=()=>pending.promise,$=()=>({value:''}),refreshControls=()=>{},setTaskURL=()=>{};
const paintVersion=(v,svg)=>painted.push(svg),handleError=e=>errors.push(e);
""" + source + """
const request=viewVersion(0);
loadEpoch=2;pageBusy=false;versionCache=new Map([[1,{svg:'B'}]]);
""" + ("pending.reject(new Error('late A failure'));" if reject else "pending.resolve('A');") + """
await request;
assert.deepEqual([...versionCache.keys()],[1]);assert.equal(versionCache.get(1).svg,'B');
assert.equal(painted.length,0);assert.equal(errors.length,0);assert.equal(pageBusy,false);
})().catch(e=>{console.error(e);process.exitCode=1});
""")


def test_late_job_list_cannot_replace_a_new_authenticated_session():
    source = function_between("async function loadJobs(){", '$("#jobs").addEventListener')
    run_node(PRELUDE + "(async()=>{\n" + """
let token='old',connected=true,sessionEpoch=1,jobsEpoch=0,allJobs=[{job_id:'new'}];
const pending=defer();let paints=0;
const api=()=>pending.promise,renderJobs=()=>paints++,handleError=()=>{throw Error('stale error')};
""" + source + """
const request=loadJobs();sessionEpoch++;token='new';pending.resolve({jobs:[{job_id:'old'}]});await request;
assert.deepEqual(allJobs,[{job_id:'new'}]);assert.equal(paints,0);
})().catch(e=>{console.error(e);process.exitCode=1});
""")


def test_review_lookup_cannot_post_after_navigation_or_identity_change():
    source = function_between("async function submitReview(){", 'document.querySelectorAll("#rvBtns .chip")')
    run_node(PRELUDE + "(async()=>{\n" + """
let jobId='A',loadEpoch=1,reviewPending={submission_id:'existing-key'},pageBusy=false;
const pending=defer(),calls=[];
const $=()=>({disabled:false}),draftKey=()=>'',saveDraft=()=>true,refreshControls=()=>{};
const api=(path,options)=>{calls.push({path,options});return pending.promise};
""" + source + """
const request=submitReview();jobId='B';loadEpoch++;pageBusy=false;
pending.resolve({reviews:[]});await request;
assert.equal(calls.length,1);assert.equal(calls[0].path,'/jobs/A/review');assert.equal(pageBusy,false);
})().catch(e=>{console.error(e);process.exitCode=1});
""")


def test_upload_response_preserves_users_new_navigation():
    source = function_between('$("#up").onsubmit=async event=>{', "async function ask(){")
    run_node(PRELUDE + "(async()=>{\n" + """
let connected=true,uploadBusy=false,sessionEpoch=1,loadEpoch=1;
const controls={'#up':{},'#step-file':{files:[{name:'part.step',size:10}],value:'part.step'},'#upload-status':{}};
const $=key=>controls[key],pending=defer(),shows=[];let lists=0;
const refreshControls=()=>{},api=()=>pending.promise,show=async id=>shows.push(id),loadJobs=async()=>lists++;
class FormData{append(){}}
""" + source + """
const request=controls['#up'].onsubmit({preventDefault(){}});loadEpoch++;
pending.resolve({job_id:'uploaded'});await request;
assert.deepEqual(shows,[]);assert.equal(lists,1);assert.equal(uploadBusy,false);
})().catch(e=>{console.error(e);process.exitCode=1});
""")


def test_storage_failure_blocks_review_post_and_keeps_existing_submission_id():
    source = function_between("async function submitReview(){", 'document.querySelectorAll("#rvBtns .chip")')
    run_node(PRELUDE + "(async()=>{\n" + """
let jobId='A',loadEpoch=1,reviewPending={submission_id:'stable-existing-id'},pageBusy=false;
let calls=0;const status={disabled:false,textContent:''};
const $=()=>status,draftKey=()=>'',saveDraft=()=>false,refreshControls=()=>{};
const api=()=>{calls++;throw Error('must not send')};
""" + source + """
await submitReview();assert.equal(calls,0);assert.equal(reviewPending.submission_id,'stable-existing-id');
assert.equal(pageBusy,false);assert.match(status.textContent,/未发送/);
})().catch(e=>{console.error(e);process.exitCode=1});
""")


def test_old_download_failure_cannot_invalidate_a_new_session():
    source = function_between("async function save(href,name){", 'document.addEventListener("click",event=>{const link=')
    run_node(PRELUDE + "(async()=>{\n" + """
let sessionEpoch=1,loadEpoch=1,errors=0;const pending=defer();
const api=()=>pending.promise,handleError=()=>errors++;
""" + source + """
const request=save('/old/file','old.dxf');sessionEpoch++;loadEpoch++;
pending.reject(Object.assign(new Error('old unauthorized'),{status:401}));await request;
assert.equal(errors,0);
})().catch(e=>{console.error(e);process.exitCode=1});
""")


def run_node(program):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the isolated frontend promise tests")
    result = subprocess.run([node, "-e", program], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
