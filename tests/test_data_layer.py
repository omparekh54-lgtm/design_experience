from datetime import date, datetime, timezone
from decimal import Decimal

from packages.data_layer.angel_one import AngelOneMarketDataProvider
from packages.data_layer.instrument_master import normalize_angel_instruments
from packages.data_layer.models import Candle, FinancialFiling, Instrument
from packages.data_layer.storage import InMemoryStorage
from packages.data_layer.validation import validate_candles


class FakeAngelSession:
    def __init__(self):
        self.requests = []

    def getCandleData(self, params):
        self.requests.append(params)
        return {
            "status": True,
            "data": [["2026-10-15T00:00:00+05:30", 100, 110, 95, 108, 123456]],
        }

    def ltpData(self, exchange, tradingsymbol, symboltoken):
        return {
            "status": True,
            "data": {"ltp": 109, "open": 100, "high": 110, "low": 95, "close": 108},
        }


def test_angel_instrument_master_normalizes_cash_equity():
    rows = [
        {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "exch_seg": "NSE"},
        {"token": "999", "symbol": "RELIANCE26OCTFUT", "name": "RELIANCE", "exch_seg": "NFO"},
    ]
    instruments = normalize_angel_instruments(rows)
    assert len(instruments) == 1
    assert instruments[0].instrument_id == "NSE:RELIANCE"
    assert instruments[0].token == "2885"


def test_angel_daily_history_is_normalized():
    instrument = Instrument("NSE:ABC", "NSE", "ABC", "ABC-EQ", token="123")
    session = FakeAngelSession()
    provider = AngelOneMarketDataProvider(session)
    candles = provider.daily_candles(instrument, date(2026, 10, 1), date(2026, 10, 16))
    assert len(candles) == 1
    assert candles[0].close == Decimal("108")
    assert candles[0].source == "angel_one"
    assert session.requests[0]["interval"] == "ONE_DAY"


def test_daily_history_chunks_long_ranges():
    instrument = Instrument("NSE:ABC", "NSE", "ABC", "ABC-EQ", token="123")
    session = FakeAngelSession()
    provider = AngelOneMarketDataProvider(session)
    provider.daily_candles(instrument, date(2010, 1, 1), date(2026, 10, 16))
    assert len(session.requests) >= 3


def test_validation_rejects_invalid_ohlc():
    candle = Candle(
        instrument_id="NSE:ABC",
        timestamp=datetime(2026, 10, 15, tzinfo=timezone.utc),
        open=Decimal("111"),
        high=Decimal("110"),
        low=Decimal("95"),
        close=Decimal("108"),
        volume=100,
        source="test",
    )
    codes = {issue.code for issue in validate_candles([candle])}
    assert "OPEN_OUTSIDE_RANGE" in codes


def test_point_in_time_filing_lookup_never_reads_future_revision():
    store = InMemoryStorage()
    first = FinancialFiling(
        instrument_id="NSE:ABC",
        period_end=date(2026, 9, 30),
        filed_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        statement_scope="consolidated",
        source="NSE",
        version=1,
        pat=Decimal("100"),
    )
    revision = FinancialFiling(
        instrument_id="NSE:ABC",
        period_end=date(2026, 9, 30),
        filed_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
        statement_scope="consolidated",
        source="NSE",
        version=2,
        pat=Decimal("92"),
    )
    store.append_filings([first, revision])
    as_of = datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert store.latest_filing_as_of("NSE:ABC", as_of).pat == Decimal("100")
