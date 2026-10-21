"""真实语料回归：M1 内核在真零件上暴露过、但自建夹具复现不了的缺陷。

语料是本地下载的 cadsy 配对数据（37 MB，不进版本库），所以缺件时跳过而不是假装通过。
"""

from collections import Counter
from pathlib import Path

import pytest

from drawform.eval.corpus import load_corpus
from drawform.geometry.loader import import_step
from drawform.geometry.projection import FRONT, LEFT, TOP, project_view

CORPUS = Path(__file__).resolve().parents[2] / "corpus" / "cadsy"
needs_corpus = pytest.mark.skipif(
    not (CORPUS / "samples").is_dir(), reason="未下载 cadsy 语料")


def _extent(curves):
    points = [p for c in curves for p in c.sample()]
    return (max(p[0] for p in points) - min(p[0] for p in points),
            max(p[1] for p in points) - min(p[1] for p in points))


@needs_corpus
@pytest.mark.parametrize("name", ["part_0008", "part_0016", "part_0041"])
def test_reported_bbox_matches_the_projected_extents(name):
    """包围盒必须与正投影量出来的范围一致。

    part_0008 / part_0041：BRepBndLib.Add 的松包围盒把带 B 样条曲面的实体虚高到
    86.59 与 114.03（真实都是 80.00），比例会选错。
    part_0016：R2 圆角被 refit 按互补弧采样，投影宽 73.20 反而大于实体 X 范围 70.00。
    """
    model = import_step(CORPUS / "samples" / name / "model.step")
    dx, dy, dz = model.bbox

    front = _extent(list(project_view(model.shape, FRONT).visible)
                    + list(project_view(model.shape, FRONT).hidden))
    top = _extent(list(project_view(model.shape, TOP).visible)
                  + list(project_view(model.shape, TOP).hidden))

    assert front[0] == pytest.approx(dx, abs=0.05), f"{name} 正视宽 {front[0]} ≠ X {dx}"
    assert front[1] == pytest.approx(dz, abs=0.05), f"{name} 正视高 {front[1]} ≠ Z {dz}"
    assert top[0] == pytest.approx(dx, abs=0.05), f"{name} 俯视宽 {top[0]} ≠ X {dx}"
    assert top[1] == pytest.approx(dy, abs=0.05), f"{name} 俯视高 {top[1]} ≠ Y {dy}"


@needs_corpus
def test_part_0063_counterbore_is_paired_even_though_the_fillet_fools_through():
    """part_0063 真实图纸写 `⌀5 ⌴⌀8 ↧2`，但我们一度把 ⌀8 判成贯通孔而漏掉整条沉孔。

    根因不在沉孔配对，而在贯通判据：⌀8 脚下的台阶上有 R2 圆角，圆角把环带
    （半径 0.5–0.97r 的四道探针全在 2–3.88 mm）啃成空腔，于是那一端被读成"开口"。
    所以**配对不能拿 through 当门槛** —— 一个会被圆角污染的布尔值不该决定一条
    尺寸发不发。自建夹具复现不出这个圆角构型，故这条只能挂在真实语料上。
    """
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.features import extract_features

    model = import_step(CORPUS / "samples" / "part_0063" / "model.step")
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))

    counterbores = sorted(c.value for c in scheme.plan.constraints if c.dof == "counterbore")
    depths = sorted(c.value for c in scheme.plan.constraints if c.dof == "depth")

    assert counterbores == pytest.approx([8.0])
    assert 2.0 in [round(d, 2) for d in depths], f"沉孔深度 ↧2 丢了：{depths}"


