# 테스트 배치

## 영상별 결과 HTML

**[R1 HTML 결과 열기](revision/output/index.html)**

R1 결과 HTML은 `revision/output/` 바로 아래에 영상 순서대로 정리했다. 번호가 붙은 HTML은 변경 후 결과이며, `before/`에는 변경 전, `audit/`에는 제외 근거 HTML이 있다. 이미지가 내장되어 파일을 직접 열 수 있다. 원본 실행의 HTML을 바이트 그대로 복사하고 SHA-256 일치를 확인했다. `index.html`과 프로젝트 루트의 [results.html](../results.html)에서 모두 접근할 수 있다. R1 품질 검수는 대기 상태다. 기존 배치 결과는 [이전 결과 모음](bottleneck/results/index.html)에 보존한다.

<details>
<summary>원본 실행과 품질 상태</summary>

[테스트 입력 URL](stage1/test_inputs.txt) 순서다. 모두 경고를 포함해 생성됐으며 품질 상태는 각 실행의 `result.json` 기준이다.

| # | 영상 | 품질 상태 |
|---:|---|---|
| 1 | [소싱 16주 루틴 캘린더](bottleneck/runs/batches/remaining-eight/stage3data/CvuE-jyg-N8/content/runs/20261108T034355Z_cd1db3789074/article.html) | `revise` |
| 2 | [쿠팡 상품등록 2026년 최신판](bottleneck/runs/batches/first-two/stage3data/7EJ-xotXiSE/content/runs/20261107T171042Z_fb408060732f/article.html) | `revise` |
| 3 | [쿠팡 상품·브랜드 등록](bottleneck/runs/batches/first-two/stage3data/Fw-8hZhnH3Q/content/runs/20261107T174237Z_5dd2dd4188ba/article.html) | `revise` |
| 4 | [스마트스토어 상품명 만들기](bottleneck/runs/batches/remaining-eight/stage3data/bjDkfjSmXVo/content/runs/20261107T195230Z_7441ba94605b/article.html) | `unreviewed` |
| 5 | [유튜브에서 말하는 부업이 실패하는 이유](bottleneck/runs/recovery/h2wn130ozlg/data/H2WN130OzLg/content/runs/20261108T173628Z_07098f084db7/article.html) | `revise` |
| 6 | [위탁판매 월 1,000만 원까지 겪는 문제](bottleneck/runs/recovery/rerun-two/stage3data/iEMuJiVY9Oc/content/runs/20261108T025710Z_dcdd4497b6b5/article.html) | `revise` |
| 7 | [상품 하나로 월매출 6,000만 원](bottleneck/runs/batches/remaining-eight/stage3data/hGVL9XRU9IQ/content/runs/20261108T033253Z_c3eaa89cf7ed/article.html) | `revise` |
| 8 | [온라인 사업에서 빠르게 성장한 셀러의 공통점](bottleneck/runs/batches/remaining-eight/stage3data/iVelddb1qQ4/content/runs/20261108T032003Z_582210c62c5e/article.html) | `revise` |
| 9 | [Flow Matching 시각화](bottleneck/runs/recovery/rerun-two/stage3data/wFikV0Y7NIk/content/runs/20261108T022355Z_db5126f3e2f5/article.html) | `unreviewed` |
| 10 | [Attention 발전 과정과 MLA](bottleneck/runs/batches/remaining-eight/stage3data/dgGhsYSVzKc/content/runs/20261108T031447Z_d5fbe06003cd/article.html) | `revise` |

5번은 11월 9일(KST)에 별도 캠페인에서 완료된 후속 결과다. 열람용 결과를 교체할 때 작업 폴더의 `content/current.json`과 `result.json`을 확인하고 복사본과 두 목록을 함께 갱신한다.

</details>

## 테스트와 실행 기록

- `stage1/`에는 다운로드·오디오·ASR·정렬·문장 파이프라인의 실행기와 로컬 회귀 검사가 있다.
- `stage2/`에는 의미 단위·시각 자료 처리의 제품 검사와 T1~T5 실험이 있다.
- `stage3/`에는 글 생성 회귀 검사, 전체 파이프라인 실행기와 T1~T5 실험이 있다.
- `bottleneck/`에는 Stage 2·3 계측 도구와 실행 기록이 있다. `runs/`에서 초기 진단, 배치, 복구 기록을 구분한다. [계측 안내](bottleneck/README.md)

```bash
.venv/bin/python -m unittest discover -s tests/stage1 -p 'test_*.py' -q
.venv/bin/python -m unittest discover -s tests/stage2 -p 'test_*.py' -q
.venv/bin/python -m unittest discover -s tests/stage2/T5 -p 'test_*.py' -q
```

Stage1 기본 실행기는 로컬 검사만 수행하며 --live를 지정하면 실제 YouTube/ASR/FA를 사용한다. 수동 Stage2 추론 도구는 stage2/tools에 있다. 과거 결과 JSON은 원래 경로를 유지하고 stage2/experiment_paths.py가 현재 위치로 해석한다.
