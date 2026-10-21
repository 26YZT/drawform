import ezdxf
import pytest

from drawform.geometry.features import extract_features
from drawform.geometry.layout import PlacedView
from drawform.geometry.loader import import_step
from drawform.geometry.projection import FRONT, TOP, LEFT, project_view
from drawform.output.dxf import write_dxf


def _views(shape):
    return (
        PlacedView(projection=project_view(shape, FRONT), origin=(0.0, 0.0)),
        PlacedView(projection=project_view(shape, TOP), origin=(200.0, 0.0)),
    )


def test_dxf_writes_and_passes_audit(plate_step, tmp_path):
    model = import_step(plate_step)
    out = tmp_path / "plate.dxf"

    write_dxf(_views(model.shape), out)

    doc = ezdxf.readfile(str(out))
    assert len(doc.audit().errors) == 0


def test_dxf_uses_separate_layers_for_visible_and_hidden(plate_step, tmp_path):
    model = import_step(plate_step)
    out = tmp_path / "plate.dxf"

    write_dxf(_views(model.shape), out)

    doc = ezdxf.readfile(str(out))
    layers = {layer.dxf.name for layer in doc.layers}
    assert {"VISIBLE", "HIDDEN"} <= layers

    msp = doc.modelspace()
    assert any(e.dxf.layer == "HIDDEN" for e in msp), "没有边被放到 HIDDEN 图层"


def test_bore_is_emitted_as_native_circle_not_a_polyline(plate_step, tmp_path):
    """端到端证明：Ø20 通孔在俯视图里必须是真正的 CIRCLE 实体。

    客户要能在 AutoCAD 里直接改半径，折线团做不到。
    """
    model = import_step(plate_step)
    out = tmp_path / "plate.dxf"

    write_dxf(_views(model.shape), out)

    doc = ezdxf.readfile(str(out))
    circles = [e for e in doc.modelspace() if e.dxftype() == "CIRCLE"]

    assert circles, "俯视图里的 Ø20 孔应输出为 CIRCLE 实体"
    assert any(abs(c.dxf.radius - 10.0) < 1e-6 for c in circles)


def test_no_polyline_leaks_for_simple_prismatic_part(plate_step, tmp_path):
    """平板类零件经 refit 后不应残留任何折线实体。"""
    model = import_step(plate_step)
    out = tmp_path / "plate.dxf"

    write_dxf(_views(model.shape), out)

    doc = ezdxf.readfile(str(out))
    polylines = [e for e in doc.modelspace()
                 if e.dxftype() in ("POLYLINE", "LWPOLYLINE", "SPLINE")]

    assert polylines == []


def test_arc_and_line_entities_present(plate_step, tmp_path):
    model = import_step(plate_step)
    out = tmp_path / "plate.dxf"

    write_dxf(_views(model.shape), out)

    doc = ezdxf.readfile(str(out))
    kinds = {e.dxftype() for e in doc.modelspace()}

    assert "LINE" in kinds
    assert kinds & {"CIRCLE", "ARC"}


def test_centerlines_land_on_their_own_layer(plate_step, tmp_path):
    """中心线必须独立成层并使用点划线线型，否则客户无法单独开关或改线型。"""
    import ezdxf

    from drawform.geometry.centerlines import centerlines_for
    from drawform.geometry.features import extract_features

    model = import_step(plate_step)
    features = extract_features(model.shape, model.bbox)
    views = tuple(
        PlacedView(
            projection=project_view(model.shape, v),
            origin=(i * 220.0, 0.0),
            centerlines=centerlines_for(project_view(model.shape, v), features),
        )
        for i, v in enumerate((FRONT, TOP))
    )
    out = tmp_path / "with_center.dxf"

    write_dxf(views, out)

    doc = ezdxf.readfile(str(out))
    center = [e for e in doc.modelspace() if e.dxf.layer == "CENTER"]
    assert center, "中心线没有输出到 CENTER 图层"
    layer = doc.layers.get("CENTER")
    assert "CHAIN" in layer.dxf.linetype.upper() or "CENTER" in layer.dxf.linetype.upper(), \
        layer.dxf.linetype


def test_layers_carry_gbt_line_weights(plate_step, tmp_path):
    """GB/T 4457.4：可见轮廓用粗实线，隐藏线/中心线用细线，粗细比 2:1。

    实测未设线宽时三层打印出来一样粗，读者分不清哪些是可见边，属于不合格图纸。
    """
    model = import_step(plate_step)
    out = tmp_path / "weights.dxf"

    write_dxf(_views(model.shape), out)

    doc = ezdxf.readfile(str(out))
    weights = {n: doc.layers.get(n).dxf.lineweight for n in ("VISIBLE", "HIDDEN", "CENTER")}

    assert weights["VISIBLE"] == 50, f"可见线应为 0.50 mm 粗线，实际 {weights}"
    assert weights["HIDDEN"] == 25, f"隐藏线应为 0.25 mm 细线，实际 {weights}"
    assert weights["CENTER"] == 25, f"中心线应为 0.25 mm 细线，实际 {weights}"


