"""SENTINEL by Rohan Namde - Live Dashboard API - Real-time trading state streaming."""

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from datetime import datetime
import json
import logging
from collections import deque
import cProfile
import io
import pstats
import time as _time
from config import is_live_allowed, LIVE_APPROVAL_PHRASE

app = Flask(__name__)
CORS(app)

logger = logging.getLogger('SENTINEL by Rohan Namde - DASHBOARD')

# Trade activity log (last 150 entries)
TRADE_LOG = deque(maxlen=150)

# Global bot state (shared with main trading bot)
BOT_STATE = {
    "pnl": 0.0,
    "mode": "WAIT",
    "signal": "WAIT",
    "trades": [],
    "risk": "LOW",
    "positions": 0,
    "daily_trades": 0,
    "timestamp": datetime.now().isoformat(),
    "market_open": False,
    "nifty_price": 0.0,
    "execution_mode": "PAPER",
    # NEW - Intelligence metrics
    "astro_confidence": 0.5,
    "astro_window": "OFF_MARKET",
    "market_condition": "UNKNOWN",
    "trend": "UNKNOWN",
    "volatility": "NORMAL",
    "learning_status": {
        "trades_logged": 0,
        "learning_count": 0,
        "last_learning": None,
        "performance_rating": "FAIR"
    },
    "position_sizing": {
        "confidence_factor": 1.0,
        "volatility_factor": 1.0,
        "losing_streak": 0
    }
}

MAX_TRADES_DISPLAY = 50


