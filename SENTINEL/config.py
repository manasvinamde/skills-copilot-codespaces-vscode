"""SENTINEL by Rohan Namde - Trading Bot Configuration for Nifty Options."""

import os
from enum import Enum

class ExecutionMode(Enum):
    """Paper trading vs Live trading mode."""
    PAPER = "PAPER"  # Mock execution (simulated trades)
    LIVE = "LIVE"    # Live execution (real Dhan API trades)

class Mode(Enum):
    SCALP = "SCALP"
    SNIPER = "SNIPER"

class TradingWindow:
    def __init__(self, name: str, start: str, end: str):
        self.name = name
        self.start = start
        self.end = end

    def __repr__(self):
        return f"{self.name}: {self.start} - {self.end}"

class DhanAPIConfig:
    # Load Dhan credentials from environment for safety in production
    api_key: str = os.getenv('DHAN_API_KEY', '')
    api_secret: str = os.getenv('DHAN_API_SECRET', '')
    access_token: str = os.getenv('DHAN_ACCESS_TOKEN', '')
    account_id: str = os.getenv('DHAN_ACCOUNT_ID', '')
    endpoint_base: str = os.getenv('DHAN_ENDPOINT_BASE', 'https://tradeapi.dhan.co')

class SentinelConfig:
    """Configuration for SENTINEL by Rohan Namde trading system."""
    # Default to PAPER to avoid accidental live trading; can be overridden with env var
    execution_mode: ExecutionMode = ExecutionMode.PAPER

    # Allow overriding key numeric params via environment variables
    capital: float = float(os.getenv('SENTINEL_CAPITAL', '500000.0'))
    risk_per_trade_pct: float = float(os.getenv('SENTINEL_RISK_PCT', '0.5'))
    max_trade_risk_inr: float = capital * risk_per_trade_pct / 100
    # Additional risk controls
    min_rr: float = float(os.getenv('SENTINEL_MIN_RR', '2.0'))
    # Hard per-trade risk cap as percentage of capital (default 2%)
    hard_risk_cap_pct: float = float(os.getenv('SENTINEL_HARD_RISK_CAP_PCT', '2.0'))
    # Portfolio-level allowed total risk as percentage of capital (default 10%)
    portfolio_risk_pct: float = float(os.getenv('SENTINEL_PORTFOLIO_RISK_PCT', '10.0'))
    # Minimum capital reserve to keep aside (INR)
    min_capital_reserve: float = float(os.getenv('SENTINEL_MIN_CAPITAL_RESERVE', '0.0'))
    # Keep a readable alias for min capital reserve in INR
    min_capital_reserve_inr: float = min_capital_reserve
    # Maximum percentage of capital allowed to be exposed at any time
    max_exposure_pct: float = float(os.getenv('SENTINEL_MAX_EXPOSURE_PCT', '50.0'))

    trading_windows = [
        TradingWindow("PRE_OPEN", "09:10", "09:15"),
        TradingWindow("REGULAR", "09:15", "15:30"),
        TradingWindow("CLOSE", "15:30", "15:35"),
    ]

    mode_settings = {
        Mode.SCALP: {
            "max_positions": 2,
            "target_pct": 0.75,
            "stop_loss_pct": 0.25,
            "max_holding_minutes": 15,
        },
        Mode.SNIPER: {
            "max_positions": 1,
            "target_pct": 1.5,
            "stop_loss_pct": 0.5,
            "max_holding_minutes": 45,
        },
    }

    default_mode = Mode.SCALP
    mode: Mode = default_mode

    # 📊 TRADING SYMBOL CONFIGURATION
    underlying_symbol: str = "NIFTY"           # Base symbol (NIFTY, BANKNIFTY, etc.)
    instrument_type: str = "OPTION"            # OPTION, FUTURE, etc.
    symbol: str = "NIFTY_OPTION"               # Trading symbol (combined: underlying + instrument)
    
    # Alternative supported symbols (uncomment to change)
    # symbol: str = "BANKNIFTY_OPTION"
    # symbol: str = "FINNIFTY_OPTION"
    
    expiry_weekday: str = "THURSDAY"           # Options expiry day

    log_level: str = "INFO"
    enable_backtest: bool = False

    dhan_api = DhanAPIConfig()


def get_risk_amount(capital: float = SentinelConfig.capital, risk_pct: float = SentinelConfig.risk_per_trade_pct) -> float:
    raw = (capital * risk_pct) / 100
    # Cap to configured absolute max per-trade risk
    try:
        return min(raw, SentinelConfig.max_trade_risk_inr)
    except Exception:
        return raw


# --- Live enablement safety helpers -------------------------------------
# Require explicit environment opt-in before allowing live execution.
# Set ENABLE_LIVE=1 and LIVE_APPROVAL_PHRASE to enable live trading.
LIVE_ENABLED: bool = os.getenv('ENABLE_LIVE', '0') == '1'
LIVE_APPROVAL_PHRASE: str = os.getenv('LIVE_APPROVAL_PHRASE', '')


def is_live_allowed(provided_phrase: str = None) -> bool:
    """Return True only if live is explicitly enabled and (optionally) the
    provided approval phrase matches the configured phrase.

    - Set `ENABLE_LIVE=1` in the environment to opt in.
    - Optionally set `LIVE_APPROVAL_PHRASE` to require a matching phrase.
    """
    if not LIVE_ENABLED:
        return False

    # If no approval phrase is configured, presence of ENABLE_LIVE is enough.
    if not LIVE_APPROVAL_PHRASE:
        return True

    # If a phrase was provided at runtime, require exact match.
    if provided_phrase is not None:
        return provided_phrase == LIVE_APPROVAL_PHRASE

    # Otherwise require the environment phrase to be non-empty (already true here).
    return True
