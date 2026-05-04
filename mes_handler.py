"""MES SECS/HSMS 핸들러 — secsgem GemEquipmentHandler 기반."""

from __future__ import annotations

import typing

import secsgem.common
import secsgem.gem
import secsgem.hsms
import secsgem.secs
from secsgem.common.tcp_server_connection import TcpServerConnection
from secsgem.gem.handler import GemHandler
from secsgem.gem.communication_state_machine import CommunicationState as _GemCommState
from secsgem.hsms.connection_state_machine import ConnectionState, ConnectionStateMachine
from secsgem.hsms.message import HsmsMessage
from secsgem.hsms.protocol import HsmsProtocol
from secsgem.hsms.reject_req_header import HsmsRejectReqHeader

if typing.TYPE_CHECKING:
    from relay import Relay


# ---------------------------------------------------------------------------
# Patch 1: Windows 소켓 에러 (10057/10038) — secsgem 서버 스레드
# ---------------------------------------------------------------------------
_orig_server_thread = TcpServerConnection._TcpServerConnection__server_thread  # type: ignore[attr-defined]

_WIN_SOCKET_ERRORS = {
    10057,  # WSAENOTCONN — listening 소켓에 shutdown() 호출 (accept 후 cleanup)
    10038,  # WSAENOTSOCK — disable()이 소켓을 닫은 직후 accept() 호출
}


def _patched_server_thread(self):
    try:
        _orig_server_thread(self)
    except OSError as exc:
        winerr = getattr(exc, "winerror", None) or exc.errno
        if winerr in _WIN_SOCKET_ERRORS:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._stop_server_thread = False
        else:
            raise


TcpServerConnection._TcpServerConnection__server_thread = _patched_server_thread  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Patch 2: SELECT.req 경쟁 조건 — 상태 머신 select 전환
#
# TCP close 직전 버퍼 잔여 SELECT.req가 들어올 때 _perform_transition의
# enter() 콜백이 실패하는 경우를 조용히 무시한다.
# enter() 호출 전에 이미 _current_state = CONNECTED_SELECTED로 바뀌므로
# 예외를 catch해도 상태 머신 일관성은 유지된다.
# ---------------------------------------------------------------------------
_orig_csm_select = ConnectionStateMachine.select


def _patched_csm_select(self: ConnectionStateMachine) -> None:
    if self.current == ConnectionState.CONNECTED_SELECTED:
        return  # 중복 SELECT.req 무시
    try:
        _orig_csm_select(self)
    except Exception:
        pass  # enter() 콜백 실패 — 상태는 이미 CONNECTED_SELECTED로 전환됨


ConnectionStateMachine.select = _patched_csm_select  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Patch 3: 알 수 없는 S/F 메시지 (S5F23 등) — HsmsProtocol 수신 핸들러
#
# 원본: decode() 실패 시 예외가 전파돼 events.fire("message_received")가
#       실행되지 않고 릴레이가 메시지를 받지 못한다.
# 패치: decode 실패를 조용히 처리하고 raw message를 그대로 이벤트로 전달한다.
# ---------------------------------------------------------------------------
_orig_on_conn_msg = HsmsProtocol._on_connection_message_received


def _patched_on_conn_msg(self: HsmsProtocol, source: object, message: HsmsMessage):
    if message.header.s_type.value > 0:
        # HSMS 제어 메시지(SELECT/DESELECT/LINKTEST) — 원본 로직 사용
        _orig_on_conn_msg(self, source, message)
        return

    # 데이터 메시지(S/F): decode는 로깅 전용이므로 실패해도 계속 진행
    try:
        decoded = self._settings.streams_functions.decode(message)
        self._communication_logger.info(
            "< %s\n%s", message, decoded, extra=self._get_log_extra()
        )
    except (ValueError, KeyError):
        self._communication_logger.info(
            "< S%dF%d (no decoder)",
            message.header.stream,
            message.header.function,
            extra=self._get_log_extra(),
        )

    if self._connection_state.current != ConnectionState.CONNECTED_SELECTED:
        self._logger.warning("received message when not selected")
        reject = HsmsMessage(
            HsmsRejectReqHeader(message.header.system, message.header.s_type, 4), b""
        )
        self._communication_logger.info(
            "> %s\n  %s", reject, reject.header.s_type.text, extra=self._get_log_extra()
        )
        self.send_message(reject)
        return

    if message.header.system in self._response_queues:
        self._response_queues[message.header.system].put_nowait(message)
    else:
        self.events.fire("message_received", {"connection": self, "message": message})


