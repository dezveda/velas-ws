<div align="center">

# ⚡ VELAS-ws (Velas Engine v16)

**Ultra-Low Latency Candlestick & Order Flow Visualization Engine**

*Built for Bybit Linear Perps with Python, PyQt6, and native Win32 kernel/hardware synchronization.*

---

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](LICENSE)
[![Python Version](https://img.shields.io/badge/Python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-0078D6?style=for-the-badge&logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![Framework](https://img.shields.io/badge/GUI-PyQt6-41CD52?style=for-the-badge&logo=qt&logoColor=white)](https://www.qt.io/)
[![GitHub Stars](https://img.shields.io/github/stars/dezveda/velas-ws?style=for-the-badge&color=gold)](https://github.com/dezveda/velas-ws/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/dezveda/velas-ws?style=for-the-badge&color=orange)](https://github.com/dezveda/velas-ws/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/dezveda/velas-ws?style=for-the-badge&color=red)](https://github.com/dezveda/velas-ws/issues)

[ Overview ](#-overview) • [ Key Features ](#-key-features) • [ Tech Stack ](#-tech-stack--requirements) • [ Installation ](#-installation--setup) • [ Usage ](#-usage) • [ License ](#-license)

</div>

---

> [!NOTE]
> **VELAS-ws** is engineered for high-frequency order flow analysis and ultra-fast tape-to-glass visual execution, utilizing sub-millisecond Windows OS timers and direct DWM VBlank synchronization.

---

## 📖 Overview

**VELAS-ws** is a high-performance, real-time market data visualizer and Order Flow overlay for Bybit Linear Perps. Designed specifically for low latency tape-to-glass rendering, it combines sub-millisecond OS timer resolution, DWM VBlank-synchronized pacing, redundant dual WebSocket feeds with dynamic leader/standby race arbitration, and advanced order flow analytics including:

- 📊 **Footprint Charts** (Micro-structure buy/sell volume)
- 📈 **Visible Range Volume Profiles** (VPOC, VAH, VAL)
- ⚡ **Delta Divergences & CVD**
- 🔄 **Open Interest Dynamics**
- 🛡️ **Flash Absorption Detection**

---

## 🚀 Key Features

### ⚡ Ultra-Low Latency Core & Win32 Platform Tuning
- **Sub-Millisecond OS Timers**: Adjusts Windows system timer resolution to 0.5ms via `NtSetTimerResolution`, combined with `timeBeginPeriod(1)` for layered timer precision.
- **Thread & Process Prioritization**: Sets process priority to `HIGH_PRIORITY_CLASS` (`0x80`) and utilizes Win32 MMCSS (`Pro Audio`) thread scheduling for pacing and WebSocket threads.
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

## 🛠️ Tech Stack & Requirements

| Component | Technology / Requirement | Description |
| :--- | :--- | :--- |
| **Language** | Python 3.9+ (64-bit) | Core execution engine |
| **OS Support** | Windows 10 / 11 | Required for Win32 AVRT, DWM VBlank, NtSetTimerResolution |
| **GUI Framework** | PyQt6 | Hardware-accelerated Qt widgets and QPainter rendering |
| **Networking** | websocket-client | Low-latency dual WS feed connection |
| **JSON Parser** | `orjson` | High-speed C-extension JSON parsing (~3-5x speedup) |

---

## 📦 Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/dezveda/velas-ws.git
   cd velas-ws
   ```

2. **Install dependencies**:
   ```bash
   pip install PyQt6 websocket-client orjson
   ```

> [!TIP]
> `orjson` is highly recommended as it speeds up JSON payload parsing by 3x-5x, reducing event loop processing latency.

### 📝 Diagnostic Logging
Runtime telemetry and diagnostic events are automatically recorded to a persistent rotating log file at:
`%LOCALAPPDATA%\Taperead\diag.log` (1 MB max, 3 backups).

---

## 🎮 Usage

Launch the engine by running `Velas16.py`:

```bash
python Velas16.py
```

### ⚙️ Configuration Dialog
Upon launch, a settings dialog allows you to customize runtime behavior:

- **Ticker Symbol**: Default `BTCUSDT`.
- **Candle Style**: HUD Glow, Wireframe, or Filled.
- **Order Flow Modules**: Toggle Footprint levels and Visible Range Volume Profile.
- **Ticker Style**: Sci-Fi HUD or Classic Pill.
- **Z-Order Mode**: Always on Top or Standard Mode.
- **Timeframe**: Select from `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `12h`, `1d`, `1w`, or `1M`.

### 🖱️ Interactive Controls
- **Right Click**: Opens the context menu to quickly change symbols or timeframes on the fly without interrupting WebSocket synchronization.
- **Cursor Proximity Tracking**: Interactive evasion zone morphs rendering elements around the mouse cursor when running in Always-on-Top mode.

---

## 📄 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for details.
