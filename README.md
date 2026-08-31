# VELAS-ws (Velas Engine v16)

An ultra-low latency unified candlestick & Order Flow rendering engine built in Python with PyQt6 and Win32 system optimizations.

---

## Overview

**VELAS-ws** is a high-performance, real-time market data visualizer and Order Flow visualization overlay for Bybit Linear Perps. Designed for low latency tape-to-glass rendering, it combines sub-millisecond OS timer resolution, DWM VBlank-synchronized pacing, redundant dual WebSocket feeds with dynamic leader/standby race arbitration, and advanced order flow analytics including Footprint charts, Visible Range Volume Profiles, Delta Divergences, Open Interest dynamics, and Absorption detection.

---

## Installation & Setup

Clone the repository and enter the directory:

```bash
git clone https://github.com/dezveda/velas-ws.git
cd velas-ws
```

---

## Requirements

- **Operating System**: Windows (10 or 11 required for Win32 AVRT, DWM, and high-precision timer APIs).
- **Python**: Python 3.9 or higher (64-bit recommended).

### Dependencies
- `PyQt6`
- `websocket-client`
- `orjson` *(optional, but recommended for ~3-5x faster JSON parsing)*

Install dependencies via pip:

```bash
pip install PyQt6 websocket-client orjson
```

### Diagnostic Logging
Runtime telemetry and diagnostic events are automatically recorded to a persistent rotating log file at `%LOCALAPPDATA%\Taperead\diag.log` (1 MB max, 3 backups).

---

## Key Features

### ⚡ Ultra-Low Latency Core & Win32 Platform Tuning
- **Sub-millisecond OS Timers**: Adjusts Windows system timer resolution to 0.5ms via `NtSetTimerResolution`, combined with `timeBeginPeriod(1)` for layered timer precision.
- **Thread & Process Prioritization**: Sets process priority to `HIGH_PRIORITY_CLASS` (0x80) and utilizes Win32 MMCSS (`Pro Audio`) thread scheduling for pacing and WebSocket threads.
- **VSync-Synchronized Pacing**: True VBlank synchronization via `VSyncPacer` -> `DwmFlush` avoiding software timer jitter.
- **CPython Switch Interval & GC Management**: Disables standard Garbage Collection during active trading, utilizing tactical collections during quiet market periods; switch interval tuned to 1ms to prevent tape-to-glass stalls.
- **Optimized Binary Ring Buffer**: Binary MPSC Ring Buffer with 64-byte slots for ultra-fast event ingestion.

### 📡 Redundant Multiplexed Network Engine
- **Dual WebSocket Feeds**: Simultaneous connections to Bybit Primary and Bytick Mirror endpoints.
- **Dynamic Leader/Standby Race Arbitration**: Dynamic race resolution for Kline, Ticker, and Trade data. Under steady state, an EWMA model transitions lagging feeds to standby to cut mutex contention while keeping health ping/pongs active.
- **RTT Clock Synchronization**: RTT Ratchet Baseline Offset measurement to accurately measure real wire latency corrected for local-server clock skew.

### 📊 Advanced Order Flow & Market Physics
- **Footprint Charting**: Dynamic footprint bucket ticks calculated based on price order-of-magnitude, rendering micro-structure Buy/Sell volumes directly on candles.
- **Visible Range Volume Profile**: Live calculation of VPOC (Volume Point of Control), VAH (Value Area High), and VAL (Value Area Low) across visible candles using a 70% Value Area threshold.
- **CVD & Delta Divergence Detection**: Real-time Cumulative Volume Delta tracking with trade-granularity bullish/bearish divergence identification.
- **Open Interest (OI) Analysis**: Live classification into Long/Short Build-up, Unwinding, and Covering regimes.
- **Absorption Detection**: Real-time flash absorption indicator in the telemetry panel when individual trade size exceeds threshold (10.0 units).
- **Market Kinetic Energy (KE)**: Kinetic energy modeling driving volatility-based dynamics (visual opacity morphing and sparklines active in Always-on-Top overlay mode).

### 🎨 Visuals & Customization
- **Multiple Rendering Styles**: HUD Glow, Thin Wireframe, and Solid Filled candle bodies with optional contrast halos.
- **Interactive Cursor Evasion**: Radial cursor evasion morphing when running as a top-level overlay.
- **Sci-Fi HUD & Classic Pill Styles**: Zero-allocation HSL text color mutation for the ticker HUD, paired with a pixel-velocity gradient trail (motion blur).
- **Context Menu & Configuration UI**: Seamless symbol switching (e.g., `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `BNBUSDT`, `DOGEUSDT`) and timeframe adjustments (`1m` to `1M`) at runtime without restarting, automatically reconnecting live WebSocket feeds and re-fetching historical candles.
- **Z-Order Modes**: Toggle between Always-on-Top overlay and Standard Mode (non-overlay).

---

## Usage

Launch the engine by running `Velas16.py`:

```bash
python Velas16.py
```

### Configuration Dialog
Upon launch, a settings dialog will appear allowing you to customize:
- **Ticker Symbol**: Default `BTCUSDT`.
- **Candle Style**: HUD Glow, Wireframe, or Filled.
- **Order Flow Modules**: Toggle Footprint levels and Visible Range Volume Profile.
- **Ticker Style**: Sci-Fi HUD or Classic Pill.
- **Z-Order Mode**: Always on Top or Standard Mode.
- **Timeframe**: Select from `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `12h`, `1d`, `1w`, or `1M`.

### Controls
- **Right Click**: Opens the context menu to quickly change symbols or timeframes (`1m` to `1M`) on the fly.
- **Cursor Proximity Tracking**: Interactive evasion zone morphs rendering elements around the mouse cursor when running in Always on Top mode.

---

## License

Distributed under the MIT License. See `LICENSE` for more information.
