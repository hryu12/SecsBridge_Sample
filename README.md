# SecsBridge

MES 호스트 시스템과 MFC 로컬 제어 프로그램 간 통신을 중계하는 브리지 애플리케이션입니다.

## 개발 환경

- Python 3.14 / PySide6
- 패키지 관리: [uv](https://docs.astral.sh/uv/)
- 의존성: `secsgem >= 0.3.0`, `pyside6 >= 6.11.0`

## 아키텍처

```
MES 서버  ←──SECS/HSMS(포트 2001)──→  SecsBridge  ←──JSON/TCP(포트 19001)──→  MFC 프로그램
(Host)                                 (Equipment)       개행 구분 JSON
```

## 주요 기능

- SECS/HSMS (SEMI E37) GEM Equipment 통신
- MFC 프로그램과 JSON/TCP 양방향 중계
- SECS ↔ type-tagged JSON 자동 변환
- request/reply UUID 매핑 (타임아웃 30초)
- MES 연결 해제 시 MFC 자동 통보

## 시작하기

```powershell
# 의존성 설치 (uv 및 Python 3.14 자동 설치)
uv sync

# 실행
uv run python main.py
```

## 설정

앱 실행 후 UI에서 직접 설정합니다.

| 항목 | 기본값 | 설명 |
|------|--------|------|
| MES IP | `0.0.0.0` | PASSIVE 모드 (모든 인터페이스 수신) |
| MES Port | `2001` | HSMS 수신 포트 |
| MFC IP | `127.0.0.1` | MFC 클라이언트 바인드 주소 |
| MFC Port | `19001` | JSON/TCP 수신 포트 |