"""非对称计分：漏标一条与多标一条不该扣一样的分。

车间里的代价是不对称的 —— 漏一个孔的定位尺寸，这批件可能整批报废；多标一条尺寸，
只是图上啰嗦。项目文档早就记下"两者差一个量级"，但一直到这轮之前，
用来决定下一步做什么的那个 F1 仍然把两者当成等价物。

后果不是理论上的：M4-c 那一轮"precision 74%→84%、recall 微跌"被当成了进步，
而按生产风险它未必是。这个文件负责把尺子掰正。
"""
import pytest

from drawform.eval.score import STEERING_BETA, score_groups


def _two_way():
    """造两个 F1 完全相等、但错法相反的样本。

    A 漏标两条（真实 10 条只标出 8 条）；B 多标两条（真实 8 条标了 10 条）。
    """
    a = score_groups({"linear": [float(i) for i in range(1, 11)]},
                     {"linear": [float(i) for i in range(1, 9)]})
    b = score_groups({"linear": [float(i) for i in range(1, 9)]},
                     {"linear": [float(i) for i in range(1, 11)]})
    return a, b


def test_missing_and_spurious_are_identical_under_f1():
    """先把"旧尺子量不出差别"这件事钉住 —— 它解释了为什么需要新尺子。"""
    a, b = _two_way()

    assert a.matched == b.matched == 8
    assert a.recall == pytest.approx(b.precision)
    assert a.precision == pytest.approx(b.recall)
    assert a.f(1.0) == pytest.approx(b.f(1.0)), "F1 应当对两种错法无差别"


def test_a_missed_dimension_costs_more_than_a_spurious_one():
    """漏标必须比多标更疼。方向反了比没有尺子更糟。"""
    a, b = _two_way()

    assert a.f(2.0) < b.f(2.0), \
        f"漏标两条得 {a.f(2.0):.3f}，多标两条却得 {b.f(2.0):.3f}"


def test_the_penalty_grows_with_the_stated_risk_ratio():
    """β 越大越保守：β=√10 对应文档里"差一个量级"的判断。

    β 不是从数据里量出来的，是生产风险的陈述折算 —— 所以这里测的是"折算方向对不对"，
    而不是某个具体值。β=1 必须退回 F1，否则这个参数是假的。
    """
    a, b = _two_way()

    assert a.f(1.0) == pytest.approx(
        2 * a.recall * a.precision / (a.recall + a.precision))
    assert b.f(2.0) - a.f(2.0) > 0
    assert (b.f(10 ** 0.5) - a.f(10 ** 0.5)) > (b.f(2.0) - a.f(2.0)), \
        "把风险差放大，两种错法的分差必须跟着放大"


def test_the_default_is_the_asymmetric_one_not_f1():
    """默认值才是真正会被用的那个数。如果 `f()` 默认退回 F1，
    所有忘记传 β 的调用点都会悄悄回到那把歪尺子。
    """
    a, b = _two_way()

    assert a.f() == pytest.approx(a.f(STEERING_BETA))
    assert a.f() < b.f(), "默认口径必须已经偏重别漏标"
    assert STEERING_BETA > 1.0, f"β={STEERING_BETA} 不构成不对称"


def test_a_perfect_drawing_scores_one_regardless_of_beta():
    """边界：全对时任何 β 都应该是 1，否则这个数不能跨 β 比较。"""
    perfect = score_groups({"linear": [10.0, 20.0]}, {"linear": [10.0, 20.0]})

    for beta in (1.0, 2.0, 10 ** 0.5):
        assert perfect.f(beta) == pytest.approx(1.0)


def test_an_empty_drawing_scores_zero_not_one():
    """一条都没标 = 最坏情况。recall 分母为空时返回 1.0 的宽容不能传染到总分。"""
    empty = score_groups({"linear": [10.0, 20.0]}, {})

    assert empty.recall == 0.0
    assert empty.f(2.0) == 0.0
