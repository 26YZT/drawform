"""DWG 适配器：只测"不撒谎"这三件事。

本模块从未在真实 AutoCAD 上跑过（开发机上没有）。所以这里的测试**不声称**
"DWG 能出"，只钉住：没配转换器时绝不假装多一个出口、转换器失败时把原因带出来、
配好之后确实按 [转换器, 输入, 输出] 调用。

真机验证是部署清单上的一条待办，不是已经成立的事实。
"""

import os
import pathlib
import stat

import pytest

from drawform.output import dwg


def _fake(tmp_path, body, name="fakeconv") -> pathlib.Path:
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)
    return script


def test_without_a_configured_converter_there_is_no_dwg_outlet(tmp_path, monkeypatch):
    monkeypatch.delenv(dwg.CONVERTER_ENV, raising=False)

    assert dwg.available() is False
    with pytest.raises(dwg.DwgConverterMissing) as caught:
        dwg.convert(tmp_path / "a.dxf", tmp_path / "a.dwg")
    assert "从未在真实 AutoCAD 上验证" in str(caught.value)


def test_a_working_converter_is_called_with_input_then_output(tmp_path, monkeypatch):
    marker = tmp_path / "argv.txt"
    tool = _fake(tmp_path, f'echo "$1 $2" > {marker}; cp "$1" "$2"')
    monkeypatch.setenv(dwg.CONVERTER_ENV, str(tool))
    source = tmp_path / "a.dxf"
    source.write_bytes(b"DXF-CONTENT")

    out = dwg.convert(source, tmp_path / "a.dwg")

    assert out.read_bytes() == b"DXF-CONTENT"
    assert marker.read_text().strip() == f"{source} {tmp_path / 'a.dwg'}"


def test_a_failing_converter_reports_its_own_stderr(tmp_path, monkeypatch):
    tool = _fake(tmp_path, 'echo "accoreconsole: cannot open source file" >&2; exit 3')
    monkeypatch.setenv(dwg.CONVERTER_ENV, str(tool))

    with pytest.raises(dwg.DwgConverterFailed) as caught:
        dwg.convert(tmp_path / "a.dxf", tmp_path / "a.dwg")

    assert "退出码 3" in str(caught.value)
    assert "cannot open source file" in str(caught.value)


def test_a_converter_that_produces_an_empty_file_is_a_failure(tmp_path, monkeypatch):
    """退出码 0 但产物是空的 —— 只看返回码会把"没写出来"当成成功。"""
    tool = _fake(tmp_path, 'touch "$2"; exit 0')
    monkeypatch.setenv(dwg.CONVERTER_ENV, str(tool))

    with pytest.raises(dwg.DwgConverterFailed) as caught:
        dwg.convert(tmp_path / "a.dxf", tmp_path / "a.dwg")

    assert "为空" in str(caught.value)


def test_a_configured_but_absent_binary_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv(dwg.CONVERTER_ENV, str(tmp_path / "nope-not-here"))

    assert dwg.available() is False
    with pytest.raises(dwg.DwgConverterFailed):
        dwg.convert(tmp_path / "a.dxf", tmp_path / "a.dwg")
