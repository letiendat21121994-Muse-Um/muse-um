import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from math import isfinite
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


UTC = timezone.utc
PURCHASE = "offsite_conversion.fb_pixel_purchase"
CHECKOUT = "offsite_conversion.fb_pixel_initiate_checkout"
LEVELS = ("account", "campaign", "adset", "ad")
BUDGET_OFFSETS = {"USD": 100, "VND": 1}


@dataclass(frozen=True)
class Metrics:
    spend: float = 0
    impressions: float = 0
    link_clicks: float = 0
    frequency: float | None = None
    purchases: float = 0
    purchase_value: float = 0
    checkouts: float = 0

    @property
    def cpa(self):
        return self.spend / self.purchases if self.purchases else None

    @property
    def roas(self):
        return self.purchase_value / self.spend if self.spend else None

    @property
    def ctr(self):
        return self.link_clicks / self.impressions if self.impressions else None

    @property
    def cpm(self):
        return 1000 * self.spend / self.impressions if self.impressions else None

    @property
    def cpc(self):
        return self.spend / self.link_clicks if self.link_clicks else None

    @property
    def conversion_rate(self):
        return self.purchases / self.link_clicks if self.link_clicks else None


@dataclass(frozen=True)
class Entity:
    level: str
    id: str
    name: str
    today: Metrics = Metrics()
    baseline: Metrics = Metrics()
    status: str | None = None
    daily_budget: float | None = None
    started_at: datetime | None = None
    ends_at: datetime | None = None
    learning_status: str | None = None
    rejected: bool = False
    has_review_feedback: bool = False


@dataclass(frozen=True)
class Snapshot:
    name: str
    currency: str
    at: datetime
    baseline_days: int
    entities: tuple[Entity, ...]
    notes: tuple[str, ...] = ()

    @property
    def day_fraction(self):
        start = datetime.combine(self.at.date(), time.min, self.at.tzinfo).astimezone(UTC)
        end = datetime.combine(self.at.date() + timedelta(days=1), time.min, self.at.tzinfo)
        return (self.at.astimezone(UTC) - start) / (end.astimezone(UTC) - start)


class Source(Protocol):
    def snapshot(self, baseline_days: int) -> Snapshot: ...


class SourceError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SourceError("Meta trả về dữ liệu số không hợp lệ.") from None
    if not isfinite(number) or number < 0:
        raise SourceError("Meta trả về dữ liệu số không hợp lệ.")
    return number


def _action(row, key, action):
    for item in row.get(key) or []:
        if item.get("action_type") == action:
            return _number(item.get("value", 0))
    return 0


def _metrics(row):
    return Metrics(
        spend=_number(row.get("spend", 0)),
        impressions=_number(row.get("impressions", 0)),
        link_clicks=_number(row.get("inline_link_clicks", 0)),
        frequency=_number(row["frequency"]) if row.get("frequency") is not None else None,
        purchases=_action(row, "actions", PURCHASE),
        purchase_value=_action(row, "action_values", PURCHASE),
        checkouts=_action(row, "actions", CHECKOUT),
    )


