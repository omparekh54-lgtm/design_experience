from datetime import datetime, timedelta, timezone
from decimal import Decimal

from packages.data_layer.features import compute_features
from packages.data_layer.models import Candle


def make_candles(count: int = 260):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(count):
        close = Decimal(str(100 + i * 0.5))
        rows.append(
            Candle(
                instrument_id="NSE:ABC",
                timestamp=start + timedelta(days=i),
                open=close - Decimal("0.5"),
                high=close + Decimal("2"),
                low=close - Decimal("2"),
                close=close,
                volume=100000 + i,
                source="test",
            )
        )
    return rows


def test_feature_frame_contains_pms_indicators():
    frame = compute_features(make_candles()).data
    latest = frame.iloc[-1]
    for field in (
        "ema9",
        "ema21",
        "dma50",
        "dma200",
        "atr14",
        "adr20_pct",
        "avg_volume20",
        "avg_volume50",
        "worst_volume20",
        "avg_turnover20",
        "high_52w",
    ):
        assert latest[field] == latest[field]  # not NaN
    assert latest["close"] > latest["dma50"]
    assert latest["high_52w"] >= latest["close"]


def test_adr_uses_average_high_low_ratio_definition():
    frame = compute_features(make_candles(30)).data
    expected = 100 * ((frame["high"] / frame["low"]).tail(20).mean() - 1)
    assert abs(frame.iloc[-1]["adr20_pct"] - expected) < 1e-10
