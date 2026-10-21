"""出图主流程：一个 STEP 进来，三个文件与一份判定清单出去。

这条链路此前只活在 `examples/build_three_view.py` 里，带着 print，没法被服务或测试复用。
提成函数之后，网页端、命令行与测试调的是同一个序列 —— 否则服务里抄一遍，
两边迟早分家（与 SVG/PDF 各算一次尺寸线是同一类错）。
"""
import pytest

from drawform.delivery import ALLOW, DeliveryBlocked
from drawform.pipeline import render_part


def test_one_step_produces_four_files_and_an_allow_verdict(bearing_seat_step, tmp_path):
    """三个出口必须一起出。少一个就是交付物缺一门，而 print 出来的日志不会有人查。"""
    part = render_part(bearing_seat_step, tmp_path)

    assert {kind for kind, _ in part.files} == {"dxf", "svg", "pdf", "json"}
    for kind, path in part.files:
        assert path.exists(), f"{kind} 没有落盘"
        assert path.stat().st_size > 0, f"{kind} 是个空文件"
    assert part.verdict.decision == ALLOW


def test_a_refused_part_leaves_no_files_behind(refused_step, tmp_path):
    """门拦下来时目录里必须干干净净。

    多实体输入必须在投影前拒绝。拦下却留下半个 DXF，客户点开就是一个残缺文件 ——
    "不过关就不出图"里最容易被忘掉的就是"不出"这两个字。
    """
    with pytest.raises(DeliveryBlocked) as caught:
        render_part(refused_step, tmp_path)

    assert list(tmp_path.iterdir()) == [], \
        f"被拦下了却留下 {[p.name for p in tmp_path.iterdir()]}"
    assert caught.value.verdict.decision == "block"


def test_content_too_big_for_the_sheet_is_a_refusal_not_a_crash(monkeypatch, tmp_path,
                                                                bearing_seat_step):
    """装不下是**这张纸的判定**，不是系统故障。

    混成 crashed 的话，工程师看到的是"程序挂了"，看不到"换 A2 就行"——
    M6-1 定三态（drawn / refused / crashed）就是为了这一类区分。
    """
    import drawform.pipeline as pipeline
    from drawform.delivery import DeliveryBlocked
    from drawform.output.drawing import SheetOverflow

    def refuse(*args, **kwargs):
        raise SheetOverflow("A3 幅面放不下：内容落到图框之外")

    monkeypatch.setattr(pipeline, "write_svg", refuse)

    with pytest.raises(DeliveryBlocked) as caught:
        render_part(bearing_seat_step, tmp_path)

    assert "sheet_overflow" in caught.value.failed
    assert any("放不下" in reason for reason in caught.value.verdict.reasons)
