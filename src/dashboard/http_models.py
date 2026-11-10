"""认证与关键词报表的公开 HTTP 响应契约。

字段显式建模，金额和比率保持十进制字符串，零分母保留 JSON null。模型不
包含密码、令牌或数据库连接信息。正式来源保留未知转化，合成来源不伪造
不存在的转化字段；路由序列化必须 exclude_unset，不能把合法 null 一并删除。
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Decimal4 = Annotated[str, Field(pattern=r"^-?\d+\.\d{4}$")]
Decimal2 = Annotated[str, Field(pattern=r"^-?\d+\.\d{2}$")]
ReportSource = Literal["formal", "synthetic"]


class PublicResponse(BaseModel):
    """响应拒绝额外字段和隐式类型转换，错误文字不展示原始输入内容。"""

    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)


class LoginResponse(PublicResponse):
    """登录成功状态，令牌仅通过 HttpOnly Cookie 传递。"""

    status: Literal["ok"] = "ok"


class VisibleAccount(PublicResponse):
    """对当前身份可见的账户最小展示信息。"""

    id: int
    name: str


class CurrentUserResponse(PublicResponse):
    """服务端认证身份及当前可见账户，不返回内部凭据或授权表。"""

    user_id: int
    accounts: list[VisibleAccount]


class KeywordMetrics(PublicResponse):
    """聚合原始量与派生指标；未知转化正式来源必有，合成来源不提供。"""

    impression: int
    click: int
    conv_form: int
    conv_lead: int
    conv_dial: int
    conv_wechat: int
    conv_1sent: int
    conv_3sent: int
    conv_78: int | None = Field(
        default=None, description="正式来源返回整数；合成来源省略"
    )
    conv_92: int | None = Field(
        default=None, description="正式来源返回整数；合成来源省略"
    )
    cost: Decimal4
    lead_total: int
    consult: int
    lead_cost: Decimal2 | None
    consult_rate: Decimal4 | None
    lead_rate: Decimal4 | None
    cpc: Decimal4 | None


class KeywordReportRow(KeywordMetrics):
    """按关键词文本合并的当前页记录。"""

    keyword: str


class KeywordDailyRow(KeywordMetrics):
    """正式日报按日聚合结果，未知转化保留原值且不加入综合线索。"""

    date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    conv_78: int
    conv_92: int


class KeywordDailyResponse(PublicResponse):
    """精确关键词的每日表现，仅含正式数据与有记录日期，不暗示数据完整。"""

    keyword: str
    start: str
    end: str
    account: int | None
    data_source: Literal["formal"] = "formal"
    missing_dates: Literal["omitted"] = "omitted"
    rows: Annotated[list[KeywordDailyRow], Field(max_length=366)]


class ReportFilters(PublicResponse):
    """实际应用的筛选值，日期以 ISO 字符串传递。"""

    start: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    end: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    account: int | None
    search: str
    sort: Literal["cost", "click", "keyword"]
    order: Literal["asc", "desc"]
    source: ReportSource


class KeywordReportResponse(PublicResponse):
    """报表完整结果，汇总覆盖全筛选范围，列表仅含当前页。"""

    rows: list[KeywordReportRow]
    total: Annotated[int, Field(ge=0)]
    summary: KeywordMetrics
    page: Annotated[int, Field(ge=1)]
    page_size: Annotated[int, Field(ge=1, le=200)]
    data_source: ReportSource
    applied_filters: ReportFilters

    @model_validator(mode="after")
    def consistent_source(self) -> Self:
        """约束来源与原字段是否存在，避免模型默认值伪造未知转化。"""
        if self.data_source != self.applied_filters.source:
            raise ValueError("响应来源与筛选不一致")
        if len(self.rows) > self.page_size or len(self.rows) > self.total:
            raise ValueError("响应分页与记录数量不一致")
        for item in [self.summary, *self.rows]:
            if self.data_source == "formal":
                if item.conv_78 is None or item.conv_92 is None:
                    raise ValueError("正式来源缺少原转化字段")
            elif {"conv_78", "conv_92"} & item.model_fields_set:
                raise ValueError("合成来源不能提供不存在的转化字段")
        return self
