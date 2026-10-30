# Stage 2 수동 검증 도구

자동 회귀 검사는 상위 `test_*.py`에 있다. 이 폴더에는 필요할 때 명시적으로 실행하는 도구만 둔다.

| 도구 | 역할 |
|---|---|
| audit_paused_run.py | 중단 run의 완료 부분을 모델 호출 없이 감사 |
| review_live_video.py | 완료 run의 원본 대조 목록·접촉 시트 준비 |
| validate_live_t5.py | 예산을 고정한 실제 T5 표본 검증; 실행하면 추론 호출 발생 |
| validate_bounded_pipeline.py | 짧은 실제 영상 검증 입력 준비; --execute-plan 실행 시 추론 발생 |

종료된 특정 run을 수정하던 `complete_t5_fixture.py`는 퇴역시켰다. 당시 원본은 [T5 legacy 색인](../T5/archive/legacy/index.json)에 있다. 이번 구조 정리에서는 어떤 실제 추론 도구도 실행하지 않았다.
