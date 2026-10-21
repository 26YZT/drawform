"""Canonical two-stage solid shaft data and answer validation, without CAD."""
import copy
import pytest
from drawform.workbench.simple_spec import validate_spec,spec_from_analysis
from drawform.workbench.plate_spec import SpecError
from drawform.workbench.revisions import apply_changes

SPEC={'schema_version':2,'kind':'stepped_shaft','parameters':{'diameter_1_mm':40,'length_1_mm':30,'diameter_2_mm':20,'length_2_mm':40},'holes':[]}


def test_canonical_two_stage_shaft_and_reversed_diameters_are_admitted():
    assert validate_spec(SPEC)==SPEC
    reverse=copy.deepcopy(SPEC);reverse['parameters'].update(diameter_1_mm=20,diameter_2_mm=40)
    assert validate_spec(reverse)==reverse


@pytest.mark.parametrize('change',[
    {'diameter_1_mm':20}, {'diameter_1_mm':0}, {'length_2_mm':-1},
    {'length_1_mm':True}, {'diameter_2_mm':float('nan')},
    {'length_2_mm':float('inf')}, {'length_2_mm':'40'}, {'length_2_mm':1000},
])
def test_degenerate_or_invalid_shaft_dimensions_are_rejected(change):
    spec=copy.deepcopy(SPEC);spec['parameters'].update(change)
    with pytest.raises(SpecError):validate_spec(spec)


def test_noncanonical_fields_holes_and_extra_geometry_are_rejected():
    for mutate in ('old_fields','hole','extra'):
        spec=copy.deepcopy(SPEC)
        if mutate=='old_fields':spec['parameters']={k.replace('_1','1').replace('_2','2'):v for k,v in spec['parameters'].items()}
        elif mutate=='hole':spec['holes']=[{'x_mm':0,'y_mm':0,'diameter_mm':5}]
        else:spec['parameters']['fillet_mm']=2
        with pytest.raises(SpecError):validate_spec(spec)


def test_source_must_confirm_solid_before_analysis_becomes_spec():
    a={'kind':'stepped_shaft','units':'mm','parameters':SPEC['parameters'],'holes':[]}
    with pytest.raises(SpecError,match='实心'):spec_from_analysis(a)
    a['shaft_solid_confirmed']=True
    assert spec_from_analysis(a)==SPEC


def test_existing_revision_operation_records_correct_stage_and_label():
    spec,diff=apply_changes(SPEC,[{'op':'set_parameter','parameter':'length_2_mm','value_mm':45}])
    assert spec['parameters']['length_2_mm']==45 and SPEC['parameters']['length_2_mm']==40
    assert diff==[{'path':'parameters.length_2_mm','label':'第2段长度','before':40,'after':45.,'unit':'mm'}]
