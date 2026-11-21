import json
from dataclasses import dataclass, fields
from math import isfinite

from .sources import Snapshot


@dataclass(frozen=True)
class Thresholds:
    baseline_days: int = 7
    min_spend: float = 30
    min_purchases: float = 2
    min_impressions: float = 1000
    cpa_rise: float = .40
    roas_drop: float = .35
    ctr_drop: float = .25
    cpm_rise: float = .40
    cpc_rise: float = .40
    conversion_rate_drop: float = .40
    max_frequency: float = 3.5
    min_day_fraction: float = .35
    pace_fast: float = 1.25
    pace_slow: float = .60
    signal_drop: float = .50
    min_expected_events: float = 4
    zero_purchase_spend: float = 60


@dataclass(frozen=True)
class Config:
    poll_seconds: int = 3600
    thresholds: Thresholds = Thresholds()


@dataclass(frozen=True)
class Alert:
    code: str
    category: str
    severity: str
    level: str
    entity_id: str
    entity_name: str
    title: str
    message: str
    suggestion: str

    @property
    def key(self):
        return self.code, self.level, self.entity_id


def load_config(path):
    if path is None:
        return Config()
    try:
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, ValueError):
        raise ValueError("Không đọc được file cấu hình JSON.") from None
    if not isinstance(raw, dict) or set(raw) - {"poll_seconds", "thresholds"}:
        raise ValueError("Config chỉ hỗ trợ poll_seconds và thresholds.")
    poll = raw.get("poll_seconds", 3600)
    if type(poll) is not int or poll < 60:
        raise ValueError("poll_seconds phải là số nguyên từ 60 trở lên.")
    values = raw.get("thresholds", {})
    if not isinstance(values, dict) or set(values) - {field.name for field in fields(Thresholds)}:
        raise ValueError("Có threshold không hợp lệ trong config.")
    thresholds = Thresholds(**values)
    for field in fields(thresholds):
        value = getattr(thresholds, field.name)
        if type(value) not in {int, float} or not isfinite(value) or value <= 0:
            raise ValueError(f"Threshold {field.name} phải là số dương.")
    if type(thresholds.baseline_days) is not int or thresholds.baseline_days > 30:
        raise ValueError("baseline_days phải là số nguyên từ 1 đến 30.")
    fractions = (thresholds.roas_drop, thresholds.ctr_drop, thresholds.conversion_rate_drop,
                 thresholds.min_day_fraction, thresholds.pace_slow, thresholds.signal_drop)
    if not all(0 < fraction < 1 for fraction in fractions) or thresholds.pace_fast <= 1:
        raise ValueError("Ngưỡng giảm phải nằm giữa 0 và 1; pace_fast phải lớn hơn 1.")
    return Config(poll, thresholds)


def _money(amount, currency):
    return f"{amount:,.2f} {currency}"


