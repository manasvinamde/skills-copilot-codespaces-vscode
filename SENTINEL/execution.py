"""Order execution engine for SENTINEL trading bot."""

import time
import random
import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import requests

try:
    from dhanhq import dhanhq
    DHANHQ_AVAILABLE = True
except ImportError:
    DHANHQ_AVAILABLE = False

from config import DhanAPIConfig, is_live_allowed

logger = logging.getLogger('SENTINEL by Rohan Namde - EXECUTION')


@dataclass
class Order:
    """Represents a trading order."""
    order_id: str
    symbol: str
    side: str  # 'BUY' or 'SELL'
    quantity: int
    order_price: float
    average_price: float
    order_type: str  # 'MARKET', 'LIMIT', 'SL', 'SL-M'
    status: str  # 'PENDING', 'FILLED', 'CANCELLED'
    creation_time: datetime
    fill_time: Optional[datetime] = None
    filled_quantity: int = 0


@dataclass
class MockTrade:
    """Represents a complete trade (entry + exit)."""
    trade_id: str
    symbol: str
    entry_time: datetime
    entry_price: float
    entry_quantity: int
    entry_side: str  # 'BUY' or 'SELL'
    
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_quantity: Optional[int] = None
    exit_side: Optional[str] = None
    
    pnl: Optional[float] = None
    pnl_percentage: Optional[float] = None
    status: str = "OPEN"  # 'OPEN', 'CLOSED', 'CANCELLED'
    
    # Additional tracking
    slippage_entry: float = 0.0
    slippage_exit: float = 0.0
    holding_minutes: Optional[float] = None
    
    def calculate_pnl(self) -> Tuple[float, float]:
        """Calculate P&L and P&L percentage."""
        if self.exit_price is None or self.exit_quantity is None:
            return 0.0, 0.0
        
        # For LONG positions (BUY entry)
        if self.entry_side == 'BUY':
            pnl = (self.exit_price - self.entry_price) * self.exit_quantity
        # For SHORT positions (SELL entry)
        else:
            pnl = (self.entry_price - self.exit_price) * self.exit_quantity
        
        pnl_percentage = (pnl / (self.entry_price * self.entry_quantity)) * 100
        
        return pnl, pnl_percentage
    
    def close_trade(self, exit_price: float, exit_time: datetime, slippage: float = 0.0):
        """Close the trade with exit price."""
        self.exit_price = exit_price
        self.exit_time = exit_time
        self.exit_quantity = self.entry_quantity
        self.exit_side = 'SELL' if self.entry_side == 'BUY' else 'BUY'
        self.slippage_exit = slippage
        self.status = "CLOSED"
        
        # Calculate holding time
        self.holding_minutes = (exit_time - self.entry_time).total_seconds() / 60
        
        # Calculate P&L
        self.pnl, self.pnl_percentage = self.calculate_pnl()
    
    def format_result(self) -> str:
        """Format trade result as readable string."""
        status_emoji = "✅" if self.pnl >= 0 else "❌"
        
        result = f"""
{status_emoji} TRADE RESULT:
   Trade ID:         {self.trade_id}
   Symbol:           {self.symbol}
   Status:           {self.status}
   
   ENTRY:
      Time:          {self.entry_time.strftime('%Y-%m-%d %H:%M:%S')}
      Side:          {self.entry_side}
      Price:         ₹{self.entry_price:.2f}
      Quantity:      {self.entry_quantity}
      Total Value:   ₹{self.entry_price * self.entry_quantity:,.2f}
      Slippage:      ₹{self.slippage_entry:.2f}
   
   EXIT:
      Time:          {self.exit_time.strftime('%Y-%m-%d %H:%M:%S') if self.exit_time else 'N/A'}
      Side:          {self.exit_side}
      Price:         ₹{self.exit_price:.2f}
      Quantity:      {self.exit_quantity}
      Total Value:   ₹{self.exit_price * self.exit_quantity:,.2f}
      Slippage:      ₹{self.slippage_exit:.2f}
   
   PERFORMANCE:
      Holding Time:  {self.holding_minutes:.1f} minutes
      Profit/Loss:   ₹{self.pnl:.2f}
      Return %:      {self.pnl_percentage:.2f}%
      Net Slippage:  ₹{self.slippage_entry + self.slippage_exit:.2f}
"""
        return result


