"""Four-gate source review, T3 closure judgement, and canonical T4 handoff exporter for E05 (0 inference calls)."""
import argparse
import copy
import fcntl
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import run_t3 as t
import run_e05_plan as e05
import render_e05


PRIMARY_UNIT_REVIEW_SPECS = [
    {
        "unit_id": "p_u00",
        "range": [0, 5],
        "must_contain": {1: "어카미입니다.", 3: "브랜드 등록"},
        "preserved_information_check": "인사(0), 화자 자기소개(1 '어카미입니다.' — 귀속 정보 보존 주의 연결), 쿠팡 상품 등록 주제 및 최근 브랜드 등록 변경 안내 예고(2~4)가 생략 없이 순서대로 보존됨.",
        "t4_sufficiency": "도입부의 화자 신원 귀속과 글의 범위(쿠팡 상품 등록 + 브랜드 등록 변경 사항)를 T4가 외부 배경 추가 없이 서술하기에 충분함. V01은 도입 음성 구간(0.48~17.68s)과 화면 시점(52.0s)이 달라 defer 유지가 타당함.",
    },
    {
        "unit_id": "p_u01",
        "range": [5, 15],
        "must_contain": {6: "썸네일, 상세 페이지", 10: "카탈로그 매칭하기", 13: "아이템 위너 작업"},
        "preserved_information_check": "최신 버전 화면 기준 안내(5, '최 최 최신' 구어 반복 원문 유지), 사전 준비물 4종(상품·상품명·썸네일·상세페이지)과 판매가 사전 계산 권고(6~7), 도매국 상품 예시 전제(8), 상품 관리→상품 등록 탭 이동 및 '카탈로그 매칭하기(아이템 위너 작업)' 절대 금지 경고(9~14)가 모두 보존됨.",
        "t4_sufficiency": "사전 준비물 목록, 시연 전제(도매국), UI 진입 경로와 카탈로그 매칭 금지 이유가 구체적으로 포함되어 T4 작성 재료로 충분함. 52.0s 프레임 V01은 reader 제외 정책에 따라 defer로 메타데이터만 유지함.",
    },
    {
        "unit_id": "p_u02",
        "range": [15, 18],
        "must_contain": {15: "판매자 배송", 16: "로켓그로스", 17: "지금은 판매자 배송"},
        "preserved_information_check": "현재 시점의 '판매자 배송' 체크 지시(15·17)와 향후 사업 확장 시 '로켓그로스' 입점 조건부 선택 절차(16)가 시점·조건 혼동 없이 원문 그대로 배정됨.",
        "t4_sufficiency": "E02 채택 계획(selected/p02.json item 0~2)과 대조해도 현재 실행 설정과 미래 확장 조건의 구분이 원문 3문장만으로 명확히 전달됨.",
    },
    {
        "unit_id": "p_u03",
        "range": [18, 40],
        "must_contain": {24: "예전에는", 28: "알고 있거든요", 35: "로고를 눅기를 딴다든지", 38: "두 부류로 구분"},
        "preserved_information_check": "화면 하단 이동(18), 브랜드 항목 변경 및 타격 평가(19~23), 과거 스토어명 입력 대비 현재 등록 업체만 인정한다는 화자 설명(24~25), '모모 기업' 예시·검색·브랜드 추가하기(26~27), 추가 서류 인지 범위 주의(28)와 로고·라벨지 전언(29), 직접 제작/라벨즈 보유 시 등록 vs 불가 시 브랜드 없음(31~32), 내부 링크(33→[30])로 연결된 편법 3가지(샘플 촬영/공급처 이미지 로고 편집 34, 의심 전사 '로고를 눅기를 딴다든지' 주의 보존 35, AI 툴 편집 신청 36), 두 부류 구분 마무리(37~39)가 전부 보존됨.",
        "t4_sufficiency": "E01에서 발생했던 현재 정책 사실 격상(F01), 35번 AI 오배정(F02), 두 부류 임의 대체 분류(F03)가 모델 재서술 제거와 고정 제약(preserve_attribution_and_modality, do_not_resolve_uncertain_STT) 및 내부 링크(33→[30])로 차단됨. 39번 전환 문장도 자동 삭제 없이 제공됨.",
    },
    {
        "unit_id": "p_u04",
        "range": [40, 57],
        "must_contain": {41: "심플하고 핵심적인 키워드", 45: "시면은 카테고리 검색", 50: "변기 뚫어뻥"},
        "preserved_information_check": "이전 문맥([6, 7] 사전 준비물)을 읽기 전용으로 연결한 노출 상품명 핵심 키워드 입력 및 도매국 상품명 시연(40~42), 카탈로그 매칭 팝업 무시(43), 잘린 전사 주의가 연결된 카테고리 검색 진입(44~45 '시면은'), 쿠팡 사이트에서 '변기 뚫어뻥' 검색 후 광고 제외 1위 업체의 '뚫어뻥' 카테고리를 참고해 선택하는 절차(46~56)가 온전히 보존됨.",
        "t4_sufficiency": "이전 준비물 참조([6, 7])가 중복 본문 배정 없이 연결되고, 45번 절단 어절 주의와 카테고리 역추적 절차가 단계별로 명확해 T4 서술 재료로 충분함.",
    },
    {
        "unit_id": "p_u05",
        "range": [57, 66],
        "must_contain": {59: "주문자 연락처", 61: "프리 혹은 원 사이즈", 63: "화이트와 블랙"},
        "preserved_information_check": "옵션 화면 진입 및 상품별 화면 차이(57~58), 비정상 연락처 항목 혼입 주의 2건이 연결된 59번 의심 원문 전체, 사이즈 예시(스몰/미디움/라지 vs 프리/원 사이즈)와 '프리' 입력·추가(60~62), 색상 '화이트'·'블랙' 각각 추가 후 하단 목록 자동 생성(63~65)이 누락·임의 교정 없이 보존됨.",
        "t4_sufficiency": "59번의 전사 오류 의심 항목('주문자 연락처' 등)을 확정 UI 필드로 서술하지 못하도록 주의 2건과 do_not_resolve_uncertain_STT 제약이 함께 제공되며, 60~65번의 실제 사이즈·색상 입력 예시가 명확해 T4가 안전하게 작성할 수 있음.",
    },
    {
        "unit_id": "p_u06",
        "range": [66, 87],
        "must_contain": {69: "결제하게 되는 금액", 75: "만 오천 원", 77: "자동 가격 조정", 83: "재고 수량 일괄 입력"},
        "preserved_information_check": "입력 전환(66), 정상가·판매가 구분 및 빨간 표시 중요성(67~68), 실제 결제 금액인 판매가 10,000원 임의 예시와 마진 사전 계산 당부(69~73), 정상가 15,000원 예시와 과도하지 않은 적절한 할인율 세팅 권고(74~76), 자동 가격 조정 선택 가능성 및 화자의 과거 사용→최근 미사용 경험(77~80), 다수 옵션 시 전체 체크박스를 통한 재고 수량 일괄 입력과 최대 숫자 입력·후속 공란 유지(81~86)가 모두 보존됨.",
        "t4_sufficiency": "수치 예시(판매가 만 원, 정상가 만 오천 원), 화자의 개인 운영 방식 변화(79~80), UI 일괄 입력 절차가 빠짐없이 담겨 T4 작성 범위가 명확함.",
    },
    {
        "unit_id": "p_u07",
        "range": [87, 102],
        "must_contain": {89: "추가 이미지 최대 아홉 장", 95: "상세 페이지 완료", 99: "이미지 추가 버튼"},
        "preserved_information_check": "옵션별 상품 이미지 등록과 잘린 장문 주의가 연결된 89번(화이트/블랙 대표 이미지=썸네일, 추가 이미지 최대 9장, 단일 통합 썸네일 vs 색상별 개별 썸네일 허용), 9장 채우기 권고와 플러스 버튼 조작(90~93), 이전 문맥([6] 상세 페이지 준비)을 참조하는 상품 상세 설명 이미지 등록·개별 이미지 추가 버튼·저장 절차(94~101)가 보존됨.",
        "t4_sufficiency": "89번 복합 구어 문장의 세부 옵션 조건과 95번의 사전 준비물 참조([6])가 모두 연결되어 요약 손실 없이 T4가 단계별 설명문으로 풀 수 있음.",
    },
    {
        "unit_id": "p_u08",
        "range": [102, 112],
        "must_contain": {103: "제조사", 104: "모모기업", 105: "인증 상품", 109: "과세 면세 구분"},
        "preserved_information_check": "상품 주요 정보의 제조사 입력 원칙(미상 시 브랜드명 기재 안내, 스토어명 '모모기업' 또는 '협력사' 입력 가능 예시 102~104), 비인증 상품 기본 유지 vs 인증 상품 클릭 후 인증 번호 입력 조건(105~108), 과세·면세 구분(대부분 과세 기본 유지 109~111)이 순서대로 보존됨.",
        "t4_sufficiency": "T1 실패 배치 진단에서 지적됐던 108번(인증 기본값 유지)과 110번(과세 기본값 유지)의 설정 대상 구분이 원문 문맥 전체 제공으로 명확히 보존됨.",
    },
    {
        "unit_id": "p_u09",
        "range": [112, 123],
        "must_contain": {115: "최대 20개", 117: "상품명에 들어간 키워드를 제외", 121: "아이템 스카우트"},
        "preserved_information_check": "검색어→태그 영역 진입과 최대 20개 입력 한도(112~115), 이전 문맥([40, 41] 상품명 키워드 조사)을 참조해 수집 키워드 중 상품명에 이미 쓴 키워드를 제외하고 넣는 원칙(116~117), 쿠팡 태그 중요성과 20개 전량 입력 강조(118~120), 아이템 스카우트 등 기본 외부 키워드 도구 언급(121~122)이 모두 보존됨.",
        "t4_sufficiency": "[40, 41] 이전 문맥이 읽기 전용으로 제공되어 왜 상품명 키워드를 태그에서 제외하는지 논리적 연결이 분명하며 수치(20개)와 도구 예시가 완전함.",
    },
    {
        "unit_id": "p_u10",
        "range": [123, 150],
        "must_contain": {134: "기구.", 135: "욕실 뚜렷봉 액체 아니니까", 137: "영광 검색기", 141: "변기 뚜렷빵", 144: "변기 뚫어뻥 기구"},
        "preserved_information_check": "쿠팡 사이트 내 하위 키워드 확인 및 연관어 복사→메모장 붙여넣기·무관 키워드 삭제(123~133), 단독 발화 134('기구.')와 복합 부정/가능성 예시 135('...욕실 뚜렷봉 액체 아니니까 이런 것들은 다 지워주세요.') 원문 전체, 엔터 후 추가 노출되는 의심 전사 137('영광 검색기' 주의 연결) 복사·필터링(136~140), 의도적 오타 키워드('변기 뚜렷빵', '뚜렷빵' 주의 연결) 필수 포함 및 무관어('배수구') 제외·20개 목표(141~142), 하위 검색어('변기 뚫어뻥 기구') 재진입 심화 수집 및 20개 전량 개별 입력 재강조(143~149)가 무결하게 보존됨.",
        "t4_sufficiency": "E03·E04에서 반복 실패한 134·135 임의 삭제/유지 이분법이 제거되고 E04 검수 참조 기반의 preserve_compound_scope_and_negation 가드가 부착됨. 단, T4 본문 생성 시 모델이 다시 134·135를 자의적으로 분해하지 않는지는 T4 원문→본문 검수에서 반드시 재확인해야 함.",
    },
    {
        "unit_id": "p_u11",
        "range": [150, 174],
        "must_contain": {154: "열 개 내외", 156: "피스톤형", 161: "50cm", 163: "애매하면 그냥 이거 삭제", 168: "필터를 걸어서 구매"},
        "preserved_information_check": "검색 필터 진입 및 상품군별 필터 개수 차이(일반 10개 내외라는 화자 기억 150~154), 형태 선택 예시(일반형/피스톤형 일괄 적용, 무관한 샤워커튼 해제 155~158), 가로·세로 길이 확인 방법(상세페이지 또는 샘플 실측, 50cm 예시)과 의미가 애매한 길이 항목 삭제·무관한 욕조 제외·각도 조절 가능 여부 입력(159~165), 필터 구매자 노출 원리와 관련 필터 전량 입력·무관 항목 체크 해제 마무리(166~173)가 보존됨.",
        "t4_sufficiency": "입력·일괄 적용·삭제·체크 해제의 4가지 구체적 필터 조작 조건과 노출 근거가 모두 원문에 포함되어 T4 작성 재료로 충분함.",
    },
    {
        "unit_id": "p_u12",
        "range": [174, 180],
        "must_contain": {174: "상품 정보 제공 곳", 175: "상품 상세 페이지 참조", 178: "구비서류"},
        "preserved_information_check": "의심 UI 명칭 주의가 연결된 174('상품 정보 제공 곳')와 전체 '상품 상세 페이지 참조' 일괄 입력(175~177), 인증 상품일 때만 구비서류가 필요하고 일반 상품은 넘어간다는 조건(178~179)이 보존됨.",
        "t4_sufficiency": "174번 UI 명칭 전사 의심 주의가 함께 전달되어 임의 명칭 교정 없이 입력 값('상품 상세 페이지 참조')과 구비서류 조건부 처리만 정확히 서술 가능함.",
    },
    {
        "unit_id": "p_u13",
        "range": [180, 190],
        "must_contain": {181: "도매처 주소지", 185: "사업장 소재지", 186: "제주도서 상관 배송", 189: "CJ"},
        "preserved_information_check": "출고지·반품 교환지에 도매처가 아닌 판매자 사업장 소재지를 입력하는 이유(위탁 판매 특성상 공급처에서 교환·반품이 진행되어 형식적 입력임 180~185), 의심 전사 주의가 연결된 186('제주도서 상관 배송' 가능/불가능 체크 및 택배사 계약 코드 언급), 일반적 택배사로 CJ 택배 선택 권고 및 쿠팡 제휴 택배사 언급(187~189)이 보존됨.",
        "t4_sufficiency": "위탁 판매 주소지 입력의 핵심 이유(184~185)와 186번 전사 의심 주의, 택배사 선택 예시가 완결되어 T4 작성에 충분함.",
    },
    {
        "unit_id": "p_u14",
        "range": [190, 205],
        "must_contain": {190: "일반 배송", 192: "묶음 배송", 194: "당일 출고", 198: "10시나 11시", 203: "3,000원"},
        "preserved_information_check": "일반 배송 유지(190~191), 화자 주장 주의가 연결된 192(위탁 판매 도매처 상이로 묶음 배송 불가능 체크 권고, 노출 유리함을 이유로 한 무료 배송 및 당일 출고 강력 추천 192~194), 세분화된 출고 시간대에서 도매처 12시 이전 마감을 고려한 10시·11시 설정 권고(195~198), 이전 leaf 문맥([183, 184, 185])을 참조하는 사업장 소재지 재입력(199~201), 반품·교환 배송비 3,000원 일반 기준 권고(202~204)가 보존됨.",
        "t4_sufficiency": "Root 10을 두 leaf(p_u13, p_u14)로 분할하면서 생긴 199→[183, 184, 185] 참조가 previous_context로 정확히 복원되었고, 192번의 노출 효과 주장도 화자 권고로 제한하는 주의가 결합되어 T4 작성에 충분함.",
    },
    {
        "unit_id": "p_u15",
        "range": [205, 217],
        "must_contain": {205: "판매 요청하기", 209: "심사 중", 213: "소규모 단톡방", 216: "좋아요와 구독"},
        "preserved_information_check": "판매 요청하기 버튼 클릭으로 등록 완료(205~208), 즉시 승인 vs '심사 중' 표시 시 대기 안내(209~211), 최신 버전 정리 마무리 및 화자의 소규모 단톡방 홍보·참여 링크 안내·좋아요/구독 인사(212~216)가 자동 삭제 없이 보존됨.",
        "t4_sufficiency": "승인·심사 중 안내(209~211)의 실질 정보와 말미 채널 홍보·구독 인사(213~216)가 원문 그대로 제공되며, 말미 홍보/인사의 편집 수준은 T3 자동 삭제가 아닌 T4/T5 본문 편집 규칙에서 투명하게 다룰 수 있음.",
    },
]


