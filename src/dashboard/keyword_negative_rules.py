"""关键词否词规则判断。

规则只判断是否命中否词及命中原因，不引入相关/不相关/边界分类，也不修改数据库。
调用方应在账户权限范围内提供规则配置；本模块不读取凭据、网络或运行库。
自定义否词优先，其次是账户分类否词、竞品品牌和非目标区域。品牌词带有明确
业务意图时保留待复核，避免把真实业务词自动判成否词。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class NegativeRuleProfile:
    """单个账户的规则快照；元组化保证一次判断期间配置不可变。"""

    categories: tuple[tuple[str, tuple[str, ...]], ...] = ()
    competitor_brands: tuple[str, ...] = ()
    non_target_regions: tuple[str, ...] = ()
    intent_words: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls,
        categories: Mapping[str, Iterable[str]] | None = None,
        *,
        competitor_brands: Iterable[str] = (),
        non_target_regions: Iterable[str] = (),
        intent_words: Iterable[str] = (),
    ) -> NegativeRuleProfile:
        """把外部配置规范化为去空白、去空值且顺序稳定的规则快照。"""

        normalized = []
        for name, values in (categories or {}).items():
            patterns = tuple(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))
            if patterns:
                normalized.append((str(name).strip(), patterns))
        return cls(
            categories=tuple(normalized),
            competitor_brands=tuple(dict.fromkeys(str(v).strip() for v in competitor_brands if str(v).strip())),
            non_target_regions=tuple(dict.fromkeys(str(v).strip() for v in non_target_regions if str(v).strip())),
            intent_words=tuple(dict.fromkeys(str(v).strip() for v in intent_words if str(v).strip())),
        )


@dataclass(frozen=True)
class NegativeRuleResult:
    """规则输出；未命中时 matched=False，调用方可保留 unknown 状态。"""

    matched: bool
    category: str = ""
    reason: str = ""
    source: str = "rule"


def judge_negative(
    word: str,
    profile: NegativeRuleProfile,
    custom_negatives: Iterable[str] = (),
) -> NegativeRuleResult:
    """按固定优先级判断否词；只返回建议，不执行任何外部写入。"""

    text = str(word or "").strip()
    if not text:
        return NegativeRuleResult(False, reason="原词为空")
    for negative in custom_negatives:
        value = str(negative).strip()
        if value and value in text:
            return NegativeRuleResult(True, "custom", f"命中自定义否词「{value}」")
    for category, patterns in profile.categories:
        for pattern in patterns:
            if pattern in text:
                return NegativeRuleResult(True, category, f"命中否词「{pattern}」")
    for brand in profile.competitor_brands:
        if brand in text:
            if any(intent and intent in text for intent in profile.intent_words):
                return NegativeRuleResult(False, reason=f"品牌词「{brand}」含业务意图，待复核")
            return NegativeRuleResult(True, "competitor_brand", f"命中竞品品牌「{brand}」")
    for region in profile.non_target_regions:
        if region in text:
            return NegativeRuleResult(True, "non_target_region", f"命中非目标区域「{region}」")
    return NegativeRuleResult(False)
