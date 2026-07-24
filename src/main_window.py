"""
Main Dashboard UI for Audi A4 B5 1.9 TDI Diagnostics.
PyQt6 native Wayland-compatible gauges for RPM, MAP, MAF.
"""

from __future__ import annotations
import sys
import time
import logging
from dataclasses import dataclass
from typing import Optional
from enum import Enum

from PyQt6.QtCore import (
    Qt, QTimer, QRectF, QPointF, QSize, QEasingCurve, QPropertyAnimation,
    pyqtSignal, pyqtSlot, pyqtProperty, QObject, QThread, QUrl
)
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetrics, QLinearGradient,
    QRadialGradient, QConicalGradient, QPaintEvent, QResizeEvent, QPixmap,
    QPolygonF, QDesktopServices, QPainterPath, QCursor
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QCheckBox, QGroupBox, QStatusBar,
    QProgressBar, QMessageBox, QSplitter, QFrame, QScrollArea, QSizePolicy,
    QToolBar, QStyle, QDial, QDialog, QDialogButtonBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QSpinBox, QAbstractItemView, QStackedWidget
)

from telemetry_worker import (
    TelemetryThread,
    TelemetrySnapshot,
    MeasuringBlock003,
    MeasuringBlock007,
    MeasuringBlock011,
    WorkerState,
    ECUIdentification,
)
from kw1281_handler import FaultCode, MeasuringValue


class GaugeStyle(Enum):
    """Gauge visual styles"""
    MODERN = "modern"
    CLASSIC = "classic"
    RACE = "race"


@dataclass
class GaugeConfig:
    """Configuration for a gauge widget"""
    title: str
    unit: str
    min_value: float
    max_value: float
    warning_threshold: Optional[float] = None
    critical_threshold: Optional[float] = None
    major_ticks: int = 10
    minor_ticks: int = 5
    start_angle: int = 225  # Degrees, 0 = 3 o'clock
    sweep_angle: int = 270  # Degrees
    style: GaugeStyle = GaugeStyle.MODERN
    show_digital: bool = True
    decimals: int = 1


# Predefined gauge configs for EDC15 AFN
GAUGE_CONFIGS = {
    'rpm': GaugeConfig(
        title="RPM",
        unit="RPM",
        min_value=0,
        max_value=6000,
        warning_threshold=4500,
        critical_threshold=5200,
        major_ticks=6,
        minor_ticks=5,
        decimals=0,
    ),
    'map_actual': GaugeConfig(
        title="MAP Actual",
        unit="mbar",
        min_value=0,
        max_value=2500,
        warning_threshold=2200,
        critical_threshold=2400,
        major_ticks=10,
        minor_ticks=5,
        decimals=0,
    ),
    'map_specified': GaugeConfig(
        title="MAP Specified",
        unit="mbar",
        min_value=0,
        max_value=2500,
        major_ticks=10,
        minor_ticks=5,
        decimals=0,
    ),
    'maf_actual': GaugeConfig(
        title="MAF Actual",
        unit="mg/stroke",
        min_value=0,
        max_value=1200,
        warning_threshold=1000,
        critical_threshold=1150,
        major_ticks=6,
        minor_ticks=5,
        decimals=1,
    ),
    'maf_specified': GaugeConfig(
        title="MAF Specified",
        unit="mg/stroke",
        min_value=0,
        max_value=1200,
        major_ticks=6,
        minor_ticks=5,
        decimals=1,
    ),
    'boost': GaugeConfig(
        title="Boost",
        unit="mbar",
        min_value=-1000,
        max_value=1500,
        warning_threshold=1200,
        critical_threshold=1400,
        major_ticks=10,
        minor_ticks=5,
        decimals=0,
    ),
    'coolant_temp': GaugeConfig(
        title="Coolant",
        unit="°C",
        min_value=-20,
        max_value=130,
        warning_threshold=100,
        critical_threshold=115,
        major_ticks=6,
        minor_ticks=5,
        decimals=0,
    ),
    'intake_temp': GaugeConfig(
        title="Intake Air",
        unit="°C",
        min_value=-20,
        max_value=80,
        warning_threshold=60,
        critical_threshold=70,
        major_ticks=5,
        minor_ticks=5,
        decimals=0,
    ),
    'wastegate': GaugeConfig(
        title="Wastegate Duty",
        unit="%",
        min_value=0,
        max_value=100,
        warning_threshold=85,
        critical_threshold=95,
        major_ticks=5,
        minor_ticks=5,
        decimals=0,
    ),
    'engine_load': GaugeConfig(
        title="Engine Load",
        unit="%",
        min_value=0,
        max_value=100,
        warning_threshold=90,
        critical_threshold=100,
        major_ticks=5,
        minor_ticks=5,
        decimals=0,
    ),
}


