# Oracle Cloud VM 크롤러 운영 가이드

> **VM 정보 한 줄 요약**: 춘천 리전, `opc@168.110.103.64`, SSH 키 `ssh-key-2026-06-03.key`

---

## 1. VM 접속하기

### 1-1. 필수 준비물

| 항목 | 값 | 비고 |
|---------------|-----|------|
| SSH 키 파일 | `d:\RSI\Claude\fishing\ssh-key-2026-06-03.key` | 최초 1회 `chmod 600` 필요 |
|     VM IP     | `168.110.103.64` | Oracle Cloud 춘천 리전 |
| 사용자 계정 | `opc` | Oracle Linux 기본 계정 (ubuntu 아님!) |

### 1-2. SSH 키 권한 설정 (최초 1회만)

```powershell
# PowerShell (관리자 권장) - 개인키 소유자만 읽기 가능하게
icacls "d:\RSI\Claude\fishing\ssh-key-2026-06-03.key" /inheritance:r /grant:r "$env:USERNAME:R"
```

> **의미**: SSH는 "키 파일 권한이 너무 열려있다"고 판단하면 접속을 거부합니다. 개인키(.key)는 **나만 읽을 수 있어야** 합니다. 위 명령어는 상속된 권한을 모두 제거하고 나에게만 읽기 권한을 줍니다.

### 1-3. SSH 접속 명령어

```powershell
ssh -i "d:\RSI\Claude\fishing\ssh-key-2026-06-03.key" opc@168.110.103.64
```

> **의미**:
> - `-i` → "이 키 파일로 인증할게" (Identity file)
> - `opc@168.110.103.64` → `opc` 계정으로 `168.110.103.64` 서버에 접속
> - 비밀번호 대신 SSH 키로 로그인하므로 비밀번호 입력 없이 바로 접속됨

### 1-4. 접속 성공 확인

접속 성공하면 프롬프트가 이렇게 바뀝니다:
```
[opc@fishing ~]$
```
- `opc` = 현재 로그인한 사용자
- `fishing` = VM 인스턴스 이름
- `~` = 현재 위치 (홈 디렉터리 = `/home/opc/`)

---

## 2. VM 초기 환경 구축 (이미 완료됨, 참고용)

> 아래는 VM을 새로 만들었을 때 **한 번만** 하면 되는 설정입니다. 현재 VM에는 이미 다 되어 있습니다.

### 2-1. 기본 패키지 설치

```bash
# 시스템 패키지 최신화
sudo dnf update -y

# Python3 + pip + git 설치
sudo dnf install -y python3 python3-pip git
```

> **의미**:
> - `sudo` → 관리자 권한으로 실행 (root처럼)
> - `dnf` → Oracle Linux의 패키지 관리자 (Ubuntu의 apt 같은 것)
> - `-y` → 모든 확인 질문에 자동 Yes

### 2-2. Python 라이브러리 설치

```bash
pip3 install --user requests beautifulsoup4 urllib3 holidays
```

> **의미**:
> - `pip3 install` → Python 패키지 설치
> - `--user` → 시스템 전체가 아닌 내 계정에만 설치 (sudo 불필요)
> - `requests` → HTTP 요청 (웹페이지 가져오기)
> - `beautifulsoup4` → HTML 파싱 (웹페이지에서 데이터 추출)
> - `urllib3` → HTTPS 경고 무시용
> - `holidays` → 한국 공휴일 정보

### 2-3. Git 저장소 클론

```bash
# GitHub에서 코드 다운로드
git clone https://github.com/rainysea23/fishing.git ~/fishing
```

> **의미**: GitHub의 `rainysea23/fishing` 저장소를 VM의 `~/fishing` 폴더에 복제

### 2-4. Git 인증 정보 저장 (push/pull 자동화용)

```bash
git config --global credential.helper store
```

> **의미**: GitHub 아이디/토큰을 한 번 입력하면 `~/.git-credentials` 파일에 저장해서 이후 자동 로그인. 크론 자동화에 필수.

### 2-5. 실행 스크립트 설정

```bash
# 레포에 있는 스크립트를 홈 디렉터리로 복사
cp ~/fishing/run_crawler.sh ~/run_crawler.sh

# 실행 권한 부여
chmod +x ~/run_crawler.sh
```

> **의미**:
> - `cp` → 파일 복사 (크론이 실행할 위치에 스크립트 배치)
> - `chmod +x` → "이 파일은 실행 가능한 스크립트다"라고 표시 (안 하면 실행 거부됨)

### 2-6. 크론(Cron) 등록 — 매 정시 자동 실행

