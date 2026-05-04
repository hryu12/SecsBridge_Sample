변경된 모든 파일을 확인하고, 프로젝트 Git 컨벤션(`<type>: <설명>`)에 맞춰 한글 커밋 메시지를 작성한 뒤 현재 브랜치로 push한다.

절차:
1. `git branch --show-current`로 현재 브랜치를 확인한다.
   - 현재 브랜치가 `main`이면 **즉시 중단**하고 사용자에게 경고한다.
     "⚠️ 현재 main 브랜치입니다. 작업 브랜치로 이동 후 다시 실행하세요."
   - 작업 브랜치(`feat/`, `fix/` 등)인 경우에만 계속 진행한다.
2. `git status`와 `git diff --stat`로 변경 파일 목록을 확인한다.
3. 변경 내용을 분석해 적절한 타입(feat/fix/design/rename/remove/docs/refactor)과 한글 설명으로 커밋 메시지 초안을 작성한다.
4. 변경 파일 목록과 커밋 메시지 초안을 사용자에게 보여주고 승인을 받는다.
5. 승인 후 `git add . → git commit → git push origin <현재브랜치>` 순서로 실행한다.
