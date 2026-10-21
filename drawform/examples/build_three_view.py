"""STEP → 三视图 + 剖视 → DXF/SVG/PDF → PNG 预览，用于人工目视检查图纸像不像一张工程图。

出图本身走 `drawform.pipeline.render_part`（服务端与测试走的是同一个函数），
这里只多做一件服务不做的事：把 DXF 渲染成 PNG 给人眼看。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from drawform.pipeline import render_part

OUT = Path(__file__).resolve().parents[1] / "out"


def build(step_path: Path, stem: str, mode: str = "first_angle") -> Path:
    part = render_part(step_path, OUT, stem, mode=mode, section_at=25.0)
    dxf_path = dict(part.files)["dxf"]

    print(f"判定 {part.verdict.decision}；尺寸 {len(part.dimensions)} 条: "
          f"{[d.text for d in part.dimensions]}")
    print(f"注记: {list(part.notes)}")
    for kind, path in part.files:
        print(f"→ {path}")

    _render(dxf_path, OUT / f"{stem}.png")
    return dxf_path


def _render(dxf_path: Path, png_path: Path) -> None:
    import ezdxf
    from ezdxf import bbox
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from matplotlib.figure import Figure

    doc = ezdxf.readfile(str(dxf_path))
    extents = bbox.extents(doc.modelspace())
    margin = 0.12 * max(extents.size.x, extents.size.y)

    figure = Figure(figsize=(14, 9), dpi=140)
    axes = figure.add_axes([0.02, 0.02, 0.96, 0.96])
    axes.set_xlim(extents.extmin.x - margin, extents.extmax.x + margin)
    axes.set_ylim(extents.extmin.y - margin, extents.extmax.y + margin)
    axes.set_facecolor("white")
    context = RenderContext(doc)
    frontend = Frontend(context, MatplotlibBackend(axes),
                        config=Configuration(color_policy=ColorPolicy.BLACK,
                                             background_policy=BackgroundPolicy.WHITE))
    frontend.draw_layout(doc.modelspace(), finalize=True)
    figure.savefig(str(png_path), facecolor="white")


if __name__ == "__main__":
    build(OUT / "seat.step", "seat_three_view")