@dataclass
class ExecutionResult:
    """Result of order execution."""
    success: bool
    order: Order
    error_message: str = ""
    slippage: float = 0.0


class MockTradeSystem:
    """Mock trading system to simulate buy/sell orders and track P&L."""
    
    def __init__(self):
        self.open_trades: Dict[str, MockTrade] = {}  # Active trades
        self.closed_trades: List[MockTrade] = []      # Completed trades
        self.trade_counter = 1000
        
        # Statistics
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
        self.total_slippage = 0.0
    
    def generate_trade_id(self) -> str:
        """Generate unique trade ID."""
        self.trade_counter += 1
        return f"TRD_{datetime.now().strftime('%Y%m%d')}_{self.trade_counter}"
    
    def enter_trade(self, symbol: str, side: str, quantity: int, 
                   entry_price: float, slippage: float = 0.0) -> MockTrade:
        """Enter a new trade (BUY or SELL)."""
        trade_id = self.generate_trade_id()
        
        trade = MockTrade(
            trade_id=trade_id,
            symbol=symbol,
            entry_time=datetime.now(),
            entry_price=entry_price,
            entry_quantity=quantity,
            entry_side=side,
            slippage_entry=slippage,
            status="OPEN"
        )
        
        self.open_trades[trade_id] = trade
        return trade
    
    def exit_trade(self, trade_id: str, exit_price: float, 
                  slippage: float = 0.0) -> Optional[MockTrade]:
        """Exit an open trade and calculate P&L."""
        if trade_id not in self.open_trades:
            return None
        
        trade = self.open_trades[trade_id]
        trade.close_trade(exit_price, datetime.now(), slippage)
        
        # Move to closed trades
        del self.open_trades[trade_id]
        self.closed_trades.append(trade)
        
        # Update statistics
        self.total_trades += 1
        self.total_pnl += trade.pnl
        self.total_slippage += (trade.slippage_entry + trade.slippage_exit)
        
        if trade.pnl > 0:
            self.winning_trades += 1
        elif trade.pnl < 0:
            self.losing_trades += 1
        
        return trade
    
    def get_open_trades(self) -> List[MockTrade]:
        """Get all open trades."""
        return list(self.open_trades.values())
    
    def get_trade_statistics(self) -> Dict:
        """Get trading statistics."""
        if self.total_trades == 0:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'average_pnl_per_trade': 0.0,
                'total_slippage': 0.0,
                'average_slippage_per_trade': 0.0
            }
        
        win_rate = (self.winning_trades / self.total_trades) * 100
        avg_pnl = self.total_pnl / self.total_trades
        avg_slippage = self.total_slippage / self.total_trades
        
        return {
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': win_rate,
            'total_pnl': self.total_pnl,
            'average_pnl_per_trade': avg_pnl,
            'total_slippage': self.total_slippage,
            'average_slippage_per_trade': avg_slippage
        }
    
    def print_trade_summary(self, trade: MockTrade):
        """Print detailed trade summary."""
        print(trade.format_result())
    
    def print_all_trades(self):
        """Print summary of all closed trades."""
        print("\n" + "="*80)
        print("ALL CLOSED TRADES SUMMARY")
        print("="*80)
        
        for i, trade in enumerate(self.closed_trades, 1):
            print(f"\n[Trade {i}/{len(self.closed_trades)}]")
            print(f"   {trade.symbol} | {trade.entry_side} @ ₹{trade.entry_price:.2f} → {trade.exit_side} @ ₹{trade.exit_price:.2f}")
            print(f"   Qty: {trade.entry_quantity} | Time: {trade.holding_minutes:.1f}m | P&L: ₹{trade.pnl:.2f} ({trade.pnl_percentage:.2f}%)")
        
        stats = self.get_trade_statistics()
        print(f"\n" + "="*80)
        print("TRADING STATISTICS")
        print("="*80)
        print(f"Total Trades:      {stats['total_trades']}")
        print(f"Winning Trades:    {stats['winning_trades']} ({stats['win_rate']:.1f}%)")
        print(f"Losing Trades:     {stats['losing_trades']}")
        print(f"Total P&L:         ₹{stats['total_pnl']:.2f}")
        print(f"Avg P&L/Trade:     ₹{stats['average_pnl_per_trade']:.2f}")
        print(f"Total Slippage:    ₹{stats['total_slippage']:.2f}")
        print(f"Avg Slippage/Trade: ₹{stats['average_slippage_per_trade']:.2f}")
        print("="*80 + "\n")


