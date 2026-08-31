# -*- coding: utf-8 -*-
r"""
Velas16.py -- Motor Unificado de Velas & Order Flow Ultra-Low Latency
========================================================================================
Consolidación Quirúrgica Completa (Velas8 + Velas9 + Taperead10 + Velas10/11):

  [Núcleo Win32 & Baja Latencia]
  - Timer del SO ajustado a 0.5ms (NtSetTimerResolution) + timeBeginPeriod(1).
  - Priorización de hilos Win32 MMCSS "Pro Audio" + HIGH_PRIORITY_CLASS (0x80), aplicada
    tanto al proceso (SetPriorityClass) como al hilo de pacing y a los hilos WebSocket.
  - Reloj de render único, sincronizado a VBlank real (VSyncPacer -> DwmFlush), sin
    temporizador de software paralelo.
  - Control táctico oportunista de GC (gc.disable() con llamadas a gc.collect en calma).
  - Socket options TCP_NODELAY en todas las conexiones físicas.
  - Logging rotativo persistente en %LOCALAPPDATA%\Taperead\diag.log.
  - Footprint con tick dinámico por orden de magnitud del precio (fidelidad multi-símbolo).

  [Arquitectura de Red Redundante]
  - Doble hilo WebSocket (Bybit Primario + Bytick Espejo) multiplexado.
  - Arbitraje por carrera para Kline, Ticker y Trade,
    serializado por un único arbitration_lock.
  - Degradación dinámica líder/standby: en régimen estacionario, tras un
    EWMA de victorias sostenido, el feed rezagado deja de parsear y
    despachar los tópicos de datos (su socket sigue vivo para ping/pong
    de salud), reduciendo a la mitad la contención sobre arbitration_lock.
    Reactivación instantánea ante staleness del líder o reconexión.
  - Ring Buffer binario de 64 bytes por slot (MPSCRingBuffer).
  - RTT Ratchet Baseline Offset con guardias contra saltos de reloj de pared.

  [Motor Físico y Visualización (Velas8/Velas9)]
  - Evasión interactiva del cursor (Morphing de radio 180px).
  - Física de resortes con sub-stepping de paso fijo para C, H, L (k_spring=0.18, damping=0.85).
  - Energía Cinética (KE) con decaimiento por vida media adaptativo por Timeframe.
  - Ticker Sci-Fi HUD con mutación de color HSL in-place (Zero-Allocation) y gradiente de escaneo.
  - Ticker Classic Pill original preservado.
  - Efecto Motion Blur por velocidad de píxeles sobre la línea de precio activo.
  - Trazado de línea de chispa (Sparkline Path) cuando morph < 1.0.
  - Menú contextual de clic derecho (Menú desplegable) para cambio de Símbolo y Timeframe sin reiniciar.
  - Arco temporizador de cuenta regresiva de la vela en curso.

  [Módulos de Order Flow Seleccionados (Rondas 1, 2 y 3)]
  - Footprint Charting: Micro-estructura Buy/Sell por nivel de precio dentro de la vela.
  - Perfil de Volumen de Rango Visible (Visible Range Volume Profile): Cálculo dinámico de VPOC (Point of Control), VAH y VAL (70% Value Area).
  - Detector de Divergencias Delta (CVD): Identificación en tiempo real de divergencias alcistas/bajistas.
  - Panel de Open Interest (OI) & Cuadrantes: Clasificación en vivo de Long/Short Build-up/Unwinding/Covering.
  - Detección de Absorción: indicador de absorción en panel de telemetría para trades superiores al umbral (10.0 unidades).
========================================================================================
"""

import sys
import time
import json
import threading
import ssl
import socket
import ctypes
import gc
import signal
import os
import logging
import http.client
import math
import struct
import random
from logging.handlers import RotatingFileHandler
from collections import deque, defaultdict
from ctypes import c_ulong, c_bool, c_void_p

import websocket  # pip install websocket-client

# Parser JSON acelerado (opcional): orjson es ~3-5x más rápido que json stdlib
# para los payloads pequeños de Bybit. Si no está instalado, se degrada de
# forma transparente al parser estándar sin alterar ningún comportamiento.
try:
    import orjson
    _json_loads = orjson.loads
except ImportError:
    _json_loads = json.loads
from PyQt6.QtWidgets import (QApplication, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel, QLineEdit, QComboBox, QRadioButton,
                             QCheckBox, QButtonGroup, QMenu)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPointF, QRectF, QThread
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QCursor, QPainterPath, QPixmap, QLinearGradient

# -------------------------------------------------------------------------
# LOGGER DE DIAGNÓSTICO PERSISTENTE
# -------------------------------------------------------------------------
_diag_log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Taperead")
try:
    os.makedirs(_diag_log_dir, exist_ok=True)
except Exception:
    _diag_log_dir = os.path.expanduser("~")

diag_logger = logging.getLogger("velas.diag")
diag_logger.setLevel(logging.INFO)
diag_logger.propagate = False
try:
    _diag_handler = RotatingFileHandler(
        os.path.join(_diag_log_dir, "diag.log"),
        maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    _diag_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    diag_logger.addHandler(_diag_handler)
except Exception:
    diag_logger.addHandler(logging.NullHandler())

# -------------------------------------------------------------------------
# GUARD DE PLATAFORMA Y BINDINGS WIN32
# -------------------------------------------------------------------------
if sys.platform != "win32":
    print("ERROR: Velas16 requiere Windows (sincronización DWM/AVRT/Waitable Timers via ctypes).")
    sys.exit(1)

gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

HIGH_PRIORITY_CLASS = 0x00000080

GetCurrentProcess = getattr(kernel32, "GetCurrentProcess", None)
if GetCurrentProcess:
    GetCurrentProcess.argtypes = []
    GetCurrentProcess.restype = c_void_p

SetPriorityClass = getattr(kernel32, "SetPriorityClass", None)
if SetPriorityClass:
    SetPriorityClass.argtypes = [c_void_p, c_ulong]
    SetPriorityClass.restype = c_bool

DwmFlush = getattr(ctypes.windll.dwmapi, "DwmFlush", None)
if DwmFlush:
    DwmFlush.argtypes = []
    DwmFlush.restype = ctypes.c_long

GetSystemTimePreciseAsFileTime = getattr(kernel32, "GetSystemTimePreciseAsFileTime", None)
if GetSystemTimePreciseAsFileTime:
    GetSystemTimePreciseAsFileTime.argtypes = [ctypes.c_void_p]
    GetSystemTimePreciseAsFileTime.restype = None

_avrt = None
try:
    _avrt = ctypes.windll.avrt
    _avrt.AvSetMmThreadCharacteristicsW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    _avrt.AvSetMmThreadCharacteristicsW.restype = ctypes.c_void_p
    _avrt.AvSetMmThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _avrt.AvSetMmThreadPriority.restype = c_bool
except Exception:
    pass

def apply_mmcss_priority():
    if _avrt:
        task_index = ctypes.c_ulong(0)
        handle = _avrt.AvSetMmThreadCharacteristicsW("Pro Audio", ctypes.byref(task_index))
        if handle:
            _avrt.AvSetMmThreadPriority(handle, 2)  # AVRT_PRIORITY_CRITICAL = 2

# Resolución de timers del SO a 0.5ms (5000 * 100ns)
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
    ntdll = ctypes.windll.ntdll
    NtSetTimerResolution = ntdll.NtSetTimerResolution
    NtSetTimerResolution.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.POINTER(ctypes.c_ulong)]
    NtSetTimerResolution.restype = ctypes.c_long
    _current_res = ctypes.c_ulong()
    NtSetTimerResolution(5000, True, ctypes.byref(_current_res))
except Exception:
    pass

# Control Táctico Oportunista de GC & Switch Interval
# NOTA: el switch interval por defecto de CPython es 0.005s (5ms). Un valor de
# 0.05s (10x mayor) permitía que el hilo que sostiene el GIL (p.ej. el hilo
# principal durante paintEvent) lo retuviera hasta 50ms antes de cederlo a los
# hilos WebSocket, inyectando ese stall directamente en la ruta tape-to-glass.
# Se reduce a 0.001s (1ms) para minimizar el peor caso de latencia de ingesta;
# el overhead adicional de cambio de contexto es despreciable con solo 4 hilos activos.
gc.disable()
sys.setswitchinterval(0.001)

