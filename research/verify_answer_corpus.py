"""让既有机器参照语料串行穿过真实问题/答案服务及文字定位层。"""

import argparse
import faulthandler
import hashlib
import json
import math
from pathlib import Path
import time
import xml.etree.ElementTree as ET

from drawform.service import app as app_module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(corpus, output):
    output.mkdir(parents=True, exist_ok=False)
    samples = sorted(corpus.glob("samples/*/model.step"))
    if not samples:
        raise ValueError("没有找到既有机器参照 STEP 语料")
    source_root = Path(__file__).resolve().parents[1] / "drawform/src"
    source_hashes = {str(p): sha(p) for p in sorted(source_root.rglob("*.py"))}
    input_hashes = {str(p): sha(p) for p in samples}
    queue_class = app_module.JobQueue
    queues = []

    class ObservedQueue(queue_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            queues.append(self)

    app_module.JobQueue = ObservedQueue
    application = app_module.create_app(output / "work", token="synthetic-corpus-verification")
    application.testing = True
    client = application.test_client()
    headers = {"Authorization": "Bearer synthetic-corpus-verification"}
    rows = []
    journal = output / "cases.jsonl"

    def wait(job):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            response = client.get(f"/jobs/{job}", headers=headers)
            assert response.status_code == 200
            result = response.get_json()
            if result["state"] in ("drawn", "refused", "crashed"):
                return result
            time.sleep(.05)
        raise TimeoutError("任务未在 90 秒内进入终态；停止跑批，不排入更多任务")

    def get(url):
        response = client.get(url, headers=headers)
        assert response.status_code == 200, (url, response.status_code)
        return response

    try:
        for number, step in enumerate(samples, 1):
            row = {"part": step.parent.name, "step_sha256": input_hashes[str(step)]}
            timed_out = False
            started = time.monotonic()
            try:
                with step.open("rb") as handle:
                    response = client.post("/jobs", headers=headers,
                                           data={"step": (handle, "model.step")}, content_type="multipart/form-data")
                assert response.status_code == 202
                job = row["job"] = response.get_json()["job_id"]
                initial = wait(job)
                row.update(initial_state=initial["state"], initial_failed=initial["failed"])
                if initial["state"] == "refused":
                    assert client.get(f"/jobs/{job}/questions", headers=headers).status_code == 409
                    row["result"] = "initially_refused"
                else:
                    assert initial["state"] == "drawn", initial
                    question_response = get(f"/jobs/{job}/questions").get_json()
                    questions = question_response["questions"]
                    row.update(questions=len(questions), truncated=question_response["truncated"])
                    for question in questions:
                        assert question["options"] and question["allow_unknown"] is True
                        assert len({o["id"] for o in question["options"]}) == len(question["options"])
                    original = {kind: get(url).data for kind, url in initial["files"].items()}
                    assert json.loads(original["json"])["annotations"] == []
                    answers, chosen = [], False
                    for question in questions:
                        fit = (not chosen and question["kind"] == "special_tolerance"
                               and any(o["id"] == "H7" for o in question["options"]))
                        answers.append({"question_id": question["id"], "option_id": "H7" if fit else None})
                        chosen = chosen or fit
                    response = client.post(f"/jobs/{job}/answers", headers=headers,
                                           json={"reviewer": "机器语料验证（非制图审核）", "answers": answers})
                    assert response.status_code == 202, response.data
                    current = wait(job)
                    assert current["state"] == "drawn", current
                    document = get(current["files"]["json"]).get_json()
                    annotations = document["annotations"]
                    assert not questions or document["annotation_base_version"] == 0
                    assert not document["questions"]
                    assert document["verdict"]["decision"] == "allow"
                    assert isinstance(document["verdict"]["warnings"], list)
                    svg = ET.fromstring(get(current["files"]["svg"]).data)
                    visible = {"".join(node.itertext()) for node in svg.findall("{*}text")}
                    for annotation in annotations:
                        assert all(math.isfinite(n) for n in annotation["box"])
                        assert annotation["box"][2] > 0 and annotation["box"][3] > 0
                        assert annotation["after"] is None or annotation["after"] in visible
                    if chosen:
                        assert annotations and any("H7" in (a["after"] or "") for a in annotations)
                    else:
                        assert annotations == []
                    for kind, data in original.items():
                        assert get(initial["files"][kind]).data == data
                    before_values = [c["value"] for c in json.loads(original["json"])["constraints"]]
                    assert [c["value"] for c in document["constraints"]] == before_values
                    assert get(current["files"]["pdf"]).data.startswith(b"%PDF")
                    row.update(result="answers_verified", fit_selected=chosen,
                               annotations=len(annotations), diff=current.get("diff_from_previous", {}))
            except Exception as error:
                row.update(result="error", error=f"{type(error).__name__}: {error}")
                timed_out = isinstance(error, TimeoutError)
            row["seconds"] = time.monotonic() - started
            rows.append(row)
            with journal.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
            print(f"[{number}/{len(samples)}] {row['part']} {row['result']}", flush=True)
            if timed_out:
                break
    finally:
        for queue in queues:
            queue.stop()
        app_module.JobQueue = queue_class
    result = {"samples": len(samples), "processed": len(rows),
              "answers_verified": sum(r["result"] == "answers_verified" for r in rows),
              "initially_refused": [r for r in rows if r["result"] == "initially_refused"],
              "errors": [r for r in rows if r["result"] == "error"],
              "input_unchanged": all(sha(Path(p)) == digest for p, digest in input_hashes.items()),
              "source_unchanged": all(sha(Path(p)) == digest for p, digest in source_hashes.items()),
              "qualification": "机器生成参照语料，存在同源偏差；脚本模拟选择 H7/不知道，不是工程师审核或正式采纳率。"}
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    faulthandler.enable()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.corpus, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(result["errors"]) or result["processed"] != result["samples"]
               or not result["input_unchanged"] or not result["source_unchanged"])


if __name__ == "__main__":
    raise SystemExit(main())