@needs_corpus
def test_every_cylindrical_face_is_classified_in_or_out():
    """**有效**实体上的每个圆柱面都必须判出"孔"还是"外圆"，一个都不许悄悄丢掉。

    实测旧版单点探针会漏掉 12 个面 / 6 个零件：面在 u 方向被修剪过时，探针取的是
    **底层曲面**参数域的中点 (u=π)，落点根本不在面上 —— 内外两侧从 δ=1e-5 到 1 mm
    全给同一个答案。part_0018 因此整条 `4×⌀6,4 ↧10 EQUALLY SPACED ON ⌀60 PCD`
    的直径、深度、定位全丢。改成参数域撒点取多数后，12 个面只剩 4 个判不了，
    全部来自 part_0002 —— 而那个模型的 `BRepCheck_Analyzer` 本来就判定无效。
    无效的实体不是几何技巧问题，该由装载层标出来，所以这里只要求有效模型。
    """
    from drawform.geometry.features import classify_cylindrical_faces

    unclassified = []
    for sample in load_corpus(CORPUS):
        model = import_step(sample.step_path)
        if not model.is_valid:
            continue
        for diameter, kind in classify_cylindrical_faces(model.shape):
            if kind is None:
                unclassified.append((sample.sample_id, round(diameter, 2)))

    assert unclassified == [], f"{len(unclassified)} 个圆柱面没被判出内外: {unclassified[:8]}"


@needs_corpus
def test_loader_flags_a_model_whose_solid_is_invalid():
    """读进来的实体必须带"这个几何本身可不可信"的标记。

    part_0002 的实体 `BRepCheck_Analyzer` 判无效：体心落在实体外，4 个圆柱面的内外
    怎么探都给同一个答案。对它硬做特征识别只会产出**看似合理但错误**的图纸。
    本阶段先把事实标出来（下游交付门必须消费它），不做几何修复。
    """
    broken = import_step(CORPUS / "samples" / "part_0002" / "model.step")
    good = import_step(CORPUS / "samples" / "part_0001" / "model.step")

    assert broken.is_valid is False
    assert good.is_valid is True


@needs_corpus
def test_one_hole_split_into_several_faces_yields_one_feature_not_several():
    """一条孔被布尔运算拆成同轴、同直径、同轴向区间的多张圆柱面时，只能出一个特征。

    实测 65 个真实零件：252 个孔面里有 10 张是这种"同一个孔的碎片"，聚成 4 组，
    全部会被错发成数量标注 —— part_0034 写 `4×⌀58`、part_0063 写 `2×⌀5`。
    错发的数量比漏标更危险：它直接叫车间"钻 4 个"，而零件上只有 1 个。
    """
    from drawform.geometry.features import Bore, extract_features

    duplicated = []
    for sample in load_corpus(CORPUS):
        model = import_step(sample.step_path)
        bores = [b for b in extract_features(model.shape, model.bbox) if isinstance(b, Bore)]
        spots = Counter((round(b.diameter, 4),
                         tuple(round(c, 4) for c in b.axis_point),
                         tuple(round(t, 4) for t in (b.axis_span or ())))
                        for b in bores)
        hits = [spot for spot, count in spots.items() if count > 1]
        if hits:
            duplicated.append((sample.sample_id, len(hits), hits[0][0]))

    assert duplicated == [], f"同一条孔被拆成多个特征，会错发 N×: {duplicated}"


@needs_corpus
def test_ambiguous_faces_are_reported_as_unknown_not_guessed():
    """判不出内外的面必须返回 None，不许平票时猜「是孔」。

    part_0002 是语料里唯一 `BRepCheck` 判无效的实体，它有 4 个圆柱面的内外怎么探都
    给同一个答案。猜成孔会凭空长出 4 条不存在的孔径与定位 —— 比标错更糟的是**看着
    像对的**。这条测试守的就是"宁可交不出，不交假的"。
    """
    from drawform.geometry.features import classify_cylindrical_faces

    model = import_step(CORPUS / "samples" / "part_0002" / "model.step")
    kinds = [kind for _d, kind in classify_cylindrical_faces(model.shape)]

    assert None in kinds, "无效实体上的模糊面被强行判成了孔或外圆"


