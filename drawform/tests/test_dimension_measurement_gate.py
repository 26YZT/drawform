"""发了且画了仍不够：方案、真实测量和共享来源必须一致，失败前不写图。"""
from dataclasses import replace
import json,math
import pytest
import ezdxf
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from drawform.geometry.loader import LoadedModel
from drawform.dimensioning.constraints import Plan,Sized,Datum
from drawform.dimensioning.scheme import Scheme
from drawform.dimensioning.place import PlacedDimension
from drawform.delivery import gate_for,DeliveryBlocked
from drawform import pipeline


@pytest.fixture(scope='module')
def model():
    return LoadedModel(BRepPrimAPI_MakeBox(10,10,10).Shape(),(10,10,10),(0,0,0))


def _scheme(*constraints):
    return Scheme(Plan(constraints,Datum('A','B','C')),tuple((c.feature,c.dof) for c in constraints))


def _linear():
    return PlacedDimension('linear','front',(0,0),(10,0),(5,-8),'10',10,source=('a','length'))


@pytest.mark.parametrize('fault',['wrong_value','wrong_geometry','nan_value','infinite_point','zero_axis','missing_base','shared_lengths','shared_value','shared_geometry','duplicate','shared_quantity'])
def test_inconsistent_measurements_are_blocked_even_with_all_declared_sources_present(model,fault):
    dim=_linear();scheme=_scheme(Sized('a','length',10));dims=None
    if fault=='wrong_value':dim=replace(dim,value=11)
    elif fault=='wrong_geometry':dim=replace(dim,p2=(11,0))
    elif fault=='nan_value':dim=replace(dim,value=math.nan)
    elif fault=='infinite_point':dim=replace(dim,p1=(math.inf,0))
    elif fault=='zero_axis':dim=replace(dim,measurement_axis=(0,0))
    elif fault=='missing_base':dim=replace(dim,base=None)
    elif fault=='duplicate':dims=(dim,dim)
    else:
        scheme=_scheme(Sized('a','length',10),Sized('b','length',10,quantity=2 if fault=='shared_quantity' else 1))
        dim=replace(dim,shared_sources=(('b','length'),),shared_values=(11 if fault=='shared_value' else 10,),shared_witnesses=(((0,0),(11 if fault=='shared_geometry' else 10,0)),))
        if fault=='shared_lengths':dim=replace(dim,shared_values=())
    verdict=gate_for(model,scheme,dims or (dim,))
    assert verdict.decision=='block'
    assert 'inconsistent_dimensions' in verdict.failed


@pytest.mark.parametrize('kind',['linear','axis_projection','diameter','pcd','zero_position'])
def test_valid_measurements_of_each_existing_geometry_kind_are_allowed(model,kind):
    dim=_linear();dof='length';value=10
    if kind=='axis_projection':dim=replace(dim,p1=(0,2),p2=(10,5),measurement_axis=(1,0))
    elif kind=='diameter':dof='diameter';dim=replace(dim,kind='diameter',p2=(5,0),base=None)
    elif kind=='pcd':dof='pcd';dim=replace(dim,kind='pcd',base=None)
    elif kind=='zero_position':dof='x';value=0;dim=replace(dim,kind='position',p1=(0,0),p2=(0,0),value=0,text='0',axis='x')
    dim=replace(dim,source=('a',dof));scheme=_scheme(Sized('a',dof,value))
    if kind=='diameter':
        # 只有数值、没有圆柱来源时不能通过；使用真实圆柱继续原通过断言。
        assert 'inconsistent_dimensions' in gate_for(model,scheme,(dim,)).failed
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2,gp_Pnt,gp_Dir
        from drawform.geometry.features import Shaft,extract_features
        shape=BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0,0,0),gp_Dir(0,1,0)),5,10).Shape()
        model=LoadedModel(shape,(10,10,10),(-5,0,-5))
        shaft=next(f for f in extract_features(shape,model.bbox) if isinstance(f,Shaft))
        scheme=replace(scheme,features=(replace(shaft,feature_id='a'),))
    assert gate_for(model,scheme,(dim,)).allowed


def test_an_actual_pipeline_numeric_fault_is_refused_before_any_artifact_is_written(plate_step,tmp_path,monkeypatch):
    positive=tmp_path/'positive';positive.mkdir();part=pipeline.render_part(plate_step,positive,with_questions=True)
    files=dict(part.files);data=json.loads(files['json'].read_text());doc=ezdxf.readfile(files['dxf'])
    assert all(abs(d['value']-e.get_measurement())<1e-6 for d,e in zip(data['dimensions'],doc.modelspace().query('DIMENSION')))
    original=pipeline.place_scheme
    def wrong(*args,**kwargs):
        dims=original(*args,**kwargs)
        return (replace(dims[0],value=dims[0].value+1),*dims[1:])
    monkeypatch.setattr(pipeline,'place_scheme',wrong)
    blocked=tmp_path/'blocked';blocked.mkdir()
    with pytest.raises(DeliveryBlocked) as error:pipeline.render_part(plate_step,blocked,with_questions=True)
    assert 'inconsistent_dimensions' in error.value.failed
    assert not list(blocked.iterdir())
