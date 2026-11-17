# 안전·환경 법령 이슈 모니터

산업안전보건법, 중대재해처벌법, 화학물질관리법, 연구실안전관리법의 이슈를 매일 자동 수집해
`docs/index.html` 한 페이지로 보여주고, 새 항목은 블로그 초안(`docs/drafts/날짜.md`)으로 만들어 줍니다.

## 수집 항목
| 항목 | 소스 | 방식 |
|---|---|---|
| 개정, 시행 예정 | 국가법령정보센터 Open API | 자동 |
| 신구법 전/후 비교 | 국가법령정보센터 Open API | 자동 |
| 중처법 판례 | 국가법령정보센터 Open API(판례) | 자동 |
| 입법예고·행정예고, 정책방향, 우수사례 | 직접 등록한 RSS/Atom | `config.json`의 `feeds`에 추가 |

## 설정 순서
1. https://open.law.go.kr 에서 OPEN API 활용 신청 후 인증키(OC)를 받습니다.
   신청 시 서버 IP/도메인 등록 항목이 있습니다. 아래 "중요" 항목을 먼저 읽으세요.
2. 이 폴더를 GitHub 저장소로 올립니다.
3. 저장소 Settings > Secrets and variables > Actions 에서 `LAW_OC` 이름으로 인증키를 등록합니다.
4. Settings > Pages 에서 Branch `main`, 폴더 `/docs` 를 선택합니다.
5. Actions 탭에서 "법령 이슈 수집"을 수동 실행(Run workflow)해 확인합니다.

## 로컬 점검
```
set LAW_OC=발급받은ID          (Windows)
python collector.py --check    소스별 연결 상태 확인
python collector.py            수집 + 대시보드 생성
```

## 중요: 서버 IP 등록
법제처 Open API는 신청 시 호출 서버의 IP 또는 도메인을 등록해야 응답하는 것으로 알려져 있습니다.
GitHub Actions는 실행할 때마다 IP가 달라질 수 있어 자동 실행이 인증 오류로 끝날 수 있습니다.
먼저 `--check`로 로컬에서 확인하고, Actions에서 "JSON이 아닌 응답" 오류가 나면
같은 스크립트를 고정 IP가 있는 PC나 서버(Windows 작업 스케줄러, cron)에서 돌리면 됩니다.

## 피드 추가 예
```json
"feeds": [
  {"name": "고용노동부 보도자료", "url": "RSS 주소", "type": "정책방향"}
]
```
`type`은 입법예고, 정책방향, 우수사례 중 하나입니다. 제목과 요약에 각 법의 키워드가 들어간 항목만 해당 법 탭에 들어갑니다.
