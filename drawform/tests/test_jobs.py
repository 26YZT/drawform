"""任务表与单 worker 线程。

选异步的代价写在这里，不藏在代码里：

* 任务表在进程内存里，**服务重启即全丢**，用户要重传。骨架阶段接受，
  但私有化交付文档必须写明，否则客户会以为上传记录是持久的。
* 只有一个 worker：OCCT 在多线程下的安全性没有验证过，串行既避开这个问题，
  也让"排队"是真的在排队而不是装饰。
* 产物按任务号落目录，**没有自动清理**。清理策略等有真实用量之后再定，现在定是猜。
"""
import hashlib
import time

import pytest

from drawform.service.jobs import (
    CRASHED,
    DRAWN,
    REFUSED,
    JobQueue,
    StaleBase,
    head_of,
)


def _drain(queue, job_id, timeout=120.0):
    """等到任务进入终态。超时就是没跑完，别让测试挂死。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = queue.get(job_id)
        if job.finished:
            return job
        time.sleep(0.02)
    raise AssertionError(f"任务 {job_id} 在 {timeout} 秒内没有结束"
                         f"（最后状态 {queue.get(job_id).state}）")


def test_a_good_step_completes_with_all_four_files(bearing_seat_step, tmp_path):
    queue = JobQueue(tmp_path / "work")
    queue.start()
    try:
        job = _drain(queue, queue.submit(bearing_seat_step))

        assert job.state == DRAWN, f"没画成：{job.error}"
        assert {kind for kind, _ in job.files} == {"dxf", "svg", "pdf", "json"}
        assert all(path.exists() for _, path in job.files)
    finally:
        queue.stop()


def test_a_refused_part_is_reported_as_refused_not_as_a_crash(refused_step,
                                                             tmp_path):
    """被门拦下是**业务结论**，不是系统故障。

    两者混成一个 failed 状态，网页上就只能显示"出图失败"，
    而工程师真正需要看到的是"输入含多个实体，需要先拆成单体零件"。
    """
    queue = JobQueue(tmp_path / "work")
    queue.start()
    try:
        job = _drain(queue, queue.submit(refused_step))

        assert job.state == REFUSED
        assert job.failed, "拒图必须带机器认的项目 id"
        assert job.reasons, "拒图必须带给人看的中文理由"
        assert job.files == (), "被拦下的任务不该挂着文件路径"
    finally:
        queue.stop()


def test_a_broken_input_fails_the_job_instead_of_the_worker(not_a_step, tmp_path):
    """worker 线程里抛出未捕获异常 = 队列从此不动，而接口还在返回 queued。

    这种挂法比崩得更难看：服务看起来活着，只是所有后来的任务永远排着。
    """
    queue = JobQueue(tmp_path / "work")
    queue.start()
    try:
        job = _drain(queue, queue.submit(not_a_step))
        assert job.state == CRASHED
        assert job.error

        later = _drain(queue, queue.submit(not_a_step))
        assert later.state == CRASHED, "第一个任务把 worker 弄死之后，队列不会再动"
    finally:
        queue.stop()


def test_effective_versions_number_up_and_a_stale_base_is_refused(
        bearing_seat_step, tmp_path):
    """版本号分配与基线校验必须在同一次加锁里做完。

    分两步（先问下一号、再写文件）时，两个人同时回写会都拿到 1 —— 后写的把前一个的
    生效版本覆盖掉，而两条审计行都写着"我已生效"。这张图到底哪一版算数就再也问不出了。
    """
    queue = JobQueue(tmp_path / "work")
    queue.start()
    try:
        job = _drain(queue, queue.submit(bearing_seat_step))
        draft = dict(job.files)["dxf"]
        base = hashlib.sha256(draft.read_bytes()).hexdigest()

        first = queue.put_version(job.id, b"first", base)
        assert first.number == 1
        assert first.path.read_bytes() == b"first"
        assert first.parent == draft, "第一版的父版就是机器底稿"

        with pytest.raises(StaleBase):
            queue.put_version(job.id, b"second", base)
        assert list(first.path.parent.glob("amended-2*")) == [], "基线不对的回写不许落盘"

        second = queue.put_version(
            job.id, b"second", hashlib.sha256(first.path.read_bytes()).hexdigest())
        assert second.number == 2
        assert second.parent == first.path, "差异只能与直接父版比"

        current = queue.get(job.id)
        assert [kind for kind, _ in current.files][-2:] == ["amended-1", "amended-2"]
        assert head_of(current) == second.path
        assert draft.read_bytes() != b"first", "机器底稿不参与覆盖"
    finally:
        queue.stop()
