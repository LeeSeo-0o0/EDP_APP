"""
WB Tool – RS232 HHT Terminal v7.1
------------------------------------
 - 7E~7E 프레임 기반 LCD 시뮬레이터
 - C0, 94, D4 제어코드 기반 라인 분리
 - ESC / UP / DN / ENT 버튼 지원
 - 메뉴 커서(▶) 깜빡임 애니메이션
 - 커서 위치 및 줄 정렬 버그 수정
"""

from __future__ import annotations
import sys, time, binascii
from typing import List, Optional
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import Qt, QTimer, pyqtSignal as Signal, pyqtSlot as Slot
import serial, serial.tools.list_ports


LCD_CHAR_MAP = {
    0xA3: "↑",
    0xA4: "↓",
    0xD4: "●",
    0xE4: "○",
}
PRINTABLE_MIN = 32
PRINTABLE_MAX = 126


class TildeFramer:
    """7E~7E 프레임 검출기"""
    def __init__(self):
        self._in = bytearray()
        self._armed = False

    def feed(self, chunk: bytes) -> List[bytes]:
        out: List[bytes] = []
        for b in chunk:
            if b == 0x7E:
                if self._armed and self._in:
                    out.append(bytes(self._in))
                    self._in.clear()
                    self._armed = False
                else:
                    self._in.clear()
                    self._armed = True
            elif self._armed:
                self._in.append(b)
        return out


