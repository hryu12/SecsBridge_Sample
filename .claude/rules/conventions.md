# 코딩 컨벤션

## Python 스타일

- 타입 힌트 필수: 함수 시그니처에 `-> type` 명시
- private 함수/속성: `_` 접두사
- 상수: 모듈 레벨 `ALL_CAPS`
- 클래스: PascalCase, 함수/변수: snake_case
- 주석은 WHY가 비명백할 때만. WHAT 설명 금지

## JSON 메시지 필드 규칙

MFC ↔ 중계기 간 JSON 메시지 구조:

```python
{
    "id":       str,   # UUID, request/reply 매핑 키
    "stream":   int,   # SECS Stream 번호
    "function": int,   # SECS Function 번호
    "reply":    bool,  # True면 동일 id 응답 대기 (30초 타임아웃)
    "data":     any,   # type-tagged JSON 또는 구조체 dict
}
```

특수 이벤트 (S/F 없음):
```python
{"id": "...", "event": "MES_DISCONNECTED"}
```

## type-tagged 값 형식

```python
{"type": "U4", "value": 1234}          # 단일 스칼라
{"type": "A",  "value": "hello"}       # ASCII 문자열
{"type": "Binary", "value": "Cg=="}   # base64 인코딩
{"type": "Boolean", "value": true}
```

지원 타입: `I1 I2 I4 I8 U1 U2 U4 U8 F4 F8 A String JIS8 Binary Boolean`

## secsgem monkey-patch 규칙

`mes_handler.py` 모듈 로드 시 4개의 patch가 적용된다. 새 patch 추가 시:
1. 원본 함수를 `_orig_xxx` 로 저장
2. 패치 함수 정의 후 클래스에 재할당
3. 어떤 버그를 왜 패치하는지 주석 필수

---

## Git 커밋 규칙

```
<type>: <변경 내용 요약>
```

| 타입 | 용도 |
|------|------|
| `feat` | 기능 추가 |
| `fix` | 버그 수정 |
| `design` | UI/디자인 수정 |
| `rename` | 이름 변경 |
| `remove` | 코드 삭제 |
| `docs` | 문서 수정 |
| `refactor` | 코드 개선 |

**예시**
```
feat: MFC S/F 포맷 불일치 시 raw SECS bytes 폴백 인코딩 추가
fix: closeEvent KeyboardInterrupt로 창 닫힘 실패 수정
refactor: RX←MFC 로그 XComPro 스타일 포맷터로 변경
```