# Prioridad de proceso alta (antes declarada como constante pero nunca aplicada)
if GetCurrentProcess and SetPriorityClass:
    try:
        SetPriorityClass(GetCurrentProcess(), HIGH_PRIORITY_CLASS)
    except Exception:
        pass

_file_time = ctypes.c_ulonglong()
def get_precise_time_ms():
    if GetSystemTimePreciseAsFileTime:
        GetSystemTimePreciseAsFileTime(ctypes.byref(_file_time))
        return (_file_time.value - 116444736000000000) / 10000.0
    return time.time() * 1000.0

# -------------------------------------------------------------------------
# MPSC RING BUFFER PARA DATOS Y TRADES
# -------------------------------------------------------------------------
SLOT_SIZE = 64             # bytes por slot
RB_SLOTS = 8192            # capacidad de slots
RB_MASK = RB_SLOTS - 1
# Layout: type(B), t(q), o(d), h(d), l(d), c(d), v(d), side(b), extra(d)
RB_FMT = "<Bqdddddbd"

class MPSCRingBuffer:
    __slots__ = ('buf', 'head', 'tail', 'dropped')
    def __init__(self):
        self.buf = bytearray(SLOT_SIZE * RB_SLOTS)
        self.head = ctypes.c_ulong(0)
        self.tail = ctypes.c_ulong(0)
        self.dropped = 0

    def write_slot(self, slot_type, t, o, h, l, c, v, side=0, extra=0.0):
        h_val = self.head.value
        t_val = self.tail.value
        next_h = (h_val + 1) & RB_MASK
        if next_h == t_val:
            self.dropped += 1
            return False
        offset = h_val * SLOT_SIZE
        struct.pack_into(RB_FMT, self.buf, offset, slot_type, int(t), float(o), float(h), float(l), float(c), float(v), int(side), float(extra))
        self.head.value = next_h
        return True

    def read_slot(self):
        t_val = self.tail.value
        if t_val == self.head.value:
            return None
        offset = t_val * SLOT_SIZE
        slot = struct.unpack_from(RB_FMT, self.buf, offset)
        self.tail.value = (t_val + 1) & RB_MASK
        return slot

    def drain_all(self):
        slots = []
        while True:
            s = self.read_slot()
            if s is None:
                break
            slots.append(s)
        return slots

# -------------------------------------------------------------------------
# ESTRUCTURAS DE DATOS DE VELA CON FOOTPRINT
# -------------------------------------------------------------------------
def _dynamic_footprint_tick(price):
    """Ancho de bucket de footprint proporcional al orden de magnitud del precio.
    Calibrado para reproducir exactamente el tick fijo de 0.50 original en el
    rango de precio de BTCUSDT (~10^4), pero sin colapsar símbolos de menor
    precio (ETH, SOL, BNB, DOGE, o cualquier símbolo futuro) a un único nivel."""
    if price <= 0:
        return 0.0001
    magnitude = 10 ** math.floor(math.log10(price))
    return magnitude * 0.00005

class Kline:
    __slots__ = ['t', 'o', 'h', 'l', 'c', 'v', 'trades', 'footprint', 'delta']
    def __init__(self, t, o, h, l, c, v, trades=0):
        self.t = t; self.o = o; self.h = h; self.l = l; self.c = c; self.v = v; self.trades = trades
        self.footprint = defaultdict(lambda: [0.0, 0.0])  # price_level -> [buy_vol, sell_vol]
        self.delta = 0.0

    def update(self, t, o, h, l, c, v, trades=0):
        self.t = t; self.o = o; self.h = h; self.l = l; self.c = c; self.v = v; self.trades = trades

    def add_trade(self, price, qty, side):
        step = _dynamic_footprint_tick(price)  # Tick size proporcional al precio del símbolo activo
        level = round(price / step) * step
        if side == 0:  # Buy
            self.footprint[level][0] += qty
            self.delta += qty
        else:          # Sell
            self.footprint[level][1] += qty
            self.delta -= qty

BYBIT_INTERVAL_MAP = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W", "1M": "M",
}

# Duración en segundos por timeframe, usada por el arco de cuenta regresiva
# de la vela en curso. "1M" usa una aproximación de 30 días (no se persigue
# exactitud calendárica exacta; el arco es indicativo, no un cronómetro legal).
TIMEFRAME_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
    "1d": 86400, "1w": 604800, "1M": 2592000,
}

# -------------------------------------------------------------------------
# ENERGÍA CINÉTICA DE MERCADO (volatilidad -> opacidad/morph/sparkline)
# El flujo de ticks (kline/ticker/trade) llega a la misma frecuencia sin
# importar el timeframe de agregación de velas seleccionado en la UI; por
# tanto la sensibilidad y el decaimiento de la energía son constantes
# únicas, deliberadamente independientes de self.timeframe.
# -------------------------------------------------------------------------
KE_INPUT_SENSITIVITY = 0.5   # divisor de price_delta_pct -> incremento de KE
KE_DECAY_HALF_LIFE_S = 5.0   # vida media de decaimiento exponencial de KE, en segundos

# -------------------------------------------------------------------------
# DIÁLOGO DE CONFIGURACIÓN (PyQt6)
# -------------------------------------------------------------------------
class ConfigDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Velas16 - Config Engine")
        self.setFixedSize(320, 640)
        self.setStyleSheet("background-color: #1a1a1a; color: #eeeeee;")
        self.selection = {
            "value": "5m", "mode": "top", "symbol": "BTCUSDT", "style": "glow",
            "ticker_style": "hud", "show_footprint": True, "show_profile": True, "halo": True
        }

        layout = QVBoxLayout()

        lbl_sym = QLabel("TICKER SYMBOL")
        lbl_sym.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        lbl_sym.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_sym)

        self.sym_input = QLineEdit("BTCUSDT")
        self.sym_input.setFont(QFont("Consolas", 12))
        self.sym_input.setStyleSheet("background-color: #333; border: 1px solid #555; padding: 5px;")
        self.sym_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.sym_input)

        lbl_style = QLabel("CANDLE STYLE")
        lbl_style.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        lbl_style.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_style)

        style_layout = QVBoxLayout()
        self.radio_glow = QRadioButton("HUD Glow (Original)")
        self.radio_wireframe = QRadioButton("Wireframe (Thin)")
        self.radio_filled = QRadioButton("Filled (Solid)")
        self.radio_glow.setChecked(True)

        self.style_group = QButtonGroup(self)
        self.style_group.addButton(self.radio_glow)
        self.style_group.addButton(self.radio_wireframe)
        self.style_group.addButton(self.radio_filled)

        style_layout.addWidget(self.radio_glow)
        style_layout.addWidget(self.radio_wireframe)
        style_layout.addWidget(self.radio_filled)

        self.check_halo = QCheckBox("Enable Contrast Halo (Shadow)")
        self.check_halo.setChecked(True)
        self.check_halo.setStyleSheet("QCheckBox { margin-top: 5px; color: #aaa; font-weight: bold; }")
        style_layout.addWidget(self.check_halo)

        layout.addLayout(style_layout)

        lbl_of = QLabel("ORDER FLOW MODULES")
        lbl_of.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        lbl_of.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_of)

        self.check_footprint = QCheckBox("Enable Footprint Levels")
        self.check_footprint.setChecked(True)
        self.check_profile = QCheckBox("Enable Visible Range Volume Profile (VPOC/VAH/VAL)")
        self.check_profile.setChecked(True)
        layout.addWidget(self.check_footprint)
        layout.addWidget(self.check_profile)

        lbl_ticker = QLabel("TICKER STYLE")
        lbl_ticker.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        lbl_ticker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_ticker)

        ticker_layout = QVBoxLayout()
        self.radio_ticker_hud = QRadioButton("Sci-Fi HUD (Advanced)")
        self.radio_ticker_classic = QRadioButton("Classic Pill (Original)")
        self.radio_ticker_hud.setChecked(True)

        self.ticker_group = QButtonGroup(self)
        self.ticker_group.addButton(self.radio_ticker_hud)
        self.ticker_group.addButton(self.radio_ticker_classic)

        ticker_layout.addWidget(self.radio_ticker_hud)
        ticker_layout.addWidget(self.radio_ticker_classic)
        layout.addLayout(ticker_layout)

        lbl_mode = QLabel("Z-ORDER MODE")
        lbl_mode.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        lbl_mode.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_mode)

        mode_layout = QHBoxLayout()
        self.btn_top = QPushButton("Always on Top")
        self.btn_bottom = QPushButton("Standard Mode")

        for btn in [self.btn_top, self.btn_bottom]:
            btn.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
            btn.setFixedHeight(30)

        self.btn_top.clicked.connect(lambda: self.set_mode("top"))
        self.btn_bottom.clicked.connect(lambda: self.set_mode("bottom"))
        mode_layout.addWidget(self.btn_top)
        mode_layout.addWidget(self.btn_bottom)
        layout.addLayout(mode_layout)

        self.update_mode_styles()

        lbl_tf = QLabel("SELECT TIMEFRAME")
        lbl_tf.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        lbl_tf.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_tf)

        self.tf_combo = QComboBox()
        options = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w", "1M"]
        self.tf_combo.addItems(options)
        self.tf_combo.setCurrentText("5m")
        self.tf_combo.setFont(QFont("Consolas", 11))
        self.tf_combo.setStyleSheet("QComboBox { background-color: #333; border: 1px solid #555; padding: 5px; }")
        layout.addWidget(self.tf_combo)

        btn_start = QPushButton("START VELAS16 ENGINE")
        btn_start.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        btn_start.setStyleSheet("background-color: #00E676; color: #000; border: none; padding: 10px; margin-top: 10px;")
        btn_start.clicked.connect(self.start_app)
        layout.addWidget(btn_start)

        self.setLayout(layout)

    def update_mode_styles(self):
        active = "background-color: #00E676; color: #000000; border: none; padding: 5px;"
        inactive = "background-color: #333333; color: #eeeeee; border: none; padding: 5px;"
        self.btn_top.setStyleSheet(active if self.selection["mode"] == "top" else inactive)
        self.btn_bottom.setStyleSheet(active if self.selection["mode"] == "bottom" else inactive)

    def set_mode(self, m):
        self.selection["mode"] = m
        self.update_mode_styles()

    def start_app(self):
        self.selection["value"] = self.tf_combo.currentText()
        self.selection["symbol"] = self.sym_input.text().strip().upper() or "BTCUSDT"
        if self.radio_glow.isChecked(): self.selection["style"] = "glow"
        elif self.radio_wireframe.isChecked(): self.selection["style"] = "wireframe"
        else: self.selection["style"] = "filled"

        self.selection["halo"] = self.check_halo.isChecked()
        self.selection["ticker_style"] = "hud" if self.radio_ticker_hud.isChecked() else "classic"
        self.selection["show_footprint"] = self.check_footprint.isChecked()
        self.selection["show_profile"] = self.check_profile.isChecked()
        self.accept()

