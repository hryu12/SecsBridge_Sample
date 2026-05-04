"""PySide6 메인 윈도우 — SECS/HSMS 중계기 UI."""

from __future__ import annotations

import asyncio
import threading

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from mes_handler import make_mes_handler
from mfc_server import MfcServer
from relay import Relay

_DOT_GREEN = "background-color: #2ecc71; border-radius: 7px;"
_DOT_RED = "background-color: #e74c3c; border-radius: 7px;"
_DOT_GRAY = "background-color: #95a5a6; border-radius: 7px;"
_MAX_LOG_LINES = 2000


class Bridge(QThread):
    """asyncio 이벤트 루프를 QThread 위에서 실행하며 시작/중지를 관리한다."""

    log_signal: Signal = Signal(str)
    mes_status_signal: Signal = Signal(bool)
    mfc_status_signal: Signal = Signal(bool)

    def __init__(self, mes_host: str, mes_port: int, mfc_host: str, mfc_port: int):
        super().__init__()
        self._mes_host = mes_host
        self._mes_port = mes_port
        self._mfc_host = mfc_host
        self._mfc_port = mfc_port

        self._relay = Relay(self._emit_log)
        self._relay.on_mes_connected = self._on_mes_connected          # type: ignore[method-assign]
        self._relay.on_mes_disconnected = self._on_mes_disconnected  # type: ignore[method-assign]
        self._relay.on_mfc_connected = self._on_mfc_connected        # type: ignore[method-assign]
        self._relay.on_mfc_disconnected = self._on_mfc_disconnected  # type: ignore[method-assign]

        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None

    # ------------------------------------------------------------------
    # 이벤트 핸들러 (relay에 monkey-patch)
    # ------------------------------------------------------------------

    def _emit_log(self, text: str):
        self.log_signal.emit(text)

    def _on_mes_connected(self):
        self._emit_log("[INFO] MES 연결됨")
        self.mes_status_signal.emit(True)

    def _on_mes_disconnected(self):
        self._emit_log("[INFO] MES 연결 해제")
        self.mes_status_signal.emit(False)
        if self._relay.mfc and self._loop:
            import uuid
            asyncio.run_coroutine_threadsafe(
                self._relay.mfc.send({"id": str(uuid.uuid4()), "event": "MES_DISCONNECTED"}),
                self._loop,
            )

    def _on_mfc_connected(self):
        self._emit_log("[INFO] MFC 연결됨 → HSMS 활성화")
        self.mfc_status_signal.emit(True)
        if self._relay.mes is not None:
            threading.Thread(target=self._relay.mes.enable, daemon=True).start()

    def _on_mfc_disconnected(self):
        self._emit_log("[INFO] MFC 연결 해제 → HSMS 비활성화")
        self.mfc_status_signal.emit(False)
        if self._relay.mes is not None:
            threading.Thread(target=self._relay.mes.disable, daemon=True).start()

    # ------------------------------------------------------------------
    # QThread run
    # ------------------------------------------------------------------

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._relay._loop = self._loop

        self._stop_event = asyncio.Event()

        # MFC 서버 생성
        mfc = MfcServer(self._relay, host=self._mfc_host, port=self._mfc_port)
        self._relay.mfc = mfc

        # MES 핸들러 생성
        mes = make_mes_handler(self._relay, address=self._mes_host, port=self._mes_port)
        self._relay.mes = mes

        async def _run_all():
            await mfc.start()
            self._emit_log(f"[INFO] MFC JSON 서버 시작: {self._mfc_host}:{self._mfc_port}")
            self._emit_log(f"[INFO] MFC 연결 대기 중 (연결 후 HSMS 시작)")
            self.mes_status_signal.emit(False)
            await self._stop_event.wait()
            try:
                loop = asyncio.get_running_loop()
                await asyncio.wait_for(
                    loop.run_in_executor(None, mes.disable),
                    timeout=2.0,
                )
            except Exception:
                pass
            await mfc.stop()
            # 루프 종료 전 외부 스레드에서 예약된 잔여 태스크 정리
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._emit_log("[INFO] 중계기 중지됨")

        self._loop.run_until_complete(_run_all())
        self._loop.close()
        self._loop = None

    def stop(self):
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)


