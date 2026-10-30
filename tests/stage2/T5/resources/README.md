# T5 기존 환경과 의존 자료

- `tools/`: 기존 CPU OCR Python 환경과 Hugging Face 다운로드 캐시.
- `vendor/`: 기존 KaTeX 렌더링·OCR 모델 자료.

폴더 위치만 옮겼고 파일 bytes, 내부 심볼릭 링크, 환경의 shebang·설정은 바꾸지 않았다. 옛 `tests/T5/tools`, `tests/T5/vendor` 위치의 호환 링크를 통해 기존 환경과 과거 HTML을 계속 사용할 수 있다. 모델 설치·추론·서버 변경은 수행하지 않았다.

현재 제품 T5는 이 환경을 import하지 않는다. 제품 실행과 검토 방법은 [T5 README](../README.md)를 따른다.
