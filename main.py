import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

QT_AVAILABLE = False
try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (
        QApplication,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QDoubleSpinBox,
        QTextEdit,
        QVBoxLayout,
        QWidget,
        QAbstractSpinBox,
        QStyleFactory,
        QInputDialog,
    )
    from PyQt6.QtGui import QPixmap

    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False

Curve = List[Tuple[float, float]]

# Lux Dynamics branding, with a light background
COLOR_BG = "#ffffff"
COLOR_TEXT = "#0b0b0b"
COLOR_ACCENT = "#7dc242"  # green arrow hue
COLOR_MUTED = "#4b5563"   # soft gray for secondary text


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_curve(data: Optional[dict], x_key: str, y_key: str) -> Curve:
    if not isinstance(data, dict):
        return []
    xs = data.get(x_key) or []
    ys = data.get(y_key) or []
    pairs: Curve = []
    for x, y in zip(xs, ys):
        try:
            pairs.append((float(x), float(y)))
        except (TypeError, ValueError):
            continue
    pairs.sort(key=lambda p: p[0])
    return pairs


def eval_curve(points: Curve, target: float) -> Optional[float]:
    if not points:
        return None
    xs, ys = zip(*points)
    if target <= xs[0]:
        return ys[0]
    if target >= xs[-1]:
        return ys[-1]
    for idx in range(1, len(xs)):
        if target <= xs[idx]:
            x0, y0 = xs[idx - 1], ys[idx - 1]
            x1, y1 = xs[idx], ys[idx]
            if x1 == x0:
                return y1
            ratio = (target - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    return ys[-1]


@dataclass
class Driver:
    label: str
    min_input_v: float
    max_input_v: float
    min_v: float
    max_v: float
    max_power: float
    efficiency_blend_weight: float
    curves: Dict[str, Curve]

    @classmethod
    def from_file(cls, path: Path) -> "Driver":
        data = json.loads(path.read_text())
        info = data.get("info", {})
        curves_raw = data.get("curves", {})
        curves = {
            "efficiency_vs_load": build_curve(curves_raw.get("efficiency_vs_load"), "load_percent", "efficiency"),
            "efficiency_vs_output_power": build_curve(curves_raw.get("efficiency_vs_output_power"), "power_w", "efficiency"),
            "efficiency_vs_vout_120": build_curve(curves_raw.get("efficiency_vs_vout_120"), "vout_v", "efficiency"),
            "efficiency_vs_vout_277": build_curve(curves_raw.get("efficiency_vs_vout_277"), "vout_v", "efficiency"),
        }
        return cls(
            label=str(info.get("model") or info.get("brand") or info.get("name") or path.stem),
            min_input_v=float(info.get("min_input_volts") or info.get("vin_min") or 0.0),
            max_input_v=float(info.get("max_input_volts") or info.get("vin_max") or 0.0),
            min_v=float(info.get("min_voltage") or info.get("vout_min") or 0.0),
            max_v=float(info.get("max_voltage") or info.get("vout_max") or 0.0),
            max_power=float(info.get("max_power") or info.get("pout_max") or 0.0),
            efficiency_blend_weight=float(info.get("efficiency_blend_weight") or 1.0),
            curves=curves,
        )

    def estimate_efficiency(self, output_v: float, output_power: float, input_v: float) -> float:
        eff_power = None
        if self.curves["efficiency_vs_output_power"]:
            eff_power = eval_curve(self.curves["efficiency_vs_output_power"], output_power)

        curve_key = "efficiency_vs_vout_277" if input_v >= 200 else "efficiency_vs_vout_120"
        eff_vout = None
        if self.curves[curve_key]:
            eff_vout = eval_curve(self.curves[curve_key], output_v)

        eff_load = None
        if self.curves["efficiency_vs_load"] and self.max_power > 0:
            load_pct = clamp((output_power / self.max_power) * 100.0, 0.0, 150.0)
            eff_load = eval_curve(self.curves["efficiency_vs_load"], load_pct)

        candidates = [c for c in (eff_power, eff_vout, eff_load) if c is not None]
        if not candidates:
            return 0.85

        primary = eff_power or eff_vout or eff_load
        others = [c for c in candidates if c is not primary]
        if not others or self.efficiency_blend_weight <= 0:
            blended = sum(candidates) / len(candidates)
        else:
            others_avg = sum(others) / len(others)
            blended = (primary * self.efficiency_blend_weight + others_avg) / (self.efficiency_blend_weight + 1.0)
        return clamp(blended, 0.5, 0.98)

    def check_limits(self, input_v: float, required_v: float, output_power: float) -> List[str]:
        issues: List[str] = []
        if self.min_input_v and input_v < self.min_input_v:
            issues.append(f"Input voltage {input_v:.1f} V is below driver min {self.min_input_v:.1f} V")
        if self.max_input_v and input_v > self.max_input_v:
            issues.append(f"Input voltage {input_v:.1f} V is above driver max {self.max_input_v:.1f} V")
        if self.min_v and required_v < self.min_v:
            issues.append(f"Load voltage {required_v:.2f} V is below driver regulation range ({self.min_v:.2f} V min)")
        if self.max_v and required_v > self.max_v:
            issues.append(f"Load voltage {required_v:.2f} V exceeds driver max {self.max_v:.2f} V")
        if self.max_power and output_power > self.max_power:
            issues.append(f"Output power {output_power:.2f} W exceeds driver limit {self.max_power:.2f} W")
        return issues


@dataclass
class Module:
    label: str
    series_count: int
    parallel_count: int
    typical_voltage: Optional[float]
    typical_voltage_per_led: Optional[float]
    typical_voltage_total: Optional[float]
    max_current: Optional[float]
    max_current_per_led: Optional[float]
    nominal_current: Optional[float]
    nominal_current_per_led: Optional[float]
    iv_curve_led: Curve

    @classmethod
    def from_file(cls, path: Path) -> "Module":
        data = json.loads(path.read_text())
        info = data.get("info", {})
        curves = data.get("curves", {})
        typical_voltage_val = info.get("typical_voltage") or info.get("typical_voltage_v") or info.get("typ_voltage")
        typical_voltage_total = info.get("typical_voltage_total") or info.get("module_voltage") or info.get("v_module")
        typical_voltage_per_led = info.get("typical_voltage_per_led") or info.get("v_f") or info.get("vf")
        return cls(
            label=str(info.get("model") or info.get("name") or info.get("led_model") or path.stem),
            series_count=int(info.get("series_count") or 1),
            parallel_count=int(info.get("parallel_count") or 1),
            typical_voltage=float(typical_voltage_val) if typical_voltage_val is not None else None,
            typical_voltage_per_led=float(typical_voltage_per_led) if typical_voltage_per_led is not None else None,
            typical_voltage_total=float(typical_voltage_total) if typical_voltage_total is not None else None,
            max_current=float(info.get("max_current")) if info.get("max_current") is not None else None,
            max_current_per_led=float(info.get("max_current_per_led")) if info.get("max_current_per_led") is not None else None,
            nominal_current=float(info.get("nominal_current")) if info.get("nominal_current") is not None else None,
            nominal_current_per_led=float(info.get("nominal_current_per_led")) if info.get("nominal_current_per_led") is not None else None,
            iv_curve_led=build_curve(curves.get("iv_curve_led"), "current_amps", "volts_per_led"),
        )

    def max_module_current(self) -> Optional[float]:
        if self.max_current is not None:
            return self.max_current
        if self.max_current_per_led is not None:
            return self.max_current_per_led * max(1, self.parallel_count)
        return None

    def suggest_current(self) -> float:
        if self.nominal_current is not None:
            return self.nominal_current
        if self.nominal_current_per_led is not None:
            return self.nominal_current_per_led * max(1, self.parallel_count)
        max_curr = self.max_module_current()
        if max_curr:
            return max_curr * 0.7
        return 0.5

    def forward_voltage(self, module_current_a: float) -> float:
        per_string_current = module_current_a / max(1, self.parallel_count)
        per_led_v = eval_curve(self.iv_curve_led, per_string_current)
        if per_led_v is None and self.typical_voltage_per_led is not None:
            per_led_v = self.typical_voltage_per_led
        if per_led_v is None and self.typical_voltage_total is not None:
            return self.typical_voltage_total
        if per_led_v is None and self.typical_voltage is not None:
            if self.series_count <= 1:
                return self.typical_voltage
            per_led_v = self.typical_voltage
        if per_led_v is None:
            raise ValueError(f"No IV data available to estimate voltage for {self.label}. Get Kailani!")
        return per_led_v * max(1, self.series_count)


def simulate(driver_path: Path, module_path: Path, drive_current: Optional[float], input_voltage: float, override_module_voltage: Optional[float] = None) -> dict:
    driver = Driver.from_file(driver_path)
    module = Module.from_file(module_path)

    current_a = drive_current if drive_current is not None else module.suggest_current()
    if current_a <= 0:
        raise ValueError("Drive current must be positive.")

    issues: List[str] = []
    if override_module_voltage is not None and override_module_voltage > 0:
        module_v = float(override_module_voltage)
    else:
        try:
            module_v = module.forward_voltage(current_a)
        except ValueError as exc:
            issues.append(str(exc))
            module_v = 0.0
    output_power = module_v * current_a
    efficiency = driver.estimate_efficiency(module_v, output_power, input_voltage) if module_v > 0 else 0.0
    input_power = output_power / efficiency if efficiency > 0 else 0.0
    module_limit = module.max_module_current()
    if module_limit is not None and current_a > module_limit:
        issues.append(f"Module current {current_a:.3f} A exceeds limit {module_limit:.3f} A")
    if module.max_current_per_led is not None:
        per_led_current = current_a / max(1, module.parallel_count)
        if per_led_current > module.max_current_per_led:
            issues.append(f"Per-LED current {per_led_current:.3f} A exceeds limit {module.max_current_per_led:.3f} A")
    issues.extend(driver.check_limits(input_voltage, module_v, output_power))

    status = "OK" if not issues else "FAIL. Get Kailani!"
    return {
        "status": status,
        "driver": driver.label,
        "module": module.label,
        "input_voltage": input_voltage,
        "module_current": current_a,
        "module_voltage": module_v,
        "driver_output_voltage": module_v,
        "output_power": output_power,
        "efficiency": efficiency,
        "input_power": input_power,
        "issues": issues,
        "used_nominal_current": drive_current is None,
    }


def print_result(result: dict) -> None:
    print(f"Driver: {result['driver']}")
    print(f"Module: {result['module']}")
    print(f"Input voltage: {result['input_voltage']:.1f} V")
    current_note = " (nominal)" if result.get("used_nominal_current") else ""
    print(f"Drive current: {result['module_current']:.3f} A{current_note}")
    print(f"Driver output voltage: {result['driver_output_voltage']:.2f} V")
    print(f"Module voltage: {result['module_voltage']:.2f} V")
    print(f"Output power: {result['output_power']:.2f} W")
    print(f"Driver efficiency: {result['efficiency'] * 100:.1f}%")
    print(f"Estimated input power: {result['input_power']:.2f} W")
    print(f"Status: {result['status']}")
    if result["issues"]:
        for issue in result["issues"]:
            print(f" - {issue}")


def _default_path(filename: str) -> Optional[str]:
    path = Path(__file__).parent / filename
    return str(path) if path.exists() else None


def _find_logo_path() -> Optional[str]:
    candidates = [
        Path(__file__).parent / "lux_logo.jpg",
        Path(__file__).parent.parent / "lux_logo.jpg",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return None


def _desktop_path() -> Optional[Path]:
    home = Path.home()
    desktop = home / "Desktop"
    return desktop if desktop.exists() else None


def _ensure_desktop_shortcut(exe_path: Path) -> None:
    desktop = _desktop_path()
    if not desktop:
        return
    shortcut = desktop / "ProDriver - Bite Edition.url"
    if shortcut.exists():
        return
    try:
        lines = [
            "[InternetShortcut]",
            f"URL=file:///{exe_path.as_posix()}",
            f"IconFile={exe_path.as_posix()}",
            "IconIndex=0",
        ]
        shortcut.write_text("\n".join(lines))
    except Exception:
        pass


if QT_AVAILABLE:
    class SimulationWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("ProDriver: Bite Edition")
            self._build_ui()
            self._apply_theme()

        def _build_ui(self) -> None:
            central = QWidget(self)
            layout = QVBoxLayout()

            # Header with product label, logo, and author
            header = QVBoxLayout()
            header.setAlignment(Qt.AlignmentFlag.AlignLeft)
            top_row = QHBoxLayout()
            top_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
            self.product_label = QLabel(
                "<span style='color: #7dc242; font-weight: bold;'>Pro</span>"
                "<span style='color: #0b0b0b; font-weight: bold;'>Driver</span>"
            )
            self.product_label.setStyleSheet("font-size: 36pt; font-family: 'Century Gothic', 'Arial', sans-serif;")
            self.product_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            top_row.addWidget(self.product_label, 0)

            logo_path = _find_logo_path()
            self.logo_label = QLabel()
            self.logo_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if logo_path:
                pix = QPixmap(logo_path)
                if not pix.isNull():
                    self.logo_label.setPixmap(pix.scaledToHeight(72, Qt.TransformationMode.SmoothTransformation))
            top_row.addStretch(1)
            top_row.addWidget(self.logo_label, 0)
            header.addLayout(top_row)

            self.name_label = QLabel("Kailani Alarcon")
            self.name_label.setStyleSheet("color: #6b7280; font-size: 9pt;")
            self.name_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            header.addWidget(self.name_label)
            layout.addLayout(header)

            form = QFormLayout()
            self.driver_path = QLineEdit(_default_path("BST1.json") or _default_path("BST2.json") or "")
            self.driver_path.setMinimumWidth(360)
            btn_browse_driver = QPushButton("Browse")
            btn_browse_driver.clicked.connect(self._choose_driver)
            driver_row = QHBoxLayout()
            driver_row.addWidget(self.driver_path)
            driver_row.addWidget(btn_browse_driver)
            form.addRow("Driver:", driver_row)

            self.module_path = QLineEdit(_default_path("HO7_4800lm.json") or _default_path("HO5.json") or "")
            self.module_path.setMinimumWidth(360)
            btn_browse_module = QPushButton("Browse")
            btn_browse_module.clicked.connect(self._choose_module)
            module_row = QHBoxLayout()
            module_row.addWidget(self.module_path)
            module_row.addWidget(btn_browse_module)
            form.addRow("Module:", module_row)

            input_row = QHBoxLayout()
            self.input_v = QDoubleSpinBox()
            self.input_v.setRange(0.0, 600.0)
            self.input_v.setDecimals(1)
            self.input_v.setSingleStep(1.0)
            self.input_v.setValue(120.0)
            self.input_v.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
            input_row.addWidget(self.input_v)
            input_row.addWidget(QLabel("V"))
            form.addRow("Input voltage:", input_row)

            current_row = QHBoxLayout()
            self.current_spin = QDoubleSpinBox()
            self.current_spin.setRange(1.0, 5000.0)
            self.current_spin.setDecimals(0)
            self.current_spin.setSingleStep(10.0)
            self.current_spin.setValue(600.0)
            self.current_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
            current_row.addWidget(self.current_spin)
            current_row.addWidget(QLabel("mA"))
            form.addRow("Drive current:", current_row)

            layout.addLayout(form)

            btn_calc = QPushButton("Calculate")
            btn_calc.clicked.connect(self._run_calc)
            layout.addWidget(btn_calc)

            self.lbl_driver = QLabel("--")
            self.lbl_module = QLabel("--")
            self.lbl_status = QLabel("Status: --")
            self.lbl_driver_v = QLabel("Driver output voltage: -- V")
            self.lbl_power = QLabel("Output power: -- W")
            self.lbl_eff = QLabel("Driver efficiency: -- %")
            self.lbl_input_p = QLabel("Estimated input power: -- W")

            for lbl in (
                self.lbl_driver,
                self.lbl_module,
                self.lbl_status,
                self.lbl_driver_v,
                self.lbl_power,
                self.lbl_eff,
                self.lbl_input_p,
            ):
                lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)

            outputs = QFormLayout()
            outputs.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            outputs.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
            outputs.addRow("Driver:", self.lbl_driver)
            outputs.addRow("Module:", self.lbl_module)
            outputs.addRow("", self.lbl_status)
            outputs.addRow("", self.lbl_driver_v)
            outputs.addRow("", self.lbl_power)
            outputs.addRow("", self.lbl_eff)
            outputs.addRow("", self.lbl_input_p)
            layout.addLayout(outputs)

            self.issues_box = QTextEdit()
            self.issues_box.setReadOnly(True)
            self.issues_box.setPlaceholderText("No issues.")
            layout.addWidget(QLabel("Issues:"))
            layout.addWidget(self.issues_box)

            central.setLayout(layout)
            self.setCentralWidget(central)

        def _choose_driver(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "Select driver JSON", ".", "JSON Files (*.json)")
            if path:
                self.driver_path.setText(path)

        def _choose_module(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "Select module JSON", ".", "JSON Files (*.json)")
            if path:
                self.module_path.setText(path)

        def _run_calc(self) -> None:
            driver_file = self.driver_path.text().strip()
            module_file = self.module_path.text().strip()
            if not driver_file or not module_file:
                QMessageBox.warning(self, "Missing file", "Please select both driver and module JSON files.")
                return
            drive_current_ma = float(self.current_spin.value())
            drive_current = drive_current_ma / 1000.0
            try:
                result = simulate(Path(driver_file), Path(module_file), drive_current, float(self.input_v.value()))
            except Exception as exc:
                QMessageBox.warning(self, "Simulation failed", str(exc))
                return

            # If missing IV data, ask the user for a module voltage and retry once.
            missing_iv = any("No IV data available" in issue for issue in result.get("issues", []))
            if missing_iv and result.get("module_voltage", 0) == 0.0:
                val, ok = QInputDialog.getDouble(
                    self,
                    "Enter module voltage",
                    "Enter estimated module voltage (V):",
                    40.0,
                    0.0,
                    1000.0,
                    2,
                )
                if ok and val > 0:
                    try:
                        result = simulate(Path(driver_file), Path(module_file), drive_current, float(self.input_v.value()), override_module_voltage=val)
                    except Exception as exc:
                        QMessageBox.warning(self, "Simulation failed", str(exc))
                        return

            self._render_result(result)

        def _render_result(self, result: dict) -> None:
            self.lbl_driver.setText(result["driver"])
            self.lbl_module.setText(result["module"])
            status_color = "green" if result["status"] == "OK" else "red"
            self.lbl_status.setText(f"Status: {result['status']}")
            self.lbl_status.setStyleSheet(f"color: {status_color}; font-weight: bold")
            self.lbl_driver_v.setText(f"Driver output voltage: {result['driver_output_voltage']:.2f} V")
            self.lbl_power.setText(f"Output power: {result['output_power']:.2f} W")
            self.lbl_eff.setText(f"Driver efficiency: {result['efficiency'] * 100:.1f} %")
            self.lbl_input_p.setText(f"Estimated input power: {result['input_power']:.2f} W")

            if result["issues"]:
                self.issues_box.setPlainText("\n".join(result["issues"]))
            else:
                self.issues_box.setPlainText("No issues. All limits OK.")

        def _apply_theme(self) -> None:
            theme = f"""
                QMainWindow {{
                    background-color: {COLOR_BG};
                    color: {COLOR_TEXT};
                    font-family: "Century Gothic", "Arial", sans-serif;
                }}
                QWidget {{
                    background-color: {COLOR_BG};
                    color: {COLOR_TEXT};
                    font-family: "Century Gothic", "Arial", sans-serif;
                }}
                QLabel {{
                    color: {COLOR_TEXT};
                    font-size: 12pt;
                    font-family: "Century Gothic", "Arial", sans-serif;
                }}
                QLineEdit, QTextEdit {{
                    background-color: #f5f5f5;
                    color: {COLOR_TEXT};
                    border: 1px solid #d1d5db;
                    padding: 4px;
                    font-family: "Century Gothic", "Arial", sans-serif;
                }}
                QDoubleSpinBox {{
                    background-color: #f5f5f5;
                    color: {COLOR_TEXT};
                    border: 1px solid #d1d5db;
                    padding: 2px 4px;
                    min-height: 28px;
                    font-family: "Century Gothic", "Arial", sans-serif;
                }}
                QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
                    background: #e5e7eb;
                    width: 18px;
                    border: 1px solid #d1d5db;
                }}
                QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {{
                    width: 10px;
                    height: 10px;
                }}
                QDoubleSpinBox::up-arrow {{
                    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'><path d='M1 7 L5 1 L9 7 Z' fill='%230b0b0b'/></svg>");
                }}
                QDoubleSpinBox::down-arrow {{
                    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'><path d='M1 3 L5 9 L9 3 Z' fill='%230b0b0b'/></svg>");
                }}
                QCheckBox {{
                    color: {COLOR_TEXT};
                }}
                QPushButton {{
                    background-color: {COLOR_ACCENT};
                    color: #0b0b0b;
                    border: 1px solid {COLOR_ACCENT};
                    padding: 6px 12px;
                    font-weight: bold;
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    background-color: #8ed452;
                    border-color: #8ed452;
                }}
                QPlainTextEdit, QTextEdit {{
                    background-color: #f9fafb;
                    color: {COLOR_TEXT};
                    border: 1px solid #d1d5db;
                }}
                QToolTip {{
                    background-color: #e5e7eb;
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_ACCENT};
                }}
            """
            self.setStyleSheet(theme)

    def launch_ui() -> None:
        app = QApplication(sys.argv)
        try:
            app.setStyle(QStyleFactory.create("Fusion"))
        except Exception:
            pass
        font = app.font()
        if font.pointSize() <= 0:
            font.setPointSize(10)
            app.setFont(font)
        # Create a desktop shortcut to this executable if we're running frozen.
        if getattr(sys, "frozen", False):
            _ensure_desktop_shortcut(Path(sys.executable))
        window = SimulationWindow()
        window.resize(520, 420)
        window.show()
        sys.exit(app.exec())
else:
    def launch_ui() -> None:
        raise RuntimeError("PyQt6 is not installed. Install it to run the UI (pip install PyQt6).")


def main() -> None:
    # No arguments: launch the UI, matching the original behavior.
    if len(sys.argv) == 1:
        if not QT_AVAILABLE:
            sys.stderr.write("PyQt6 is required to launch the UI. Install with `pip install PyQt6` or run with CLI arguments.\n")
            sys.exit(1)
        launch_ui()
        return

    parser = argparse.ArgumentParser(
        description="Calculate driver input power and module output voltage using realistic curve data."
    )
    parser.add_argument("--driver", required=True, help="Path to the driver JSON definition.")
    parser.add_argument("--module", required=True, help="Path to the LED module JSON definition.")
    parser.add_argument(
        "--current",
        type=float,
        help="Desired module drive current in amps. If omitted, the module nominal current is used.",
    )
    parser.add_argument(
        "--input-v",
        type=float,
        default=120.0,
        help="AC input voltage feeding the driver (default: 120 V).",
    )
    args = parser.parse_args()

    result = simulate(Path(args.driver), Path(args.module), args.current, float(args.input_v))
    print_result(result)


if __name__ == "__main__":
    main()
