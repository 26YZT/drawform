"""把已确认参考写成可读注记与真实交点引注，不混入原生尺寸文本。"""
from collections import Counter
from dataclasses import replace
import math
from pathlib import Path
import tempfile

import ezdxf

from drawform.domain.curves import to_view, view_basis
from drawform.geometry.references import ReferenceInputError,GEOMETRY_TOL
from drawform.output.drawing import DrawingItem, SheetOverflow, Text, TEXT_HEIGHT
from drawform.output.sheet import make_frame

DXF_APP = 'DRAWFORM_REFERENCE_V1'


def definitions(scheme):
    facts=sorted(scheme.reference_facts,key=lambda fact:fact.bore_id)
    if not facts:return (),()
    if scheme.plan.datum is None or len(scheme.origin)!=3:
        raise SheetOverflow('参考方程缺少明确的三轴基准坐标，未猜测原点')
    origins=list(scheme.origin);letters=scheme.plan.datum.planes
    if len(letters)!=3:raise SheetOverflow('参考方程缺少三个基准方向')
    choices=dict(scheme.datum_choices);candidates={c.id:c for c in scheme.datum_candidates}
    for i,letter in enumerate(letters):
        face=candidates.get(choices.get(letter))
        if face is not None:
            if face.axis!='xyz'[i]:raise SheetOverflow('参考方程缺少一致的当前基准坐标体系')
            origins[i]=face.position
    plane_tags={key:f'R{i+1:02}' for i,key in enumerate(sorted({f.plane_id for f in facts}))}
    placeholders=[letter for letter in letters if choices.get(letter) not in candidates]
    status=f"占位基准{'/'.join(placeholders)}待确认" if placeholders else '基准按已选真实平面'
    notes=[f"参考方程原点为{'/'.join(letters)}坐标面交点，X/Y/Z正向沿STEP；{status}，单位mm。"]
    # 同一R方程可能被不同方向的孔共用；显示归零须满足每个孔未标方向的误差界限。
    plane_constants={}
    for plane_id in plane_tags:
        users=[fact for fact in facts if fact.plane_id==plane_id]
        first=users[0]
        if any(fact.normal!=first.normal or fact.constant!=first.constant for fact in users):
            raise SheetOverflow('同一参考平面的原精度方程不一致，未猜测截面定义')
        local=first.constant-math.fsum(n*o for n,o in zip(first.normal,origins))
        limit=min(abs(fact.normal[next(i for i in range(3) if 'xyz'[i] not in fact.axes)])
                  for fact in users)*GEOMETRY_TOL
        plane_constants[plane_id]=(local,0.0 if abs(local)<=limit else local)
    written=set();mapping=[]
    for i,fact in enumerate(facts):
        hole=f'H{i+1:02}';plane=plane_tags[fact.plane_id]
        local,display_constant=plane_constants[fact.plane_id]
        if fact.plane_id not in written:
            terms=[f'{n:.17g}*{axis}' for n,axis in zip(fact.normal,'XYZ') if n!=0]
            notes.append(f"{plane}：{'+'.join(terms).replace('+-','-')}={display_constant:.17g}")
            written.add(fact.plane_id)
        relation='面片交点' if fact.on_patch else '延拓平面参考，孔口关系未核实'
        notes.append(f'{hole}两向定位量至孔轴与{plane}交点（{relation}）。')
        mapping.append({'hole_tag':hole,'plane_tag':plane,'feature_id':fact.feature_id,
                        'bore_id':fact.bore_id,'plane_id':fact.plane_id,'point':fact.point,
                        'axes':fact.axes,'coordinate_origin':tuple(origins),
                        'plane_normal':fact.normal,'local_plane_constant':local,
                        'display_plane_constant':display_constant,
                        'relation':relation})
    return tuple(mapping),tuple(notes)