def evaluate(snapshot: Snapshot, t: Thresholds) -> tuple[Alert, ...]:
    alerts = []
    fraction = snapshot.day_fraction
    currency = snapshot.currency
    for entity in snapshot.entities:
        current, previous = entity.today, entity.baseline

        def add(code, category, severity, title, message, suggestion):
            alerts.append(Alert(code, category, severity, entity.level, entity.id, entity.name,
                                title, message, suggestion))

        def drift(code, title, a, b, threshold, formatter, suggestion, *, drop=False):
            if a is None or b is None or b <= 0:
                return
            change = a / b - 1
            if (-change if drop else change) >= threshold:
                add(code, "performance", "warning", title,
                    f"Hôm nay {formatter(a)}; baseline {formatter(b)} ({change:+.0%}).",
                    suggestion)

        money = lambda amount: _money(amount, currency)
        percent = lambda value: f"{value:.2%}"
        if current.spend >= t.min_spend and previous.spend >= t.min_spend:
            if min(current.purchases, previous.purchases) >= t.min_purchases:
                drift("cpa_spike", "CPA tăng", current.cpa, previous.cpa, t.cpa_rise, money,
                      "So sánh offer, creative và placement; kiểm tra Purchase trong Events Manager.")
                drift("roas_drop", "ROAS giảm", current.roas, previous.roas, t.roas_drop,
                      lambda x: f"{x:.2f}×", "Kiểm tra giá trị Purchase, margin và độ trễ attribution.",
                      drop=True)
            if min(current.impressions, previous.impressions) >= t.min_impressions:
                drift("ctr_drop", "CTR giảm", current.ctr, previous.ctr, t.ctr_drop, percent,
                      "Kiểm tra hook, primary text và tỷ lệ hiển thị theo placement.", drop=True)
                drift("cpm_rise", "CPM tăng", current.cpm, previous.cpm, t.cpm_rise, money,
                      "Kiểm tra auction và placement; tránh thay nhiều biến cùng lúc.")
                drift("cpc_rise", "CPC tăng", current.cpc, previous.cpc, t.cpc_rise, money,
                      "So sánh CPC với CTR và CPM trước khi chỉnh creative.")
                if min(current.purchases, previous.purchases) >= t.min_purchases:
                    drift("conversion_rate_drop", "Conversion rate giảm",
                          current.conversion_rate, previous.conversion_rate,
                          t.conversion_rate_drop, percent,
                          "Kiểm tra landing page, inventory và checkout trên thiết bị thật.",
                          drop=True)
        if current.impressions >= t.min_impressions and (current.frequency or 0) >= t.max_frequency:
            add("fatigue", "performance", "warning", "Frequency cao",
                f"Frequency hôm nay {current.frequency:.1f}×; có thể là dấu hiệu creative fatigue.",
                "Đối chiếu với CTR và CPA trước khi thử hook hoặc visual mới.")

        start = snapshot.at.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
        if (entity.daily_budget and entity.status == "ACTIVE" and fraction >= t.min_day_fraction
                and (entity.started_at is None or entity.started_at <= start)
                and (entity.ends_at is None or entity.ends_at >= end)):
            expected = entity.daily_budget * fraction
            ratio = current.spend / expected
            text = f"Đã chi {money(current.spend)}; mức tham chiếu đến lúc này {money(expected)}."
            if ratio >= t.pace_fast:
                add("pace_fast", "spend", "warning", "Chi tiêu nhanh hơn dự kiến", text,
                    "Xem lại budget, thời điểm delivery và thay đổi gần đây trong Ads Manager.")
            elif ratio <= t.pace_slow:
                add("pace_slow", "spend", "warning", "Chi tiêu chậm hơn dự kiến", text,
                    "Kiểm tra delivery, giới hạn bid và đối tượng; không tăng budget chỉ vì cảnh báo này.")

        if entity.learning_status == "FAIL" and entity.status == "ACTIVE":
            add("learning_limited", "delivery", "warning", "Learning Limited",
                "Ad set chưa đủ kết quả để thoát learning.",
                "Xem lịch sử chỉnh sửa và số Purchase trong Ads Manager.")
        if entity.rejected:
            feedback = " Meta có trả review feedback." if entity.has_review_feedback else ""
            add("disapproved", "delivery", "critical", "Quảng cáo bị từ chối",
                "Meta trả effective_status = DISAPPROVED." + feedback,
                "Mở Ads Manager để xem review feedback và đối chiếu Advertising Standards.")
        elif entity.status in {"PENDING_BILLING_INFO", "WITH_ISSUES"}:
            add("delivery_issue", "delivery", "warning", "Delivery cần kiểm tra",
                f"Meta trả effective_status = {entity.status}.",
                "Mở Ads Manager để xem lỗi delivery hoặc billing; app không đổi cài đặt.")

        if fraction < t.min_day_fraction or current.spend < t.min_spend:
            continue
        if current.purchases == 0 and current.spend >= t.zero_purchase_spend:
            add("zero_purchase", "tracking", "critical", "Có chi tiêu nhưng chưa có Purchase",
                f"Hôm nay đã chi {money(current.spend)} nhưng Meta chưa ghi nhận Purchase được quy gán.",
                "Đối chiếu số đơn và độ trễ attribution; kiểm tra Pixel / Conversions API.")
        if entity.level == "account":
            for field, label in (("purchases", "Purchase"), ("checkouts", "Checkout")):
                expected = getattr(previous, field) / snapshot.baseline_days * fraction
                actual = getattr(current, field)
                if expected >= t.min_expected_events and actual <= expected * (1 - t.signal_drop):
                    add(f"{field}_drop", "tracking", "warning", f"Tín hiệu {label} giảm",
                        f"Meta ghi nhận {actual:g} {label} được quy gán; tham chiếu {expected:.1f}.",
                        "So sánh số liệu tổng hợp của cửa hàng và xem Test Events; chưa thể kết luận lỗi tracking.")
    return tuple(sorted(alerts, key=lambda item: (item.severity != "critical", item.category,
                                                   item.entity_name, item.code)))
