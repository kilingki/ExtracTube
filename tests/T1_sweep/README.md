# T1 sweep

T1 코드와 `tests/T1/outputs`는 기준선이다. 이 디렉터리의 실행은 그 경로를 옮기거나 덮어쓰지 않는다.

검사만 할 때:

```bash
python3 -B tests/T1_sweep/run_sweep.py --check
```

전체 순차 실행:

```bash
python3 -B tests/T1_sweep/run_sweep.py --run-all
```

중단 후 같은 campaign을 이어갈 때:

```bash
python3 -B tests/T1_sweep/run_sweep.py --resume tests/T1_sweep/campaigns/<campaign_id>
```

campaign 디렉터리의 `resume_command.txt`와 `pid.txt`에 재개 명령과 마지막 부모 PID가 있다. 완료된 슬롯은 다시 모델 호출을 하지 않는다. 코드, 프롬프트, 계획, 입력 해시가 바뀌면 재개하지 않고 새 campaign을 만든다.