# ---------------------------------------------------------------------------
# 메인 윈도우
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SECS/HSMS 중계기")
        self.resize(620, 520)
        self._bridge: Bridge | None = None
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(6)

        # --- 상태 표시 ---
        status_row = QHBoxLayout()
        self._mes_dot = QLabel()
        self._mes_dot.setFixedSize(14, 14)
        self._mes_dot.setStyleSheet(_DOT_GRAY)
        self._mfc_dot = QLabel()
        self._mfc_dot.setFixedSize(14, 14)
        self._mfc_dot.setStyleSheet(_DOT_GRAY)
        status_row.addWidget(QLabel("MES"))
        status_row.addWidget(self._mes_dot)
        status_row.addSpacing(16)
        status_row.addWidget(QLabel("MFC"))
        status_row.addWidget(self._mfc_dot)
        status_row.addStretch()
        root.addLayout(status_row)

        # --- 연결 설정 ---
        settings_box = QGroupBox("연결 설정")
        sg = QVBoxLayout(settings_box)

        mes_row = QHBoxLayout()
        mes_row.addWidget(QLabel("MES  IP:"))
        self._mes_ip = QLineEdit("0.0.0.0")
        self._mes_ip.setFixedWidth(120)
        mes_row.addWidget(self._mes_ip)
        mes_row.addWidget(QLabel("Port:"))
        self._mes_port = QSpinBox()
        self._mes_port.setRange(1, 65535)
        self._mes_port.setValue(2001)
        mes_row.addWidget(self._mes_port)
        mes_row.addStretch()
        sg.addLayout(mes_row)

        mfc_row = QHBoxLayout()
        mfc_row.addWidget(QLabel("MFC  IP:"))
        self._mfc_ip = QLineEdit("127.0.0.1")
        self._mfc_ip.setFixedWidth(120)
        mfc_row.addWidget(self._mfc_ip)
        mfc_row.addWidget(QLabel("Port:"))
        self._mfc_port = QSpinBox()
        self._mfc_port.setRange(1, 65535)
        self._mfc_port.setValue(19001)
        mfc_row.addWidget(self._mfc_port)
        mfc_row.addStretch()
        sg.addLayout(mfc_row)

        root.addWidget(settings_box)

        # --- 로그 ---
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._log_view)

        self.setStatusBar(QStatusBar())

    # ------------------------------------------------------------------
    # 슬롯
    # ------------------------------------------------------------------

    def _start_relay(self):
        if self._bridge and self._bridge.isRunning():
            return
        self._mes_ip.setEnabled(False)
        self._mes_port.setEnabled(False)
        self._mfc_ip.setEnabled(False)
        self._mfc_port.setEnabled(False)

        self._bridge = Bridge(
            mes_host=self._mes_ip.text(),
            mes_port=self._mes_port.value(),
            mfc_host=self._mfc_ip.text(),
            mfc_port=self._mfc_port.value(),
        )
        self._bridge.log_signal.connect(self._append_log)
        self._bridge.mes_status_signal.connect(self._update_mes_status)
        self._bridge.mfc_status_signal.connect(self._update_mfc_status)
        self._bridge.finished.connect(self._on_bridge_finished)
        self._bridge.start()

    @Slot(str)
    def _append_log(self, text: str):
        self._log_view.appendPlainText(text)
        # 최대 줄 수 제한
        doc = self._log_view.document()
        while doc.blockCount() > _MAX_LOG_LINES:
            cursor = self._log_view.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.LineUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()
        self._log_view.ensureCursorVisible()

    @Slot(bool)
    def _update_mes_status(self, connected: bool):
        self._mes_dot.setStyleSheet(_DOT_GREEN if connected else _DOT_RED)

    @Slot(bool)
    def _update_mfc_status(self, connected: bool):
        self._mfc_dot.setStyleSheet(_DOT_GREEN if connected else _DOT_RED)

    @Slot()
    def _on_bridge_finished(self):
        self._mes_dot.setStyleSheet(_DOT_GRAY)
        self._mfc_dot.setStyleSheet(_DOT_GRAY)

    def showEvent(self, event):
        super().showEvent(event)
        self._start_relay()

    def closeEvent(self, event):
        if self._bridge and self._bridge.isRunning():
            try:
                self._bridge.stop()
                self._bridge.wait(5000)
            except (KeyboardInterrupt, Exception):
                pass
        super().closeEvent(event)