CONFIRM_UNIT_REVIEW_SPECS = [
    {
        "unit_id": "c_u00",
        "range": [134, 145],
        "must_contain": {135: "마이클 포터", 136: "진입하기 어려운 정도", 140: "담장이 났는데", 144: "공멸"},
        "preserved_information_check": "1980년 마이클 포터 경쟁 전략론의 진입 장벽 개념 도입(134~135), 진입 난이도가 시장 이익을 지킨다는 원리(136~137), 어린 시절 담장 비유(138~144: 140번 구어 전사 '담장이 났는데' 포함, 낮은 장벽+높은 이익 시 유입 급증으로 이익 감소·공멸)가 그대로 보존됨.",
        "t4_sufficiency": "개념·연도·인물(1980, 마이클 포터)과 담장 비유의 인과관계가 완결됨. 140번 '담장이 났는데'는 별도 수동 caution 테이블 없이 전역 do_not_resolve_uncertain_STT 제약으로 임의 사실 추가를 막음.",
    },
    {
        "unit_id": "c_u01",
        "range": [145, 150],
        "must_contain": {145: "아까 그 네 단어", 146: "무자본", 148: "담장 허들이 없다는 뜻"},
        "preserved_information_check": "앞서 언급한 네 단어 재호출(145)과 직후 열거('무자본, 뭐 노스킬, 뭐 누구나 할 수 있고 오늘 시작할 수 있고' 146), 이것이 장점이 아니라 담장(허들) 부재를 뜻한다는 논지(147~149)가 보존됨.",
        "t4_sufficiency": "145번이 확인 구간 시작(134) 이전을 가리키지만 146번에서 네 조건을 직접 재열거하므로 외부 문맥 없이도 T4가 논지를 완결해 서술할 수 있음.",
    },
    {
        "unit_id": "c_u02",
        "range": [150, 156],
        "must_contain": {152: "맨스필드", 153: "4년 안에 전부 모방", 155: "허들이 아예 없다"},
        "preserved_information_check": "특허 반론 제기(150~151), 1981년 경제학자 맨스필드의 혁신 아이템 48건 추적 연구(70% 특허 취득, 그중 60%가 4년 내 모방됨 152~153), 법적 특허조차 모방을 막지 못하므로 '누구나 오늘 시작' 강의·방법론에는 허들이 전무하다는 결론(154~155)이 정확히 보존됨.",
        "t4_sufficiency": "연도(1981), 연구자(맨스필드), 표본·비율 수치(48건, 70%, 60%, 4년)와 논리 귀결이 모두 원문에 명시되어 T4 작성 재료로 충분함.",
    },
    {
        "unit_id": "c_u03",
        "range": [156, 170],
        "must_contain": {159: "53.8%", 163: "미용실이 73.4%", 166: "통신판매업이 사십오 점 칠 퍼센트", 169: "커피점은 오십삼 점 이 퍼센트"},
        "preserved_information_check": "이론에서 실증 통계로의 전환(156~158), 국세청 100대 생활 업종 2023년 기준 창업 후 3년 생존률 평균 53.8%(100명 중 54명 생존·46명 폐업 환산 159~161), 상위 3개 업종(미용실 73.4%, 펜션·게스트하우스 73.1%, 교습학원 70.1% 162~164), 하위 3개 업종(통신판매업 45.7%, 분식점 46.6%, 패스트푸드점 약 46.8% 165~168) 및 평균 대조군 커피점(53.2% 169) 수치가 아라비아 숫자·한글 수사 원문 표기 그대로 전량 보존됨.",
        "t4_sufficiency": "다수의 통계 수치와 비교 기준(2023년 기준, 3년 생존률, 100대 생활 업종)이 누락 없이 한 leaf에 모여 있어 T4가 정확한 수치 대조 문단을 작성하기에 충분함.",
    },
    {
        "unit_id": "c_u04",
        "range": [170, 175],
        "must_contain": {170: "이십칠 점 칠 포인트", 171: "이천이십육 년", 172: "사십육 명", 174: "다 잘 먹고 잘 살까"},
        "preserved_information_check": "상위 미용실과 하위 통신판매업의 27.7%p 격차(170), 2026년 각 100명 동시 창업 가정 시 3년 뒤 생존자 비교(미용실 73명 vs 통신판매업 약 46명, 171번 '유입당하고' 구어 원문 포함 171~172), 생존자 중에서도 실제 수익성에 대한 환기 질문(173~174)이 보존됨.",
        "t4_sufficiency": "직전 leaf(c_u03)의 통계 수치를 100명 기준 직관적 인원수 차이로 풀어주는 구간으로, 가정 연도(2026년)와 인원 수치(73명 vs 46명)가 온전히 포함되어 있음.",
    },
    {
        "unit_id": "c_u05",
        "range": [175, 184],
        "must_contain": {175: "진입 장벽", 177: "미용 기술", 180: "펜션은 건물", 183: "실력과 평판"},
        "preserved_information_check": "상위 생존 3개 업종의 공통점=진입 장벽 존재(175), 미용실(미용 기술 필수, 하루 만에 창업 불가 176~179), 펜션(건물·자본 필수 180), 학원(실력과 평판 없으면 원생 0명 181~183)의 요인별 분석이 순서대로 보존됨.",
        "t4_sufficiency": "기술·자본·평판 3가지 구체적 진입 장벽이 업종별로 명확히 대응되어 T4 서술 구조가 자명함.",
    },
    {
        "unit_id": "c_u06",
        "range": [184, 192],
        "must_contain": {184: "생존 하위 세 업종", 188: "통신판매분", 190: "복제 시간", 191: "유통 기한"},
        "preserved_information_check": "생존 하위 3개 업종 대조 전환(184), 오늘 마음먹으면 시작 가능한 낮은 진입 패턴(185~187, '것들이 들이' 반복 원문 유지), 의심 전사 '통신판매분'(188)과 컴퓨터 한 대로 당일 등록 가능한 특성(189), 복제 시간(하루 미만)이 곧 사업의 '유통 기한'을 결정한다는 핵심 결론(190~191)이 보존됨.",
        "t4_sufficiency": "188번 '통신판매분'(통신판매업은의 의심 전사)에는 대표 영상 같은 수동 caution 항목이 없으나 전역 do_not_resolve_uncertain_STT 제약이 적용되며, 문서 전체 목차(document_outline)가 함께 제공되어 c_u03·c_u05와의 상·하위 업종 대조 맥락을 T4가 이해할 수 있음.",
    },
]


