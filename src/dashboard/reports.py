"""正式与合成关键词报表的统一只读查询入口。

账户权限在 SQL 聚合之前过滤，按词文本跨账户与日期合计，再稳定排序分页。
列表、总数、汇总在调用方同一事务快照读取。来源白名单选择正式日报或合成表，
不接旧库或百度；人员/分类/计划细节与真实日粒度后续按正式模型补充。
"""

from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from sqlalchemy import (
    Connection,
    Engine,
    func,
    select,
)
from sqlalchemy.sql.elements import ColumnElement

from dashboard.access import visible_account_ids
from dashboard.auth import AuthenticationError, authenticate
from dashboard.auth_constants import COOKIE
from dashboard.http_models import KeywordReportResponse

# 查询仅依赖共享 schema，未知转化保留原值，不加入综合线索。
from dashboard.schema import REPORT_COUNTS as COUNTS
from dashboard.schema import keyword_daily as formal
from dashboard.schema import samples


def metrics(raw: dict[str, Any]) -> dict[str, Any]:
    """合计后计算派生指标；十进制字符串传输，零分母返回空值。"""
    row = {name: int(raw.get(name) or 0) for name in COUNTS}
    row.update(
        {name: int(raw[name] or 0) for name in ("conv_78", "conv_92") if name in raw}
    )
    result: dict[str, Any] = dict(raw, **row)
    cost = Decimal(raw.get("cost") or 0)
    lead = sum(
        row[name] for name in ("conv_form", "conv_lead", "conv_dial", "conv_wechat")
    )
    consult = row["conv_1sent"] + row["conv_3sent"]

    def ratio(value: Decimal | int, denominator: int, digits: int) -> str | None:
        """按当前合成契约采用偶数舍入，真实金额导入前仍须核实口径。"""
        return (
            str(
                (Decimal(value) / denominator).quantize(
                    Decimal(1).scaleb(-digits), rounding=ROUND_HALF_EVEN
                )
            )
            if denominator
            else None
        )

    result.update(
        cost=str(cost.quantize(Decimal("0.0001"))),
        lead_total=lead,
        consult=consult,
        lead_cost=ratio(cost, lead, 2),
        consult_rate=ratio(consult, row["click"], 4),
        lead_rate=ratio(lead, row["click"], 4),
        cpc=ratio(cost, row["click"], 4),
    )
    return result


def keyword_report(
    conn: Connection,
    user: int,
    start: date,
    end: date,
    account: int | None,
    search: str,
    sort: str,
    order: str,
    page: int,
    page_size: int,
    source: str = "synthetic",
) -> dict[str, Any]:
    """在数据库执行过滤、总数和分页，明确请求无权账户时拒绝访问。"""
    if (
        start > end
        or page < 1
        or not 1 <= page_size <= 200
        or sort not in {"cost", "click", "keyword"}
        or order not in {"asc", "desc"}
        or source not in {"formal", "synthetic"}
    ):
        raise ValueError("报表参数无效")
    daily = formal if source == "formal" else samples
    day = daily.c.date if source == "formal" else samples.c.day
    # 显式选择单列供 IN 使用，不改变既有双列权限查询的公共契约。
    allowed = visible_account_ids(user).subquery()
    scope = select(allowed.c.id)
    if (
        account is not None
        and conn.execute(scope.where(allowed.c.id == account)).first() is None
    ):
        raise PermissionError("账户不可见")
    conditions: list[ColumnElement[bool]] = [
        daily.c.account_id.in_(scope),
        day.between(start, end),
    ]
    if account is not None:
        conditions.append(daily.c.account_id == account)
    if search.strip():
        # 自动转义 LIKE 通配符，百分号和下划线按普通词文本处理。
        conditions.append(daily.c.keyword.contains(search.strip(), autoescape=True))
    totals = [
        func.coalesce(func.sum(daily.c[name]), 0).label(name)
        for name in (
            "cost",
            *COUNTS,
            *(("conv_78", "conv_92") if source == "formal" else ()),
        )
    ]
    grouped = (
        select(daily.c.keyword, *totals)
        .where(*conditions)
        .group_by(daily.c.keyword)
        .subquery()
    )
    total = int(conn.execute(select(func.count()).select_from(grouped)).scalar_one())
    summary = metrics(
        dict(conn.execute(select(*totals).where(*conditions)).mappings().one())
    )
    sort_column = grouped.c[sort]
    query = (
        select(grouped)
        .order_by(
            sort_column.asc() if order == "asc" else sort_column.desc(),
            grouped.c.keyword.asc(),
        )
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = [metrics(dict(row)) for row in conn.execute(query).mappings()]
    return {
        "rows": rows,
        "total": total,
        "summary": summary,
        "page": page,
        "page_size": page_size,
        "data_source": source,
        "applied_filters": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "account": account,
            "search": search.strip(),
            "sort": sort,
            "order": order,
            "source": source,
        },
    }


def register_reports(app: FastAPI, engine: Engine) -> None:
    """挂载需登录的报表读取接口，使用独立请求事务。"""
    # 固定请求内的快照隔离级别，避免依赖数据库服务器默认配置。
    report_engine = engine.execution_options(isolation_level="REPEATABLE READ")

    @app.get(
        "/api/v1/reports/keywords",
        response_model=KeywordReportResponse,
        response_model_exclude_unset=True,
    )
    def report(
        request: Request,
        response: Response,
        start: date | None = None,
        end: date | None = None,
        account: int | None = None,
        search: str = Query(default="", max_length=255),
        sort: Literal["cost", "click", "keyword"] = "cost",
        order: Literal["asc", "desc"] = "desc",
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        source: Literal["formal", "synthetic"] = "formal",
    ) -> KeywordReportResponse:
        """返回所选来源；默认昨天结束七天，日期只填一端或倒置时拒绝。"""
        if (start is None) != (end is None):
            raise HTTPException(422, "请同时填写起止日期")
        if start is None or end is None:
            end = datetime.now(timezone(timedelta(hours=8))).date() - timedelta(days=1)
            start = end - timedelta(days=6)
        try:
            with report_engine.begin() as conn:
                user = authenticate(conn, request.cookies.get(COOKIE, ""))
                result = keyword_report(
                    conn,
                    user,
                    start,
                    end,
                    account,
                    search,
                    sort,
                    order,
                    page,
                    page_size,
                    source,
                )
        except AuthenticationError:
            raise HTTPException(401, "请先登录") from None
        except PermissionError:
            raise HTTPException(403, "账户不可见") from None
        except ValueError:
            raise HTTPException(422, "报表参数无效") from None
        response.headers["Cache-Control"] = "no-store"
        return KeywordReportResponse.model_validate(result)