HsmsProtocol._on_connection_message_received = _patched_on_conn_msg  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Patch 4: GemHandler._on_communicating — CommunicationSM.select() 실패 방지
#
# 재연결 시 CommunicationSM이 NOT_COMMUNICATING이 아닌 상태(WAIT_CRA 등)에
# 있으면 select()가 예외를 던진다. secsgem Event.__call__은 try-except가 없어
# 예외 발생 시 이후 콜백(_on_communicating_relay)이 호출되지 않는다.
# 예외를 무시해도 select()는 이미 상태를 전환했으므로 동작에 영향 없다.
# ---------------------------------------------------------------------------
_orig_on_communicating = GemHandler._on_communicating


def _patched_on_communicating(self: GemHandler, data) -> None:
    try:
        _orig_on_communicating(self, data)
    except Exception:
        pass  # CommunicationSM 상태 불일치 — 이벤트 체인 중단 방지


GemHandler._on_communicating = _patched_on_communicating  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# MES 핸들러
# ---------------------------------------------------------------------------

class MesHandler(secsgem.gem.GemEquipmentHandler):
    """MES로부터 오는 SECS 메시지를 Relay로 전달하는 장비 핸들러."""

    def __init__(self, settings: secsgem.common.Settings, relay: Relay):
        super().__init__(settings)
        self._relay = relay
        self.protocol.events.disconnected += self._on_disconnected_relay

    def _on_communicating(self, data):
        # "MES 연결됨" → S1F13 전송 순서 보장
        self._relay.on_mes_connected()
        try:
            super()._on_communicating(data)
        except Exception:
            pass
        # MFC가 이미 연결된 경우에만 전달 (MFC Start 버튼 이후)
        if self._relay.mfc and self._relay.mfc.is_connected:
            self._relay.forward_s1f13_to_mfc()

    def _on_disconnected_relay(self, _):
        # CommunicationSM을 NOT_COMMUNICATING으로 완전히 리셋해야
        # 재연결 시 select() 전환이 정상 동작한다.
        # disable()은 어느 상태에서도 DISABLED로, enable()은 NOT_COMMUNICATING으로.
        sm = self._communication_state
        try:
            if sm.current != _GemCommState.NOT_COMMUNICATING:
                sm.disable()
                sm.enable()
        except Exception:
            pass
        self._relay.on_mes_disconnected()

    def _on_state_wait_cra(self, data):
        self._relay._log("[TX→MES] S1F13 (Establish Communication)")
        super()._on_state_wait_cra(data)

    def _on_message_received(self, data: dict):
        """CommunicationSM 상태에 무관하게 모든 S/F를 처리한다.

        S1F13/S1F14(GEM 핸드쉐이크): 부모 클래스에 위임 — SM 전환 및 S1F14 응답 유지.
        그 외 모든 메시지: SM 상태를 무시하고 바로 relay로 전달.
        """
        message = data["message"]
        s, f = message.header.stream, message.header.function
        if s == 1 and f in (13, 14):
            # GEM 통신 수립 핸드쉐이크 — relay에는 전달하지 않음
            try:
                super()._on_message_received(data)
            except Exception:
                pass
            return
        self._handle_stream_function(message)

    def _handle_stream_function(self, message: secsgem.common.Message):
        """모든 수신 S/F를 relay로 전달. 알 수 없는 S/F는 S9F5 대신 relay로 넘긴다."""
        self._relay.on_secs_received(message)


def make_mes_handler(relay: Relay, address: str = "0.0.0.0", port: int = 2001) -> MesHandler:
    settings = secsgem.hsms.HsmsSettings(
        address=address,
        port=port,
        connect_mode=secsgem.hsms.HsmsConnectMode.PASSIVE,
        device_type=secsgem.common.DeviceType.EQUIPMENT,
        session_id=1,
        t3=45.0,
    )
    return MesHandler(settings, relay)