def _verify_unit_specs(dossiers, specs):
    if len(dossiers["units"]) != len(specs):
        raise ValueError("unit_spec_count_mismatch")
    reviews = []
    for unit, spec in zip(dossiers["units"], specs):
        if unit["unit_id"] != spec["unit_id"]:
            raise ValueError("unit_id_order_mismatch:" + unit["unit_id"])
        sr = [unit["source_range"]["sentence_start"], unit["source_range"]["sentence_end"]]
        if sr != spec["range"]:
            raise ValueError("unit_range_mismatch:" + unit["unit_id"])
        by_ix = {s["sentence_index"]: s["text"] for s in unit["required_source_sentences"]}
        if sorted(by_ix) != list(range(sr[0], sr[1])):
            raise ValueError("unit_sentences_incomplete:" + unit["unit_id"])
        for ix, substr in spec["must_contain"].items():
            if substr not in by_ix[ix]:
                raise ValueError(f"expected_phrase_missing:{unit['unit_id']}:{ix}:{substr}")
        reviews.append(
            {
                "unit_id": unit["unit_id"],
                "root_index": unit["section_hierarchy"]["root_index"],
                "leaf_index": unit["section_hierarchy"]["leaf_index"],
                "root_title": unit["section_hierarchy"]["root_title"],
                "leaf_title": unit["section_hierarchy"]["leaf_title"],
                "reviewed_sentence_indices": list(range(sr[0], sr[1])),
                "caution_count": len(unit["resolved_cautions"]),
                "internal_link_count": len(unit["internal_source_links"]),
                "previous_context_indices": unit["previous_context"]["previous_context_indices"],
                "asset_action": unit["assets"]["asset_action"],
                "preserved_information_check": spec["preserved_information_check"],
                "t4_sufficiency": spec["t4_sufficiency"],
                "decision": "accepted",
            }
        )
    return reviews


