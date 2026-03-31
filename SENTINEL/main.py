"""Main trading loop for SENTINEL bot."""

import time
import logging
from datetime import datetime
from typing import Optional, Tuple
import argparse
import os
from config import Mode, ExecutionMode, SentinelConfig, is_live_allowed
from data import get_market_data, fetch_nifty_spot_price
from strategy import get_strategy, Signal
from scalper_v2 import ScalperV2
from risk import get_risk_manager
from execution import get_execution_engine
from trade_logger import TradeLogger, get_trade_logger
from strike import choose_option, get_security_id
from dashboard_integration import init_dashboard, update_dashboard_state, log_trade_to_dashboard

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/Users/rohan/SENTINEL/sentinel.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('SENTINEL by Rohan Namde')


class SentinelBot:
    """Main SENTINEL by Rohan Namde - Trading Bot Controller."""

    def __init__(self, mode: Mode = Mode.SCALP, execution_mode: ExecutionMode = None, test_mode: bool = False, adaptive: bool = False):
        self.mode = mode
        self.execution_mode = execution_mode or SentinelConfig.execution_mode
        self.test_mode = test_mode
        self.adaptive = adaptive  # Enable automatic mode switching
        
        # Initialize components
        self.strategy = get_strategy(mode, adaptive_mode=adaptive)
        self.scalper_v2 = ScalperV2(min_score=5, max_score=10)  # Initialize Scalper v2 strategy
        self.risk_manager = get_risk_manager(mode)
        
        # Initialize execution engine based on execution mode
        mock_mode = (self.execution_mode == ExecutionMode.PAPER)
        self.execution_engine = get_execution_engine(mock_mode=mock_mode)
        self.trade_logger = get_trade_logger()  # Initialize trade logger
        
        # Initialize dashboard integration (check env variable or default to True)
        dashboard_enabled = os.getenv('DASHBOARD_ENABLED', 'true').lower() == 'true'
        init_dashboard(enabled=dashboard_enabled)
        
        # Trading state
        self.is_running = False
        self.total_trades = 0
        self.successful_trades = 0
        self.failed_trades = 0
        self.total_pnl = 0.0
        self.mode_switches = 0  # Track mode switches
        
        # Trade ID tracking for logger
        self.active_trade_ids = {}  # Maps position ID to trade ID
        
        # Time-based trading controls
        self.trading_start_time = "09:15"  # Market open (IST)
        self.new_trades_stop_time = "15:15"  # 3:15 PM - stop new trades
        self.close_all_positions_time = "15:30"  # 3:30 PM - market close
        self.positions_closed_today = False  # Track if positions closed at 3:30
        
        logger.info(f"SENTINEL by Rohan Namde - Bot initialized - Strategy: {mode.value}, Execution: {self.execution_mode.value}, Test: {test_mode}, Adaptive: {adaptive}")
        logger.info(f"  ⚡ Scalper v2 Engine: ENABLED (EMA 3/8 · Min Score: 5/10)")

    def load_data(self, symbol: str = "NIFTY", bars: int = 100) -> bool:
        """Load market data."""
        try:
            df = get_market_data(symbol=symbol, bars=bars)
            if df.empty:
                logger.warning(f"No data received for {symbol}")
                return False
            logger.debug(f"Loaded {len(df)} bars for {symbol}")
            return True
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            return False

    def check_adaptive_mode_switch(self, symbol: str = "NIFTY") -> None:
        """Check and execute mode switch if adaptive mode is enabled."""
        if not self.adaptive:
            return
        
        try:
            df = get_market_data(symbol=symbol, bars=100)
            if df.empty:
                return
            
            # Determine optimal mode
            optimal_mode, reason = self.strategy.determine_optimal_mode(df)
            
            # Check if mode should switch
            if optimal_mode != self.mode:
                logger.info(f"⚡ MODE SWITCH: {self.mode.value} → {optimal_mode.value}")
                logger.info(f"   Reason: {reason}")
                
                # Switch mode
                self.mode = optimal_mode
                self.strategy = get_strategy(optimal_mode, adaptive_mode=True)
                self.risk_manager = get_risk_manager(optimal_mode)
                self.mode_switches += 1
                
                # Track mode switch
                self.strategy.mode_switch_history.append({
                    'timestamp': datetime.now(),
                    'from_mode': self.mode.value,
                    'to_mode': optimal_mode.value,
                    'reason': reason
                })
            else:
                # Log market conditions even if no switch
                volatility_pct, vol_class = self.strategy.calculate_volatility(df)
                trend_strength, trend_info = self.strategy.detect_trend_strength(df)
                logger.debug(
                    f"Market Status - Volatility: {vol_class} ({volatility_pct:.2f}%), "
                    f"Trend: {trend_info}, Mode: {self.mode.value}"
                )
        
        except Exception as e:
            logger.error(f"Error checking adaptive mode: {e}")

    def is_trading_started(self, current_time: datetime = None) -> bool:
        """Check if trading has started (after 9:15 AM)."""
        if current_time is None:
            current_time = datetime.now()
        
        current_time_only = current_time.time()
        trading_start = datetime.strptime(self.trading_start_time, "%H:%M").time()
        
        return current_time_only >= trading_start

    def can_place_new_trades(self, current_time: datetime = None) -> bool:
        """Check if new trades are allowed (before 3:15 PM)."""
        if current_time is None:
            current_time = datetime.now()
        
        current_time_only = current_time.time()
        stop_time = datetime.strptime(self.new_trades_stop_time, "%H:%M").time()
        
        return current_time_only < stop_time

    def is_close_all_positions_time(self, current_time: datetime = None) -> bool:
        """Check if it's time to close all positions (3:30 PM)."""
        if current_time is None:
            current_time = datetime.now()
        
        current_time_only = current_time.time()
        close_time = datetime.strptime(self.close_all_positions_time, "%H:%M").time()
        
        # Close if at or past 3:30 PM
        return current_time_only >= close_time

    def force_close_all_positions(self, symbol: str = "NIFTY") -> int:
        """Force close all open positions at market close (3:30 PM)."""
        try:
            if not self.risk_manager.positions:
                logger.info("No open positions to close at market close.")
                return 0
            
            logger.warning("⏰ MARKET CLOSE - FORCE CLOSING ALL POSITIONS")
            
            # Get current price for closing
            df = get_market_data(symbol=symbol, bars=1)
            if df.empty:
                logger.error("Cannot get price for market close")
                return 0
            
            current_price = df['close'].iloc[-1]
            positions_to_close = list(self.risk_manager.positions.keys())
            closed_count = 0
            
            for pos_symbol in positions_to_close:
                if pos_symbol in self.risk_manager.positions:
                    position = self.risk_manager.positions[pos_symbol]
                    pnl = self.risk_manager.close_position(pos_symbol, current_price)
                    
                    if pnl is not None:
                        self.total_pnl += pnl
                        closed_count += 1
                        logger.info(
                            f"🗑️ Market close: Closed {position.side} {position.quantity} {pos_symbol} "
                            f"@ ₹{current_price:.2f} - P&L: ₹{pnl:.2f}"
                        )
                        
                        # Log trade closure to trade logger for market close
                        if pos_symbol in self.active_trade_ids:
                            trade_id = self.active_trade_ids[pos_symbol]
                            self.trade_logger.close_trade(
                                trade_id=trade_id,
                                exit_price=current_price,
                                exit_reason="Market Close",
                                pnl=pnl
                            )
                            del self.active_trade_ids[pos_symbol]  # Remove from active trades
            
            self.positions_closed_today = True
            logger.info(f"✅ Closed {closed_count} position(s) at market close")
            return closed_count
        
        except Exception as e:
            logger.error(f"Error closing positions at market close: {e}")
            return 0

    def run_strategy(self, symbol: str = "NIFTY", bars: int = 100) -> tuple:
        """Run trading strategy to get signal - Uses Scalper v2 by default."""
        try:
            # Load market data as dataframe
            df = get_market_data(symbol=symbol, bars=bars)
            
            if df.empty:
                logger.warning("No market data available for strategy")
                return Signal.WAIT, "No market data"
            
            # Convert dataframe to candle format for ScalperV2
            candles = []
            for idx, row in df.iterrows():
                candle = {
                    'open': row['open'],
                    'high': row['high'],
                    'low': row['low'],
                    'close': row['close'],
                    'volume': row['volume']
                }
                candles.append(candle)
            
            # Get signal from Scalper v2
            signal, reason = self.scalper_v2.get_signal_for_main(candles)
            logger.info(f"Strategy signal: {signal.value} - Reason: {reason}")
            return signal, reason
        except Exception as e:
            logger.error(f"Error running strategy: {e}")
            return Signal.WAIT, f"Error: {str(e)}"

    def check_risk(self, symbol: str, entry_price: float, side: str) -> tuple:
        """Check risk parameters before opening position."""
        try:
            can_open, reason = self.risk_manager.can_open_position(symbol, entry_price, side)
            if not can_open:
                logger.warning(f"Risk check failed for {symbol}: {reason}")
                return False, reason
            
            # Calculate position size
            stop_loss = entry_price * (1 - self.risk_manager.mode_config['stop_loss_pct'] / 100)
            quantity = self.risk_manager.calculate_position_size(entry_price, stop_loss)
            
            if quantity == 0:
                logger.warning(f"Position size is zero for {symbol}")
                return False, "Invalid position size"
            
            logger.info(f"Risk check passed - Quantity: {quantity}, Stop Loss: {stop_loss:.2f}")
            return True, quantity
        except Exception as e:
            logger.error(f"Error checking risk: {e}")
            return False, f"Error: {str(e)}"

    def execute_trade(self, symbol: str, signal: Signal, entry_price: float, quantity: int) -> bool:
        """
        Execute trade when BUY or SELL signal is received.
        
        Steps:
        1. Determine buy/sell side from signal
        2. Call place_order() with position sizing
        3. Print confirmation
        4. Track in risk manager & logger
        
        Args:
            symbol: Trading symbol
            signal: Signal.BUY or Signal.SELL
            entry_price: Current market price for order
            quantity: Position size (from risk manager)
            
        Returns:
            True if trade executed successfully, False otherwise
        """
        try:
            # Step 1: Determine order side from signal
            if signal == Signal.BUY:
                side = "BUY"
                side_emoji = "📈"
            elif signal == Signal.SELL:
                side = "SELL"
                side_emoji = "📉"
            else:
                return False

            # PRINT PRE-EXECUTION INFO
            print("\n" + "─" * 90)
            print(f"{side_emoji} PLACING {side} ORDER")
            print("─" * 90)
            print(f"  Symbol:        {symbol}")
            print(f"  Side:          {side}")
            print(f"  Quantity:      {quantity} contracts")
            print(f"  Entry Price:   ₹{entry_price:.2f}")
            print(f"  Order Type:    MARKET")
            print(f"  Total Value:   ₹{entry_price * quantity:,.2f}")

            # Step 2: Place order through execution engine with position sizing
            logger.info(f"Placing {side} order: {quantity} {symbol} @ ₹{entry_price:.2f}")
            
            result = self.execution_engine.place_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=entry_price,
                order_type="MARKET"
            )

            if result.success:
                # Step 3: Print execution confirmation
                actual_price = result.order.average_price
                filled_qty = result.order.filled_quantity
                slippage_pct = (result.slippage / (entry_price * quantity)) * 100 if quantity > 0 else 0
                
                print(f"\n  ✅ ORDER EXECUTED SUCCESSFULLY")
                print(f"     Order ID:      {result.order.order_id}")
                print(f"     Filled Price:  ₹{actual_price:.2f}")
                print(f"     Filled Qty:    {filled_qty} contracts")
                print(f"     Slippage:      ₹{result.slippage:.2f} ({slippage_pct:.3f}%)")
                print("─" * 90 + "\n")
                
                # Step 4: Open position in risk manager
                df = get_market_data(symbol=symbol, bars=100)
                atr = df['atr_14'].iloc[-1] if 'atr_14' in df.columns else 50
                
                position = self.risk_manager.open_position(
                    symbol=symbol,
                    side=side,
                    entry_price=actual_price,
                    quantity=filled_qty,
                    atr=atr
                )

                if position:
                    # Calculate risk/reward
                    stop_loss = position.stop_loss if hasattr(position, 'stop_loss') else 0
                    target = position.target if hasattr(position, 'target') else 0
                    
                    logger.info(f"✅ Trade confirmed: {side} {filled_qty} {symbol} @ ₹{actual_price:.2f}")
                    logger.info(f"   Stop Loss: ₹{stop_loss:.2f} | Target: ₹{target:.2f}")
                    
                    # Log trade to trade logger
                    trade = self.trade_logger.create_trade(
                        symbol=symbol,
                        side=side,
                        entry_price=actual_price,
                        quantity=filled_qty,
                        entry_reason=f"Signal: {signal.value}",
                        slippage=result.slippage
                    )
                    
                    # Store mapping of position symbol to trade ID for closing
                    self.active_trade_ids[symbol] = trade.trade_id
                    
                    self.total_trades += 1
                    self.successful_trades += 1
                    
                    return True
                else:
                    logger.error(f"Failed to open position for {symbol}")
                    print(f"  ❌ POSITION SETUP FAILED - Could not register in risk manager")
                    print("─" * 90 + "\n")
                    return False
            else:
                # Order execution failed
                print(f"\n  ❌ ORDER EXECUTION FAILED")
                print(f"     Reason: {result.error_message}")
                print("─" * 90 + "\n")
                
                logger.error(f"Order execution failed: {result.error_message}")
                self.failed_trades += 1
                return False

        except Exception as e:
            print(f"  ❌ EXCEPTION DURING ORDER PLACEMENT: {e}")
            print("─" * 90 + "\n")
            logger.error(f"Error executing trade: {e}")
            self.failed_trades += 1
            return False

    def prepare_and_execute_trade(self, signal: Signal, quantity: int) -> Tuple[bool, Optional[str]]:
        """
        Complete pre-trade flow: fetch price → select strike → execute trade.
        
        This is the main entry point for placing trades. It orchestrates:
        1. Fetch current NIFTY spot price
        2. Choose option (strike + type) based on signal
        3. Get Dhan security ID for the option
        4. Execute trade with option symbol
        
        Args:
            signal: Signal.BUY or Signal.SELL
            quantity: Number of contracts to trade
        
        Returns:
            Tuple[bool, Optional[str]]: (success, option_symbol)
            - success: True if trade executed, False otherwise
            - option_symbol: The selected option symbol (e.g., "NIFTY 22150 CE")
        
        Example:
            >>> success, symbol = bot.prepare_and_execute_trade(Signal.BUY, quantity=1)
            >>> if success:
            ...     print(f"Traded: {symbol}")
        """
        try:
            # ========================================================================
            # STEP 1: Fetch current NIFTY spot price
            # ========================================================================
            logger.info("📊 Step 1: Fetching NIFTY spot price...")
            nifty_price = fetch_nifty_spot_price(use_dhan=True)
            
            if nifty_price is None or nifty_price <= 0:
                logger.error("❌ Failed to fetch NIFTY spot price")
                return False, None
            
            logger.info(f"✅ NIFTY Spot: ₹{nifty_price:.2f}")
            
            # ========================================================================
            # STEP 2: Choose option strike & type based on signal
            # ========================================================================
            logger.info(f"⚙️  Step 2: Selecting option for signal {signal.value}...")
            
            signal_str = "BUY" if signal == Signal.BUY else "SELL"
            option_symbol = choose_option(nifty_price, signal_str)
            
            logger.info(f"✅ Selected Option: {option_symbol}")
            
            # ========================================================================
            # STEP 3: Get Dhan security ID for the option
            # ========================================================================
            logger.info(f"🔗 Step 3: Looking up Dhan security ID...")
            security_id = get_security_id(option_symbol)
            
            if security_id is None:
                logger.warning(f"⚠️  Security ID not found for {option_symbol}")
                logger.warning("   Attempting generic NIFTY_CE/PE mapping...")
                generic_type = "NIFTY_CE" if signal == Signal.BUY else "NIFTY_PE"
                security_id = get_security_id(generic_type, use_generic=True)
                
                if security_id is None:
                    logger.error(f"❌ No security ID mapping available")
                    return False, option_symbol
            
            logger.info(f"✅ Security ID: {security_id}")
            
            # ========================================================================
            # STEP 4: Execute trade with selected option symbol
            # ========================================================================
            logger.info(f"📍 Step 4: Executing trade...")
            
            success = self.execute_trade(
                symbol=option_symbol,
                signal=signal,
                entry_price=nifty_price,
                quantity=quantity
            )
            
            if success:
                logger.info(f"🎯 Trade execution completed: {option_symbol}")
                return True, option_symbol
            else:
                logger.error(f"❌ Trade execution failed for {option_symbol}")
                return False, option_symbol
        
        except Exception as e:
            logger.error(f"❌ Exception in prepare_and_execute_trade: {e}")
            return False, None

    def check_positions(self, symbol: str = "NIFTY", current_price: float = None) -> None:
        """Check and close positions if stop loss/target hit."""
        try:
            if current_price is None:
                df = get_market_data(symbol=symbol, bars=1)
                current_price = df['close'].iloc[-1] if not df.empty else None

            if current_price is None:
                return

            # Check all positions
            symbols_to_close = self.risk_manager.check_positions({symbol: current_price})

            for sym in symbols_to_close:
                if sym in self.risk_manager.positions:
                    position = self.risk_manager.positions[sym]
                    pnl = self.risk_manager.close_position(sym, current_price)
                    
                    if pnl is not None:
                        self.total_pnl += pnl
                        logger.info(
                            f"Position closed: {position.side} {position.quantity} {sym} "
                            f"@ ₹{current_price:.2f} - P&L: ₹{pnl:.2f}"
                        )
                        
                        # Log trade closure to dashboard
                        log_trade_to_dashboard(
                            timestamp=datetime.now().strftime("%H:%M:%S"),
                            symbol=sym,
                            signal="SELL" if position.side == "BUY" else "BUY",
                            price=current_price,
                            pnl=pnl
                        )
                        
                        # Also update BOT_STATE with closed trade
                        BOT_STATE["trades"].insert(0, {
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "symbol": sym,
                            "signal": "EXIT",
                            "price": current_price,
                            "pnl": pnl
                        })
                        BOT_STATE["trades"] = BOT_STATE["trades"][:50]
                        
                        # Log trade closure to trade logger
                        if sym in self.active_trade_ids:
                            trade_id = self.active_trade_ids[sym]
                            exit_reason = "Target Hit" if pnl > 0 else "Stop Loss" if pnl < 0 else "Manual Close"
                            
                            self.trade_logger.close_trade(
                                trade_id=trade_id,
                                exit_price=current_price,
                                exit_reason=exit_reason,
                                pnl=pnl
                            )
                            
                            del self.active_trade_ids[sym]  # Remove from active trades
                        
                        # Track consecutive losses
                        if pnl < 0:
                            self.risk_manager.consecutive_losses += 1
                        else:
                            self.risk_manager.consecutive_losses = 0

        except Exception as e:
            logger.error(f"Error checking positions: {e}")

    def log_results(self) -> None:
        """Log current trading results."""
        portfolio_status = self.risk_manager.get_portfolio_status()
        
        logger.info(
            f"===== TRADING SUMMARY ====="
            f" | Total Trades: {self.total_trades}"
            f" | Successful: {self.successful_trades}"
            f" | Failed: {self.failed_trades}"
            f" | Daily P&L: ₹{portfolio_status['daily_pnl']:.2f}"
            f" | Total P&L: ₹{self.total_pnl:.2f}"
            f" | Open Positions: {portfolio_status['total_positions']}"
            f" | Consecutive Losses: {self.risk_manager.consecutive_losses}"
            f" | Cool-down Trades: {self.risk_manager.cool_down_trades}"
        )
    
    def print_trade_statistics(self):
        """Print trade statistics from trade logger."""
        self.trade_logger.print_statistics()
    
    def print_all_trades(self):
        """Print all trades from trade logger."""
        self.trade_logger.print_trades()
    
    def save_trading_session(self):
        """Save trading session summary to JSON."""
        self.trade_logger.save_summary()
        logger.info("Trading session saved to summary")

    def print_cycle_summary(self, signal: Signal, current_price: float, quantity: int = 0, trade_executed: bool = False) -> None:
        """Print formatted cycle summary with mode, signal, and P&L."""
        portfolio_status = self.risk_manager.get_portfolio_status()
        
        print("\n" + "─" * 90)
        print(f"📊 CYCLE SUMMARY | Time: {datetime.now().strftime('%H:%M:%S')}")
        print("─" * 90)
        print(f"  Mode:           {self.mode.value}")
        print(f"  Signal:         {signal.value}")
        print(f"  Current Price:  ₹{current_price:.2f}")
        if quantity > 0:
            print(f"  Position Size:  {quantity} contracts")
        print(f"  P&L (Session):  ₹{self.total_pnl:.2f}")
        print(f"  Open Positions: {portfolio_status['total_positions']}")
        print(f"  Win Rate:       {self.successful_trades}/{self.total_trades} ({(self.successful_trades/max(1, self.total_trades)*100):.1f}%)")
        if trade_executed:
            print(f"  ✅ Trade Executed")
        print("─" * 90 + "\n")

    def run_once(self, symbol: str = "NIFTY") -> None:
        """Run single trading cycle: get data → strategy → risk → execute → log → print."""
        try:
            current_time = datetime.now()
            
            # ⏰ TIME CONTROL 1: Check if trading has started (9:15 AM)
            if not self.is_trading_started(current_time):
                logger.debug(f"Trading not started yet. Market opens at {self.trading_start_time}")
                return
            
            # ⏰ TIME CONTROL 2: Check if it's market close time (3:30 PM) - force close all positions
            if self.is_close_all_positions_time(current_time):
                if not self.positions_closed_today:
                    logger.warning("🔔 MARKET CLOSE TIME - Force closing all positions")
                    self.force_close_all_positions(symbol=symbol)
                else:
                    logger.debug("Positions already closed at market close for today")
                return
            
            logger.info("===== New Trading Cycle =====")
            
            # ┌─────────────────────────────────────────────────────────────────┐
            # │ STEP 0: Check Adaptive Mode Switching                            │
            # └─────────────────────────────────────────────────────────────────┘
            self.check_adaptive_mode_switch(symbol=symbol)
            
            # ┌─────────────────────────────────────────────────────────────────┐
            # │ STEP 1: GET DATA                                                │
            # └─────────────────────────────────────────────────────────────────┘
            logger.debug("Step 1: Loading market data...")
            if not self.load_data(symbol=symbol, bars=100):
                logger.warning("Skipping cycle: Failed to load data")
                return

            df = get_market_data(symbol=symbol, bars=1)
            if df.empty:
                logger.warning("No current price data available")
                return
            current_price = df['close'].iloc[-1]
            logger.debug(f"  ✓ Current Price: ₹{current_price:.2f}")

            # ┌─────────────────────────────────────────────────────────────────┐
            # │ STEP 2: CHECK EXISTING POSITIONS (Exit on SL/Target)           │
            # └─────────────────────────────────────────────────────────────────┘
            logger.debug("Step 2: Checking existing positions...")
            self.check_positions(symbol=symbol, current_price=current_price)
            
            # ┌─────────────────────────────────────────────────────────────────┐
            # │ STEP 3: RUN STRATEGY                                            │
            # └─────────────────────────────────────────────────────────────────┘
            logger.debug("Step 3: Running strategy...")
            signal, reason = self.run_strategy(symbol=symbol, bars=100)
            logger.debug(f"  ✓ Signal: {signal.value} ({reason})")

            if signal == Signal.WAIT:
                logger.debug("  ⏸ No trade signal - waiting")
                self.print_cycle_summary(signal, current_price, 0, False)
                return

            # ⏰ TIME CONTROL 3: Check if new trades are still allowed (before 3:15 PM)
            if not self.can_place_new_trades(current_time):
                logger.info(f"🛑 NEW TRADES STOPPED - No new trades allowed after {self.new_trades_stop_time}")
                logger.info(f"   Monitoring {len(self.risk_manager.positions)} open position(s) until market close")
                return

            # ┌─────────────────────────────────────────────────────────────────┐
            # │ STEP 4: CHECK RISK                                              │
            # └─────────────────────────────────────────────────────────────────┘
            logger.debug("Step 4: Checking risk parameters...")
            can_trade, quantity = self.check_risk(symbol, current_price, signal.value)
            
            if not can_trade:
                logger.warning(f"  ✗ Risk check failed: {quantity}")
                self.print_cycle_summary(signal, current_price, 0, False)
                return
            
            logger.debug(f"  ✓ Risk check passed - Position size: {quantity}")

            # ┌─────────────────────────────────────────────────────────────────┐
            # │ STEP 5: EXECUTE TRADE                                           │
            # └─────────────────────────────────────────────────────────────────┘
            logger.debug("Step 5: Executing trade...")
            trade_executed = self.execute_trade(symbol, signal, current_price, quantity)
            
            if not trade_executed:
                logger.warning("  ✗ Trade execution failed")
                self.print_cycle_summary(signal, current_price, quantity, False)
                return
            
            logger.debug(f"  ✓ Trade executed successfully")

            # Track cool-down trades
            if self.risk_manager.cool_down_trades > 0:
                self.risk_manager.cool_down_trades -= 1
                logger.info(f"Cool-down period: {self.risk_manager.cool_down_trades} trades remaining")

            # Check if max consecutive losses reached
            if self.risk_manager.consecutive_losses >= self.risk_manager.max_consecutive_losses:
                logger.warning("Max consecutive losses reached - Entering cool-down period")
                self.risk_manager.cool_down_trades = self.risk_manager.max_cool_down_trades
                self.risk_manager.consecutive_losses = 0

            # ┌─────────────────────────────────────────────────────────────────┐
            # │ STEP 6: LOG RESULTS                                             │
            # └─────────────────────────────────────────────────────────────────┘
            logger.debug("Step 6: Logging results...")
            self.log_results()
            
            # ┌─────────────────────────────────────────────────────────────────┐
            # │ STEP 7: UPDATE DASHBOARD                                        │
            # └─────────────────────────────────────────────────────────────────┘
            portfolio_status = self.risk_manager.get_portfolio_status()
            
            # Update dashboard via integration layer
            update_dashboard_state(
                pnl=self.total_pnl,
                mode=self.mode.value,
                signal=signal.value if signal != Signal.WAIT else "WAIT",
                positions=portfolio_status['total_positions'],
                daily_trades=self.total_trades,
                nifty_price=current_price,
                execution_mode=self.execution_mode.value
            )
            
            # Also update bot_state directly for immediate API access
            from api import BOT_STATE
            BOT_STATE["pnl"] = self.total_pnl
            BOT_STATE["mode"] = self.mode.value
            BOT_STATE["signal"] = signal.value if signal != Signal.WAIT else "WAIT"
            BOT_STATE["positions"] = portfolio_status['total_positions']
            BOT_STATE["daily_trades"] = self.total_trades
            BOT_STATE["nifty_price"] = current_price
            BOT_STATE["execution_mode"] = self.execution_mode.value
            BOT_STATE["risk"] = "CRITICAL" if self.total_pnl < -1000 else ("HIGH" if self.total_pnl < -500 else ("MEDIUM" if self.total_pnl < 0 else "LOW"))
            BOT_STATE["timestamp"] = datetime.now().isoformat()
            
            # Log trade to dashboard if executed
            if trade_executed:
                current_trade_pnl = 0.0  # Will be calculated when position closes
                log_trade_to_dashboard(
                    timestamp=datetime.now().strftime("%H:%M:%S"),
                    symbol=f"NIFTY {int(current_price / 50) * 50} {'CE' if signal == Signal.BUY else 'PE'}",
                    signal="BUY" if signal == Signal.BUY else "SELL",
                    price=current_price,
                    pnl=current_trade_pnl
                )
                
                # Also add to BOT_STATE trades list directly
                BOT_STATE["trades"].insert(0, {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "symbol": f"NIFTY {int(current_price / 50) * 50} {'CE' if signal == Signal.BUY else 'PE'}",
                    "signal": "BUY" if signal == Signal.BUY else "SELL",
                    "price": current_price,
                    "pnl": current_trade_pnl
                })
                # Keep only last 50 trades
                BOT_STATE["trades"] = BOT_STATE["trades"][:50]
            
            # ┌─────────────────────────────────────────────────────────────────┐
            # │ STEP 8: PRINT CYCLE SUMMARY                                     │
            # └─────────────────────────────────────────────────────────────────┘
            self.print_cycle_summary(signal, current_price, quantity, trade_executed)

        except Exception as e:
            logger.error(f"Error in trading cycle: {e}")

    def run_continuous(self, symbol: str = "NIFTY", interval: float = 1.0) -> None:
        """Run bot continuously with specified interval."""
        self.is_running = True
        logger.info(f"Starting continuous trading loop - Interval: {interval}s")

        try:
            while self.is_running:
                cycle_start = time.time()
                
                self.run_once(symbol=symbol)
                
                # Maintain interval timing
                elapsed = time.time() - cycle_start
                sleep_time = max(0, interval - elapsed)
                
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            logger.info("Trading loop interrupted by user")
        except Exception as e:
            logger.error(f"Fatal error in trading loop: {e}")
        finally:
            self.is_running = False
            logger.info("SENTINEL Bot stopped")
            self.log_results()

    def stop(self) -> None:
        """Stop the bot."""
        self.is_running = False
        logger.info("Stop signal sent to bot")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='SENTINEL Trading Bot')
    parser.add_argument('--mode', type=str, choices=['SCALP', 'SNIPER'], 
                       default='SCALP', help='Trading strategy mode (SCALP or SNIPER)')
    parser.add_argument('--execution', type=str, choices=['PAPER', 'LIVE'], 
                       default='PAPER', help='Execution mode (PAPER for mock trades, LIVE for real trades)')
    parser.add_argument('--test', action='store_true', 
                       help='Run in test mode (single cycle)')
    parser.add_argument('--adaptive', action='store_true',
                       help='Enable adaptive mode switching based on market conditions')
    parser.add_argument('--interval', type=float, default=1.0, 
                       help='Trading cycle interval in seconds')
    parser.add_argument('--symbol', type=str, default=SentinelConfig.symbol, 
                       help=f'Trading symbol (default: {SentinelConfig.symbol})')
    
    # Legacy support: --live flag maps to LIVE execution mode
    parser.add_argument('--live', action='store_true', dest='legacy_live',
                       help='(Deprecated: use --execution LIVE instead) Run with live execution')
    
    args = parser.parse_args()

    # Initialize bot
    strategy_mode = Mode.SCALP if args.mode == 'SCALP' else Mode.SNIPER
    
    # Determine execution mode (--execution takes priority over --live)
    if args.legacy_live and args.execution == 'PAPER':
        execution_mode = ExecutionMode.LIVE
        logger.warning("⚠️  --live flag is deprecated. Use --execution LIVE instead")
    else:
        execution_mode = ExecutionMode.LIVE if args.execution == 'LIVE' else ExecutionMode.PAPER

    # Safety gate: require explicit opt-in for LIVE execution via environment
    if execution_mode == ExecutionMode.LIVE and not is_live_allowed():
        logger.error("LIVE execution requested but ENABLE_LIVE environment flag or approval phrase not present.")
        logger.error("To enable LIVE trading set ENABLE_LIVE=1 and optionally set LIVE_APPROVAL_PHRASE to require a phrase.")
        logger.error("Falling back to PAPER mode to avoid accidental live trading.")
        execution_mode = ExecutionMode.PAPER
    
    # Create bot
    bot = SentinelBot(
        mode=strategy_mode,
        execution_mode=execution_mode,
        test_mode=args.test,
        adaptive=args.adaptive
    )

    # Print startup info
    logger.info("="*80)
    logger.info(f"🚀 SENTINEL Bot Started")
    logger.info(f"   Strategy Mode:    {strategy_mode.value}")
    logger.info(f"   Execution Mode:   {execution_mode.value}")
    logger.info(f"   Adaptive:         {args.adaptive}")
    logger.info(f"   Symbol:           {args.symbol}")
    if not args.test:
        logger.info(f"   Cycle Interval:   {args.interval}s")
    logger.info("="*80)

    # Run bot
    if args.test:
        logger.info("Running in TEST MODE - Single cycle")
        bot.run_once(symbol=args.symbol)
    else:
        logger.info(f"Running in CONTINUOUS MODE - Interval: {args.interval}s")
        try:
            bot.run_continuous(symbol=args.symbol, interval=args.interval)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            bot.stop()


if __name__ == "__main__":
    main()
