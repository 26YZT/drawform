from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader
from OCP.TopoDS import TopoDS_Shape, TopoDS_Iterator
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_COMPSOLID, TopAbs_SOLID


class StepImportError(Exception):
    """STEP 无法读入，或读入后没有可用实体几何。"""


@dataclass(frozen=True)
class LoadedModel:
    shape: TopoDS_Shape
    bbox: tuple[float, float, float]
    #: 包围盒最小角。零件不一定在原点，而定位尺寸必须从基准面量起，
    #: 所以这个原点要和三个方向尺寸一起交出去。
    origin: tuple[float, float, float]
    #: 实体自身是否通过 OCCT 的几何有效性检查。无效的实体上"这一点在不在材料里"
    #: 是没有定义的（实测 cadsy part_0002：体心落在实体外，4 个圆柱面内外怎么探
    #: 都给同一个答案），继续硬做识别只会产出看似合理但错误的图纸。
    #: 交付门必须消费这个标记 —— 检查不通过不等于通过。
    is_valid: bool = True
    #: 唯一实体之外的非实体根几何类型；原 STEP 保留，范围选择随 JSON/警告披露。
    excluded_geometry: tuple[str, ...] = ()

    @property
    def solid_count(self) -> int:
        explorer = TopExp_Explorer(self.shape, TopAbs_SOLID)
        count = 0
        while explorer.More():
            count += 1
            explorer.Next()
        return count


def _excluded_types(shape, material):
    if shape.IsSame(material):
        return set()
    if shape.ShapeType() in (TopAbs_COMPOUND, TopAbs_COMPSOLID):
        result = set()
        children = TopoDS_Iterator(shape)
        while children.More():
            result.update(_excluded_types(children.Value(), material))
            children.Next()
        return result
    return {str(shape.ShapeType()).rsplit('TopAbs_', 1)[-1]}


def import_step(path: Path) -> LoadedModel:
    path = Path(path)
    reader = STEPControl_Reader()

    if reader.ReadFile(str(path)) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise StepImportError(f"无法解析 STEP 文件：{path.name}")

    if reader.NbRootsForTransfer() == 0:
        raise StepImportError(f"STEP 文件不含可转移的根实体：{path.name}")

    reader.TransferRoots()
    shape = reader.OneShape()

    if shape.IsNull():
        raise StepImportError(f"STEP 解析后得到空形状：{path.name}")

    source_valid = BRepCheck_Analyzer(shape).IsValid()
    solids = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        solids.append(explorer.Current())
        explorer.Next()
    excluded = ()
    if len(solids) == 1:
        # 单体零件按唯一材料实体制图，附加面/线/点不参与制造尺寸。
        # 多实体原样交给交付门拒绝，不能在这里任选一个或合并。
        excluded = tuple(sorted(_excluded_types(shape, solids[0])))
        shape = solids[0]

    box = Bnd_Box()
    # 必须用紧且精确的包围盒，实测三种口径都不一样：
    #   Add       松，按曲面控制域算，含 B 样条曲面的实体会明显外扩
    #              （cadsy part_0041 报 114.03，真实 80.00，虚高 43%）；
    #   AddClose  仍偏小（part_0027 报 81.04，真实 82.07）—— 偏小比偏大更危险，
    #              它会让我们把图纸比例选紧、把图裁掉；
    #   AddOptimal 与独立量出来的三视图投影范围逐例吻合。
    # 这个值决定选什么图纸比例，所以宁可慢也要准。
    BRepBndLib.AddOptimal_s(shape, box)
    if box.IsVoid():
        raise StepImportError(f"STEP 无有效几何包围盒：{path.name}")

    lo, hi = box.CornerMin(), box.CornerMax()
    bbox = (hi.X() - lo.X(), hi.Y() - lo.Y(), hi.Z() - lo.Z())
    if min(bbox) <= 1e-9:
        raise StepImportError(f"STEP 几何退化（包围盒 {bbox}），不是可制图的实体：{path.name}")

    return LoadedModel(shape=shape, bbox=bbox, origin=(lo.X(), lo.Y(), lo.Z()),
                       is_valid=source_valid and BRepCheck_Analyzer(shape).IsValid(),
                       excluded_geometry=excluded)
