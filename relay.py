"""SECS ↔ JSON 변환 및 중계 로직."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import typing
import uuid

import secsgem.secs.variables as sv

if typing.TYPE_CHECKING:
    import secsgem.common
    from PySide6.QtCore import Signal
    from mes_handler import MesHandler
    from mfc_server import MfcServer


# ---------------------------------------------------------------------------
# SECS 변수 → JSON dict 변환
# ---------------------------------------------------------------------------

def _var_to_json(var) -> object:
    """secsgem 변수를 JSON 직렬화 가능한 값으로 변환한다."""
    if isinstance(var, sv.List):
        result = {}
        for name in var._data_format:  # type: ignore[attr-defined]
            child = getattr(var, name, None)
            if child is not None:
                result[name] = _var_to_json(child)
        return result
    if isinstance(var, sv.Array):
        return [_var_to_json(item) for item in var]
    if isinstance(var, (sv.Boolean,)):
        return {"type": var.text_code, "value": bool(var.get())}
    if isinstance(var, (sv.String, sv.JIS8)):
        return {"type": var.text_code, "value": str(var.get())}
    if isinstance(var, sv.Binary):
        raw = var.get()
        if isinstance(raw, (bytes, bytearray)):
            return {"type": "Binary", "value": base64.b64encode(raw).decode()}
        return {"type": "Binary", "value": str(raw)}
    # 숫자형 (I1/I2/I4/I8, U1/U2/U4/U8, F4/F8)
    try:
        return {"type": var.text_code, "value": var.get()}
    except Exception:
        return {"type": "Raw", "value": repr(var)}


def _parse_secs_bytes(data: bytes, pos: int = 0) -> tuple[object, int]:
    """S/F 정의 없이 SECS 바이너리를 재귀적으로 파싱한다.

    Returns:
        (파싱된 값, 다음 파싱 위치)
    """
    if pos >= len(data):
        return None, pos

    fmt_byte = data[pos]
    num_len_bytes = fmt_byte & 0x03
    fmt_code = (fmt_byte >> 2) & 0x3F
    pos += 1

    if pos + num_len_bytes > len(data):
        return None, len(data)

    length = 0
    for i in range(num_len_bytes):
        length = (length << 8) | data[pos + i]
    pos += num_len_bytes

    end = pos + length

    # List (fmt_code == 0)
    if fmt_code == 0:
        items = []
        for _ in range(length):
            item, pos = _parse_secs_bytes(data, pos)
            items.append(item)
        return items, pos

    chunk = data[pos:end]
    pos = end

    _FMT = {
        8:  ("Binary",  1, False),
        9:  ("Boolean", 1, False),
        16: ("A",       1, True),
        17: ("JIS8",    1, True),
        24: ("I8",      8, False),
        25: ("I1",      1, False),
        26: ("I2",      2, False),
        28: ("I4",      4, False),
        32: ("F8",      8, False),
        36: ("F4",      4, False),
        40: ("U8",      8, False),
        41: ("U1",      1, False),
        42: ("U2",      2, False),
        44: ("U4",      4, False),
    }

    if fmt_code not in _FMT:
        return {"type": "Raw", "value": base64.b64encode(chunk).decode()}, pos

    type_name, item_size, is_str = _FMT[fmt_code]

    if fmt_code == 8:  # Binary
        return {"type": "Binary", "value": base64.b64encode(chunk).decode()}, pos
    if is_str:         # ASCII / JIS8
        return {"type": type_name, "value": chunk.decode("ascii", errors="replace")}, pos

    import struct
    _STRUCT = {1: "b", 2: "h", 4: "i", 8: "q"}
    _USTRUCT = {1: "B", 2: "H", 4: "I", 8: "Q"}
    _FSTRUCT = {4: "f", 8: "d"}
    is_signed = type_name.startswith("I")
    is_float  = type_name.startswith("F")
    is_bool   = type_name == "Boolean"

    if item_size == 0 or len(chunk) == 0:
        return {"type": type_name, "value": []}, pos

    count = len(chunk) // item_size
    if is_bool:
        values = [bool(b) for b in chunk[:count]]
    elif is_float:
        values = list(struct.unpack(f">{count}{_FSTRUCT[item_size]}", chunk[:count * item_size]))
    elif is_signed:
        values = list(struct.unpack(f">{count}{_STRUCT[item_size]}", chunk[:count * item_size]))
    else:
        values = list(struct.unpack(f">{count}{_USTRUCT[item_size]}", chunk[:count * item_size]))

    value = values[0] if count == 1 else values
    return {"type": type_name, "value": value}, pos


def secs_to_dict(message: secsgem.common.Message, handler: MesHandler) -> object:
    """SECS 메시지의 데이터 부분을 JSON 직렬화 가능한 객체로 변환한다."""
    try:
        decoded = handler.settings.streams_functions.decode(message)
        if decoded is None or decoded.data is None:
            return {}
        raw_value = decoded.get()
        if raw_value is None:
            return {}
        if isinstance(raw_value, dict):
            return {k: _wrap_if_bare(v) for k, v in raw_value.items()}
        if isinstance(raw_value, list):
            return [_wrap_if_bare(v) for v in raw_value]
        return _wrap_if_bare(raw_value)
    except Exception:
        # secsgem 정의 불일치 → 제네릭 바이너리 파서로 재시도
        if message.data:
            result, _ = _parse_secs_bytes(message.data)
            return result if result is not None else {}
        return {}


def _wrap_if_bare(value) -> object:
    """secsgem get()이 반환한 bare Python 값을 JSON 형태로 래핑한다."""
    # secsgem Base 변수 인스턴스는 _var_to_json으로 처리
    if hasattr(value, "text_code") and hasattr(value, "get"):
        return _var_to_json(value)
    if isinstance(value, dict):
        return {k: _wrap_if_bare(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_wrap_if_bare(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# JSON dict → SECS 함수 인스턴스 변환
# ---------------------------------------------------------------------------

def dict_to_secs(msg: dict, handler: MesHandler):
    """JSON 메시지를 secsgem SecsStreamFunction 인스턴스로 변환한다.

    알 수 없는 S/F이면 KeyError를 발생시킨다.
    """
    s, f = msg["stream"], msg["function"]
    data = msg.get("data", {})
    sf_class = handler.stream_function(s, f)
    try:
        return sf_class(_json_to_value(data))
    except Exception:
        # secsgem 정의와 맞지 않으면 type-tagged JSON → raw SECS bytes 로 직접 인코딩
        return _build_raw_secs_func(sf_class, s, f, _json_to_secs_bytes(data))


def _json_to_value(value) -> object:
    """JSON 값을 secsgem이 받아들이는 파이썬 값으로 복원한다."""
    if isinstance(value, dict):
        # type 태그가 있는 단일 값
        if "type" in value and "value" in value:
            t = value["type"]
            v = value["value"]
            if t == "Binary":
                return base64.b64decode(v)
            if t in ("Boolean",):
                return bool(v)
            return v
        # 구조체 → dict 재귀
        return {k: _json_to_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_to_value(v) for v in value]
    return value


def _json_to_secs_bytes(value) -> bytes:
    """JSON type-tagged 값을 raw SECS-II bytes로 인코딩한다 (heterogeneous list 지원)."""
    if isinstance(value, list):
        parts = b"".join(_json_to_secs_bytes(v) for v in value)
        n = len(value)
        if n < 256:
            hdr = bytes([0x01, n])
        elif n < 65536:
            hdr = bytes([0x02, n >> 8, n & 0xFF])
        else:
            hdr = bytes([0x03, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])
        return hdr + parts

    if isinstance(value, dict) and "type" in value and "value" in value:
        t, v = value["type"], value["value"]
        _MAP = {
            "I1": sv.I1, "I2": sv.I2, "I4": sv.I4, "I8": sv.I8,
            "U1": sv.U1, "U2": sv.U2, "U4": sv.U4, "U8": sv.U8,
            "F4": sv.F4, "F8": sv.F8,
            "A": sv.String, "String": sv.String,
            "JIS8": sv.JIS8, "Boolean": sv.Boolean,
        }
        if t == "Binary":
            raw = base64.b64decode(v) if isinstance(v, str) else bytes(v)
            return sv.Binary(raw).encode()
        if t in _MAP:
            return _MAP[t](v).encode()
        return sv.String(str(v)).encode()

    if isinstance(value, bool):
        return sv.Boolean(value).encode()
    if isinstance(value, int):
        for typ in (sv.U1, sv.U2, sv.U4, sv.U8, sv.I1, sv.I2, sv.I4, sv.I8):
            if typ().supports_value(value):
                return typ(value).encode()
        return sv.String(str(value)).encode()
    if isinstance(value, float):
        return sv.F8(value).encode()
    if isinstance(value, str):
        return sv.String(value).encode()
    return b""


def _build_raw_secs_func(sf_class, stream: int, function: int, raw: bytes):
    """secsgem 정의와 맞지 않는 data를 raw bytes로 포장한 SecsStreamFunction을 반환한다."""
    import secsgem.secs.functions

    cls = type(
        f"_RawS{stream:02d}F{function:02d}",
        (secsgem.secs.functions.SecsStreamFunction,),
        {
            "_stream": stream,
            "_function": function,
            "_data_format": None,
            "_has_reply": getattr(sf_class, "_has_reply", True),
            "_is_reply_required": getattr(sf_class, "_is_reply_required", True),
            "_is_multi_block": True,
        },
    )

    class _RawData:
        def encode(self_inner) -> bytes:
            return raw

    obj = object.__new__(cls)
    for attr, val in [
        ("data", _RawData()),
        ("data_format", None),
        ("to_host", True),
        ("to_equipment", True),
        ("has_reply", getattr(sf_class, "_has_reply", True)),
        ("is_reply_required", getattr(sf_class, "_is_reply_required", True)),
        ("is_multi_block", True),
        ("_object_intitialized", True),
    ]:
        object.__setattr__(obj, attr, val)
    return obj


# ---------------------------------------------------------------------------
# SECS 텍스트 포맷터 (XComPro 스타일)
# ---------------------------------------------------------------------------

_SECS_TYPE_DISPLAY = {
    "I1": "INT1",    "I2": "INT2",    "I4": "INT4",    "I8": "INT8",
    "U1": "UINT1",   "U2": "UINT2",   "U4": "UINT4",   "U8": "UINT8",
    "F4": "FLOAT4",  "F8": "FLOAT8",
    "A": "ASCII",    "String": "ASCII",
    "JIS8": "JIS8",  "Binary": "BINARY",  "Boolean": "BOOL",
}


def _fmt_secs_data(value, indent: int = 0) -> str:
    """type-tagged JSON 값을 XComPro 스타일 SECS 텍스트로 포맷."""
    pad = "  " * indent

    if isinstance(value, list):
        inner = "\n".join(_fmt_secs_data(v, indent + 1) for v in value)
        header = f"{pad}LIST {len(value)}"
        return f"{header}\n{inner}" if inner else header

    if isinstance(value, dict) and "type" in value and "value" in value:
        t, v = value["type"], value["value"]
        type_name = _SECS_TYPE_DISPLAY.get(t, t)

        if t == "Binary":
            raw = base64.b64decode(v) if isinstance(v, str) else bytes(v)
            val_str = ", ".join(f"0x{b:02X}" for b in raw)
            count = len(raw)
        elif t == "Boolean":
            val_str = "true" if v else "false"
            count = 1
        elif t in ("A", "String", "JIS8"):
            val_str = str(v)
            count = len(str(v))
        elif isinstance(v, list):
            val_str = ", ".join(str(x) for x in v)
            count = len(v)
        else:
            val_str = str(v)
            count = 1

        return f"{pad}{type_name} {count} , [{val_str}]"

    return f"{pad}{json.dumps(value, ensure_ascii=False)}"


def _fmt_secs_msg(msg: dict) -> str:
    """JSON 메시지를 'S{s}F{f}\\n{데이터}' 포맷으로 변환."""
    s, f = msg.get("stream"), msg.get("function")
    if s is None or f is None:
        return json.dumps(msg, ensure_ascii=False)
    header = f"S{s}F{f}"
    data = msg.get("data")
    if not data and data != 0:
        return header
    data_str = _fmt_secs_data(data)
    return f"{header}\n{data_str}" if data_str else header


# ---------------------------------------------------------------------------
# Relay
# ---------------------------------------------------------------------------

class Relay:
    """SECS ↔ JSON 변환 및 MES/MFC 간 메시지 중계."""

    def __init__(self, log_fn: typing.Callable[[str], None]):
        self._log = log_fn
        self.mes: MesHandler | None = None
        self.mfc: MfcServer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # 생명주기
    # ------------------------------------------------------------------

    def start_loop(self):
        """asyncio 이벤트 루프를 별도 스레드에서 시작한다."""
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="relay-asyncio"
        )
        self._loop_thread.start()

    def stop_loop(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop = None

    def _run_async(self, coro) -> asyncio.Future:
        """asyncio 루프에 코루틴을 스케줄한다 (스레드 안전)."""
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ------------------------------------------------------------------
    # 연결 이벤트
    # ------------------------------------------------------------------

    def on_mes_connected(self):
        self._log("[INFO] MES 연결됨")

    def on_mes_disconnected(self):
        self._log("[INFO] MES 연결 해제")
        if self.mfc and self._loop:
            self._run_async(
                self.mfc.send({"id": str(uuid.uuid4()), "event": "MES_DISCONNECTED"})
            )

    def on_mfc_connected(self):
        self._log("[INFO] MFC 연결됨 → HSMS 활성화")
        if self.mes is not None:
            threading.Thread(target=self.mes.enable, daemon=True).start()

    def on_mfc_disconnected(self):
        self._log("[INFO] MFC 연결 해제 → HSMS 비활성화")
        if self.mes is not None:
            threading.Thread(target=self.mes.disable, daemon=True).start()
        self._s1f13_notified = False  # 재연결 시 S1F13 재전달 허용

    # ------------------------------------------------------------------
    # MES 통신 수립 완료 → MFC에 S1F13 전달 (MFC 세션당 1회)
    # ------------------------------------------------------------------

    def forward_s1f13_to_mfc(self):
        """MES↔릴레이 간 S1F13/S1F14 완료 후 MFC에도 S1F13을 전달한다."""
        if self._s1f13_notified:
            return
        self._s1f13_notified = True
        if self.mfc is None or self._loop is None:
            self._log("[RELAY] MFC 미연결 — S1F13 전달 생략")
            return
        msg = {
            "id": str(uuid.uuid4()),
            "stream": 1,
            "function": 13,
            "reply": True,
            "data": {},
        }
        self._log(f"[TX→MFC] {_fmt_secs_msg(msg)}")
        self._run_async(self.mfc.send(msg))

    # ------------------------------------------------------------------
    # MES → MFC 방향
    # ------------------------------------------------------------------

    def on_secs_received(self, message: secsgem.common.Message):
        """MES로부터 SECS 수신 → JSON 변환 후 MFC에 전달."""
        s = message.header.stream
        f = message.header.function
        self._log(f"[RX←MES] S{s}F{f}")

        if self.mfc is None or self._loop is None:
            self._log(f"[RELAY] MFC 미연결, S{s}F{f} 버림")
            return

        need_reply = bool(message.header.require_response)
        data = secs_to_dict(message, self.mes)  # type: ignore[arg-type]

        msg: dict = {
            "id": str(uuid.uuid4()),
            "stream": s,
            "function": f,
            "reply": need_reply,
            "data": data,
        }

        self._log(f"[TX→MFC] {_fmt_secs_msg(msg)}")
        self._run_async(self._forward_to_mfc(msg, message))

    async def _forward_to_mfc(self, msg: dict, original: secsgem.common.Message):
        from mfc_server import _NOT_CONNECTED
        assert self.mfc is not None
        if msg.get("reply"):
            reply_json = await self.mfc.send(msg, wait_reply=True)
            if reply_json is _NOT_CONNECTED:
                self._log("[RELAY] MFC 미연결 — reply 없음")
                return
            if reply_json is None:
                self._log("[RELAY] MFC reply timeout")
                return
            rs = reply_json.get("stream", original.header.stream)
            rf = reply_json.get("function", original.header.function + 1)
            self._log(f"[TX→MES] S{rs}F{rf}")
            try:
                func = dict_to_secs(reply_json, self.mes)  # type: ignore[arg-type]
                self.mes.send_response(func, original.header.system)  # type: ignore[union-attr]
            except Exception as exc:
                self._log(f"[RELAY] MES reply 전송 실패: {exc}")
        else:
            await self.mfc.send(msg)

    # ------------------------------------------------------------------
    # MFC → MES 방향
    # ------------------------------------------------------------------

    def on_json_received(self, msg: dict):
        """MFC로부터 JSON 수신 → SECS 변환 후 MES에 전달."""
        self._log(f"[RX←MFC] {_fmt_secs_msg(msg)}")
        threading.Thread(
            target=self._forward_to_mes, args=(msg,), daemon=True
        ).start()

    def _forward_to_mes(self, msg: dict):
        assert self.mes is not None
        # S1F14는 MES와의 통신 수립 후 MFC가 보내는 ack — MES에 재전송 불필요
        if msg.get("stream") == 1 and msg.get("function") == 14:
            self._log("[RELAY] S1F14 (MFC comm ack) — 무시")
            return
        try:
            func = dict_to_secs(msg, self.mes)
        except KeyError as exc:
            self._log(f"[RELAY] 알 수 없는 S/F: {exc}")
            return
        except Exception as exc:
            self._log(f"[RELAY] SECS 변환 실패: {exc}")
            return

        if msg.get("reply"):
            reply_secs = self.mes.send_and_waitfor_response(func)
            if reply_secs is None:
                self._log("[RELAY] MES T3 timeout")
                if self.mfc and self._loop:
                    error_msg = {"id": msg.get("id", ""), "error": "T3_TIMEOUT"}
                    self._run_async(self.mfc.send(error_msg))
                return
            rs = reply_secs.header.stream
            rf = reply_secs.header.function
            self._log(f"[TX→MFC] S{rs}F{rf}")
            reply_data = secs_to_dict(reply_secs, self.mes)
            reply_json = {
                "id": msg.get("id", str(uuid.uuid4())),
                "stream": rs,
                "function": rf,
                "data": reply_data,
            }
            if self.mfc and self._loop:
                self._run_async(self.mfc.send(reply_json))
        else:
            self.mes.send_stream_function(func)
            self._log(f"[TX→MES] S{msg['stream']}F{msg['function']}")
