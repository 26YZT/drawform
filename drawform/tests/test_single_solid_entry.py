"""多实体必须在投影/出图前拒绝，不能靠模拟审核放行。"""

from pathlib import Path

import pytest
from OCP.BRep import BRep_Builder
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TopoDS import TopoDS_Compound

from drawform.delivery import DeliveryBlocked
from drawform.pipeline import render_part


@pytest.mark.parametrize('count',[2,3])
def test_compound_is_rejected_before_projecting_without_output(tmp_path,monkeypatch,count):
    shape=TopoDS_Compound();builder=BRep_Builder();builder.MakeCompound(shape)
    for i in range(count):builder.Add(shape,BRepPrimAPI_MakeBox(gp_Pnt(i*30,0,0),20,10,8).Shape())
    writer=STEPControl_Writer();writer.Transfer(shape,STEPControl_AsIs)
    step=tmp_path/'compound.step';writer.Write(str(step))
    import drawform.pipeline as pipeline
    def unexpected(*args,**kwargs):raise AssertionError('多实体不应进入投影')
    monkeypatch.setattr(pipeline,'project_view',unexpected)
    with pytest.raises(DeliveryBlocked) as caught:render_part(step,tmp_path/'out')
    assert caught.value.failed==('multiple_solids',)
    assert str(count) in str(caught.value) and '单体' in str(caught.value)
    assert not (tmp_path/'out').exists()


def test_single_solid_with_many_holes_is_not_mistaken_for_many_parts(plate_step,tmp_path):
    (tmp_path/'out').mkdir()
    part=render_part(plate_step,tmp_path/'out')
    assert part.verdict.allowed