```bash
# 현재 등록된 크론 확인
crontab -l

# 크론 편집 (아래 한 줄 등록)
crontab -e
# 등록할 내용:
# 0 * * * * /home/opc/run_crawler.sh >> /home/opc/crawler.log 2>&1
```

> **의미**:
> - `crontab -l` → (list) 현재 예약된 작업 목록 보기
> - `crontab -e` → (edit) 예약 작업 편집
> - `0 * * * *` → **매시 정각 0분**에 실행
>   - 형식: `분(0-59) 시(0-23) 일(1-31) 월(1-12) 요일(0-7)`
>   - `*` = "모든" (매 시간, 매일, 매월, 모든 요일)
> - `>> /home/opc/crawler.log 2>&1` → 실행 결과와 에러를 `crawler.log` 파일에 저장

---

## 3. 일상 운영 — 코드 수정 후 VM에 배포하기

### 3-1. 로컬에서 GitHub로 Push

```powershell
# 로컬 PC에서 — 코드 수정 후
cd D:\RSI\Claude\fishing
git add .
git commit -m "수정 내용 설명"
git push
```

### 3-2. VM에서 최신 코드 받아오기 (배포)

```bash
# VM에 SSH 접속 후
cd ~/fishing
git pull
```

> 또는 **한 번에 실행** (로컬에서):
> ```powershell
> ssh -i "d:\RSI\Claude\fishing\ssh-key-2026-06-03.key" opc@168.110.103.64 `
>   "cd ~/fishing && git reset --hard HEAD && git pull && echo 배포완료"
> ```
>
> **의미**:
> - `git reset --hard HEAD` → 로컬 변경사항이 있으면 모두 폐기하고 마지막 커밋 상태로 되돌림 (충돌 방지)
> - `git pull` → GitHub에서 최신 코드 다운로드
> - `&&` → 앞 명령어가 성공하면 뒤 명령어 실행 (Windows PowerShell에서는 `;` 후 `if ($?) { ... }`)

### 3-3. `run_crawler.sh` 스크립트 자체를 수정한 경우

```bash
# VM에서
cp ~/fishing/run_crawler.sh ~/run_crawler.sh
chmod +x ~/run_crawler.sh
```

> **의미**: GitHub에서 받은 최신 스크립트를 크론이 바라보는 위치(`~/run_crawler.sh`)로 복사하고 실행 권한 부여

---

## 4. 수동으로 크롤러 실행하기

### 4-1. 전체 실행 (크롤링 + HTML 생성 + 결과 커밋)

```bash
# VM에서
cd ~/fishing
export TELEGRAM_BOT_TOKEN="8956265432:AAEZ8dthVr40CxsqxuZbYdV_GZDgEnGL-Xw"
export TELEGRAM_CHAT_ID="5472071056"
python3 scripts/main.py
```

> **의미**:
> - `export` → 환경변수 설정 (텔레그램 봇 인증 정보)
> - `python3 scripts/main.py` → 크롤러 실행
> - 결과: `index.html`, `data.json` 파일이 갱신되고, 텔레그램으로 알림 발송

### 4-2. 스크립트로 실행 (git pull + 크롤링 + push 까지 한 번에)

```bash
# VM에서
bash ~/run_crawler.sh
```

---

## 5. 로그 확인 & 문제 해결

### 5-1. 크론 실행 로그 보기

```bash
# VM에서 — 최근 50줄
tail -50 ~/crawler.log

# 실시간 로그 감시 (Ctrl+C로 종료)
tail -f ~/crawler.log
```

> **의미**:
> - `tail -50` → 마지막 50줄만 보여줌
> - `tail -f` → (follow) 파일에 새 내용이 추가될 때마다 실시간 출력

### 5-2. 크론 등록 상태 확인

```bash
crontab -l
# 출력 예: 0 * * * * /home/opc/run_crawler.sh >> /home/opc/crawler.log 2>&1
```

### 5-3. Git 상태 확인

```bash
cd ~/fishing
git status          # 변경된 파일 확인
git log --oneline -5  # 최근 5개 커밋 보기
```

### 5-4. 디스크 공간 확인

```bash
df -h ~              # 홈 디렉터리 남은 공간
du -sh ~/fishing     # fishing 폴더가 차지하는 용량
```

> **의미**: VM은 1GB RAM + 스왑으로 운영되므로 디스크가 꽉 차지 않았는지 주기적 확인 필요

---

## 6. 크론 스케줄 변경하기

### 6-1. 현재 설정

```
0 * * * * /home/opc/run_crawler.sh >> /home/opc/crawler.log 2>&1
```
→ 매시 정각에 실행 (스크립트 내에서 0~10분 랜덤 딜레이 추가됨)

### 6-2. 자주 쓰는 크론 표현식

| 표현식 | 의미 |
|--------|------|
| `0 * * * *` | 매시 정각 |
| `30 * * * *` | 매시 30분 |
| `*/30 * * * *` | 30분마다 |
| `0 */2 * * *` | 2시간마다 정각 |
| `0 6 * * *` | 매일 아침 6시 |
| `0 9 * * 1-5` | 평일(월~금) 9시 |

### 6-3. 변경 방법

```bash
crontab -e
# 편집기에서 시간 부분 수정 후 저장
```

---

## 7. 비상 상황 대처

### 7-1. VM이 응답 없을 때

```powershell
# 로컬에서 Ping 확인
ping 168.110.103.64

