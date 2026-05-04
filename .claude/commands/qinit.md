현재 디렉터리를 GitHub 원격 저장소에 초기 등록하고 첫 커밋을 push한다.

사용법: /qinit <remote-url>
예시: /qinit https://github.com/user/repo.git

절차:
1. `$ARGUMENTS`에서 원격 URL을 가져온다.
   - URL이 비어 있으면 즉시 중단하고 사용자에게 안내한다.
     "사용법: /qinit <remote-url>"
2. 실행할 명령어 목록을 사용자에게 보여주고 승인을 받는다.
   ```
   git add .
   git commit -m "first commit"
   git branch -M main
   git remote add origin <url>
   git push -u origin main
   ```
3. 승인 후 위 순서대로 실행한다.
   - `git remote add origin` 실패(이미 등록된 경우) 시 `git remote set-url origin <url>`로 대체한다.
4. push 성공 시 원격 URL을 출력한다.