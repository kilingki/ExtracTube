# 테스트 배치

- stage1/: 다운로드·오디오·ASR·정렬·문장 파이프라인의 실행기, 로컬 회귀 검사와 결과.
- stage2/: 의미 단위·시각 자료 처리의 제품 검사, T1~T5 실험과 검증 근거.
- 향후 Stage3의 T1~T5는 tests/stage3/에 배치한다. 루트 T1~T5 이름을 공유하지 않는다.

```bash
.venv/bin/python -m unittest discover -s tests/stage1 -p 'test_*.py' -q
.venv/bin/python -m unittest discover -s tests/stage2 -p 'test_*.py' -q
.venv/bin/python -m unittest discover -s tests/stage2/T5 -p 'test_*.py' -q
```

Stage1 기본 실행기는 로컬 검사만 수행하며 --live를 지정하면 실제 YouTube/ASR/FA를 사용한다. 수동 Stage2 추론 도구는 stage2/tools에 있다. 과거 결과 JSON은 원래 경로를 유지하고 stage2/experiment_paths.py가 현재 위치로 해석한다.
