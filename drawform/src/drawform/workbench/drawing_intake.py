"""Bounded local drawing facts. Native entities/OCR, never pixel dimensions.

The parent controls paths and persistence. This child produces a reproducible
analysis snapshot; it cannot adopt a model or submit a drawing job.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "runtimes/drawing_bridge/drawing_ocr.swift"
NATIVE = ROOT / "drawform/out/drawing-native"
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
UNSUPPORTED = re.compile(r"盲孔|沉孔|沉头|螺纹|圆角|倒角|台阶|凸台|凸起|加强筋|筋板|肋板|凹槽|方孔|长圆孔|椭圆|斜孔|孔深|深度|锥度|不贯穿|非通孔|blind|counterbore|countersink|thread|fillet|chamfer|slot|pocket|\b(?:boss(?:es)?|ribs?|stepped|tapped|depth|taper)\b|square\s+hole|not\s+(?:a\s+)?through|non[-\s]?through", re.I)
LABELS = {
    "length_mm": rf"(?:长度|长|\blength\b|\bL(?=\s*[:=]))\s*[:=：]?\s*({NUMBER})",
    "width_mm": rf"(?:宽度|宽|\bwidth\b|\bW(?=\s*[:=]))\s*[:=：]?\s*({NUMBER})",
    "thickness_mm": rf"(?:厚度|厚|\bthickness\b|\bT(?=\s*[:=]))\s*[:=：]?\s*({NUMBER})",
    "outer_diameter_mm": rf"(?:外直径|外径|\bouter\s+diameter\b|\bOD(?=\s*[:=]))\s*[:=：]?\s*({NUMBER})",
    "inner_diameter_mm": rf"(?:内直径|内径|\binner\s+diameter\b|\bID(?=\s*[:=]))\s*[:=：]?\s*({NUMBER})",
}


class IntakeError(ValueError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def environment():
    return {k: v for k, v in os.environ.items()
            if k in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE")}


def ensure_ocr():
    """Compile only our fixed Swift source, locally, with a bounded wait."""
    if sys.platform != "darwin" or not shutil.which("xcrun"):
        raise IntakeError("本机缺少macOS本地识图运行环境；原生DXF仍可使用。")
    NATIVE.mkdir(parents=True, exist_ok=True)
    binary = NATIVE / "drawing_ocr"
    stamp = NATIVE / "drawing_ocr.sha256"
    source_sha = digest(SOURCE)
    deadline = time.monotonic() + 120
    with (NATIVE / "drawing_ocr.build.lock").open("a") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise IntakeError("本地识图程序编译等待超时。")
                time.sleep(.05)
        if binary.is_file() and not binary.is_symlink() and stamp.is_file() and stamp.read_text().strip() == source_sha:
            return binary
        temporary = NATIVE / f"drawing_ocr.{os.getpid()}.tmp"
        try:
            result = subprocess.run(["xcrun", "swiftc", "-O", str(SOURCE), "-o", str(temporary)],
                                    env=environment(), capture_output=True, timeout=120, check=False)
            (NATIVE / "drawing_ocr.compile.log").write_bytes(result.stdout + result.stderr)
            if result.returncode:
                raise IntakeError("本地识图程序编译失败，请查看本地识图缓存中的编译日志。")
            os.replace(temporary, binary)
            stamp.write_text(source_sha + "\n")
        except subprocess.TimeoutExpired as error:
            raise IntakeError("本地识图程序编译超时。") from error
        finally:
            temporary.unlink(missing_ok=True)
    return binary


def base(sha):
    return {"schema_version": 2, "source_sha256": sha, "kind": None,
            "parameters": {}, "holes": [], "units": None, "facts": [],
            "questions": [], "preview_file": "preview.png", "notes": []}


def fact(analysis, parameter, value, evidence, unit="source_unit"):
    item = {"id": f"fact-{len(analysis['facts']) + 1}", "parameter": parameter,
            "value": value, "unit": unit, "evidence": evidence}
    analysis["facts"].append(item)
    return item


def blocking(analysis, message, code="source_conflict"):
    analysis["unsupported"] = code
    analysis["notes"].append(message)
    if not any(q.get("readonly") for q in analysis["questions"]):
        analysis["questions"].append({"id": "source_conflict", "parameter": "source_conflict",
            "label": "请更正图纸后重新上传", "reason": message, "type": "choice",
            "choices": [], "readonly": True})


def question(analysis, parameter, label, reason, choices=None):
    item = {"id": parameter, "parameter": parameter, "label": label, "reason": reason,
            "type": "choice" if choices is not None else "number"}
    if choices is not None:
        item["choices"] = [{"value": value, "label": text} for value, text in choices]
    else:
        item["unit"] = "source_unit"
    analysis["questions"].append(item)


def close(a, b):
    return math.isclose(float(a), float(b), rel_tol=1e-7, abs_tol=1e-6)


def normalized(text):
    return unicodedata.normalize("NFKC", text).replace("⌀", "ø").replace("Ø", "ø")


def one_edit_apart(left, right):
    """For unread role labels only; this never recovers a numeric dimension."""
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) <= 1
    short, long = (left, right) if len(left) < len(right) else (right, left)
    return any(long[:i] + long[i+1:] == short for i in range(len(long)))


def unread_role(text, analysis):
    if analysis.get("kind") != "plate":
        return None
    match = re.fullmatch(rf"\s*([a-z]{{4,12}})\s*[:=：]?\s*{NUMBER}\s*(?:mm|inch(?:es)?)?\s*[.;]?\s*", text, re.I)
    if match:
        label = match.group(1).lower()
        for role in ("length", "width"):
            parameter = role + "_mm"
            if analysis["parameters"].get(parameter) is None and one_edit_apart(label, role):
                return parameter
    return None


def consume_annotations(analysis, entries):
    """Account for every selected-page text line, including low-confidence text.

    Grammar spans are consumed individually. Remaining words/digits cannot be
    silently treated as noise, even when another span on the same line is valid.
    Only a narrow, declared metadata grammar is allowed without geometric facts.
    """
    ledger = []
    for entry in entries:
        text = normalized(entry["text"])
        evidence = entry["evidence"]
        record = {"id": f"annotation-{len(ledger)+1}", "text": entry["text"], "roles": [], "evidence": evidence}
        possible_role = unread_role(text, analysis)
        if possible_role:
            record.update(status="unread_role", roles=[possible_role])
            fact(analysis, "unread_role", entry["text"], evidence, "text")
            ledger.append(record)
            continue
        # Identifiers/page counts/scales are the only whole-line metadata forms.
        metadata = (r"(?:图号|drawing\s*(?:no\.?|number)|part\s*(?:no\.?|number))\s*[:=：]\s*[a-z0-9_.-]+",
                    r"(?:第\s*\d+\s*页|page\s*\d+(?:\s*of\s*\d+)?)",
                    rf"(?:比例|scale)\s*[:=：]?\s*{NUMBER}\s*[:：/]\s*{NUMBER}")
        if any(re.fullmatch(pattern, text.strip(), re.I) for pattern in metadata):
            record.update(status="allowed_text", roles=["metadata"])
            ledger.append(record)
            continue
        remaining = list(text)
        def consume(pattern, role):
            for match in re.finditer(pattern, text, re.I):
                remaining[match.start():match.end()] = " " * (match.end()-match.start())
                if role not in record["roles"]:
                    record["roles"].append(role)
        # A low-confidence numeric role must not become an accepted dimension.
        trusted = evidence.get("confidence", 1) >= .4
        if trusted:
            active = {'length_mm','width_mm','thickness_mm'} if analysis['kind']=='plate' else {'outer_diameter_mm','inner_diameter_mm','thickness_mm'}
            for parameter, pattern in LABELS.items():
                if parameter not in active:continue
                consume(pattern, parameter)
            consume(r"矩形(?:板)?|长方形(?:板)?|\brectangular\s+plate\b|垫圈|圆环|\bwasher\b|圆盘|圆板|\bdisc\b|\bdisk\b", "kind")
            consume(r"毫米|(?<![a-z])mm\b|英寸|(?<![a-z])inch(?:es)?\b", "units")
            consume(r"(?:单位|\bunits?\b)\s*[:=：]?", "units_label")
            consume(r"(?:零件名称|图纸|\bpart\s+name\b|\bdrawing\b)\s*[:=：]?", "title_label")
            consume(r"无孔|没有孔|\bno\s+holes?\b|\bsolid\s+plate\b", "hole_mode")
            consume(r"相对板中心|板中心为原点|relative\s+to\s+(?:the\s+)?plate\s+cent(?:er|re)|origin\s*[:=]?\s*plate\s+cent(?:er|re)", "hole_origin")
            if "hole_origin" in record["roles"]:
                consume(r"孔坐标|\bhole\s+coordinates\b", "hole_origin")
            consume(r"中心|中央|中间|\bcent(?:er|re)\b", "hole_position")
            consume(r"通孔|贯穿|\bthrough\b", "through")
            if analysis['kind']=='plate' and analysis['holes']:
                consume(rf"(?:直径|孔径|\bdiameter\b|ø|\bD(?=\s*[:=]))\s*[:=：]?\s*{NUMBER}", "hole_diameter")
                consume(rf"\b[XY]\s*[:=：]\s*{NUMBER}", "hole_position")
                consume(r"孔\s*\d*|\bholes?\s*\d*", "hole_declaration")
            if len(analysis["holes"]) == 1:
                consume(r"一个|一处|\bone\b|\bsingle\b", "hole_count")
        residual = re.sub(r"[\s,，;；:：=。.!！?？、()（）\[\]{}-]", "", "".join(remaining))
        if residual:
            record.update(status="unexplained", residual=residual)
            fact(analysis, "unexplained_annotation", entry["text"], evidence, "text")
            if not analysis.get("unsupported"):
                analysis["unsupported"] = "unexplained_annotation"
                analysis["questions"].append({"id": "unexplained_annotation", "parameter": "unexplained_annotation",
                    "label": "请核对未解释的原图要求并更正上传", "reason": f"此原文未被有限建模语法解释：{entry['text']}",
                    "type": "choice", "choices": [], "readonly": True})
            analysis["notes"].append(f"未解释原文（第{evidence['page_index']+1}页）：{entry['text']}")
        else:
            record["status"] = "consumed_role" if record["roles"] else "allowed_text"
        ledger.append(record)
    analysis["text_consumption"] = ledger


def text_facts(analysis, entries, *, native=False):
    """Only role-labelled numbers become facts. Generic dimension text stays evidence."""
    labels = {}
    unit_values = set()
    through = False
    for entry in entries:
        text = normalized(entry["text"])
        evidence = entry["evidence"]
        if UNSUPPORTED.search(text):
            blocking(analysis, "图纸包含本批不支持的特征，不能按简单板件或垫圈省略处理。", "unsupported_feature")
        if re.search(r"±|公差|配合|\btolerance\b|(?<=\d)[hHfFgGkKmMpP]\d{1,2}\b", text, re.I):
            blocking(analysis, "本批尚不支持从输入图纸复现公差，请先使用无公差的简单几何图。", "unsupported_tolerance")
        if re.search(r"毫米|(?<![A-Za-z])mm\b", text, re.I):
            unit_values.add("mm")
            fact(analysis, "units", "mm", evidence, "unit")
        if re.search(r"英寸|(?<![A-Za-z])inch(?:es)?\b", text, re.I):
            unit_values.add("inch")
            fact(analysis, "units", "inch", evidence, "unit")
        if re.search(r"厘米|(?<![A-Za-z])cm\b|\bmeters?\b", text, re.I):
            blocking(analysis, "输入图纸单位不在本批毫米/英寸范围内。", "unsupported_unit")
        if re.search(r"通孔|贯穿|\bthrough\b", text, re.I):
            through = True
            fact(analysis, "through", "through", evidence, "intent")
        for match in re.finditer(rf"(?:直径|孔径|\bdiameter\b|ø)\s*[:=：]?\s*({NUMBER})", text, re.I):
            if re.match(r"(?:\s*/\s*\d|,\d|[eE][-+]?\d)", text[match.end(1):]):
                blocking(analysis, "孔径不是本批支持的明确十进制数，不能只读取其前半部分。", "unsupported_numeric_format")
        for parameter, pattern in LABELS.items():
            for match in re.finditer(pattern, text, re.I):
                if re.match(r"(?:\s*/\s*\d|,\d|[eE][-+]?\d)", text[match.end(1):]):
                    blocking(analysis, "本批尺寸仅支持明确十进制数；分数、小数逗号或科学计数法须更正源图。", "unsupported_numeric_format")
                    continue
                value = float(match.group(1))
                fact(analysis, parameter, value, evidence)
                if parameter in labels and not close(labels[parameter], value):
                    blocking(analysis, f"图纸中的{parameter}出现不同明确数值，须更正源图。")
                labels[parameter] = value
    if len(unit_values) > 1:
        blocking(analysis, "图纸同时出现毫米和英寸，不能自行选择单位。")
    elif unit_values:
        unit = next(iter(unit_values))
        if analysis["units"] and analysis["units"] != unit:
            blocking(analysis, "图纸明确单位文字与DXF原生单位不一致。")
        else:
            analysis["units"] = unit
    for parameter, value in labels.items():
        if parameter in analysis["parameters"] and not close(analysis["parameters"][parameter], value):
            blocking(analysis, f"{parameter}的文字尺寸与原生轮廓不一致，须更正源图。")
        elif not native or parameter == "thickness_mm":
            analysis["parameters"][parameter] = value
    return labels, through


def finish(analysis, through):
    if not analysis["kind"]:
        blocking(analysis, "没有足够明确的简单零件轮廓/类型，不能仅靠手填所有尺寸代替识图。", "unreadable_shape")
        return analysis
    keys = ("length_mm", "width_mm", "thickness_mm") if analysis["kind"] == "plate" else ("outer_diameter_mm", "inner_diameter_mm", "thickness_mm")
    for key in keys:
        analysis["parameters"].setdefault(key, None)
    for item in analysis["facts"]:
        if item["unit"] == "source_unit" and analysis["units"]:
            item["unit"] = analysis["units"]
    if analysis.get("unsupported"):
        return analysis
    if analysis["units"] is None:
        question(analysis, "units", "图纸尺寸采用什么单位？", "源图没有明确毫米或英寸单位。", [("mm", "毫米"), ("inch", "英寸")])
    if analysis["parameters"].get("thickness_mm") is None:
        question(analysis, "thickness_mm", "零件厚度是多少？", "平面轮廓不能证明厚度，请填写图纸实际厚度。")
    for parameter, label in (("length_mm", "长度"), ("width_mm", "宽度")):
        if analysis["kind"] == "plate" and analysis["parameters"].get(parameter) is None:
            question(analysis, parameter, f"矩形板{label}是多少？", f"已有其他明确尺寸，但源图{label}标签未能可靠读出。")
    has_holes = bool(analysis["holes"]) or analysis["kind"] == "washer" and analysis["parameters"].get("inner_diameter_mm", 0) > 0
    if has_holes and not through:
        question(analysis, "through", "圆孔是否沿厚度方向贯穿？", "平面圆不能证明通孔；本批仅支持贯穿孔。", [("through", "确认全部为贯穿孔")])
    analysis["notes"].append("参数字段保留原图单位；确认单位后由服务端统一换算毫米。识图与尺寸确认不等于工程审图。")
    return analysis


def entity_evidence(entity, text=None):
    result = {"type": "dxf_entity", "entity_handle": entity.dxf.handle,
              "layer": entity.dxf.layer}
    if text is not None:
        result["text"] = text
    return result


def rectangle(points):
    if len(points) != 4 or any(not all(math.isfinite(float(v)) for v in point) for point in points):
        return None
    xs = sorted(set(round(float(p[0]), 8) for p in points))
    ys = sorted(set(round(float(p[1]), 8) for p in points))
    if len(xs) != 2 or len(ys) != 2 or len(set(tuple(p) for p in points)) != 4:
        return None
    if any(not (close(points[i][0], points[(i+1) % 4][0]) or close(points[i][1], points[(i+1) % 4][1])) for i in range(4)):
        return None
    return xs[0], ys[0], xs[1], ys[1]


def rectangle_lines(lines):
    if len(lines) != 4:
        return None
    endpoints = [(float(p.x), float(p.y)) for e in lines for p in (e.dxf.start, e.dxf.end)]
    vertices = []
    for p in endpoints:
        if not any(close(p[0], q[0]) and close(p[1], q[1]) for q in vertices):
            vertices.append(p)
    if len(vertices) != 4:
        return None
    for p in vertices:
        if sum(close(p[0], q[0]) and close(p[1], q[1]) for q in endpoints) != 2:
            return None
    xs, ys = sorted({p[0] for p in vertices}), sorted({p[1] for p in vertices})
    if len(xs) != 2 or len(ys) != 2:
        return None
    if any(not (close(e.dxf.start.x, e.dxf.end.x) or close(e.dxf.start.y, e.dxf.end.y)) for e in lines):
        return None
    return xs[0], ys[0], xs[1], ys[1]


def preview_dxf(document, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.config import Configuration, ImagePolicy
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    fig = plt.figure(figsize=(10, 7), dpi=130)
    axis = fig.add_axes([0, 0, 1, 1])
    try:
        config = Configuration(image_policy=ImagePolicy.IGNORE)
        Frontend(RenderContext(document), MatplotlibBackend(axis), config=config).draw_layout(document.modelspace(), finalize=True)
        fig.savefig(output / "preview.png", dpi=130)
    finally:
        plt.close(fig)


def analyze_dxf(path, output, sha):
    import ezdxf
    analysis = base(sha)
    document = ezdxf.readfile(path)
    entities = list(document.modelspace())
    if not entities or len(entities) > 10000:
        raise IntakeError("DXF没有可读实体或超出10000实体限制。")
    # Render the actual drawing, never a reconstruction from inferred parameters.
    preview_dxf(document, output)
    from .shaft_input import has_shaft_declaration, analyze_shaft_dxf
    if any(has_shaft_declaration(e.plain_text()) for e in entities if e.dxftype() in ('TEXT','MTEXT')):
        return analyze_shaft_dxf(document,sha)
    geometry = [e for e in entities if e.dxftype() in ("LWPOLYLINE", "LINE", "CIRCLE")]
    unknown = sorted({e.dxftype() for e in entities if e.dxftype() not in ("LWPOLYLINE", "LINE", "CIRCLE", "TEXT", "MTEXT", "DIMENSION")})
    if unknown:
        blocking(analysis, "原生图纸包含本批未支持的实体：" + "、".join(unknown), "unsupported_entity")
    header_unit = int(document.header.get("$INSUNITS", 0))
    if header_unit in (1, 4):
        analysis["units"] = {1: "inch", 4: "mm"}[header_unit]
        fact(analysis, "units", analysis["units"], {"type": "dxf_header", "text": f"$INSUNITS={header_unit}"}, "unit")
    elif header_unit != 0:
        blocking(analysis, "DXF原生单位不是本批支持的毫米或英寸。", "unsupported_unit")
    polylines = [e for e in geometry if e.dxftype() == "LWPOLYLINE"]
    lines = [e for e in geometry if e.dxftype() == "LINE"]
    circles = [e for e in geometry if e.dxftype() == "CIRCLE"]
    z_values = []
    for e in geometry:
        extrusion = tuple(float(v) for v in e.dxf.get("extrusion", (0, 0, 1)))
        if any(not close(a, b) for a, b in zip(extrusion, (0, 0, 1))):
            blocking(analysis, "原生实体不在同一XY平面方向。", "unsupported_geometry")
        if e.dxftype() == "LWPOLYLINE":
            z_values.append(float(e.dxf.elevation))
        elif e.dxftype() == "CIRCLE":
            z_values.append(float(e.dxf.center.z))
            if not math.isfinite(float(e.dxf.radius)) or e.dxf.radius <= 0:
                blocking(analysis, "圆半径无效。", "unsupported_geometry")
        else:
            z_values.extend((float(e.dxf.start.z), float(e.dxf.end.z)))
    if z_values and (not all(math.isfinite(z) for z in z_values) or max(z_values) - min(z_values) > 1e-6):
        blocking(analysis, "原生实体存在多个投影平面；本批只接受单个平面轮廓。", "unsupported_geometry")
    bounds = None
    outline = None
    if len(polylines) == 1 and not lines:
        poly = polylines[0]
        if poly.closed and all(close(p[4], 0) for p in poly.get_points()):
            bounds = rectangle([(float(p[0]), float(p[1])) for p in poly.get_points()])
            outline = poly
    elif not polylines and lines:
        bounds = rectangle_lines(lines)
        outline = lines[0]
    if bounds:
        xmin, ymin, xmax, ymax = bounds
        center = ((xmin + xmax) / 2, (ymin + ymax) / 2)
        analysis["kind"] = "plate"
        analysis["parameters"].update(length_mm=xmax-xmin, width_mm=ymax-ymin)
        for key in ("length_mm", "width_mm"):
            fact(analysis, key, analysis["parameters"][key], entity_evidence(outline))
        if len(circles) > 16:
            blocking(analysis, "本批板件最多支持16个圆孔。", "unsupported_geometry")
        for index, e in enumerate(sorted(circles, key=lambda c: (float(c.dxf.center.x), float(c.dxf.center.y), float(c.dxf.radius)))):
            hole = {"x_mm": float(e.dxf.center.x-center[0]), "y_mm": float(e.dxf.center.y-center[1]), "diameter_mm": float(e.dxf.radius*2)}
            analysis["holes"].append(hole)
            for key, value in hole.items():
                fact(analysis, f"holes.{index}.{key}", value, entity_evidence(e))
            if e.dxf.center.x-e.dxf.radius <= xmin or e.dxf.center.x+e.dxf.radius >= xmax or e.dxf.center.y-e.dxf.radius <= ymin or e.dxf.center.y+e.dxf.radius >= ymax:
                blocking(analysis, "圆孔接触或穿越矩形边缘，不能作为本批独立通孔。", "unsupported_geometry")
        for i, a in enumerate(analysis["holes"]):
            for b in analysis["holes"][i+1:]:
                if math.hypot(a["x_mm"]-b["x_mm"], a["y_mm"]-b["y_mm"]) <= (a["diameter_mm"]+b["diameter_mm"])/2:
                    blocking(analysis, "圆孔相交或相切，不能按独立通孔建模。", "unsupported_geometry")
    elif not polylines and not lines and len(circles) in (1, 2):
        ordered = sorted(circles, key=lambda c: c.dxf.radius, reverse=True)
        if len(ordered) == 1 or all(close(a, b) for a, b in zip(ordered[0].dxf.center, ordered[1].dxf.center)):
            analysis["kind"] = "washer"
            analysis["parameters"] = {"outer_diameter_mm": float(ordered[0].dxf.radius*2), "inner_diameter_mm": float(ordered[1].dxf.radius*2) if len(ordered) == 2 else 0.0}
            for index, key in enumerate(("outer_diameter_mm", "inner_diameter_mm")):
                fact(analysis, key, analysis["parameters"][key], entity_evidence(ordered[min(index, len(ordered)-1)]))
            if len(ordered) == 2 and close(ordered[0].dxf.radius, ordered[1].dxf.radius):
                blocking(analysis, "垫圈内外圆重合。", "unsupported_geometry")
    if not analysis["kind"]:
        blocking(analysis, "未找到唯一轴对齐矩形或同心圆轮廓；多视图/复杂轮廓需要后续支持。", "unsupported_geometry")
    entries = []
    for e in entities:
        if e.dxftype() in ("TEXT", "MTEXT"):
            text = e.plain_text()
            entries.append({"text": text, "evidence": entity_evidence(e, text)})
    _, through = text_facts(analysis, entries, native=True)
    for entry in entries:
        text = normalized(entry["text"]).replace("%%c", "ø")
        for count, diameter in re.findall(rf"(\d+)\s*[x×]\s*ø\s*({NUMBER})", text, re.I):
            matching = sum(close(float(c.dxf.radius)*2, float(diameter)) for c in circles)
            fact(analysis, "hole_count_callout", int(count), entry["evidence"], "count")
            if int(count) != matching:
                blocking(analysis, "孔数量标注与相同直径的实际圆实体数量不一致。")
        diameter_matches = re.findall(rf"(?:直径|孔径|\bdiameter\b|ø)\s*[:=：]?\s*({NUMBER})", text, re.I)
        for diameter in diameter_matches:
            if not any(close(float(c.dxf.radius)*2, float(diameter)) for c in circles):
                blocking(analysis, "孔径文字标注与实际圆实体不一致。")
            if bounds and re.search(r"中心|中央|中间|\bcent(?:er|re)\b", text, re.I):
                if not any(close(float(c.dxf.radius)*2, float(diameter)) and close(c.dxf.center.x, center[0]) and close(c.dxf.center.y, center[1]) for c in circles):
                    blocking(analysis, "中心孔文字标注与实际圆心位置不一致。")
    x_coords, y_coords = [], []
    if bounds:
        x_coords = [bounds[0], bounds[2]] + [float(e.dxf.center.x) for e in circles]
        y_coords = [bounds[1], bounds[3]] + [float(e.dxf.center.y) for e in circles]
    for e in entities:
        if e.dxftype() != "DIMENSION":
            continue
        try:
            dimtype = e.dimtype
            measurement = e.get_measurement()
            if dimtype not in (0, 1, 3, 4) or not isinstance(measurement, (int, float)):
                blocking(analysis, "本批不支持此原生尺寸类型。", "unsupported_dimension")
                continue
            measured = float(measurement)
            displayed = e.dxf.get("text", "")
            evidence = entity_evidence(e, displayed)
            fact(analysis, "native_dimension", measured, evidence)
            factor = float(e.override().get("dimlfac", 1.0))
            if not close(factor, 1):
                blocking(analysis, "原生尺寸使用非1的dimlfac，显示量与几何量存在比例歧义。")
            explicit = re.findall(NUMBER, normalized(displayed)) if displayed.strip() and "<>" not in displayed else []
            if explicit and (len(explicit) != 1 or not close(float(explicit[0]), measured)):
                fact(analysis, "displayed_dimension_override", displayed, evidence, "text")
                blocking(analysis, "原生尺寸显示替换与实际几何测量不一致。")
            if dimtype in (3, 4):
                candidates = [float(c.dxf.radius) * (2 if dimtype == 3 else 1) for c in circles]
                if not any(close(measured, c) for c in candidates):
                    blocking(analysis, "原生直径/半径尺寸与实际圆实体不一致。")
            elif bounds:
                p1, p2 = e.dxf.defpoint2, e.dxf.defpoint3
                angle = float(e.dxf.get("angle", 0)) % 180
                if dimtype == 1:
                    angle = math.degrees(math.atan2(p2.y-p1.y, p2.x-p1.x)) % 180
                coords, values = (x_coords, (p1.x, p2.x)) if close(angle, 0) else (y_coords, (p1.y, p2.y)) if close(angle, 90) else ([], ())
                if not coords or any(not any(close(v, c) for c in coords) for v in values):
                    blocking(analysis, "原生线性尺寸的定义点未对应已识别轮廓/孔位。")
        except (AttributeError, TypeError, ValueError, ZeroDivisionError):
            blocking(analysis, "原生尺寸定义损坏或不可核对。")
    return finish(analysis, through)


def analyze_raster(path, output, sha, format):
    binary = ensure_ocr()
    try:
        result = subprocess.run([str(binary), str(path), format, str(output)],
                                env=environment(), capture_output=True, timeout=90, check=False)
    except subprocess.TimeoutExpired as error:
        raise IntakeError("本地图纸识字超时。") from error
    (output / "ocr.stderr.log").write_bytes(result.stderr)
    if result.returncode or len(result.stdout) > 8_000_000:
        raise IntakeError("本地图纸识字失败或内容超出限制。")
    try:
        payload = json.loads(result.stdout)
        pages = payload["pages"]
        if not isinstance(pages, list) or not 1 <= len(pages) <= 4:
            raise ValueError("invalid page count")
    except (ValueError, KeyError, TypeError) as error:
        raise IntakeError("本地识字回执格式无效。") from error
    (output / "ocr.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    from .shaft_input import has_shaft_declaration, analyze_shaft_entries
    if any(has_shaft_declaration(item.get('text','')) for page in pages for item in page.get('texts',[])):
        shaft_entries=[]
        for page in pages:
            for item in page.get('texts',[]):
                if not isinstance(item.get('text'),str) or not item['text'].strip():continue
                shaft_entries.append({'text':item['text'],'evidence':{'type':item.get('method','vision_ocr'),
                    'page_index':page['page_index'],'bbox':item['bbox'],'text':item['text'],'confidence':item['confidence']}})
        result=analyze_shaft_entries(shaft_entries,sha)
        selected_page=next((f['evidence']['page_index'] for f in result['facts'] if f['parameter'] in result['parameters'] and f['value'] is not None),pages[0]['page_index'])
        selected=next(page for page in pages if page['page_index']==selected_page)
        preview=output/selected['preview_file']
        if preview.parent!=output or not preview.is_file():raise IntakeError('轴图纸预览缺失。')
        shutil.copyfile(preview,output/'preview.png')
        return result
    analysis = base(sha)
    # One coherent part per source. Other pages must not contribute hidden dimensions.
    entries = []
    all_entries = []
    selected = None
    candidate_pages = []
    for page in pages:
        current = []
        for item in page.get("texts", []):
            text = item.get("text", "")
            if not isinstance(text, str) or not text.strip():
                continue
            current.append({"text": text, "evidence": {"type": item.get("method", "vision_ocr"),
                "page_index": page["page_index"], "bbox": item["bbox"], "text": text,
                "confidence": item["confidence"]}})
        all_entries.extend(current)
        joined = "\n".join(e["text"] for e in current)
        if any(re.search(pattern, normalized(joined), re.I) for pattern in LABELS.values()):
            candidate_pages.append((page, current))
    if candidate_pages:
        selected, entries = candidate_pages[0]
    else:
        selected = pages[0]
    if len(candidate_pages) > 1:
        blocking(analysis, "多个PDF页面包含建模尺寸；本批需上传单个零件的单页明确图纸。", "ambiguous_pages")
    source_preview = output / selected["preview_file"]
    if source_preview.parent != output or not source_preview.is_file():
        raise IntakeError("本地源图预览缺失。")
    shutil.copyfile(source_preview, output / "preview.png")
    trusted_entries = [e for e in entries if e["evidence"]["confidence"] >= .4]
    trusted_all = [e for e in all_entries if e["evidence"]["confidence"] >= .4]
    joined = normalized("\n".join(e["text"] for e in trusted_entries))
    labels, through = text_facts(analysis, trusted_all)
    is_plate = bool(re.search(r"矩形(?:板)?|长方形(?:板)?|\brectangular\s+plate\b", joined, re.I))
    is_washer = bool(re.search(r"垫圈|圆环|\bwasher\b", joined, re.I))
    is_disc = bool(re.search(r"圆盘|圆板|\bdisc\b|\bdisk\b", joined, re.I))
    if sum((is_plate, is_washer, is_disc)) != 1:
        blocking(analysis, "需要明确矩形板、垫圈或圆盘的类型文字；本批不会从图片像素猜测轮廓。", "unreadable_shape")
    elif is_plate:
        analysis["kind"] = "plate"
        analysis["parameters"] = {k: v for k, v in labels.items() if k in ("length_mm", "width_mm", "thickness_mm")}
        if not any(k in labels for k in ("length_mm", "width_mm")) or len(set(labels) & {"length_mm", "width_mm", "thickness_mm"}) < 2:
            blocking(analysis, "没有足够的明确角色尺寸，不能让手工填写代替全部识图。", "unreadable_dimensions")
        explicit_none = bool(re.search(r"无孔|没有孔|\bno\s+holes?\b|\bsolid\s+plate\b", joined, re.I))
        hole_entries = [e for e in trusted_entries if re.search(r"孔|\bhole\b", normalized(e["text"]), re.I) and not re.search(r"无孔|没有孔|\bno\s+holes?\b", normalized(e["text"]), re.I)]
        if explicit_none and hole_entries:
            blocking(analysis, "图纸同时声明无孔和有孔，须更正源图。")
        for entry in hole_entries:
            text = normalized(entry["text"])
            diameters = list(re.finditer(rf"(?:直径|孔径|\bdiameter\b|[ø]|\bD(?=\s*[:=]))\s*[:=：]?\s*({NUMBER})", text, re.I))
            if len(diameters) != 1:
                blocking(analysis, "孔声明缺少唯一可读直径，或包含本批不能展开的孔阵列。", "unreadable_holes")
                continue
            diameter = float(diameters[0].group(1))
            if re.search(r"中心|中央|中间|\bcent(?:er|re)\b", text, re.I):
                x = y = 0.0
            else:
                mx = re.search(rf"\bX\s*[:=：]\s*({NUMBER})", text, re.I)
                my = re.search(rf"\bY\s*[:=：]\s*({NUMBER})", text, re.I)
                if not mx or not my:
                    blocking(analysis, "孔位置没有明确的中心声明或相对板中心X/Y数值，不能按像素推孔位。", "unreadable_holes")
                    continue
                x, y = float(mx.group(1)), float(my.group(1))
                if not re.search(r"相对板中心|板中心为原点|relative\s+to\s+(?:the\s+)?plate\s+cent(?:er|re)|origin\s*[:=]?\s*plate\s+cent(?:er|re)", joined, re.I):
                    blocking(analysis, "孔X/Y的坐标原点未明确为板中心。", "unreadable_holes")
            hole = {"x_mm": x, "y_mm": y, "diameter_mm": diameter}
            index = len(analysis["holes"])
            analysis["holes"].append(hole)
            for key, value in hole.items():
                fact(analysis, f"holes.{index}.{key}", value, entry["evidence"])
        if not explicit_none and not hole_entries:
            blocking(analysis, "图纸未明确无孔或可读孔配置，本批不能忽略图片中可能存在的孔。", "unreadable_holes")
        if len(analysis["holes"]) > 16:
            blocking(analysis, "本批最多支持16个明确圆孔。", "unsupported_geometry")
    elif is_washer or is_disc:
        analysis["kind"] = "washer"
        analysis["parameters"] = {k: v for k, v in labels.items() if k in ("outer_diameter_mm", "inner_diameter_mm", "thickness_mm")}
        if "outer_diameter_mm" not in labels or is_washer and "inner_diameter_mm" not in labels:
            blocking(analysis, "垫圈须有明确内外径，圆盘须有明确外径。", "unreadable_dimensions")
        if is_disc:
            analysis["parameters"]["inner_diameter_mm"] = 0.0
            if labels.get("inner_diameter_mm", 0) != 0:
                blocking(analysis, "圆盘类型与非零内径冲突。")
    consume_annotations(analysis, all_entries)
    return finish(analysis, through)


def run(request_path):
    request_path = Path(request_path)
    if request_path.stat().st_size > 65536:
        raise IntakeError("识图请求超出大小限制。")
    request = json.loads(request_path.read_text())
    if not isinstance(request, dict) or set(request) != {"input_path", "format", "output_dir", "source_sha256"}:
        raise IntakeError("识图请求字段不正确。")
    source, output = Path(request["input_path"]), Path(request["output_dir"])
    if not source.is_absolute() or source.is_symlink() or source != source.resolve() or not source.is_file():
        raise IntakeError("源图必须是父进程选择的规范本地普通文件。")
    if source.stat().st_size > 32_000_000:
        raise IntakeError("源图超过32MB。")
    if not output.is_absolute() or output != output.resolve() or output.is_symlink():
        raise IntakeError("识图输出目录必须是规范绝对路径。")
    sha = request["source_sha256"]
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha) or digest(source) != sha:
        raise IntakeError("源图摘要不一致。")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise IntakeError("识图输出目录必须为新目录或空目录。")
    output.mkdir(parents=True, exist_ok=True)
    format = request["format"]
    if format == "dxf":
        analysis = analyze_dxf(source, output, sha)
    elif format in ("pdf", "png", "jpeg"):
        analysis = analyze_raster(source, output, sha, format)
    else:
        raise IntakeError("不支持此图纸格式。")
    if digest(source) != sha:
        raise IntakeError("识图期间源图发生变化，拒绝发布分析。")
    if not (output / "preview.png").is_file():
        raise IntakeError("源图预览没有生成。")
    (output / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return analysis


def main():
    try:
        if len(sys.argv) != 2:
            raise IntakeError("用法：python -m drawform.workbench.drawing_intake request.json")
        result = run(sys.argv[1])
        print(json.dumps({"status": "analyzed", "source_sha256": result["source_sha256"], "unsupported": result.get("unsupported")}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
