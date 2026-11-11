"""关键词质量判定的无数据库兼容层。

本模块只按调用方传入的快照计算质量状态，不读取或修改现成数据库。
质量判断与否词规则分开；人工质量覆盖优先于自动结果，金额使用 Decimal 精确比较。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

Quality = Literal["pending", "ordinary", "high"]


@dataclass(frozen=True)
class QualityInput:
    """一次质量判断所需的已授权数据快照。"""

    clicks: int | None
    leads: int | None
    cost: Decimal | str | int | None
    data_state: Literal["complete", "partial", "unknown"]
    rule_passed: bool | None
    negative_confirmed: bool = False
    manual_quality: Quality | None = None


@dataclass(frozen=True)
class QualityResult:
    """质量最终值、自动值和可审计原因。"""

    value: Quality
    automatic: Quality
    reason: str


def judge_quality(
    item: QualityInput,
    *,
    min_clicks: int | None,
    min_leads: int | None,
    target_lead_cost: Decimal | str | int | None,
) -> QualityResult:
    """根据规则快照计算质量；配置或数据不足时返回 pending。"""

    def decimal(value: Decimal | str | int | None) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return result if result.is_finite() and result >= 0 else None

    automatic: Quality = "pending"
    reason = "数据或配置不足"
    cost = decimal(item.cost)
    valid_metrics = (
        item.clicks is not None
        and item.leads is not None
        and item.clicks >= 0
        and item.leads >= 0
        and cost is not None
    )
    if item.data_state != "complete":
        reason = "数据窗口不完整"
    elif item.negative_confirmed:
        reason = "已确认否词，需人工复核"
    elif item.rule_passed is not True:
        reason = "否词规则未通过或结果待复核"
    elif not valid_metrics or min_clicks is None or min_leads is None or target_lead_cost is None:
        reason = "质量判断参数或指标缺失"
    else:
        target = decimal(target_lead_cost)
        if target is None or min_clicks < 0 or min_leads < 0:
            reason = "质量判断参数无效"
        elif item.clicks < min_clicks:
            reason = "点击样本不足"
        elif item.leads < min_leads:
            automatic = "ordinary"
            reason = "综合线索不足"
        elif item.leads == 0:
            automatic = "ordinary"
            reason = "无综合线索，成本未知"
        elif cost <= target * item.leads:
            automatic = "high"
            reason = "达到点击、线索和成本阈值"
        else:
            automatic = "ordinary"
            reason = "线索成本超过目标"
    return QualityResult(item.manual_quality or automatic, automatic, "人工覆盖：" + reason if item.manual_quality else reason)
