# 구현 규칙 — 스레딩 및 비동기 아키텍처

## 1. 스레드 경계 규칙

이 프로젝트는 세 가지 실행 컨텍스트가 공존한다.

| 컨텍스트 | 역할 |
|----------|------|
| Qt 메인 스레드 | UI, 시그널/슬롯 |
| `Bridge` QThread | asyncio 이벤트 루프 소유 (`loop.run_until_complete`) |
| secsgem 콜백 스레드 | OS 스레드, blocking I/O |

**asyncio 코루틴을 다른 스레드에서 호출할 때는 반드시 `run_coroutine_threadsafe` 사용:**

```python
# secsgem 콜백(OS 스레드)에서 asyncio로 전달
asyncio.run_coroutine_threadsafe(coro, self._loop)

# asyncio에서 blocking 함수 실행 (루프 블로킹 방지)
loop = asyncio.get_running_loop()
await loop.run_in_executor(None, blocking_func)
```

**Qt 시그널은 메인 스레드에서만 emit:**
Bridge QThread → Qt UI 전달은 `Signal.emit()` 경유. 슬롯에서 직접 위젯 조작.

---

## 2. asyncio 루프에서 blocking 호출 금지

`async def` 코루틴 안에서 secsgem의 blocking 메서드를 직접 호출하면 루프 전체가 멈춘다.

```python
# ❌ 잘못된 예 — 루프 블로킹
async def _run_all():
    await stop_event.wait()
    mes.disable()           # blocking! asyncio 루프 멈춤

# ✅ 올바른 예 — executor로 위임
async def _run_all():
    await stop_event.wait()
    loop = asyncio.get_running_loop()
    await asyncio.wait_for(loop.run_in_executor(None, mes.disable), timeout=2.0)
```

secsgem의 `enable()`, `disable()`, `send_and_waitfor_response()` 는 모두 blocking이다.  
`send_and_waitfor_response()`는 `_forward_to_mes`처럼 별도 OS 스레드에서 호출한다.

---

## 3. MFC → MES 방향: 반드시 별도 스레드

`on_json_received`에서 `_forward_to_mes`는 반드시 `threading.Thread`로 실행한다.  
asyncio 루프 안에서 직접 호출하면 `send_and_waitfor_response`가 루프를 블로킹한다.

```python
def on_json_received(self, msg: dict):
    threading.Thread(target=self._forward_to_mes, args=(msg,), daemon=True).start()
```

---

## 4. closeEvent 종료 시퀀스

```
Bridge.stop()                     # asyncio stop_event.set()
  └─ _run_all 코루틴 재개
       └─ mes.disable() [executor, timeout=2s]
       └─ mfc.stop()
       └─ 잔여 태스크 cancel
Bridge.wait(5000)                 # QThread 종료 대기
```

`closeEvent`에서 `KeyboardInterrupt` 포함 예외를 잡아야 한다. `super().closeEvent(event)` 는 항상 호출되어야 창이 닫힌다.