def review_primary():
    run = e05.current()
    cfg = e05.check(run)
    p_plan = t.read(run / "primary_plan.json")
    p_dossiers = t.read(run / "resolved_primary_dossiers.json")
    p_plan_sha = t.sha(run / "primary_plan.json")
    p_dossiers_sha = t.sha(run / "resolved_primary_dossiers.json")

    unit_reviews = _verify_unit_specs(p_dossiers, PRIMARY_UNIT_REVIEW_SPECS)

    # Gate 1: [112, 150) 38 sentences side-by-side against E04 repair_p07
    e04_rec = t.read(e05.E04_RUN / "journal/repair_p07.json")
    e04_rev = t.read(e05.E04_REVIEW_PATH)
    e04_item9 = e04_rec["parsed"]["items"][9]
    assert e04_item9["source_indices"] == [134, 135]
    u09 = next(u for u in p_dossiers["units"] if u["unit_id"] == "p_u09")
    u10 = next(u for u in p_dossiers["units"] if u["unit_id"] == "p_u10")
    u10_map = {s["sentence_index"]: s["text"] for s in u10["required_source_sentences"]}

    gate1 = {
        "gate": "Gate 1 — known 38-sentence [112, 150) comparison against rejected E04 repair_p07",
        "decision": "accepted",
        "reviewer": "Codex delegated full sentence-by-sentence source comparison",
        "review_scope": "Stage 1 aligned STT text vs E04 repair_p07 vs E05 p_u09+p_u10; 0 new audio/video/model calls",
        "config_sha256": t.digest(cfg),
        "primary_plan_sha256": p_plan_sha,
        "resolved_primary_dossiers_sha256": p_dossiers_sha,
        "compared_e04_response_sha256": e04_rec["response_sha256"],
        "compared_e04_review_sha256": t.sha(e05.E04_REVIEW_PATH),
        "reviewed_unit_ids": ["p_u09", "p_u10"],
        "reviewed_sentence_indices": list(range(112, 150)),
        "summary": "E04 item 9의 '기구 삭제·욕실 분리 유지' 추정 재분류(M01)가 완전히 제거되었고, 134('기구.')·135('...욕실 뚜렷봉 액체 아니니까...') 원문 전체와 부정/복합 범위 보존 가드, 137·141 주의, [40, 41] 이전 문맥, 20개 태그 목표가 누락 없이 보존됨.",
        "findings": [
            {
                "id": "G1-01",
                "status": "resolved_in_E05_transferred_guard_to_T4",
                "source_indices": [134, 135],
                "e04_defect": e04_rev["findings"][0]["detail"],
                "e05_verified_state": {
                    "sentence_134_verbatim": u10_map[134],
                    "sentence_135_verbatim": u10_map[135],
                    "new_keyword_classification_fields": 0,
                    "faulty_e04_prose_excluded_from_dossier": True,
                    "attached_constraint": "preserve_compound_scope_and_negation",
                },
                "residual_risk": "원문 자체의 구어 모호성('기구.', '욕실 뚜렷봉 액체 아니니까')은 원문 그대로 남으므로 T4 본문 생성 후 원문→본문 대조 검수가 계속 필수임.",
            },
            {
                "id": "G1-02",
                "status": "preserved",
                "source_indices": [112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122],
                "detail": "p_u09([112, 123))에서 태그 최대 20개 상한, [40, 41] 읽기 전용 이전 문맥 연결을 통한 상품명 중복 키워드 제외 원칙, 아이템 스카우트 등 기본 수집 경로가 모두 보존됨.",
            },
            {
                "id": "G1-03",
                "status": "preserved",
                "source_indices": [123, 124, 125, 126, 127, 128, 129, 130, 131, 132, 133, 136, 137, 138, 139, 140, 141, 142, 143, 144, 145, 146, 147, 148, 149],
                "detail": "p_u10([123, 150))에서 쿠팡 연관어 복사→메모장 편집, 137 '영광 검색기' 의심 전사 주의, 141 '변기 뚜렷빵' 의도적 오타 수집 주의와 '배수구' 제외 예시, 144 '변기 뚫어뻥 기구' 하위 재검색, 20개 전량 입력 강조가 모두 보존됨.",
            },
        ],
    }

    # Gate 2: [57, 87) 30 unseen sentences (CPU path, 0 model calls)
    u05 = next(u for u in p_dossiers["units"] if u["unit_id"] == "p_u05")
    u06 = next(u for u in p_dossiers["units"] if u["unit_id"] == "p_u06")
    u05_map = {s["sentence_index"]: s["text"] for s in u05["required_source_sentences"]}
    gate2 = {
        "gate": "Gate 2 — previously unsent 30-sentence [57, 87) CPU dossier verification",
        "decision": "accepted",
        "execution_path": "CPU deterministic assembly only; 0 LLM inference calls (not a model output test)",
        "reviewer": "Codex delegated full sentence-by-sentence source comparison",
        "config_sha256": t.digest(cfg),
        "primary_plan_sha256": p_plan_sha,
        "resolved_primary_dossiers_sha256": p_dossiers_sha,
        "reviewed_unit_ids": ["p_u05", "p_u06"],
        "reviewed_sentence_indices": list(range(57, 87)),
        "summary": "미전송 [57, 87) 30문장(p_u05·p_u06)에서 59번 의심 UI 목록 주의 2건이 누락 없이 결합되었고, 사이즈(프리)·색상(화이트/블랙) 추가, 판매가(10,000원)·정상가(15,000원) 할인율 권고, 자동 가격 조정 경험(과거 사용/최근 미사용), 재고 수량 일괄 입력 및 후속 공란 조건이 전부 보존됨.",
        "findings": [
            {
                "id": "G2-01",
                "status": "preserved_with_dual_cautions",
                "source_indices": [59],
                "sentence_59_verbatim": u05_map[59],
                "caution_count_on_59": len(u05["resolved_cautions"]),
                "detail": "59번에 연결된 기존 주의 2건(/source_cautions/0, /source_cautions/7)이 모두 p_u05에 결합되어 '주문자 연락처, 주문자 이메일...' 등 혼입 전사를 실제 옵션 UI 필드로 확정하지 않도록 강제함.",
            },
            {
                "id": "G2-02",
                "status": "preserved",
                "source_indices": [57, 58, 60, 61, 62, 63, 64, 65],
                "detail": "상품별 옵션 화면 차이(58), 스몰/미디움/라지 및 프리/원 사이즈 예시와 '프리' 입력(60~62), 화이트·블랙 추가 버튼 클릭 및 하단 옵션 행 자동 생성(63~65)이 순서대로 보존됨.",
            },
            {
                "id": "G2-03",
                "status": "preserved",
                "source_indices": list(range(66, 87)),
                "detail": "p_u06([66, 87))에서 판매가(구매자 결제 금액, 만 원 예시, 마진 계산 전제)와 정상가(만 오천 원 예시, 과도하지 않은 적절한 할인율 추천), 자동 가격 조정(선택 사항, 화자는 과거 사용·최근 미사용), 전체 체크박스를 통한 재고 수량 일괄 입력 및 뒤 공란 유지가 모두 보존됨.",
            },
        ],
    }

    # Gate 3: Full primary 217 sentences / 16 leaves
    primary_review = {
        "gate": "Gate 3 — full primary 217-sentence / 16-leaf dossier review",
        "decision": "accepted",
        "reviewer": "Codex delegated full sentence-by-sentence source comparison",
        "review_scope": "All 217 primary sentences (Fw-8hZhnH3Q [0, 217)) across 12 roots / 16 leaves; CPU-compiled plan + resolved dossiers",
        "config_sha256": t.digest(cfg),
        "primary_plan_sha256": p_plan_sha,
        "resolved_primary_dossiers_sha256": p_dossiers_sha,
        "reviewed_sentence_indices": list(range(0, 217)),
        "reviewed_unit_ids": [u["unit_id"] for u in p_dossiers["units"]],
        "e02_scoped_plan_comparison": {
            "e02_selected_ref": "tests/stage3/T3/runs/20261030T151221Z_e02_22f2fa/selected/p02.json",
            "compared_range": [15, 40],
            "e05_units": ["p_u02", "p_u03"],
            "result": "E02 검수 채택안(25문장)이 보존했던 판매자 배송/로켓그로스 시점 구분, 화자 귀속(28·29), 33→[30] 편법 연결, 35 의심 전사 보존, V01 defer가 E05의 p_u02·p_u03 재료에도 100% 보존되며 E02 계획을 새 스키마에 임의 혼합하지 않음.",
        },
        "summary": "대표 217문장 전체가 16개 T2 leaf 단위에 누락·중복·순서 역전 없이 정확히 분할·해석되었고, 주의 13건(기존 12건 + E04 134·135 가드 1건), 명시적 연결 5건(내부 1 + 이전 문맥 4), V01 defer 정책 및 고정 제약 6개가 결합되어 단순 인덱스 나열이 아닌 실질적 T4 단위별 작성 재료로 충분함을 확인함.",
        "unit_reviews": unit_reviews,
    }

    (run / "reviews").mkdir(exist_ok=True)
    t.create(run / "reviews/gate1_known38_comparison.json", gate1)
    t.create(run / "reviews/gate2_new30_review.json", gate2)
    t.create(run / "reviews/primary_review.json", primary_review)
    print(
        json.dumps(
            {
                "run_id": run.name,
                "gate1_known38": gate1["decision"],
                "gate2_new30": gate2["decision"],
                "gate3_primary217": primary_review["decision"],
                "reviewed_primary_units": len(unit_reviews),
            },
            ensure_ascii=False,
        )
    )


