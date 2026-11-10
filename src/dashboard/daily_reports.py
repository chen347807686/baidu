"""正式关键词日报的每日表现只读查询。

按完整词文本精确匹配，在账户权限过滤后按日期合计；复用报表指标口径。
最多查询连续 366 天，仅返回有记录的日期，不将缺失日报解释成零消耗。
不连接旧库或百度，不改变账户映射，不提供写入和跨来源混合查询。
"""

from datetime import date

from fastapi import FastAPI, HTTPException, Query, Request, Response
from sqlalchemy import Connection, Engine, func, select

from dashboard.access import visible_account_ids
from dashboard.auth import AuthenticationError, authenticate
from dashboard.auth_constants import COOKIE
from dashboard.http_models import KeywordDailyResponse, KeywordDailyRow
from dashboard.reports import metrics
from dashboard.schema import KEYWORD_COUNTS, keyword_daily


def keyword_daily_report(
    conn: Connection,
    user: int,
    start: date,
    end: date,
    keyword: str,
    account: int | None = None,
) -> KeywordDailyResponse:
    """在调用方快照内查询精确关键词，拒绝无权账户和无界日期范围。"""
    if (
        start > end
        or (end - start).days >= 366
        or not keyword
        or len(keyword.encode("utf-8")) > 65535
    ):
        raise ValueError("每日表现查询参数无效")
    scope = visible_account_ids(user)
    if (
        account is not None
        and conn.execute(scope.where(scope.selected_columns.id == account)).first()
        is None
    ):
        raise PermissionError("账户不可见")
    conditions = [
        keyword_daily.c.account_id.in_(scope),
        keyword_daily.c.date.between(start, end),
        # 正式词文本使用二进制 collation，保留大小写、重音和首尾空格语义。
        keyword_daily.c.keyword == keyword,
    ]
    if account is not None:
        conditions.append(keyword_daily.c.account_id == account)
    query = (
        select(
            keyword_daily.c.date,
            *[
                func.sum(keyword_daily.c[name]).label(name)
                for name in ("cost", *KEYWORD_COUNTS)
            ],
        )
        .where(*conditions)
        .group_by(keyword_daily.c.date)
        .order_by(keyword_daily.c.date.asc())
    )
    rows = []
    for record in conn.execute(query).mappings():
        raw = dict(record)
        day = raw.pop("date")
        rows.append(
            KeywordDailyRow.model_validate(dict(metrics(raw), date=day.isoformat()))
        )
    return KeywordDailyResponse(
        keyword=keyword,
        start=start.isoformat(),
        end=end.isoformat(),
        account=account,
        rows=rows,
    )


def register_daily_reports(app: FastAPI, engine: Engine) -> None:
    """注册需认证的正式日报下钻接口，权限与数据使用同一事务快照。"""
    report_engine = engine.execution_options(isolation_level="REPEATABLE READ")

    @app.get("/api/v1/reports/keywords/daily", response_model=KeywordDailyResponse)
    def daily(
        request: Request,
        response: Response,
        start: date,
        end: date,
        keyword: str = Query(min_length=1, max_length=65535),
        account: int | None = None,
    ) -> KeywordDailyResponse:
        """返回最多 366 天的有记录日期；无记录返回空列表，缺失日期不补零。"""
        try:
            with report_engine.begin() as conn:
                user = authenticate(conn, request.cookies.get(COOKIE, ""))
                result = keyword_daily_report(conn, user, start, end, keyword, account)
        except AuthenticationError:
            raise HTTPException(401, "请先登录") from None
        except PermissionError:
            raise HTTPException(403, "账户不可见") from None
        except ValueError:
            raise HTTPException(
                422, "请提供有效关键词及不超过 366 天的日期范围"
            ) from None
        response.headers["Cache-Control"] = "no-store"
        return result
