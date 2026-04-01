Batch A - Non-critical fixes and tests
=====================================

Summary of changes made during Batch A session (safe, small edits limited to demos/tests/config/execution):

- Fixed typo in `config.py` (TradingWindow.__init__: `star` -> `start`).
- Fixed several NameError typos in `main.py` to prevent runtime crashes (`trading_star`/`cycle_star`/`closed_coun`).
- Updated `execution.py`:
  - Added optional `circuit_breaker` parameter to engine factory and constructor.
  - Added exposure tracking fields and `reserve_exposure` / `release_exposure` APIs.
  - Wired `MockTradeSystem` to call `release_exposure` on trade exit.
  - Kept production behavior strict (reservations require available exposure).
- Added tests:
  - `tests/test_integration_exposure.py` (fixed typo: `am` -> `amt`).
  - `tests/test_paper_order_flow.py` (end-to-end PAPER-mode reserve→place→release flow).

Testing:
- Ran a PAPER-mode single-cycle smoke test: bot starts, strategy signals generated, trades execute only when exposure reserved.
- Ran full test suite: 22 passed.

Notes:
- All changes were kept minimal and focused. No modifications were made to core strategy, execution logic for live mode, `api.py`, or broker integrations.
- Tests added ensure exposure APIs and mock flows are exercised without changing production exposure rules.

Next recommended steps:
- Add CI pipeline to run the new tests and flake8 on changed files.
- Optionally create a branch/PR describing these non-behavioral fixes.

Date: 2026-04-01
