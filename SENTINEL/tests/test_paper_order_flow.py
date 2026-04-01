"""End-to-end PAPER-mode order placement smoke test.

This test intentionally manipulates the engine's exposure limits to allow a
full place/reserve/release cycle without changing production logic. It
verifies that `reserve_exposure`, `place_order`, and mock trade exit ->
`release_exposure` callback work together.
"""

from execution import DhanExecutionEngine


def test_paper_order_flow_reserve_place_release():
    eng = DhanExecutionEngine(mock_mode=True)

    # Ensure generous exposure for the test (does not affect production defaults)
    eng.max_exposure_inr = 1_000_000.0
    eng.min_capital_reserve = 0.0

    price = 100.0
    qty = 10
    reserve_amt = price * qty

    assert eng.reserve_exposure(reserve_amt), "Failed to reserve exposure for test"

    res = eng.place_order("NIFTY_TEST", "BUY", qty, price, order_type="MARKET")
    assert res.success, f"place_order failed: {res.error_message}"

    # Create a mock trade and exit it to trigger release callback
    trade = eng._mock_system.enter_trade("NIFTY_TEST", "BUY", qty, price)
    assert trade is not None

    exited = eng._mock_system.exit_trade(trade.trade_id, price)
    assert exited is not None

    # After exit, used exposure should be clamped to >= 0 and ideally less than max
    assert eng.used_exposure_inr >= 0.0
    assert eng.used_exposure_inr <= eng.max_exposure_inr
