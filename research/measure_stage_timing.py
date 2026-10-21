"""逐段量确定性内核的单件耗时：装载 → 特征 → 方案 → 投影 → 落位 → DXF。

给《模型价格表》的成本模型与《模型评测》的 M6 提供可重现的基线。
用法：cd drawform && PYTHONPATH=src ../spike/.venv/bin/python ../research/measure_stage_timing.py
注意：必须让 write_dxf / write_svg / write_pdf 全部进循环 —— GEO-013 就是因为这条路径从没被跑过而漏了 15/65 件。
"""
import time, statistics, tempfile, sys, traceback
from pathlib import Path
from dataclasses import replace
from drawform.eval.corpus import load_corpus
from drawform.geometry.loader import import_step
from drawform.geometry.features import extract_features
from drawform.geometry.centerlines import centerlines_for
from drawform.dimensioning.scheme import derive_scheme
from drawform.geometry.projection import FRONT, TOP, LEFT, project_view
from drawform.geometry.layout import layout_views
from drawform.dimensioning.place import place_scheme
from drawform.output.dxf import write_dxf
from drawform.output.pdf import write_pdf
from drawform.output.svg import write_svg

CORPUS = Path("corpus/cadsy") if Path("corpus/cadsy").exists() else Path("../corpus/cadsy")
stages = {k: [] for k in ("import","features","scheme","project","place","dxf","svg","pdf")}
tmp = Path(tempfile.mkdtemp()); n=0; errs=0
for s in load_corpus(CORPUS):
    try:
        p = s.directory/"model.step"
        t=time.perf_counter(); model=import_step(p); stages["import"].append(time.perf_counter()-t)
        t=time.perf_counter(); f=extract_features(model.shape, model.bbox); stages["features"].append(time.perf_counter()-t)
        t=time.perf_counter(); scheme=derive_scheme(model,f); stages["scheme"].append(time.perf_counter()-t)
        t=time.perf_counter(); proj={d.name:project_view(model.shape,d) for d in (FRONT,TOP,LEFT)}; stages["project"].append(time.perf_counter()-t)
        views=[replace(v,centerlines=centerlines_for(v.projection,f)) for v in layout_views(tuple(proj.values()))]
        t=time.perf_counter(); dims=place_scheme(scheme,proj); stages["place"].append(time.perf_counter()-t)
        t=time.perf_counter(); write_dxf(views,tmp/(s.sample_id+".dxf"),dimensions=dims,notes=scheme.notes); stages["dxf"].append(time.perf_counter()-t)
        t=time.perf_counter(); write_svg(views,tmp/(s.sample_id+".svg"),dimensions=dims,notes=scheme.notes); stages["svg"].append(time.perf_counter()-t)
        t=time.perf_counter(); write_pdf(views,tmp/(s.sample_id+".pdf"),dimensions=dims,notes=scheme.notes); stages["pdf"].append(time.perf_counter()-t)
        n+=1
    except Exception as e:
        errs+=1
        print("ERR", s.sample_id, repr(e), file=sys.stderr)
print("n",n,"errs",errs)
for k,v in stages.items():
    if not v: continue
    print(f"{k:<9} mean {statistics.mean(v)*1000:7.1f} ms  p95 {sorted(v)[max(0,int(len(v)*0.95)-1)]*1000:7.1f} ms")
tot_mean=sum(statistics.mean(v) for v in stages.values() if v)
per={k: sorted(v)[max(0,int(len(v)*0.95)-1)] for k,v in stages.items() if v}
print(f"TOTAL mean {tot_mean*1000:.1f} ms  p95(sum-of-per-stage-p95 保守上界) {sum(per.values())*1000:.1f} ms")
