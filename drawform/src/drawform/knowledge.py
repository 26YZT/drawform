""""这张图为什么这么画"的问答层：只检索自己算得出的事实。

出图说明（`output/assumptions.py`）里的每一条都是本次跑批**算出来的** —— 基准取了哪个
坐标、1020 条约束里几条带公差、尺寸链留空哪一环、哪些故意没画、哪些标准条款没核对。
本模块把这些做成可问的索引，并且守一条比"答得多"重要得多的性质：
**答不上来就明确说答不上来。** 集团自己的制图规范还没拿到，任何"按贵集团惯例"的回答都是编的。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 每个事实认领的关键词。这是检索词表，不是知识 —— 词表之外的问题一律拒答。
KEYWORDS: dict[str, tuple[str, ...]] = {
    "datum-face": ("基准", "量起", "从哪", "哪个面", "原点", "datum"),
    "general-tolerance": ("公差", "精度", "偏差", "2768", "tolerance"),
    "open-chains": ("尺寸链", "链", "留空", "封闭", "缺哪"),
    "not-drawn": ("没画", "没标", "未画", "故意", "漏", "分布圆", "pcd", "奇数"),
    "views-fixed": ("视图", "投影", "第一角", "第三角"),
    "title-block-unverified": ("标题栏", "图框", "标准", "核对", "10609", "模板", "没验"),
}


@dataclass(frozen=True)
class Fact:
    fact_id: str
    claim: str
    basis: str
    impact: str
    #: 是否已与集团真实图纸核对过。今天一条都没有 —— 面板必须把这件事说出来。
    checked: bool = False


NO_ANSWER = Fact(
    "no-answer",
    "答不了：这次出图算得出的事实里没有这个问题涉及的内容。"
    "集团自己的制图规范与历史图纸还没有拿到，猜一个答案比说没有更糟。",
    basis="drawform.knowledge 只索引 assumptions_for() 算出来的条目",
    impact="需要人工回答，或等历史图纸接入后由检索回答",
)


def index_of(assumptions) -> tuple[Fact, ...]:
    """把出图说明的条目变成可检索事实。没有关键词条目的问题域等于不存在。"""
    return tuple(
        Fact(fact_id=item["id"], claim=item["claim"], basis=item["basis"],
             impact=item.get("impact", ""))
        for item in assumptions if item["id"] in KEYWORDS)


def ask(question: str, facts: tuple[Fact, ...]) -> tuple[Fact, ...]:
    """按命中的关键词个数排序返回；一个都没命中就只返回 `NO_ANSWER`。"""
    scored = []
    for position, fact in enumerate(facts):
        hits = sum(1 for word in KEYWORDS[fact.fact_id] if word in question)
        if hits:
            scored.append((-hits, position, fact))
    if not scored:
        return (NO_ANSWER,)
    return tuple(fact for _, _, fact in sorted(scored))