def add_trade_log(event_type: str, message: str, color: str = "neutral"):
    """Add entry to trade activity log."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "type": event_type,  # ENTRY, EXIT, WIN, LOSS, SCAN, WARNING, ERROR
        "message": message,
        "color": color  # green, red, cyan, yellow
    }
    TRADE_LOG.append(entry)
    logger.info(f"[{event_type}] {message}")


@app.route('/state', methods=['GET'])
def get_state():
    """Get current bot state for real-time dashboard with live indicators and astro data."""
    from api_indicators import get_indicator_calculator
    from astro import get_astro_intelligence
    
    # Get live indicator data
    calc = get_indicator_calculator()
    indicators = calc.get_live_indicators(symbol="NIFTY", bars=50)
    
    # Get enhanced astro data
    astro = get_astro_intelligence()
    astro_data = astro.get_astro_compact()
    
    # Merge indicators and astro with bot state
    state_with_indicators = {
        **BOT_STATE,
        "indicators": indicators,
        "astro": astro_data,  # NEW: Rich astro data
        "trades_today": BOT_STATE.get("daily_trades", 0),  # Map for dashboard
        "session_pnl": BOT_STATE.get("pnl", 0.0),  # Map for dashboard
        "trades": list(TRADE_LOG) if TRADE_LOG else [],  # Include recent trades/activity
        "connected": True,  # Connection status flag for alerts
        "timestamp": datetime.now().isoformat()
    }
    
    return jsonify(state_with_indicators)


@app.route('/stats', methods=['GET'])
def get_stats():
    """Get detailed trading statistics including intelligence metrics."""
    from astro import get_astro_intelligence
    
    astro = get_astro_intelligence()
    astro_details = astro.get_astro_score_details()
    
    return jsonify({
        "total_trades": len(BOT_STATE["trades"]),
        "winning_trades": sum(1 for t in BOT_STATE["trades"] if t.get("pnl", 0) > 0),
        "losing_trades": sum(1 for t in BOT_STATE["trades"] if t.get("pnl", 0) < 0),
        "avg_pnl": sum(t.get("pnl", 0) for t in BOT_STATE["trades"]) / max(len(BOT_STATE["trades"]), 1),
        "win_rate": (sum(1 for t in BOT_STATE["trades"] if t.get("pnl", 0) > 0) / max(len(BOT_STATE["trades"]), 1)) * 100,
        "total_pnl": BOT_STATE["pnl"],
        "execution_mode": BOT_STATE["execution_mode"],
        # Intelligence metrics
        "astro_confidence": BOT_STATE.get("astro_confidence", 0.5),
        "astro_window": BOT_STATE.get("astro_window", "UNKNOWN"),
        "market_condition": BOT_STATE.get("market_condition", "UNKNOWN"),
        "trend": BOT_STATE.get("trend", "UNKNOWN"),
        "volatility": BOT_STATE.get("volatility", "NORMAL"),
        "learning_status": BOT_STATE.get("learning_status", {}),
        "position_sizing": BOT_STATE.get("position_sizing", {}),
        # NEW: Detailed astro intelligence
        "astro_details": astro_details,
        "astro_performance": astro.analyze_astro_performance()
    })


@app.route('/astro', methods=['GET'])
def get_astro():
    """Get detailed astro intelligence data."""
    from astro import get_astro_intelligence
    
    astro = get_astro_intelligence()
    
    return jsonify({
        "current": astro.get_astro_compact(),
        "detailed": astro.get_astro_score_details(),
        "performance": astro.analyze_astro_performance()
    })


@app.route('/trades', methods=['GET'])
def get_trades():
    """Get trade activity log."""
    return jsonify({
        "count": len(TRADE_LOG),
        "log": list(TRADE_LOG)
    })


@app.route('/command', methods=['POST'])
def command():
    """Handle bot commands from dashboard."""
    from flask import request
    
    try:
        data = request.get_json() or {}
        action = data.get('action', '').upper()
        
        if action == 'START':
            BOT_STATE['mode'] = 'SCALPING'
            BOT_STATE['signal'] = 'ACTIVE'
            logger.info("Dashboard command: START bot trading")
            return jsonify({"status": "success", "action": "START"})
        
        elif action == 'STOP':
            BOT_STATE['mode'] = 'WAIT'
            BOT_STATE['signal'] = 'WAIT'
            logger.info("Dashboard command: STOP bot trading")
            return jsonify({"status": "success", "action": "STOP"})
        
        elif action == 'SCAN':
            BOT_STATE['signal'] = 'MANUAL_SCAN'
            logger.info("Dashboard command: Manual SCAN triggered")
            return jsonify({"status": "success", "action": "SCAN", "message": "Scanning market conditions..."})
        
        elif action == 'EXIT':
            # KILL SWITCH - Close ALL positions immediately
            BOT_STATE['mode'] = 'WAIT'
            BOT_STATE['signal'] = 'EXIT_ALL'
            BOT_STATE['positions'] = 0
            BOT_STATE['trades'] = []
            logger.warning("🚨 KILL SWITCH ACTIVATED - Closing ALL positions immediately!")
            add_trade_log('WARNING', '🚨 KILL SWITCH activated - All positions closed', 'red')
            return jsonify({"status": "success", "action": "EXIT", "message": "Kill switch activated - all positions closed"})
        
        else:
            return jsonify({"status": "error", "message": f"Unknown action: {action}"}), 400
    
    except Exception as e:
        logger.error(f"Error processing command: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "bot_running": True
    })


@app.route('/profile', methods=['GET'])
def profile():
    """Run a short profiling session of indicator + astro computations and return pstats text.

    Query params:
      duration: seconds to run the sample loop (default 1)
    """
    try:
        duration = float(request.args.get('duration', '1'))
        duration = max(0.1, min(10.0, duration))
        pr = cProfile.Profile()

        def workload():
            from api_indicators import get_indicator_calculator
            from astro import get_astro_intelligence
            calc = get_indicator_calculator()
            astro = get_astro_intelligence()
            end = _time.time() + duration
            while _time.time() < end:
                calc.get_live_indicators(symbol="NIFTY", bars=50)
                astro.get_astro_compact()

        pr.enable()
        workload()
        pr.disable()

        s = io.StringIO()
        ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
        ps.print_stats(30)

        return s.getvalue(), 200, {'Content-Type': 'text/plain'}
    except Exception as e:
        logger.error(f"Profiling error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/', methods=['GET'])
def dashboard():
    """Serve the simple HTML dashboard."""
    try:
        with open('/Users/rohan/SENTINEL/dashboard_simple.html', 'r') as f:
            return f.read(), 200, {'Content-Type': 'text/html'}
    except Exception as e:
        return f"Error loading dashboard: {e}", 500


def update_bot_state(pnl: float = None, mode: str = None, signal: str = None, positions: int = None, 
                    daily_trades: int = None, nifty_price: float = None, 
                    execution_mode: str = None,
                    astro_confidence: float = None,
                    astro_window: str = None,
                    market_condition: str = None,
                    trend: str = None,
                    volatility: str = None,
                    learning_status: dict = None,
                    position_sizing: dict = None):
    """Update bot state from trading bot with all intelligence metrics."""
    global BOT_STATE
    
    # Core metrics
    if pnl is not None:
        BOT_STATE["pnl"] = pnl
    if mode is not None:
        BOT_STATE["mode"] = mode
    if signal is not None:
        BOT_STATE["signal"] = signal
    if positions is not None:
        BOT_STATE["positions"] = positions
    if daily_trades is not None:
        BOT_STATE["daily_trades"] = daily_trades
    if nifty_price is not None:
        BOT_STATE["nifty_price"] = nifty_price
    if execution_mode is not None:
        BOT_STATE["execution_mode"] = execution_mode
    
    # Intelligence metrics
    if astro_confidence is not None:
        BOT_STATE["astro_confidence"] = astro_confidence
    if astro_window is not None:
        BOT_STATE["astro_window"] = astro_window
    if market_condition is not None:
        BOT_STATE["market_condition"] = market_condition
    if trend is not None:
        BOT_STATE["trend"] = trend
    if volatility is not None:
        BOT_STATE["volatility"] = volatility
    if learning_status is not None:
        BOT_STATE["learning_status"] = learning_status
    if position_sizing is not None:
        BOT_STATE["position_sizing"] = position_sizing
    
    BOT_STATE["timestamp"] = datetime.now().isoformat()
    
    # Determine risk level based on PnL
    if pnl is not None:
        if pnl < -1000:
            BOT_STATE["risk"] = "CRITICAL"
        elif pnl < -500:
            BOT_STATE["risk"] = "HIGH"
        elif pnl < 0:
            BOT_STATE["risk"] = "MEDIUM"
        else:
            BOT_STATE["risk"] = "LOW"


def add_trade(timestamp: str, symbol: str, signal: str, price: float, pnl: float):
    """Add trade to dashboard."""
    global BOT_STATE
    
    trade = {
        "time": timestamp,
        "symbol": symbol,
        "signal": signal,
        "price": price,
        "pnl": pnl
    }
    
    BOT_STATE["trades"].insert(0, trade)  # Add to front
    
    # Keep only last N trades
    BOT_STATE["trades"] = BOT_STATE["trades"][:MAX_TRADES_DISPLAY]


@app.route('/update_state', methods=['POST'])
def update_state_endpoint():
    """Receive bot state updates from the trading bot."""
    from flask import request
    
    try:
        data = request.get_json()
        
        update_bot_state(
            pnl=data.get('pnl'),
            mode=data.get('mode'),
            signal=data.get('signal'),
            positions=data.get('positions'),
            daily_trades=data.get('daily_trades'),
            nifty_price=data.get('nifty_price'),
            execution_mode=data.get('execution_mode'),
            astro_confidence=data.get('astro_confidence'),
            astro_window=data.get('astro_window'),
            market_condition=data.get('market_condition'),
            trend=data.get('trend'),
            volatility=data.get('volatility'),
            learning_status=data.get('learning_status'),
            position_sizing=data.get('position_sizing')
        )
        
        return jsonify({"status": "updated", "timestamp": datetime.now().isoformat()}), 200
    except Exception as e:
        logger.error(f"Error updating state: {e}")
        return jsonify({"error": str(e)}), 400


@app.route('/enable_live', methods=['POST'])
def enable_live_endpoint():
    """Attempt to enable live mode for dashboard/API interactions.

    This endpoint verifies the provided approval phrase against the configured
    `LIVE_APPROVAL_PHRASE`. It does NOT change environment variables; it sets
    an in-memory confirmation flag for the running API which the operator can
    use as confirmation. The trading process itself must still be started with
    live enabled (ENV + startup options) to actually place live orders.
    """
    try:
        data = request.get_json() or {}
        phrase = data.get('approval_phrase', '')

        if not LIVE_APPROVAL_PHRASE:
            return jsonify({"status": "error", "message": "Live approval phrase not configured on server"}), 400

        if phrase == LIVE_APPROVAL_PHRASE:
            BOT_STATE['live_confirmed'] = True
            logger.warning("Live mode confirmed via API endpoint (in-memory only). Ensure process started with ENABLE_LIVE=1 for full effect.")
            return jsonify({"status": "success", "message": "Live approved in API (in-memory)"}), 200
        else:
            return jsonify({"status": "error", "message": "Approval phrase mismatch"}), 403
    except Exception as e:
        logger.error(f"Error enabling live: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/add_trade', methods=['POST'])
def add_trade_endpoint():
    """Receive completed trade from trading bot."""
    from flask import request
    
    try:
        data = request.get_json()
        
        add_trade(
            timestamp=data.get('timestamp'),
            symbol=data.get('symbol'),
            signal=data.get('signal'),
            price=data.get('price'),
            pnl=data.get('pnl')
        )
        
        return jsonify({"status": "trade_added", "timestamp": datetime.now().isoformat()}), 200
    except Exception as e:
        logger.error(f"Error adding trade: {e}")
        return jsonify({"error": str(e)}), 400


if __name__ == '__main__':
    # Test mode
    print("🚀 Starting SENTINEL by Rohan Namde - Dashboard API...")
    print("📊 Dashboard available at: http://localhost:5000")
    print("📡 API endpoint: http://localhost:5000/state")
    
    # Demo data
    update_bot_state(
        pnl=1250.50,
        mode="SCALP",
        signal="BUY",
        positions=2,
        daily_trades=5,
        nifty_price=22145.50,
        execution_mode="PAPER"
    )
    
    add_trade("09:45:30", "NIFTY 22150 CE", "BUY", 22145.50, 250.00)
    add_trade("09:50:15", "NIFTY 22200 PE", "SELL", 22100.00, -150.00)
    
    # Try to start on port 5000, fallback to 5001, 5002, etc.
    import socket
    port = 5000
    for  attempt in range(5):
        try:
            # Test if port is available
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            result = s.connect_ex(('127.0.0.1', port))
            s.close()
            if result != 0:
                # Port is available
                print(f"✅ Starting API on port {port}")
                app.run(debug=False, host='0.0.0.0', port=port)
                break
            else:
                print(f"Port {port} is in use, trying {port + 1}...")
                port += 1
        except Exception as e:
            print(f"Error checking port {port}: {e}, trying next port...")
            port += 1
    else:
        # If all ports tried are in use, just try to force start
        print("⚠️  Could not find available port, attempting to start anyway on 5000...")
        app.run(debug=False, host='0.0.0.0', port=5000)
