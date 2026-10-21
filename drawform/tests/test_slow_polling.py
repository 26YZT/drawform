"""直接执行页面轮询，验证慢任务与切换任务的可观察行为。"""
import json
import shutil
import subprocess

import pytest

from drawform.service.app import PAGE


NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(NODE is None, reason='页面行为回归需要 Node.js')


def _execute(program):
    start = PAGE.index('async function poll(')
    end = PAGE.index('function engineVersions(', start)
    source = PAGE[start:end]
    script = '''const vm = require('node:vm');
const assert = require('node:assert/strict');
let clock = 0, requests = 0, sleeps = 0;
const notices = [];
const context = {POLL_INTERVAL:600, SLOW_TASK_AFTER:90000, loadEpoch:1,
 Date:{now:()=>clock}, STATE:{running:'正在出图',drawn:'已出图'},
 say:value=>notices.push(value), showNotice:(...args)=>notices.push(args), clearNotice:()=>{},
 sleep:async ms=>{clock+=ms;sleeps++;},
 api:async()=>{requests++;return {state:clock>=106000?'drawn':'running'};}};
vm.createContext(context);
vm.runInContext(SOURCE, context);
(async()=>{PROGRAM})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''.replace('SOURCE', json.dumps(source)).replace('PROGRAM', program)
    result = subprocess.run([NODE, '-e', script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_a_task_finishing_after_90_seconds_updates_without_manual_refresh():
    _execute('''const status=await context.poll('job',1);
assert.equal(status.state,'drawn');
assert.ok(clock>=106000);
assert.ok(notices.some(n=>JSON.stringify(n).includes('等待')),'必须显示实际等待状态');''')


def test_switching_jobs_stops_old_polling_before_another_request():
    _execute('''context.sleep=async ms=>{clock+=ms;context.loadEpoch=2;};
const status=await context.poll('old',1);
assert.equal(status,null);
assert.equal(requests,1,'切换后不能继续请求旧任务');''')


def test_a_refused_task_returns_its_reasons_without_turning_into_a_timeout():
    _execute('''context.api=async()=>({state:'refused',failed:['sheet_overflow'],reasons:['图面放不下']});
const status=await context.poll('refused',1);
assert.equal(status.state,'refused');
assert.deepEqual(status.failed,['sheet_overflow']);
assert.equal(clock,0);''')