@needs_corpus
def test_part_0018_bolt_circle_is_detected_at_all():
    """part_0018 的 4×⌀6,4 螺栓孔必须被识别成孔，否则 PCD 与定位一起消失。"""
    from drawform.geometry.features import Bore, extract_features

    model = import_step(CORPUS / "samples" / "part_0018" / "model.step")
    bores = [b for b in extract_features(model.shape, model.bbox) if isinstance(b, Bore)]

    six_forty_four = [b for b in bores if abs(b.diameter - 6.4) < 0.01]
    assert len(six_forty_four) == 4, f"只识别到 {len(six_forty_four)} 个 ⌀6.4 孔"
    assert all(b.depth == pytest.approx(10.0, abs=0.05) for b in six_forty_four)


@needs_corpus
def test_every_corpus_part_that_passes_the_gate_produces_a_dxf(tmp_path):
    """交付层必须在真实语料上端到端跑过 —— 之前它一次都没跑过。

    `pcd` 尺寸的 `base` 是 None（分布圆钉在两个对角实圆心上，没有线性尺寸的基线位置），
    而 DXF 写出把所有非直径尺寸都送进 `add_linear_dim`。落位测试到 `PlacedDimension`
    就断了，出图测试只用没有螺栓圆的夹具，中间这条缝让 **15/65 件真实零件一张图都写不出来**，
    而 recall/precision 与交付门全都没察觉（尺寸确实"落位"了，只是写不出去）。
    """
    from dataclasses import replace

    from drawform.delivery import DeliveryBlocked, gate_for, require_allowed
    from drawform.dimensioning.place import place_scheme
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.centerlines import centerlines_for
    from drawform.geometry.features import extract_features
    from drawform.geometry.layout import layout_views
    from drawform.output.dxf import write_dxf

    crashed = []
    blocked = []
    for sample in load_corpus(CORPUS):
        try:
            model = import_step(sample.directory / "model.step")
            require_allowed(gate_for(model))
            features = extract_features(model.shape, model.bbox)
            scheme = derive_scheme(model, features)
            projections = {d.name: project_view(model.shape, d)
                           for d in (FRONT, TOP, LEFT)}
            views = [replace(v, centerlines=centerlines_for(v.projection, features))
                     for v in layout_views(tuple(projections.values()))]
            dims = place_scheme(scheme, projections)
            require_allowed(gate_for(model, scheme, dims))
            write_dxf(views, tmp_path / f"{sample.sample_id}.dxf",
                      dimensions=dims, notes=scheme.notes)
        except DeliveryBlocked as verdict:
            blocked.append((sample.sample_id, verdict.failed))
        except Exception as error:
            crashed.append(f"{sample.sample_id}: {error!r}")

    assert crashed == [], f"{len(crashed)} 件真实零件写不出 DXF：{crashed[:6]}"
    # 交付门拦下是**预期行为**（part_0002 无效实体就该被拦），所以这里只统计不断言