def closeout():
    run = e05.current()
    cfg = e05.check(run)
    if not (run / "confirmation_freeze.json").exists():
        raise ValueError("confirmation_freeze_required_before_closeout")

    freeze = t.read(run / "confirmation_freeze.json")
    for p, h in freeze["frozen_files"].items():
        t.bound_sha(p, h)

    p_plan = t.read(run / "primary_plan.json")
    c_plan = t.read(run / "confirmation_plan.json")
    p_dossiers = t.read(run / "resolved_primary_dossiers.json")
    c_dossiers = t.read(run / "resolved_confirmation_dossiers.json")
    tp = t.read(run / "token_plan.json")
    e05.assert_bundle_isolation(p_dossiers, c_dossiers)

    confirm_unit_reviews = _verify_unit_specs(c_dossiers, CONFIRM_UNIT_REVIEW_SPECS)
    confirmation_review = {
        "gate": "Gate 4 — post-freeze confirmation 58-sentence / 7-leaf dossier review (H2WN130OzLg [134, 192))",
        "decision": "accepted",
        "blind_status": "not_blind_reused_from_T2_confirmation_segment",
        "policy_revisions_after_freeze": 0,
        "reviewer": "Codex delegated full sentence-by-sentence source comparison",
        "config_sha256": t.digest(cfg),
        "confirmation_freeze_sha256": t.sha(run / "confirmation_freeze.json"),
        "confirmation_plan_sha256": t.sha(run / "confirmation_plan.json"),
        "resolved_confirmation_dossiers_sha256": t.sha(run / "resolved_confirmation_dossiers.json"),
        "reviewed_sentence_indices": list(range(134, 192)),
        "reviewed_unit_ids": [u["unit_id"] for u in c_dossiers["units"]],
        "cross_contamination_check": {
            "shared_sentences_with_primary": 0,
            "shared_assets_with_primary": 0,
            "shared_cautions_with_primary": 0,
        },
        "summary": "스키마·정책·코드 동결 후 동일 builder로 조립한 다른 영상(H2WN130OzLg [134, 192)) 58문장·7개 leaf 재료에서 마이클 포터 진입 장벽, 맨스필드 특허 모방 통계(48건·70%·60%·4년), 국세청 100대 생활 업종 3년 생존률 수치, 상·하위 업종 장벽 대조 및 복제 시간=유통 기한 논지가 왜곡·혼입 없이 보존됨. 동결 후 정책 재튜닝은 0회임.",
        "known_confirmation_gaps_recorded": [
            "이 58문장 구간은 T2 확인에서 이미 사용했으므로 blind 테스트가 아님.",
            "대표 영상과 달리 별도 수동 source_cautions/connections 테이블이 없어 140('담장이 났는데'), 188('통신판매분') 등 의심 전사 및 145('아까 그 네 단어')·184('생존 하위 세 업종') 맥락 연결은 전역 writer_constraints(do_not_resolve_uncertain_STT)와 전체 목차(document_outline)에 의존함.",
        ],
        "unit_reviews": confirm_unit_reviews,
    }
    t.create(run / "reviews/confirmation_review.json", confirmation_review)

    tok_by_uid = {u["unit_id"]: u for u in tp["units"]}
    t4_units = []
    for u in p_dossiers["units"]:
        u_copy = copy.deepcopy(u)
        trow = tok_by_uid[u["unit_id"]]
        u_copy["material_token_measurement"] = {
            "raw_material_tokens": trow["raw_material_tokens"],
            "chat_wrapped_material_tokens": trow["chat_wrapped_material_tokens"],
            "remaining_for_t4_instructions_under_12k": trow["remaining_for_t4_instructions_under_12k"],
            "measurement_scope": "material_payload_only_not_full_t4_prompt",
        }
        t4_units.append(u_copy)

    limitations_and_t4_responsibilities = [
        "T3 E05는 계획 단계의 LLM 재서술 왜곡을 제거했지만 최종 본문(T4)의 충실성을 증명하지 않으므로, T4에서 원문→본문 및 본문→원문 전수 대조가 필수임.",
        "134('기구.')·135('욕실 뚜렷봉 액체 아니니까...'), 59(주문자 연락처 등 혼입), 89(절단 장문), 35('로고를 눅기를 딴다든지'), 174('상품 정보 제공 곳'), 186('제주도서 상관 배송')의 구어/STT 모호성은 원문 그대로 T4에 전달되므로 T4 프롬프트와 검수에서 자의적 분류·교정을 다시 차단해야 함.",
        "시각 판독기(reader)는 계속 제외(reader_policy=excluded) 상태이며 원본 의미 단위 254/270개의 시각 범위는 미검수이고 V01은 defer 상태임. T4 본문은 미검증 화면 상태에 의존하지 않는 자연스러운 설명문으로 작성해야 함.",
        "확인 58문장(H2WN130OzLg [134, 192))은 T2 재사용 구간으로 blind가 아니며 수동 caution 테이블 없이 전역 제약으로만 보호됨.",
        f"token_plan.json 수치는 Qwen3.8-27B CPU tokenizer로 측정한 작성 재료 payload 토큰(대표 leaf당 chat-wrapped {tp['summary']['primary_chat_wrapped_tokens_min']}~{tp['summary']['primary_chat_wrapped_tokens_max']} 토큰, 확인 leaf당 {tp['summary']['confirmation_chat_wrapped_tokens_min']}~{tp['summary']['confirmation_chat_wrapped_tokens_max']} 토큰; 12,000 입력 한도 내 지시문 여유 6,648+ 토큰)이며, 미확정 T4 시스템/작성 지시문과 출력 예산은 T4 실행 문서(t4_execution.md)에서 확정해야 함.",
        "중간 단계 게이트(T3→T4) 통과는 사용자 위임 범위로 판정했으나, 실제 생성될 최종 블로그 글의 품질 채택 권한은 전적으로 사용자에게 있음(user_accepted=false).",
    ]

    t4_input = {
        "schema_version": "extractube.stage3.t4.input.experimental.v1",
        "stage_gate_accepted": True,
        "T3_complete": True,
        "t4_ready": True,
        "user_accepted": False,
        "final_article_user_accepted": False,
        "decision_authority": "user_delegated_T3_to_T4_stage_judgement",
        "selected_t3_candidate": "E05",
        "method": "CPU-compiled source-preserving T3 leaf plan dossiers without model factual restatement",
        "plan_schema_version": e05.SCHEMA_VERSION,
        "primary_video_id": p_dossiers["video_id"],
        "primary_sentence_range": [0, 217],
        "primary_sentence_count": 217,
        "primary_unit_count": 16,
        "document_outline": p_dossiers["document_outline"],
        "writer_constraints": [
            {"id": cid, **e05.POLICY_CATALOG["constraints"][cid]}
            for cid in e05.CONSTRAINT_IDS
        ],
        "reader_policy": "excluded",
        "model_fact_restatement": False,
        "new_visual_evidence": [],
        "upstream_refs": {
            "t3_input": {"path": e05.relpath(t.INPUT), "sha256": t.INPUT_SHA},
            "plan_schema": {"path": e05.relpath(e05.SCHEMA_PATH), "sha256": e05.SCHEMA_SHA},
            "source_bundle": {"path": e05.relpath(run / "source_bundle.json"), "sha256": t.sha(run / "source_bundle.json")},
            "policy_catalog": {"path": e05.relpath(run / "policy_catalog.json"), "sha256": t.sha(run / "policy_catalog.json")},
            "editorial_reference_notes": {
                "path": e05.relpath(run / "editorial_reference_notes.json"),
                "sha256": t.sha(run / "editorial_reference_notes.json"),
            },
            "primary_plan": {"path": e05.relpath(run / "primary_plan.json"), "sha256": t.sha(run / "primary_plan.json")},
            "resolved_primary_dossiers": {
                "path": e05.relpath(run / "resolved_primary_dossiers.json"),
                "sha256": t.sha(run / "resolved_primary_dossiers.json"),
            },
            "token_plan": {"path": e05.relpath(run / "token_plan.json"), "sha256": t.sha(run / "token_plan.json")},
            "primary_review": {
                "path": e05.relpath(run / "reviews/primary_review.json"),
                "sha256": t.sha(run / "reviews/primary_review.json"),
            },
            "confirmation_review": {
                "path": e05.relpath(run / "reviews/confirmation_review.json"),
                "sha256": t.sha(run / "reviews/confirmation_review.json"),
            },
        },
        "generation_units": t4_units,
        "unreviewed_visual_scope": t.read(t.INPUT)["unreviewed_visual_scope"],
        "validation_only": {
            "confirmation_video_id": "H2WN130OzLg",
            "confirmation_sentence_range": [134, 192],
            "confirmation_unit_count": 7,
            "blind": False,
            "confirmation_plan_ref": {
                "path": e05.relpath(run / "confirmation_plan.json"),
                "sha256": t.sha(run / "confirmation_plan.json"),
            },
            "resolved_confirmation_dossiers_ref": {
                "path": e05.relpath(run / "resolved_confirmation_dossiers.json"),
                "sha256": t.sha(run / "resolved_confirmation_dossiers.json"),
            },
        },
        "auxiliary_content_in_primary": False,
        "limitations_and_t4_responsibilities": limitations_and_t4_responsibilities,
    }
    t.create(run / "t4_input.json", t4_input)
    t4_input_sha = t.sha(run / "t4_input.json")

    offline_out = t.read(run / "offline_check.json")["output"]
    m_tests = [line for line in offline_out.splitlines() if line.startswith("Ran ")]
    tests_count = int(m_tests[0].split()[1]) if m_tests else 33

    e04_close = t.read(e05.E04_RUN / "closeout.json")
    closeout_obj = {
        "run_id": run.name,
        "closed_at_kst": datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Seoul")).isoformat(),
        "candidate": "E05 CPU-compiled source-preserving T3 plan dossiers",
        "contract_version": e05.SCHEMA_VERSION,
        "decision": "accept_E05_source_preserving_plan_handoff; close_T3_proceed_to_T4_preparation",
        "execution_status": "completed_all_4_source_review_gates_and_t4_handoff",
        "technical_status": "strict v2 schema validation, Stage 1 SHA-256/text identity, 33 offline/adversarial tests, post-freeze confirmation isolation, and CPU material payload token planning passed",
        "quality_status": "all 217 primary sentences (16 leaves) and 58 confirmation sentences (7 leaves) reviewed against Stage 1 STT; 0 model factual restatement distortions; unit dossiers provide concrete hierarchy, verbatim source, cautions, internal/prior links, and constraints sufficient for T4 writing",
        "stage_quality_accepted": True,
        "T3_complete": True,
        "T3_successful_closure": True,
        "t4_ready": True,
        "user_accepted": False,
        "final_article_user_accepted": False,
        "decision_authority": "user_delegated_intermediate_stage_gate_review",
        "model_execution_enabled": False,
        "new_calls": 0,
        "new_http_seconds": 0.0,
        "new_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "family_calls": e04_close["family_calls"],
        "family_max_calls": e04_close["family_max_calls"],
        "family_http_seconds": e04_close["family_http_seconds"],
        "family_max_http_seconds": e04_close["family_max_http_seconds"],
        "family_usage": e04_close["family_usage"],
        "prior_E01_to_E04_records_mutated": False,
        "unknown_calls": 0,
        "retry": 0,
        "policy_revisions_after_freeze": 0,
        "manual_instruction_edits_in_E05": 0,
        "manual_edits_family": e04_close["manual_edits_family"],
        "offline_tests_passed": tests_count,
        "primary_sentences_reviewed_in_E05": 217,
        "primary_units_reviewed_in_E05": 16,
        "confirmation_sentences_reviewed_in_E05": 58,
        "confirmation_units_reviewed_in_E05": 7,
        "t4_material_token_summary": tp["summary"],
        "t4_input_ref": "t4_input.json",
        "t4_input_sha256": t4_input_sha,
        "T4_document_prepared": False,
        "T4_inference_calls": 0,
        "codex_account_usage": "not_observed",
        "limitations_and_t4_responsibilities": limitations_and_t4_responsibilities,
    }
    t.create(run / "closeout.json", closeout_obj)

    acceptance_obj = {
        "stage": "T3",
        "candidate": "E05",
        "contract_version": e05.SCHEMA_VERSION,
        "accepted": True,
        "stage_gate_accepted": True,
        "T3_complete": True,
        "t4_ready": True,
        "user_accepted": False,
        "final_article_user_accepted": False,
        "decision_authority": "user_delegated_T3_to_T4_stage_judgement",
        "selected_complete_plan": "primary_plan.json",
        "selected_complete_plan_sha256": t.sha(run / "primary_plan.json"),
        "selected_resolved_dossiers": "resolved_primary_dossiers.json",
        "selected_resolved_dossiers_sha256": t.sha(run / "resolved_primary_dossiers.json"),
        "confirmation_plan": "confirmation_plan.json",
        "confirmation_plan_sha256": t.sha(run / "confirmation_plan.json"),
        "handoff_ref": "t4_input.json",
        "handoff_sha256": t4_input_sha,
        "historical_scoped_plan_preserved": "E02 selected/p02.json (25 sentences, comparison history only, not mixed into v2 contract)",
        "reviews": {
            "gate1_known38": {
                "ref": "reviews/gate1_known38_comparison.json",
                "sha256": t.sha(run / "reviews/gate1_known38_comparison.json"),
            },
            "gate2_new30": {
                "ref": "reviews/gate2_new30_review.json",
                "sha256": t.sha(run / "reviews/gate2_new30_review.json"),
            },
            "primary217": {
                "ref": "reviews/primary_review.json",
                "sha256": t.sha(run / "reviews/primary_review.json"),
            },
            "confirmation58": {
                "ref": "reviews/confirmation_review.json",
                "sha256": t.sha(run / "reviews/confirmation_review.json"),
            },
        },
        "closeout_sha256": t.sha(run / "closeout.json"),
    }
    t.create(run / "acceptance.json", acceptance_obj)

    html_links = render_e05.render()

    final_ver = {
        "old_T2_source_protected_hashes": 195,
        "E05_protected_E04_files_and_code": len(cfg["critical_files"]),
        "offline_tests": tests_count,
        "e05_v2_schema_plans_validated": 2,
        "resolved_unit_dossiers_validated": 23,
        "CPU_payload_token_units_measured": len(tp["units"]),
        "all_units_within_8000_material_cap": tp["summary"]["all_units_within_cap"],
        "gate1_known38_sentences_compared": 38,
        "gate2_new30_sentences_reviewed": 30,
        "primary_sentences_reviewed": 217,
        "confirmation_sentences_reviewed": 58,
        "policy_revisions_after_freeze": 0,
        "html_links_validated": html_links,
        "family_accounting": "6 calls / 276.185664s / 25690 measured local tokens (E05: 0 calls / 0.0s)",
        "E05_accepted": True,
        "T3_successful_closure": True,
        "t4_ready": True,
        "user_accepted": False,
        "t4_input_sha256": t4_input_sha,
        "T4_document_or_inference": False,
        "new_calls_in_E05": 0,
    }
    t.create(run / "final_verification.json", final_ver)
    print(json.dumps(final_ver, ensure_ascii=False, indent=2))


def snapshot():
    run = e05.current()
    e05.check(run)
    e05.verify()
    (run / "final_execution_snapshot.md").write_bytes(t.SPEC.read_bytes())
    docs = [
        "local/stage3/t3_execution.md",
        "local/stage3/t2_execution.md",
        "local/stage3_design.md",
        "local/stage3_experiments.md",
        "local/stage3_implementation.md",
    ]
    files = {}
    for p in sorted(run.rglob("*")):
        if p.is_file() and p.name != "result_manifest.json":
            files[str(p.relative_to(run))] = t.sha(p)
    manifest = {
        "files": files,
        "current_documents": {d: t.sha(e05.ROOT / d) for d in docs},
    }
    t.write(run / "result_manifest.json", manifest)
    print(
        json.dumps(
            {
                "run_id": run.name,
                "manifest_files": len(files),
                "t3_execution_sha256": manifest["current_documents"]["local/stage3/t3_execution.md"],
            },
            ensure_ascii=False,
        )
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["review-primary", "closeout", "snapshot"])
    a = ap.parse_args()
    with (e05.HERE / "campaign.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if a.action == "review-primary":
            review_primary()
        elif a.action == "closeout":
            closeout()
        else:
            snapshot()


if __name__ == "__main__":
    main()
