"""默认相对工作路径必须能下载图纸，并固定在服务创建时的目录下。"""

from pathlib import Path

from tests.test_live_answers import AUTH, _answer, _drawn, _fit, _submit, _wait, service_factory


def test_relative_work_directory_serves_original_and_answer_files(plate_step, tmp_path, monkeypatch, service_factory):
    monkeypatch.chdir(tmp_path)
    service = service_factory(seeded=False, work=Path("out/jobs"))
    job_id, original, questions = _drawn(service, plate_step)
    for url in original["files"].values():
        response = service.http.get(url, headers=AUTH)
        assert response.status_code == 200 and response.data
    assert _submit(service, job_id, [_answer(_fit(questions))]).status_code == 202
    answered = _wait(service, job_id)
    assert answered["state"] == "drawn"
    for url in answered["files"].values():
        assert service.http.get(url, headers=AUTH).status_code == 200
    assert service.http.get(f"/jobs/{job_id}/effective", headers=AUTH).status_code == 200


def test_work_directory_stays_anchored_if_the_process_changes_directory(plate_step, tmp_path, monkeypatch, service_factory):
    monkeypatch.chdir(tmp_path)
    service = service_factory(seeded=False, work=Path("jobs"))
    job_id, _original, _questions = _drawn(service, plate_step)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    response = service.http.get(f"/jobs/{job_id}/questions", headers=AUTH)
    assert response.status_code == 200, response.data
    questions = response.get_json()["questions"]
    assert _submit(service, job_id, [_answer(_fit(questions))]).status_code == 202
    assert _wait(service, job_id)["state"] == "drawn"
    assert service.queue.get(job_id).directory == tmp_path / "jobs" / job_id
    assert not (other / "jobs").exists()