@needs_corpus
def test_datum_leaders_do_not_cross_dimension_lines_on_any_real_sheet():
    """基准引出线穿尺寸线：GB/T 不许符号线与尺寸线相交。

    ⚠️ 这条**必须走带 `datum_marks` 的那条路**。上面那条 text/line 的回归用的是
    不含代号的 `build_drawing`，所以代号方框与引出线从来不在它的视野里 ——
    我第一次量这个数时也量出了假的 0（符号折线 0 条），是"负向断言空转"的又一例。
    """
    from dataclasses import replace

    from drawform.delivery import DeliveryBlocked, gate_for, require_allowed
    from drawform.dimensioning.place import place_datum_marks, place_scheme
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.centerlines import centerlines_for
    from drawform.geometry.features import extract_features
    from drawform.geometry.layout import layout_views
    from drawform.output.collide import find_collisions
    from drawform.output.drawing import build_drawing

    leaders = 0
    boxed_text = 0
    for sample in load_corpus(CORPUS):
        try:
            model = import_step(sample.directory / "model.step")
            require_allowed(gate_for(model))
            features = extract_features(model.shape, model.bbox)
            scheme = derive_scheme(model, features)
            projections = {d.name: project_view(model.shape, d)
                           for d in (FRONT, TOP, LEFT)}
            placed = [replace(v, centerlines=centerlines_for(v.projection, features))
                      for v in layout_views(tuple(projections.values()))]
            dims = place_scheme(scheme, projections)
            marks = place_datum_marks(scheme, projections, dims)
            require_allowed(gate_for(model, scheme, dims))
        except DeliveryBlocked:
            continue
        hits = find_collisions(build_drawing(placed, dimensions=dims, notes=scheme.notes,
                                             datum_marks=marks))
        leaders += sum(1 for c in hits if c.kind == "leader/line")
        boxed_text += sum(1 for c in hits if c.kind == "text/line")

    assert leaders <= 160, f"引出线穿尺寸线 {leaders} 处，比滑开判据上线时的 155 退步了"
    assert boxed_text <= 330, f"算上代号之后字被线穿过 {boxed_text} 处"
    # ⚠️ 这条只承认"滑开的那一部分"。实测 74 个代号里 28 个棱长为 0（投影上基准就是个
    # 点，无处可滑）、40 个"沿棱滑不出去" —— 因为那条尺寸线横跨整条棱，竖直引出线
    # 在下方没有任何可避的位置。剩下的 155 处要靠两件不同的事解决，都还没做：
    # ① GB/T 1182 允许基准代号与尺寸线**对齐共线**，而 `_crosses` 把共线重叠算成相交
    #    —— 那是尺子偏严，需要真图纸来定这条豁免的边界；② 折线引出（先出小段再横着
    #    绕到尺寸线端点之外）。**别把这个数当成"避让已做完"。**


@needs_corpus
def test_labels_do_not_sit_on_geometry_they_could_have_slid_off():
    """字被别人的线穿过：真图对照组 0.4%，我们曾 668 处，躲线后 262 处。

    阈值不是目标值，是**不许回退**的地板：剩下的 262 处是尺寸线本来就画得太贴近轮廓，
    要靠落位层选走廊解决（#51），不是靠事后挪字。等落位改好就把这个数往下压。
    """
    from dataclasses import replace

    from drawform.delivery import DeliveryBlocked, gate_for, require_allowed
    from drawform.dimensioning.place import place_scheme
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.centerlines import centerlines_for
    from drawform.geometry.features import extract_features
    from drawform.geometry.layout import layout_views
    from drawform.output.collide import find_collisions
    from drawform.output.drawing import build_drawing

    total = 0
    for sample in load_corpus(CORPUS):
        try:
            model = import_step(sample.directory / "model.step")
            require_allowed(gate_for(model))
            features = extract_features(model.shape, model.bbox)
            scheme = derive_scheme(model, features)
            projections = {d.name: project_view(model.shape, d)
                           for d in (FRONT, TOP, LEFT)}
            placed = [replace(v, centerlines=centerlines_for(v.projection, features))
                      for v in layout_views(tuple(projections.values()))]
            dims = place_scheme(scheme, projections)
            require_allowed(gate_for(model, scheme, dims))
        except DeliveryBlocked:
            continue
        drawing = build_drawing(placed, dimensions=dims, notes=scheme.notes)
        total += sum(1 for c in find_collisions(drawing) if c.kind == "text/line")

    assert total <= 300, f"字被线穿过 {total} 处，比躲线上线时的 262 处退步了"


