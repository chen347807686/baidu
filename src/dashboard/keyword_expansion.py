"""大模型拓词的本地契约与模拟实现。

输入只接受已选中的原词快照，输出保留原词引用和规则判断结果。模拟器用于页面和
接口联调，不代表真实模型能力，也不读取凭据；后续接入提供方时复用这些结构。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from dashboard.keyword_negative_rules import NegativeRuleProfile, judge_negative


@dataclass(frozen=True)
class ExpansionSeed:
    """服务端确认的拓词种子。"""

    seed_id: str
    account_id: int
    keyword: str
    quality: str
    negative_status: str
    snapshot_version: int = 1


@dataclass(frozen=True)
class ExpansionCandidate:
    """拓词候选，始终带来源原词和否词建议。"""

    candidate_id: str
    seed_id: str
    account_id: int
    keyword: str
    source_keyword: str
    negative_matched: bool
    negative_category: str
    negative_reason: str
    status: str = "pending"


def validate_seed(seed: ExpansionSeed) -> None:
    """校验种子资格，避免把普通词或已确认否词送入模型。"""

    if seed.quality != "high":
        raise ValueError("只有优质词可以拓词")
    if seed.negative_status != "clear":
        raise ValueError("否词状态未确认，不能拓词")
    if not seed.keyword.strip() or seed.snapshot_version < 1:
        raise ValueError("拓词种子快照无效")


def simulate_expansion(
    seed: ExpansionSeed,
    *,
    profile: NegativeRuleProfile,
    custom_negatives: tuple[str, ...] = (),
) -> list[ExpansionCandidate]:
    """生成两个确定性模拟候选，供接口和前端联调。"""

    validate_seed(seed)
    variants = (f"{seed.keyword} 价格", f"{seed.keyword} 上门")
    output: list[ExpansionCandidate] = []
    for keyword in variants:
        negative = judge_negative(keyword, profile, custom_negatives)
        output.append(
            ExpansionCandidate(
                candidate_id=uuid4().hex,
                seed_id=seed.seed_id,
                account_id=seed.account_id,
                keyword=keyword,
                source_keyword=seed.keyword,
                negative_matched=negative.matched,
                negative_category=negative.category,
                negative_reason=negative.reason,
            )
        )
    return output