class DhanExecutionEngine:
    """Order execution engine for Dhan API."""

    def __init__(self, mock_mode: bool = True):
        self.mock_mode = mock_mode
        self.orders: Dict[str, Order] = {}
        self.order_counter = 1000
        
        # Dhan API client (initialized for live mode)
        self.dhan_client = None

        # Safety: if caller requested live but live not explicitly allowed,
        # force mock mode and log a clear message.
        if not self.mock_mode and not is_live_allowed():
            logger.error("Live execution requested but ENABLE_LIVE is not set or approval phrase missing. Forcing MOCK mode.")
            self.mock_mode = True

        if not self.mock_mode:
            self._initialize_dhan_client()
        
        # Execution statistics
        self.total_orders = 0
        self.successful_orders = 0
        self.failed_orders = 0
        self.total_slippage = 0.0
        
        logger.info(f"Execution Engine initialized - Mode: {'MOCK' if mock_mode else 'LIVE'}")

    def _initialize_dhan_client(self) -> bool:
        """Initialize Dhan API client with credentials."""
        try:
            # Check if dhanhq library is available
            if not DHANHQ_AVAILABLE:
                logger.error("dhanhq library not found. Install with: pip3 install dhanhq")
                return False

            # Check for credentials
            if not DhanAPIConfig.access_token or DhanAPIConfig.access_token == "YOUR_DHAN_ACCESS_TOKEN_HERE":
                logger.error("Dhan API credentials not configured. Update environment variables (DHAN_ACCESS_TOKEN etc.).")
                return False

            # Try initializing client with a small retry loop
            attempts = 3
            for attempt in range(1, attempts + 1):
                try:
                    self.dhan_client = dhanhq(DhanAPIConfig.api_key, DhanAPIConfig.access_token)
                    logger.info("✅ Dhan API client initialized successfully")
                    logger.info(f"   Account ID: {DhanAPIConfig.account_id}")
                    logger.info(f"   API Key: {DhanAPIConfig.api_key[:10]}...")
                    return True
                except Exception as inner_e:
                    logger.warning(f"Dhan client init attempt {attempt}/{attempts} failed: {inner_e}")
                    time.sleep(0.5 * attempt)

            logger.error("Failed to initialize Dhan API client after retries")
            return False

        except Exception as e:
            logger.error(f"Failed to initialize Dhan API client: {e}")
            return False

    def generate_order_id(self) -> str:
        """Generate unique order ID."""
        self.order_counter += 1
        return f"ORD_{datetime.now().strftime('%Y%m%d')}_{self.order_counter}"

    def simulate_slippage(self, price: float, side: str, volatility: float = 50.0) -> tuple:
        """Simulate realistic market slippage."""
        base_slippage_pct = 0.05  # 0.05% base slippage
        volatility_factor = volatility / 100.0
        additional_slippage_pct = random.uniform(0, 0.15) * volatility_factor
        
        total_slippage_pct = base_slippage_pct + additional_slippage_pct
        slippage_amount = price * (total_slippage_pct / 100)
        
        # BUY orders get worse fills (higher prices), SELL orders get better fills (lower prices)
        if side == 'BUY':
            filled_price = price + slippage_amount
        else:  # SELL
            filled_price = price - slippage_amount
        
        return filled_price, slippage_amount

    def place_order_mock(self, symbol: str, side: str, quantity: int,
                        price: float, order_type: str) -> ExecutionResult:
        """Place order in mock mode (simulated execution)."""
        try:
            # Generate order ID
            order_id = self.generate_order_id()
            
            # Simulate execution delay (50-200ms)
            execution_delay = random.uniform(0.05, 0.2)
            time.sleep(execution_delay)
            
            # Simulate slippage for market orders
            if order_type == "MARKET":
                filled_price, slippage = self.simulate_slippage(price, side)
            else:
                filled_price = price
                slippage = 0.0
            
            # Create order object
            order = Order(
                order_id=order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_price=price,
                average_price=filled_price,
                order_type=order_type,
                status="FILLED",
                creation_time=datetime.now(),
                fill_time=datetime.now(),
                filled_quantity=quantity
            )
            
            # Store order
            self.orders[order_id] = order
            self.total_orders += 1
            self.successful_orders += 1
            self.total_slippage += slippage
            
            return ExecutionResult(
                success=True,
                order=order,
                slippage=slippage
            )

        except Exception as e:
            self.failed_orders += 1
            return ExecutionResult(
                success=False,
                order=None,
                error_message=str(e)
            )

    def place_order_live(self, symbol: str, side: str, quantity: int,
                        price: float, order_type: str) -> ExecutionResult:
        """Place order with real Dhan API (requires credentials and dhanhq library)."""
        try:
            # Check if client is initialized
            if not self.dhan_client:
                logger.error("Dhan client not initialized. Check credentials in config.py")
                return ExecutionResult(
                    success=False,
                    order=None,
                    error_message="Dhan API client not initialized"
                )
            
            # Generate order ID locally
            order_id = self.generate_order_id()
            
            # Prepare order parameters
            transaction_type = "BUY" if side == "BUY" else "SELL"
            
            # Map order types to Dhan API format
            order_type_map = {
                "MARKET": "MARKET",
                "LIMIT": "LIMIT",
                "SL": "SL",
                "SL-M": "SL-M"
            }
            
            dhan_order_type = order_type_map.get(order_type, "MARKET")
            
            logger.info(f"Placing {transaction_type} order: {symbol} | Qty: {quantity} | Price: ₹{price:.2f} | Type: {dhan_order_type}")
            
            # Place order using dhanhq client
            response = self.dhan_client.place_order(
                security_id=symbol,
                exchange_token=symbol,
                transaction_type=transaction_type,
                quantity=quantity,
                order_type=dhan_order_type,
                price=price,
                product_type="CNC"
            )
            
            # Handle response
            if response and response.get('status') == 'success':
                logger.info(f"✅ Order placed successfully: {order_id}")
                
                # Extract response data
                order_data = response.get('data', {})
                filled_price = float(order_data.get('price', price))
                
                # Create order object
                order = Order(
                    order_id=order_id,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    order_price=price,
                    average_price=filled_price,
                    order_type=dhan_order_type,
                    status="FILLED",
                    creation_time=datetime.now(),
                    fill_time=datetime.now(),
                    filled_quantity=quantity
                )
                
                self.orders[order_id] = order
                self.total_orders += 1
                self.successful_orders += 1
                
                slippage = abs(filled_price - price)
                self.total_slippage += slippage * quantity
                
                logger.info(f"   Filled Price: ₹{filled_price:.2f}")
                logger.info(f"   Slippage: ₹{slippage:.2f}")
                
                return ExecutionResult(
                    success=True,
                    order=order,
                    slippage=slippage * quantity
                )
            else:
                # Handle error response
                error_msg = response.get('message', 'Unknown error') if response else "No response from server"
                logger.error(f"❌ Order placement failed: {error_msg}")
                self.failed_orders += 1
                
                return ExecutionResult(
                    success=False,
                    order=None,
                    error_message=error_msg
                )

        except AttributeError as e:
            logger.error(f"Dhan client method error: {e}")
            logger.error("Ensure dhanhq library is properly installed: pip3 install dhanhq")
            self.failed_orders += 1
            return ExecutionResult(
                success=False,
                order=None,
                error_message="Dhan client method error - check dhanhq installation"
            )
        except requests.Timeout:
            logger.error("API request timeout - Dhan server not responding")
            self.failed_orders += 1
            return ExecutionResult(
                success=False,
                order=None,
                error_message="API request timeout"
            )
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            self.failed_orders += 1
            return ExecutionResult(
                success=False,
                order=None,
                error_message=str(e)
            )

    def place_order(self, symbol: str, side: str, quantity: int,
                   price: float, order_type: str = "MARKET") -> ExecutionResult:
        """
        Place order (routes to mock or live based on configuration).
        
        Args:
            symbol: Trading symbol (e.g., "NIFTY", "BANKNIFTY")
            side: "BUY" or "SELL"
            quantity: Number of contracts
            price: Order price in rupees
            order_type: "MARKET", "LIMIT", "SL", or "SL-M"
        
        Returns:
            ExecutionResult with order details or error message
        """
        if self.mock_mode:
            return self.place_order_mock(symbol, side, quantity, price, order_type)
        else:
            return self.place_order_live(symbol, side, quantity, price, order_type)

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            True if cancelled successfully, False otherwise
        """
        if self.mock_mode:
            return self._cancel_order_mock(order_id)
        else:
            return self._cancel_order_live(order_id)
    
    def _cancel_order_mock(self, order_id: str) -> bool:
        """Cancel order in mock mode."""
        if order_id not in self.orders:
            return False
        
        order = self.orders[order_id]
        if order.status in ["FILLED", "CANCELLED"]:
            return False
        
        order.status = "CANCELLED"
        logger.info(f"Order {order_id} cancelled (mock mode)")
        return True
    
    def _cancel_order_live(self, order_id: str) -> bool:
        """Cancel order via Dhan API."""
        try:
            if not self.dhan_client:
                logger.error("Dhan client not initialized")
                return False
            
            if order_id not in self.orders:
                logger.error(f"Order {order_id} not found")
                return False
            
            order = self.orders[order_id]
            if order.status in ["FILLED", "CANCELLED"]:
                logger.warning(f"Order {order_id} already {order.status}")
                return False
            
            # Cancel via Dhan API
            response = self.dhan_client.cancel_order(
                order_id=order_id,
                order_type="REGULAR"
            )
            
            if response and response.get('status') == 'success':
                order.status = "CANCELLED"
                logger.info(f"✅ Order {order_id} cancelled via Dhan API")
                return True
            else:
                error_msg = response.get('message', 'Unknown error') if response else "No response"
                logger.error(f"Failed to cancel order {order_id}: {error_msg}")
                return False
                
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return False

    def get_order_status(self, order_id: str) -> Optional[Order]:
        """
        Get status of an order.
        
        Args:
            order_id: Order ID to check
            
        Returns:
            Order object if found, None otherwise
        """
        if self.mock_mode:
            return self.orders.get(order_id)
        else:
            return self._get_order_status_live(order_id)
    
    def _get_order_status_live(self, order_id: str) -> Optional[Order]:
        """Get order status from Dhan API."""
        try:
            if not self.dhan_client:
                logger.error("Dhan client not initialized")
                return self.orders.get(order_id)
            
            # Get order status from API
            response = self.dhan_client.get_order_by_id(order_id=order_id)
            
            if response and response.get('status') == 'success':
                order_data = response.get('data', {})
                
                # Update local order object
                if order_id in self.orders:
                    order = self.orders[order_id]
                    order.status = order_data.get('orderStatus', order.status)
                    order.filled_quantity = int(order_data.get('filledQty', order.filled_quantity))
                    order.average_price = float(order_data.get('filledPrice', order.average_price))
                    
                    return order
                    
            return self.orders.get(order_id)
            
        except Exception as e:
            logger.warning(f"Could not fetch live order status: {e}")
            return self.orders.get(order_id)

    def get_execution_stats(self) -> Dict:
        """Get execution statistics."""
        success_rate = (self.successful_orders / self.total_orders * 100) if self.total_orders > 0 else 0
        avg_slippage = (self.total_slippage / self.successful_orders) if self.successful_orders > 0 else 0
        
        return {
            'total_orders': self.total_orders,
            'successful_orders': self.successful_orders,
            'failed_orders': self.failed_orders,
            'success_rate': success_rate,
            'total_slippage': self.total_slippage,
            'average_slippage': avg_slippage
        }


def get_execution_engine(mock_mode: bool = True) -> DhanExecutionEngine:
    """Factory function to get execution engine instance."""
    return DhanExecutionEngine(mock_mode=mock_mode)


if __name__ == "__main__":
    # Test execution engine
    engine = get_execution_engine(mock_mode=True)
    
    # Test multiple orders
    print("Testing execution engine...")
    
    for i in range(5):
        result = engine.place_order("NIFTY", "BUY", 10, 22000, "MARKET")
        if result.success:
            print(f"Order {i+1}: ₹{result.order.average_price:.2f} (Slippage: ₹{result.slippage:.2f})")
        else:
            print(f"Order {i+1} failed: {result.error_message}")
    
    # Print stats
    stats = engine.get_execution_stats()
    print(f"\nExecution Stats: {stats}")
