"""标题栏。这一单元真正要守的只有一条：**不知道的就留空。**

尺寸与分格未经 GB/T 10609.1 原文核对（公开网页取不到分格尺寸），所以这里
不测"符合标准"，只测三件不依赖数表、且错了会致命的事：
位置在图框右下角、缺字段留空而不是编一个、以及这条未验证状态本身要出现在
出图说明里。
"""

import json

import pytest

from drawform.output.sheet import make_frame
from drawform.output.titleblock import BLOCK_H, BLOCK_W, TitleMeta, title_block


@pytest.fixture
def inner():
    return make_frame("A3").inner


def test_the_block_sits_in_the_bottom_right_of_the_design_area(inner):
    """GB/T 10609.1 把标题栏钉在图框右下角。模型坐标 y 向上，所以右下角是 `y0` 那一侧
    —— 这条曾经把断言写成贴着 `y1`，测试名字说"右下角"、断言量的是右上角，一起放过了 bug。
    """
    lines, _ = title_block(TitleMeta(), inner)
    x0, y0, x1, y1 = inner

    box = max(lines, key=lambda run: len(run))
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]

    assert min(xs) == pytest.approx(x1 - BLOCK_W) and max(xs) == pytest.approx(x1)
    assert min(ys) == pytest.approx(y0) and max(ys) == pytest.approx(y0 + BLOCK_H)
    assert max(ys) <= y1 + 1e-6, "标题栏不许越出图框"


def test_fields_we_do_not_have_stay_blank_instead_of_being_invented(inner):
    """STEP 里没有图样代号、材料、制图人。编出来就是假图纸。

    这是本模块唯一不可谈判的性质，所以它值得一条测试。
    """
    lines, texts = title_block(TitleMeta(), inner)

    values = [t for t, _x, _y, h in texts if h > 3.0]      # 值用大一号字
    labels = {t for t, _x, _y, h in texts if h <= 3.0}

    assert "钢" not in values and "Q235" not in values
    assert "" not in values, "空值不该产生文字对象：留白格靠的是没有文字对象"
    # 四区标签齐了才算按图2 摆；更改区以前整块缺失，是这次最要紧的补项
    assert {"标记", "处数", "分区", "更改文件号"} <= labels, "更改区又没了"
    assert {"设计", "审核", "工艺", "标准化", "批准"} <= labels, "签字区角色不对"
    assert {"材料标记", "阶段标记", "重量", "比例", "共 张 第 张"} <= labels, "其他区缺项"
    assert {"单位名称", "图样名称", "图样代号"} <= labels, "名称及代号区缺项"


def test_only_the_general_tolerance_is_a_fact_we_actually_own(inner):
    """一般公差是我们自己定的策略，所以它是唯一一格能预先填上的内容。"""
    meta = TitleMeta.from_step_name("part_0001", tolerance_note="一般公差 ISO 2768-m")
    lines, texts = title_block(meta, inner)

    assert any(t == "一般公差 ISO 2768-m" for t, _x, _y, _h in texts)
    assert meta.name == "" and meta.material == "", "文件名不能冒充零件名称"
    assert meta.code == "part_0001"


def test_the_three_columns_are_the_verified_widths(inner):
    """80 / 50 / 50 是图A.1 标出来的，不是等分凑的 —— 所以钉住。"""
    from drawform.output.titleblock import CHANGE_W, NAME_W, OTHER_W

    lines, _ = title_block(TitleMeta(), inner)
    _, _, x1, y1 = inner

    assert (CHANGE_W, OTHER_W, NAME_W) == (80.0, 50.0, 50.0)
    assert CHANGE_W + OTHER_W + NAME_W == 180.0
    verticals = sorted({p[0] for run in lines for p in run})
    assert any(abs(v - (x1 - 100.0)) < 1e-6 for v in verticals), "左列 80 的分隔线不在"
    assert any(abs(v - (x1 - 50.0)) < 1e-6 for v in verticals), "中列 50 的分隔线不在"


def test_the_unverified_geometry_is_declared_as_an_assumption(plate_step):
    """没核对标准原文这件事本身必须可见，不能被画好的格子掩盖。"""
    from drawform.geometry.loader import import_step
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.features import extract_features
    from drawform.dimensioning.place import place_scheme, place_datum_marks
    from drawform.output.assumptions import assumptions_for

    model = import_step(plate_step)
    features = extract_features(model.shape, model.bbox)
    scheme = derive_scheme(model, features)
    from drawform.geometry.projection import FRONT, TOP, LEFT, project_view
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    dimensions = place_scheme(scheme, projections)
    marks = place_datum_marks(scheme, projections, dimensions)

    ids = {item["id"] for item in assumptions_for(model, scheme, dimensions, marks)}

    assert "title-block-unverified" in ids


def test_content_is_lifted_clear_of_the_title_block(plate_step):
    """内容居中后不能压在标题栏上（实测有 4 件真实零件会压上）。

    上移只补"刚好让开"的量，不预留整条带 —— 预留会把本来放得下的件
    白判成放不下，那是拿正确性换一个虚构的整洁。
    """
    from drawform.geometry.loader import import_step
    from drawform.geometry.features import extract_features
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.dimensioning.place import place_scheme
    from drawform.geometry.projection import FRONT, TOP, LEFT, project_view
    from drawform.geometry.layout import DEFAULT_GAP, layout_views
    from drawform.output.titleblock import BLOCK_H, BLOCK_W
    from drawform.output.sheet import TITLE_CLEAR, center_on_frame, content_box, make_frame

    model = import_step(plate_step)
    features = extract_features(model.shape, model.bbox)
    scheme = derive_scheme(model, features)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    dimensions = place_scheme(scheme, projections)
    frame = make_frame("A4")          # 故意选小图幅，逼出重叠
    placed = layout_views(tuple(projections.values()), gap=DEFAULT_GAP + 10.0)
    inner = frame.inner
    rect = (inner[2] - BLOCK_W, inner[1], inner[2], inner[1] + BLOCK_H)

    lifted = center_on_frame(placed, frame, dimensions, (), title_block=rect)
    plain = center_on_frame(placed, frame, dimensions, ())
    box = content_box(lifted, dimensions)
    plain_box = content_box(plain, dimensions)

    assert not (plain_box[2] <= rect[0] or plain_box[0] >= rect[2]
                or plain_box[3] <= rect[1] or plain_box[1] >= rect[3]), "先证明未避让布局确实与标题栏重叠"
    assert box[1] > plain_box[1], "该让的时候没让：内容仍压在标题栏区域"
    assert box[1] >= rect[3] + TITLE_CLEAR, "上移不够，还压着"


def test_the_general_tolerance_is_written_once_on_the_sheet(plate_step):
    """标题栏已经带"一般公差 ISO 2768-m"，左下角再抄一遍是两张嘴说同一句话。"""
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.features import extract_features
    from drawform.geometry.layout import layout_views
    from drawform.geometry.loader import import_step
    from drawform.geometry.projection import FRONT, LEFT, TOP, project_view
    from drawform.output.drawing import build_drawing

    model = import_step(plate_step)
    features = extract_features(model.shape, model.bbox)
    scheme = derive_scheme(model, features)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    placed = layout_views(tuple(projections.values()))
    meta = TitleMeta.from_step_name(plate_step.stem,
                                    tolerance_note="一般公差 ISO 2768-m")

    drawing = build_drawing(placed, notes=scheme.notes, title_meta=meta)
    hits = [t.content for t in drawing.texts if "2768" in t.content]

    assert len(hits) == 1, f"一般公差在图面上出现了 {len(hits)} 次：{hits}"
