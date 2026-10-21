"""取数体检 CLI：对一个目录里的图纸逐张判定"能不能用、能用于哪几类对照"。

判定逻辑在 `drawform.eval.intake`（有测试与变异测试守着），这里只做遍历和中文渲染。

用法（重现方式）：
    cd drawform && PYTHONPATH=src ../spike/.venv/bin/python ../research/check_intake.py <图纸目录>
"""

from __future__ import annotations

import pathlib
import sys

from drawform.eval.intake import (
    USE_LAYOUT,
    USE_PAIRING,
    USE_RULER,
    USE_TOLERANCE,
    inspect_dxf,
)

USE_CN = {
    USE_LAYOUT: "版式与字段词汇",
    USE_PAIRING: "尺寸/基准选择对照",
    USE_RULER: "图面互斥尺子",
    USE_TOLERANCE: "公差写法口径",
}
FLAG_CN = {
    "no_native_dimensions": "读不到原生 DIMENSION（本来没标、或被批处理炸开，两种都在这里分不开）",
    "no_vector_lines": "没有矢量线",
    "garbled_text": "文字乱码",
    "no_block_attributes": "没有块属性（标题栏可能是空图或已被打散）",
    "proxy_entities": "含代理对象，转 DXF 时可能丢内容",
}
REASON_CN = {"not_dxf": "打不开：不是 ASCII DXF（DWG 或二进制 DXF 都要先转）"}


def _sheets(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in root.rglob("*")
                  if p.suffix.lower() in (".dxf", ".dwg") and p.is_file())


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    root = pathlib.Path(argv[0])
    sheets = _sheets(root)
    if not sheets:
        print(f"{root} 里没有 .dxf/.dwg 文件")
        return 1

    usable = 0
    for path in sheets:
        report = inspect_dxf(path)
        if not report.ok:
            print(f"{path.name:<28} ✗ {REASON_CN.get(report.reason, report.reason)}")
            continue
        uses = "、".join(USE_CN[u] for u in report.uses) or "（没有可用用途）"
        flags = "；".join(FLAG_CN[f] for f in report.flags) or "无"
        print(f"{path.name:<28} {report.version:<6} 尺寸 {report.dims:>3} "
              f"文字 {report.texts:>3} 属性 {report.attribs:>3} "
              f"矢量 {report.vector:>4} 公差 {report.tolerances:>2} "
              f"→ {uses}\n{'':<28}   问题：{flags}")
        usable += USE_PAIRING in report.uses

    print(f"\n合计 {len(sheets)} 张，其中 {usable} 张能用于尺寸/基准对照"
          f"（其余只能当版式参照）")
    return 0 if usable else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
