"""HTML summary and full source-to-plan dossier comparison renderer for E05 (0 inference calls)."""
import html
import json
import re

import run_t3 as t
import run_e05_plan as e05


def render():
    run = e05.current()
    esc = lambda x: html.escape(str(x), quote=True)
    css = (
        "body{font:16px/1.65 system-ui,sans-serif;color:#1e293b;max-width:1380px;margin:28px auto;padding:0 18px}"
        "h1,h2,h3{line-height:1.3}"
        ".note{background:#f1f5f9;padding:16px;border-radius:8px;border-left:5px solid #0369a1;margin:14px 0}"
        ".warn{background:#fffbeb;padding:14px 16px;border-radius:8px;border-left:5px solid #b45309;margin:14px 0}"
        ".ok{background:#f0fdf4;padding:14px 16px;border-radius:8px;border-left:5px solid #15803d;margin:14px 0}"
        ".unit{border:1px solid #cbd5e1;border-radius:8px;padding:16px;margin:18px 0;background:#ffffff}"
        ".pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;border-top:1px solid #e2e8f0;padding:14px 0}"
        ".three{grid-template-columns:repeat(3,minmax(0,1fr))}"
        ".idx{font-weight:700;color:#334155;font-size:14px}"
        ".tag{display:inline-block;background:#e2e8f0;color:#1e293b;border-radius:4px;padding:2px 8px;font-size:13px;margin-right:6px}"
        ".caution{background:#fef2f2;border-left:4px solid #dc2626;padding:8px 12px;margin:8px 0;font-size:14px}"
        "table{border-collapse:collapse;width:100%;margin:12px 0}"
        "th,td{padding:8px 10px;border:1px solid #cbd5e1;text-align:left;vertical-align:top}"
        "th{background:#f8fafc}"
        "code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}"
        "a{color:#0369a1}"
        "@media(max-width:820px){.pair,.three{display:block}.pair>div{margin-bottom:14px}body{font-size:15px}}"
    )

    def page(title, body):
        return (
            '<!doctype html><html lang="ko"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>" + esc(title) + "</title><style>" + css + "</style><body>" + body + "</body></html>"
        )

    close = t.read(run / "closeout.json")
    acc = t.read(run / "acceptance.json")
    tp = t.read(run / "token_plan.json")
    p_dossiers = t.read(run / "resolved_primary_dossiers.json")
    c_dossiers = t.read(run / "resolved_confirmation_dossiers.json")
    g1 = t.read(run / "reviews/gate1_known38_comparison.json")
    g2 = t.read(run / "reviews/gate2_new30_review.json")
    p_rev = t.read(run / "reviews/primary_review.json")
    c_rev = t.read(run / "reviews/confirmation_review.json")

    tok_by_uid = {u["unit_id"]: u for u in tp["units"]}

    # Build result.html
    summary = (
        "<h1>T3 E05 결과 — 원문 보존형 CPU 계획 인계 채택 및 T3 마감</h1>"
        '<div class="ok"><b>위임된 중간 단계 판정:</b> <code>stage_quality_accepted=true</code> · '
        "<code>T3_complete=true</code> · <code>t4_ready=true</code> · "
        "<code>user_accepted=false</code> (최종 블로그 글 품질 채택 아님 · T4 추론 0회)</div>"
        '<p class="note">E05는 T3에서 모델이 원문 사실을 다시 요약·분류하는 <code>instruction</code> 생성을 제거하고, '
        "채택된 T2 계층 구조(대표 12 root / 16 leaf · 확인 4 root / 7 leaf) 위에 불변 원문 전체, 기존 주의(caution), "
        "단위 내부/이전 문맥 링크, 시각 자산 유보 정책(<code>V01 defer</code> · <code>reader_policy=excluded</code>), "
        "고정 작성 제약 6개를 CPU에서 결정론적으로 조립·해석한 계획 인계입니다.</p>"
        "<p>run <code>" + esc(run.name) + "</code> · schema <code>" + esc(e05.SCHEMA_VERSION) + "</code></p>"
        "<p>"
        '<a href="review.html">원문 ↔ E04 실패안 ↔ E05 작성 재료 전체 대조</a> · '
        '<a href="t4_input.json">T4 고정 인계 (t4_input.json)</a> · '
        '<a href="primary_plan.json">대표 계획</a> · '
        '<a href="resolved_primary_dossiers.json">대표 해석 재료 (16 leaf)</a> · '
        '<a href="confirmation_plan.json">확인 계획</a> · '
        '<a href="resolved_confirmation_dossiers.json">확인 해석 재료 (7 leaf)</a> · '
        '<a href="token_plan.json">CPU 재료 토큰 측정</a> · '
        '<a href="closeout.json">마감 기록</a> · '
        '<a href="acceptance.json">단계 판정</a>'
        "</p>"
    )

    summary += (
        "<h2>1. 비용·회계 및 불변성 검증</h2>"
        "<ul>"
        "<li><b>E05 신규 모델 추론:</b> <code>0</code>회 · HTTP <code>0.0</code>초 (<code>model_execution_enabled=false</code>)</li>"
        "<li><b>T3 계열 누적(E01~E05):</b> <code>"
        + esc(close["family_calls"])
        + "/"
        + esc(close["family_max_calls"])
        + "</code> 요청 · HTTP <code>"
        + esc(close["family_http_seconds"])
        + "/"
        + esc(close["family_max_http_seconds"])
        + "</code>초 · 로컬 모델 토큰 <code>"
        + esc(close["family_usage"]["total_tokens"])
        + "</code> (과거 E01~E04 실패·미통과 기록 변경 0건)</li>"
        "<li><b>오프라인 계약·적대적 변조 테스트:</b> <code>"
        + esc(close["offline_tests_passed"])
        + "</code>개 전체 통과 (잘못된 hash, 빈/중복 범위, 미래 문맥 누수, 주의 누락, 잘못된 정책 ID, 가짜 시각 근거, 반환 텍스트 변조, 134·135 절단/오분류 유입 차단)</li>"
        "<li><b>CPU 작성 재료 토큰 측정(Qwen3.8-27B tokenizer, 가중치 미로드):</b> 대표 16개 단위 chat-wrapped 재료 <code>"
        + esc(tp["summary"]["primary_chat_wrapped_tokens_min"])
        + "~"
        + esc(tp["summary"]["primary_chat_wrapped_tokens_max"])
        + "</code> 토큰, 확인 7개 단위 <code>"
        + esc(tp["summary"]["confirmation_chat_wrapped_tokens_min"])
        + "~"
        + esc(tp["summary"]["confirmation_chat_wrapped_tokens_max"])
        + "</code> 토큰 (모두 상한 <code>"
        + esc(tp["unit_material_payload_cap_tokens"])
        + "</code> 이하, 12,000 입력 한도 내 지시문 여유 <code>4,000+</code> 토큰 확보; 미확정 T4 전체 프롬프트 토큰이 아님)</li>"
        "<li><b>동결 규율:</b> 대표 검수 후 <a href=\"confirmation_freeze.json\">confirmation_freeze.json</a> 동결 → 확인 58문장 별도 조립·검수 → 동결 후 정책/스키마/코드 수정 <code>0</code>회</li>"
        "</ul>"
    )

    summary += (
        "<h2>2. 4단계 원문 검수 요약</h2>"
        "<table><tr><th>게이트</th><th>대상 범위</th><th>단위</th><th>판정</th><th>핵심 검수 결과</th><th>근거 JSON</th></tr>"
        "<tr><td>Gate 1 (기존 실패 구간 대조)</td><td>Fw-8hZhnH3Q [112, 150) · 38문장</td><td>p_u09, p_u10</td><td>"
        + esc(g1["decision"])
        + "</td><td>"
        + esc(g1["summary"])
        + '</td><td><a href="reviews/gate1_known38_comparison.json">gate1_known38_comparison.json</a></td></tr>'
        "<tr><td>Gate 2 (미전송 새 구간 대조)</td><td>Fw-8hZhnH3Q [57, 87) · 30문장</td><td>p_u05, p_u06</td><td>"
        + esc(g2["decision"])
        + "</td><td>"
        + esc(g2["summary"])
        + '</td><td><a href="reviews/gate2_new30_review.json">gate2_new30_review.json</a></td></tr>'
        "<tr><td>Gate 3 (대표 전체 검수)</td><td>Fw-8hZhnH3Q [0, 217) · 217문장</td><td>16 leaves (12 roots)</td><td>"
        + esc(p_rev["decision"])
        + "</td><td>"
        + esc(p_rev["summary"])
        + '</td><td><a href="reviews/primary_review.json">primary_review.json</a></td></tr>'
        "<tr><td>Gate 4 (동결 후 다른 영상 확인)</td><td>H2WN130OzLg [134, 192) · 58문장</td><td>7 leaves (4 roots)</td><td>"
        + esc(c_rev["decision"])
        + "</td><td>"
        + esc(c_rev["summary"])
        + '</td><td><a href="reviews/confirmation_review.json">confirmation_review.json</a></td></tr>'
        "</table>"
    )

    summary += (
        "<h2>3. 대표 16개 작성 단위(Leaf Dossier) 개요</h2>"
        "<table><tr><th>Unit</th><th>Root / Leaf 제목</th><th>원문 범위 (문장 수)</th><th>주의(Caution)</th><th>내부/이전 문맥</th><th>자산</th><th>재료 토큰 (wrapped)</th></tr>"
    )
    for u in p_dossiers["units"]:
        sh = u["section_hierarchy"]
        sr = u["source_range"]
        cnt = sr["sentence_end"] - sr["sentence_start"]
        c_list = [str(c["sentence_indices"]) for c in u["resolved_cautions"]]
        int_list = [f"{x['from_sentence_index']}→{x['to_sentence_indices']}" for x in u["internal_source_links"]]
        prev_list = u["previous_context"]["previous_context_indices"]
        tok = tok_by_uid[u["unit_id"]]["chat_wrapped_material_tokens"]
        title_disp = sh["root_title"] if sh["child_index_in_root"] is None else (sh["root_title"] + " / " + sh["leaf_title"])
        summary += (
            "<tr><td><code>" + esc(u["unit_id"]) + "</code></td>"
            "<td>" + esc(title_disp) + "</td>"
            "<td><code>[" + str(sr["sentence_start"]) + ", " + str(sr["sentence_end"]) + ")</code> (" + str(cnt) + ")</td>"
            "<td>" + esc(", ".join(c_list) if c_list else "없음") + "</td>"
            "<td>" + esc(("내부 " + ", ".join(int_list) + " " if int_list else "") + ("이전 " + str(prev_list) if prev_list else ("없음" if not int_list else ""))) + "</td>"
            "<td><code>" + esc(u["assets"]["asset_action"]) + (" (" + ",".join(u["assets"]["asset_ids"]) + ")" if u["assets"]["asset_ids"] else "") + "</code></td>"
            "<td><code>" + str(tok) + "</code></td></tr>"
        )
    summary += "</table>"

    summary += (
        "<h2>4. 확인 영상 7개 작성 단위(Leaf Dossier) 개요 (검증 전용 · 대표 본문 미혼입)</h2>"
        "<table><tr><th>Unit</th><th>Root / Leaf 제목</th><th>원문 범위 (문장 수)</th><th>자산</th><th>재료 토큰 (wrapped)</th></tr>"
    )
    for u in c_dossiers["units"]:
        sh = u["section_hierarchy"]
        sr = u["source_range"]
        cnt = sr["sentence_end"] - sr["sentence_start"]
        tok = tok_by_uid[u["unit_id"]]["chat_wrapped_material_tokens"]
        title_disp = sh["root_title"] if sh["child_index_in_root"] is None else (sh["root_title"] + " / " + sh["leaf_title"])
        summary += (
            "<tr><td><code>" + esc(u["unit_id"]) + "</code></td>"
            "<td>" + esc(title_disp) + "</td>"
            "<td><code>[" + str(sr["sentence_start"]) + ", " + str(sr["sentence_end"]) + ")</code> (" + str(cnt) + ")</td>"
            "<td><code>" + esc(u["assets"]["asset_action"]) + "</code></td>"
            "<td><code>" + str(tok) + "</code></td></tr>"
        )
    summary += "</table>"

    summary += (
        "<h2>5. T4 이관 책임 및 남은 한계 (숨김 없이 기록)</h2>"
        '<div class="warn"><ul>'
        + "".join("<li>" + esc(lim) + "</li>" for lim in close["limitations_and_t4_responsibilities"])
        + "</ul></div>"
        "<p>판정 요약 JSON: <code>" + esc(json.dumps(acc, ensure_ascii=False)) + "</code></p>"
    )

    # Build review.html
    e04_parsed = t.read(e05.E04_RUN / "journal/repair_p07.json")["parsed"]
    detail = (
        "<h1>T3 E05 원문 ↔ 계획 재료 전체 대조 검수</h1>"
        '<p class="note">고정 Stage 1 정렬 STT 원문과 E05 해석 재료(대표 217문장/16단위 + 확인 58문장/7단위) 전체를 대조한 기록입니다. '
        "신규 음성·화면·LLM 추론은 0회입니다. <a href=\"result.html\">결과 요약으로 돌아가기</a></p>"
    )

    detail += (
        "<h2>Gate 1 집중 대조: [112, 150) 38문장 — E04 기각 응답 vs E05 원문 보존 재료</h2>"
        "<p>E04(<code>repair_p07</code>)는 134·135 원문을 그대로 인용한 직후에도 <code>기구</code>를 삭제 확정하고 "
        "<code>욕실 뚜렷봉 액체 아니니까</code>에서 <code>욕실</code>만 떼어 유지 대상으로 재분류해 기각되었습니다. "
        "E05(<code>p_u09</code>, <code>p_u10</code>)는 새 사실 분류 필드를 제거하고 원문 전체와 "
        "<code>preserve_compound_scope_and_negation</code> 제약을 그대로 T4에 인계합니다.</p>"
    )
    p_units_g1 = [u for u in p_dossiers["units"] if u["unit_id"] in ("p_u09", "p_u10")]
    for u in p_units_g1:
        cautions_by_s = {}
        for c in u["resolved_cautions"]:
            for ix in c["sentence_indices"]:
                cautions_by_s.setdefault(ix, []).append(c)
        for s in u["required_source_sentences"]:
            ix = s["sentence_index"]
            e04_items = [
                (i, it) for i, it in enumerate(e04_parsed["items"]) if ix in it["source_indices"]
            ]
            detail += '<section class="pair three">'
            detail += (
                '<div><span class="idx">원문 ' + str(ix) + " (" + esc(s["audio_start"]) + "~" + esc(s["audio_end"]) + "s)</span>"
                "<p>" + esc(s["text"]) + "</p></div>"
            )
            detail += "<div><b>E04 기각 계획 (비교 이력 · 미채택)</b>"
            for i, it in e04_items:
                detail += '<p class="idx">item ' + str(i) + " " + esc(it["source_indices"]) + "</p><p>" + esc(it["instruction"]) + "</p>"
            detail += "</div>"
            detail += (
                "<div><b>E05 채택 재료 (<code>" + esc(u["unit_id"]) + "</code> · " + esc(u["section_hierarchy"]["leaf_title"]) + ")</b>"
                "<p><span class=\"tag\">" + esc(u["required_source_policy"]) + "</span>"
                "<span class=\"tag\">" + esc(u["omission_policy"]) + "</span></p>"
                "<p>제공 원문: <code>" + esc(s["text"]) + "</code> (재서술 0건)</p>"
            )
            if ix in (116, 117) and u["previous_context"]["previous_context_indices"]:
                detail += "<p>읽기 전용 이전 문맥 연결: <code>" + esc(u["previous_context"]["previous_context_indices"]) + "</code></p>"
            for c in cautions_by_s.get(ix, []):
                detail += '<div class="caution"><b>주의 (' + esc(c["kind"]) + "):</b> " + esc(c["guidance"]) + "</div>"
            detail += "</div></section>"

    detail += (
        "<h2>Gate 2 집중 검수: 미전송 [57, 87) 30문장 (<code>p_u05</code> · <code>p_u06</code>, CPU 조립 · 모델 호출 0회)</h2>"
        "<p>59번의 의심 UI 필드 나열 주의 2건과 사이즈/색상 옵션, 판매가/정상가 할인율, 자동 가격 조정, 재고 일괄 입력 및 뒷부분 공란 조건이 "
        "모델 추측 교정 없이 원문 그대로 보존됨을 확인했습니다.</p>"
    )

    p_u_reviews = {r["unit_id"]: r for r in p_rev["unit_reviews"]}
    detail += "<h2>Gate 3: 대표 영상 전체 16개 작성 단위 (217문장 전수 대조)</h2>"
    for u in p_dossiers["units"]:
        uid = u["unit_id"]
        sh = u["section_hierarchy"]
        sr = u["source_range"]
        urev = p_u_reviews[uid]
        tok = tok_by_uid[uid]
        detail += (
            '<section class="unit"><h3><code>' + esc(uid) + "</code> · Root " + str(sh["root_index"]) + ": "
            + esc(sh["root_title"])
            + ("" if sh["child_index_in_root"] is None else " → Leaf: " + esc(sh["leaf_title"]))
            + " <code>[" + str(sr["sentence_start"]) + ", " + str(sr["sentence_end"]) + ")</code></h3>"
            "<p><span class=\"tag\">문장 " + str(len(u["required_source_sentences"])) + "개</span>"
            "<span class=\"tag\">wrapped 재료 토큰 " + str(tok["chat_wrapped_material_tokens"]) + "</span>"
            "<span class=\"tag\">자산 " + esc(u["assets"]["asset_action"]) + (" " + ",".join(u["assets"]["asset_ids"]) if u["assets"]["asset_ids"] else "") + "</span></p>"
            "<p><b>검수 확인:</b> " + esc(urev["preserved_information_check"]) + "</p>"
            "<p><b>T4 작성 충분성 판정:</b> " + esc(urev["t4_sufficiency"]) + "</p>"
        )
        if u["previous_context"]["sentences"]:
            detail += "<p><b>읽기 전용 이전 참고 원문 (중복 작성 금지):</b></p><ul>"
            for ps in u["previous_context"]["sentences"]:
                detail += "<li><code>[" + str(ps["sentence_index"]) + "]</code> " + esc(ps["text"]) + "</li>"
            detail += "</ul>"
        if u["internal_source_links"]:
            detail += "<p><b>단위 내부 재참조 링크:</b> <code>" + esc(json.dumps(u["internal_source_links"], ensure_ascii=False)) + "</code></p>"
        for c in u["resolved_cautions"]:
            detail += '<div class="caution"><b>주의 ' + esc(c["sentence_indices"]) + " (" + esc(c["kind"]) + "):</b> " + esc(c["guidance"]) + "</div>"
        detail += "<table><tr><th>Index</th><th>시각(s)</th><th>배정 원문 (verbatim)</th></tr>"
        for s in u["required_source_sentences"]:
            detail += (
                "<tr><td><code>" + str(s["sentence_index"]) + "</code></td>"
                "<td><code>" + esc(s["audio_start"]) + "~" + esc(s["audio_end"]) + "</code></td>"
                "<td>" + esc(s["text"]) + "</td></tr>"
            )
        detail += "</table></section>"

    c_u_reviews = {r["unit_id"]: r for r in c_rev["unit_reviews"]}
    detail += "<h2>Gate 4: 동결 후 확인 영상 7개 작성 단위 (H2WN130OzLg [134, 192), 58문장 전수 대조)</h2>"
    for u in c_dossiers["units"]:
        uid = u["unit_id"]
        sh = u["section_hierarchy"]
        sr = u["source_range"]
        urev = c_u_reviews[uid]
        tok = tok_by_uid[uid]
        detail += (
            '<section class="unit"><h3><code>' + esc(uid) + "</code> · Root " + str(sh["root_index"]) + ": "
            + esc(sh["root_title"])
            + ("" if sh["child_index_in_root"] is None else " → Leaf: " + esc(sh["leaf_title"]))
            + " <code>[" + str(sr["sentence_start"]) + ", " + str(sr["sentence_end"]) + ")</code></h3>"
            "<p><span class=\"tag\">문장 " + str(len(u["required_source_sentences"])) + "개</span>"
            "<span class=\"tag\">wrapped 재료 토큰 " + str(tok["chat_wrapped_material_tokens"]) + "</span>"
            "<span class=\"tag\">자산 " + esc(u["assets"]["asset_action"]) + "</span></p>"
            "<p><b>검수 확인:</b> " + esc(urev["preserved_information_check"]) + "</p>"
            "<p><b>T4 작성 충분성 및 잔여 주의:</b> " + esc(urev["t4_sufficiency"]) + "</p>"
            "<table><tr><th>Index</th><th>시각(s)</th><th>배정 원문 (verbatim)</th></tr>"
        )
        for s in u["required_source_sentences"]:
            detail += (
                "<tr><td><code>" + str(s["sentence_index"]) + "</code></td>"
                "<td><code>" + esc(s["audio_start"]) + "~" + esc(s["audio_end"]) + "</code></td>"
                "<td>" + esc(s["text"]) + "</td></tr>"
            )
        detail += "</table></section>"

    detail += (
        '<p><a href="result.html">결과 요약</a> · '
        '<a href="editorial_reference_notes.json">비삭제 편집 참고 기록</a> · '
        '<a href="policy_catalog.json">고정 작성 제약 카탈로그</a> · '
        '<a href="offline_check.json">오프라인 테스트 로그</a></p>'
    )

    (run / "result.html").write_text(page("ExtracTube T3 E05 결과 · T3 마감 및 T4 인계", summary), encoding="utf-8")
    (run / "review.html").write_text(page("ExtracTube T3 E05 원문 대조 검수", detail), encoding="utf-8")

    validated_links = 0
    for html_path in [run / "result.html", run / "review.html"]:
        for href in re.findall(r'href="([^"]+)"', html_path.read_text(encoding="utf-8")):
            if not (html_path.parent / href).exists():
                raise ValueError(f"broken_html_link:{html_path.name}:{href}")
            validated_links += 1

    print(json.dumps({"rendered": str(run), "validated_html_links": validated_links}, ensure_ascii=False))
    return validated_links


if __name__ == "__main__":
    render()
