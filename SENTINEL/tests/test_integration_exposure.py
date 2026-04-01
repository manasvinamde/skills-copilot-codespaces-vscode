"""Integration tests for exposure reservation and release."""

from execution import DhanExecutionEngine


def test_reserve_and_release_basic():
    eng = DhanExecutionEngine(mock_mode=True)
    initial = eng.used_exposure_inr

    # Determine available exposure and reserve a safe fraction for the tes
    max_exposure = eng.max_exposure_inr
    min_reserve = eng.min_capital_reserve
    available = max_exposure - min_reserve - eng.used_exposure_inr
    amt = max(1.0, available / 4.0)

    ok = eng.reserve_exposure(amt)
    assert ok, "reserve_exposure should succeed for computed available amount"
    assert eng.used_exposure_inr >= initial + amt

    # Release and ensure used exposure goes back (clamped >= 0)
    eng.release_exposure(amt)
    assert eng.used_exposure_inr <= initial + 1e-6


def test_place_order_and_release_via_mock_exit():
    eng = DhanExecutionEngine(mock_mode=True)
    initial = eng.used_exposure_inr

    price = 100.0
    qty = 2
    reserve_amt = price * qty
    assert eng.reserve_exposure(reserve_amt)

    # Place market order via engine (mock)
    res = eng.place_order("NIFTY 22150 CE", "BUY", qty, price, order_type="MARKET")
    assert res.success, f"place_order failed: {res.error_message}"

    # Now create a mock trade via internal mock system and exit it to trigger release callback
    trade = eng._mock_system.enter_trade("NIFTY 22150 CE", "BUY", qty, price)
    assert trade is not None

    # Exit trade which will call engine.release_exposure via callback
    exited = eng._mock_system.exit_trade(trade.trade_id, price)
    assert exited is not None

    # After exit, used exposure should be less or equal than before (clamped)
    assert eng.used_exposure_inr <= eng.max_exposure_inr


if __name__ == "__main__":
    test_reserve_and_release_basic()
    print("test_reserve_and_release_basic: PASSED")
    test_place_order_and_release_via_mock_exit()
    print("test_place_order_and_release_via_mock_exit: PASSED")