def annotate(drawing, views, dimensions, mapping, notes, factor):
    if not mapping:return drawing,()
    from drawform.output.annotation_layout import annotation_box, leader_route
    from drawform.output.collide import _boxes_overlap, _crosses_any, blocking_lines, item_box
    from drawform.output.paper_notes import wrap
    from drawform.output.titleblock import BLOCK_W
    frame=make_frame(drawing.sheet);x0,y0,x1,y1=frame.inner
    rows=Counter(row for note in notes for row in wrap(note,x1-BLOCK_W-x0-5))
    texts=[];occurrence=0
    for text in drawing.texts:
        if text.owner=='paper-note' and rows[text.content]:
            rows[text.content]-=1
            text=replace(text,owner=f'reference:note:{occurrence}')
            occurrence+=1
        texts.append(text)
    if any(rows.values()):raise SheetOverflow('图面没有完整保留参考截面定义，未省略注记继续出图')
    from drawform.output.titleblock import BLOCK_H
    from drawform.output.sheet import TITLE_CLEAR
    note_top=max((t.y+t.height+1 for t in drawing.texts if t.owner=='paper-note'),default=y0)
    if note_top > y0+BLOCK_H+1e-6:
        frame=replace(frame,inner=(x0,note_top+TITLE_CLEAR,x1,y1))
    items=list(drawing.items);lines={layer:list(runs) for layer,runs in drawing.lines}
    by_view={v.label or v.projection.direction.name:v for v in views};records=[]
    for record in mapping:
        related={d.view for d in dimensions if any(source and source[0]==record['feature_id']
                  for source in (d.source,*d.shared_sources)) and d.kind=='position'}
        if not related:raise SheetOverflow('已确认孔缺少可对应的两向定位视图，未猜测引注')
        for name in sorted(related):
            view=by_view[name];local=to_view(record['point'],*view_basis(view.projection.direction))
            tip=tuple(view.origin[i]+local[i]*factor for i in range(2))
            owner=f"reference:point:{record['bore_id']}:{name}"
            obstacles=[annotation_box(t) for t in texts]
            blocked=[(item,item_box(item)) for item in blocking_lines(items)]
            settled=None
            for radius in range(12,109,6):
                for dx,dy in ((radius,radius),(radius,-radius),(-radius,radius),(-radius,-radius),
                              (0,radius),(0,-radius),(radius,0),(-radius,0)):
                    text=Text(record['hole_tag'],tip[0]+dx,tip[1]+dy,'middle',owner=owner,height=TEXT_HEIGHT)
                    box=annotation_box(text)
                    if (not frame.contains(box) or any(_boxes_overlap(box,b,.8) for b in obstacles)
                            or _crosses_any(box,blocked,owner)):continue
                    route=leader_route(tip,box,obstacles,frame)
                    if route is not None:settled=(text,route);break
                if settled is not None:break
            if settled is None:raise SheetOverflow(f"参考定位点{record['hole_tag']}没有可读引注位置，未隐藏参考定义")
            text,route=settled;texts.append(text)
            mark=((tip[0]-.7,tip[1]),(tip[0]+.7,tip[1]))
            cross=((tip[0],tip[1]-.7),(tip[0],tip[1]+.7))
            for run in (route,mark,cross):
                if not all(frame.contains((*p,*p)) for p in run):raise SheetOverflow('参考引注越出图框')
                lines.setdefault('NOTE',[]).append(run);items.append(DrawingItem(owner,'NOTE',tuple(run)))
            records.append({'hole_tag':record['hole_tag'],'view':name,'paper_target':tip,
                            'label_position':(text.x,text.y),'leader':route})
    result=replace(drawing,texts=tuple(texts),items=tuple(items),
                   lines=tuple((layer,tuple(runs)) for layer,runs in lines.items()))
    return result,tuple(records)


def _attributes(entity, names):
    def value(item):
        if isinstance(item,float) and not math.isfinite(item):
            raise ValueError('nonfinite reference presentation')
        if not isinstance(item,(str,int,float,type(None))):
            return tuple(value(v) for v in item)
        return item
    return tuple((name,value(entity.dxf.get_default(name))) for name in names)


def _signature(doc):
    layer=doc.layers.get('NOTE')
    # 锁定参考实体直接依赖的显示/打印资源；不把无关CAD批注纳入签名。
    if layer.get_vp_overrides().has_overrides() or any(
            'note' in {name.casefold() for name in viewport.frozen_layers}
            for layout in doc.layouts for viewport in layout.query('VIEWPORT')):
        raise ReferenceInputError('参考注记存在视口隐藏或覆盖，请保留参考显示属性',
                                  'reference_definition_changed',409)
    result=[('LAYER',_attributes(layer,('color','plot','linetype','lineweight','true_color')),
             layer.dxf.flags & 3,layer.transparency)]
    linetype=doc.linetypes.get(layer.dxf.linetype)
    result.append(('LINETYPE',tuple((tag.code,tag.value) for tag in linetype.pattern_tags.tags)))
    tagged_count=0
    for entity in doc.modelspace():
        if not entity.has_xdata(DXF_APP):continue
        tagged_count+=1
        owner=tuple((tag.code,tag.value) for tag in entity.get_xdata(DXF_APP))
        if entity.dxf.layer!='NOTE':raise ReferenceInputError('参考引注层已变化，请重新提交STEP确认','reference_definition_changed',409)
        visible=_attributes(entity,('invisible','color','true_color','transparency','linetype',
                                    'ltscale','lineweight','extrusion'))
        def xyz(point):
            values=tuple(float(v) for v in point)
            if not all(math.isfinite(v) for v in values):raise ValueError('nonfinite reference geometry')
            return tuple(round(v,6) for v in values)
        if entity.dxftype()=='LINE':
            result.append(('LINE',owner,visible,tuple(sorted((xyz(entity.dxf.start),xyz(entity.dxf.end))))))
        elif entity.dxftype()=='TEXT':
            alignment,p1,p2=entity.get_placement()
            style=doc.styles.get(entity.dxf.style)
            font=(_attributes(style,('height','width','oblique','generation_flags','font','bigfont')),
                  style.dxf.flags & 5,style.get_extended_font_data())
            result.append(('TEXT',owner,visible,entity.dxf.text,str(alignment),xyz(p1),xyz(p2) if p2 is not None else None,
                           round(entity.dxf.height,6),round(entity.dxf.rotation,6),
                           _attributes(entity,('width','text_generation_flag','oblique','style')),font))
        else:raise ReferenceInputError('参考引注结构不可核验，请重新提交STEP确认','reference_definition_changed',409)
    if not tagged_count:raise ValueError('required reference annotations missing')
    return Counter(result)


def require_integrity(parent, data):
    try:
        with tempfile.TemporaryDirectory(prefix='drawform-reference-dxf-') as scratch:
            uploaded=Path(scratch)/'uploaded.dxf';uploaded.write_bytes(data)
            expected=_signature(ezdxf.readfile(parent));actual=_signature(ezdxf.readfile(uploaded))
        if not expected or actual!=expected:
            raise ValueError('required reference annotations differ')
    except ReferenceInputError:raise
    except Exception:
        raise ReferenceInputError('已确认参考的截面定义或交点引注缺失/变化；请保留参考注记，或重新提交STEP确认',
                                  'reference_definition_changed',409) from None
