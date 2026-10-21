"""STEP → 一张零件图的三个出口（DXF / SVG / PDF）与一份交付判定。

这是全项目唯一一条"从模型到图纸"的调用序列。命令行示例、服务端与测试都必须走这里：
序列一旦被抄第二遍，两份就会在不同的一天开始给出不同的图，而这种分叉在车间里
表现为"电脑上看到的和纸上印的不一样"。

门的三次检查各自挡不同的事，顺序不能重排：
* 装载之后立刻查实体有效性 —— 无效实体让投影层跑出一个裸异常，拿到裸异常等于没有门；
* 落位之前查方案合法性 —— 方案不合法时落位可能崩，也可能画出一张自相矛盾的图；
* 写文件之前查"发了没画" —— 这一条必须排在所有落盘之前，否则拦下了却留下半个文件。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
import tempfile
import pathlib

from drawform.delivery import BLOCK, Verdict, gate_for, require_allowed
from drawform.dimensioning.place import place_datum_marks, place_scheme
from drawform.dimensioning.scheme import derive_scheme
from drawform.dimensioning.constraints import Sized
from drawform.geometry.centerlines import centerlines_for
from drawform.geometry.features import extract_features
from drawform.geometry.bore_topology import BoreTopologyError
from drawform.geometry.layout import DEFAULT_GAP, PlacedView, layout_views
from drawform.geometry.loader import import_step
from drawform.geometry.references import snapshot_for, resolve
from drawform.geometry.projection import FRONT, LEFT, TOP, project_view
from drawform.geometry.section import HATCH_SPACING, section_view
from drawform.output.drawing import SectionMark, SheetOverflow, build_drawing
from drawform.output import dwg
from drawform.output.titleblock import TitleMeta
from drawform.output.paper_notes import NotesOverflow
from drawform.output.paper import choose_scale, prepare_layout, scale_label
from drawform.output.dxf import write_dxf
from drawform.output.json_out import write_json
from drawform.output.pdf import write_pdf
from drawform.output.sheet import make_frame
from drawform.output.svg import write_svg
from drawform.output.references import definitions as reference_definitions, annotate as annotate_references
from drawform.service.questions import Question, confirmation_warnings, questions_for
from drawform.service.answers import (Answer, AnswerError, apply_answers, bind_scheme,
                                      dimension_overrides, validate_answers,
                                      write_dimension_overrides)

#: 输出顺序即交付清单顺序。三个出口缺一门就是交付物残缺。
FORMATS = ("dxf", "svg", "pdf", "json")


@dataclass(frozen=True)
class Part:
    """一次出图的全部产出。"""

    verdict: Verdict
    files: tuple[tuple[str, pathlib.Path], ...]
    dimensions: tuple = ()
    notes: tuple[str, ...] = ()
    questions: tuple[Question, ...] = ()
    answers: tuple[Answer, ...] = ()
    answer_notes: tuple[str, ...] = ()


def _local_box(projection):
    points = [p for c in tuple(projection.visible) + tuple(projection.hidden)
              for p in c.sample()]
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _same_source(path, sha256):
    try:return hashlib.sha256(path.read_bytes()).hexdigest()==sha256
    except OSError:return False


def render_part(step_path, out_dir, stem: str | None = None, *,
                sheet: str = "A3", mode: str = "first_angle",
                section_at: float | None = None,
                section_axis: str = "y",
                title_meta=None, with_questions: bool = False,
                questions_in=(), questions_ref=(), reference_in=None) -> Part:
    """把 STEP 画成零件图。门拦下来时抛 `DeliveryBlocked`，且**不留下任何文件**。

    `section_at` 是剖平面在模型坐标里的位置，默认不出剖视 —— 自动选剖切位置
    本身是一个还没做的能力，拿零件尺寸凑一个数会画出看不懂的剖视。
    """
    step_path = pathlib.Path(step_path)
    out_dir = pathlib.Path(out_dir)
    stem = step_path.stem if stem is None else stem

    source_bytes = step_path.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    # OCCT只读本次不可变字节副本，原文件后续变化不会换掉已装载几何的来源。
    with tempfile.TemporaryDirectory(prefix='drawform-source-') as scratch:
        immutable_source = pathlib.Path(scratch) / step_path.name
        immutable_source.write_bytes(source_bytes)
        model = import_step(immutable_source)
    require_allowed(gate_for(model))

    try:
        features = extract_features(model.shape, model.bbox)
    except BoreTopologyError as error:
        require_allowed(Verdict(BLOCK, ("counterbore_semantics",), (str(error),)))
    projections = {direction.name: project_view(model.shape, direction)
                   for direction in (FRONT, TOP, LEFT)}
    if not _same_source(step_path,source_sha):
        require_allowed(Verdict(BLOCK,('stale_reference',),('STEP在装载后发生变化，未生成图件，请重新提交',)))
    reference_snapshot = snapshot_for(model, features, source_sha, tuple(projections.values()))
    reference_facts = resolve(reference_snapshot, reference_in)
    placed = [PlacedView(projection=view.projection, origin=view.origin,
                         centerlines=centerlines_for(view.projection, features))
              for view in layout_views(tuple(projections.values()), mode=mode)]

    marks: tuple[SectionMark, ...] = ()
    if section_at is not None:
        section = section_view(model.shape, FRONT, axis=section_axis, at=section_at)
        box = _local_box(section.projection)
        right_edge = max(_local_box(view.projection)[2] + view.origin[0]
                         for view in placed)
        base_y = next(view.origin[1] for view in placed
                      if view.projection.direction.name == "front")
        placed.append(PlacedView(projection=section.projection,
                                 origin=(right_edge + DEFAULT_GAP - box[0],
                                         base_y - box[1]),
                                 centerlines=centerlines_for(section.projection,
                                                             features),
                                 hatch=section.hatch, label="A-A"))
        marks = (SectionMark("top", section_at, "A", (0.0, 1.0)),)

    scheme = derive_scheme(model, features, reference_facts=reference_facts)
    original_scheme = scheme
    answers, answer_notes = (), ()
    if questions_in:
        scheme = bind_scheme(scheme, model)
        reference = tuple(questions_ref) or questions_for(model, features, scheme)
        answers, duplicate_notes = validate_answers(reference, questions_in)
        updated, answer_notes = apply_answers(scheme, reference, answers)
        if updated is scheme:
            raise AnswerError("；".join(answer_notes), "stale_base", 409)
        scheme = updated
        answer_notes = duplicate_notes + answer_notes
    require_allowed(gate_for(model, scheme))
    dimensions = place_scheme(scheme, projections)
    from drawform.domain.curves import to_view, view_basis
    targets = {fact.feature_id:fact.point for fact in reference_facts}
    expected_sources = {(fact.feature_id,axis) for fact in reference_facts for axis in fact.axes}
    plan_sources = {(c.feature,c.dof) for c in scheme.plan.constraints if hasattr(c,'dof')}
    drawn_sources = {source for dim in dimensions for source in (dim.source,*dim.shared_sources)}
    if expected_sources-plan_sources or expected_sources-drawn_sources:
        require_allowed(Verdict(BLOCK,('incomplete_reference_position',),
            ('已确认斜孔的两向定位没有全部进入方案与图面，未删减约束或继续出图',)))
    reference_errors = []
    for dim in dimensions:
        pairs = [(dim.source,dim.p2), *[(source,pair[1]) for source,pair
                                      in zip(dim.shared_sources,dim.shared_witnesses)]]
        for source, point in pairs:
            if source and source[0] in targets and source[1] in ('x','y','z'):
                expected = to_view(targets[source[0]],*view_basis(projections[dim.view].direction))
                if math.dist(point,expected)>1e-6:
                    reference_errors.append(f'{source} 的落位目标与已确认参考截面交点不符')
    if reference_errors:
        require_allowed(Verdict(BLOCK,('inconsistent_reference_geometry',),tuple(reference_errors)))
    overrides = dimension_overrides(scheme, dimensions)

    # 代号要在尺寸之后算：它得知道该侧的尺寸带排到哪，才让得开。
    datum_marks = place_datum_marks(scheme, projections, dimensions)
    layout_dimensions, layout_marks = None, datum_marks
    changed_owners = set()
    if answers:
        layout_dimensions = place_scheme(original_scheme, projections)
        layout_marks = place_datum_marks(original_scheme, projections, layout_dimensions)
    if scheme.datum_choices:
        layout_dimensions = place_scheme(original_scheme, projections)
        layout_marks = place_datum_marks(original_scheme, projections, layout_dimensions)
        letters = dict(scheme.datum_choices)
        axes = {"xyz"[index] for index, letter in enumerate(scheme.plan.datum.planes)
                if letter in letters}
        changed_owners = {f"datum:{letter}" for letter in letters}
        changed_owners.update(f"dim:{dim.source}" for dim in dimensions
                              if dim.kind == "position" and dim.axis in axes)
        changed_owners.update(f"dim:{dim.source}" for dim in layout_dimensions
                              if dim.kind == "position" and dim.axis in axes)
    frame = make_frame(sheet)
    # 最后一道，且必须排在任何写文件之前。
    verdict = gate_for(model, scheme, dimensions, projections=projections)
    require_allowed(verdict)
    verdict = replace(verdict, warnings=confirmation_warnings(model, scheme, dimensions, answers))
    questions = (questions_for(model, features, scheme, dimensions=dimensions)
                 if with_questions else ())
    answered_ids = {answer.question_id for answer in answers}
    questions = tuple(q for q in questions if q.id not in answered_ids)

    meta = title_meta if title_meta is not None else TitleMeta.from_step_name(
        stem, tolerance_note=next((n for n in scheme.notes if n.startswith("一般公差")), ""))
    explicit = [f"{c.value:g}{c.tolerance}" for c in scheme.plan.constraints
                if isinstance(c, Sized) and c.tolerance is not None and c.dof != "angle"]
    if explicit:
        meta = replace(meta, tolerance_note=meta.tolerance_note + "；专门公差：" + "、".join(explicit))
    # 一般公差只许出现在一处。标题栏那一格的值由 meta.tolerance_note 决定；
    # 人工填专门公差时它和 scheme 自动生成的那句**不是同一个字符串**，
    # drawing.py 按字面相等去重就漏掉了，图面上会同时留下两句互相矛盾的要求。
    # 所以这里按「谁拥有这一格」来去重。没写上的那种情况保持原样：角落空着。
    reference_mapping, reference_notes = reference_definitions(scheme)
    if reference_notes:
        scheme = replace(scheme,notes=scheme.notes+reference_notes)
    notes = scheme.notes
    if meta.tolerance_note:
        notes = tuple(n for n in scheme.notes if not n.startswith("一般公差"))
    try:
        factor = choose_scale(placed, sheet,
                              dimensions if layout_dimensions is None else layout_dimensions,
                              layout_marks, mode, notes=notes, tolerance_note=meta.tolerance_note)
        meta = replace(meta, scale=scale_label(factor))
        if section_at is not None and factor != 1:
            section = section_view(model.shape, FRONT, axis=section_axis, at=section_at,
                                   hatch_spacing=HATCH_SPACING/factor)
            placed = [replace(v, hatch=section.hatch) if v.label == 'A-A' else v for v in placed]
        paper = prepare_layout(placed, dimensions, datum_marks, marks, sheet, mode, factor,
                               layout_dimensions=layout_dimensions, layout_marks=layout_marks,
                               notes=notes, tolerance_note=meta.tolerance_note)
        layout_reference = (build_drawing(paper.views, sheet, paper.reference_dimensions, notes, paper.sections,
                                          paper.reference_datums, meta, text_overrides=overrides,
                                          placed_on_sheet=paper.content_frame is not None)
                            if layout_dimensions is not None else None)
        drawing = build_drawing(paper.views, sheet, paper.dimensions, notes, paper.sections, paper.datums, meta,
                                text_overrides=overrides, layout_dimensions=paper.reference_dimensions,
                                layout_reference=layout_reference, changed_owners=changed_owners,
                                placed_on_sheet=paper.content_frame is not None)
        drawing, reference_callouts = annotate_references(drawing,paper.views,paper.dimensions,
                                                          reference_mapping,reference_notes,factor)
        placed, dimensions = paper.model_views, paper.model_dimensions
        if not _same_source(step_path,source_sha):
            require_allowed(Verdict(BLOCK,('stale_reference',),('STEP在出图前发生变化，未写交付图，请重新提交',)))
        files = (("dxf", write_dxf(placed, out_dir / f"{stem}.dxf",
                                   dimensions=dimensions, notes=notes,
                                   section_marks=marks, datum_marks=datum_marks,
                                   title_meta=meta, frame=frame, drawing=drawing, paper_scale=factor)),
                 ("svg", write_svg(placed, out_dir / f"{stem}.svg", sheet=sheet,
                                   dimensions=dimensions, notes=notes,
                                   section_marks=marks, datum_marks=datum_marks,
                                   title_meta=meta, text_overrides=overrides,
                                   layout_dimensions=layout_dimensions,
                                   layout_reference=layout_reference, changed_owners=changed_owners, drawing=drawing)),
                 ("pdf", write_pdf(placed, out_dir / f"{stem}.pdf", sheet=sheet,
                                   dimensions=dimensions, notes=notes,
                                   section_marks=marks, datum_marks=datum_marks,
                                   title_meta=meta, text_overrides=overrides,
                                   layout_dimensions=layout_dimensions,
                                   layout_reference=layout_reference, changed_owners=changed_owners, drawing=drawing)),
                 ("json", write_json(out_dir / f"{stem}.json", model=model, sheet=sheet,
                                     scheme=scheme, dimensions=tuple(replace(d, text=overrides.get(
                                         f"dim:{d.source}", d.text)) for d in dimensions),
                                     datum_marks=datum_marks, views=placed,
                                     verdict=verdict,
                                     questions=questions if with_questions else None,
                                     answers=answers, answer_notes=answer_notes,
                                     reference_facts=reference_facts,
                                     reference_presentations={'mapping':reference_mapping,'notes':reference_notes,
                                                              'callouts':reference_callouts} if reference_facts else None,
                                     drawing_scale=factor, scale_label=scale_label(factor))))
    except (SheetOverflow, NotesOverflow) as overflow:
        # 装不下是**这张纸给出的判定**，不是程序故障：翻成拒绝，理由里带上换哪张幅面。
        require_allowed(Verdict(decision=BLOCK, failed=("sheet_overflow",),
                                reasons=(str(overflow),)))
    write_dimension_overrides(dict(files)["dxf"], dimensions, overrides)
    if dwg.available():
        # 配了转换器才多这一个出口。没配就交四件套，**不假装支持 DWG** ——
        # 本项目的 DWG 能力至今没在真实 AutoCAD 上验证过，见 output/dwg.py。
        tool = dwg.converter()
        files = files + (("dwg", dwg.convert(dict(files)["dxf"],
                                              out_dir / f"{stem}.dwg")),)
    return Part(verdict=verdict, files=files,
                dimensions=tuple(replace(d, text=overrides.get(f"dim:{d.source}", d.text))
                                 for d in dimensions), notes=scheme.notes, questions=questions,
                answers=answers, answer_notes=answer_notes)