def test_no_two_labels_overlap_on_any_real_sheet():
    """65 件真零件一张都不许有两个归属不同的字叠在一起。

    真图纸对照组是 0 对（`measure_sheet_collisions.py`），我们曾是 47 对。
    这个数字只能由全库跑批守住：单件夹具上根本凑不出那种密集排布。
    """
    from dataclasses import replace

    from drawform.delivery import DeliveryBlocked, gate_for, require_allowed
    from drawform.dimensioning.place import place_scheme, place_datum_marks
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.centerlines import centerlines_for
    from drawform.geometry.features import extract_features
    from drawform.geometry.layout import layout_views
    from drawform.output.collide import find_collisions
    from drawform.output.drawing import build_drawing

    dirty = []
    for sample in load_corpus(CORPUS):
        try:
            model = import_step(sample.directory / "model.step")
            require_allowed(gate_for(model))
            features = extract_features(model.shape, model.bbox)
            scheme = derive_scheme(model, features)
            projections = {d.name: project_view(model.shape, d)
                           for d in (FRONT, TOP, LEFT)}
            placed = [replace(v, centerlines=centerlines_for(v.projection, features))
                      for v in layout_views(tuple(projections.values()))]
            dims = place_scheme(scheme, projections)
            require_allowed(gate_for(model, scheme, dims))
            drawing = build_drawing(placed, dimensions=dims, notes=scheme.notes,
                                    datum_marks=place_datum_marks(scheme, projections, dims))
        except DeliveryBlocked:
            continue
        hits = [c for c in find_collisions(drawing) if c.kind == "text/text"]
        if hits:
            dirty.append((sample.sample_id, len(hits)))

    assert dirty == [], f"{len(dirty)} 件真零件上有叠在一起的字：{dirty[:6]}"


def test_every_gated_part_also_renders_to_svg_and_pdf(tmp_path):
    """网页与打印是同一张图 —— 那两者都必须在真零件上端到端跑过。

    DXF 那条测试抓到过 15/65 件写不出图；渲染器如果只在夹具上测过，
    同样的缝会换到 SVG/PDF 上重开一次。
    """
    from dataclasses import replace

    from drawform.delivery import DeliveryBlocked, gate_for, require_allowed
    from drawform.dimensioning.place import place_scheme
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.centerlines import centerlines_for
    from drawform.geometry.features import extract_features
    from drawform.geometry.layout import layout_views
    from drawform.output.pdf import write_pdf
    from drawform.output.svg import write_svg

    crashed = []
    gated = 0
    for sample in load_corpus(CORPUS):
        try:
            model = import_step(sample.directory / "model.step")
            require_allowed(gate_for(model))
            features = extract_features(model.shape, model.bbox)
            scheme = derive_scheme(model, features)
            projections = {d.name: project_view(model.shape, d)
                           for d in (FRONT, TOP, LEFT)}
            views = [replace(v, centerlines=centerlines_for(v.projection, features))
                     for v in layout_views(tuple(projections.values()))]
            dims = place_scheme(scheme, projections)
            require_allowed(gate_for(model, scheme, dims))
            stem = tmp_path / sample.sample_id
            write_svg(views, stem.with_suffix(".svg"), dimensions=dims,
                      notes=scheme.notes)
            write_pdf(views, stem.with_suffix(".pdf"), dimensions=dims,
                      notes=scheme.notes)
            gated += 1
        except DeliveryBlocked:
            continue
        except Exception as error:
            crashed.append(f"{sample.sample_id}: {error!r}")

    assert crashed == [], f"{len(crashed)} 件真实零件出不了图：{crashed[:6]}"
    assert gated > 50, f"只有 {gated} 件跑到出图，语料没被真正压上去"