class DemoSource:
    def snapshot(self, baseline_days):
        tz = ZoneInfo("America/New_York")
        at = datetime(2026, 11, 21, 17, 0, tzinfo=tz)
        launch = at - timedelta(days=14)

        def history(spend, impressions, clicks, purchases, value, checkouts):
            return Metrics(
                spend * baseline_days, impressions * baseline_days, clicks * baseline_days,
                None, purchases * baseline_days, value * baseline_days, checkouts * baseline_days,
            )

        soft = Metrics(222, 10500, 121, 4.8, 2, 118, 5)
        gentle = Metrics(33, 3150, 65, 1.8, 2, 113, 4)
        soft_base = history(140, 12500, 220, 8, 512, 17)
        gentle_base = history(99, 7200, 144, 5, 277, 11)
        return Snapshot("Lumière Hair US", "USD", at, baseline_days, (
            Entity("account", "demo-account", "Lumière Hair US",
                   Metrics(255, 13650, 186, 3.3, 4, 231, 9),
                   history(239, 19700, 364, 13, 789, 28)),
            Entity("campaign", "demo-repair", "Repair Bundle · US", soft, soft_base,
                   "ACTIVE", 240, launch),
            Entity("campaign", "demo-gentle", "Gentle Wash · US", gentle, gentle_base,
                   "ACTIVE", 160, launch),
            Entity("adset", "demo-broad", "US Broad · Repair", soft, soft_base,
                   "ACTIVE", started_at=launch, learning_status="FAIL"),
            Entity("adset", "demo-return", "Returning Shoppers · Wash", gentle, gentle_base,
                   "ACTIVE", started_at=launch),
            Entity("ad", "demo-ugc", "UGC · 7-Day Routine", Metrics(142, 7500, 57, 4.8, 0, 0, 4),
                   history(88, 8500, 151, 5, 328, 11), "ACTIVE"),
            Entity("ad", "demo-packshot", "Packshot · Before & After", status="DISAPPROVED",
                   rejected=True, has_review_feedback=True),
            Entity("ad", "demo-carousel", "Carousel · Wash Day", status="WITH_ISSUES"),
        ))


