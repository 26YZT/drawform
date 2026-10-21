import io,json,time
from pathlib import Path
import pytest
from werkzeug.datastructures import MultiDict
from drawform.service.jobs import JobQueue
from drawform.service.maintenance import _index_rows
from tests.test_live_questions import service_factory,AUTH
from tests.test_pre_reference_service import wait

def upload(service,step,sheet=None):
    data={'step':(io.BytesIO(step.read_bytes()),'paper.step')}
    if sheet is not None:data['sheet']=sheet
    response=service.http.post('/jobs',headers=AUTH,data=data,content_type='multipart/form-data')
    assert response.status_code==202,response.data
    return response.get_json()['job_id']

@pytest.mark.parametrize('bad',['','A1','A4','a2'])
def test_bad_paper_never_creates_a_task(service_factory,bearing_seat_step,bad):
    service=service_factory(seeded=False)
    reply=service.http.post('/jobs',headers=AUTH,data={'sheet':bad,'step':(io.BytesIO(bearing_seat_step.read_bytes()),'part.step')})
    assert reply.status_code==400 and service.queue.listing()==()
    assert not (service.work/'jobs.jsonl').exists()

def test_duplicate_paper_choice_is_rejected(service_factory,bearing_seat_step):
    service=service_factory(seeded=False)
    data=MultiDict([('sheet','A2'),('sheet','A3'),('step',(io.BytesIO(bearing_seat_step.read_bytes()),'part.step'))])
    assert service.http.post('/jobs',headers=AUTH,data=data).status_code==400
    assert not service.queue.listing()

@pytest.mark.parametrize('sheet',['A2',None])
def test_paper_survives_render_and_queue_reload(service_factory,bearing_seat_step,sheet):
    service=service_factory(seeded=False);job=upload(service,bearing_seat_step,sheet);wait(service,job,'drawn')
    expected=sheet or 'A3'
    doc=service.http.get(f'/jobs/{job}/files/json',headers=AUTH).get_json()
    assert doc['sheet']==expected
    detail=service.http.get(f'/jobs/{job}?detail=1',headers=AUTH).get_json()
    assert detail['sheet']==detail['context']['sheet']==expected
    service.queue.stop();again=JobQueue(service.work)
    assert again.get(job).sheet==expected

def test_old_job_without_paper_retains_a3_and_invalid_record_is_not_guessed(service_factory,bearing_seat_step):
    service=service_factory(seeded=False);job=upload(service,bearing_seat_step);wait(service,job,'drawn');service.queue.stop()
    path=service.work/'jobs.jsonl';rows=[json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:row.pop('sheet',None)
    path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    assert JobQueue(service.work).get(job).sheet=='A3'
    rows[-1]['sheet']='A1';path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    with pytest.raises(ValueError,match='纸幅'):JobQueue(service.work)
    valid,damaged=_index_rows(path.read_text())
    assert damaged

def test_a2_answer_recalculation_keeps_paper(service_factory,bearing_seat_step):
    service=service_factory(seeded=False);job=upload(service,bearing_seat_step,'A2');wait(service,job,'drawn')
    questions=service.http.get(f'/jobs/{job}/questions',headers=AUTH).get_json()['questions'];q=next(q for q in questions if q['kind']=='special_tolerance')
    reply=service.http.post(f'/jobs/{job}/answers',headers=AUTH,json={'reviewer':'paper test','answers':[{'question_id':q['id'],'option_id':q['options'][0]['id']}]})
    assert reply.status_code==202,reply.data
    wait(service,job,'drawn')
    data=service.http.get(f'/jobs/{job}/files/json',headers=AUTH).get_json()
    assert data['sheet']=='A2'
