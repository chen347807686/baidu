"""关键词工作台的只读规则预览接口。

本步不写入现成数据库，也不执行否词动作；接口只在已认证且可见账户范围内，
用请求携带的规则快照返回命中原因。正式规则配置接入现有配置来源前，不把预览
结果当作持久标注或百度操作结果。
"""

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, Engine

from dashboard.access import visible_account_ids
from dashboard.auth import AuthenticationError, authenticate
from dashboard.auth_constants import COOKIE
from dashboard.keyword_negative_rules import NegativeRuleProfile, judge_negative


class NegativePreviewInput(BaseModel):
    """单词规则预览输入；规则只用于本次请求，不会写入数据库。"""

    model_config = ConfigDict(strict=True, extra="forbid")

    account_id: int
    word: str = Field(min_length=1, max_length=65535)
    custom_negatives: list[str] = Field(default_factory=list, max_length=500)
    categories: dict[str, list[str]] = Field(default_factory=dict, max_length=100)
    competitor_brands: list[str] = Field(default_factory=list, max_length=500)
    non_target_regions: list[str] = Field(default_factory=list, max_length=500)
    intent_words: list[str] = Field(default_factory=list, max_length=500)


class NegativePreviewOutput(BaseModel):
    """规则预览结果，明确区分命中建议和未命中。"""

    model_config = ConfigDict(strict=True, extra="forbid")

    account_id: int
    word: str
    matched: bool
    category: str
    reason: str
    source: str
    persistent: bool = False


def preview_negative(connection: Connection, user_id: int, body: NegativePreviewInput) -> NegativePreviewOutput:
    """校验账户可见范围后执行纯规则判断。"""
    visible = visible_account_ids(user_id)
    allowed = connection.execute(visible.where(visible.selected_columns.id == body.account_id)).first()
    if allowed is None:
        raise PermissionError("账户不可见")
    profile = NegativeRuleProfile.from_mapping(
        body.categories,
        competitor_brands=body.competitor_brands,
        non_target_regions=body.non_target_regions,
        intent_words=body.intent_words,
    )
    result = judge_negative(body.word, profile, body.custom_negatives)
    return NegativePreviewOutput(
        account_id=body.account_id,
        word=body.word,
        matched=result.matched,
        category=result.category,
        reason=result.reason,
        source=result.source,
    )


def register_keyword_actions(app: FastAPI, engine: Engine) -> None:
    """注册只读预览路由；不创建表、不执行迁移、不写入外部平台。"""

    @app.post("/api/v1/keywords/negative-preview", response_model=NegativePreviewOutput)
    def negative_preview(
        body: NegativePreviewInput, request: Request, response: Response
    ) -> NegativePreviewOutput:
        try:
            with engine.begin() as connection:
                user_id = authenticate(connection, request.cookies.get(COOKIE, ""))
                result = preview_negative(connection, user_id, body)
        except AuthenticationError:
            raise HTTPException(401, "请先登录") from None
        except PermissionError:
            raise HTTPException(403, "账户不可见") from None
        response.headers["Cache-Control"] = "no-store"
        return result
