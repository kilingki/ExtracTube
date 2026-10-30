# Stage2 테스트

제품 회귀 검사는 이 폴더의 test_*.py에 있고 T1~T5는 각각 Stage2의 실험·검토 자료다. 향후 Stage3의 같은 단계 이름과 섞지 않는다. 수동 검증 도구는 tools, 실제 실행 증거는 live/reviews에 있다.

```bash
.venv/bin/python -m unittest discover -s tests/stage2 -p 'test_*.py' -q
.venv/bin/python -m unittest discover -s tests/stage2/T5 -p 'test_*.py' -q
.venv/bin/python tests/stage2/T1/run_t1.py --check
.venv/bin/python tests/stage2/T2/run_t2.py --check
.venv/bin/python tests/stage2/T3/run_t3.py --check
.venv/bin/python tests/stage2/T4/run_t4.py --check
.venv/bin/python tests/stage2/T5/run_t5.py --check-history
```

위 명령은 실제 모델 추론을 하지 않는다. 과거 테스트 경로는 experiment_paths.py가 현재 위치로 해석하며 원장·snapshot의 경로 문자열은 그대로다.

- [T5 안내](T5/README.md)
- [수동 도구](tools/README.md)
- [현재 회귀 결과](offline_validation.json)
- [구조 통합 검증](reviews/20261030_pipeline_integration/verification.json)
