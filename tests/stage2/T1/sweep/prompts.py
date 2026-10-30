"""Sweep 프롬프트. 자리표시자만 치환하고 str.format은 쓰지 않는다."""

import sys as _path_sys
from pathlib import Path as _WorkspacePath
_path_sys.path.insert(0, str(next(parent for parent in _WorkspacePath(__file__).resolve().parents if (parent / "experiment_paths.py").is_file())))
from experiment_paths import ROOT as EXPERIMENT_ROOT, resolve_path as resolve_experiment_path


BOUNDARY = """영상 전사에서 의미 단위의 경계를 찾는다. 새 의미 단위는 주제, 대상, 목적 중 하나가 바뀔 때 시작한다. 같은 주제의 설명, 근거, 예시는 한 단위로 둔다. 청크의 시작과 끝, 문장 수, 발화 길이는 경계가 아니다.

각 줄의 형식은 [문장 index] 시작시각-종료시각 원문 이다. 원문과 시각은 이미 정해져 있다. 출력은 경계의 문장 index와 근거만 담는다. 문장 index는 아래 목록에 있는 값만 쓴다. 경계 index는 새 의미 단위의 첫 문장이다. 목록의 첫 문장 index는 경계로 고르지 않는다.

JSON 객체만 출력한다. 최상위 키는 boundaries, 값은 배열이다. 각 항목의 키는 sentence_index(정수), reason(비어 있지 않은 문자열)이다. 같은 index를 반복하지 않는다.
경계가 없으면 {"boundaries":[]} 를 출력한다.

문장:
{문장 줄}"""

_CONFLICT_DISTANCE_PHRASE = "문장 index 차이가 1 또는 2인 후보가 연결되어 있다."

CONFLICT_C2 = """겹친 구간에서 의미 경계 후보가 서로 다르거나, 문장 index 차이가 1 또는 2인 후보가 연결되어 있다. 각 후보에 대해 keep, drop, move 중 하나를 고른다.

keep: 그 문장 index에서 주제, 대상, 목적 중 하나가 실제로 바뀐다.
drop: 그 문장 index는 의미 경계가 아니다.
move: 전환은 있으나 시작 문장이 다르다. to_index는 아래 문장 목록에 있는 index이고 목록의 첫 문장이 아니며 원래 후보와 달라야 한다.

가까운 후보라서 하나를 지우지 않는다. 두 후보가 서로 다른 전환이면 둘 다 keep한다. 제안하지 않은 청크의 존재만으로 후보를 지우지 않는다.

JSON 객체만 출력한다. 최상위 키는 decisions, 값은 배열이다. 후보마다 정확히 한 항목을 출력한다. 항목의 키는 sentence_index(후보 정수), action(keep/drop/move), reason(비어 있지 않은 문자열)이다. move일 때만 to_index(정수)를 추가한다.

후보:
{후보 줄}

문장:
{문맥 문장 줄}"""

CONFLICT_DISTANCE_TEXT = {
    "C1": "문장 index 차이가 1인 후보가 연결되어 있다.",
    "C2": _CONFLICT_DISTANCE_PHRASE,
    "C3": "문장 index 차이가 1, 2 또는 3인 후보가 연결되어 있다.",
}

VERIFY_P0 = """아래 두 구간은 경계 하나로 나뉜 인접 의미 단위의 전체 또는 경계 주변 발췌다. 그 경계가 주제, 대상, 목적의 전환이면 keep, 같은 주제의 설명·근거·예시가 이어진 분할이면 merge다.

JSON 객체만 출력한다.
{"action":"keep","reason":"주제, 대상, 목적 중 무엇이 어떻게 바뀌거나 이어지는지 한 문장"}
action은 keep 또는 merge다. reason은 비어 있지 않은 문자열이다.

경계 앞:
{왼쪽 문장 줄}

경계 뒤:
{오른쪽 문장 줄}"""

VERIFY_P1 = """아래 두 구간은 경계 하나로 나뉜 인접 의미 단위의 전체 또는 경계 주변 발췌다. 경계 뒤의 첫 문장이 새로운 주제, 작업 대상, 작업 목적을 시작하면 keep, 같은 대상과 목적에 대한 설명·근거·예시·보충이 이어지면 merge다.

판단 기준:
- 영상 전체의 주제나 상위 작업이 같다는 사실만으로 merge하지 않는다. 예를 들어 같은 상품 등록 과정이어도 상품 상세 설명·상세페이지 등록에서 제조사·인증 정보 입력으로, 또는 상품 기본정보 입력에서 검색 태그 수집으로 넘어가면 독립된 작업 대상과 목적의 전환이므로 keep한다.
- 단순한 클릭, 스크롤, 다음 문장이라는 이유만으로 keep하지 않는다. 같은 필드의 값을 입력하고 저장하는 동작, 같은 키워드를 수집하고 불필요한 항목을 정리하는 동작은 대상과 목적이 계속 같으면 merge한다.
- 앞 설명의 이유·예시가 뒤에 나온다는 이유만으로 새 단위를 만들지 않는다. 반대로 앞 문제에 대한 후속 설명이라는 이유만으로 서로 다른 실행 전략을 합치지 않는다. 사업자 수 확대와 상품 객단가 개선처럼 실행 대상·목적이 달라지면 keep한다.
- 문장 수, 구간 길이, 청크 시작·끝은 경계의 근거가 아니다. 발췌 문맥일 수 있으므로 보이지 않는 내용을 추측하지 않는다.
- reason에는 경계 직전과 직후의 구체적인 대상·목적이 무엇이며 어떻게 달라지거나 유지되는지 한 문장으로 쓴다. '같은 과정이라서' 같은 상위 주제만으로 판단하지 않는다.

JSON 객체만 출력한다.
{"action":"keep","reason":"경계 앞뒤의 구체적인 대상·목적이 어떻게 바뀌거나 이어지는지 한 문장"}
action은 keep 또는 merge다. reason은 비어 있지 않은 문자열이다.

경계 앞:
{왼쪽 문장 줄}

경계 뒤:
{오른쪽 문장 줄}"""


def conflict_prompt(prompt_id: str) -> str:
    if prompt_id not in CONFLICT_DISTANCE_TEXT:
        raise KeyError(prompt_id)
    if CONFLICT_C2.count(_CONFLICT_DISTANCE_PHRASE) != 1:
        raise RuntimeError("CONFLICT 거리 구절이 한 번이 아닙니다.")
    if prompt_id == "C2":
        return CONFLICT_C2
    return CONFLICT_C2.replace(_CONFLICT_DISTANCE_PHRASE, CONFLICT_DISTANCE_TEXT[prompt_id], 1)


def verify_prompt(prompt_id: str) -> str:
    if prompt_id == "P0":
        return VERIFY_P0
    if prompt_id == "P1":
        return VERIFY_P1
    raise KeyError(prompt_id)