class CircularGauge(QWidget):
    """
    High-performance circular gauge widget with smooth animations.
    Supports modern, classic, and race styles.
    """
    
    def __init__(
        self,
        config: GaugeConfig,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.config = config
        self._value = config.min_value
        self._target_value = config.min_value
        self._animated = True
        
        # Animation for smooth needle movement
        self._animation = QPropertyAnimation(self, b"value", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        # Colors
        self._bg_color = QColor(15, 15, 20)
        self._rim_color = QColor(40, 40, 50)
        self._text_color = QColor(220, 220, 230)
        self._accent_color = QColor(230, 169, 74)   # warm amber, matches new theme
        self._warning_color = QColor(255, 170, 0)
        self._critical_color = QColor(239, 83, 80)  # softer red
        self._green_zone_color = QColor(0, 200, 100)
        
        # Fonts
        self._title_font = QFont("Inter", 11, QFont.Weight.Medium)
        self._value_font = QFont("Inter", 26, QFont.Weight.Bold)
        self._unit_font = QFont("Inter", 10, QFont.Weight.Normal)
        self._tick_font = QFont("Inter", 8, QFont.Weight.Normal)
        
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        # Enable antialiasing
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
    
    def _get_value(self) -> float:
        return self._value
    
    def _set_value(self, v: float) -> None:
        self._value = max(self.config.min_value, min(self.config.max_value, v))
        self.update()
    
    # PyQt precisa disto registado como pyqtProperty (não um @property normal do
    # Python) para o QPropertyAnimation conseguir animar o valor da agulha.
    value = pyqtProperty(float, _get_value, _set_value)
    
    def set_value(self, value: float, animated: bool = True) -> None:
        """Set gauge value with optional animation."""
        clamped = max(self.config.min_value, min(self.config.max_value, value))
        self._target_value = clamped
        
        if animated and self._animated:
            self._animation.stop()
            self._animation.setStartValue(self._value)
            self._animation.setEndValue(clamped)
            self._animation.start()
        else:
            self._value = clamped
            self.update()
    
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        
        rect = self.rect()
        size = min(rect.width(), rect.height())
        center = QPointF(rect.center())
        radius = (size - 20) / 2
        
        # Draw background
        self._draw_background(painter, center, radius)
        
        # Draw scale/ticks
        self._draw_scale(painter, center, radius)
        
        # Draw colored zones
        self._draw_zones(painter, center, radius)
        
        # Draw needle
        self._draw_needle(painter, center, radius)
        
        # Draw center hub
        self._draw_hub(painter, center, radius)
        
        # Draw digital value
        if self.config.show_digital:
            self._draw_digital_value(painter, center, radius)
        
        # Draw title
        self._draw_title(painter, center, radius)
    
    def _draw_background(self, painter: QPainter, center: QPointF, radius: float) -> None:
        # Outer rim gradient
        rim_grad = QRadialGradient(center, radius + 10)
        rim_grad.setColorAt(0, QColor(30, 30, 40))
        rim_grad.setColorAt(0.7, QColor(20, 20, 30))
        rim_grad.setColorAt(1, QColor(10, 10, 15))
        painter.setBrush(QBrush(rim_grad))
        painter.setPen(QPen(QColor(60, 60, 80), 2))
        painter.drawEllipse(center, radius + 8, radius + 8)
        
        # Main face
        face_grad = QRadialGradient(center, radius)
        face_grad.setColorAt(0, QColor(25, 25, 35))
        face_grad.setColorAt(1, QColor(15, 15, 20))
        painter.setBrush(QBrush(face_grad))
        painter.setPen(QPen(QColor(50, 50, 60), 1))
        painter.drawEllipse(center, radius, radius)
    
    def _draw_zones(self, painter: QPainter, center: QPointF, radius: float) -> None:
        """Draw warning/critical zones on the gauge rim."""
        if self.config.warning_threshold is None:
            return
        
        # Warning zone
        warn_start = self._value_to_angle(self.config.warning_threshold)
        warn_end = self._value_to_angle(
            self.config.critical_threshold if self.config.critical_threshold else self.config.max_value
        )
        
        if warn_end > warn_start:
            zone_rect = QRectF(
                center.x() - radius + 5, center.y() - radius + 5,
                (radius - 5) * 2, (radius - 5) * 2
            )
            painter.setPen(QPen(self._warning_color, 4))
            painter.drawArc(zone_rect, int(warn_start * 16), int((warn_end - warn_start) * 16))
        
        # Critical zone
        if self.config.critical_threshold is not None:
            crit_start = self._value_to_angle(self.config.critical_threshold)
            crit_end = self._value_to_angle(self.config.max_value)
            
            if crit_end > crit_start:
                zone_rect = QRectF(
                    center.x() - radius + 5, center.y() - radius + 5,
                    (radius - 5) * 2, (radius - 5) * 2
                )
                painter.setPen(QPen(self._critical_color, 4))
                painter.drawArc(zone_rect, int(crit_start * 16), int((crit_end - crit_start) * 16))
    
    def _draw_scale(self, painter: QPainter, center: QPointF, radius: float) -> None:
        """Draw major and minor tick marks with labels."""
        major_step = (self.config.max_value - self.config.min_value) / self.config.major_ticks
        minor_step = major_step / self.config.minor_ticks
        total_range = self.config.max_value - self.config.min_value
        
        # Major ticks
        painter.setPen(QPen(self._text_color, 2))
        for i in range(self.config.major_ticks + 1):
            value = self.config.min_value + i * major_step
            angle = self._value_to_angle(value)
            
            r1 = radius - 12
            r2 = radius - 2
            
            x1 = center.x() + r1 * self._cos_deg(angle)
            y1 = center.y() + r1 * self._sin_deg(angle)
            x2 = center.x() + r2 * self._cos_deg(angle)
            y2 = center.y() + r2 * self._sin_deg(angle)
            
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            
            # Label
            if i % 1 == 0:  # Every major tick
                label = f"{value:.0f}" if self.config.decimals == 0 else f"{value:.1f}"
                fm = QFontMetrics(self._tick_font)
                label_rect = fm.boundingRect(label)
                label_angle = angle
                lx = center.x() + (r1 - 14) * self._cos_deg(label_angle) - label_rect.width() / 2
                ly = center.y() + (r1 - 14) * self._sin_deg(label_angle) + label_rect.height() / 3
                painter.setFont(self._tick_font)
                painter.drawText(QPointF(lx, ly), label)
        
        # Minor ticks
        painter.setPen(QPen(QColor(100, 100, 120), 1))
        for i in range(self.config.major_ticks * self.config.minor_ticks + 1):
            if i % self.config.minor_ticks == 0:
                continue
            value = self.config.min_value + i * minor_step
            angle = self._value_to_angle(value)
            
            r1 = radius - 8
            r2 = radius - 2
            
            x1 = center.x() + r1 * self._cos_deg(angle)
            y1 = center.y() + r1 * self._sin_deg(angle)
            x2 = center.x() + r2 * self._cos_deg(angle)
            y2 = center.y() + r2 * self._sin_deg(angle)
            
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    
    def _draw_needle(self, painter: QPainter, center: QPointF, radius: float) -> None:
        """Draw the gauge needle."""
        angle = self._value_to_angle(self._value)
        
        # Needle length
        needle_len = radius - 18
        needle_width = 3
        
        # Needle color based on value
        if self.config.critical_threshold and self._value >= self.config.critical_threshold:
            needle_color = self._critical_color
        elif self.config.warning_threshold and self._value >= self.config.warning_threshold:
            needle_color = self._warning_color
        else:
            needle_color = self._accent_color
        
        # Needle polygon (triangle)
        tip_x = center.x() + needle_len * self._cos_deg(angle)
        tip_y = center.y() + needle_len * self._sin_deg(angle)
        
        base_angle = angle + 90
        base_x1 = center.x() + needle_width * self._cos_deg(base_angle)
        base_y1 = center.y() + needle_width * self._sin_deg(base_angle)
        base_x2 = center.x() - needle_width * self._cos_deg(base_angle)
        base_y2 = center.y() - needle_width * self._sin_deg(base_angle)
        
        needle_poly = QPolygonF([
            QPointF(tip_x, tip_y),
            QPointF(base_x1, base_y1),
            QPointF(base_x2, base_y2),
        ])
        
        # Needle gradient
        needle_grad = QLinearGradient(
            center.x(), center.y(),
            tip_x, tip_y
        )
        needle_grad.setColorAt(0, needle_color.lighter(150))
        needle_grad.setColorAt(1, needle_color)
        
        painter.setBrush(QBrush(needle_grad))
        painter.setPen(QPen(needle_color.darker(150), 1))
        painter.drawPolygon(needle_poly)
        
        # Needle tail (counterweight)
        tail_len = 15
        tail_x = center.x() - tail_len * self._cos_deg(angle)
        tail_y = center.y() - tail_len * self._sin_deg(angle)
        painter.setPen(QPen(needle_color, 3))
        painter.drawLine(QPointF(center.x(), center.y()), QPointF(tail_x, tail_y))
    
    def _draw_hub(self, painter: QPainter, center: QPointF, radius: float) -> None:
        """Draw center hub."""
        hub_radius = 12
        hub_grad = QRadialGradient(center, hub_radius)
        hub_grad.setColorAt(0, QColor(60, 60, 70))
        hub_grad.setColorAt(1, QColor(20, 20, 30))
        painter.setBrush(QBrush(hub_grad))
        painter.setPen(QPen(QColor(80, 80, 90), 1))
        painter.drawEllipse(center, hub_radius, hub_radius)
        
        # Inner highlight
        painter.setBrush(QBrush(QColor(80, 80, 100, 100)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, hub_radius - 3, hub_radius - 3)
    
    def _draw_digital_value(self, painter: QPainter, center: QPointF, radius: float) -> None:
        """Draw digital value display in center."""
        if self.config.decimals == 0:
            text = f"{self._value:.0f}"
        else:
            text = f"{self._value:.{self.config.decimals}f}"
        
        painter.setFont(self._value_font)
        fm = QFontMetrics(self._value_font)
        text_rect = fm.boundingRect(text)
        
        # Value position (below center)
        y_pos = center.y() + radius * 0.15
        x_pos = center.x() - text_rect.width() / 2
        
        # Shadow
        painter.setPen(QColor(0, 0, 0, 180))
        painter.drawText(QPointF(x_pos + 1, y_pos + 1), text)
        
        # Main text - color based on thresholds
        if self.config.critical_threshold and self._value >= self.config.critical_threshold:
            painter.setPen(self._critical_color)
        elif self.config.warning_threshold and self._value >= self.config.warning_threshold:
            painter.setPen(self._warning_color)
        else:
            painter.setPen(self._text_color)
        painter.drawText(QPointF(x_pos, y_pos), text)
        
        # Unit
        painter.setFont(self._unit_font)
        unit_fm = QFontMetrics(self._unit_font)
        unit_rect = unit_fm.boundingRect(self.config.unit)
        ux = center.x() - unit_rect.width() / 2
        uy = y_pos + text_rect.height() + 4
        painter.setPen(QColor(150, 150, 160))
        painter.drawText(QPointF(ux, uy), self.config.unit)
    
    def _draw_title(self, painter: QPainter, center: QPointF, radius: float) -> None:
        """Draw gauge title at top."""
        painter.setFont(self._title_font)
        fm = QFontMetrics(self._title_font)
        text_rect = fm.boundingRect(self.config.title)
        
        y_pos = center.y() - radius + 22
        x_pos = center.x() - text_rect.width() / 2
        
        painter.setPen(QColor(140, 140, 150))
        painter.drawText(QPointF(x_pos, y_pos), self.config.title)
    
    def _value_to_angle(self, value: float) -> float:
        """Convert value to angle in degrees."""
        normalized = (value - self.config.min_value) / (self.config.max_value - self.config.min_value)
        normalized = max(0.0, min(1.0, normalized))
        return self.config.start_angle + normalized * self.config.sweep_angle
    
    def _cos_deg(self, deg: float) -> float:
        import math
        return math.cos(math.radians(deg))
    
    def _sin_deg(self, deg: float) -> float:
        import math
        return math.sin(math.radians(deg))
    
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update()


class DualGauge(QWidget):
    """Widget showing two related gauges (Actual vs Specified) side by side."""
    
    def __init__(
        self,
        actual_config: GaugeConfig,
        specified_config: GaugeConfig,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        
        layout = QHBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.actual_gauge = CircularGauge(actual_config)
        self.specified_gauge = CircularGauge(specified_config)
        
        # Style specified gauge differently (subtle, muted gold)
        self.specified_gauge._accent_color = QColor(200, 170, 120)
        
        layout.addWidget(self.actual_gauge)
        layout.addWidget(self.specified_gauge)
    
    def set_values(self, actual: float, specified: float) -> None:
        self.actual_gauge.set_value(actual)
        self.specified_gauge.set_value(specified)


class StatusIndicator(QWidget):
    """Compact status indicator with label and colored dot."""
    
    def __init__(self, label: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._label = label
        self._status = "unknown"  # unknown, connected, disconnected, error
        self._message = ""
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        
        self._dot = QLabel("●")
        self._dot.setFont(QFont("Inter", 12))
        self._dot.setFixedWidth(16)
        
        self._text = QLabel(label)
        self._text.setFont(QFont("Inter", 9))
        
        self._detail = QLabel("")
        self._detail.setFont(QFont("Inter", 8))
        self._detail.setStyleSheet("color: #888;")
        
        layout.addWidget(self._dot)
        layout.addWidget(self._text)
        layout.addWidget(self._detail)
        layout.addStretch()
        
        self.set_status("unknown")
    
    def set_status(self, status: str, message: str = "") -> None:
        self._status = status
        self._message = message
        
        colors = {
            "connected": "#00cc66",
            "disconnected": "#ff4444",
            "connecting": "#ffaa00",
            "error": "#ff4444",
            "unknown": "#888888",
        }
        color = colors.get(status, "#888888")
        
        self._dot.setStyleSheet(f"color: {color};")
        self._text.setText(self._label)
        if message:
            self._detail.setText(message)


class FaultCodesDialog(QDialog):
    """Modal dialog showing the DTCs (fault codes) currently stored on the ECU."""

    def __init__(self, codes: list[FaultCode], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Fault Codes (DTCs)")
        self.resize(480, 320)

        layout = QVBoxLayout(self)

        if not codes:
            label = QLabel("No fault codes stored. ✓")
            label.setStyleSheet("font-size: 14px; color: #00cc66; padding: 24px;")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        else:
            info = QLabel(
                f"{len(codes)} fault code(s) found. Status byte meaning (sporadic/permanent, "
                f"signal range, etc.) is ECU-specific - shown raw for now."
            )
            info.setWordWrap(True)
            info.setStyleSheet("color: #aaa; padding: 4px;")
            layout.addWidget(info)

            table = QTableWidget(len(codes), 2)
            table.setHorizontalHeaderLabels(["DTC Code", "Status Byte"])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)

            for row, fc in enumerate(codes):
                table.setItem(row, 0, QTableWidgetItem(fc.code_str))
                table.setItem(row, 1, QTableWidgetItem(f"0x{fc.status_byte:02X}"))

            layout.addWidget(table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class TrendChart(QWidget):
    """
    Lightweight rolling line chart (no external plotting library needed).
    Tracks up to a handful of named series, each with its own color and value range.
    """

    def __init__(self, max_points: int = 300, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(140)
        self._max_points = max_points
        self._series: dict[str, dict] = {}  # name -> {color, min, max, values: list[float]}

    def add_series(self, name: str, color: str, min_value: float, max_value: float) -> None:
        self._series[name] = {
            "color": QColor(color),
            "min": min_value,
            "max": max_value,
            "values": [],
        }

    def push(self, name: str, value: float) -> None:
        s = self._series.get(name)
        if s is None:
            return
        s["values"].append(value)
        if len(s["values"]) > self._max_points:
            s["values"].pop(0)
        self.update()

    def clear_history(self) -> None:
        for s in self._series.values():
            s["values"].clear()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(8, 8, -8, -8)
        painter.fillRect(self.rect(), QColor("#1c1c24"))

        # Grid lines
        painter.setPen(QPen(QColor("#333"), 1))
        for i in range(1, 4):
            y = rect.top() + rect.height() * i / 4
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        for name, s in self._series.items():
            values = s["values"]
            if len(values) < 2:
                continue
            vmin, vmax = s["min"], s["max"]
            span = (vmax - vmin) or 1.0

            painter.setPen(QPen(s["color"], 2))
            points = []
            n = len(values)
            for i, v in enumerate(values):
                x = rect.left() + rect.width() * i / max(1, self._max_points - 1)
                norm = max(0.0, min(1.0, (v - vmin) / span))
                y = rect.bottom() - norm * rect.height()
                points.append(QPointF(x, y))
            for i in range(len(points) - 1):
                painter.drawLine(points[i], points[i + 1])

        # Legend
        legend_x = rect.left()
        painter.setFont(QFont("Inter", 8))
        for name, s in self._series.items():
            painter.setPen(QPen(s["color"], 2))
            painter.drawLine(QPointF(legend_x, rect.top() + 4), QPointF(legend_x + 14, rect.top() + 4))
            painter.setPen(QColor("#ccc"))
            current = f"{s['values'][-1]:.0f}" if s["values"] else "--"
            painter.drawText(int(legend_x + 18), int(rect.top() + 8), f"{name}: {current}")
            legend_x += 110


class DigitalCard(QWidget):
    """
    Flat 'digital dashboard' style readout - an alternative look to the
    analog CircularGauge, showing the same value/thresholds as a big
    number + colored fill bar instead of a needle.
    """

    def __init__(self, config: GaugeConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self._value = config.min_value
        self.setMinimumSize(170, 110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_value(self, value: float, animated: bool = True) -> None:
        # 'animated' kept for API parity with CircularGauge.set_value; unused here
        self._value = max(self.config.min_value, min(self.config.max_value, value))
        self.update()

    @property
    def value(self) -> float:
        return self._value

    def _color_for_value(self) -> QColor:
        if self.config.critical_threshold is not None and self._value >= self.config.critical_threshold:
            return QColor("#ff4444")
        if self.config.warning_threshold is not None and self._value >= self.config.warning_threshold:
            return QColor("#ffaa00")
        return QColor("#00cc88")

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect().adjusted(1, 1, -1, -1))
        color = self._color_for_value()

        # Card background
        painter.setPen(QPen(QColor("#33333f"), 1))
        painter.setBrush(QBrush(QColor("#1e1e28")))
        painter.drawRoundedRect(rect, 10, 10)

        # Title
        painter.setPen(QColor("#a3a3b0"))
        painter.setFont(QFont("Inter", 11, QFont.Weight.DemiBold))
        painter.drawText(
            QRectF(rect.left() + 14, rect.top() + 8, rect.width() - 28, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.config.title.upper(),
        )

        # Big digital value + unit
        value_font = QFont("Inter", 30, QFont.Weight.Bold)
        painter.setFont(value_font)
        painter.setPen(color)
        value_str = f"{self._value:.{self.config.decimals}f}"
        value_rect = QRectF(rect.left() + 14, rect.top() + 26, rect.width() - 28, rect.height() - 56)
        painter.drawText(value_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value_str)

        fm_value = QFontMetrics(value_font)
        value_width = fm_value.horizontalAdvance(value_str)
        painter.setFont(QFont("Inter", 11))
        painter.setPen(QColor("#9a9aa5"))
        painter.drawText(
            QRectF(rect.left() + 18 + value_width, rect.top() + 26, rect.width() - value_width - 32, rect.height() - 56),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            self.config.unit,
        )

        # Bottom fill bar (position within min..max)
        bar_rect = QRectF(rect.left() + 14, rect.bottom() - 16, rect.width() - 28, 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#33333f"))
        painter.drawRoundedRect(bar_rect, 3, 3)

        span = (self.config.max_value - self.config.min_value) or 1.0
        frac = max(0.0, min(1.0, (self._value - self.config.min_value) / span))
        if frac > 0:
            fill_rect = QRectF(bar_rect.left(), bar_rect.top(), bar_rect.width() * frac, bar_rect.height())
            painter.setBrush(color)
            painter.drawRoundedRect(fill_rect, 3, 3)


class CustomGroupsDialog(QDialog):
    """
    Non-modal dialog letting the user query any KW1281 measuring group (not
    just the default 003/007/011), showing the raw decoded fields. Useful
    for exploring what parameters this ECU actually reports where.
    """

    request_group = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Custom Measuring Groups")
        self.resize(520, 360)
        self.setModal(False)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Query any group number (1-255). The car can only be asked for one group "
            "at a time, so this pauses briefly between requests rather than flooding it."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #aaa;")
        layout.addWidget(info)

        row = QHBoxLayout()
        row.addWidget(QLabel("Group #:"))
        self.group_spin = QSpinBox()
        self.group_spin.setRange(1, 255)
        self.group_spin.setValue(1)
        row.addWidget(self.group_spin)

        self.query_btn = QPushButton("Query")
        self.query_btn.clicked.connect(self._on_query_clicked)
        row.addWidget(self.query_btn)
        row.addStretch()
        layout.addLayout(row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Field", "Kennzahl", "Raw (a,b)", "Value", "Confirmed?"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _on_query_clicked(self) -> None:
        self.request_group.emit(self.group_spin.value())

    def show_group_result(self, group_number: int, values: list[MeasuringValue]) -> None:
        if group_number != self.group_spin.value():
            return  # a different query was in flight; ignore stale results
        self.table.setRowCount(len(values))
        for row, mv in enumerate(values):
            self.table.setItem(row, 0, QTableWidgetItem(mv.label))
            self.table.setItem(row, 1, QTableWidgetItem(str(mv.kennzahl)))
            self.table.setItem(row, 2, QTableWidgetItem(f"{mv.raw_a}, {mv.raw_b}"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{mv.value:.2f} {mv.unit}"))
            self.table.setItem(row, 4, QTableWidgetItem("yes" if mv.confirmed else "unverified"))


@dataclass
class GaugePagePreset:
    """A named, friendly bundle of KW1281 measuring groups - so the user
    picks 'Boost / Turbo' instead of having to know it means group 011."""
    name: str
    icon: str
    description: str
    group_numbers: list[int]
    gauge_keys: list[str]  # which GAUGE_CONFIGS entries this page actually shows


GAUGE_PAGE_PRESETS: list[GaugePagePreset] = [
    GaugePagePreset(
        name="Full Dashboard",
        icon="🎛️",
        description="Everything at once — RPM, MAP, MAF, boost, temperatures (groups 3, 7, 11)",
        group_numbers=[3, 7, 11],
        gauge_keys=['rpm', 'map_actual', 'map_specified', 'maf_actual', 'maf_specified',
                     'boost', 'coolant_temp', 'intake_temp', 'wastegate', 'engine_load'],
    ),
    GaugePagePreset(
        name="Engine Basics",
        icon="🔧",
        description="RPM, mass airflow, engine load (group 3)",
        group_numbers=[3],
        gauge_keys=['rpm', 'maf_actual', 'maf_specified', 'engine_load'],
    ),
    GaugePagePreset(
        name="Temperatures",
        icon="🌡️",
        description="Coolant & intake air temperature (group 7)",
        group_numbers=[7],
        gauge_keys=['coolant_temp', 'intake_temp'],
    ),
    GaugePagePreset(
        name="Boost / Turbo",
        icon="💨",
        description="MAP actual/specified, boost pressure, wastegate duty (group 11)",
        group_numbers=[11],
        gauge_keys=['map_actual', 'map_specified', 'boost', 'wastegate'],
    ),
]


class GaugePagePicker(QDialog):
    """
    Lets the user choose a named gauge page instead of hunting for the
    correct KW1281 measuring-group number themselves. The car (being an
    old ECU) can only report a handful of groups at once, so each page
    keeps that count sane.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Choose a Gauge Page")
        self.resize(440, 420)
        self.selected_preset: Optional[GaugePagePreset] = None

        layout = QVBoxLayout(self)
        info = QLabel(
            "This ECU can only report a few sensor groups at a time - pick which "
            "page you want live. You can switch pages any time from the dashboard."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #aaa; padding-bottom: 8px;")
        layout.addWidget(info)

        for preset in GAUGE_PAGE_PRESETS:
            btn = QPushButton(f"{preset.icon}   {preset.name}")
            btn.setMinimumHeight(52)
            btn.setStyleSheet(
                "QPushButton { text-align: left; padding: 10px 16px; font-size: 14px; "
                "border: 1px solid #33333f; border-radius: 8px; background: #1e1e28; color: white; } "
                "QPushButton:hover { background: #262632; border-color: #e6a94a; }"
            )
            btn.clicked.connect(lambda checked=False, p=preset: self._choose(p))
            layout.addWidget(btn)

            sub = QLabel(preset.description)
            sub.setWordWrap(True)
            sub.setStyleSheet("color: #777; font-size: 11px; padding: 0 6px 10px 6px;")
            layout.addWidget(sub)

        layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)

    def _choose(self, preset: GaugePagePreset) -> None:
        self.selected_preset = preset
        self.accept()


class CatLinkButton(QWidget):
    """
    A small clickable cat badge that opens the project's GitHub page.
    This is an original doodle drawn with QPainter, NOT GitHub's Octocat
    mascot (that's a registered trademark, not something to reproduce).
    """

    def __init__(self, url: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._url = url
        self.setFixedSize(60, 60)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip("Open project on GitHub")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            QDesktopServices.openUrl(QUrl(self._url))
        super().mousePressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Circular dark badge background
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#24292e"))
        painter.drawEllipse(1, 1, w - 2, h - 2)

        cx, cy = w / 2, h / 2 + 3
        head_r = w * 0.24

        # Ears (two simple triangles)
        painter.setBrush(QColor("#f0f0f0"))
        for side in (-1, 1):
            ear = QPainterPath()
            ear.moveTo(cx + side * head_r * 0.7, cy - head_r * 0.6)
            ear.lineTo(cx + side * head_r * 0.25, cy - head_r * 1.7)
            ear.lineTo(cx + side * head_r * 0.05, cy - head_r * 0.7)
            ear.closeSubpath()
            painter.drawPath(ear)

        # Head
        painter.drawEllipse(QPointF(cx, cy), head_r, head_r)

        # Eyes
        painter.setBrush(QColor("#24292e"))
        eye_r = head_r * 0.13
        painter.drawEllipse(QPointF(cx - head_r * 0.35, cy - head_r * 0.05), eye_r, eye_r)
        painter.drawEllipse(QPointF(cx + head_r * 0.35, cy - head_r * 0.05), eye_r, eye_r)

        # Whiskers
        painter.setPen(QPen(QColor("#f0f0f0"), 1))
        for side in (-1, 1):
            base_x = cx + side * head_r * 0.55
            base_y = cy + head_r * 0.25
            for dy in (-3, 0, 3):
                painter.drawLine(
                    QPointF(base_x, base_y + dy),
                    QPointF(base_x + side * head_r * 0.7, base_y + dy * 1.5),
                )


class LauncherWindow(QWidget):
    """
    Start screen shown when the app opens. Big, simple, plain-language
    buttons - pick what you want to do instead of landing in a technical
    dashboard full of jargon.
    """

    GITHUB_URL = "https://github.com/Pabloar7882/audi-diag"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audi A4 B5 Diagnostics")
        self.setMinimumSize(600, 620)
        self.setStyleSheet("background-color: #16161d;")
        self._dashboard: Optional["MainDashboard"] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(44, 40, 44, 24)
        outer.setSpacing(6)

        title = QLabel("🚗  Audi A4 B5 · 1.9 TDI")
        title.setStyleSheet("color: #f5f5f7; font-size: 30px; font-weight: 800;")
        subtitle = QLabel("Simple car check-up tool — pick what you want to do")
        subtitle.setStyleSheet("color: #9a9aa5; font-size: 14px; margin-bottom: 26px;")
        outer.addWidget(title)
        outer.addWidget(subtitle)

        self._add_menu_button(
            outer, "📊", "Watch the Engine Live",
            "See the speed, temperature and pressure gauges update in real time",
            self._open_dashboard_with_picker,
        )
        self._add_menu_button(
            outer, "🔧", "Check for Problems",
            "See if the car has found any issues, and clear them if needed",
            lambda: self._open_dashboard("fault_codes"),
        )
        self._add_menu_button(
            outer, "🔬", "Look at One Sensor",
            "For advanced users - pick a specific measurement to inspect",
            lambda: self._open_dashboard("groups"),
        )

        outer.addStretch()

        bottom_row = QHBoxLayout()
        exit_btn = QPushButton("Exit")
        exit_btn.setStyleSheet(
            "color: #9a9aa5; padding: 10px 22px; border: none; font-size: 13px;"
        )
        exit_btn.clicked.connect(self.close)
        bottom_row.addWidget(exit_btn)
        bottom_row.addStretch()

        self.cat_link = CatLinkButton(self.GITHUB_URL, self)
        bottom_row.addWidget(self.cat_link)
        outer.addLayout(bottom_row)

    def _add_menu_button(self, layout: QVBoxLayout, icon: str, title: str,
                          subtitle: str, on_click) -> None:
        btn = QPushButton(f"{icon}   {title}")
        btn.setMinimumHeight(76)
        btn.setStyleSheet(
            "QPushButton { text-align: left; padding: 14px 22px; font-size: 18px; font-weight: 700; "
            "border: 2px solid #2e2e3a; border-radius: 14px; background: #1e1e28; color: #f5f5f7; } "
            "QPushButton:hover { background: #262632; border-color: #e6a94a; }"
        )
        btn.clicked.connect(on_click)
        layout.addWidget(btn)

        sub = QLabel(subtitle)
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #9a9aa5; font-size: 12.5px; padding: 2px 8px 18px 8px;")
        layout.addWidget(sub)

    def _open_dashboard_with_picker(self) -> None:
        picker = GaugePagePicker(self)
        if picker.exec() == QDialog.DialogCode.Accepted and picker.selected_preset:
            preset = picker.selected_preset
            self._open_dashboard("dashboard", blocks=preset.group_numbers, gauge_keys=preset.gauge_keys)

    def _open_dashboard(self, mode: str, blocks: Optional[list[int]] = None,
                         gauge_keys: Optional[list[str]] = None) -> None:
        self._dashboard = MainDashboard(mode=mode, initial_blocks=blocks, gauge_keys=gauge_keys)
        self._dashboard.show()
        self.close()


class MainDashboard(QMainWindow):
    """
    Main application window with telemetry gauges and controls.
    """
    
    def __init__(
        self,
        mode: str = "dashboard",
        initial_blocks: Optional[list[int]] = None,
        gauge_keys: Optional[list[str]] = None,
    ):
        super().__init__()
        self.setWindowTitle("Audi A4 B5 1.9 TDI (AFN/EDC15) Diagnostics")
        self.setMinimumSize(1200, 800)
        
        # Apply dark theme
        self._apply_dark_theme()
        
        # Worker thread
        self._worker_thread: Optional[TelemetryThread] = None
        self._session_id: Optional[int] = None
        self._custom_groups_dialog: Optional["CustomGroupsDialog"] = None
        self._active_alerts: set[str] = set()  # metric names currently past critical threshold
        self._mode: str = mode  # "dashboard" | "fault_codes" | "groups"
        self._initial_blocks: list[int] = initial_blocks or [3, 7, 11]
        self._gauge_keys: Optional[list[str]] = gauge_keys
        self.setWindowTitle(
            f"Audi A4 B5 1.9 TDI (AFN/EDC15) Diagnostics — groups {self._initial_blocks}"
        )
        
        # UI setup
        self._setup_ui()
        self._setup_toolbar()
        self._setup_statusbar()
        self._apply_mode()
        
        # Auto-detect KKL adapter
        self._detect_adapters()
    
    def _apply_mode(self) -> None:
        """
        Configure what's visible depending on how this window was opened:
        - 'dashboard': show only the gauges relevant to the selected page
        - 'fault_codes' / 'groups': hide gauges/trends entirely, show a
          placeholder pointing at the relevant toolbar button instead
        """
        if self._mode == "dashboard":
            self._apply_gauge_visibility()
            return

        # Focused screens: no gauge clutter, just the connection panel +
        # toolbar action for the one thing this screen is about.
        self.view_stack.setVisible(False)
        self.trend_group.setVisible(False)
        self.view_toggle_action.setVisible(False)

        if self._mode == "fault_codes":
            self.mode_placeholder.setText(
                "🔧  Connect to the car below, then click \"Check for Problems\" above."
            )
        elif self._mode == "groups":
            self.mode_placeholder.setText(
                "🔬  Connect to the car below, then click \"Look at a Sensor\" above."
            )
            # Open it right away so it's one less click
            QTimer.singleShot(200, self._open_custom_groups_dialog_if_connected)
        self.mode_placeholder.setVisible(True)

    def _open_custom_groups_dialog_if_connected(self) -> None:
        # Silent variant used on auto-open right after entering "groups" mode -
        # don't nag with a "not connected" popup before the user has even tried.
        if self._worker_thread and self._worker_thread.isRunning():
            self._open_custom_groups_dialog()

    def _apply_gauge_visibility(self) -> None:
        """Show only the gauges relevant to the selected gauge page (self._gauge_keys)."""
        if self._gauge_keys is None:
            return  # no filtering requested - show everything (default/back-compat)

        visible = set(self._gauge_keys)

        self.rpm_container.setVisible('rpm' in visible)
        self.map_dual.setVisible('map_actual' in visible or 'map_specified' in visible)
        self.maf_dual.setVisible('maf_actual' in visible or 'maf_specified' in visible)
        self.boost_gauge.setVisible('boost' in visible)
        self.coolant_gauge.setVisible('coolant_temp' in visible)
        self.intake_gauge.setVisible('intake_temp' in visible)
        self.wastegate_gauge.setVisible('wastegate' in visible)
        self.load_gauge.setVisible('engine_load' in visible)

        for key, card in self._digital_cards.items():
            card.setVisible(key in visible)
    
    def _apply_dark_theme(self) -> None:
        """Apply the app's visual theme - warm, high-contrast, big and simple."""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #16161d;
                color: #f2f2f5;
            }
            QWidget {
                background-color: #16161d;
                color: #f2f2f5;
                font-size: 13px;
            }
            QGroupBox {
                border: 1px solid #33333f;
                border-radius: 10px;
                margin-top: 14px;
                padding-top: 10px;
                font-weight: 600;
                font-size: 14px;
                color: #e6a94a;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QPushButton {
                background-color: #23232e;
                border: 1px solid #3d3d4d;
                border-radius: 8px;
                padding: 10px 20px;
                color: #f2f2f5;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #2d2d3a;
                border-color: #e6a94a;
            }
            QPushButton:pressed {
                background-color: #3a3a4a;
            }
            QPushButton:disabled {
                background-color: #1c1c24;
                color: #666672;
                border-color: #2a2a35;
            }
            QPushButton#connectBtn {
                background-color: #2e7d32;
                border-color: #43a047;
            }
            QPushButton#connectBtn:hover {
                background-color: #388e3c;
            }
            QPushButton#disconnectBtn {
                background-color: #b23c3c;
                border-color: #cc4f4f;
            }
            QPushButton#disconnectBtn:hover {
                background-color: #c94848;
            }
            QComboBox {
                background-color: #23232e;
                border: 1px solid #3d3d4d;
                border-radius: 8px;
                padding: 8px 14px;
                color: #f2f2f5;
                font-size: 13px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #23232e;
                border: 1px solid #3d3d4d;
                selection-background-color: #4d3a1f;
            }
            QLabel {
                color: #f2f2f5;
            }
            QStatusBar {
                background-color: #1c1c24;
                border-top: 1px solid #2a2a35;
                color: #b8b8c2;
                font-size: 12px;
            }
            QProgressBar {
                border: 1px solid #3d3d4d;
                border-radius: 4px;
                background-color: #23232e;
                text-align: center;
                color: #f2f2f5;
            }
            QProgressBar::chunk {
                background-color: #e6a94a;
                border-radius: 3px;
            }
            QSplitter::handle {
                background-color: #2a2a35;
            }
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollBar:vertical {
                background: #1c1c24;
                width: 10px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: #3d3d4d;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #4d4d5d;
            }
            QToolBar {
                background-color: #1c1c24;
                border: none;
                padding: 6px;
                spacing: 4px;
            }
            QToolButton {
                font-size: 13px;
                padding: 8px 14px;
                border-radius: 8px;
            }
            QToolButton:hover {
                background-color: #2d2d3a;
            }
        """)
    
    def _setup_ui(self) -> None:
        """Setup main UI layout."""
        central = QWidget()
        self.setCentralWidget(central)
        
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(12, 12, 12, 12)
        
        # Top row: Connection controls and ECU info
        top_group = QGroupBox("Connection")
        top_layout = QHBoxLayout(top_group)
        top_layout.setSpacing(16)
        
        # Port selection
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(200)
        self.port_combo.addItem("/dev/ttyUSB0")
        self.port_combo.setEditable(False)
        
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["10400", "9600", "38400", "57600"])
        self.baud_combo.setCurrentText("10400")
        self.baud_combo.setMaximumWidth(100)
        
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("connectBtn")
        self.connect_btn.setMinimumWidth(120)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setObjectName("disconnectBtn")
        self.disconnect_btn.setMinimumWidth(120)
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        
        self.refresh_ports_btn = QPushButton("Refresh")
        self.refresh_ports_btn.setMaximumWidth(80)
        self.refresh_ports_btn.clicked.connect(self._detect_adapters)
        
        top_layout.addWidget(QLabel("Port:"))
        top_layout.addWidget(self.port_combo)
        top_layout.addWidget(QLabel("Baud:"))
        top_layout.addWidget(self.baud_combo)
        top_layout.addWidget(self.connect_btn)
        top_layout.addWidget(self.disconnect_btn)
        top_layout.addWidget(self.refresh_ports_btn)
        top_layout.addStretch()
        
        # ECU Info labels
        self.ecu_part_label = QLabel("Part: —")
        self.ecu_part_label.setFont(QFont("Inter", 9, QFont.Weight.Medium))
        self.ecu_sw_label = QLabel("SW: —")
        self.ecu_sw_label.setFont(QFont("Inter", 9))
        self.ecu_engine_label = QLabel("Engine: —")
        self.ecu_engine_label.setFont(QFont("Inter", 9))
        
        top_layout.addWidget(self.ecu_part_label)
        top_layout.addWidget(self.ecu_sw_label)
        top_layout.addWidget(self.ecu_engine_label)
        
        main_layout.addWidget(top_group)
        
        # Gauges area - using splitter for resizable sections
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Row 1: RPM (large) + MAP Dual + MAF Dual
        row1 = QWidget()
        row1_layout = QHBoxLayout(row1)
        row1_layout.setSpacing(16)
        row1_layout.setContentsMargins(0, 0, 0, 0)
        
        # RPM Gauge (large, prominent)
        self.rpm_gauge = CircularGauge(GAUGE_CONFIGS['rpm'])
        self.rpm_gauge.setMinimumSize(240, 240)
        self.rpm_container = QWidget()
        rpm_layout = QVBoxLayout(self.rpm_container)
        rpm_layout.setContentsMargins(0, 0, 0, 0)
        rpm_layout.addWidget(self.rpm_gauge)
        row1_layout.addWidget(self.rpm_container, 1)
        
        # MAP Dual Gauge
        self.map_dual = DualGauge(
            GAUGE_CONFIGS['map_actual'],
            GAUGE_CONFIGS['map_specified']
        )
        row1_layout.addWidget(self.map_dual, 1)
        self.map_actual_gauge = self.map_dual.actual_gauge
        self.map_specified_gauge = self.map_dual.specified_gauge
        
        # MAF Dual Gauge
        self.maf_dual = DualGauge(
            GAUGE_CONFIGS['maf_actual'],
            GAUGE_CONFIGS['maf_specified']
        )
        row1_layout.addWidget(self.maf_dual, 1)
        self.maf_actual_gauge = self.maf_dual.actual_gauge
        self.maf_specified_gauge = self.maf_dual.specified_gauge
        
        splitter.addWidget(row1)
        
        # Row 2: Boost, Temps, Wastegate, Engine Load
        row2 = QWidget()
        row2_layout = QHBoxLayout(row2)
        row2_layout.setSpacing(12)
        row2_layout.setContentsMargins(0, 0, 0, 0)
        
        self.boost_gauge = CircularGauge(GAUGE_CONFIGS['boost'])
        self.coolant_gauge = CircularGauge(GAUGE_CONFIGS['coolant_temp'])
        self.intake_gauge = CircularGauge(GAUGE_CONFIGS['intake_temp'])
        self.wastegate_gauge = CircularGauge(GAUGE_CONFIGS['wastegate'])
        self.load_gauge = CircularGauge(GAUGE_CONFIGS['engine_load'])
        
        for gauge in [self.boost_gauge, self.coolant_gauge, self.intake_gauge,
                      self.wastegate_gauge, self.load_gauge]:
            row2_layout.addWidget(gauge, 1)
        
        splitter.addWidget(row2)
        
        # Set splitter sizes (60% top, 40% bottom)
        splitter.setSizes([480, 200])

        # Two interchangeable interfaces sharing the same live data:
        # index 0 = classic analog gauges (unchanged), index 1 = new digital-card view
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(splitter)
        self.view_stack.addWidget(self._build_modern_view())
        main_layout.addWidget(self.view_stack, 1)
        
        # Trend chart (RPM / Boost / Coolant history)
        self.trend_group = QGroupBox("Trends")
        trend_layout = QVBoxLayout(self.trend_group)
        self.trend_chart = TrendChart()
        self.trend_chart.add_series("RPM", "#e6a94a", 0, 6000)
        self.trend_chart.add_series("Boost", "#ff9500", -1000, 1500)
        self.trend_chart.add_series("Coolant", "#ff4444", -20, 130)
        trend_layout.addWidget(self.trend_chart)
        main_layout.addWidget(self.trend_group)
        
        # Placeholder shown instead of gauges/trends in "fault_codes" / "groups" modes
        self.mode_placeholder = QLabel("")
        self.mode_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mode_placeholder.setStyleSheet("color: #9a9aa5; font-size: 19px; font-weight: 600; padding: 60px;")
        self.mode_placeholder.setVisible(False)
        main_layout.addWidget(self.mode_placeholder, 1)
        
        # Bottom status row
        status_group = QGroupBox("Status")
        status_layout = QHBoxLayout(status_group)
        status_layout.setSpacing(24)
        
        self.conn_status = StatusIndicator("Connection")
        self.poll_status = StatusIndicator("Polling")
        self.error_status = StatusIndicator("Errors")
        self.frame_status = StatusIndicator("Frames")
        
        status_layout.addWidget(self.conn_status)
        status_layout.addWidget(self.poll_status)
        status_layout.addWidget(self.error_status)
        status_layout.addWidget(self.frame_status)
        status_layout.addStretch()
        
        main_layout.addWidget(status_group)
    
    def _build_modern_view(self) -> QWidget:
        """Build the alternative 'digital dashboard' interface (DigitalCard grid)."""
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setSpacing(12)

        keys = [
            'rpm', 'map_actual', 'map_specified', 'maf_actual', 'maf_specified',
            'boost', 'coolant_temp', 'intake_temp', 'wastegate', 'engine_load',
        ]
        self._digital_cards: dict[str, DigitalCard] = {}
        cols = 5
        for i, key in enumerate(keys):
            card = DigitalCard(GAUGE_CONFIGS[key])
            self._digital_cards[key] = card
            grid.addWidget(card, i // cols, i % cols)

        return widget
    
    def _setup_toolbar(self) -> None:
        """Setup toolbar with actions."""
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        self.addToolBar(toolbar)
        
        # Back to launcher menu
        back_action = toolbar.addAction("⬅  Menu")
        back_action.setToolTip("Go back to the start screen")
        back_action.triggered.connect(self._back_to_menu)
        
        toolbar.addSeparator()
        
        # Logging toggle
        self.log_action = toolbar.addAction("⏺  Record")
        self.log_action.setToolTip("Save what the car is doing to a file")
        self.log_action.setCheckable(True)
        self.log_action.toggled.connect(self._on_logging_toggled)
        
        toolbar.addSeparator()
        
        # Fault codes (DTCs)
        read_errors_action = toolbar.addAction("📋  Check for Problems")
        read_errors_action.setToolTip("Read stored fault codes from the car")
        read_errors_action.triggered.connect(self._read_errors)
        
        clear_action = toolbar.addAction("🧹  Clear Problems")
        clear_action.setToolTip("Erase stored fault codes from the car")
        clear_action.triggered.connect(self._clear_errors)
        
        toolbar.addSeparator()
        
        # Custom measuring groups explorer
        custom_groups_action = toolbar.addAction("🔬  Look at a Sensor")
        custom_groups_action.setToolTip("Advanced: inspect any measuring group by number")
        custom_groups_action.triggered.connect(self._open_custom_groups_dialog)
        
        # Fullscreen
        fs_action = toolbar.addAction("⛶  Big Screen")
        fs_action.setToolTip("Fill the whole screen")
        fs_action.setCheckable(True)
        fs_action.toggled.connect(self._toggle_fullscreen)
        
        # Alternative interface toggle
        self.view_toggle_action = toolbar.addAction("🔁  Switch Look")
        self.view_toggle_action.setToolTip("Switch between dial gauges and big numbers")
        self.view_toggle_action.setCheckable(True)
        self.view_toggle_action.toggled.connect(self._toggle_interface)
    
    def _setup_statusbar(self) -> None:
        """Setup status bar."""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        self.status_label = QLabel("Ready - Select port and click Connect")
        self.status_bar.addWidget(self.status_label, 1)
        
        self.poll_rate_label = QLabel("Poll: 0 Hz")
        self.status_bar.addPermanentWidget(self.poll_rate_label)
        
        self.uptime_label = QLabel("Uptime: 00:00:00")
        self.status_bar.addPermanentWidget(self.uptime_label)
        
        # Cat badge - links to the project's GitHub page (kept consistent with the launcher)
        self.cat_link = CatLinkButton(LauncherWindow.GITHUB_URL, self)
        self.status_bar.addPermanentWidget(self.cat_link)
        
        # Uptime timer
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._update_uptime)
        self._session_start_time = 0
    
    def _detect_adapters(self) -> None:
        """Preenche o seletor com todas as portas série presentes no sistema."""
        from kw1281_handler import list_serial_ports

        ports = list_serial_ports()
        current_device = self._current_port()

        self.port_combo.clear()

        if not ports:
            self.status_label.setText(
                "Nenhuma porta série encontrada - liga o cabo KKL e clica em Refresh"
            )
            return

        kkl_count = 0
        for p in ports:
            # Windows já costuma incluir "(COM3)" na descrição — não repetir
            desc = p['description'] or ''
            desc = desc.replace(f"({p['device']})", "").strip()
            if p['is_kkl']:
                label = f"{p['device']}  —  {desc}  (KKL)" if desc else f"{p['device']}  (KKL)"
                kkl_count += 1
            else:
                label = f"{p['device']}  —  {desc}" if desc else p['device']
            self.port_combo.addItem(label, p['device'])

        restored = False
        for i in range(self.port_combo.count()):
            if self.port_combo.itemData(i) == current_device:
                self.port_combo.setCurrentIndex(i)
                restored = True
                break
        if not restored:
            self.port_combo.setCurrentIndex(0)

        if kkl_count:
            self.status_label.setText(f"Encontrado(s) {kkl_count} adaptador(es) KKL")
        else:
            self.status_label.setText(
                f"{len(ports)} porta(s) série encontrada(s) - nenhuma reconhecida como KKL, seleciona manualmente"
            )

    def _current_port(self) -> str:
        """Devolve o nome real da porta selecionada, independente do texto mostrado."""
        data = self.port_combo.currentData()
        if data:
            return data
        return self.port_combo.currentText().strip().split("  —  ")[0].strip()
    
    def _on_connect_clicked(self) -> None:
        """Handle connect button click."""
        port = self._current_port()
        baud = int(self.baud_combo.currentText())
        
        if not port:
            QMessageBox.warning(self, "Error", "Please select a serial port")
            return
        
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.port_combo.setEnabled(False)
        self.baud_combo.setEnabled(False)
        
        self.conn_status.set_status("connecting", "Initializing...")
        self.status_label.setText(f"Connecting to {port} at {baud} baud...")
        
        try:
            # Create and start worker thread
            self._worker_thread = TelemetryThread(
                port=port,
                baudrate=baud,
                poll_interval_ms=100,
                blocks=self._initial_blocks,
            )
            
            # Connect signals
            self._worker_thread.telemetry_updated.connect(self._on_telemetry)
            self._worker_thread.ecu_identified.connect(self._on_ecu_identified)
            self._worker_thread.connection_state_changed.connect(self._on_state_changed)
            self._worker_thread.error_occurred.connect(self._on_error)
            self._worker_thread.stats_updated.connect(self._on_stats)
            self._worker_thread.log_message.connect(self._on_log_message)
            self._worker_thread.fault_codes_received.connect(self._on_fault_codes_received)
            self._worker_thread.fault_codes_cleared.connect(self._on_fault_codes_cleared)
            self._worker_thread.group_reading_received.connect(self._on_group_reading_received)
            
            self._worker_thread.start()
            self._session_start_time = time.time()
            self._uptime_timer.start(1000)
        except Exception as e:
            # Não deixar isto propagar para fora do slot - mostra erro e repõe a UI
            logging.getLogger(__name__).exception("Falha ao iniciar ligação")
            QMessageBox.critical(
                self, "Erro ao ligar",
                f"Não foi possível iniciar a ligação a {port}:\n\n{type(e).__name__}: {e}"
            )
            self._worker_thread = None
            self.connect_btn.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            self.port_combo.setEnabled(True)
            self.baud_combo.setEnabled(True)
            self.conn_status.set_status("disconnected")
            self.status_label.setText("Ready - Select port and click Connect")
    
    def _on_disconnect_clicked(self) -> None:
        """Handle disconnect button click."""
        if self._worker_thread:
            self._worker_thread.stop()
            self._worker_thread = None
        
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.port_combo.setEnabled(True)
        self.baud_combo.setEnabled(True)
        
        self.conn_status.set_status("disconnected")
        self.poll_status.set_status("unknown")
        self.status_label.setText("Disconnected")
        self._uptime_timer.stop()
        self.uptime_label.setText("Uptime: 00:00:00")
        
        # Reset gauges
        self._reset_gauges()
        self._reset_ecu_info()
    
    @pyqtSlot(object)
    def _on_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        """Update gauges with new telemetry data."""
        cards = self._digital_cards
        # MB003: RPM, MAF
        if snapshot.mb003:
            mb = snapshot.mb003
            self.rpm_gauge.set_value(mb.rpm)
            self.maf_actual_gauge.set_value(mb.maf_actual_mg_stroke)
            self.maf_specified_gauge.set_value(mb.maf_specified_mg_stroke)
            self.load_gauge.set_value(mb.engine_load_pct)
            self.trend_chart.push("RPM", mb.rpm)
            cards['rpm'].set_value(mb.rpm)
            cards['maf_actual'].set_value(mb.maf_actual_mg_stroke)
            cards['maf_specified'].set_value(mb.maf_specified_mg_stroke)
            cards['engine_load'].set_value(mb.engine_load_pct)
        
        # MB007: Temperatures
        if snapshot.mb007:
            mb = snapshot.mb007
            self.coolant_gauge.set_value(mb.coolant_temp_c)
            self.intake_gauge.set_value(mb.intake_air_temp_c)
            self.trend_chart.push("Coolant", mb.coolant_temp_c)
            cards['coolant_temp'].set_value(mb.coolant_temp_c)
            cards['intake_temp'].set_value(mb.intake_air_temp_c)
        
        # MB011: MAP/Boost
        if snapshot.mb011:
            mb = snapshot.mb011
            self.map_actual_gauge.set_value(mb.map_actual_mbar)
            self.map_specified_gauge.set_value(mb.map_specified_mbar)
            self.boost_gauge.set_value(mb.boost_pressure_mbar)
            self.wastegate_gauge.set_value(mb.wastegate_duty_pct)
            self.trend_chart.push("Boost", mb.boost_pressure_mbar)
            cards['map_actual'].set_value(mb.map_actual_mbar)
            cards['map_specified'].set_value(mb.map_specified_mbar)
            cards['boost'].set_value(mb.boost_pressure_mbar)
            cards['wastegate'].set_value(mb.wastegate_duty_pct)

        self._check_alerts()

    def _check_alerts(self) -> None:
        """
        Compare every gauge's current value against its configured
        critical_threshold. Beeps once per NEW alert (not every poll cycle)
        and keeps the status bar showing which metric(s) are critical.
        """
        gauges = {
            'rpm': self.rpm_gauge,
            'map_actual': self.map_actual_gauge,
            'maf_actual': self.maf_actual_gauge,
            'boost': self.boost_gauge,
            'coolant_temp': self.coolant_gauge,
            'intake_temp': self.intake_gauge,
            'wastegate': self.wastegate_gauge,
            'engine_load': self.load_gauge,
        }

        now_critical: set[str] = set()
        for key, gauge in gauges.items():
            cfg = GAUGE_CONFIGS[key]
            if cfg.critical_threshold is not None and gauge.value >= cfg.critical_threshold:
                now_critical.add(key)

        newly_critical = now_critical - self._active_alerts
        if newly_critical:
            QApplication.beep()
            names = ", ".join(GAUGE_CONFIGS[k].title for k in newly_critical)
            self.status_label.setText(f"⚠ ALERT: {names} at critical level")

        self._active_alerts = now_critical
    
    @pyqtSlot(object)
    def _on_ecu_identified(self, ecu_id: ECUIdentification) -> None:
        """Update ECU info labels."""
        self.ecu_part_label.setText(f"Part: {ecu_id.part_number or '—'}")
        self.ecu_sw_label.setText(f"SW: {ecu_id.software_version or '—'}")
        self.ecu_engine_label.setText(f"Engine: {ecu_id.engine_code or 'AFN'}")
    
    @pyqtSlot(str)
    def _on_state_changed(self, state: str) -> None:
        """Handle connection state changes."""
        if state == WorkerState.CONNECTED.value:
            self.conn_status.set_status("connected", "KW1281 Active")
            self.poll_status.set_status("connected", "Polling blocks 3,7,11")
            self.status_label.setText("Connected - Streaming telemetry")
        elif state == WorkerState.RECONNECTING.value:
            self.conn_status.set_status("connecting", "Reconnecting...")
            self.poll_status.set_status("disconnected", "Waiting...")
        elif state == WorkerState.ERROR.value:
            self.conn_status.set_status("error", "Error")
        elif state == WorkerState.STOPPED.value:
            self.conn_status.set_status("disconnected")
            self.poll_status.set_status("unknown")
    
    @pyqtSlot(str, str)
    def _on_error(self, error_type: str, message: str) -> None:
        """Handle errors from worker."""
        self.error_status.set_status("error", f"{error_type}: {message[:40]}")
        self.status_label.setText(f"Error: {error_type} - {message}")
    
    @pyqtSlot(dict)
    def _on_stats(self, stats: dict) -> None:
        """Update statistics display."""
        total = stats.get('total_polls', 0)
        failed = stats.get('failed_polls', 0)
        timeouts = stats.get('timeouts', 0)
        crc_errors = stats.get('checksum_errors', 0)
        
        self.frame_status.set_status(
            "connected" if failed == 0 else "warning" if failed < total * 0.1 else "error",
            f"OK: {total-failed}  Err: {failed}  TO: {timeouts}  CRC: {crc_errors}"
        )
        
        # Poll rate
        avg_ms = stats.get('avg_poll_ms', 0)
        if avg_ms > 0:
            hz = 1000 / avg_ms
            self.poll_rate_label.setText(f"Poll: {hz:.1f} Hz ({avg_ms:.0f}ms)")
    
    @pyqtSlot(str, str)
    def _on_log_message(self, level: str, message: str) -> None:
        """Handle log messages from worker."""
        if level in ("WARNING", "ERROR", "CRITICAL"):
            self.status_label.setText(f"[{level}] {message}")
    
    def _reset_gauges(self) -> None:
        """Reset all gauges to zero."""
        for gauge in [self.rpm_gauge, self.map_actual_gauge, self.map_specified_gauge,
                      self.maf_actual_gauge, self.maf_specified_gauge, self.boost_gauge,
                      self.coolant_gauge, self.intake_gauge, self.wastegate_gauge, self.load_gauge]:
            gauge.set_value(gauge.config.min_value, animated=False)
        for card in self._digital_cards.values():
            card.set_value(card.config.min_value)
        self.trend_chart.clear_history()
        self._active_alerts.clear()
    
    def _reset_ecu_info(self) -> None:
        self.ecu_part_label.setText("Part: —")
        self.ecu_sw_label.setText("SW: —")
        self.ecu_engine_label.setText("Engine: —")
    
    def _update_uptime(self) -> None:
        """Update uptime display."""
        if self._session_start_time > 0:
            elapsed = time.time() - self._session_start_time
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            s = int(elapsed % 60)
            self.uptime_label.setText(f"Uptime: {h:02d}:{m:02d}:{s:02d}")
    
    def _on_logging_toggled(self, checked: bool) -> None:
        """Handle logging toggle."""
        if checked:
            self.log_action.setText("⏹  Stop Recording")
            self.status_label.setText("Recording to file...")
            # TODO: Implement database logging
        else:
            self.log_action.setText("⏺  Record")
            self.status_label.setText("Recording stopped")
    
    def _read_errors(self) -> None:
        """Request fault codes (DTCs) from the ECU."""
        if not self._worker_thread or not self._worker_thread.isRunning():
            QMessageBox.information(self, "Not connected", "Connect to the ECU first.")
            return
        self.status_label.setText("Reading fault codes...")
        self._worker_thread.request_read_fault_codes()

    @pyqtSlot(list)
    def _on_fault_codes_received(self, codes: list) -> None:
        if codes:
            self.error_status.set_status("error", f"{len(codes)} fault(s) stored")
        else:
            self.error_status.set_status("connected", "No faults")
        self.status_label.setText(f"Fault codes read: {len(codes)} found")
        dialog = FaultCodesDialog(codes, self)
        dialog.exec()

    @pyqtSlot(bool)
    def _on_fault_codes_cleared(self, success: bool) -> None:
        if success:
            self.error_status.set_status("unknown")
            self.status_label.setText("Fault codes cleared")
        else:
            self.status_label.setText("Failed to clear fault codes")

    def _clear_errors(self) -> None:
        """Send the real Clear Fault Codes command to the ECU (block title 0x05)."""
        if not self._worker_thread or not self._worker_thread.isRunning():
            QMessageBox.information(self, "Not connected", "Connect to the ECU first.")
            return
        reply = QMessageBox.question(
            self, "Clear fault codes?",
            "This will permanently erase all fault codes stored on the ECU. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.status_label.setText("Clearing fault codes...")
            self._worker_thread.request_clear_fault_codes()

    def _open_custom_groups_dialog(self) -> None:
        """Open (or bring to front) the custom measuring-group explorer."""
        if not self._worker_thread or not self._worker_thread.isRunning():
            QMessageBox.information(self, "Not connected", "Connect to the ECU first.")
            return
        if self._custom_groups_dialog is None:
            self._custom_groups_dialog = CustomGroupsDialog(self)
            self._custom_groups_dialog.request_group.connect(
                lambda n: self._worker_thread and self._worker_thread.request_group(n)
            )
        self._custom_groups_dialog.show()
        self._custom_groups_dialog.raise_()
        self._custom_groups_dialog.activateWindow()

    @pyqtSlot(int, list)
    def _on_group_reading_received(self, group_number: int, values: list) -> None:
        if self._custom_groups_dialog is not None:
            self._custom_groups_dialog.show_group_result(group_number, values)
    
    def _toggle_fullscreen(self, checked: bool) -> None:
        """Toggle fullscreen mode."""
        if checked:
            self.showFullScreen()
        else:
            self.showNormal()
    
    def _toggle_interface(self, checked: bool) -> None:
        """Switch between the classic analog gauges and the new digital-card view."""
        self.view_stack.setCurrentIndex(1 if checked else 0)
        self.view_toggle_action.setText("🔁  Dial Gauges" if checked else "🔁  Switch Look")
    
    def _back_to_menu(self) -> None:
        """Stop any active connection and go back to the launcher menu."""
        if self._worker_thread:
            self._worker_thread.stop()
            self._worker_thread = None
        launcher = LauncherWindow()
        launcher.show()
        self._launcher_ref = launcher  # keep alive - this window is about to close
        self.close()
    
    def closeEvent(self, event) -> None:
        """Handle window close."""
        if self._worker_thread:
            self._worker_thread.stop()
        event.accept()


def _install_exception_handler() -> None:
    """
    Instala um sys.excepthook global.

    Sem isto, o PyQt6 aborta o processo INTEIRO em silêncio sempre que
    acontece uma exceção não apanhada dentro de um slot (ex: ao clicar
    "Connect" com uma porta problemática). Em modo janela (sem consola),
    isso parece a aplicação a fechar-se sozinha sem dizer nada.

    Com isto instalado, o erro fica registado no logs/audi_diag.log e
    é mostrado ao utilizador numa caixa de diálogo, e a app continua a correr.
    """
    import logging
    import traceback

    err_logger = logging.getLogger("uncaught")

    def handle_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        err_logger.error("Erro não tratado:\n%s", tb_text)

        try:
            QMessageBox.critical(
                None,
                "Erro inesperado",
                f"Ocorreu um erro inesperado:\n\n"
                f"{exc_type.__name__}: {exc_value}\n\n"
                f"Detalhes completos em logs/audi_diag.log",
            )
        except Exception:
            pass  # se nem a caixa de diálogo abrir, pelo menos ficou no log

    sys.excepthook = handle_exception


def main():
    """Application entry point."""
    app = QApplication(sys.argv)
    app.setApplicationName("Audi A4 B5 Diagnostics")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("AudiDiag")
    
    _install_exception_handler()
    
    # Set default font
    font = QFont("Inter", 11)
    app.setFont(font)
    
    launcher = LauncherWindow()
    launcher.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()