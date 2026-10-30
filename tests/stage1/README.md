# Stage1 테스트

run_stage1.py는 실행 코드이며 outputs는 보고서 위치다. 기본 실행과 test_pipeline.py는 기존41개 로컬 계약만 검사한다. --live를 명시할 때만 실제 다운로드·ASR·정렬을 실행한다. 기존 outputs/stage1_report.json은 덮어쓰지 않았다.

```bash
.venv/bin/python tests/stage1/run_stage1.py
.venv/bin/python -m unittest discover -s tests/stage1 -p 'test_*.py' -q
```
