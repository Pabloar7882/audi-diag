"""
Modal dialogs for Audi A4 B5 Diagnostics GUI.
Natively Windows-style dialogs integrated with MainDashboard theme.
"""

from PyQt6.QtCore import Qt, QRectF, QPointF, QEasingCurve, QPropertyAnimation, QTimer
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QSpinBox, QPlainTextEdit, QTextEdit, QComboBox, QCheckBox, QGroupBox,
    QRadioButton, QButtonGroup, QProgressBar, QScrollArea, QSizePolicy,
    QFormLayout, QSplitter, QApplication
)

from src.kw1281_handler import ECUIdentification
class ECUIdentificationDialog(QDialog):
    """
    Dialog para exibir informações de identificação do ECU.
    Igual ao estilo do MainDashboard, com tema escuro e gauges.
    """

    def __init__(self, parent=None, ecu_id=None):
        super().__init__(parent)
        self.ecu_id = ecu_id or ECUIdentification()
        self.setWindowTitle("Audi A4 B5 Diagnostics - ECU Identification")
        self.setFixedSize(440, 280)
        self._apply_style()
        self._setup_ui()

    def _apply_style(self):
        """Aplica o mesmo tema do MainDashboard."""
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #16161d;
                color: #f2f2f5;
                border: 1px solid #33333f;
                border-radius: 8px;
            }}
            QLabel {{
                color: #f2f2f5;
                font-size: 13px;
            }}
            QLabel#title {{
                font-size: 16px;
                font-weight: bold;
                color: #e6a94a;
            }}
            QLabel#subtitle {{
                font-size: 11px;
                color: #9a9aa5;
            }}
            QLabel#highlight {{
                color: #00cc66;
                font-weight: bold;
            }}
        """)

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # Header
        title = QLabel("📋 ECU Identification")
        title.setObjectName("title")
        layout.addWidget(title)

        # ECU Information Grid
        info_layout = QGridLayout()
        info_layout.setSpacing(12)
        info_layout.setColumnStretch(1, 1)
        info_layout.setColumnMinimumWidth(0, 140)

        # Part Number
        layout.addWidget(QLabel("📀 Part Number:"), 0, 0)
        layout.addWidget(QLabel(f"{self.ecu_id.part_number or '—'}"), 0, 1)

        # Component
        layout.addWidget(QLabel("🔧 Component:"), 1, 0)
        layout.addWidget(QLabel(f"{self.ecu_id.component or '—'}"), 1, 1)

        # Software Version
        layout.addWidget(QLabel("💻 Software Version:"), 2, 0)
        layout.addWidget(QLabel(f"{self.ecu_id.software_version or '—'}"), 2, 1)

        # Engine Code
        layout.addWidget(QLabel("🏷️ Engine Code:"), 3, 0)
        layout.addWidget(QLabel(f"{self.ecu_id.engine_code or 'AFN'}"), 3, 1)

        # Additional Info
        if self.ecu_id.additional:
            add_label = QLabel(f"Additional: {', '.join(self.ecu_id.additional)}")
            add_label.setWordWrap(True)
            layout.addWidget(add_label, 4, 0, 1, 2)

        layout.addStretch()

        # Footer
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.setFixedWidth(80)
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)

        layout.addLayout(button_layout)
        self.setLayout(layout)
class EEPROMDialog(QDialog):
    """
    Dialog para ler/escrever dados da EEPROM do ECU.
    Igual ao Windows File Explorer, com preview, tema escuro.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_data = b""
        self.setWindowTitle("Read/Write ECU EEPROM")
        self.setMinimumSize(520, 400)
        self._apply_style()
        self._setup_ui()

    def _apply_style(self):
        """Aplica o mesmo tema do MainDashboard."""
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #16161d;
                color: #f2f2f5;
                border: 1px solid #33333f;
                border-radius: 8px;
            }}
            QLabel {{
                color: #f2f2f5;
                font-size: 13px;
            }}
            QSpinBox, QPlainTextEdit {{
                background-color: #1c1c24;
                border: 1px solid #33333f;
                border-radius: 4px;
                color: #f2f2f5;
                font-family: Consolas, Monaco, monospace;
                font-size: 11px;
            }}
            QPushButton {{
                background-color: #23232e;
                border: 1px solid #3d3d4d;
                border-radius: 4px;
                padding: 6px 12px;
                color: #f2f2f5;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: #2d2d3a;
                border-color: #e6a94a;
            }}
            QPushButton:pressed {{
                background-color: #3a3a4a;
            }}
        """)

    def _setup_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        # Controls Section (igual ao top_group no MainWindow)
        controls_group = QGroupBox("EEPROM Operations")
        controls_layout = QFormLayout()
        controls_layout.setVerticalSpacing(12)

        # Address
        self.address_input = QSpinBox()
        self.address_input.setRange(0, 65535)
        self.address_input.setDisplayIntegerBase(16)
        self.address_input.setPrefix("0x")
        controls_layout.addRow("📍 Start Address:", self.address_input)

        # Length
        self.length_input = QSpinBox()
        self.length_input.setRange(1, 1024)
        controls_layout.addRow("📦 Data Length:", self.length_input)

        # Warning
        warning_label = QLabel("⚠️ Only supported for VDO clusters")
        warning_label.setStyleSheet("color: #ff9500; font-size: 11px;")
        controls_layout.addRow("", warning_label)

        # Operation Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)

        self.read_btn = QPushButton("📖 Read")
        self.read_btn.clicked.connect(self.read_data)
        button_layout.addWidget(self.read_btn)

        self.write_btn = QPushButton("💾 Write")
        self.write_btn.clicked.connect(self.write_data)
        button_layout.addWidget(self.write_btn)

        button_layout.addStretch()
        controls_layout.addRow("", button_layout)

        controls_group.setLayout(controls_layout)
        main_layout.addWidget(controls_group)

        # Data Display (igual ao TrendChart)
        data_group = QGroupBox("Data Preview")
        data_layout = QVBoxLayout()

        self.data_text = QPlainTextEdit()
        self.data_text.setReadOnly(True)
        self.data_text.setFont(QFont("Consolas", 10))
        self.data_text.document().setDefaultStyleSheet("""
            .hex { color: #9cdcfd; }
            .ascii { color: #ce9178; }
        """)

        data_layout.addWidget(self.data_text)
        data_group.setLayout(data_layout)
        main_layout.addWidget(data_group, 1)

        # Footer
        self.status_label = QLabel("Ready - Select address and click Read")
        self.status_label.setStyleSheet("color: #9a9aa5; font-size: 11px;")
        main_layout.addWidget(self.status_label)

        self.setLayout(main_layout)

    def read_data(self):
        """Handle read operation - would call worker via signal."""
        address = self.address_input.value()
        length = self.length_input.value()

        self.status_label.setText(f"Reading {length} bytes from 0x{address:04X}...")
        self.status_label.setStyleSheet("color: #ffaa00;")

        # Would emit signal here: self.request_read_eeprom(address, length)
        # For now: simulate response
        import random
        self.current_data = bytes(random.randint(0, 255) for _ in range(length))

        self.display_data()
        self.status_label.setText(f"Read completed: {len(self.current_data)} bytes")
        self.status_label.setStyleSheet("color: #00cc66;")

    def write_data(self):
        """Handle write operation - would call worker via signal."""
        address = self.address_input.value()
        # Would emit signal here: self.request_write_eeprom(address, self.current_data)

        self.status_label.setText(f"Writing {len(self.current_data)} bytes to 0x{address:04X}...")
        self.status_label.setStyleSheet("color: #ffaa00;")

        # Simulate write result
        import random
        success = random.choice([True, False])

        if success:
            self.status_label.setText(f"Write completed: OK at 0x{address:04X}")
            self.status_label.setStyleSheet("color: #00cc66;")
        else:
            self.status_label.setText(f"Write failed: Error at 0x{address:04X}")
            self.status_label.setStyleSheet("color: #ff4444;")

    def display_data(self):
        """Display data in hex + ASCII format."""
        hex_text = []
        ascii_text = []
        address = self.address_input.value()

        for i in range(0, len(self.current_data), 16):
            chunk = self.current_data[i:i + 16]

            # Hex bytes
            hex_line = f"{address + i:08X}: "
            for j, byte in enumerate(chunk):
                hex_line += f"{byte:02X} "
            hex_line += " " * (49 - len(hex_line))
            hex_text.append(f'<span class="hex">{hex_line}</span>')

            # ASCII representation
            ascii_line = ""
            for byte in chunk:
                if 32 <= byte <= 126:
                    ascii_line += chr(byte)
                else:
                    ascii_line += "."
            ascii_text.append(f'<span class="ascii">{ascii_line}</span>')

        full_text = '\n'.join(f'{h}    {a}' for h, a in zip(hex_text, ascii_text))
        self.data_text.setHtml(full_text)
        self.data_text.verticalScrollBar().setValue(0)
class KeyAdaptationWizard(QDialog):
    """
    Wizard modal para programação de chaves.
    Igual ao Windows Configure New Hardware Wizard.
    """

    def __init__(self, parent=None, ecu_id=None):
        super().__init__(parent)
        self.ecu_id = ecu_id
        self.current_step = 0
        self.setWindowTitle("Audi A4 B5 - Key Programming Wizard")
        self.setFixedSize(520, 380)
        self._apply_style()
        self._setup_ui()
        self._update_step()

    def _apply_style(self):
        """Aplica o mesmo tema do MainDashboard."""
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #16161d;
                color: #f2f2f5;
                border: 1px solid #33333f;
                border-radius: 8px;
            }}
            QLabel {{
                color: #f2f2f5;
                font-size: 13px;
            }}
            QLabel#title {{
                font-size: 16px;
                font-weight: bold;
                color: #e6a94a;
            }}
            QLabel#subtitle {{
                font-size: 11px;
                color: #9a9aa5;
            }}
            QLabel#warning {{
                color: #ff9500;
                font-weight: bold;
                background-color: #2a1a00;
                border: 1px solid #664200;
                border-radius: 4px;
                padding: 8px;
            }}
            QPushButton {{
                background-color: #23232e;
                border: 1px solid #3d3d4d;
                border-radius: 4px;
                padding: 8px 16px;
                color: #f2f2f5;
                font-weight: 500;
                min-width: 80px;
            }}
            QPushButton:hover {{
                background-color: #2d2d3a;
                border-color: #e6a94a;
            }}
            QPushButton:pressed {{
                background-color: #3a3a4a;
            }}
            QPushButton:disabled {{
                background-color: #1c1c24;
                color: #666672;
                border-color: #2a2a35;
            }}
        """)

    def _setup_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        # Header
        self.title_label = QLabel("🔐 Key Programming Wizard")
        self.title_label.setObjectName("title")
        main_layout.addWidget(self.title_label)

        self.subtitle_label = QLabel("Program new keys for your ECU safely and easily")
        self.subtitle_label.setObjectName("subtitle")
        main_layout.addWidget(self.subtitle_label)

        # Steps area (igual ao QStackedWidget)
        self.steps_widget = QStackedWidget()

        # Step 1: Introduction
        intro_page = self._create_intro_step()
        self.steps_widget.addWidget(intro_page)

        # Step 2: Connection Status
        connection_page = self._create_connection_step()
        self.steps_widget.addWidget(connection_page)

        # Step 3: Key Settings
        settings_page = self._create_settings_step()
        self.steps_widget.addWidget(settings_page)

        # Step 4: Instructions
        instructions_page = self._create_instructions_step()
        self.steps_widget.addWidget(instructions_page)

        # Step 5: Progress
        progress_page = self._create_progress_step()
        self.steps_widget.addWidget(progress_page)

        main_layout.addWidget(self.steps_widget)

        # Button bar
        self.button_layout = QHBoxLayout()
        self.button_layout.setSpacing(10)

        self.back_btn = QPushButton("← Back")
        self.back_btn.clicked.connect(self._prev_step)
        self.back_btn.setEnabled(False)

        self.next_btn = QPushButton("Next →")
        self.next_btn.clicked.connect(self._next_step)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        self.button_layout.addWidget(self.back_btn)
        self.button_layout.addStretch()
        self.button_layout.addWidget(self.next_btn)
        self.button_layout.addWidget(self.cancel_btn)

        main_layout.addLayout(self.button_layout)
        self.setLayout(main_layout)

    def _create_intro_step(self):
        page = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(16)

        intro_text = QLabel("""
📋 **Step 1: Introduction**

This wizard will help you program new keys for your vehicle's ECU.

✅ **What you'll need:**
• New key(s) to program
• Existing key(s) for reference
• 5-10 seconds per key

⚠️ **Important:** You must remove all existing keys before starting!

📝 **Process Overview:**
1. ✅ Remove all existing keys from ignition
2. ✅ Insert new key #1, turn to RUN position  
3. ✅ Click "Start Programming" (automatically detects key)
4. ✅ Remove key #1, insert key #2
5. ✅ Wait for completion signal

💡 **Safety Notes:**
• Never leave vehicle unattended during key programming
• Don't start engine during programming sequence
• Remove key from ignition before inserting new one
        """)
        intro_text.setWordWrap(True)
        intro_text.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(intro_text)

        layout.addStretch()
        page.setLayout(layout)
        return page

    def _create_connection_step(self):
        page = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(12)

        status_label = QLabel("🔌 Connection Status")
        status_label.setStyleSheet("font-weight: bold; color: #e6a94a;")
        layout.addWidget(status_label)

        # Connection indicator (igual ao StatusIndicator)
        self.conn_widget = QWidget()
        conn_layout = QHBoxLayout(self.conn_widget)
        conn_layout.setSpacing(8)

        self.conn_dot = QLabel("●")
        self.conn_dot.setStyleSheet("color: #ff4444; font-size: 16px;")
        self.conn_label = QLabel("Not Connected")
        self.conn_detail = QLabel("")

        conn_layout.addWidget(self.conn_dot)
        conn_layout.addWidget(self.conn_label)
        conn_layout.addWidget(self.conn_detail)
        conn_layout.addStretch()

        layout.addWidget(self.conn_widget)

        # ECU Info
        ecu_frame = QGroupBox("ECU Information")
        ecu_layout = QGridLayout()
        ecu_layout.setVerticalSpacing(8)

        ecu_layout.addWidget(QLabel("📀 Part:"), 0, 0)
        self.ecu_part = QLabel("—")
        ecu_layout.addWidget(self.ecu_part, 0, 1)

        ecu_layout.addWidget(QLabel("🔧 Component:"), 1, 0)
        self.ecu_comp = QLabel("—")
        ecu_layout.addWidget(self.ecu_comp, 1, 1)

        ecu_layout.addWidget(QLabel("💻 Software:"), 2, 0)
        self.ecu_sw = QLabel("—")
        ecu_layout.addWidget(self.ecu_sw, 2, 1)

        ecu_frame.setLayout(ecu_layout)
        layout.addWidget(ecu_frame)

        layout.addStretch()
        page.setLayout(layout)
        return page

    def _create_settings_step(self):
        page = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(12)

        settings_label = QLabel("🔑 Key Programming Settings")
        settings_label.setStyleSheet("font-weight: bold; color: #e6a94a;")
        layout.addWidget(settings_label)

        # Channel
        channel_layout = QHBoxLayout()
        channel_layout.addWidget(QLabel("📍 Channel Number:"))
        self.channel_input = QSpinBox()
        self.channel_input.setRange(1, 255)
        self.channel_input.setValue(21)
        channel_layout.addWidget(self.channel_input)
        channel_layout.addStretch()
        layout.addLayout(channel_layout)

        # Key count
        keycount_layout = QHBoxLayout()
        keycount_layout.addWidget(QLabel("📦 Keys to Program:"))
        self.keycount_input = QSpinBox()
        self.keycount_input.setRange(1, 10)
        self.keycount_input.setValue(1)
        keycount_layout.addWidget(self.keycount_input)
        keycount_layout.addStretch()
        layout.addLayout(keycount_layout)

        # Login/SKC (Optional)
        login_layout = QHBoxLayout()
        login_layout.addWidget(QLabel("🔐 SKC (Secret Key Code):"))
        self.login_input = QSpinBox()
        self.login_input.setRange(0, 65535)
        self.login_input.setValue(0x1111)
        self.login_input.setDisplayIntegerBase(16)
        self.login_input.setPrefix("0x")
        login_layout.addWidget(self.login_input)
        login_layout.addStretch()
        layout.addLayout(login_layout)

        self.login_note = QLabel("(Optional: Only required for some ECUs)")
        self.login_note.setStyleSheet("color: #9a9aa5; font-size: 11px;")
        layout.addWidget(self.login_note)

        layout.addStretch()
        page.setLayout(layout)
        return page

    def _create_instructions_step(self):
        page = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(12)

        instr_label = QLabel("⚠️ Critical Instructions")
        instr_label.setStyleSheet("font-weight: bold; color: #ff9500;")
        layout.addWidget(instr_label)

        # Warning box
        warning_text = QLabel("""
🚨 SAFETY REMINDERS:

1. ✅ REMOVE ALL EXISTING KEYS FIRST
   • Take out every key in your possession
   • Lock car doors
   • No keys in ignition

2. ✅ PREPARE NEW KEYS
   • Have the key(s) you want to program ready
   • Don't turn ignition on yet
   • Keep them accessible

3. ✅ WHAT HAPPENS NEXT:
   • You click "Start Programming"  
   • System detects connection
   • You insert new key #1
   • System programs key #1
   • You remove key #1, insert key #2
   • System repeats for remaining keys

4. ✅ WHAT NOT TO DO:
   • Do NOT start the engine during programming
   • Do NOT turn ignition off between keys
   • Do NOT remove keys until programming completes
        """)
        warning_text.setWordWrap(True)
        warning_text.setStyleSheet("""
            color: #ffaa00;
            background-color: #2a1a00;
            border: 1px solid #664200;
            border-radius: 4px;
            padding: 12px;
            font-size: 12px;
        """)
        layout.addWidget(warning_text)

        # Next step indicator
        next_label = QLabel("🔄 Next: System will detect when you insert new key...")
        next_label.setStyleSheet("color: #00cc66; font-style: italic;")
        layout.addWidget(next_label)

        layout.addStretch()
        page.setLayout(layout)
        return page

    def _create_progress_step(self):
        page = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(12)

        progress_label = QLabel("🔄 Programming Progress")
        progress_label.setStyleSheet("font-weight: bold; color: #e6a94a;")
        layout.addWidget(progress_label)

        # Progress indicator (igual ao QProgressBar)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # Status label
        self.status_label = QLabel("Waiting for key insertion...")
        self.status_label.setStyleSheet("color: #9a9aa5; font-size: 12px;")
        layout.addWidget(self.status_label)

        # Mock progress for demo
        self.progress_timer = None
        self.current_progress = 0

        layout.addStretch()
        page.setLayout(layout)
        return page

    def _update_step(self):
        """Atualiza cada passo da interface."""
        total_steps = self.steps_widget.count()

        self.back_btn.setEnabled(self.current_step > 0)
        self.next_btn.setText("Next →" if self.current_step < total_steps - 1 else "Finish")

        self.steps_widget.setCurrentIndex(self.current_step)

    def _next_step(self):
        """Handle next step navigation."""
        if self.current_step < self.steps_widget.count() - 1:
            self.current_step += 1
        else:
            # Finish wizard
            self.accept()
            return

        self._update_step()

        # Step-specific initialization
        if self.current_step == 1:
            self._initialize_connection_step()
        elif self.current_step == 2:
            self._initialize_settings_step()
        elif self.current_step == 3:
            self._initialize_instructions_step()
        elif self.current_step == 4:
            self._start_progress_simulation()

    def _prev_step(self):
        """Handle previous step navigation."""
        if self.current_step > 0:
            self.current_step -= 1
            self._update_step()

    def _initialize_connection_step(self):
        """Initialize connection step with mock data."""
        # Mock ECU data (igual ao worker emit)
        if self.ecu_id:
            self.ecu_part.setText(self.ecu_id.part_number or "—")
            self.ecu_comp.setText(self.ecu_id.component or "—")
            self.ecu_sw.setText(self.ecu_id.software_version or "—")

        # Simulate connection
        self.conn_label.setText("Connected")
        self.conn_label.setStyleSheet("color: #00cc66; font-weight: bold;")
        self.conn_dot.setStyleSheet("color: #00cc66; font-size: 16px;")
        self.conn_detail.setText("(KW1281 Active)")

    def _initialize_settings_step(self):
        """Initialize settings step with saved values."""
        # Load last used values
        # Would connect to worker here
        pass

    def _initialize_instructions_step(self):
        """Initialize instructions step."""
        # Would validate settings here
        pass

    def _start_progress_simulation(self):
        """Start mock progress simulation."""
        import random

        self.current_progress = 0
        self.progress_timer = None
        self.progress_timer = self._create_progress_timer()

    def _create_progress_timer(self):
        """Create a simple progress timer for demo."""
        timer = QPropertyAnimation(self, b"value")
        timer.setDuration(3000)
        timer.setStartValue(0)
        timer.setEndValue(100)
        timer.setEasingCurve(QEasingCurve.Type.OutCubic)
        timer.valueChanged.connect(self._update_progress)
        timer.start()
        return timer

    def _update_progress(self, value):
        """Atualiza o indicador de progresso."""
        self.progress_bar.setValue(int(value))
        if value >= 100:
            self.status_label.setText("✅ Programming completed successfully!")
            self.status_label.setStyleSheet("color: #00cc66; font-weight: bold;")

            # Update button texts
            self.next_btn.setText("Finish")
        else:
            self.status_label.setText(f"Programming... {int(value)}%")

    def closeEvent(self, event):
        """Cancel any running timers on close."""
        if self.progress_timer:
            self.progress_timer.stop()
        super().closeEvent(event)

    def set_ecu_data(self, ecu_id):
        """Update ECU data from worker signal."""
        self.ecu_id = ecu_id
        self._initialize_connection_step()

    def get_programming_settings(self):
        """Get the programming settings from UI."""
        return {
            'channel': self.channel_input.value(),
            'key_count': self.keycount_input.value(),
            'login': self.login_input.value(),
        }