# -------------------------------------------------------------------------
# RTT RATCHET BASELINE OFFSET
# Estima el desfase de reloj (clock skew) entre esta máquina y los servidores
# de Bybit, para que last_wire_latency_ms mida latencia de red real y no una
# mezcla de latencia + desfase de reloj local. Técnica: se muestrea el RTT de
# los ping/pong de aplicación ya existentes (cada ~20s) y se conserva el
# mínimo sobre una ventana móvil (el mínimo es el mejor estimador del delay
# de propagación puro, libre de colas). offset = t_local_recv - t_server -
# RTT_min/2, asumiendo latencia simétrica ida/vuelta (aproximación estándar
# de sincronización tipo NTP). Guardia: si la muestra de offset difiere
# abruptamente de la última aceptada (>2s), se descarta como probable salto
# de reloj de pared (suspensión del SO, ajuste manual de hora) en vez de
# contaminar el ratchet.
# -------------------------------------------------------------------------
class ClockSync:
    __slots__ = ('_lock', '_rtt_window', '_rtt_min_ms', '_last_offset_ms')
    _RTT_WINDOW_SIZE = 20
    _JUMP_GUARD_MS = 2000.0

    def __init__(self):
        self._lock = threading.Lock()
        self._rtt_window = deque(maxlen=self._RTT_WINDOW_SIZE)
        self._rtt_min_ms = None
        self._last_offset_ms = 0.0

    def record_rtt(self, rtt_ms):
        if rtt_ms <= 0 or rtt_ms > 5000:
            return
        with self._lock:
            self._rtt_window.append(rtt_ms)
            self._rtt_min_ms = min(self._rtt_window)

    def corrected_latency_ms(self, local_recv_ms, server_ts_ms):
        """Devuelve la latencia de red estimada para un mensaje, corrigiendo
        el desfase de reloj local-servidor. Si aún no hay muestras de RTT,
        degrada al cálculo naive (comportamiento previo)."""
        raw_offset_ms = local_recv_ms - server_ts_ms
        with self._lock:
            rtt_min = self._rtt_min_ms
            if rtt_min is None:
                return max(0.0, raw_offset_ms)
            skew_ms = raw_offset_ms - (rtt_min / 2.0)
            if abs(skew_ms - self._last_offset_ms) > self._JUMP_GUARD_MS and self._last_offset_ms != 0.0:
                return max(0.0, raw_offset_ms)  # muestra descartada del ratchet, se usa fallback naive
            self._last_offset_ms = skew_ms
            corrected = raw_offset_ms - skew_ms
        return max(0.0, corrected)