# SSH 상세 로그로 접속 시도 (문제 파악용)
ssh -v -i "d:\RSI\Claude\fishing\ssh-key-2026-06-03.key" opc@168.110.103.64
```
- Ping 응답 없음 → Oracle Cloud 콘솔에서 인스턴스 상태 확인
- SSH 연결 거부 → 인스턴스 재부팅 (콘솔에서)

### 7-2. git push 실패 (토큰 만료)

GitHub PAT(Personal Access Token)은 90일 후 만료됩니다. 만료 시:

1. GitHub.com → Settings → Developer settings → Tokens → 새 토큰 발급
2. VM에서 다시 인증:
   ```bash
   cd ~/fishing
   git push
   # Username: rainysea23
   # Password: <새로 발급한 PAT 토큰>
   ```
   → `~/.git-credentials`에 자동 저장되어 이후 정상 작동

### 7-3. 텔레그램 알림 안 올 때

```bash
# VM에서 환경변수 확인
echo $TELEGRAM_BOT_TOKEN
echo $TELEGRAM_CHAT_ID

# run_crawler.sh에 토큰이 하드코딩되어 있으므로 재설정
cat ~/run_crawler.sh | grep TELEGRAM
```

### 7-4. IP 차단 의심 시

```bash
# 현재 크론 로그 확인
tail -50 ~/crawler.log | grep -i "timeout\|error\|block\|denied"
```

현재 설정: 1시간 간격 + 0~10분 랜덤 딜레이로 차단 회피 중

---

## 8. 유용한 단축 명령어 모음

```powershell
# === 로컬 PowerShell에서 ===

# VM 접속
ssh -i "d:\RSI\Claude\fishing\ssh-key-2026-06-03.key" opc@168.110.103.64

# VM에 코드 배포 (한 줄)
ssh -i "d:\RSI\Claude\fishing\ssh-key-2026-06-03.key" opc@168.110.103.64 "cd ~/fishing && git reset --hard HEAD && git pull && echo 배포완료"

# VM 로그 보기 (한 줄)
ssh -i "d:\RSI\Claude\fishing\ssh-key-2026-06-03.key" opc@168.110.103.64 "tail -30 ~/crawler.log"
```

```bash
# === VM 내부에서 ===

# 수동 크롤링 실행
bash ~/run_crawler.sh

# 크론 로그 확인
tail -30 ~/crawler.log

# 크론 일정 확인
crontab -l

# git 상태 확인
cd ~/fishing && git status && git log --oneline -3
```

---

## 9. 전체 흐름 요약

```
[로컬 PC]                          [GitHub]                        [Oracle VM]
   │                                  │                                 │
   │ ① git push                  │                                 │
   ├───────────────-───────►│                            │
   │                                  │                                 │
   │                                  │  ② 매 정시 크론 발동   │
   │                                  │  git pull + 실행           │
   │                                  │◄───────────────────────────┤
   │                                  │                                 │
   │                                  │  ③ 크롤링 결과 push    │
   │                                  │◄───────────────────────────┤
   │                                  │                                 │
   │ ④ git pull (필요시)       │                                 │
   │◄─────────────────────────────────┤                            │
   │                                  │                                 │
   │ ⑤ index.html 확인        │  ⑥ 텔레그램 알림        │
   │    (로컬에서 열람)         │    (변동사항 있으면)     │
```

---

## 부록: 참고 링크

- GitHub 저장소: `https://github.com/rainysea23/fishing`
- Oracle Cloud 콘솔: `https://cloud.oracle.com`
- 지도호 예약 페이지: `http://www.newjidoho.com/index.php?mid=bk`
- 라온호 예약 페이지: `http://www.raonfishing.com/index.php?mid=bk`
- 크롤링 결과 페이지: `https://rainysea23.github.io/fishing/` (GitHub Pages)
