# T5 검토와 실험 근거

현재 제품 T5는 [assets.py](../../../src/extractube/assets.py)의 원본 이미지 사용 판정이다. 실행은 `extractube stage2 VIDEO_ID`, 제품 결과 검증은 `extractube stage2-verify VIDEO_ID`를 사용한다. [Stage 2 최종 기록](../../../local/stage2_implementation.md#implementation-validation)에 합의한 검증과 인계 결과가 있다.

루트는 T1~T4처럼 현재 코드와 실행·검수 결과를 중심으로 둔다. 종료된 실험·이관 기록은 archive, 환경·렌더링 의존 자료는 resources에 모았다.

```text
T5/
  run_t5.py          오프라인 검사 CLI
  review.py         기존 인계의 원본·문맥·PTS 검사
  inputs.py         채택된 T4 표본 출처 검사
  common.py         파일·해시·시각·과거 경로 대응
  test_review.py    소비자 보호 검사
  visual_review.json
  visual_closeout.json
  runs/
  reviews/
  archive/
  resources/
```

`tools -> resources/tools`, `vendor -> resources/vendor`는 옛 HTML 329개 참조와 Python 환경의 고정 경로를 위한 호환 링크다. 자료 복사본이나 별도 실행 코드가 아니다.

## 검사

```bash
.venv/bin/python tests/stage2/T5/run_t5.py --check
.venv/bin/python tests/stage2/T5/run_t5.py --check-history
.venv/bin/python -m unittest discover -s tests/T5 -p 'test_*.py' -q
```

모든 명령은 모델을 호출하지 않는다. 제품 실행은 기존 Stage2 CLI로 하고 과거 실험 재실행은 지원하지 않는다.

## 기록

- [visual_review.json](visual_review.json): 29장 원본 검수. [visual_closeout.json](visual_closeout.json): 기존 이미지 10장·9단위 인계 포인터.
- `runs/`, `reviews/`: 원응답·실패·검수·snapshot·인계 기록을 기존 위치에 유지한다.
- [archive](archive/README.md): 종료된 실험, 모델 비교, 이름·모델 이관, 원장과 소스 보존 색인.
- [resources](resources/README.md): 기존 Python 환경·모델 다운로드·렌더링 의존 자료.
- [최종 구조 정리 기록](../../../local/stage2_implementation.md#structure-cleanup).

과거 JSON·원장·source snapshot의 경로와 bytes는 바꾸지 않았다. [경로 대응표](archive/relocation.json)와 오프라인 판독기의 경로 대응으로 새 위치를 찾는다. 과거 소스는 우선 해당 run snapshot을 보고, 현재 위치에서 퇴역한 버전은 [소스 색인](archive/legacy/index.json)의 경로·SHA로 확인한다.

과거 HTML에서 상위 경로가 바뀐 14개 화면은 [링크를 갱신한 보기](reviews/20261030_relocated_html/index.html)로 열 수 있다. 원본 HTML과 원응답·원본 이미지는 그대로 보존했다.
