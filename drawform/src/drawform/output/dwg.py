"""DWG 出口：借外部转换器，不自己写 DWG。

为什么不自研（2026-10-08 核过原始出处，不是道听途说）：
DWG 是 Autodesk 的私有二进制格式，没有公开规范。唯一认真的开源实现 GNU LibreDWG
在自己的 README 里写得很清楚 —— 读没问题，写不行：
    "R2010-R2018 writing leads to CRC errors still."
    "Write support only works for earlier versions until r2004."
现代 AutoCAD 默认存 R2018，写出来带 CRC 错误等于交一个打不开的文件。
更关键的是我们交付的核心是**原生 DIMENSION 实体**，而 DIMENSION 是 DWG 里最复杂、
最随版本变化的对象之一，LibreDWG 对它的写入没有任何承诺。

所以这条路是**调外部转换器**：集团里大概率已经有 AutoCAD 席位，
`accoreconsole` 能把 DXF 存成 DWG，产物由正版 AutoCAD 写出，兼容性比任何第三方库都好。

⚠️ **本模块没有在任何真实 AutoCAD 上跑过** —— 开发机上没有。测到的只有
"调用拼装正确、失败会被如实报出、没有转换器时明确拒绝"这三件事。
在这之前，任何地方都不许写"支持 DWG 输出"。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

#: 指向一个能把 DXF 转成 DWG 的可执行文件（accoreconsole / ODAFileConverter / 自研脚本）。
#: 没设就是没配，产品照旧交付 DXF/PDF/SVG/JSON，不假装多一个出口。
CONVERTER_ENV = "DRAWFORM_DWG_CONVERTER"

TIMEOUT_SECONDS = 120.0


class DwgConverterMissing(Exception):
    """没配转换器。这是配置状态，不是失败 —— 调用方应当少产一个出口而不是崩掉。"""


class DwgConverterFailed(Exception):
    """转换器在，但它没干成。必须把它的输出带出来，否则运维只能猜。"""


def converter() -> str | None:
    """配置的转换器路径，或 None。"""
    value = os.environ.get(CONVERTER_ENV, "").strip()
    return value or None


def available() -> bool:
    """转换器配了、且在 PATH 里找得到。"""
    path = converter()
    return bool(path) and shutil.which(path) is not None


def convert(dxf_path, dwg_path, timeout: float = TIMEOUT_SECONDS) -> pathlib.Path:
    """把 DXF 转成 DWG。

    参数按 `[转换器, 输入, 输出]` 传 —— 这个约定属于部署方（不同转换器命令行不同），
    所以包一层脚本指过来即可，不在这里为每种转换器写分支：那才是屎山。
    """
    tool = converter()
    if not tool:
        raise DwgConverterMissing(
            f"没有配置 {CONVERTER_ENV}，跳过 DWG 出口。"
            "注意：本项目的 DWG 能力从未在真实 AutoCAD 上验证过。")
    source, target = pathlib.Path(dxf_path), pathlib.Path(dwg_path)
    try:
        done = subprocess.run([tool, str(source), str(target)],
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as error:
        raise DwgConverterFailed(f"转换器 {tool} 找不到：{error}") from error
    except subprocess.TimeoutExpired as error:
        raise DwgConverterFailed(f"转换器 {tool} 超时 {timeout}s") from error
    if done.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        raise DwgConverterFailed(
            f"{tool} 退出码 {done.returncode}，产物 {'未生成' if not target.exists() else '为空'}。"
            f" stderr: {(done.stderr or '').strip()[:400]}")
    return target
