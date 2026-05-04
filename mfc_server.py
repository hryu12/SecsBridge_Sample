"""MFC 프로그램과 JSON/TCP로 통신하는 서버."""

from __future__ import annotations

import asyncio
import json
import typing
import uuid

if typing.TYPE_CHECKING:
    from relay import Relay


_NOT_CONNECTED = object()  # send() 반환값: MFC 미연결 구분용 sentinel


class MfcServer:
    """MFC 클라이언트가 연결해 오기를 기다리는 JSON-over-TCP 서버.

    프로토콜: 개행('\n') 구분 JSON 메시지.
    - MFC → PySide: 새 요청 (reply 대기 포함)
    - PySide → MFC: 이벤트 전달 또는 reply 회신
    """

    def __init__(self, relay: Relay, host: str = "127.0.0.1", port: int = 19001):
        self._relay = relay
        self.host = host
        self.port = port
        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future] = {}

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    @property
    def is_connected(self) -> bool:
        return self._writer is not None

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        self._writer = writer
        self._relay.on_mfc_connected()
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                self._dispatch(msg)
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            self._writer = None
            # 대기 중인 Future를 모두 취소
            for fut in self._pending.values():
                if not fut.done():
                    fut.cancel()
            self._pending.clear()
            self._relay.on_mfc_disconnected()

    def _dispatch(self, msg: dict):
        msg_id = msg.get("id")
        if msg_id and msg_id in self._pending:
            fut = self._pending.pop(msg_id)
            if not fut.done():
                fut.set_result(msg)
        else:
            self._relay.on_json_received(msg)

    async def send(self, msg: dict, *, wait_reply: bool = False, timeout: float = 30.0) -> dict | None:
        """JSON 메시지를 MFC에 전송.

        Args:
            msg: 전송할 메시지 dict
            wait_reply: True이면 같은 id의 응답을 기다린다.
            timeout: reply 대기 최대 시간(초)

        Returns:
            wait_reply=True일 때 응답 dict, 타임아웃/연결 없음이면 None.
        """
        if self._writer is None:
            return _NOT_CONNECTED

        msg.setdefault("id", str(uuid.uuid4()))
        line = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")

        try:
            self._writer.write(line)
            await self._writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            return None

        if not wait_reply:
            return None

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[msg["id"]] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._pending.pop(msg["id"], None)
            return None