def _timestamp(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result if result.tzinfo is not None else None


class MetaSource:
    def __init__(self, token, account_id, version="v27.0", *, open_request=None, clock=None):
        if not token or not token.isascii() or any(not 33 <= ord(c) <= 126 for c in token):
            raise ValueError("Cần META_ADS_TOKEN hợp lệ trong biến môi trường.")
        if not re.fullmatch(r"(?:act_)?[0-9]+", account_id):
            raise ValueError("META_AD_ACCOUNT_ID phải là ID dạng số hoặc act_<ID>.")
        if not re.fullmatch(r"v[0-9]+\.[0-9]+", version):
            raise ValueError("META_GRAPH_VERSION phải có dạng v27.0.")
        self._token = token
        self._account = "act_" + account_id.removeprefix("act_")
        self._version = version
        self._open = open_request if open_request is not None else build_opener(_NoRedirect()).open
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("META_ADS_TOKEN", ""), os.environ.get("META_AD_ACCOUNT_ID", ""),
                   os.environ.get("META_GRAPH_VERSION", "v27.0"))

    def _request(self, method, edge="", **params):
        if method != "GET":
            raise ValueError("Meta Ads Alerts chỉ cho phép GET.")
        if edge not in {"", "insights", "campaigns", "adsets", "ads"}:
            raise ValueError("Meta endpoint không được hỗ trợ.")
        path = self._account + (f"/{edge}" if edge else "")
        url = f"https://graph.facebook.com/{self._version}/{path}?{urlencode(params)}"
        request = Request(url, method="GET", headers={
            "Authorization": f"Bearer {self._token}", "Accept": "application/json",
        })
        try:
            with self._open(request, timeout=15) as response:
                data = json.load(response)
        except HTTPError as error:
            try:
                data = json.load(error)
            except (ValueError, OSError):
                data = None
            finally:
                error.close()
            info = data.get("error") if isinstance(data, dict) else None
            code = info.get("code") if isinstance(info, dict) else None
            raise SourceError(f"Meta từ chối yêu cầu (HTTP {error.code}).", code) from None
        except (URLError, OSError, ValueError):
            raise SourceError("Không đọc được dữ liệu từ Meta. Kiểm tra kết nối và thử lại.") from None
        if not isinstance(data, dict) or "error" in data:
            raise SourceError("Meta không trả về dữ liệu hợp lệ.")
        return data

    def _rows(self, edge, **params):
        after = None
        for _ in range(100):
            data = self._request("GET", edge, limit=100, **params,
                                 **({"after": after} if after else {}))
            if not isinstance(data.get("data"), list):
                raise SourceError("Meta không trả về danh sách dữ liệu.")
            yield from data["data"]
            paging = data.get("paging") or {}
            if not paging.get("next"):
                return
            cursor = (paging.get("cursors") or {}).get("after")
            if not isinstance(cursor, str) or not cursor or cursor == after:
                break
            after = cursor
        raise SourceError("Không tải đủ các trang dữ liệu Meta.")

    def _insights(self, level, since, until):
        id_field = f"{level}_id"
        rows = self._rows(
            "insights", level=level,
            time_range=json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
            fields=f"{id_field},spend,impressions,inline_link_clicks,frequency,actions,action_values",
            action_report_time="conversion", action_attribution_windows='["7d_click","1d_view"]',
        )
        return {str(row[id_field]): _metrics(row) for row in rows}

    def snapshot(self, baseline_days):
        account = self._request("GET", fields="id,name,currency,timezone_name")
        try:
            tz = ZoneInfo(account["timezone_name"])
            currency = account["currency"]
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
            raise SourceError("Không xác định được múi giờ hoặc tiền tệ của ad account.") from None
        at = self._clock().astimezone(tz)
        day = at.date()
        first = day - timedelta(days=baseline_days)
        yesterday = day - timedelta(days=1)
        offset = BUDGET_OFFSETS.get(currency)
        notes = []
        if not offset:
            notes.append("Chưa hỗ trợ budget pacing cho tiền tệ này; các cảnh báo khác vẫn hoạt động.")

        campaign_fields = "id,name,effective_status,daily_budget,lifetime_budget,start_time,stop_time"
        adset_fields = "id,name,campaign_id,effective_status,daily_budget,start_time,end_time"
        campaigns = list(self._rows("campaigns", fields=campaign_fields))
        try:
            adsets = list(self._rows("adsets", fields=adset_fields + ",learning_stage_info"))
        except SourceError as error:
            if error.code not in {10, 100, 200}:
                raise
            adsets = list(self._rows("adsets", fields=adset_fields))
            notes.append("Không đọc được Learning Limited cho ad account này.")
        if not any(row.get("learning_stage_info") for row in adsets) and adsets and not any(
            "Learning Limited" in note for note in notes
        ):
            notes.append("Meta không trả trạng thái learning cho các ad set.")
        ads_fields = "id,name,effective_status"
        try:
            ads = list(self._rows("ads", fields=ads_fields + ",ad_review_feedback"))
        except SourceError as error:
            if error.code not in {10, 100, 200}:
                raise
            ads = list(self._rows("ads", fields=ads_fields))
            notes.append("Meta không trả review feedback; trạng thái disapproved vẫn được kiểm tra.")

        campaign_budgets = {
            str(row["id"]) for row in campaigns
            if _number(row.get("daily_budget") or 0) or _number(row.get("lifetime_budget") or 0)
        }
        entities = []
        for level, rows in zip(LEVELS, ([account], campaigns, adsets, ads), strict=True):
            today = self._insights(level, day, day)
            baseline = self._insights(level, first, yesterday)
            for row in rows:
                raw_id = str(row["id"])
                entity_id = raw_id.removeprefix("act_") if level == "account" else raw_id
                current = today.get(entity_id, Metrics())
                status = row.get("effective_status")
                monitored = {"ACTIVE", "DISAPPROVED", "PENDING_BILLING_INFO", "WITH_ISSUES"}
                if level != "account" and status not in monitored and not current.spend:
                    continue
                budget = None
                if offset and level in {"campaign", "adset"}:
                    if level != "adset" or str(row.get("campaign_id")) not in campaign_budgets:
                        budget = _number(row.get("daily_budget") or 0) / offset or None
                entities.append(Entity(
                    level, entity_id, row.get("name") or entity_id, current,
                    baseline.get(entity_id, Metrics()), status, budget, _timestamp(row.get("start_time")),
                    _timestamp(row.get("stop_time") or row.get("end_time")),
                    (row.get("learning_stage_info") or {}).get("status"),
                    status == "DISAPPROVED",
                    bool(row.get("ad_review_feedback")),
                ))
        return Snapshot(account.get("name") or self._account, currency, at, baseline_days,
                        tuple(entities), tuple(notes))