def test_section_hatch_is_emitted_as_thin_continuous_lines(bearing_seat_step, tmp_path):
    """剖面线是细实线：与粗实线轮廓同层同宽就分不清哪条是真实轮廓。"""
    from drawform.geometry.section import section_view

    model = import_step(bearing_seat_step)
    section = section_view(model.shape, FRONT, axis="y", at=25.0)
    views = (PlacedView(projection=section.projection, origin=(0.0, 0.0),
                        centerlines=(), hatch=section.hatch),)
    out = tmp_path / "section.dxf"

    write_dxf(views, out)

    doc = ezdxf.readfile(str(out))
    hatch = [e for e in doc.modelspace() if e.dxf.layer == "HATCH"]

    assert hatch, "剖面线没有输出到 HATCH 图层"
    assert all(e.dxftype() == "LINE" for e in hatch)
    assert doc.layers.get("HATCH").dxf.lineweight == 25
    assert doc.layers.get("HATCH").dxf.linetype.upper() == "CONTINUOUS"


def test_layers_carry_gbt_linetypes(plate_step, tmp_path):
    """可见线必须连续，隐藏线虚线，中心线点划线 —— 线型写错等于改变含义。"""
    model = import_step(plate_step)
    out = tmp_path / "linetypes.dxf"

    write_dxf(_views(model.shape), out)

    doc = ezdxf.readfile(str(out))
    kinds = {n: doc.layers.get(n).dxf.linetype.upper() for n in ("VISIBLE", "HIDDEN", "CENTER")}

    assert kinds["VISIBLE"] == "CONTINUOUS"
    assert "DASH" in kinds["HIDDEN"], kinds
    assert "CHAIN" in kinds["CENTER"] or "CENTER" in kinds["CENTER"], kinds


def _pattern(doc, name):
    return [round(tag.value, 4) for tag in doc.linetypes.get(name).pattern_tags.tags
            if tag.code == 49]


def test_linetype_geometry_follows_gbt(plate_step, tmp_path):
    """GB/T 4457.4：虚线线段 8–12 mm、间隔 1 mm；点划线线段 18–24 mm、间隔 3–4.5 mm、点 ≤0.75 mm。

    实测沿用 AutoCAD 内置线型时，虚线周期只有 1.524 mm（按英寸定义），
    在 1:1 毫米图上打出来几乎连成实线；而单靠 LTSCALE 无法同时满足两种线型
    （放大到虚线合规时，点划线的点会变成 3 mm 长的小段，不再是点）。
    """
    model = import_step(plate_step)
    out = tmp_path / "patterns.dxf"

    write_dxf(_views(model.shape), out)

    doc = ezdxf.readfile(str(out))
    dashed = _pattern(doc, doc.layers.get("HIDDEN").dxf.linetype)
    chain = _pattern(doc, doc.layers.get("CENTER").dxf.linetype)

    assert [seg for seg in dashed if seg > 0], "虚线没有实线段"
    assert all(8.0 <= seg <= 12.0 for seg in dashed if seg > 0), f"虚线线段长度 {dashed}"
    assert all(1.0 <= -seg <= 2.0 for seg in dashed if seg < 0), f"虚线间隔 {dashed}"

    assert all(18.0 <= seg <= 24.0 for seg in chain if seg > 0.75), f"点划线线段 {chain}"
    assert any(0 < seg <= 0.75 for seg in chain), f"点划线缺少点：{chain}"
    assert all(3.0 <= -seg <= 4.5 for seg in chain if seg < 0), f"点划线间隔 {chain}"


def test_pcd_dimension_writes_as_a_diameter_dim_not_a_crash(bolt_circle_step, tmp_path):
    """分布圆尺寸此前让 DXF 写出直接崩：`base=None` 落进了线性尺寸分支。

    实测 65 件真实语料里有 15 件因此一张图都出不来。测试从没覆盖过它：
    落位测试到 `PlacedDimension` 为止就断了，而出图测试用的夹具都没有螺栓圆。
    """
    from drawform.dimensioning.place import place_scheme
    from drawform.dimensioning.scheme import derive_scheme

    model = import_step(bolt_circle_step)
    features = extract_features(model.shape, model.bbox)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    dims = place_scheme(derive_scheme(model, features), projections)
    pcd = [d for d in dims if d.kind == "pcd"]
    assert pcd, "夹具没产出分布圆尺寸，这条测试是空转的"

    out = tmp_path / "bolt.dxf"
    write_dxf(_views(model.shape), out, dimensions=dims)

    doc = ezdxf.readfile(str(out))
    assert len(doc.audit().errors) == 0
    texts = [entity.dxf.text for entity in doc.modelspace().query("DIMENSION")]
    assert any("PCD" in text for text in texts), f"分布圆没落到图上：{texts}"
