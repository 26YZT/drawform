"""同一真实出图在新进程哈希种子变化时，DXF类定义仍须稳定。"""
import hashlib,os,subprocess,sys
from pathlib import Path
import ezdxf
import pytest
from drawform.eval.stability import canonical_bytes


@pytest.mark.parametrize('sample',['plate','rim'])
def test_generated_dxf_is_identical_across_python_hash_seeds(plate_step,tmp_path,sample):
    root=Path(__file__).resolve().parents[1]
    step=plate_step if sample=='plate' else root.parent/'corpus/cadsy/samples/part_0027/model.step'
    if not step.is_file():pytest.skip('未下载用于跨进程类表回归的机器生成语料')
    script='''from pathlib import Path
import sys
from drawform.pipeline import render_part
render_part(Path(sys.argv[1]),Path(sys.argv[2]),stem='stability',with_questions=True)
'''
    hashes=[];classes=[]
    for seed in (1,2,3,17,41):
        folder=tmp_path/str(seed);folder.mkdir();path=folder/'stability.dxf';env={**os.environ,'PYTHONPATH':str(root/'src'),'PYTHONHASHSEED':str(seed)}
        run=subprocess.run([sys.executable,'-c',script,str(step),str(folder)],env=env,capture_output=True,text=True,timeout=30)
        assert run.returncode==0,run.stderr
        hashes.append(hashlib.sha256(canonical_bytes('dxf',path.read_bytes())).hexdigest())
        doc=ezdxf.readfile(path);assert not doc.audit().errors;assert doc.header['$INSUNITS']==4
        assert len(list(doc.modelspace().query('DIMENSION')))>0
        classes.append([(c.dxf.name,c.dxf.cpp_class_name) for c in doc.classes])
    assert len(set(hashes))==1, '仅创建元数据可变，DXF不能依赖新进程哈希种子'
    assert all(rows==classes[0] for rows in classes)


def test_real_output_does_not_depend_on_legal_class_registration_order(plate_step,tmp_path,monkeypatch):
    from drawform.output import dxf as output
    from drawform.pipeline import render_part
    original=output._new_document
    hashes=[];class_rows=[]
    for index,order in enumerate((('LAYOUT','ACDBPLACEHOLDER'),('ACDBPLACEHOLDER','LAYOUT'))):
        def create():
            doc=original()
            for name in order:doc.classes.add_class(name)
            return doc
        monkeypatch.setattr(output,'_new_document',create)
        folder=tmp_path/str(index);folder.mkdir();part=render_part(plate_step,folder,stem='stability',with_questions=True)
        path=dict(part.files)['dxf'];hashes.append(hashlib.sha256(canonical_bytes('dxf',path.read_bytes())).hexdigest())
        doc=ezdxf.readfile(path);assert not doc.audit().errors
        class_rows.append([(c.dxf.name,c.dxf.cpp_class_name) for c in doc.classes])
    assert hashes[0]==hashes[1], '相同类定义的注册先后不能改变最终DXF内容'
    assert class_rows[0]==class_rows[1]
