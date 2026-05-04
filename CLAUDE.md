# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 명령어

```powershell
# 의존성 설치 (uv 사용)
uv sync

# 앱 실행
uv run python main.py
```

테스트 파일 없음. 수동으로 MES 시뮬레이터·MFC 클라이언트 연결해 검증.

---

## 아키텍처

```
MES 서버  ←──SECS/HSMS(포트 2001)──→  PySide 중계기  ←──JSON/TCP(포트 19001)──→  MFC 프로그램
(Host)                                  (Equipment)         개행 구분 JSON
```

MES(반도체 장비 제어 서버)와 MFC(로컬 제어 프로그램) 사이를 중계하는 브리지 애플리케이션.  
MES와는 SECS/HSMS 프로토콜(secsgem), MFC와는 newline-delimited JSON/TCP로 통신한다.

### 파일별 역할

| 파일 | 역할 |
|------|------|
| `main.py` | PySide6 앱 진입점 |
| `main_window.py` | Qt UI + `Bridge`(QThread) — asyncio 루프 관리 |
| `relay.py` | SECS↔JSON 변환 로직 (`Relay` 클래스) + `secs_to_dict`/`dict_to_secs` |
| `mes_handler.py` | `MesHandler(GemEquipmentHandler)` — MES SECS 수신 → relay 전달, secsgem 버그 패치 4개 포함 |
| `mfc_server.py` | `MfcServer` — asyncio JSON/TCP 서버, id 기반 request/reply 매핑 |

### 스레딩 모델

- **Qt 메인 스레드**: UI
- **`Bridge` QThread**: asyncio 이벤트 루프를 `loop.run_until_complete()`로 소유
- **secsgem 콜백**: 별도 OS 스레드 (blocking I/O)
- 스레드 경계를 넘는 asyncio 호출은 반드시 `asyncio.run_coroutine_threadsafe()` 사용

### JSON 메시지 구조

```json
{
  "id": "uuid",
  "stream": 2,
  "function": 41,
  "reply": true,
  "data": { "RCMD": {"type": "A", "value": "START"} }
}
```

- `data` 값은 `{"type": "U4", "value": 1}` 형태의 type-tagged 객체 또는 그 중첩 리스트
- `reply: true` → 동일 `id`로 응답이 올 때까지 대기 (타임아웃 30초)
- 특수 이벤트: `{"id": "...", "event": "MES_DISCONNECTED"}` (MFC에 MES 연결 해제 통보)

### SECS ↔ JSON 변환 (relay.py)

| 함수 | 방향 | 설명 |
|------|------|------|
| `secs_to_dict` | SECS → JSON | secsgem decode → type-tagged dict |
| `dict_to_secs` | JSON → SECS | secsgem S/F 클래스 생성; 구조 불일치 시 `_build_raw_secs_func` 폴백 |
| `_json_to_secs_bytes` | JSON → bytes | type-tagged 이종 리스트를 raw SECS-II bytes로 직접 인코딩 |
| `_fmt_secs_msg` | JSON → 로그 | XComPro 스타일 텍스트 (LIST/INT1/UINT4 등) |

### 연결 시퀀스

1. 앱 시작 → `MfcServer` 리스닝(포트 19001) + `MesHandler` PASSIVE 대기(포트 2001)
2. MFC 연결 → `on_mfc_connected` → `MesHandler.enable()` [별도 스레드] → HSMS 활성화
3. MES 연결 → HSMS SELECT → GEM S1F13/S1F14 핸드셰이크 → `on_mes_connected` → MFC에 S1F13 전달
4. MFC 연결 해제 → `MesHandler.disable()` [별도 스레드] → HSMS 비활성화

### secsgem 패치 (mes_handler.py)

secsgem의 Windows 환경 버그 4가지를 모듈 로드 시 monkey-patch로 수정:
- **Patch 1**: Windows 소켓 에러 10057/10038 무시
- **Patch 2**: 중복 SELECT.req 경쟁 조건 방지
- **Patch 3**: 알 수 없는 S/F decode 실패 시 raw 메시지 그대로 전달
- **Patch 4**: `GemHandler._on_communicating` 재연결 시 SM 상태 불일치 방지
