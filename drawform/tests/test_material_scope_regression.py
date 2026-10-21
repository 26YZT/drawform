"""单体零件的边界来自唯一实体；额外非实体几何不能抬高制造尺寸。"""
import hashlib
import json

import pytest
from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeVertex
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopoDS import TopoDS_Compound
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

from drawform.delivery import DeliveryBlocked
from drawform.geometry.loader import StepImportError, import_step
from drawform.pipeline import render_part
from tests.conftest import _write_step


def _compound(*shapes):
    result=TopoDS_Compound();builder=BRep_Builder();builder.MakeCompound(result)
    for shape in shapes:builder.Add(result,shape)
    return result


def _box():return BRepPrimAPI_MakeBox(30,20,10).Shape()


def test_a_far_reference_point_does_not_become_the_single_parts_height(tmp_path):
    shape=_compound(_box(),BRepBuilderAPI_MakeVertex(gp_Pnt(15,10,500)).Vertex())
    step=_write_step(shape,tmp_path/'point.step');model=import_step(step)
    assert model.bbox==pytest.approx((30,20,10),abs=1e-6)
    assert model.shape.ShapeType()==TopAbs_SOLID
    assert model.excluded_geometry


def test_free_faces_and_curves_are_excluded_from_the_material_body(tmp_path):
    face=BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(0,0,100),gp_Dir(0,0,1)),-50,50,-50,50).Face()
    edge=BRepBuilderAPI_MakeEdge(gp_Pnt(-100,0,30),gp_Pnt(100,0,30)).Edge()
    step=_write_step(_compound(_box(),face,edge),tmp_path/'mixed.step');model=import_step(step)
    assert model.bbox==pytest.approx((30,20,10),abs=1e-6)
    assert model.solid_count==1 and model.shape.ShapeType()==TopAbs_SOLID
    assert model.excluded_geometry


def test_the_excluded_scope_is_disclosed_and_the_original_step_bytes_stay_intact(tmp_path):
    step=_write_step(_compound(_box(),BRepBuilderAPI_MakeVertex(gp_Pnt(15,10,500)).Vertex()),tmp_path/'mixed.step')
    original=hashlib.sha256(step.read_bytes()).hexdigest()
    part=render_part(step,tmp_path,with_questions=True);data=json.loads(dict(part.files)['json'].read_text())
    assert sorted(c['value'] for c in data['constraints'])==[10,20,30]
    assert data['part'].get('excluded_geometry')
    assert any('非实体' in warning for warning in part.verdict.warnings)
    assert any(a['id']=='geometry-scope' for a in data['assumptions'])
    assert hashlib.sha256(step.read_bytes()).hexdigest()==original


def test_a_wrapper_containing_only_one_solid_has_no_spurious_exclusion_warning(tmp_path):
    step=_write_step(_compound(_box()),tmp_path/'wrapped.step')
    part=render_part(step,tmp_path,with_questions=True);data=json.loads(dict(part.files)['json'].read_text())
    assert part.verdict.warnings==()
    assert not data['part'].get('excluded_geometry')


def test_multiple_solids_with_references_are_still_rejected_without_picking_one(tmp_path):
    second=BRepPrimAPI_MakeBox(gp_Pnt(50,0,0),30,20,10).Shape()
    step=_write_step(_compound(_box(),second,BRepBuilderAPI_MakeVertex(gp_Pnt(15,10,500)).Vertex()),tmp_path/'two.step')
    assert import_step(step).solid_count==2
    with pytest.raises(DeliveryBlocked) as error:render_part(step,tmp_path/'drawing')
    assert error.value.failed==('multiple_solids',)
    assert not (tmp_path/'drawing').exists()


def test_only_a_reference_face_is_not_promoted_to_a_solid_part(tmp_path):
    face=BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(0,0,100),gp_Dir(0,0,1)),-50,50,-50,50).Face()
    step=_write_step(face,tmp_path/'face.step')
    with pytest.raises(StepImportError):import_step(step)