# -------------------------------------------------------------------------
# HILO DE MERCADO WEBSOCKET REDUNDANTE MULTIPLEXADO (Bybit + Bytick)
# -------------------------------------------------------------------------
class BybitRedundantMarketDataThread(QThread):
    history_ready = pyqtSignal(list)
    connection_status = pyqtSignal(bool)

    def __init__(self, symbol, timeframe, ring_buffer):
        super().__init__()
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.bybit_interval = BYBIT_INTERVAL_MAP.get(timeframe, "5")
        self.ring_buffer = ring_buffer
        self.running = True
        self._force_reconnect = False

        self.ws_primary = None
        self.ws_mirror = None

        self.reconnects = 0
        self.last_kline_ts = -1
        self.last_ticker_ts = -1
        self.last_trade_id = ""
        self.arbitration_lock = threading.Lock()
        self.clock_sync = ClockSync()
        self._last_ping_sent_ms = {"primary": 0.0, "mirror": 0.0}

        # ---------------------------------------------------------------
        # DEGRADACIÓN DINÁMICA LÍDER/STANDBY EN LA CARRERA DE FEEDS
        # En régimen estacionario, con ambos feeds sanos, casi siempre gana
        # la carrera el mismo lado (menor RTT estructural en ese momento).
        # El lado perdedor de todas formas paga _json_loads + adquisición
        # de arbitration_lock + struct.pack_into en CADA mensaje, sin
        # aportar ninguna ventaja marginal salvo la detección de
        # degradación del líder. Este bloque mantiene un EWMA de "victorias"
        # por feed; cuando un lado gana de forma sostenida, el otro pasa a
        # "standby": su socket sigue vivo y sigue respondiendo a ping/pong
        # (vigilancia de salud), pero deja de parsear y despachar los
        # tópicos de datos (kline/ticker/trade). Se reactiva de inmediato
        # si el líder deja de refrescar (staleness) o si reconnects sube.
        # ---------------------------------------------------------------
        self.feed_stats_lock = threading.Lock()
        self.win_ewma = {"primary": 0.5, "mirror": 0.5}
        self.EWMA_ALPHA = 0.05
        self.STANDBY_ENTER_WIN_RATE = 0.85   # win-rate sostenido para degradar al otro feed
        self.STANDBY_EXIT_WIN_RATE = 0.60    # si el líder decae por debajo de esto, se reactiva el standby
        self.STANDBY_STALENESS_MS = 3000.0   # si el líder no refresca en este tiempo, reactivación inmediata
        self.standby_tag = None              # None => ambos feeds activos (arranque / sin líder claro)
        self._leader_last_accept_ms = 0.0
        self._reconnects_at_standby_entry = 0

    def _record_race_outcome(self, source_tag, won):
        """Actualiza el EWMA de victorias del feed y decide si promueve o
        degrada un lado a standby. Se llama fuera de arbitration_lock para
        no extender la sección crítica más de lo necesario."""
        other_tag = "mirror" if source_tag == "primary" else "primary"
        with self.feed_stats_lock:
            alpha = self.EWMA_ALPHA
            sample = 1.0 if won else 0.0
            self.win_ewma[source_tag] = (1.0 - alpha) * self.win_ewma[source_tag] + alpha * sample
            self.win_ewma[other_tag] = 1.0 - self.win_ewma[source_tag]

            if won:
                self._leader_last_accept_ms = get_precise_time_ms()

            if self.standby_tag is None:
                if self.win_ewma[source_tag] >= self.STANDBY_ENTER_WIN_RATE:
                    self.standby_tag = other_tag
                    self._reconnects_at_standby_entry = self.reconnects
            elif self.standby_tag == other_tag:
                # source_tag es el líder actual; si decae, reactivar al standby
                if self.win_ewma[source_tag] < self.STANDBY_EXIT_WIN_RATE:
                    self._reactivate_standby_locked()

    def _reactivate_standby_locked(self):
        """Debe llamarse con feed_stats_lock ya adquirido (o desde un punto
        donde una carrera de reactivación benigna es aceptable)."""
        self.standby_tag = None
        self.win_ewma["primary"] = 0.5
        self.win_ewma["mirror"] = 0.5

    def _check_leader_staleness_and_reconnects(self):
        """Guardia de reactivación instantánea: si el líder deja de refrescar
        por más de STANDBY_STALENESS_MS, o si el contador global de
        reconnects avanzó desde que se entró en standby (indicio de que
        justo el líder es el que se está degradando), se reactiva el feed
        en standby a procesamiento completo inmediatamente."""
        if self.standby_tag is None:
            return
        now_ms = get_precise_time_ms()
        stale = (now_ms - self._leader_last_accept_ms) > self.STANDBY_STALENESS_MS
        reconnected = self.reconnects > self._reconnects_at_standby_entry
        if stale or reconnected:
            with self.feed_stats_lock:
                self._reactivate_standby_locked()

    def set_symbol_timeframe(self, symbol, timeframe):
        with self.arbitration_lock:
            self.symbol = symbol.upper()
            self.timeframe = timeframe
            self.bybit_interval = BYBIT_INTERVAL_MAP.get(timeframe, "5")
            self.last_kline_ts = -1
            self.last_ticker_ts = -1
            self.last_trade_id = ""
            self._force_reconnect = True
        with self.feed_stats_lock:
            self._reactivate_standby_locked()
        self._fetch_history()

        # Force active WebSockets to close so worker threads re-subscribe to the new symbol/timeframe
        for ws in (self.ws_primary, self.ws_mirror):
            if ws:
                try: ws.close()
                except Exception: pass

    def _fetch_history(self):
        ctx = ssl.create_default_context()
        for attempt in range(3):
            try:
                hist_url = f"/v5/market/kline?category=linear&symbol={self.symbol}&interval={self.bybit_interval}&limit=150"
                conn = http.client.HTTPSConnection("api.bybit.com", timeout=5, context=ctx)
                conn.request("GET", hist_url, headers={'User-Agent': 'Mozilla/5.0'})
                resp = conn.getresponse()
                raw_payload = resp.read().decode()
                conn.close()

                payload = json.loads(raw_payload)
                rows = payload.get("result", {}).get("list", [])
                if not rows:
                    time.sleep(0.5)
                    continue

                history = [
                    (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), 0)
                    for r in reversed(rows)
                ]
                self.history_ready.emit(history)
                return True
            except Exception:
                time.sleep(0.5)
        return False

    def _create_ws_connection(self, url):
        return websocket.create_connection(
            url,
            sslopt={"cert_reqs": ssl.CERT_REQUIRED},
            sockopt=((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),)
        )

    def _on_message_dispatch(self, message, source_tag):
        try:
            msg = _json_loads(message)
        except Exception:
            return

        if msg.get("op") == "pong" or msg.get("ret_msg") == "pong":
            sent_ms = self._last_ping_sent_ms.get(source_tag, 0.0)
            if sent_ms > 0.0:
                self.clock_sync.record_rtt(get_precise_time_ms() - sent_ms)
            return

        topic = msg.get("topic", "")
        if topic.startswith("kline."):
            rows = msg.get("data") or []
            if not rows: return
            k = rows[0]
            start_t = int(k["start"])

            # Sección crítica: la verificación de deduplicación y la escritura
            # física en el ring buffer deben ser atómicas entre sí, ya que
            # ambos hilos WS (primary/mirror) son escritores concurrentes.
            # Dejar write_slot() fuera del lock permite un "torn write" cuando
            # ambos feeds ganan la carrera casi simultáneamente.
            with self.arbitration_lock:
                won = start_t >= self.last_kline_ts
                if won:
                    self.last_kline_ts = start_t
                    self.ring_buffer.write_slot(
                        1, start_t, float(k["open"]), float(k["high"]), float(k["low"]),
                        float(k["close"]), float(k["volume"])
                    )
            self._record_race_outcome(source_tag, won)

        elif topic.startswith("tickers."):
            data = msg.get("data") or {}
            if isinstance(data, list): data = data[0] if data else {}
            last_price = data.get("lastPrice")
            open_interest = data.get("openInterest")
            if last_price is None: return

            ts = int(msg.get("ts", 0))
            oi_val = float(open_interest) if open_interest is not None else 0.0
            with self.arbitration_lock:
                won = ts > self.last_ticker_ts
                if won:
                    self.last_ticker_ts = ts
                    self.ring_buffer.write_slot(
                        2, ts, 0.0, 0.0, 0.0, float(last_price), 0.0, 0, oi_val
                    )
            self._record_race_outcome(source_tag, won)

        elif topic.startswith("publicTrade."):
            trades = msg.get("data") or []
            for t in trades:
                t_id = t.get("i", "")
                side_code = 0 if t.get("S") == "Buy" else 1
                with self.arbitration_lock:
                    won = t_id != self.last_trade_id
                    if won:
                        self.last_trade_id = t_id
                        self.ring_buffer.write_slot(
                            3, int(t.get("T", 0)), 0.0, 0.0, 0.0, float(t.get("p", 0)),
                            float(t.get("v", 0)), side_code, 0.0
                        )
                self._record_race_outcome(source_tag, won)

    def _ws_worker_loop(self, url, source_tag):
        apply_mmcss_priority()

        backoff = 1.0
        while self.running:
            sub_msg = json.dumps({"op": "subscribe", "args": [
                f"kline.{self.bybit_interval}.{self.symbol}",
                f"tickers.{self.symbol}",
                f"publicTrade.{self.symbol}"
            ]})

            ws = None
            try:
                ws = self._create_ws_connection(url)
                if source_tag == "primary": self.ws_primary = ws
                else: self.ws_mirror = ws

                ws.send(sub_msg)
                self.connection_status.emit(True)
                backoff = 1.0
                self._force_reconnect = False

                last_ping = time.time()
                while self.running:
                    ws.settimeout(1.0)
                    try:
                        msg = ws.recv()
                        if msg:
                            # Degradación líder/standby: si este feed está en
                            # standby y el mensaje es de un tópico de datos
                            # (kline/ticker/trade), se evita _json_loads +
                            # arbitration_lock + write_slot por completo -- el
                            # sondeo de staleness/reconnects es lo único que
                            # se paga, y reactiva al standby instantáneamente
                            # si el líder se degrada. Los mensajes de control
                            # (pong) no llevan "topic" y siempre se procesan
                            # por completo, ya que alimentan clock_sync.
                            if self.standby_tag == source_tag and '"topic"' in msg:
                                self._check_leader_staleness_and_reconnects()
                                if self.standby_tag == source_tag:
                                    continue
                            self._on_message_dispatch(msg, source_tag)
                        else: break
                    except (socket.timeout, websocket.WebSocketTimeoutException):
                        pass

                    now = time.time()
                    if now - last_ping > 20.0:
                        self._last_ping_sent_ms[source_tag] = get_precise_time_ms()
                        ws.send(json.dumps({"op": "ping"}))
                        last_ping = now

            except Exception:
                self.reconnects += 1
                self.connection_status.emit(False)
            finally:
                if ws:
                    try: ws.close()
                    except Exception: pass
                if source_tag == "primary" and self.ws_primary is ws:
                    self.ws_primary = None
                elif source_tag == "mirror" and self.ws_mirror is ws:
                    self.ws_mirror = None

            if not self.running: break
            if self._force_reconnect:
                backoff = 1.0
                time.sleep(0.05)
            else:
                time.sleep(min(5.0, backoff))
                backoff = min(5.0, backoff * 1.5)

    def run(self):
        self._fetch_history()

        t_primary = threading.Thread(
            target=self._ws_worker_loop,
            args=("wss://stream.bybit.com/v5/public/linear", "primary"), daemon=True
        )
        t_mirror = threading.Thread(
            target=self._ws_worker_loop,
            args=("wss://stream.bytick.com/v5/public/linear", "mirror"), daemon=True
        )

        t_primary.start()
        t_mirror.start()
        t_primary.join()
        t_mirror.join()

    def stop(self):
        self.running = False
        for ws in (self.ws_primary, self.ws_mirror):
            if ws:
                try: ws.close()
                except Exception: pass