@needs_corpus
def test_part_0063_counterbore_depth_reaches_the_drawing():
    """方案发了 ↧2 不等于图上画了 ↧2 —— 这条量的是落位，不是发射。

    ⌀8 沉孔的底面只在部分角度上有材料（θ=210°/330° 有，θ=30–150° 全空），
    开口探针因此把两端都读成"开口"→ `mouth=None` → `_place_depths` 直接跳过。
    真实图纸确实标这个深度，所以这是**真漏标**，不是画不出来。

    修法是让沉孔的口/底由配对关系定（贴着引导孔那端是底），不去问探针。
    """
    from dataclasses import replace

    from drawform.dimensioning.place import place_scheme
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.centerlines import centerlines_for
    from drawform.geometry.features import extract_features
    from drawform.geometry.layout import layout_views

    model = import_step(CORPUS / "samples" / "part_0063" / "model.step")
    features = extract_features(model.shape, model.bbox)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    scheme = derive_scheme(model, features)

    dims = place_scheme(scheme, projections)
    depth_dims = [d for d in dims if d.kind == "depth"]

    dim = next((d for d in depth_dims if abs(d.value - 2.0) < 1e-6), None)
    assert dim is not None, f"沉孔 ↧2 没落到图上，图上只有 {[d.text for d in depth_dims]}"

    # 光"画出来了"不够：口与底反过来照样是一条 ↧2，而 ↧ 必须从入口量起。
    # 沉孔开在零件外表面上，所以入口端一定落在该视图投影轮廓的边界上，底端在内部。
    box = [pt for c in tuple(projections[dim.view].visible)
           + tuple(projections[dim.view].hidden) for pt in c.sample()]
    vertical = abs(dim.p1[1] - dim.p2[1]) > abs(dim.p1[0] - dim.p2[0])
    axis = 1 if vertical else 0
    extremes = [pt[axis] for pt in box]
    lo, hi = min(extremes), max(extremes)
    mouth, floor = dim.p1[axis], dim.p2[axis]
    at_surface = min(abs(mouth - lo), abs(mouth - hi))
    floor_inside = min(abs(floor - lo), abs(floor - hi))
    assert at_surface < floor_inside - 1e-6, \
        (f"↧2 从底端量起了：入口端落在 {mouth}（离轮廓边界 {at_surface:.2f}），"
         f"底端 {floor}（离边界 {floor_inside:.2f}）—— 口与底反了")


@needs_corpus
def test_a_bore_flanched_by_smaller_bores_on_both_ends_is_not_a_counterbore():
    """阶梯通孔 ⌀8 | ⌀70 | ⌀8 不是"⌀70 沉孔深 90"。

    沉孔的定义性特征是**只有一端**接着更小的引导孔，另一端是零件外表面。两端都夹着
    更小同轴孔的那段，是同一个通孔的中间加粗段，真实图纸只写各段直径、不写 ↧。

    这条与"沉孔的口由配对定"是同一个改动的两半：口算准之后，被误认的假沉孔不再被门
    拦下，会带着编造的 ↧90 直接出货（实测过）。所以判据必须一起补。
    """
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.features import extract_features

    model = import_step(CORPUS / "samples" / "part_0047" / "model.step")
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))

    assert [c.dof for c in scheme.plan.constraints].count("counterbore") == 0, \
        "阶梯通孔被当成了沉孔"
    depths = [c.value for c in scheme.plan.constraints if c.dof == "depth"]
    assert 90.0 not in depths, f"编造的深度 ↧90 仍在发出：{depths}"


@needs_corpus
def test_the_closed_chain_check_runs_on_real_output_and_stays_quiet():
    """`CLOSED_CHAIN` 不能再变回永远不会响的死代码。

    2026-10-07 实测：65 件真实零件里 `derive_scheme` 产出的 `Chain` 总数 = 0，
    这个检查只在手工构造 Chain 的单测里过过 —— 也就是"防止尺寸标重了"这句话
    从来没被真正执行。壁厚/台阶发出去以后会造出 30 处铺满外廓的链，
    每处按 GB/T 留一环不标。

    所以这条断言两头都管：链必须**存在**（0 条 = 哨兵又下岗了），
    而且必须**不响**（响了 = 留一环的规则没生效）。
    """
    from drawform.dimensioning.constraints import Chain, check_plan
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.features import extract_features

    chains = closed = 0
    for sample in load_corpus(CORPUS):
        model = import_step(sample.directory / "model.step")
        scheme = derive_scheme(model, extract_features(model.shape, model.bbox))
        chains += sum(1 for c in scheme.plan.constraints if isinstance(c, Chain))
        closed += sum(1 for v in check_plan(scheme.plan, scheme.unknowns)
                      if v.kind == "closed_chain")

    assert chains > 0, "方案里没有一条 Chain —— 封闭尺寸链检查又变成死代码了"
    assert closed == 0, f"留一环没生效：{closed} 处封闭尺寸链"
