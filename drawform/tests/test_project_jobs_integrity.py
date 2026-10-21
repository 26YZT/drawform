"""Fault windows around the legacy drawing queue; synthetic render only."""
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
import pytest
from drawform.service.jobs import JobQueue, DRAWN, REFUSED, CRASHED, StaleBase, versions_of


def make_queue(tmp_path, render=None):
    source=tmp_path/'source.step';source.write_bytes(b'original source')
    q=JobQueue(tmp_path/'work',render=render)
    identity=q.submit_once(source,request_id='test',expected_sha=hashlib.sha256(source.read_bytes()).hexdigest())
    return q,identity


def render(source,directory,**kwargs):
    files=[]
    for kind in ('dxf','svg','pdf','json'):
        p=directory/('drawing.'+kind);p.write_bytes(('synthetic '+kind).encode());files.append((kind,p))
    return SimpleNamespace(files=tuple(files),verdict=SimpleNamespace(failed=(),reasons=()),questions=())


def test_source_replaced_before_render_never_calls_renderer(tmp_path):
    calls=[]
    q,jid=make_queue(tmp_path,lambda *a,**k:calls.append(a))
    q.get(jid).source.write_bytes(b'other model')
    q._execute(jid)
    assert q.get(jid).state==REFUSED and not q.get(jid).files and not calls


def test_source_replaced_during_render_never_publishes_outputs(tmp_path):
    def change(source,directory,**kwargs):
        result=render(source,directory,**kwargs);source.write_bytes(b'other model');return result
    q,jid=make_queue(tmp_path,change);q._execute(jid)
    assert q.get(jid).state==REFUSED and not q.get(jid).files
    assert not list(q.get(jid).directory.glob('drawing-edition-*.json'))


def test_drawing_seal_pins_four_formats_and_source(tmp_path):
    q,jid=make_queue(tmp_path,render);q._execute(jid)
    job=q.get(jid);assert job.state==DRAWN
    seal=json.loads((job.directory/'drawing-edition-0.json').read_text())
    assert seal['source_step_sha256']==job.source_sha256
    assert set(seal['files'])=={'dxf','svg','pdf','json'}
    dict(job.files)['pdf'].write_bytes(b'changed PDF')
    with pytest.raises(StaleBase):q._seal_drawing(job,job.files,0)


def test_missing_amendment_slot_retained_and_cannot_be_renumbered(tmp_path):
    q,jid=make_queue(tmp_path,render);q._execute(jid)
    first=q.put_version(jid,b'first');second=q.put_version(jid,b'second')
    before=second.path.read_bytes();first.path.unlink()
    restored=JobQueue(q._work);job=restored.get(jid)
    assert job.state==CRASHED
    assert [p.name for p in versions_of(job)]==['amended-1.dxf','amended-2.dxf']
    with pytest.raises(StaleBase):restored.put_version(jid,b'third')
    assert second.path.read_bytes()==before


def test_orphan_amendment_not_overwritten_or_guessed(tmp_path):
    q,jid=make_queue(tmp_path,render);q._execute(jid)
    orphan=q.get(jid).directory/'amended-1.dxf';orphan.write_bytes(b'unrecorded user work')
    with pytest.raises(StaleBase):q.put_version(jid,b'overwrite')
    assert orphan.read_bytes()==b'unrecorded user work'
