"""任务表落盘：重启之后，生效链还在。

`JobQueue` 原先把任务表只放在内存里，注释写着"骨架阶段接受，用户要重传"。
那个前提在生效版本上线之后**已经不成立**：以前重启只是重新上传一个 STEP（便宜），
现在重启会把工程师改出来、已经生效的那几版图**彻底找不回来** —— 文件还在盘上，
但索引没了，`/effective`、`/diff`、交付包全部指向不存在的东西。

这一层只补一件事：**任务表是盘上那份 JSONL 的缓存**，不是唯一的事实。
"""

from __future__ import annotations

import json
import pathlib
import time

from drawform.service.jobs import DRAWN, JobQueue, head_of, versions_of


def _drain(queue, job_id, timeout=120.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = queue.get(job_id)
        if job.finished:
            return job
        time.sleep(0.02)
    raise AssertionError(f"任务 {job_id} 没在 {timeout} 秒内结束")


def test_a_finished_job_survives_a_restart(bearing_seat_step, tmp_path):
    work = tmp_path / "work"
    first = JobQueue(work)
    first.start()
    try:
        job_id = first.submit(bearing_seat_step)
        assert _drain(first, job_id).state == DRAWN
    finally:
        first.stop()

    second = JobQueue(work)
    job = second.get(job_id)

    assert job.state == DRAWN
    assert {kind for kind, _ in job.files} >= {"dxf", "svg", "pdf", "json"}
    assert all(path.exists() for _, path in job.files), "重启后不许挂着找不到的文件"


def test_the_effective_chain_survives_a_restart(bearing_seat_step, tmp_path):
    """这条才是重启真正会弄丢的东西：人改出来的生效版本。"""
    work = tmp_path / "work"
    first = JobQueue(work)
    first.start()
    try:
        job_id = first.submit(bearing_seat_step)
        job = _drain(first, job_id)
        draft = dict(job.files)["dxf"]
        version = first.put_version(job_id, b"v1",
                                    __import__("hashlib").sha256(
                                        draft.read_bytes()).hexdigest())
    finally:
        first.stop()

    second = JobQueue(work)
    restored = second.get(job_id)

    assert head_of(restored) == version.path
    assert head_of(restored).read_bytes() == b"v1"
    assert [k for k, _ in restored.files].count("amended-1") == 1, "别把同一版登记两遍"


def test_a_manually_removed_job_is_not_resurrected(bearing_seat_step, tmp_path):
    """被清理掉的任务目录不该在重启时诈尸成"图还在"。"""
    work = tmp_path / "work"
    # 仅移除本测试临时目录，模拟人工确认后已删除的任务；服务不自动删除。
    import shutil
    queue = JobQueue(work)
    job_id = queue.submit(bearing_seat_step)
    shutil.rmtree(work / job_id)

    reloaded = JobQueue(work)
    try:
        reloaded.get(job_id)
    except KeyError:
        return
    raise AssertionError(f"目录已删的 {job_id} 被重新装回了任务表")


def test_a_running_job_is_not_resumed_as_done(bearing_seat_step, tmp_path):
    """重启时还在排队的任务：worker 线程已经死了，记成 drawn 就是假账。"""
    work = tmp_path / "work"
    queue = JobQueue(work)
    job_id = queue.submit(bearing_seat_step)      # 不起 worker，停在 queued
    del queue

    reloaded = JobQueue(work)
    assert reloaded.get(job_id).state == "crashed", "没跑完的任务要能重跑，不能装作有图"
    assert reloaded.get(job_id).error


def test_the_index_is_append_only_and_survives_a_half_written_line(
        bearing_seat_step, tmp_path):
    """断电时最后一行常常是半截 JSON。跳过它，但别把整张表丢掉。"""
    work = tmp_path / "work"
    queue = JobQueue(work)
    queue.start()
    try:
        job_id = queue.submit(bearing_seat_step)
        _drain(queue, job_id)
    finally:
        queue.stop()
    index = work / "jobs.jsonl"
    index.write_text(index.read_text(encoding="utf-8") + '{"job": "cut', encoding="utf-8")

    reloaded = JobQueue(work)

    assert reloaded.get(job_id).state == DRAWN
    assert len(reloaded.listing()) == 1


def test_a_recorded_drawing_that_is_no_longer_on_disk_is_not_claimed(tmp_path):
    """索引说"已出图"而文件不在：宁可报丢了，也不要挂着空路径让人下载失败。"""
    work = tmp_path / "work"
    directory = work / "abc123def456"
    directory.mkdir(parents=True)
    (directory / "model.step").write_bytes(b"ISO-10303-21;")
    (work / "jobs.jsonl").write_text(
        json.dumps({"job": "abc123def456", "state": "drawn", "source": "model.step",
                    "files": [["dxf", "abc123def456.dxf"], ["json", "abc123def456.json"]],
                    "failed": [], "error": ""}) + "\n", encoding="utf-8")

    job = JobQueue(work).get("abc123def456")

    assert job.state == "crashed", job.state
    assert job.error and "盘" in job.error
    assert job.files == (), "不许挂着找不到的文件"


def test_the_restarted_queue_can_still_take_new_work(bearing_seat_step,
                                                    plate_step, tmp_path):
    work = tmp_path / "work"
    first = JobQueue(work)
    first.start()
    try:
        old = _drain(first, first.submit(bearing_seat_step))
    finally:
        first.stop()

    second = JobQueue(work)
    second.start()
    try:
        new_id = second.submit(plate_step)
        fresh = _drain(second, new_id)
        assert fresh.state == DRAWN
        assert second.get(old.id).state == DRAWN, "老任务与新任务要在同一张表里"
        assert len(second.listing()) == 2
    finally:
        second.stop()