# -------------------------------------------------------------------------
# RELOJ ÚNICO DE RENDER SINCRONIZADO A VBLANK
# Antes existían dos relojes independientes y en conflicto: un QTimer de
# software a 16ms (sujeto a coalescing del scheduler) y una llamada a
# DwmFlush() bloqueante emitida DESPUÉS de programar el repintado. Ambos
# batían entre sí generando jitter intermitente. Este hilo es ahora la
# única fuente de cadencia: bloquea en DwmFlush() (o duerme a 60Hz si DWM
# no está disponible) y solo entonces dispara el ciclo de animación.
# -------------------------------------------------------------------------
class VSyncPacer(QThread):
    tick = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.running = True

    def run(self):
        apply_mmcss_priority()
        while self.running:
            if DwmFlush:
                DwmFlush()
            else:
                time.sleep(1.0 / 60.0)
            if self.running:
                self.tick.emit()

    def stop(self):
        self.running = False

# -------------------------------------------------------------------------
# OVERLAY PRINCIPAL PYQT6
# -------------------------------------------------------------------------
class CandlestickOverlay(QWidget):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.symbol = config["symbol"].upper()
        self.timeframe = config["value"]
        self.mode = config["mode"]
        self.candle_style = config["style"]
        self.use_halo = config["halo"]
        self.ticker_style = config.get("ticker_style", "hud")
        self.show_footprint = config.get("show_footprint", True)
        self.show_profile = config.get("show_profile", True)
        self.local_mouse_pos = QPointF(-1000, -1000)

        self.max_candles = 150
        self.candles = deque(maxlen=self.max_candles)
        self.ring_buffer = MPSCRingBuffer()

        self.setup_window()

        self.current_opacity = 0.0
        self.target_opacity = 0.80 if self.mode == "top" else 0.95
        self.base_opacity = self.target_opacity
        self.ws_connected = False

        self.current_morph = 1.0
        self.target_morph = 1.0

        self.anim_c = None
        self.anim_o = self.anim_h = self.anim_l = 0.0
        self.vel_c = self.vel_h = self.vel_l = 0.0
        self.anim_time = None

        self.kinetic_energy = 0.0
        self.last_tick_c = None
        self.last_tick_time = 0
        self.last_ke_update = time.monotonic()

        self.render_max_val = -float('inf')
        self.render_min_val = float('inf')
        self.cache_is_morphed = False
        self.price_history = deque(maxlen=5)

        self.ke_buffer = deque(maxlen=5)
        self.ke_sum = 0.0
        self.ke_smoothed = 0.0

        # MUTACIÓN HSL IN-PLACE (ZERO-ALLOCATION)
        self.hud_text_color = QColor()

        self.bg_cache = None
        self.cache_valid = False
        self.cached_candles_list = []
        self.last_frame_time = time.monotonic()
        self.candles_dirty = False

        # ORDER FLOW & METRICAS (CVD, OI, ABSORPTION, PROFILE)
        self.cum_cvd = 0.0
        self.prev_cvd = 0.0
        self.current_oi = 0.0
        self.prev_oi = 0.0
        self.oi_regime = "NEUTRAL"
        self.cvd_divergence = "NONE" # BULLISH, BEARISH, NONE
        self.absorption_state = "NONE" # ASK, BID, NONE

        self.session_profile = defaultdict(float)
        self.vpoc_price = 0.0
        self.vah_price = 0.0
        self.val_price = 0.0

        # Telemetría
        self.frame_count = 0
        self.last_fps_time = time.monotonic()
        self.current_fps = 0.0
        self.data_count = 0
        self.last_data_hz_time = time.monotonic()
        self.current_data_hz = 0.0
        self.last_wire_latency_ms = 0.0

        # CONFIGURACIÓN DE MENÚ CONTEXTUAL (CLIC DERECHO)
        self.setup_context_menu()

        # Inicia Hilo de Datos WS
        self.market_thread = BybitRedundantMarketDataThread(self.symbol, self.timeframe, self.ring_buffer)
        self.market_thread.history_ready.connect(self.handle_history)
        self.market_thread.connection_status.connect(self.handle_ws_status)
        self.market_thread.start()

        # Reloj único de render, pausado a VBlank real (ver VSyncPacer)
        self.vsync_pacer = VSyncPacer()
        self.vsync_pacer.tick.connect(self.animate_and_repaint)
        self.vsync_pacer.start()

    def setup_window(self):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.mode == "top": flags |= Qt.WindowType.WindowStaysOnTopHint
        flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        screen = QApplication.primaryScreen().availableGeometry()
        self.setGeometry(screen)

        if sys.platform == "win32":
            hwnd = int(self.winId())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x00080000 | 0x00000020)

    def setup_context_menu(self):
        self.menu = QMenu(self)
        self.menu.setStyleSheet("QMenu { background-color: #1a1a1a; color: #eeeeee; border: 1px solid #333; }"
                                "QMenu::item:selected { background-color: #333333; }")

        self.menu_sym = self.menu.addMenu("Symbol >")
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT"]:
            action = self.menu_sym.addAction(sym)
            action.triggered.connect(lambda ch, s=sym: self.change_symbol(s))

        self.menu_tf = self.menu.addMenu("Timeframe >")
        for tf in ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w", "1M"]:
            action = self.menu_tf.addAction(tf)
            action.triggered.connect(lambda ch, t=tf: self.change_timeframe(t))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.show_context_menu(event.globalPosition().toPoint())

    def show_context_menu(self, pos):
        self.menu.exec(pos)

    def change_symbol(self, new_sym):
        if new_sym == self.symbol: return
        self.symbol = new_sym
        self.candles.clear()
        self.session_profile.clear()
        self.cum_cvd = 0.0
        self.market_thread.set_symbol_timeframe(self.symbol, self.timeframe)

    def change_timeframe(self, new_tf):
        if new_tf == self.timeframe: return
        self.timeframe = new_tf
        self.candles.clear()
        self.session_profile.clear()
        self.market_thread.set_symbol_timeframe(self.symbol, self.timeframe)

    def recalculate_scales(self):
        if not self.candles: return
        actual_max = max(c.h for c in self.candles)
        actual_min = min(c.l for c in self.candles)
        margin = (actual_max - actual_min) * 0.10
        if margin == 0: margin = 1

        self.render_max_val = actual_max + margin
        self.render_min_val = actual_min - margin
        self.cache_valid = False
        self.recalculate_volume_profile()

    def recalculate_volume_profile(self):
        if not self.show_profile or not self.candles: return
        self.session_profile.clear()

        for c in self.candles:
            for lvl, vols in c.footprint.items():
                self.session_profile[lvl] += (vols[0] + vols[1])

        if not self.session_profile: return

        sorted_lvls = sorted(self.session_profile.items(), key=lambda x: x[1], reverse=True)
        self.vpoc_price = sorted_lvls[0][0]

        total_vol = sum(self.session_profile.values())
        target_va_vol = total_vol * 0.70

        va_vol_acc = 0.0
        va_levels = []
        for lvl, vol in sorted_lvls:
            va_vol_acc += vol
            va_levels.append(lvl)
            if va_vol_acc >= target_va_vol: break

        if va_levels:
            self.vah_price = max(va_levels)
            self.val_price = min(va_levels)

    def handle_history(self, hist_data):
        if not hist_data: return
        self.candles.clear()
        for data in hist_data:
            t, o, h, l, c, v, trades = data
            self.candles.append(Kline(t, o, h, l, c, v, trades))
        self.candles_dirty = True
        self.recalculate_scales()

    def handle_ws_status(self, status):
        self.ws_connected = status

    def _drain_ring_buffer(self):
        slots = self.ring_buffer.drain_all()
        if not slots: return

        for slot in slots:
            slot_type, t, o, h, l, c, v, side, extra = slot
            self.data_count += 1

            if slot_type == 1:  # Kline completo
                if self.last_tick_c is not None and self.last_tick_c > 0:
                    price_delta_pct = abs(c - self.last_tick_c) / self.last_tick_c * 100
                    self.kinetic_energy = min(1.0, self.kinetic_energy + min(1.0, price_delta_pct / KE_INPUT_SENSITIVITY))

                self.last_tick_c = c
                self.last_tick_time = t

                if not self.candles:
                    self.candles.append(Kline(t, o, h, l, c, v))
                    self.recalculate_scales()
                elif t > self.candles[-1].t:
                    if len(self.candles) == self.max_candles:
                        recycled = self.candles.popleft()
                        recycled.update(t, o, h, l, c, v)
                        self.candles.append(recycled)
                    else:
                        self.candles.append(Kline(t, o, h, l, c, v))
                    self.recalculate_scales()
                elif t == self.candles[-1].t:
                    self.candles[-1].update(t, o, h, l, c, v)
                    p_range = self.render_max_val - self.render_min_val
                    if h >= self.render_max_val - (p_range * 0.02) or l <= self.render_min_val + (p_range * 0.02):
                        self.recalculate_scales()

            elif slot_type == 2:  # Ticker rápido & Open Interest
                price_c = c
                price_t = t
                if self.last_tick_c is not None and self.last_tick_c > 0:
                    price_delta_pct = abs(price_c - self.last_tick_c) / self.last_tick_c * 100
                    self.kinetic_energy = min(1.0, self.kinetic_energy + 0.6 * min(1.0, price_delta_pct / KE_INPUT_SENSITIVITY))

                self.last_tick_c = price_c
                self.last_tick_time = price_t
                if price_t > 0:
                    self.last_wire_latency_ms = self.market_thread.clock_sync.corrected_latency_ms(
                        get_precise_time_ms(), price_t
                    )

                if extra > 0.0:
                    self.prev_oi = self.current_oi
                    self.current_oi = extra
                    if self.prev_oi > 0.0 and self.candles:
                        delta_p = price_c - self.candles[-1].o
                        delta_oi = self.current_oi - self.prev_oi
                        if delta_p > 0 and delta_oi > 0: self.oi_regime = "LONG BUILD-UP"
                        elif delta_p < 0 and delta_oi > 0: self.oi_regime = "SHORT BUILD-UP"
                        elif delta_p < 0 and delta_oi < 0: self.oi_regime = "LONG UNWINDING"
                        elif delta_p > 0 and delta_oi < 0: self.oi_regime = "SHORT COVERING"

                if self.candles:
                    last = self.candles[-1]
                    last.c = price_c
                    if price_c > last.h: last.h = price_c; self.recalculate_scales()
                    if price_c < last.l: last.l = price_c; self.recalculate_scales()

            elif slot_type == 3:  # Trade individual para Footprint & CVD
                trade_price = c; trade_qty = v; trade_side = side  # 0=Buy, 1=Sell

                if self.candles:
                    self.candles[-1].add_trade(trade_price, trade_qty, trade_side)

                delta_val = trade_qty if trade_side == 0 else -trade_qty
                self.cum_cvd += delta_val

                # Absorción
                if trade_qty > 10.0:
                    self.absorption_state = "ASK" if trade_side == 0 else "BID"
                else:
                    self.absorption_state = "NONE"

                if self.candles and len(self.candles) >= 2:
                    curr_c = self.candles[-1]; prev_c = self.candles[-2]
                    if curr_c.c <= prev_c.c and self.cum_cvd > self.prev_cvd: self.cvd_divergence = "BULLISH"
                    elif curr_c.c >= prev_c.c and self.cum_cvd < self.prev_cvd: self.cvd_divergence = "BEARISH"
                    else: self.cvd_divergence = "NONE"
                    self.prev_cvd = self.cum_cvd

            self.candles_dirty = True

    def animate_and_repaint(self):
        self._drain_ring_buffer()

        mouse_pos_global = QCursor.pos()
        local_mouse = self.mapFromGlobal(mouse_pos_global)

        if self.mode == "top" and local_mouse != self.local_mouse_pos:
            self.local_mouse_pos = local_mouse
            in_evasion_zone = (-180 < local_mouse.x() < self.width() - 85 + 180 and 20 < local_mouse.y() < self.height() - 20)
            if in_evasion_zone or self.cache_is_morphed:
                self.cache_is_morphed = in_evasion_zone
                self.cache_valid = False

        now = time.monotonic()
        dt = min(now - getattr(self, 'last_frame_time', now - 0.016), 0.1)
        self.last_frame_time = now

        self.frame_count += 1
        fps_elapsed = now - self.last_fps_time
        if fps_elapsed >= 1.0:
            self.current_fps = self.frame_count / fps_elapsed
            self.frame_count = 0; self.last_fps_time = now

        data_elapsed = now - self.last_data_hz_time
        if data_elapsed >= 1.0:
            self.current_data_hz = self.data_count / data_elapsed
            self.data_count = 0; self.last_data_hz_time = now

        if len(self.candles) > 0:
            current_time = time.monotonic()
            elapsed = current_time - self.last_ke_update
            self.kinetic_energy *= math.exp(-(0.693 / KE_DECAY_HALF_LIFE_S) * elapsed)

            if len(self.ke_buffer) == self.ke_buffer.maxlen: self.ke_sum -= self.ke_buffer[0]
            self.ke_sum += self.kinetic_energy
            self.ke_buffer.append(self.kinetic_energy)
            self.ke_smoothed = self.ke_sum / len(self.ke_buffer) if len(self.ke_buffer) >= 3 else self.kinetic_energy

            self.last_ke_update = current_time

            if self.current_data_hz < 2.0 and current_time - getattr(self, 'last_tactical_gc', 0) > 30.0:
                gc.collect(0)
                self.last_tactical_gc = current_time

        if self.mode == "top" and len(self.candles) > 0:
            threshold_morph = min(0.3, (0.05 if len(self.candles) < 20 else 0.1) * 0.5)
            self.target_morph = 1.0 if self.ke_smoothed > threshold_morph else 0.0
            self.target_opacity = 0.25 + (self.ke_smoothed * 0.70)
        else:
            self.target_opacity = self.base_opacity
            self.target_morph = 1.0

        if abs(self.current_opacity - self.target_opacity) > 0.01:
            self.current_opacity += (self.target_opacity - self.current_opacity) * 0.25

        if abs(self.current_morph - self.target_morph) > 0.005:
            self.current_morph += (self.target_morph - self.current_morph) * 0.15
            self.cache_valid = False

        if self.candles:
            real_last = self.candles[-1]
            if self.anim_time != real_last.t:
                self.anim_time = real_last.t
                self.anim_c = real_last.c
                self.anim_o, self.anim_h, self.anim_l = real_last.o, real_last.h, real_last.l
                self.vel_c = self.vel_h = self.vel_l = 0.0
            else:
                k_spring = 0.18; damping = 0.85
                time_accum = dt; step_size = 1.0 / 60.0
                while time_accum > 0.0001:
                    chunk = min(time_accum, step_size); dt_rate = chunk * 60.0
                    self.vel_c += (-k_spring * (self.anim_c - real_last.c) - damping * self.vel_c) * dt_rate
                    self.anim_c += self.vel_c * dt_rate
                    self.vel_h += (-k_spring * (self.anim_h - real_last.h) - damping * self.vel_h) * dt_rate
                    self.anim_h += self.vel_h * dt_rate
                    self.vel_l += (-k_spring * (self.anim_l - real_last.l) - damping * self.vel_l) * dt_rate
                    self.anim_l += self.vel_l * dt_rate
                    time_accum -= chunk

        if self.anim_c is not None:
            threshold = min((self.render_max_val - self.render_min_val) * 0.0001, self.anim_c * 0.001)
            if not self.price_history or abs(self.anim_c - self.price_history[-1]) > threshold:
                self.price_history.append(self.anim_c)

        self.update()

    def paintEvent(self, event):
        if getattr(self, 'candles_dirty', False) or not self.cached_candles_list:
            self.cached_candles_list = list(self.candles)
            self.candles_dirty = False

        candles_list = self.cached_candles_list
        n = len(candles_list)

        if n == 0:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor(0, 230, 118, 150))
            painter.setFont(QFont("Consolas", 14, QFont.Weight.Bold))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "BOOTING VELAS16 ENGINE...")
            painter.end()
            return

        w = self.width(); h = self.height()
        pad_top = 40; pad_bottom = 20; right_margin = 85
        chart_w = w - right_margin
        chart_h = h - pad_top - pad_bottom

        price_range = self.render_max_val - self.render_min_val
        def price_to_y(p):
            if price_range == 0: return pad_top
            return pad_top + ((self.render_max_val - p) / price_range) * chart_h

        cw = chart_w / self.max_candles
        start_idx = self.max_candles - n

        if not self.cache_valid or not self.bg_cache or self.bg_cache.size() != self.size():
            self.bg_cache = QPixmap(self.size())
            self.bg_cache.fill(Qt.GlobalColor.transparent)
            cp = QPainter(self.bg_cache)
            cp.setRenderHint(QPainter.RenderHint.Antialiasing)

            pen_grid = QPen(QColor(51, 51, 51, 150), 1, Qt.PenStyle.DashLine)
            cp.setFont(QFont("Consolas", 10))
            for j in range(5):
                frac = (j + 1) / 6.0
                p = self.render_max_val - price_range * frac
                y = price_to_y(p)
                cp.setPen(pen_grid)
                cp.drawLine(QPointF(0, y), QPointF(chart_w, y))
                cp.setPen(QColor(136, 136, 136, 255))
                cp.drawText(QPointF(chart_w + 5, y + 4), f"{p:,.1f}")
            cp.end()
            self.cache_valid = True

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.setOpacity(self.current_opacity)
        if self.bg_cache: painter.drawPixmap(0, 0, self.bg_cache)
        painter.setOpacity(1.0)

        # -----------------------------------------------------------------
        # PERFIL DE VOLUMEN DE RANGO VISIBLE (VPOC / VAH / VAL)
        # -----------------------------------------------------------------
        if self.show_profile and self.session_profile:
            max_prof_vol = max(self.session_profile.values()) if self.session_profile else 1.0
            profile_max_w = chart_w * 0.20

            for price_lvl, vol in self.session_profile.items():
                if self.render_min_val <= price_lvl <= self.render_max_val:
                    y_p = price_to_y(price_lvl)
                    bar_w = (vol / max_prof_vol) * profile_max_w
                    prof_rect = QRectF(0, y_p - 1, bar_w, 2)
                    painter.fillRect(prof_rect, QColor(0, 188, 212, 60))

            if self.vpoc_price > 0:
                vpoc_y = price_to_y(self.vpoc_price)
                painter.setPen(QPen(QColor(255, 214, 0, 220), 2, Qt.PenStyle.SolidLine))
                painter.drawLine(QPointF(0, vpoc_y), QPointF(chart_w, vpoc_y))
                painter.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
                painter.setPen(QColor(255, 214, 0))
                painter.drawText(QPointF(5, vpoc_y - 3), f"VPOC: {self.vpoc_price:,.2f}")

            if self.vah_price > 0 and self.val_price > 0:
                vah_y = price_to_y(self.vah_price); val_y = price_to_y(self.val_price)
                painter.setPen(QPen(QColor(0, 229, 255, 180), 1, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(0, vah_y), QPointF(chart_w, vah_y))
                painter.drawLine(QPointF(0, val_y), QPointF(chart_w, val_y))

        # -----------------------------------------------------------------
        # RENDER VELAS & FOOTPRINT LEVELS & EVASIÓN
        # -----------------------------------------------------------------
        mouse_x = self.local_mouse_pos.x(); mouse_y = self.local_mouse_pos.y()
        for i in range(n):
            c = candles_list[i]
            x_center = (start_idx + i) * cw + cw / 2
            y_c = price_to_y(c.c)

            evasion_morph = 1.0; evasion_opacity = 1.0
            if self.mode == "top":
                radius_max = 180
                if abs(x_center - mouse_x) < radius_max and abs(y_c - mouse_y) < radius_max:
                    dist = ((x_center - mouse_x)**2 + (y_c - mouse_y)**2)**0.5
                    if dist < radius_max:
                        factor = max(0.0, (dist - 60) / 120)
                        evasion_morph = factor; evasion_opacity = 0.2 + 0.8 * factor

            final_morph = self.current_morph * evasion_morph
            c_val = c.c
            y_o = price_to_y(c_val + (c.o - c_val) * final_morph)
            y_h = price_to_y(c_val + (c.h - c_val) * final_morph)
            y_l = price_to_y(c_val + (c.l - c_val) * final_morph)

            is_bull = c.c >= c.o
            base_color = QColor(0, 230, 118) if is_bull else QColor(255, 61, 0)
            fade_factor = (0.3 + (0.7 * (i / max(1, n - 1)))) * evasion_opacity

            if self.current_morph > 0.05:
                candle_body_w = max(1.0, cw * 0.7)
                top, bot = min(y_o, y_c), max(y_o, y_c)
                if bot - top < 1: bot = top + 1
                rect = QRectF(x_center - candle_body_w/2, top, candle_body_w, bot - top)

                if self.use_halo:
                    halo_color = QColor(10, 10, 10, int(180 * fade_factor))
                    painter.setPen(QPen(halo_color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                    painter.drawLine(QPointF(x_center, y_h), QPointF(x_center, y_l))

                wick_color = QColor(base_color)
                wick_color.setAlphaF(fade_factor * 0.8 * self.current_morph)
                painter.setPen(QPen(wick_color, 1))
                painter.drawLine(QPointF(x_center, y_h), QPointF(x_center, y_l))

                if self.use_halo:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(halo_color, 3))
                    painter.drawRect(rect)

                core_color = QColor(base_color)
                core_color.setAlphaF(fade_factor * self.current_morph)

                if self.candle_style == "filled":
                    painter.setBrush(QBrush(core_color))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawRect(rect)
                elif self.candle_style == "glow":
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    glow_color = QColor(base_color)
                    glow_color.setAlphaF(fade_factor * 0.3 * self.current_morph)
                    painter.setPen(QPen(glow_color, 3))
                    painter.drawRect(rect)
                    painter.setPen(QPen(core_color, 1))
                    painter.drawRect(rect)
                else:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(core_color, 1))
                    painter.drawRect(rect)

                # -----------------------------------------------------------------
                # RENDER FOOTPRINT LEVELS (GARANTIZADO EN CUALQUIER ANCHO Y ESTILO)
                # -----------------------------------------------------------------
                if self.show_footprint and c.footprint:
                    painter.setFont(QFont("Consolas", 6, QFont.Weight.Bold))
                    candle_body_w = max(1.0, cw * 0.7)

                    for lvl_p, vols in c.footprint.items():
                        if c.l <= lvl_p <= c.h:
                            lvl_y = price_to_y(lvl_p)
                            b_v, s_v = vols[0], vols[1]
                            imbal = b_v - s_v

                            # 1. Franja de color de Imbalanza (Visible siempre en todas las velas)
                            fp_color = QColor(0, 230, 118, 140) if imbal >= 0 else QColor(255, 61, 0, 140)
                            fp_rect = QRectF(x_center - candle_body_w/2, lvl_y - 2, candle_body_w, 4)
                            painter.fillRect(fp_rect, fp_color)

                            # 2. Texto de Números Ventas x Compras (Visible en las velas recientes)
                            # Se dibuja siempre en las últimas 15 velas vivas o si la vela mide más de 12px
                            if (i >= n - 15) or cw > 12:
                                painter.setPen(QColor(255, 255, 255, 230))
                                txt_lvl = f"{int(s_v)}x{int(b_v)}"
                                painter.drawText(fp_rect, Qt.AlignmentFlag.AlignCenter, txt_lvl)

        # SPARKLINE PATH CUANDO MORPH < 1.0
        if self.current_morph < 1.0 and n > 0:
            spark_path = QPainterPath()
            for i in range(n):
                c = candles_list[i]
                x_c = (start_idx + i) * cw + cw / 2
                y_c = price_to_y(c.c)
                if i == 0: spark_path.moveTo(x_c, y_c)
                else: spark_path.lineTo(x_c, y_c)

            cur_is_bull = candles_list[-1].c >= candles_list[-1].o
            spark_color = QColor(0, 230, 118) if cur_is_bull else QColor(255, 61, 0)
            spark_color.setAlphaF((1.0 - self.current_morph) * self.current_opacity * 0.80)
            painter.setPen(QPen(spark_color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(spark_path)

        # -----------------------------------------------------------------
        # HUD DE TELEMETRÍA, OI & DIVERGENCIAS (Esquina Superior Derecha)
        # -----------------------------------------------------------------
        tf_font = QFont("Consolas", 10, QFont.Weight.Bold)
        painter.setFont(tf_font)
        tf_text = f"{self.symbol.upper()} | {self.timeframe}"
        tf_fm = painter.fontMetrics()
        tf_w = tf_fm.horizontalAdvance(tf_text)
        dot_w = tf_fm.horizontalAdvance("● ")
        tf_h = tf_fm.height()

        tf_x = w - tf_w - dot_w - 15; tf_y = 8
        tf_rect = QRectF(tf_x, tf_y, tf_w + dot_w + 8, tf_h + 4)

        painter.setPen(QPen(QColor(255, 255, 255, 200), 1))
        painter.setBrush(QColor(10, 10, 10, 220))
        painter.drawRoundedRect(tf_rect, 4, 4)

        status_color = QColor(0, 230, 118) if self.ws_connected else QColor(255, 61, 0)
        painter.setPen(status_color)
        painter.drawText(QPointF(tf_x + 4, tf_y + tf_h - 2), "●")
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(QPointF(tf_x + 4 + dot_w, tf_y + tf_h - 2), tf_text)

        # ARCO TEMPORIZADOR DE LA VELA (Junto al Timeframe)
        arc_size = 14; arc_x = tf_x - arc_size - 8; arc_y = tf_y + 2
        tf_seconds = TIMEFRAME_SECONDS.get(self.timeframe, 300)
        candle_start_s = candles_list[-1].t / 1000.0
        elapsed_s = time.time() - candle_start_s
        ratio_left = min(1.0, max(0.0, elapsed_s / tf_seconds))
        painter.setPen(QPen(QColor(0, 229, 255, 200), 2))
        painter.drawArc(QRectF(arc_x, arc_y, arc_size, arc_size), 90 * 16, int(-360 * 16 * ratio_left))

        # PANEL DETALLADO DE TELEMETRÍA
        debug_font = QFont("Consolas", 8, QFont.Weight.Bold)
        painter.setFont(debug_font)
        debug_lines = [
            f"FPS {self.current_fps:.1f}",
            f"RX {self.current_data_hz:.1f}Hz",
            f"PIPE {self.last_wire_latency_ms:.1f}ms",
            f"RCN {self.market_thread.reconnects}",
            f"DROP {self.ring_buffer.dropped}",
            f"OI: {self.current_oi:,.0f}",
            f"REGIME: {self.oi_regime}",
            f"DIV CVD: {self.cvd_divergence}",
            f"ABSORPTION: {self.absorption_state}"
        ]
        debug_fm = painter.fontMetrics()
        debug_w = max(debug_fm.horizontalAdvance(line) for line in debug_lines)
        line_h = debug_fm.height()
        debug_h = line_h * len(debug_lines)
        debug_x = w - debug_w - 15; debug_y = tf_y + tf_h + 8

        debug_rect = QRectF(debug_x, debug_y, debug_w + 8, debug_h + 8)
        painter.setPen(QPen(QColor(170, 170, 170, 160), 1))
        painter.setBrush(QColor(10, 10, 10, 220))
        painter.drawRoundedRect(debug_rect, 3, 3)

        for j, line in enumerate(debug_lines):
            if "BUILD-UP" in line or "BULLISH" in line or "ASK" in line: painter.setPen(QColor(0, 230, 118))
            elif "UNWINDING" in line or "BEARISH" in line or "BID" in line or "SHORT" in line: painter.setPen(QColor(255, 61, 0))
            else: painter.setPen(QColor(170, 170, 170))
            painter.drawText(QPointF(debug_x + 4, debug_y + (j + 1) * line_h + 2), line)

        # LÍNEA DE PRECIO ACTUAL Y TICKER SCI-FI HUD CON DYNAMIC HSL MUTATION
        last_c = candles_list[-1]
        cur_p = last_c.c
        py = price_to_y(cur_p)
        color_hex = "#00E676" if last_c.c >= last_c.o else "#FF3D00"

        line_color = QColor(color_hex)
        line_color.setAlphaF(self.current_opacity)
        painter.setPen(QPen(line_color, 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(0, py), QPointF(chart_w, py))

        # MOTION BLUR
        if len(self.price_history) >= 2:
            prev_py = price_to_y(self.price_history[-2])
            pixel_vel = abs(py - prev_py)
            if pixel_vel > 1.5 and self.ke_smoothed > 0.15:
                grad = QLinearGradient(0, prev_py, 0, py)
                grad.setColorAt(0.0, QColor(0, 0, 0, 0))
                grad_head = QColor(color_hex)
                grad_head.setAlphaF(self.current_opacity * 0.45)
                grad.setColorAt(1.0, grad_head)
                painter.setBrush(QBrush(grad))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(QRectF(chart_w - 45, min(py, prev_py), 90, pixel_vel))

        p_str = f"{cur_p:,.2f}"
        font = QFont("Consolas", 11, QFont.Weight.Bold)

        if self.ticker_style == "hud":
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.5)
            painter.setFont(font)
            fm = painter.fontMetrics()
            text_w = fm.horizontalAdvance(p_str); text_h = fm.height()

            pad_x, pad_y = 3, 4
            pill_rect = QRectF(chart_w + 1, py - text_h/2 - pad_y/2 - 1, text_w + pad_x*2, text_h + pad_y)

            grad = QLinearGradient(pill_rect.topLeft(), pill_rect.bottomRight())
            grad_color_start = QColor(color_hex)
            grad_color_start.setAlphaF(self.current_opacity * 0.8)
            grad.setColorAt(0.0, grad_color_start)
            grad.setColorAt(0.3, QColor(10, 10, 10, int(240 * self.current_opacity)))
            grad.setColorAt(1.0, QColor(5, 5, 5, int(250 * self.current_opacity)))

            painter.setPen(QPen(line_color, 1))
            painter.setBrush(QBrush(grad))
            painter.drawRoundedRect(pill_rect, 3, 3)

            # MUTACIÓN HSL IN-PLACE (ZERO-ALLOCATION)
            rng = max(0.0001, last_c.h - last_c.l)
            skew_ratio = abs(last_c.c - last_c.o) / rng
            flash_intensity = math.sqrt(min(1.0, self.ke_smoothed * 1.5))
            activation = flash_intensity * (0.3 + 0.7 * skew_ratio)

            is_bull = last_c.c >= last_c.o
            hue = 0.33 if is_bull else 0.00
            sat = activation
            lig = 1.0 - (0.25 * activation)
            self.hud_text_color.setHslF(hue, sat, lig, 1.0)

            painter.setPen(self.hud_text_color)
            painter.drawText(QPointF(chart_w + 1 + pad_x, py + text_h/3), p_str)
        else:
            painter.setFont(font)
            fm = painter.fontMetrics()
            text_w = fm.horizontalAdvance(p_str); text_h = fm.height()
            pill_rect = QRectF(chart_w + 2, py - text_h/2 - 2, text_w + 8, text_h + 4)
            painter.setPen(QPen(QColor(255, 255, 255, 255), 1))
            painter.setBrush(QColor(10, 10, 10, 255))
            painter.drawRoundedRect(pill_rect, 4, 4)
            painter.setPen(QColor(color_hex))
            painter.drawText(QPointF(chart_w + 6, py + text_h/3), p_str)

    def closeEvent(self, event):
        self.market_thread.stop()
        self.market_thread.wait()
        self.vsync_pacer.stop()
        self.vsync_pacer.wait()
        event.accept()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication(sys.argv)

    dialog = ConfigDialog()
    if dialog.exec() == QDialog.DialogCode.Accepted:
        overlay = CandlestickOverlay(dialog.selection)
        overlay.show()
        sys.exit(app.exec())