class SerialReader(QtCore.QThread):
    data_raw = Signal(bytes)
    data_framed = Signal(bytes)

    def __init__(self, ser: serial.Serial):
        super().__init__()
        self.ser = ser
        self._running = True
        self._framer = TildeFramer()

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            n = self.ser.in_waiting
            if n:
                data = self.ser.read(n)
                self.data_raw.emit(data)
                for frame in self._framer.feed(data):
                    self.data_framed.emit(frame)
            self.msleep(5)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WB Tool – RS232 HHT Terminal v7.1")
        self.resize(1280, 800)
        self.ser: Optional[serial.Serial] = None
        self.reader: Optional[SerialReader] = None
        self.cursor_index = 0
        self.current_lines: List[str] = []

        # 커서 깜빡임용 타이머
        self.cursor_visible = True
        self.cursor_timer = QTimer()
        self.cursor_timer.timeout.connect(self.toggle_cursor)
        self.cursor_timer.start(500)

        # UI 구성
        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)
        root = QtWidgets.QVBoxLayout(cw)

        # ─── 상단 포트 설정
        bar = QtWidgets.QHBoxLayout()
        root.addLayout(bar)

        self.port_combo = QtWidgets.QComboBox()
        for p in serial.tools.list_ports.comports():
            self.port_combo.addItem(p.device)
        bar.addWidget(QtWidgets.QLabel("Port:"))
        bar.addWidget(self.port_combo, 1)

        self.baud_edit = QtWidgets.QLineEdit("115200")
        self.baud_edit.setFixedWidth(100)
        bar.addWidget(QtWidgets.QLabel("Baud:"))
        bar.addWidget(self.baud_edit)

        self.parity_combo = QtWidgets.QComboBox()
        self.parity_combo.addItems(["N", "E", "O", "M", "S"])
        bar.addWidget(QtWidgets.QLabel("Parity:"))
        bar.addWidget(self.parity_combo)

        self.stop_combo = QtWidgets.QComboBox()
        self.stop_combo.addItems(["1", "1.5", "2"])
        bar.addWidget(QtWidgets.QLabel("Stop:"))
        bar.addWidget(self.stop_combo)

        self.data_combo = QtWidgets.QComboBox()
        self.data_combo.addItems(["8", "7", "6", "5"])
        bar.addWidget(QtWidgets.QLabel("Data:"))
        bar.addWidget(self.data_combo)

        self.btn_conn = QtWidgets.QPushButton("Connect")
        self.btn_disc = QtWidgets.QPushButton("Disconnect")
        self.btn_disc.setEnabled(False)
        bar.addWidget(self.btn_conn)
        bar.addWidget(self.btn_disc)
        self.btn_conn.clicked.connect(self.on_connect)
        self.btn_disc.clicked.connect(self.on_disconnect)

        # ─── 중앙 (로그 + LCD)
        split = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        self.chk_ts = QtWidgets.QCheckBox("Timestamp")
        self.chk_ts.setChecked(True)
        self.chk_hex = QtWidgets.QCheckBox("HEX view")
        self.chk_hex.setChecked(True)
        hl = QtWidgets.QHBoxLayout()
        hl.addWidget(self.chk_ts)
        hl.addWidget(self.chk_hex)
        hl.addStretch(1)
        ll.addLayout(hl)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        ll.addWidget(self.log, 1)
        split.addWidget(left)

        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.addWidget(QtWidgets.QLabel("LCD Terminal (Real HHT Style)"))
        self.lcd_view = QtWidgets.QPlainTextEdit()
        self.lcd_view.setReadOnly(True)
        self.lcd_view.setStyleSheet("""
            QPlainTextEdit {
                background-color: #001A99;
                color: white;
                font-family: Consolas;
                font-size: 18px;
                border: 3px solid #111;
            }
        """)
        rl.addWidget(self.lcd_view, 1)
        split.addWidget(right)
        split.setStretchFactor(1, 3)

        # ─── 하단 버튼
        btnrow = QtWidgets.QHBoxLayout()
        root.addLayout(btnrow)
        self.btn_esc = QtWidgets.QPushButton("ESC")
        self.btn_up = QtWidgets.QPushButton("UP")
        self.btn_dn = QtWidgets.QPushButton("DN")
        self.btn_ent = QtWidgets.QPushButton("ENT")
        for b in (self.btn_esc, self.btn_up, self.btn_dn, self.btn_ent):
            b.setFixedHeight(50)
            b.setStyleSheet("font-size:16px;font-weight:bold;")
            btnrow.addWidget(b, 1)
        self.btn_esc.clicked.connect(lambda: self._send(bytes([0x04])))
        self.btn_up.clicked.connect(self.move_up)
        self.btn_dn.clicked.connect(self.move_dn)
        self.btn_ent.clicked.connect(self.enter_menu)

    # ─── 시리얼 연결
    def on_connect(self):
        port = self.port_combo.currentText()
        try:
            baud = int(self.baud_edit.text())
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "Baud", "Invalid baudrate")
            return
        parity_map = {
            "N": serial.PARITY_NONE, "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD, "M": serial.PARITY_MARK, "S": serial.PARITY_SPACE
        }
        stop_map = {"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE, "2": serial.STOPBITS_TWO}
        data_map = {"8": serial.EIGHTBITS, "7": serial.SEVENBITS, "6": serial.SIXBITS, "5": serial.FIVEBITS}

        try:
            self.ser = serial.Serial(
                port=port, baudrate=baud,
                bytesize=data_map[self.data_combo.currentText()],
                parity=parity_map[self.parity_combo.currentText()],
                stopbits=stop_map[self.stop_combo.currentText()],
                timeout=0,
            )
        except serial.SerialException as e:
            QtWidgets.QMessageBox.critical(self, "Open Port", str(e))
            return

        self.reader = SerialReader(self.ser)
        self.reader.data_raw.connect(self.on_raw)
        self.reader.data_framed.connect(self.on_framed)
        self.reader.start()
        self.btn_conn.setEnabled(False)
        self.btn_disc.setEnabled(True)
        self.statusBar().showMessage("Connected")

    def on_disconnect(self):
        if self.reader:
            self.reader.stop()
            self.reader.wait(500)
        if self.ser:
            self.ser.close()
        self.btn_conn.setEnabled(True)
        self.btn_disc.setEnabled(False)
        self.statusBar().showMessage("Disconnected")

    # ─── 수신 로그
    @Slot(bytes)
    def on_raw(self, data: bytes):
        ts = time.strftime("%H:%M:%S ") if self.chk_ts.isChecked() else ""
        if self.chk_hex.isChecked():
            s = " ".join(f"{b:02X}" for b in data)
        else:
            s = data.decode(errors="replace")
        self.log.appendPlainText(f"{ts}{s}")

    @Slot(bytes)
    def on_framed(self, frame: bytes):
        filtered = bytearray(b for b in frame if b not in (0x80,))
        lines = self._split_lcd_lines(filtered)
        self.current_lines = lines
        self._render_lcd()

    # ─── LCD 표시 관련
    def _split_lcd_lines(self, data: bytes) -> list[str]:
        lines, buf = [], bytearray()
        for b in data:
            if b in (0xC0, 0x94, 0xD4):
                if buf:
                    lines.append(self._to_text(buf).strip())
                    buf.clear()
            elif 32 <= b <= 126 or b in LCD_CHAR_MAP:
                buf.append(b)
        if buf:
            lines.append(self._to_text(buf).strip())
        return [ln for ln in lines if ln.strip() != ""]

    def _to_text(self, raw: bytes) -> str:
        out = []
        for b in raw:
            if b in LCD_CHAR_MAP:
                out.append(LCD_CHAR_MAP[b])
            elif PRINTABLE_MIN <= b <= PRINTABLE_MAX:
                out.append(chr(b))
            elif b == 0x20:
                out.append(" ")
        return "".join(out)

    def _render_lcd(self):
        """LCD 화면에 커서 포함해서 출력"""
        if not self.current_lines:
            return

        # 빈줄 제거 및 인덱스 보정
        lines = [ln for ln in self.current_lines if ln.strip() != ""]
        if not lines:
            return

        self.cursor_index %= len(lines)

        rendered = []
        for i, line in enumerate(lines):
            prefix = "▶ " if (i == self.cursor_index and self.cursor_visible) else "  "
            rendered.append(prefix + line)

        self.lcd_view.setPlainText("\n".join(rendered))
        self.lcd_view.verticalScrollBar().setValue(0)

    def toggle_cursor(self):
        self.cursor_visible = not self.cursor_visible
        self._render_lcd()

    # ─── 버튼 동작
    def move_up(self):
        if not self.current_lines:
            return
        self.cursor_index = (self.cursor_index - 1) % len(self.current_lines)
        self._render_lcd()
        self._send(bytes([0x08]))

    def move_dn(self):
        if not self.current_lines:
            return
        self.cursor_index = (self.cursor_index + 1) % len(self.current_lines)
        self._render_lcd()
        self._send(bytes([0x01]))

    def enter_menu(self):
        self._send(bytes([0x02]))
        self.lcd_view.setStyleSheet("""
            QPlainTextEdit {
                background-color: #001A99;
                color: yellow;
                font-family: Consolas;
                font-size: 18px;
                border: 3px solid #111;
            }
        """)
        QtCore.QTimer.singleShot(500, lambda: self.lcd_view.setStyleSheet("""
            QPlainTextEdit {
                background-color: #001A99;
                color: white;
                font-family: Consolas;
                font-size: 18px;
                border: 3px solid #111;
            }
        """))

    def _send(self, data: bytes):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(data)
                self.log.appendPlainText(f"[TX] {' '.join(f'{b:02X}' for b in data)}")
            except Exception as e:
                self.log.appendPlainText(f"[TX ERROR] {e}")


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
