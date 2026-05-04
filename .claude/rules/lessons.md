# 교훈 — 알려진 함정과 주의사항

## 1. secsgem S/F 정의와 맞지 않는 data 구조

secsgem의 `SecsS02F33` 등 일부 함수는 `{"DATAID":..., "DATA":[...]}` 같은 특정 구조를 요구한다.  
MFC가 `[[{type-tagged}], [{type-tagged}]]` 같은 raw 이종 리스트를 보내면 `ValueError`가 발생한다.

**해결**: `dict_to_secs`에 폴백 구현됨.  
`sf_class(data)` 실패 시 `_json_to_secs_bytes` → `_build_raw_secs_func`로 raw bytes 인코딩.  
새 S/F를 추가할 때 secsgem 정의와 data 구조가 맞는지 먼저 확인한다.

---

## 2. asyncio 루프 안에서 blocking secsgem 호출

`mes.disable()`, `mes.enable()`, `send_and_waitfor_response()`는 모두 blocking이다.  
`async def` 코루틴 안에서 직접 호출하면 MfcServer의 모든 I/O가 멈춘다.

**해결**:
- `disable()` / `enable()` → `run_in_executor(None, func)` + `asyncio.wait_for(timeout=2.0)`
- `send_and_waitfor_response()` → `threading.Thread(daemon=True)` 에서 호출

---

## 3. closeEvent에서 KeyboardInterrupt

`QThread.wait(ms)` 는 C++ blocking call이다. 대기 중 OS 시그널(SIGINT 등)이 들어오면  
Python이 `KeyboardInterrupt`를 pending 상태로 저장하고, wait 반환 직후 raise한다.  
MES + MFC 동시 연결 상태에서 X 클릭 시 재현됨.

**해결**: `closeEvent`에서 `except (KeyboardInterrupt, Exception): pass` 로 잡는다.

---

## 4. secsgem Windows 소켓 에러 (10057 / 10038)

Windows에서 `disable()` 직후 `accept()` 호출, 또는 `shutdown()` 타이밍 문제로  
`OSError` 10057 / 10038이 발생해 서버 스레드가 비정상 종료된다.  
→ `mes_handler.py` Patch 1이 이를 처리한다. 수정하지 않는다.

---

## 5. 중복 SELECT.req 경쟁 조건

MES TCP 연결 종료 직전 버퍼에 남은 `SELECT.req`가 들어올 때  
`ConnectionStateMachine.select()` 가 예외를 던져 이후 콜백이 실행되지 않는다.  
→ `mes_handler.py` Patch 2가 이를 처리한다.

---

## 6. GEM 재연결 시 CommunicationSM 상태 불일치

MES 연결이 끊긴 후 재연결 시 `CommunicationSM`이 `NOT_COMMUNICATING`이 아닌  
`WAIT_CRA` 등 중간 상태에 있으면 `_on_communicating`이 예외를 던진다.

**해결**: `_on_disconnected_relay`에서 `sm.disable()` → `sm.enable()` 로 강제 리셋.

---

## 7. _s1f13_notified 플래그 — MFC 재연결 시 리셋 필수

MFC가 재연결될 때 `_s1f13_notified`를 `False`로 리셋하지 않으면  
MES가 이미 연결된 상태에서 MFC가 재접속해도 S1F13이 전달되지 않는다.  
`on_mfc_disconnected()`에서 반드시 `self._s1f13_notified = False` 실행.

---

## 8. secsgem 오타: `_object_intitialized`

secsgem 내부 속성명이 `_object_intitialized` (initialized → intitialized 오타)이다.  
`_build_raw_secs_func`에서 `object.__setattr__`로 이 속성을 설정할 때 오타 그대로 사용해야 한다.