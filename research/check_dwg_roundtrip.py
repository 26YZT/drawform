"""DWG 往返判定 CLI：把 CAD 机上跑回来的 `back/*.dxf` 与原始样本逐张比。

判定逻辑在 `drawform.eval.dwg_roundtrip`（5 项测试，对照组是"自家图跟自己比必须通过"）。
这里只做遍历与中文渲染。

跑法（重现方式）：
    cd drawform && PYTHONPATH=src ../spike/.venv/bin/python \
        ../research/check_dwg_roundtrip.py ../tools/dwg_verify/samples <发回来的>/back
"""

from __future__ import annotations

import pathlib
import sys

from drawform.eval.dwg_roundtrip import compare_pair

FAIL_CN = {
    "source_unreadable": "样本读不出来",
    "target_unreadable": "导回的 DXF 读不出来（是不是直接把 .dwg 发回来了？）",
    "dimensions_lost": "尺寸实体变少（被炸成线条与文字了）",
    "labels_lost": "图上显示的数字对不上",
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    samples = pathlib.Path(argv[0])
    returned = pathlib.Path(argv[1])

    rows = []
    for src in sorted(samples.glob("*.dxf")):
        dst = returned / src.name
        if not dst.exists():
            rows.append((src.name, None))
            continue
        rows.append((src.name, compare_pair(src, dst)))

    failed = 0
    for name, verdict in rows:
        if verdict is None:
            print(f"{name:<18} ✗ 没在 {returned.name}/ 里找到导回的文件")
            failed += 1
            continue
        if verdict.ok:
            print(f"{name:<18} ✓ 尺寸 {verdict.src_dims} → {verdict.dst_dims}，"
                  f"显示数字全部一致"
                  + (f"（多出 {len(verdict.added)} 条，见下）" if verdict.added else ""))
            continue
        failed += 1
        why = "；".join(FAIL_CN.get(f, f) for f in verdict.failed)
        print(f"{name:<18} ✗ {why}")
        if verdict.lost:
            print(f"{'':<18}   丢掉的显示值：{', '.join(verdict.lost[:12])}")
        if verdict.added:
            print(f"{'':<18}   多出的显示值：{', '.join(verdict.added[:12])}")

    print(f"\n合计 {len(rows)} 张，{len(rows) - failed} 张往返无损。")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
