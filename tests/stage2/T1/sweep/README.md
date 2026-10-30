# T1 sweep

T1 코드와 `tests/T1/outputs`는 기준선이다. 이 디렉터리의 실행은 그 경로를 옮기거나 덮어쓰지 않는다.

검사만 할 때:

```bash
python3 -B tests/T1/sweep/run_sweep.py --check
```

전체 순차 실행:

```bash
python3 -B tests/T1/sweep/run_sweep.py --run-all
```

중단 후 같은 campaign을 이어갈 때:

```bash
python3 -B tests/T1/sweep/run_sweep.py --resume tests/T1/sweep/campaigns/<campaign_id>
```

campaign 디렉터리의 `resume_command.txt`와 `pid.txt`에 재개 명령과 마지막 부모 PID가 있다. 완료된 슬롯은 다시 모델 호출을 하지 않는다. 코드, 프롬프트, 계획, 입력 해시가 바뀌면 재개하지 않고 새 campaign을 만든다.

## 2026-10-27 단일 경로 정리

현재 경로는 `tests/T1/sweep/`만 사용한다. 2026-10-26에 과거 참조를 위해 만든 `tests/T1_sweep -> T1/sweep` 심볼릭 링크는 제거했다. T2의 새 입력은 현재 경로를 사용하고, T3/T4/T5는 원시 JSON에 저장된 과거 T1 경로를 읽을 때 현재 경로로 변환한다. 원시 JSON과 해시는 변경하지 않으며 링크를 다시 만들지 않는다. 과거 campaign의 실행 소스 해시는 당시 값으로 보존하고, 수정된 소스로 완료 campaign을 재개하지 않는다. 변경 전 소스와 앞선 검증 기록은 `campaigns/20261017_181001_1c4a84/maintenance/20261026_reference_repair/`에 있다.
