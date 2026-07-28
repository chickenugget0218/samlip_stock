# -*- coding: utf-8 -*-
"""
삼립 무인편의점 재고·발주 (클라우드 버전) — UI 진입점
- 백엔드(DB·계산 로직)는 core.py 에 분리되어 있습니다. 두 파일을 함께 배포하세요.
- Secrets: DB_URL, APP_PASSWORD
"""
import os
import pandas as pd
import streamlit as st

st.set_page_config(page_title="삼립 무인편의점 재고·발주", page_icon="📦", layout="wide")


def get_secret(key: str, default: str = "") -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)


# ──────────────────────────────────────────────
# 비밀번호 게이트
# ──────────────────────────────────────────────
def check_password() -> bool:
    if st.session_state.get("authenticated"):
        return True
    st.title("🔒 삼립 무인편의점 재고·발주")
    st.caption("접속 비밀번호를 입력하세요.")
    with st.form("login"):
        pw = st.text_input("비밀번호", type="password")
        ok = st.form_submit_button("로그인", use_container_width=True, type="primary")
    if ok:
        if pw and pw == get_secret("APP_PASSWORD"):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    return False


if not check_password():
    st.stop()

# ── 로그인 후에만 DB 연결·백엔드 로딩 (core.py) ──
try:
    import core as _core
except Exception as _e:
    import traceback as _tb
    st.error("⚠️ core.py 를 불러오지 못했습니다. app.py 와 core.py 를 **함께** 최신으로 올렸는지 확인하세요.")
    st.code(f"{type(_e).__name__}: {_e}\n\n{_tb.format_exc()}")
    st.stop()

# core의 공개 이름(밑줄 없는 것)을 현재 전역으로 가져오기
_g = globals()
for _name in dir(_core):
    if not _name.startswith("_"):
        _g[_name] = getattr(_core, _name)

# 버전 확인 (구버전 core 감지) — collect 옵션까지 확인
import inspect as _inspect
_core_ok = (hasattr(_core, "LOGIC_VERSION") and hasattr(_core, "fmt_stock_ea")
            and hasattr(_core, "lot_add")
            and "collect" in _inspect.signature(_core.lot_add).parameters)
if not _core_ok:
    st.error("⚠️ core.py 가 예전 버전입니다. **app.py 와 core.py 를 반드시 함께** 최신으로 올린 뒤 "
             "⋮ → Reboot app 하세요. (한쪽만 올리면 이 오류가 납니다)")
    st.info(f"현재 불러온 core.py 위치: {getattr(_core, '__file__', '알 수 없음')}")
    st.stop()

if not st.session_state.get("snapshot_done"):
    import time as _time
    _t0 = _time.time()
    migrate_to_lots_once()   # 기존 재고/거래 → 로트 1회 이관
    _t1 = _time.time()
    snapshot_today()
    _t2 = _time.time()
    # DB 왕복 1회 순수 측정 (제품 조회)
    _t3 = _time.time()
    try:
        _ = df_products()
    except Exception:
        pass
    _t4 = _time.time()
    st.session_state["_perf_first_load"] = {
        "마이그레이션(1회성)": round(_t1 - _t0, 2),
        "스냅샷저장": round(_t2 - _t1, 2),
        "제품조회(DB왕복1회)": round(_t4 - _t3, 3),
    }
    st.session_state["snapshot_done"] = True

# ── 성능 측정 패널 (사이드바) ──
_PERF_ON = st.query_params.get("perf") == "1"

@st.dialog("📆 날짜별 기록 관리", width="large")
def open_day_dialog(dsel: str):
    """달력에서 날짜 클릭 시 열리는 모달: 그날의 일정 요약 + 기록 추가/수정/삭제 통합 표"""
    d_obj = pd.Timestamp(dsel).date()
    st.markdown(f"### {dsel} ({KOR_WEEKDAY[d_obj.weekday()]}요일)")

    # 오늘의 일정 요약 (정기납품 / 발주마감 / 소비기한)
    lines = []
    stores_all = df_stores()
    if not stores_all.empty:
        wd = KOR_WEEKDAY[d_obj.weekday()]
        due = [s["name"] for _, s in stores_all.iterrows()
               if wd in str(s["delivery_day"] or "").split(",") or "매일" in str(s["delivery_day"] or "")]
        if due:
            lines.append("🚚 정기 납품: " + ", ".join(due))
    try:
        _sched = replacement_schedule(int(get_setting("buffer_days", "2")),
                                      int(get_setting("cutoff_days", "2")),
                                      get_setting("cutoff_time", "11:30"))
        if not _sched.empty:
            # 🔄 오늘 교체(매장에 새 제품 넣는 날) — _L = 마지막 교체 납품일
            if "_L" in _sched.columns:
                repl = _sched[_sched["_L"].astype(str) == dsel]
                if not repl.empty:
                    items = []
                    for _, rr in repl.iterrows():
                        items.append(f"{rr['제품명']}→{rr['매장']}(기한 {rr['소비기한']})")
                    lines.append("🔄 오늘 교체: " + ", ".join(items[:8]))
            cut = _sched[_sched["_cutoff"].astype(str).str[:10] == dsel]
            if not cut.empty:
                lines.append("🧾 발주마감: " + ", ".join(cut["제품명"].unique()[:6]))
            expd = _sched[_sched["소비기한"] == dsel]
            if not expd.empty:
                lines.append("⏳ 소비기한 도래: " + ", ".join(expd["제품명"].unique()[:6]))
    except Exception:
        pass
    if lines:
        st.info(" · ".join(lines))

    # 🔄 교체 대상 강조 박스 (그날 교체할 제품이 있으면 눈에 띄게)
    try:
        if "_sched" in dir() and _sched is not None and not _sched.empty and "_L" in _sched.columns:
            repl2 = _sched[_sched["_L"].astype(str) == dsel]
            if not repl2.empty:
                st.warning("🔄 **오늘 교체할 제품** — 매장에서 빼고 새 제품으로 교체하세요:")
                show = repl2[["제품명", "매장", "소비기한", "발주마감", "상태"]].copy()
                st.dataframe(show, use_container_width=True, hide_index=True)
    except Exception:
        pass

    prods = df_products()
    stores = df_stores()
    if prods.empty:
        st.warning("제품을 먼저 등록하세요.")
        return
    store_opts = store_select_options(stores)

    def _store_label(row):
        if pd.notna(row["store_id"]):
            hit = stores[stores["id"] == int(row["store_id"])]
            if not hit.empty:
                return hit.iloc[0]["name"]
        if str(row["region"] or ""):
            return f"🗺️ {row['region']} 전체"
        return "(총량 / 매장 미지정)"

    day_tx = qdf("""SELECT t.id, t.product_id, t.store_id, t.region,
                           p.name AS 제품명, t.ttype AS 구분,
                           t.qty_box AS 박스, t.qty_ea AS 낱개,
                           t.expiry_date AS 소비기한, t.memo AS 메모
                    FROM transactions t JOIN products p ON p.id = t.product_id
                    WHERE t.tdate = :d ORDER BY t.id""", d=dsel)
    grid = pd.DataFrame(columns=["id", "제품명", "구분", "매장", "박스", "낱개", "소비기한", "메모"])
    if not day_tx.empty:
        grid = day_tx.copy()
        grid["매장"] = grid.apply(_store_label, axis=1)
        grid["소비기한"] = pd.to_datetime(grid["소비기한"].replace("", pd.NA), errors="coerce")
        grid = grid[["id", "제품명", "구분", "매장", "박스", "낱개", "소비기한", "메모"]]
    else:
        grid["소비기한"] = pd.Series(dtype="datetime64[ns]")

    st.caption("행 추가 = 맨 아래 빈 줄 입력 · 삭제 = 행 선택 후 Delete · 수량/기한/메모 수정 = 셀 클릭. 저장 시 재고가 자동 반영됩니다.")
    edited = st.data_editor(
        grid, num_rows="dynamic", hide_index=True, use_container_width=True,
        disabled=["id"],
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "제품명": st.column_config.SelectboxColumn("제품명", options=prods["name"].tolist(), required=True),
            "구분": st.column_config.SelectboxColumn("구분", options=TTYPE_OPTIONS, default="출고"),
            "매장": st.column_config.SelectboxColumn("매장", options=store_opts, default="(총량 / 매장 미지정)"),
            "박스": st.column_config.NumberColumn("박스", min_value=0, step=1, default=0),
            "낱개": st.column_config.NumberColumn("낱개", min_value=0, step=1, default=0),
            "소비기한": st.column_config.DateColumn("소비기한", format="YYYY-MM-DD"),
            "메모": st.column_config.TextColumn("메모"),
        }, key=f"day_editor_{dsel}")

    if st.button("💾 저장 (재고 자동 반영)", type="primary", use_container_width=True,
                 key=f"day_save_{dsel}"):
        prow_map = {r["name"]: r for _, r in prods.iterrows()}
        old_map = {int(r["id"]): r for _, r in day_tx.iterrows()} if not day_tx.empty else {}

        def _eff(tt, qb, qe, bq):
            if tt in ("입고", "반품"):      # 반품 = 매장에서 되돌아온 재고 → 다시 +
                return qb * bq + qe
            if tt == "출고":
                return -(qb * bq + qe)
            return 0

        def _dstr(v):
            return "" if pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%d")

        ops, deltas, seen, n_chg = [], {}, set(), 0
        lot_ops = []  # (pid, expiry, qty_signed)  qty_signed>0 → lot_add, <0 → lot_consume

        def _revert(o):
            e = _eff(o["구분"], int(o["박스"]), int(o["낱개"]),
                     bq_of(prow_map.get(str(o["제품명"]), {"box_qty": 1}).get("box_qty", 1)))
            # 입고였으면(+) 되돌림은 로트 차감(-), 출고였으면(-) 되돌림은 로트 추가(+)
            lot_ops.append((int(o["product_id"]), str(o["소비기한"] or ""), -e))

        def _apply(pid, tt, qb, qe, bq, ex):
            e = _eff(tt, qb, qe, bq)
            lot_ops.append((pid, ex, e))

        for _, r in edited.iterrows():
            if pd.isna(r["제품명"]) or str(r["제품명"]) not in prow_map:
                continue
            pr = prow_map[str(r["제품명"])]
            pid = int(pr["id"])
            bq = bq_of(pr["box_qty"])
            qb = int(r["박스"]) if pd.notna(r["박스"]) else 0
            qe = int(r["낱개"]) if pd.notna(r["낱개"]) else 0
            tt = r["구분"] if r["구분"] in TTYPE_OPTIONS else "출고"
            sid, region = parse_store_choice(str(r["매장"]), stores)
            ex = _dstr(r["소비기한"])
            mm = "" if pd.isna(r["메모"]) else str(r["메모"])
            rid = r["id"]
            if pd.notna(rid) and int(rid) in old_map:      # 기존 행: 변경 감지 + 수량 델타
                rid = int(rid); seen.add(rid)
                o = old_map[rid]
                o_bq = bq_of(prow_map.get(str(o["제품명"]), pr)["box_qty"])
                o_sid = int(o["store_id"]) if pd.notna(o["store_id"]) else None
                if (str(o["제품명"]), o["구분"], int(o["박스"]), int(o["낱개"]),
                        str(o["소비기한"] or ""), str(o["메모"] or ""), o_sid, str(o["region"] or "")) != (
                        str(r["제품명"]), tt, qb, qe, ex, mm, sid, region):
                    _revert(o)
                    _apply(pid, tt, qb, qe, bq, ex)
                    ops.append(("UPDATE transactions SET product_id=:p, ttype=:t, store_id=:s, region=:rg, "
                                "qty_box=:qb, qty_ea=:qe, expiry_date=:ex, memo=:m WHERE id=:i",
                                dict(p=pid, t=tt, s=sid, rg=region, qb=qb, qe=qe, ex=ex, m=mm, i=rid)))
                    ops.append(log_op(str(r["제품명"]), "기록수정",
                                      f"#{rid} {o['구분']} {o['박스']}박스 {o['낱개']}낱개 기한 {o['소비기한'] or '없음'}",
                                      f"{tt} {qb}박스 {qe}낱개 기한 {ex or '없음'}"))
                    n_chg += 1
            else:                                           # 신규 행
                if qb == 0 and qe == 0:
                    continue
                _apply(pid, tt, qb, qe, bq, ex)
                ops.append(("INSERT INTO transactions (tdate, product_id, ttype, store_id, qty_box, qty_ea, "
                            "region, expiry_date, memo, created_at) VALUES (:d,:p,:t,:s,:qb,:qe,:rg,:ex,:m,:c)",
                            dict(d=dsel, p=pid, t=tt, s=sid, qb=qb, qe=qe, rg=region, ex=ex, m=mm, c=KST_NOW())))
                ops.append(log_op(str(r["제품명"]), "기록추가", "(달력)", f"{dsel} {tt} {qb}박스 {qe}낱개"))
                n_chg += 1

        for rid, o in old_map.items():                       # 삭제된 행: 재고 복원
            if rid not in seen:
                _revert(o)
                ops.append(("DELETE FROM transactions WHERE id=:i", {"i": rid}))
                ops.append(log_op(str(o["제품명"]), "기록삭제", f"#{rid} {o['구분']} {o['박스']}박스 {o['낱개']}낱개", "(달력에서 삭제)"))
                n_chg += 1

        if n_chg == 0:
            st.info("변경된 내용이 없습니다.")
        else:
            lot_sql = []
            for _pid, _ex, _q in lot_ops:      # 로트 반영 (재고 = 로트 합계)
                if _q > 0:
                    lot_sql += lot_add(_pid, _ex, _q, dsel, collect=True)
                elif _q < 0:
                    lot_sql += lot_consume(_pid, -_q, _ex, collect=True)
            run_batch(ops + lot_sql)   # 기록 + 로트를 한 번에
            clear_cache(f"day_editor_{dsel}")
            st.success(f"✅ {dsel} · {n_chg}건 반영 완료")
            st.rerun()


@st.fragment
def render_schedule_calendar(sched: pd.DataFrame, cutt: str, key_prefix: str = "cal"):
    """클릭형 교체·발주 달력: 날짜를 누르면 그날 기록을 추가/수정/삭제하는 모달이 열립니다."""
    st.subheader("🗓️ 교체·발주 달력")
    st.markdown(f"""
    <style>
    .st-key-{key_prefix}_calwrap div[data-testid="stButton"] > button {{
        height: 52px; border-radius: 12px; font-weight: 800; font-size: 1.02rem;
        border: 1px solid rgba(128,128,128,.25);
    }}
    .st-key-{key_prefix}_calwrap div[data-testid="stButton"] > button:disabled {{
        opacity: 0; pointer-events: none;
    }}
    .st-key-{key_prefix}_calwrap div[data-testid="stCaptionContainer"] {{
        text-align: center; margin-top: -6px; min-height: 20px; font-size: .72rem;
    }}
    .st-key-{key_prefix}_calwrap [data-testid="column"] {{ padding: 0 3px; }}
    </style>""", unsafe_allow_html=True)
    okey = f"{key_prefix}_cal_offset"
    if okey not in st.session_state:
        st.session_state[okey] = 0
    cprev, ctitle, cnext = st.columns([1, 3, 1])
    if cprev.button("◀ 이전달", key=f"{key_prefix}_cal_prev"):
        st.session_state[okey] -= 1
        st.rerun()
    if cnext.button("다음달 ▶", key=f"{key_prefix}_cal_next"):
        st.session_state[okey] += 1
        st.rerun()
    base = today_kst().replace(day=15)
    ym = base.month - 1 + st.session_state[okey]
    year, month = base.year + ym // 12, ym % 12 + 1
    ctitle.markdown(f"<h3 style='text-align:center'>{year}년 {month}월</h3>", unsafe_allow_html=True)

    # 날짜별 이벤트 집계 (배지 표시용)
    ev = {}

    def _bump(dstr, k):
        try:
            d = pd.Timestamp(dstr).date()
        except Exception:
            return
        ev.setdefault(d, {"🚚": 0, "🧾": 0, "🔄": 0, "⏳": 0})
        ev[d][k] += 1

    if sched is not None and not sched.empty:
        for _, r in sched.iterrows():
            if r.get("_cutoff") and r["_cutoff"] != "-":
                _bump(str(r["_cutoff"])[:10], "🧾")
            if r.get("_L") and r["_L"] != "-":
                _bump(r["_L"], "🔄")
            _bump(r["소비기한"], "⏳")
    stores_all = df_stores()
    import calendar as _cal
    weeks = _cal.Calendar(firstweekday=0).monthdatescalendar(year, month)
    if not stores_all.empty:
        for week in weeks:
            for d in week:
                if d.month != month:
                    continue
                wd = KOR_WEEKDAY[d.weekday()]
                for _, s in stores_all.iterrows():
                    days = str(s["delivery_day"] or "").split(",")
                    if wd in days or "매일" in days:
                        _bump(d.strftime("%Y-%m-%d"), "🚚")

    calwrap = st.container(key=f"{key_prefix}_calwrap")
    hdr = calwrap.columns(7)
    for i, nm in enumerate(["월", "화", "수", "목", "금", "토", "일"]):
        color = "#4D96FF" if nm == "토" else "#FF6B6B" if nm == "일" else "inherit"
        hdr[i].markdown(f"<div style='text-align:center;font-weight:800;color:{color}'>{nm}</div>",
                        unsafe_allow_html=True)
    today = today_kst()
    for week in weeks:
        cols = calwrap.columns(7)
        for i, d in enumerate(week):
            with cols[i]:
                if d.month != month:
                    st.button(" ", key=f"{key_prefix}_pad_{d}", disabled=True, use_container_width=True)
                    st.caption(" ")
                    continue
                btype = "primary" if d == today else "secondary"
                if st.button(f"{d.day}", key=f"{key_prefix}_day_{d}", type=btype, use_container_width=True):
                    st.session_state["_open_day"] = d.strftime("%Y-%m-%d")
                    st.rerun(scope="app")
                badges = ev.get(d, {})
                txt = " ".join(f"{k}{v}" for k, v in badges.items() if v)
                st.caption(txt if txt else " ")
    st.caption("🚚 정기납품 · 🔄 교체납품 · 🧾 발주마감 · ⏳ 소비기한 — 날짜 버튼을 누르면 그날의 기록을 팝업에서 바로 추가·수정·삭제할 수 있습니다.")


# 달력(fragment)에서 클릭된 날짜의 모달을 앱 레벨에서 오픈
if st.session_state.get("_open_day"):
    _d = st.session_state.pop("_open_day")
    open_day_dialog(_d)

st.sidebar.title("📦 삼립 무인편의점")

# ── 4개 핵심 화면 + 설정(기타) ──
#  ① 오늘 할 일  ② 입출고(로트 자동반영)  ③ 재고 현황  ④ 메모
_MAIN = {
    "🏠 오늘 할 일 (달력·발주)": "📊 대시보드",
    "📅 월별 격자표 (제품×날짜)": "📅 월별 격자표",
    "🔄 입출고 (입고·출고·반품·교체)": "📝 일일 기록",
    "📦 재고 현황 (제품·소비기한)": "📦 재고 현황",
    "🗒️ 메모": "🗒️ 일자별 메모",
}
_SETTINGS = {
    "📅 교체·발주 일정 (상세)": "📅 교체·발주 일정",
    "🗂️ 제품 관리": "📦 제품 관리(엑셀표)",
    "🏬 납품처 관리": "🏬 납품처 관리(엑셀표)",
    "📋 납품 정리표": "📋 납품 정리표(매장×제품)",
    "📜 변경이력": "📜 변경이력",
    "⬇️ 엑셀 내보내기": "⬇️ 엑셀 내보내기",
}


def _reset_settings():
    st.session_state["nav_settings"] = "(선택 안 함)"


_main_label = st.sidebar.radio("메뉴", list(_MAIN.keys()), key="nav_main", on_change=_reset_settings)
with st.sidebar.expander("⚙️ 설정·관리 (제품·납품처·이력 등)"):
    _set_label = st.radio("설정 메뉴", ["(선택 안 함)"] + list(_SETTINGS.keys()),
                          key="nav_settings", label_visibility="collapsed")
page = _SETTINGS[_set_label] if _set_label != "(선택 안 함)" else _MAIN[_main_label]

st.sidebar.divider()
st.sidebar.caption("① 오늘 할 일 → 달력에서 교체·발주 확인\n\n② 입출고 → 입고·출고·반품(제품 교체) 기록·수정\n\n③ 재고 현황 → 지금 남은 재고·소비기한\n\n④ 메모 → 일자별 업무 메모")

# ── 성능 측정 패널: 주소 뒤에 ?perf=1 붙이면 표시 ──
if _PERF_ON:
    with st.sidebar.expander("⏱️ 속도 측정", expanded=True):
        fl = st.session_state.get("_perf_first_load", {})
        if fl:
            st.caption("**첫 로딩(이번 세션)**")
            for k, v in fl.items():
                st.write(f"- {k}: **{v}초**")
        sv = st.session_state.get("_perf_last_save")
        if sv:
            st.caption("**최근 저장**")
            st.write(f"- {sv['건수']}건 저장: **{sv['소요']}초** "
                     f"(기록 {sv['기록']}s + 로트 {sv['로트']}s)")
        # 즉석 DB 왕복 측정
        if st.button("🔄 지금 DB 왕복 측정", key="perf_ping"):
            import time as _tm
            _a = _tm.time()
            try:
                _ = qdf("SELECT 1 AS x")
            except Exception:
                pass
            _b = _tm.time()
            st.write(f"→ DB 왕복 1회: **{round((_b - _a) * 1000)}ms**")
        st.caption("측정 끄려면 주소에서 ?perf=1 제거")


# ══════════════════════════════════════════════
# 1. 대시보드
# ══════════════════════════════════════════════
if page == "📊 대시보드":
    # ── 대시보드 전용 스타일: 카드형 지표 + 섹션 헤더 강조 ──
    st.markdown("""
    <style>
    [data-testid="stMetric"] {
        background: var(--secondary-background-color, #ffffff);
        border: 1px solid rgba(128,128,128,.25);
        border-radius: 14px; padding: 14px 16px 10px 16px;
        box-shadow: 0 2px 6px rgba(0,0,0,.07);
    }
    [data-testid="stMetricLabel"] p { font-size: .85rem; font-weight: 700; opacity:.85; }
    [data-testid="stMetricValue"] { font-size: 1.7rem; font-weight: 800; }
    .sec-hdr { border-left: 6px solid #FF6B35; padding: 4px 12px; margin: 6px 0 2px 0;
               font-size: 1.15rem; font-weight: 800; background: rgba(255,107,53,.08);
               border-radius: 6px; }
    .sec-hdr.blue { border-left-color:#4D96FF; background: rgba(77,150,255,.08); }
    .sec-hdr.green { border-left-color:#6BCB77; background: rgba(107,203,119,.08); }
    </style>""", unsafe_allow_html=True)

    st.title("📊 대시보드")
    st.caption(f"{TODAY()} ({KOR_WEEKDAY[today_kst().weekday()]}) · 삼립 무인편의점 재고·발주")
    prods = df_products()
    today = TODAY()
    tx_today = df_transactions(today, today)

    # 유통기한 현황 선계산 (KPI + 핵심 섹션용)
    bd = expiry_breakdown()
    _dated = bd[bd["소비기한"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)] if not bd.empty else pd.DataFrame()
    if not _dated.empty:
        from datetime import timedelta as _td
        _limit = (today_kst() + _td(days=30)).strftime("%Y-%m-%d")
        _soon = _dated[(_dated["소비기한"] <= _limit) & (_dated["소비기한"] >= today)]
        _passed = _dated[_dated["소비기한"] < today]
    else:
        _soon = _passed = pd.DataFrame()

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("등록 제품", f"{len(prods)}개")
    k2.metric("오늘 입고", f"{len(tx_today[tx_today['구분']=='입고'])}건")
    k3.metric("오늘 출고", f"{len(tx_today[tx_today['구분']=='출고'])}건")
    k4.metric("오늘 발주", f"{len(tx_today[tx_today['구분']=='발주'])}건")
    k5.metric("⏰ 기한 임박(30일)", f"{len(_soon)}건",
              delta=f"-{int(_soon['잔여낱개환산'].sum()):,}낱개" if len(_soon) else None,
              delta_color="inverse")
    k6.metric("🚨 기한 경과", f"{len(_passed)}건",
              delta=f"-{int(_passed['잔여낱개환산'].sum()):,}낱개" if len(_passed) else None,
              delta_color="inverse")

    st.markdown('<div class="sec-hdr">⏰ 유통기한 관리</div>', unsafe_allow_html=True)

    # 소비기한 임박 경고 (남아있는 수량 기준, 30일 이내)
    if not bd.empty:
        from datetime import timedelta
        limit = (today_kst() + timedelta(days=30)).strftime("%Y-%m-%d")
        dated = bd[bd["소비기한"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
        soon = dated[dated["소비기한"] <= limit]
        if not soon.empty:
            passed = soon[soon["소비기한"] < TODAY()]
            if not passed.empty:
                st.error(f"🚨 소비기한 경과 {len(passed)}건 · 잔여 {int(passed['잔여낱개환산'].sum()):,}낱개")
            st.warning(f"⏰ 소비기한 30일 이내(경과 포함) {len(soon)}건 · "
                       f"잔여 합계 {int(soon['잔여낱개환산'].sum()):,}낱개")
            st.dataframe(soon.sort_values("소비기한"), use_container_width=True, hide_index=True)
            st.caption("※ 입고 당시 수량이 아니라, 출고·재고조정을 반영한 **현재 남아있는 수량**입니다.")

    # 소비기한별 잔여 수량 분해 (입고 로트 기준, 출고는 기한 빠른 순 차감)
    if not bd.empty:
        with st.expander("📦 소비기한별 수량 — 일일기록 기준", expanded=True):
            c_ts, c_rf = st.columns([4, 1])
            c_ts.caption(f"계산 기준: {KST_NOW()} (KST) · 로직 {LOGIC_VERSION} · 숫자가 이상하면 새로고침을 눌러주세요.")
            if c_rf.button("🔄 새로고침", key="exp_refresh", use_container_width=True):
                st.cache_data.clear()
                st.rerun()
            tab_raw, tab_calc = st.tabs(["📄 일일기록 로트 + 현재 잔여", "🧮 잔여 계산 (소비기한별 합산)"])

            # ── 탭1: 일일 기록에서 입력한 값을 아무 가공 없이 그대로 표시 ──
            with tab_raw:
                raw = qdf("""
                    SELECT t.tdate AS 입고일, p.name AS 제품명, t.expiry_date AS 소비기한,
                           t.qty_box AS 박스, t.qty_ea AS 낱개,
                           (t.qty_box * GREATEST(p.box_qty, 1) + t.qty_ea) AS 환산낱개,
                           t.memo AS 메모
                    FROM transactions t JOIN products p ON p.id = t.product_id
                    WHERE t.ttype = '입고' AND t.expiry_date <> ''
                    ORDER BY t.expiry_date, t.tdate, t.id""")
                if raw.empty:
                    st.info("일일 기록에서 소비기한과 함께 입고를 입력하면 여기에 그대로 표시됩니다.")
                else:
                    # ── 현재 잔여 배분: 잔여계산(제품관리 재고 수정·출고 반영) 결과를
                    #    같은 소비기한의 입고 기록에 오래된 것부터 소진된 것으로 배분 ──
                    left_map = {}
                    if not bd.empty:
                        for _, _r in bd.iterrows():
                            left_map[(_r["제품명"], _r["소비기한"])] = int(_r["잔여낱개환산"])
                    raw["현재잔여"] = 0
                    for (_pn, _ex), _g in raw.groupby(["제품명", "소비기한"], sort=False):
                        _left = left_map.get((_pn, _ex), 0)
                        _consumed = max(int(_g["환산낱개"].sum()) - _left, 0)
                        for _idx in _g.sort_values(["입고일"]).index:  # 오래된 입고분부터 소진 처리
                            _q = int(raw.at[_idx, "환산낱개"])
                            _take = min(_q, _consumed)
                            _consumed -= _take
                            raw.at[_idx, "현재잔여"] = _q - _take
                    raw["디데이"] = raw["소비기한"].apply(
                        lambda e: (lambda dd: f"D{dd:+d}" if dd < 0 else (f"D-{dd}" if dd > 0 else "D-DAY"))(
                            (pd.Timestamp(e).date() - today_kst()).days))
                    raw = raw[["입고일", "제품명", "소비기한", "박스", "낱개", "환산낱개", "현재잔여", "디데이", "메모"]]
                    def _hl_raw(row):
                        try:
                            dd = (pd.Timestamp(str(row["소비기한"])).date() - today_kst()).days
                            base = ("background-color: #FFEBEE" if dd < 0
                                    else "background-color: #FFF8E1" if dd <= 30 else "")
                        except Exception:
                            base = ""
                        styles = [base] * len(row)
                        # '현재잔여'는 기준 열 → 파란 강조로 덮어쓰기
                        ci = list(row.index).index("현재잔여")
                        styles[ci] = "background-color: #1E88E5; color: #ffffff; font-weight: 800;"
                        return styles
                    raw_view = search_box(raw, "search_raw_exp", "🔍 제품명 검색")
                    styled = (raw_view.style.apply(_hl_raw, axis=1)
                              .set_properties(subset=["현재잔여"],
                                              **{"background-color": "#1565C0", "color": "white",
                                                 "font-weight": "800"}))
                    st.dataframe(styled, use_container_width=True, hide_index=True,
                                 column_config={"현재잔여": st.column_config.NumberColumn("🔵 현재잔여")})
                    tot = raw_view.groupby("제품명", as_index=False).agg(
                        입고합=("환산낱개", "sum"), 잔여합=("현재잔여", "sum"))
                    manual_note = ""
                    if not bd.empty:
                        _mn = bd[bd["소비기한"] == "(수동조정분)"]
                        if not _mn.empty:
                            manual_note = " | 수동조정분: " + " · ".join(
                                f"{r['제품명']} {int(r['잔여낱개환산']):+,}" for _, r in _mn.iterrows())
                    st.caption("제품별 [입고합 → 현재잔여]: " + " · ".join(
                        f"**{r['제품명']}** {int(r['입고합']):,}→{int(r['잔여합']):,}"
                        for _, r in tot.iterrows()) + manual_note)
                    csv_button(raw_view, "소비기한_일일기록_잔여", "csv_raw_exp")
                    st.caption("'환산낱개'는 입력한 그대로, '현재잔여'는 출고와 제품 관리(엑셀표) 재고 수정을 반영해 "
                               "이 로트에서 지금 남은 수량입니다 (출고는 유통기한 짧은 로트부터, 같은 기한 안에서는 오래된 입고분부터 소진). "
                               "표에서 수동으로 넣은 재고는 소비기한을 알 수 없어 '수동조정분'으로 따로 집계됩니다.")

            # ── 탭2: 잔여 계산 (기존 로직: 출고·수동조정 반영, 합계=현재고) ──
            with tab_calc:
                def _hl_exp(row):
                    # 날짜가 아닌 값('(기한없음)', '(수동조정분)' 등)은 색칠하지 않음
                    try:
                        dd = (pd.Timestamp(str(row["소비기한"])).date() - today_kst()).days
                    except Exception:
                        return [""] * len(row)
                    if dd < 0:
                        return ["background-color: #FFEBEE"] * len(row)   # 경과: 연빨강
                    if dd <= 30:
                        return ["background-color: #FFF8E1"] * len(row)   # 임박: 연노랑
                    return [""] * len(row)
                # 제품별 검산: 현재 재고 vs 소비기한 잔여합계
                cur_tbl = prods.copy()
                cur_tbl["현재재고(낱개환산)"] = cur_tbl.apply(total_ea, axis=1)
                cur_tbl = cur_tbl[["name", "현재재고(낱개환산)"]].rename(columns={"name": "제품명"})
                sum_tbl = bd.groupby("제품명", as_index=False)["잔여낱개환산"].sum().rename(
                    columns={"잔여낱개환산": "소비기한 잔여합계"})
                chk = cur_tbl.merge(sum_tbl, on="제품명", how="left").fillna({"소비기한 잔여합계": 0})
                chk["소비기한 잔여합계"] = chk["소비기한 잔여합계"].astype(int)
                chk["차이"] = chk["현재재고(낱개환산)"] - chk["소비기한 잔여합계"]
                gap = chk[chk["차이"] != 0]

                pick_p = st.selectbox("제품 필터", ["(전체)"] + sorted(bd["제품명"].unique()), key="exp_pick")
                show = bd if pick_p == "(전체)" else bd[bd["제품명"] == pick_p]
                show = search_box(show, "search_expiry", "🔍 제품명 검색")
                _st2 = (show.style.apply(_hl_exp, axis=1)
                        .set_properties(subset=["잔여낱개환산"],
                                        **{"background-color": "#1565C0", "color": "white",
                                           "font-weight": "800"}))
                st.dataframe(_st2, use_container_width=True, hide_index=True)
                if pick_p != "(전체)":
                    st.caption(f"**{pick_p}** 잔여 합계: **{int(show['잔여낱개환산'].sum()):,}낱개** "
                               f"(현재 재고: {int(chk[chk['제품명']==pick_p]['현재재고(낱개환산)'].iloc[0]):,}낱개)")
                csv_button(bd, "소비기한별잔여", "csv_expiry_bd")

                st.markdown("**🧮 검산 — 현재 재고 vs 소비기한 잔여합계**")
                if gap.empty:
                    st.success("✅ 모든 제품에서 소비기한 잔여합계가 현재 재고와 일치합니다.")
                else:
                    st.error(f"❌ {len(gap)}개 제품에서 수치가 어긋납니다. 아래 표를 확인하세요.")
                st.dataframe(chk if not gap.empty else chk.head(50),
                             use_container_width=True, hide_index=True)
                st.caption("출고는 유통기한이 짧은 로트부터(FEFO) 차감되고, 로트를 다 쓰면 수동재고에서 차감됩니다. 현재 재고(제품 관리 표 수정 포함)와 "
                           "합계가 일치하도록 자동 보정합니다. 출고는 유통기한 짧은 로트부터 차감되며, 표에서 수동으로 조정한 차이는 '(수동조정분)' 행에 ±로 모입니다. 수동조정분을 일일기록(입고/출고)으로 옮겨 적으면 이 행은 0이 되어 사라집니다.")

    # ── 교체·발주 달력 (대시보드) ──
    _buf = int(get_setting("buffer_days", "2"))
    _cutd = int(get_setting("cutoff_days", "2"))
    _cutt = get_setting("cutoff_time", "11:30")
    _sched = replacement_schedule(_buf, _cutd, _cutt)
    render_schedule_calendar(_sched, _cutt, key_prefix="dash")

    st.markdown('<div class="sec-hdr blue">🚚 오늘의 알림</div>', unsafe_allow_html=True)

    # 오늘 요일에 납품 나가는 매장 (다중 요일 "화,금" 형식 지원)
    today_day = KOR_WEEKDAY[today_kst().weekday()]
    stores_all = df_stores()
    if not stores_all.empty:
        def _due(s):
            days = str(s or "").split(",")
            return today_day in days or "매일" in days
        due = stores_all[stores_all["delivery_day"].apply(_due)]
        if not due.empty:
            st.info(f"🚚 오늘({today_day}요일) 납품 나가는 매장 {len(due)}곳: "
                    + ", ".join(due["name"].tolist()))


    with st.expander("📦 재고 현황 · 오늘 기록 보기 (열면 계산)", expanded=False):
        if prods.empty:
            st.info("등록된 제품이 없습니다. [설정·관리 → 제품 관리]에서 제품을 추가하세요.")
        else:
            tab_now, tab_trend = st.tabs(["📋 현재 재고 현황", "📈 일자별 재고 추세 (자동 저장)"])
            with tab_now:
                view = prods.copy()
                view["총낱개환산"] = view.apply(total_ea, axis=1)
                view["박스환산"] = view.apply(
                    lambda r: fmt_stock_ea(int(r["총낱개환산"]), r["box_qty"]), axis=1)
                view = view[["name", "barcode", "is_new", "box_qty", "spec", "normal_price", "sale_price",
                             "storage", "delivery_ea", "총낱개환산", "박스환산"]]
                view.columns = ["제품명", "바코드", "구분", "박스입수량", "규격(무게)", "정상가", "할인판매가",
                                "보관방법", "납품갯수(낱개)", "총낱개환산", "박스환산(자동)"]
                st.dataframe(view, use_container_width=True, hide_index=True)
                csv_button(view, "재고현황", "csv_dash")
            with tab_trend:
                snaps = df_snapshots()
                if snaps.empty:
                    st.info("접속·저장할 때마다 그날의 재고가 자동 기록됩니다. 내일부터 추세가 그려집니다.")
                else:
                    opts = sorted(snaps["제품명"].unique())
                    pick = st.multiselect("표시할 제품", opts, default=opts[:min(5, len(opts))],
                                          key="snap_pick")
                    if pick:
                        sub = snaps[snaps["제품명"].isin(pick)]
                        chart = sub.pivot_table(index="날짜", columns="제품명",
                                                values="재고환산낱개", aggfunc="last")
                        st.line_chart(chart)
                        st.caption("매일의 마지막 재고 상태가 날짜별로 자동 저장됩니다. 세로축 = 재고(총낱개환산).")
                        csv_button(sub, "재고추세", "csv_snap")

            st.subheader("오늘 기록")
            st.dataframe(tx_today.drop(columns=["id"]), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════
# 2. 일일 기록 (입고/출고/발주)
# ══════════════════════════════════════════════
elif page == "📝 일일 기록":
    st.title("📝 일일 입고 · 출고 · 발주 기록")
    prods = df_products()
    stores = df_stores()

    if prods.empty:
        st.warning("먼저 [제품 관리]에서 제품을 등록하세요.")
    else:
        mode_daily = st.radio(
            "입력 방식",
            ["1️⃣ 단일 입력 (실시간 환산 표시)", "🧾 여러 제품 일괄 입력 (엑셀형 · 발주서 CSV)"],
            horizontal=True, label_visibility="collapsed")

        # ═══ 모드 1: 단일 입력 ═══
        if mode_daily.startswith("1️⃣"):
            c1, c2, c3 = st.columns(3)
            tdate = c1.date_input("날짜", value=today_kst())
            pname = c2.selectbox("제품", prods["name"].tolist())
            ttype = c3.radio("구분", TTYPE_OPTIONS, horizontal=True)

            prow = prods[prods["name"] == pname].iloc[0]
            box_qty = max(int(prow["box_qty"]), 1)

            c4, c5, c6, c7 = st.columns([1.2, 1, 1, 1.2])
            store_names = store_select_options(stores)
            sname = c4.selectbox("매장(납품처)", store_names,
                                 help="개별 매장 / 🗺️ 지역 전체(인천·경기서울 등) / (총량) 중 선택. "
                                      "지역 그룹은 [납품처 관리]의 '지역(그룹)' 칸에 입력하면 자동 생성")
            qty_box = c5.number_input("수량(박스)", min_value=0, step=1, value=0, key="in_box",
                                      help="박스만 입력해도 됩니다 → 낱개로 자동 환산")
            qty_ea = c6.number_input("수량(낱개)", min_value=0, step=1, value=0, key="in_ea",
                                     help="낱개만 입력해도 됩니다")

            # ── 실시간 환산 표시 (박스 입력 → 낱개 자동 계산) ──
            conv = int(qty_box) * box_qty + int(qty_ea)
            with c7:
                st.metric("환산 낱개(자동)", f"{conv:,}개",
                          help=f"'{pname}' 1박스 = {box_qty}낱개 기준 자동 계산")
            if qty_box and qty_ea:
                st.caption(f"↳ 박스 {qty_box} × {box_qty}낱개 + 낱개 {qty_ea} = **{conv:,}낱개**")
            elif qty_box:
                st.caption(f"↳ 박스 {qty_box} × {box_qty}낱개 = **{conv:,}낱개** (박스만 입력됨)")
            elif qty_ea:
                st.caption(f"↳ 낱개 {qty_ea}개 (낱개만 입력됨)")

            c8, c9 = st.columns([1, 2])
            exp_use = c8.checkbox("소비기한 입력", value=True, help="물건이 들어올 때(입고) 소비기한을 기록 (기본 켜짐)")
            exp_date = c8.date_input("소비기한", value=today_kst(), label_visibility="collapsed")
            memo = c9.text_input("메모", placeholder="예: 국군복지단 정기 납품 / 로트번호 등")
            ok = st.button("💾 기록 저장", use_container_width=True, type="primary")

            if ok:
                if qty_box == 0 and qty_ea == 0:
                    st.error("박스 또는 낱개 수량 중 하나 이상을 입력하세요.")
                else:
                    pid = int(prow["id"])
                    sid, region = parse_store_choice(sname, stores)
                    ops = [(
                        "INSERT INTO transactions (tdate, product_id, ttype, store_id, qty_box, qty_ea, region, expiry_date, memo, created_at) "
                        "VALUES (:d, :p, :t, :s, :qb, :qe, :rg, :ex, :m, :c)",
                        dict(d=tdate.strftime("%Y-%m-%d"), p=pid, t=ttype, s=sid, rg=region,
                             qb=int(qty_box), qe=int(qty_ea),
                             ex=exp_date.strftime("%Y-%m-%d") if exp_use else "",
                             m=memo, c=KST_NOW()))]
                    if ttype in ("입고", "출고", "반품"):
                        ex_str = exp_date.strftime("%Y-%m-%d") if exp_use else ""
                        if ttype in ("입고", "반품"):   # 반품도 재고 +
                            ops += lot_add(pid, ex_str, int(conv), tdate.strftime("%Y-%m-%d"), collect=True)
                        else:
                            ops += lot_consume(pid, int(conv), ex_str, collect=True)
                        ops.append(log_op(pname, "재고(로트)", "", f"{ttype} 환산 {conv}낱개"
                                          + (f" · 기한 {ex_str}" if ex_str else " · 기한없음(FEFO)")))
                        run_batch(ops)   # 기록 + 로트 + 로그를 한 번에
                    else:
                        run_batch(ops)
                    clear_cache()
                    # 수량 입력칸 초기화
                    for k in ("in_box", "in_ea"):
                        st.session_state.pop(k, None)
                    st.success(f"✅ {tdate} · {pname} · {ttype} · 환산 {conv:,}낱개 기록 완료")
                    st.rerun()


        # ═══ 모드 2: 여러 제품 일괄 입력 (엑셀형) → 발주서 CSV ═══
        else:
            st.caption("아래 표에 행을 추가하며 여러 제품을 한 번에 입력하세요. 저장 전에도 [발주서 CSV]로 내보내 발주 담당자에게 전달할 수 있습니다.")
            bdate = st.date_input("날짜 (모든 행에 일괄 적용)", value=today_kst(), key="bulk_date")

            store_opts = store_select_options(stores)
            bulk_empty = pd.DataFrame({
                "제품명": pd.Series(dtype="object"),
                "구분": pd.Series(dtype="object"),
                "매장": pd.Series(dtype="object"),
                "박스": pd.Series(dtype="Int64"),
                "낱개": pd.Series(dtype="Int64"),
                "소비기한": pd.Series(dtype="datetime64[ns]"),
                "메모": pd.Series(dtype="object"),
            })
            edited_b = st.data_editor(
                bulk_empty, num_rows="dynamic", hide_index=True, use_container_width=True,
                column_config={
                    "제품명": st.column_config.SelectboxColumn("제품명", options=prods["name"].tolist(), required=True),
                    "구분": st.column_config.SelectboxColumn("구분", options=TTYPE_OPTIONS, default="발주"),
                    "매장": st.column_config.SelectboxColumn("매장(납품처)", options=store_opts, default="(총량 / 매장 미지정)"),
                    "박스": st.column_config.NumberColumn("수량(박스)", min_value=0, step=1, default=0),
                    "낱개": st.column_config.NumberColumn("수량(낱개)", min_value=0, step=1, default=0),
                    "소비기한": st.column_config.DateColumn("소비기한(선택)", format="YYYY-MM-DD"),
                    "메모": st.column_config.TextColumn("메모"),
                }, key="daily_bulk_editor")

            valid = edited_b[edited_b["제품명"].notna()].copy()
            if valid.empty:
                st.info("표에 제품을 추가하면 발주서 CSV 내보내기와 일괄 저장 버튼이 나타납니다.")
            else:
                valid["박스"] = valid["박스"].fillna(0).astype(int)
                valid["낱개"] = valid["낱개"].fillna(0).astype(int)
                valid["구분"] = valid["구분"].fillna("발주")
                valid["매장"] = valid["매장"].fillna("(총량 / 매장 미지정)")
                valid["메모"] = valid["메모"].fillna("")

                # ── 발주서 CSV: 상품 정보(바코드·가격·박스입수량) 포함 ──
                info = prods[["name", "barcode", "box_qty", "normal_price", "sale_price"]].rename(
                    columns={"name": "제품명", "barcode": "바코드", "box_qty": "박스입수량",
                             "normal_price": "정상가", "sale_price": "할인판매가"})
                order = valid.merge(info, on="제품명", how="left")
                order["날짜"] = bdate.strftime("%Y-%m-%d")
                order["환산낱개"] = order["박스"] * order["박스입수량"].clip(lower=1) + order["낱개"]
                order["소비기한"] = order["소비기한"].apply(
                    lambda v: "" if pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%d"))
                order = order[["날짜", "구분", "매장", "제품명", "바코드", "박스입수량",
                               "정상가", "할인판매가", "박스", "낱개", "환산낱개", "소비기한", "메모"]]
                order.columns = ["날짜", "구분", "매장", "제품명", "바코드", "박스입수량",
                                 "정상가", "할인판매가", "수량(박스)", "수량(낱개)", "환산낱개", "소비기한", "메모"]

                c_dl, c_sv = st.columns(2)
                with c_dl:
                    st.download_button(
                        "📤 발주서 CSV 내보내기 (담당자 전달용)",
                        data=csv_bytes(order),
                        file_name=f"발주서_{bdate.strftime('%Y-%m-%d')}.csv",
                        mime="text/csv", use_container_width=True, key="order_csv")
                with c_sv:
                    do_save = st.button(f"💾 {len(valid)}건 일괄 저장", type="primary",
                                        use_container_width=True, key="bulk_save")

                if do_save:
                    bad = valid[(valid["박스"] == 0) & (valid["낱개"] == 0)]
                    if not bad.empty:
                        st.error(f"수량이 0인 행이 {len(bad)}건 있습니다. 박스 또는 낱개를 입력하세요.")
                    else:
                        # 중복 저장 방지: 같은 내용(날짜+행들)의 지문을 만들어 직전 저장과 비교
                        import hashlib as _hl
                        _fp = _hl.md5(
                            (bdate.strftime("%Y-%m-%d") + "|" +
                             valid[["제품명", "구분", "매장", "박스", "낱개", "소비기한", "메모"]]
                             .astype(str).to_csv(index=False)).encode("utf-8")).hexdigest()
                        if st.session_state.get("_bulk_last_fp") == _fp:
                            st.warning("⚠️ 방금 저장한 내용과 동일합니다. 중복 저장을 막았어요. "
                                       "(다시 저장하려면 표를 새로 입력하세요)")
                        else:
                            prow_map = {r["name"]: r for _, r in prods.iterrows()}
                            ops = []
                            lot_ops = []  # (pid, expiry, qty_signed)
                            for _, r in valid.iterrows():
                                pr = prow_map[r["제품명"]]
                                pid = int(pr["id"])
                                sid, region = parse_store_choice(str(r["매장"]), stores)
                                ex = "" if pd.isna(r["소비기한"]) else pd.Timestamp(r["소비기한"]).strftime("%Y-%m-%d")
                                bq = bq_of(pr["box_qty"])
                                conv = int(r["박스"]) * bq + int(r["낱개"])
                                ops.append((
                                    "INSERT INTO transactions (tdate, product_id, ttype, store_id, qty_box, qty_ea, region, expiry_date, memo, created_at) "
                                    "VALUES (:d, :p, :t, :s, :qb, :qe, :rg, :ex, :m, :c)",
                                    dict(d=bdate.strftime("%Y-%m-%d"), p=pid, t=r["구분"],
                                         s=sid, rg=region,
                                         qb=int(r["박스"]), qe=int(r["낱개"]), ex=ex, m=r["메모"], c=KST_NOW())))
                                if r["구분"] in ("입고", "반품"):
                                    lot_ops.append((pid, ex, conv))
                                elif r["구분"] == "출고":
                                    lot_ops.append((pid, ex, -conv))
                            import time as _tm
                            _sa = _tm.time()
                            # 로트 연산까지 모아서 한 트랜잭션으로 (왕복 최소화)
                            lot_sql = []
                            for _pid, _ex, _q in lot_ops:
                                if _q > 0:
                                    lot_sql += lot_add(_pid, _ex, _q, bdate.strftime("%Y-%m-%d"), collect=True)
                                elif _q < 0:
                                    lot_sql += lot_consume(_pid, -_q, _ex, collect=True)
                            run_batch(ops + lot_sql)   # 기록 + 로트를 한 번에
                            _sc = _tm.time()
                            st.session_state["_perf_last_save"] = {
                                "건수": len(valid),
                                "소요": round(_sc - _sa, 2),
                                "기록": round(_sc - _sa, 2),
                                "로트": 0.0,
                            }
                            st.session_state["_bulk_last_fp"] = _fp   # 저장 지문 기록
                            clear_cache("daily_bulk_editor")          # 표 초기화 (같은 내용 재저장 방지)
                            st.success(f"✅ {bdate.strftime('%Y-%m-%d')} · {len(valid)}건 일괄 저장 완료")
                            st.rerun()

        # ── 일일 기록 엑셀 내보내기 ──
        st.divider()
        c_a, c_b = st.columns([1, 2])
        with c_a:
            exp_date = st.date_input("내보낼 날짜", value=today_kst(), key="daily_exp")
        with c_b:
            st.write("")
            st.write("")
            d = exp_date.strftime("%Y-%m-%d")
            st.download_button(
                f"📥 {d} 일일 기록 엑셀 다운로드",
                data=build_excel(d, d),
                file_name=f"일일재고관리_{d}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, type="primary")

        st.divider()
        st.subheader("최근 기록 (삭제 시 재고 자동 복원)")

        # ── ✏️ 기록 수정: 날짜·수량·소비기한·메모 전부 수정 가능 (재고 자동 재반영) ──
        with st.expander("✏️ 기록 수정 — 날짜·수량·소비기한·메모 전부 수정", expanded=False):
            fix_src = qdf("""
                SELECT t.id, t.tdate AS 날짜, p.name AS 제품명, t.ttype AS 구분,
                       COALESCE(s.name, CASE WHEN t.region <> '' THEN '(' || t.region || ' 전체)'
                                             ELSE '(총량)' END) AS 매장,
                       t.product_id, p.box_qty,
                       t.qty_box AS 박스, t.qty_ea AS 낱개,
                       t.expiry_date AS 소비기한, t.memo AS 메모
                FROM transactions t
                JOIN products p ON p.id = t.product_id
                LEFT JOIN stores s ON s.id = t.store_id
                ORDER BY t.tdate DESC, t.id DESC LIMIT 200""")
            if fix_src.empty:
                st.info("수정할 기록이 없습니다.")
            else:
                fq = st.text_input("🔍 수정할 기록 검색", key="fix_tx_search",
                                   placeholder="제품명·구분·메모 일부 입력 (예: 카스테라, 반품)")
                fix_view = fix_src
                if fq:
                    m = fix_src.apply(lambda r: r.astype(str).str.contains(
                        fq, case=False, na=False, regex=False).any(), axis=1)
                    fix_view = fix_src[m]
                fix_grid = fix_view[["id", "날짜", "제품명", "구분", "매장", "박스", "낱개", "소비기한", "메모"]].copy()
                fix_grid["날짜"] = pd.to_datetime(fix_grid["날짜"], errors="coerce")
                fix_grid["소비기한"] = pd.to_datetime(fix_grid["소비기한"].replace("", pd.NA), errors="coerce")

                st.caption("날짜·수량(박스/낱개)·소비기한·메모를 고칠 수 있습니다. 저장하면 재고(로트)에 자동 반영됩니다. "
                           "제품·구분·매장을 바꾸려면 삭제 후 다시 입력하세요.")
                fixed = st.data_editor(
                    fix_grid, hide_index=True, use_container_width=True,
                    disabled=["id", "제품명", "구분", "매장"],
                    column_config={
                        "id": st.column_config.NumberColumn("ID", width="small"),
                        "날짜": st.column_config.DateColumn("날짜", format="YYYY-MM-DD"),
                        "박스": st.column_config.NumberColumn("박스", min_value=0, step=1),
                        "낱개": st.column_config.NumberColumn("낱개", min_value=0, step=1),
                        "소비기한": st.column_config.DateColumn("소비기한", format="YYYY-MM-DD",
                                                             help="비우면 '기한 없음'"),
                        "메모": st.column_config.TextColumn("메모"),
                    }, key="fix_tx_editor")

                if st.button("💾 수정 저장 (재고 자동 반영)", type="primary", use_container_width=True, key="fix_tx_save"):
                    import hashlib as _hl
                    _fixfp = _hl.md5(
                        fixed[["id", "날짜", "박스", "낱개", "소비기한", "메모"]]
                        .astype(str).to_csv(index=False).encode("utf-8")).hexdigest()
                    if st.session_state.get("_fix_last_fp") == _fixfp:
                        st.warning("⚠️ 방금 저장한 수정 내용과 동일합니다. 중복 저장(재고 이중 반영)을 막았어요.")
                        st.stop()

                    def _dstr(v):
                        return "" if pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%d")

                    def _eff2(tt, conv):
                        return conv if tt in ("입고", "반품") else (-conv if tt == "출고" else 0)

                    old_map = {int(r["id"]): r for _, r in fix_view.iterrows()}
                    ops, lot_ops, n_fix = [], [], 0
                    for _, r in fixed.iterrows():
                        rid = int(r["id"]); old = old_map.get(rid)
                        if old is None:
                            continue
                        bq = bq_of(old["box_qty"]); tt = str(old["구분"]); pid = int(old["product_id"])
                        new_d, new_e = _dstr(r["날짜"]), _dstr(r["소비기한"])
                        new_m = "" if pd.isna(r["메모"]) else str(r["메모"])
                        new_qb = int(r["박스"]) if pd.notna(r["박스"]) else 0
                        new_qe = int(r["낱개"]) if pd.notna(r["낱개"]) else 0
                        old_e = str(old["소비기한"] or "")
                        if (str(old["날짜"]), old_e, str(old["메모"] or ""), int(old["박스"]), int(old["낱개"])) != (
                                new_d, new_e, new_m, new_qb, new_qe):
                            if not new_d:
                                st.warning(f"#{rid}: 날짜는 비울 수 없어 건너뜀"); continue
                            # 같은 날짜·제품·구분·소비기한의 다른 기록이 이미 있으면 안내(참고용, 저장은 진행)
                            dup = qdf("""SELECT COUNT(*) AS n FROM transactions
                                         WHERE id <> :i AND product_id = :p AND ttype = :t
                                           AND tdate = :d AND expiry_date = :e""",
                                      i=rid, p=pid, t=tt, d=new_d, e=new_e)
                            if not dup.empty and int(dup.iloc[0]["n"]) > 0:
                                st.info(f"ℹ️ #{rid}: 같은 날짜·제품·구분·소비기한의 다른 기록이 이미 있습니다. "
                                        "필요하면 그 기록과 합치거나 하나를 삭제하세요.")
                            # 재고 재반영: 옛 효과 되돌리고(로트) 새 효과 적용
                            old_conv = int(old["박스"]) * bq + int(old["낱개"])
                            new_conv = new_qb * bq + new_qe
                            lot_ops.append((pid, old_e, -_eff2(tt, old_conv)))   # 되돌림
                            lot_ops.append((pid, new_e, _eff2(tt, new_conv)))    # 새 적용
                            ops.append(("UPDATE transactions SET tdate=:d, qty_box=:qb, qty_ea=:qe, "
                                        "expiry_date=:e, memo=:m WHERE id=:i",
                                        dict(d=new_d, qb=new_qb, qe=new_qe, e=new_e, m=new_m, i=rid)))
                            ops.append(log_op(str(old["제품명"]), "기록수정",
                                              f"#{rid} {tt} {old['박스']}박스 {old['낱개']}낱개 / {old['날짜']} / 기한 {old_e or '없음'}",
                                              f"{new_qb}박스 {new_qe}낱개 / {new_d} / 기한 {new_e or '없음'}"))
                            n_fix += 1
                    if n_fix == 0:
                        st.info("변경된 내용이 없습니다.")
                    else:
                        lot_sql = []
                        for _pid, _ex, _q in lot_ops:
                            if _q > 0:
                                lot_sql += lot_add(_pid, _ex, _q, collect=True)
                            elif _q < 0:
                                lot_sql += lot_consume(_pid, -_q, _ex, collect=True)
                        run_batch(ops + lot_sql)
                        clear_cache("fix_tx_editor")
                        st.session_state["_fix_last_fp"] = _fixfp   # 저장 지문 기록 (중복 방지)
                        st.success(f"✅ {n_fix}건 수정 완료 — 재고에 즉시 반영됩니다.")
                        st.rerun()
        tx = df_transactions()
        if tx.empty:
            st.info("기록이 없습니다.")
        else:
            tx_view = search_box(tx.drop(columns=["id"]), "search_tx", "🔍 기록 검색 (제품/매장/메모)")
            st.dataframe(tx_view.head(50), use_container_width=True, hide_index=True)
            csv_button(tx_view, "입출고기록", "csv_tx")
            del_id = st.selectbox(
                "삭제할 기록 선택",
                options=[0] + tx["id"].tolist(),
                format_func=lambda i: "선택 안 함" if i == 0 else (
                    lambda r: f"#{i} | {r['날짜']} | {r['제품명']} | {r['구분']} | {r['매장']} | "
                              f"{int(r['박스'])}박스 {int(r['낱개'])}낱개 | 기한 "
                              f"{r['소비기한'] if str(r['소비기한']).strip() else '없음'}"
                )(tx[tx['id'] == i].iloc[0]),
            )
            if del_id and st.button("🗑️ 선택 기록 삭제"):
                row = qdf("SELECT product_id, ttype, qty_box, qty_ea, expiry_date FROM transactions WHERE id=:i", i=del_id)
                if not row.empty:
                    r = row.iloc[0]
                    pr2 = prods[prods["id"] == int(r["product_id"])].iloc[0]
                    conv = int(r["qty_box"]) * bq_of(pr2["box_qty"]) + int(r["qty_ea"])
                    run("DELETE FROM transactions WHERE id=:i", i=int(del_id))
                    if r["ttype"] in ("입고", "반품"):   # 입고/반품 취소 → 로트 차감
                        lot_consume(int(r["product_id"]), conv, str(r["expiry_date"] or ""))
                    elif r["ttype"] == "출고":     # 출고 취소 → 로트 복원
                        lot_add(int(r["product_id"]), str(r["expiry_date"] or ""), conv)
                    clear_cache()
                    st.success("삭제 및 재고 복원 완료")
                    st.rerun()


# ══════════════════════════════════════════════
# 월별 격자표 — 제품 × 날짜 (입고+/출고-/반품↺)
# ══════════════════════════════════════════════
elif page == "📅 월별 격자표":
    st.title("📅 월별 격자표 (제품 × 날짜)")
    st.markdown("""
    <style>
    .grid-wrap { overflow-x: auto; }
    table.mgrid { border-collapse: collapse; font-size: .8rem; white-space: nowrap; }
    table.mgrid th, table.mgrid td { border: 1px solid rgba(128,128,128,.25); padding: 3px 6px; text-align: center; }
    table.mgrid th.pname, table.mgrid td.pname { text-align: left; position: sticky; left: 0;
        background: var(--background-color, #fff); font-weight: 700; min-width: 150px; z-index: 2; }
    table.mgrid th.sat { color:#4D96FF; } table.mgrid th.sun { color:#FF6B6B; }
    table.mgrid td.wknd { background: rgba(255,107,53,.05); }
    table.mgrid td.cell { min-width: 46px; line-height: 1.25; }
    table.mgrid .in { color:#2E7D32; } table.mgrid .out { color:#C62828; } table.mgrid .rt { color:#F57C00; }
    table.mgrid tr.tot td { font-weight:800; background: rgba(77,150,255,.08); }
    table.mgrid td.rowtot { font-weight:800; background: rgba(107,203,119,.10); }
    </style>""", unsafe_allow_html=True)

    if "grid_ym" not in st.session_state:
        st.session_state["grid_ym"] = today_kst().strftime("%Y-%m")
    cp, ct, cn = st.columns([1, 2, 1])
    if cp.button("◀ 이전달", key="grid_prev"):
        y, m = map(int, st.session_state["grid_ym"].split("-"))
        m -= 1
        if m == 0: y, m = y - 1, 12
        st.session_state["grid_ym"] = f"{y:04d}-{m:02d}"; st.rerun()
    if cn.button("다음달 ▶", key="grid_next"):
        y, m = map(int, st.session_state["grid_ym"].split("-"))
        m += 1
        if m == 13: y, m = y + 1, 1
        st.session_state["grid_ym"] = f"{y:04d}-{m:02d}"; st.rerun()
    gy, gm = map(int, st.session_state["grid_ym"].split("-"))
    ct.markdown(f"<h3 style='text-align:center'>{gy}년 {gm}월</h3>", unsafe_allow_html=True)

    if "month_grid" not in globals():
        st.error("⚠️ core.py 가 예전 버전이라 격자표를 만들 수 없습니다. "
                 "**app.py 와 core.py 를 함께** 최신으로 올린 뒤 ⋮ → Reboot app 하세요.")
        st.stop()

    pivot, days, dtot, meta = month_grid(gy, gm)
    if pivot is None or pivot.empty:
        st.info("제품이 없습니다. [설정·관리 → 제품 관리]에서 먼저 등록하세요.")
    else:
        q = st.text_input("🔍 제품 검색", key="grid_search", placeholder="제품명 일부 입력")
        pv = pivot
        if q:
            pv = pivot[pivot["제품명"].str.contains(q, case=False, na=False, regex=False)]

        wk = ["월", "화", "수", "목", "금", "토", "일"]
        # 헤더
        html = ["<div class='grid-wrap'><table class='mgrid'><thead><tr>",
                "<th class='pname'>제품명</th>"]
        for d in days:
            wd = d.weekday()
            cls = "sat" if wd == 5 else ("sun" if wd == 6 else "")
            html.append(f"<th class='{cls}'>{d.day}<br><span style='font-size:.7rem'>{wk[wd]}</span></th>")
        html.append("<th class='rowtot'>합계</th></tr></thead><tbody>")
        # 본문
        for _, r in pv.iterrows():
            html.append("<tr>")
            tip = meta.get(r["제품명"], "")
            html.append(f"<td class='pname' title='{tip}'>{r['제품명']}"
                        + (f"<br><span style='font-size:.68rem;color:#888;font-weight:400'>{tip}</span>" if tip else "")
                        + "</td>")
            for d in days:
                ds = d.strftime("%Y-%m-%d")
                wknd = "wknd" if d.weekday() >= 5 else ""
                val = r.get(ds, "") or ""
                cellhtml = ""
                for line in str(val).split("\n"):
                    if line.startswith("+"):
                        cellhtml += f"<div class='in'>{line}</div>"
                    elif line.startswith("-"):
                        cellhtml += f"<div class='out'>{line}</div>"
                    elif line.startswith("↺"):
                        cellhtml += f"<div class='rt'>{line}</div>"
                html.append(f"<td class='cell {wknd}'>{cellhtml}</td>")
            html.append(f"<td class='rowtot'>{int(r['_순합계']):+,}</td></tr>")
        # 하단 일자별 합계 (순 = 입고-출고+반품)
        html.append("<tr class='tot'><td class='pname'>일자별 순증감</td>")
        for d in days:
            ds = d.strftime("%Y-%m-%d")
            i, o, rt = dtot.get(ds, [0, 0, 0])
            net = i - o + rt
            html.append(f"<td>{net:+,}</td>" if net else "<td></td>")
        html.append("<td class='rowtot'></td></tr>")
        html.append("</tbody></table></div>")
        st.markdown("".join(html), unsafe_allow_html=True)
        st.caption("셀 표시: <span style='color:#2E7D32'>+입고</span> · "
                   "<span style='color:#C62828'>-출고</span> · "
                   "<span style='color:#F57C00'>↺반품</span> (낱개환산) · 제품명 아래 = 소비기한 잔여 요약 · "
                   "토=파랑/일=빨강, 주말 음영", unsafe_allow_html=True)

        st.divider()
        st.markdown("#### ✏️ 특정 날짜 기록 추가·수정")
        st.caption("격자에서 고칠 칸의 날짜를 골라 팝업에서 그날 기록을 추가·수정·삭제하세요.")
        pick_day = st.date_input("날짜 선택", value=today_kst().replace(day=1) if gm != today_kst().month else today_kst(),
                                 key="grid_pick_day")
        if st.button("📆 그 날짜 기록 관리 열기", type="primary", key="grid_open_day"):
            open_day_dialog(pick_day.strftime("%Y-%m-%d"))


# ══════════════════════════════════════════════
# 재고 현황 — 제품별 재고(로트 합계) + 소비기한 로트
# ══════════════════════════════════════════════
elif page == "📦 재고 현황":
    st.title("📦 재고 현황")
    st.caption("재고는 소비기한 로트의 합계입니다. 수량 조정은 [입출고]에서 출고/입고로, 세부 로트는 [제품 관리 → 로트 직접 편집]에서.")

    prods = df_products()
    if prods.empty:
        st.info("제품을 먼저 등록하세요. (설정 → 제품 관리)")
    else:
        tab_p, tab_e = st.tabs(["📋 제품별 재고", "⏳ 소비기한별 로트"])

        with tab_p:
            v = prods.copy()
            v["현재고"] = v.apply(total_ea, axis=1)
            v["박스환산"] = v.apply(lambda r: fmt_stock_ea(int(r["현재고"]), r["box_qty"]), axis=1)
            v = v[["name", "barcode", "box_qty", "storage", "현재고", "박스환산"]]
            v.columns = ["제품명", "바코드", "박스입수량", "보관", "현재고(낱개환산)", "박스환산(자동)"]
            v = search_box(v, "stk_search", "🔍 제품 검색")
            st.dataframe(v, use_container_width=True, hide_index=True,
                         column_config={"현재고(낱개환산)": st.column_config.NumberColumn(
                             "🔵 현재고(낱개환산)")})
            csv_button(v, "재고현황", "csv_stock_now")

        with tab_e:
            bd = expiry_breakdown()
            if bd.empty:
                st.info("소비기한이 있는 재고 로트가 없습니다. [입출고]에서 입고 시 소비기한을 입력하세요.")
            else:
                bd2 = search_box(bd, "stk_exp_search", "🔍 제품 검색")

                def _hle(row):
                    try:
                        dd = (pd.Timestamp(str(row["소비기한"])).date() - today_kst()).days
                    except Exception:
                        return [""] * len(row)
                    if dd < 0:
                        return ["background-color: #FFEBEE"] * len(row)
                    if dd <= 30:
                        return ["background-color: #FFF8E1"] * len(row)
                    return [""] * len(row)

                st.dataframe(bd2.style.apply(_hle, axis=1), use_container_width=True, hide_index=True)
                st.caption("🟥 기한 경과 · 🟨 30일 이내 · 각 행 = 소비기한별 남은 수량(로트)")
                csv_button(bd2, "소비기한로트", "csv_stock_lots")


elif page == "📦 제품 관리(엑셀표)":
    st.title("📦 제품 관리 — 엑셀처럼 직접 수정")
    st.caption("셀을 터치/더블클릭해 수정 → [변경사항 저장]. 맨 아래 빈 줄에 입력하면 신규 제품 추가. 수정 내용은 변경이력에 자동 기록됩니다.")

    prods = df_products()

    # ── 재고 일별 추세 그래프 (수불부 기반) ──
    with st.expander("📈 재고 일별 추세 그래프", expanded=True):
        led = build_ledger()
        if led.empty or prods.empty:
            st.info("입고/출고 기록이 쌓이면 일별 재고 추세가 표시됩니다.")
        else:
            opts = prods["name"].tolist()
            _sel = st.session_state.get("detail_prod")
            default = [_sel] if _sel in opts else opts[:min(5, len(opts))]
            pick = st.multiselect("표시할 제품 (여러 개 선택 가능)", opts, default=default, key="trend_pick")
            if pick:
                sub = led[led["제품명"].isin(pick)]
                chart = sub.pivot_table(index="날짜", columns="제품명", values="누적재고", aggfunc="last")
                st.line_chart(chart)
                st.caption("세로축: 재고(총낱개환산) · 매일의 입고−출고가 누적된 값 · 마지막 점 = 현재 재고")

    # ── 아래 상세 드롭다운(담당제품)과 연동되는 표 필터 ──
    sel_detail = st.session_state.get("detail_prod")
    only_sel = st.toggle(
        "🔍 아래 상세에서 선택한 담당제품만 표에 표시"
        + (f" — 현재: **{sel_detail}**" if sel_detail else ""),
        value=st.session_state.get("only_sel_prod", False), key="only_sel_prod")
    prods_view = prods[prods["name"] == sel_detail] if (only_sel and sel_detail and not prods.empty) else prods
    pq = st.text_input("🔍 제품 검색", key="prod_search",
                       placeholder="제품명·바코드·규격 일부 입력 (예: 카스테라)")
    if pq and not prods_view.empty:
        m = (prods_view["name"].astype(str).str.contains(pq, case=False, na=False, regex=False)
             | prods_view["barcode"].astype(str).str.contains(pq, case=False, na=False, regex=False)
             | prods_view["spec"].astype(str).str.contains(pq, case=False, na=False, regex=False)
             | prods_view["memo"].astype(str).str.contains(pq, case=False, na=False, regex=False))
        prods_view = prods_view[m]

    grid_cols = ["id", "name", "barcode", "is_new", "box_qty", "spec", "normal_price", "sale_price",
                 "storage", "delivery_ea", "memo", "updated_at"]
    grid = prods_view[grid_cols].copy() if not prods_view.empty else pd.DataFrame(columns=grid_cols)
    # 현재고(낱개환산)는 로트 합계 → 읽기전용 표시
    _sk = stock_of()
    if not grid.empty:
        grid.insert(grid.columns.get_loc("memo"), "현재고", grid["id"].apply(lambda i: int(_sk.get(int(i), 0))))
    else:
        grid["현재고"] = pd.Series(dtype="int")

    edited = st.data_editor(
        grid,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=["id", "updated_at", "현재고"],
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "name": st.column_config.TextColumn("제품명", required=True),
            "barcode": st.column_config.TextColumn(
                "바코드(번호)", validate=r"^[0-9]*$",
                help="숫자만 입력 가능 (앞자리 0 보존을 위해 문자로 저장)"),
            "is_new": st.column_config.SelectboxColumn("구분", options=NEW_OLD_OPTIONS),
            "box_qty": st.column_config.NumberColumn("박스입수량", min_value=1, step=1),
            "spec": st.column_config.TextColumn("규격(무게)"),
            "normal_price": st.column_config.NumberColumn("정상가", format="%d원"),
            "sale_price": st.column_config.NumberColumn("할인판매가", format="%d원"),
            "storage": st.column_config.SelectboxColumn("보관방법", options=STORAGE_OPTIONS,
                                                        help="상온/냉장/냉동"),
            "delivery_ea": st.column_config.NumberColumn("납품갯수(낱개)", min_value=0, step=1),
            "현재고": st.column_config.NumberColumn("현재고(낱개환산)",
                help="로트 합계로 자동 계산됩니다. 재고 조정은 일일 기록(입고/출고) 또는 '🧺 로트 직접 편집'에서 하세요."),
            "memo": st.column_config.TextColumn("메모"),
            "updated_at": st.column_config.TextColumn("저장시각(자동)", help="이 행이 마지막으로 저장된 일시"),
        },
        key="prod_editor",
    )
    csv_button(grid.rename(columns=FIELD_LABELS), "제품관리표", "csv_prod")

    if st.button("💾 변경사항 저장", type="primary", use_container_width=True):
        # 필터 중에는 표시된 제품만 비교/삭제 대상 (숨겨진 제품은 안전하게 유지)
        old_map = {int(r["id"]): r for _, r in prods_view.iterrows()} if not prods_view.empty else {}
        existing_names = set(prods["name"].tolist()) if not prods.empty else set()
        seen_ids, changes, ops = set(), 0, []
        now = KST_NOW()

        for _, r in edited.iterrows():
            if pd.isna(r["name"]) or str(r["name"]).strip() == "":
                continue
            rid = r["id"]
            vals = {
                "name": str(r["name"]).strip(),
                "barcode": "" if pd.isna(r["barcode"]) else str(r["barcode"]).strip(),
                "is_new": r["is_new"] if r["is_new"] in NEW_OLD_OPTIONS else "신규",
                "box_qty": int(r["box_qty"]) if pd.notna(r["box_qty"]) else 1,
                "spec": "" if pd.isna(r["spec"]) else str(r["spec"]),
                "normal_price": int(r["normal_price"]) if pd.notna(r["normal_price"]) else 0,
                "sale_price": int(r["sale_price"]) if pd.notna(r["sale_price"]) else 0,
                "storage": r["storage"] if r["storage"] in STORAGE_OPTIONS else "상온",
                "delivery_ea": int(r["delivery_ea"]) if pd.notna(r["delivery_ea"]) else 0,
                "memo": "" if pd.isna(r["memo"]) else str(r["memo"]),
            }
            if pd.notna(rid) and int(rid) in old_map:  # 기존 행 수정
                rid = int(rid); seen_ids.add(rid)
                old = old_map[rid]
                def _norm(x):
                    if x is None or (isinstance(x, float) and pd.isna(x)):
                        return ""
                    if isinstance(x, (int, float)) and not isinstance(x, bool):
                        try:
                            return str(int(x))
                        except Exception:
                            return str(x)
                    return str(x).strip()
                diff = {k: v for k, v in vals.items()
                        if _norm(old[k] if k in old.index else None) != _norm(v)}
                if diff:
                    sets = ", ".join(f"{k}=:{k}" for k in diff)
                    ops.append((f"UPDATE products SET {sets}, updated_at=:ua WHERE id=:rid",
                                {**diff, "ua": now, "rid": rid}))
                    for k, v in diff.items():
                        ops.append(log_op(vals["name"], k, old[k], v))
                    changes += len(diff)
            else:  # 신규 행
                if vals["name"] in existing_names:
                    st.warning(f"'{vals['name']}' 은(는) 이미 존재하는 제품명이라 건너뛰었습니다.")
                    continue
                existing_names.add(vals["name"])
                cols = ", ".join(vals.keys())
                ph = ", ".join(f":{k}" for k in vals)
                ops.append((f"INSERT INTO products ({cols}, created_at, updated_at) VALUES ({ph}, :ca, :ua)",
                            {**vals, "ca": now, "ua": now}))
                ops.append(log_op(vals["name"], "name", "(신규등록)", vals["name"]))
                changes += 1

        for rid, old in old_map.items():
            if rid not in seen_ids:
                ops.append(("DELETE FROM products WHERE id=:i", {"i": rid}))
                ops.append(log_op(old["name"], "name", old["name"], "(삭제됨)"))
                changes += 1

        run_batch(ops)  # 모든 변경을 한 번의 트랜잭션으로 → 저장 속도 대폭 개선
        clear_cache("prod_editor")
        st.success(f"✅ 저장 완료 — 변경 {changes}건이 변경이력에 기록되었습니다.")
        st.rerun()

    # ── 🧺 로트 직접 편집 (재고 = 로트 합계이므로 여기서 소비기한·수량을 바로 고침) ──
    st.divider()
    with st.expander("🧺 로트(소비기한별 재고) 직접 편집", expanded=False):
        st.caption("재고는 이 로트들의 합계입니다. 소비기한·수량을 직접 고치거나, 행을 추가/삭제하면 재고에 바로 반영됩니다.")
        lots_all = df_lots()
        lot_prod = st.selectbox("제품 선택", ["(전체)"] + prods["name"].tolist(), key="lot_edit_prod")
        lv = lots_all if lot_prod == "(전체)" else lots_all[lots_all["제품명"] == lot_prod]
        if lv.empty:
            st.info("표시할 로트가 없습니다. 일일 기록에서 입고를 추가하면 로트가 생성됩니다.")
        else:
            grid = lv[["id", "제품명", "소비기한", "입고일", "잔여낱개"]].copy()
            grid["소비기한"] = pd.to_datetime(grid["소비기한"].replace("", pd.NA), errors="coerce")
            grid["입고일"] = pd.to_datetime(grid["입고일"].replace("", pd.NA), errors="coerce")
            ed = st.data_editor(
                grid, hide_index=True, use_container_width=True, num_rows="fixed",
                disabled=["id", "제품명"],
                column_config={
                    "id": st.column_config.NumberColumn("ID", width="small"),
                    "소비기한": st.column_config.DateColumn("소비기한", format="YYYY-MM-DD"),
                    "입고일": st.column_config.DateColumn("입고일", format="YYYY-MM-DD"),
                    "잔여낱개": st.column_config.NumberColumn("잔여(낱개환산)", min_value=0, step=1),
                }, key="lot_editor")
            if st.button("💾 로트 저장", type="primary", key="lot_save"):
                def _d(v):
                    return "" if pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%d")
                omap = {int(r["id"]): r for _, r in lv.iterrows()}
                ops, n = [], 0
                for _, r in ed.iterrows():
                    rid = int(r["id"]); o = omap.get(rid)
                    if o is None:
                        continue
                    ne, ie, qq = _d(r["소비기한"]), _d(r["입고일"]), int(r["잔여낱개"] or 0)
                    if (str(o["소비기한"] or ""), str(o["입고일"] or ""), int(o["잔여낱개"])) != (ne, ie, qq):
                        if qq <= 0:
                            ops.append(("DELETE FROM lots WHERE id=:i", {"i": rid}))
                        else:
                            ops.append(("UPDATE lots SET expiry_date=:e, in_date=:d, qty_ea=:q, updated_at=:u WHERE id=:i",
                                        dict(e=ne, d=ie, q=qq, u=KST_NOW(), i=rid)))
                        add_log(str(o["제품명"]), "로트수정",
                                f"기한 {o['소비기한'] or '없음'} / {int(o['잔여낱개'])}낱개",
                                f"기한 {ne or '없음'} / {qq}낱개")
                        n += 1
                if n == 0:
                    st.info("변경된 내용이 없습니다.")
                else:
                    run_batch(ops)
                    clear_cache("lot_editor")
                    st.success(f"✅ 로트 {n}건 반영 완료 — 재고에 즉시 반영됩니다.")
                    st.rerun()

    # ── 제품 상세: 이미지 / 구성품 / 납품처 ──
    st.divider()
    st.subheader("🔍 제품 상세 (이미지 · 낱개 구성품 · 납품 매장)")
    prods = df_products()
    if prods.empty:
        st.info("제품을 먼저 등록하세요.")
    else:
        sel = st.selectbox("제품 선택 (담당제품)", prods["name"].tolist(), key="detail_prod",
                           help="여기서 제품을 고르고 위의 토글을 켜면 표에 이 제품만 표시됩니다")
        prow = prods[prods["name"] == sel].iloc[0]
        pid = int(prow["id"])

        col_img, col_detail = st.columns([1, 2])
        with col_img:
            img = qdf("SELECT image_data FROM products WHERE id=:i", i=pid)
            if not img.empty and img.iloc[0]["image_data"] is not None:
                st.image(bytes(img.iloc[0]["image_data"]), width=220, caption=sel)
            up = st.file_uploader("제품 이미지 업로드", type=["png", "jpg", "jpeg", "webp"], key=f"img{pid}")
            if up is not None:
                run_batch([
                    ("UPDATE products SET image_name=:n, image_data=:d, updated_at=:u WHERE id=:i",
                     dict(n=up.name, d=up.getvalue(), u=KST_NOW(), i=pid)),
                    log_op(sel, "image_name", prow["image_name"] or "(없음)", up.name)])
                clear_cache()
                st.rerun()

        with col_detail:
            st.markdown("**들어가는 낱개 품목** (세트/박스 구성)")
            items = qdf("SELECT id, item_name, qty FROM product_items WHERE product_id=:p ORDER BY id", p=pid)
            items_edit = st.data_editor(
                items.rename(columns={"item_name": "낱개품목명", "qty": "수량"}),
                num_rows="dynamic", hide_index=True, use_container_width=True,
                disabled=["id"], key=f"items{pid}",
                column_config={"id": st.column_config.NumberColumn("ID", width="small")})
            if st.button("구성품 저장", key=f"items_save{pid}"):
                ops = [("DELETE FROM product_items WHERE product_id=:p", {"p": pid})]
                names = []
                for _, ir in items_edit.iterrows():
                    nm = str(ir["낱개품목명"]).strip() if pd.notna(ir["낱개품목명"]) else ""
                    if nm:
                        q = int(ir["수량"]) if pd.notna(ir["수량"]) else 1
                        ops.append(("INSERT INTO product_items (product_id, item_name, qty) VALUES (:p, :n, :q)",
                                    dict(p=pid, n=nm, q=q)))
                        names.append(f"{nm}x{q}")
                ops.append(log_op(sel, "구성품", "(수정 전)", ", ".join(names) or "(없음)"))
                run_batch(ops)
                clear_cache(f"items{pid}")
                st.success("구성품 저장 완료")

            st.markdown("**납품 매장 (매장명·납품개소)**")
            stores = df_stores()
            if stores.empty:
                st.info("[납품처 관리]에서 매장을 먼저 등록하세요.")
            else:
                cur = qdf("SELECT store_id FROM product_stores WHERE product_id=:p", p=pid)["store_id"].tolist()
                cur_names = stores[stores["id"].isin(cur)]["name"].tolist()
                new_names = st.multiselect("납품 중인 매장", stores["name"].tolist(),
                                           default=cur_names, key=f"ps{pid}")
                if st.button("납품 매장 저장", key=f"ps_save{pid}"):
                    ops = [("DELETE FROM product_stores WHERE product_id=:p", {"p": pid})]
                    for nm in new_names:
                        sid = int(stores[stores["name"] == nm].iloc[0]["id"])
                        ops.append(("INSERT INTO product_stores (product_id, store_id) VALUES (:p, :s) "
                                    "ON CONFLICT DO NOTHING", dict(p=pid, s=sid)))
                    ops.append(log_op(sel, "납품매장", ", ".join(cur_names) or "(없음)",
                                      ", ".join(new_names) or "(없음)"))
                    run_batch(ops)
                    clear_cache()
                    st.success("납품 매장 저장 완료")


# ══════════════════════════════════════════════
# 4. 납품처 관리 — 엑셀형 그리드
# ══════════════════════════════════════════════
elif page == "🏬 납품처 관리(엑셀표)":
    st.title("🏬 납품처(매장) 관리 — 엑셀처럼 직접 수정")
    st.caption("셀을 터치/더블클릭해 수정하고 [저장]. 맨 아래 빈 줄에 입력하면 신규 매장 추가.")
    stores = df_stores()
    grid_cols = ["id", "name", "region", "location", "delivery_day", "phone", "memo", "note"]
    grid = stores[grid_cols].copy() if not stores.empty else pd.DataFrame(columns=grid_cols)
    # DB의 "화,금" 문자열 → 다중선택용 리스트로 변환
    grid["delivery_day"] = grid["delivery_day"].apply(
        lambda s: [d for d in str(s).split(",") if d in DAY_OPTIONS] if isinstance(s, str) else [])
    edited = st.data_editor(
        grid, num_rows="dynamic", hide_index=True, use_container_width=True,
        disabled=["id"],
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "name": st.column_config.TextColumn("매장명", required=True),
            "region": st.column_config.TextColumn(
                "지역(그룹)", help="일일기록에서 묶어 출고할 그룹명. 예: 인천 / 경기서울 — 같은 이름끼리 한 그룹"),
            "location": st.column_config.TextColumn("납품개소/주소"),
            "delivery_day": st.column_config.MultiselectColumn(
                "납품요일", options=DAY_OPTIONS,
                help="이 지점에 물건이 나가는 요일을 모두 선택 (예: 화·금)"),
            "phone": st.column_config.TextColumn(
                "점주 전화번호", validate=r"^[0-9\-\s]*$",
                help="숫자와 하이픈(-)만 입력. 예: 010-1234-5678"),
            "memo": st.column_config.TextColumn("메모"),
            "note": st.column_config.TextColumn("특이사항"),
        }, key="store_editor")
    export_df = grid.rename(columns={"name": "매장명", "region": "지역", "location": "납품개소",
                                     "delivery_day": "납품요일", "phone": "점주전화번호",
                                     "memo": "메모", "note": "특이사항"}).copy()
    export_df["납품요일"] = export_df["납품요일"].apply(
        lambda v: ",".join(v) if isinstance(v, list) else ("" if pd.isna(v) else str(v)))
    c_csv, c_png = st.columns(2)
    with c_csv:
        csv_button(export_df, "납품처", "csv_store")
    with c_png:
        if not export_df.empty:
            st.download_button("🖼️ 표 PNG로 저장 (요일 색상 포함)",
                               data=table_png(export_df.drop(columns=["id"], errors="ignore")),
                               file_name=f"납품처_{TODAY()}.png", mime="image/png", key="png_store")
    _legend = "  ".join(f":{c}[**{d}**]" for d, c in
                        [("월", "red"), ("화", "orange"), ("수", "green"), ("목", "violet"), ("금", "blue")])
    st.caption("요일 색상: " + _legend + " · 토=청록 / 일=주황 / 매일=회색")

    if st.button("💾 저장", type="primary", use_container_width=True):
        old_map = {int(r["id"]): r for _, r in stores_view.iterrows()} if not stores_view.empty else {}
        existing_store_names = set(stores["name"].tolist()) if not stores.empty else set()
        seen, ops = set(), []
        for _, r in edited.iterrows():
            if pd.isna(r["name"]) or not str(r["name"]).strip():
                continue
            nm = str(r["name"]).strip()
            loc = "" if pd.isna(r["location"]) else str(r["location"])
            raw = r["delivery_day"]
            if isinstance(raw, (list, tuple)):
                days = [d for d in raw if d in DAY_OPTIONS]
            elif isinstance(raw, str):
                days = [d for d in raw.split(",") if d in DAY_OPTIONS]
            else:
                days = []
            dd = ",".join(sorted(set(days), key=DAY_OPTIONS.index))  # 예: "화,금"
            ph = "" if pd.isna(r["phone"]) else str(r["phone"]).strip()
            mm = "" if pd.isna(r["memo"]) else str(r["memo"])
            nt = "" if pd.isna(r["note"]) else str(r["note"])
            rg = "" if pd.isna(r["region"]) else str(r["region"]).strip()
            if pd.notna(r["id"]) and int(r["id"]) in old_map:
                rid = int(r["id"]); seen.add(rid)
                old = old_map[rid]
                if (old["name"], old["location"], old["region"], old["delivery_day"], old["phone"], old["memo"], old["note"]) != (nm, loc, rg, dd, ph, mm, nt):
                    ops.append(("UPDATE stores SET name=:n, location=:l, region=:rg, delivery_day=:d, phone=:p, memo=:m, note=:nt WHERE id=:i",
                                dict(n=nm, l=loc, rg=rg, d=dd, p=ph, m=mm, nt=nt, i=rid)))
                    ops.append(log_op(f"[매장] {nm}", "매장정보",
                                      f"{old['name']} / {old['location']} / {old['delivery_day'] or '요일미지정'} / {old['phone'] or '번호없음'}",
                                      f"{nm} / {loc} / {dd or '요일미지정'} / {ph or '번호없음'}"))
            else:
                if nm in existing_store_names:
                    st.warning(f"'{nm}' 매장은 이미 존재합니다.")
                    continue
                existing_store_names.add(nm)
                ops.append(("INSERT INTO stores (name, location, region, delivery_day, phone, memo, note) VALUES (:n, :l, :rg, :d, :p, :m, :nt)",
                            dict(n=nm, l=loc, rg=rg, d=dd, p=ph, m=mm, nt=nt)))
                ops.append(log_op(f"[매장] {nm}", "매장정보", "(신규등록)", f"{nm} / {loc} / {dd or '요일미지정'}"))
        for rid, old in old_map.items():
            if rid not in seen:
                ops.append(("DELETE FROM stores WHERE id=:i", {"i": rid}))
                ops.append(log_op(f"[매장] {old['name']}", "매장정보", old["name"], "(삭제됨)"))
        run_batch(ops)
        clear_cache("store_editor")
        st.success("저장 완료")
        st.rerun()

    st.divider()
    st.subheader("매장별 납품 제품 조회 — 제품명 묶음(피벗)")
    ps = qdf(
        """SELECT s.name AS 매장명, s.location AS 납품개소, s.delivery_day AS 납품요일,
                  s.phone AS 점주전화번호, p.name AS 제품명
           FROM product_stores x
           JOIN stores s ON s.id = x.store_id
           JOIN products p ON p.id = x.product_id
           ORDER BY s.name, p.name""")
    if ps.empty:
        st.info("제품 관리 상세에서 제품별 납품 매장을 지정하면 여기에 표시됩니다.")
    else:
        grouped = (ps.groupby(["매장명", "납품개소", "납품요일", "점주전화번호"], dropna=False)["제품명"]
                     .agg(lambda s: ", ".join(s)).reset_index()
                     .rename(columns={"제품명": "납품 제품"}))
        grouped.insert(1, "제품수", grouped["납품 제품"].apply(lambda v: v.count(",") + 1 if v else 0))
        grouped = grouped[["매장명", "납품요일", "제품수", "납품 제품", "납품개소", "점주전화번호"]]
        grouped = search_box(grouped, "search_group", "🔍 매장·제품 검색")
        st.dataframe(grouped, use_container_width=True, hide_index=True,
                     column_config={"납품 제품": st.column_config.TextColumn("납품 제품", width="large")})

        c_csv2, c_png2 = st.columns(2)
        with c_csv2:
            csv_button(grouped, "매장별납품제품_묶음", "csv_ps_group")
        with c_png2:
            # PNG용: 긴 제품 목록은 줄바꿈 처리
            import textwrap
            png_df = grouped.copy()
            png_df["납품 제품"] = png_df["납품 제품"].apply(
                lambda v: "\n".join(textwrap.wrap(str(v), width=38)) or "")
            st.download_button("🖼️ 묶음표 PNG로 저장 (요일 색상 포함)",
                               data=table_png(png_df),
                               file_name=f"매장별납품제품_{TODAY()}.png", mime="image/png",
                               key="png_ps_group")

        with st.expander("행별 상세 보기 (매장×제품 1행씩)"):
            st.dataframe(ps, use_container_width=True, hide_index=True)
            csv_button(ps, "매장별납품제품_상세", "csv_ps")


# ══════════════════════════════════════════════
# 5. 납품 정리표 — 매장×제품 수량
# ══════════════════════════════════════════════
elif page == "📋 납품 정리표(매장×제품)":
    st.title("📋 납품 정리표 — 매장별 · 제품별 수량")
    st.caption("어느 매장에 어떤 제품이 몇 개 들어가야 하는지 정리하는 표입니다. 엑셀처럼 수정하고 [저장]하면 유지됩니다.")

    prods = df_products()
    stores = df_stores()
    if prods.empty or stores.empty:
        st.warning("제품과 납품처(매장)를 먼저 등록하세요.")
    else:
        plan = df_plan()
        mode = st.radio("입력 방식", ["🏬 매장별 일괄 입력 (한 매장에 여러 제품 한 번에)", "✏️ 전체 편집 (행 단위)"],
                        horizontal=True, label_visibility="collapsed")

        # ═══ 모드 A: 매장 하나 선택 → 전체 제품 수량을 한 표에서 입력 ═══
        if mode.startswith("🏬"):
            sel_store = st.selectbox("매장 선택", stores["name"].tolist(), key="bulk_store")
            srow = stores[stores["name"] == sel_store].iloc[0]
            sid = int(srow["id"])
            st.caption(f"'{sel_store}' 에 들어갈 제품별 수량을 입력하세요. 0/0에 메모도 없으면 정리표에서 빠집니다."
                       + (f" (납품요일: {srow['delivery_day']})" if srow["delivery_day"] else ""))

            # 기존 정리표 값 불러와 전체 제품 목록에 병합
            cur = plan[plan["매장명"] == sel_store][["제품명", "박스", "낱개", "메모"]] if not plan.empty \
                else pd.DataFrame(columns=["제품명", "박스", "낱개", "메모"])
            bulk = prods[["name", "box_qty"]].rename(columns={"name": "제품명", "box_qty": "박스입수량"}).copy()
            bulk = bulk.merge(cur, on="제품명", how="left")
            bulk["박스"] = bulk["박스"].fillna(0).astype(int)
            bulk["낱개"] = bulk["낱개"].fillna(0).astype(int)
            bulk["메모"] = bulk["메모"].fillna("")
            bulk["환산낱개"] = bulk["박스"] * bulk["박스입수량"].clip(lower=1) + bulk["낱개"]

            bq_search = st.text_input("🔍 제품 검색", key=f"bulk_search_{sid}",
                                      placeholder="제품명 일부 입력 → 해당 제품만 표시")
            if bq_search:
                bulk = bulk[bulk["제품명"].astype(str).str.contains(bq_search, case=False, na=False, regex=False)]
            bulk_edit = st.data_editor(
                bulk, hide_index=True, use_container_width=True,
                disabled=["제품명", "박스입수량", "환산낱개"],
                column_config={
                    "제품명": st.column_config.TextColumn("제품명"),
                    "박스입수량": st.column_config.NumberColumn("박스입수량", width="small"),
                    "박스": st.column_config.NumberColumn("수량(박스)", min_value=0, step=1),
                    "낱개": st.column_config.NumberColumn("수량(낱개)", min_value=0, step=1),
                    "환산낱개": st.column_config.NumberColumn("환산낱개(저장시 계산)", width="small"),
                    "메모": st.column_config.TextColumn("메모"),
                }, key=f"bulk_editor_{sid}")

            c_save, c_csv = st.columns([2, 1])
            with c_csv:
                csv_button(bulk_edit.drop(columns=["환산낱개"]), f"정리표_{sel_store}", "csv_bulk")
            with c_save:
                if st.button(f"💾 '{sel_store}' 정리표 저장", type="primary", use_container_width=True):
                    pid_map = dict(zip(prods["name"], prods["id"]))
                    old_map = {r["제품명"]: r for _, r in cur.iterrows()}
                    ops, changes = [], 0
                    for _, r in bulk_edit.iterrows():
                        pname = r["제품명"]
                        qb = int(r["박스"]) if pd.notna(r["박스"]) else 0
                        qe = int(r["낱개"]) if pd.notna(r["낱개"]) else 0
                        mm = "" if pd.isna(r["메모"]) else str(r["메모"])
                        old = old_map.get(pname)
                        if qb == 0 and qe == 0 and not mm:
                            if old is not None:  # 기존 항목 → 제거
                                ops.append(("DELETE FROM store_product_qty WHERE store_id=:s AND product_id=:p",
                                            dict(s=sid, p=int(pid_map[pname]))))
                                ops.append(log_op(f"[정리표] {sel_store} × {pname}", "납품수량",
                                                  f"박스 {old['박스']} / 낱개 {old['낱개']}", "(삭제됨)"))
                                changes += 1
                            continue
                        if old is None or (int(old["박스"]), int(old["낱개"]), str(old["메모"])) != (qb, qe, mm):
                            ops.append(("""INSERT INTO store_product_qty (store_id, product_id, qty_box, qty_ea, memo, updated_at)
                                   VALUES (:s, :p, :qb, :qe, :m, :u)
                                   ON CONFLICT (store_id, product_id)
                                   DO UPDATE SET qty_box=:qb, qty_ea=:qe, memo=:m, updated_at=:u""",
                                        dict(s=sid, p=int(pid_map[pname]), qb=qb, qe=qe, m=mm, u=KST_NOW())))
                            ops.append(log_op(f"[정리표] {sel_store} × {pname}", "납품수량",
                                              "(신규)" if old is None else f"박스 {old['박스']} / 낱개 {old['낱개']}",
                                              f"박스 {qb} / 낱개 {qe}"))
                            changes += 1
                    run_batch(ops)
                    clear_cache(f"bulk_editor_{sid}")
                    st.success(f"✅ '{sel_store}' 정리표 저장 완료 — 변경 {changes}건")
                    st.rerun()

        # ═══ 모드 B: 기존 행 단위 전체 편집 ═══
        else:
            plq = st.text_input("🔍 정리표 검색", key="plan_search", placeholder="매장명·제품명 일부 입력")
            plan_view = plan
            if plq and not plan.empty:
                m = (plan["매장명"].astype(str).str.contains(plq, case=False, na=False, regex=False)
                     | plan["제품명"].astype(str).str.contains(plq, case=False, na=False, regex=False))
                plan_view = plan[m]
            grid_cols = ["id", "매장명", "제품명", "박스", "낱개", "메모"]
            grid = plan_view[grid_cols].copy() if not plan_view.empty else pd.DataFrame(columns=grid_cols)

            edited = st.data_editor(
                grid, num_rows="dynamic", hide_index=True, use_container_width=True,
                disabled=["id"],
                column_config={
                    "id": st.column_config.NumberColumn("ID", width="small"),
                    "매장명": st.column_config.SelectboxColumn("매장명", options=stores["name"].tolist(), required=True),
                    "제품명": st.column_config.SelectboxColumn("제품명", options=prods["name"].tolist(), required=True),
                    "박스": st.column_config.NumberColumn("수량(박스)", min_value=0, step=1),
                    "낱개": st.column_config.NumberColumn("수량(낱개)", min_value=0, step=1),
                    "메모": st.column_config.TextColumn("메모"),
                }, key="plan_editor")
            csv_button(edited, "납품정리표", "csv_plan")

            if st.button("💾 정리표 저장", type="primary", use_container_width=True):
                sid_map = dict(zip(stores["name"], stores["id"]))
                pid_map = dict(zip(prods["name"], prods["id"]))
                old_map = {}
                if not plan.empty:
                    for _, r in plan.iterrows():
                        old_map[(r["매장명"], r["제품명"])] = r

                new_keys, changes, ops = set(), 0, []
                for _, r in edited.iterrows():
                    if pd.isna(r["매장명"]) or pd.isna(r["제품명"]):
                        continue
                    sname, pname = str(r["매장명"]), str(r["제품명"])
                    if sname not in sid_map or pname not in pid_map:
                        continue
                    key = (sname, pname)
                    if key in new_keys:
                        st.warning(f"'{sname} × {pname}' 이 중복 입력되어 첫 행만 저장했습니다.")
                        continue
                    new_keys.add(key)
                    qb = int(r["박스"]) if pd.notna(r["박스"]) else 0
                    qe = int(r["낱개"]) if pd.notna(r["낱개"]) else 0
                    mm = "" if pd.isna(r["메모"]) else str(r["메모"])
                    old = old_map.get(key)
                    if old is None or (int(old["박스"]), int(old["낱개"]), str(old["메모"])) != (qb, qe, mm):
                        ops.append(("""INSERT INTO store_product_qty (store_id, product_id, qty_box, qty_ea, memo, updated_at)
                               VALUES (:s, :p, :qb, :qe, :m, :u)
                               ON CONFLICT (store_id, product_id)
                               DO UPDATE SET qty_box=:qb, qty_ea=:qe, memo=:m, updated_at=:u""",
                            dict(s=int(sid_map[sname]), p=int(pid_map[pname]),
                                 qb=qb, qe=qe, m=mm, u=KST_NOW())))
                        ops.append(log_op(f"[정리표] {sname} × {pname}", "납품수량",
                                          "(신규)" if old is None else f"박스 {old['박스']} / 낱개 {old['낱개']}",
                                          f"박스 {qb} / 낱개 {qe}"))
                        changes += 1

                for key, old in old_map.items():
                    if key not in new_keys:
                        ops.append(("DELETE FROM store_product_qty WHERE id=:i", {"i": int(old["id"])}))
                        ops.append(log_op(f"[정리표] {key[0]} × {key[1]}", "납품수량",
                                          f"박스 {old['박스']} / 낱개 {old['낱개']}", "(삭제됨)"))
                        changes += 1

                run_batch(ops)
                clear_cache()
                st.success(f"✅ 정리표 저장 완료 — 변경 {changes}건 기록")
                st.rerun()

        # ── 조회 ──
        st.divider()
        plan = df_plan()
        if plan.empty:
            st.info("정리표에 데이터를 입력하면 아래에서 매장별/제품별로 볼 수 있습니다.")
        else:
            tab1, tab2, tab3 = st.tabs(["🏬 매장별 보기", "📦 제품별 보기", "🗂️ 전체 매트릭스"])
            with tab1:
                s_sel = st.selectbox("매장 선택", sorted(plan["매장명"].unique()))
                sub = plan[plan["매장명"] == s_sel][["제품명", "박스", "낱개", "환산낱개", "메모"]]
                st.dataframe(sub, use_container_width=True, hide_index=True)
                st.caption(f"{s_sel} — 제품 {len(sub)}종 / 합계 박스 {sub['박스'].sum()} · 낱개 {sub['낱개'].sum()} · 환산낱개 {sub['환산낱개'].sum()}")
            with tab2:
                p_sel = st.selectbox("제품 선택", sorted(plan["제품명"].unique()))
                sub = plan[plan["제품명"] == p_sel][["매장명", "박스", "낱개", "환산낱개", "메모"]]
                st.dataframe(sub, use_container_width=True, hide_index=True)
                st.caption(f"{p_sel} — 매장 {len(sub)}곳 / 합계 박스 {sub['박스'].sum()} · 낱개 {sub['낱개'].sum()} · 환산낱개 {sub['환산낱개'].sum()}")
            with tab3:
                mat = plan.pivot_table(index="제품명", columns="매장명", values="박스",
                                       aggfunc="sum", fill_value=0)
                st.caption("행=제품, 열=매장, 값=수량(박스)")
                st.dataframe(mat, use_container_width=True)


# ══════════════════════════════════════════════
# 교체·발주 일정 — 소비기한 역산 + 달력
# ══════════════════════════════════════════════
elif page == "📅 교체·발주 일정":
    st.title("📅 교체·발주 일정 (소비기한 역산)")
    st.caption("소비기한 → 교체마감일 → 마지막 교체 납품일(매장 요일) → 발주마감(D-2 · 11:30 KST)을 자동 계산합니다.")

    with st.expander("⚙️ 기준 설정", expanded=False):
        c1, c2, c3 = st.columns(3)
        buf = c1.number_input("여유일 (소비기한 며칠 전까지 교체)", 0, 30,
                              int(get_setting("buffer_days", "2")), key="set_buf")
        cutd = c2.number_input("발주 마감 (납품일 며칠 전)", 0, 14,
                               int(get_setting("cutoff_days", "2")), key="set_cutd")
        cutt = c3.text_input("발주 마감 시각 (HH:MM, 한국시간)",
                             get_setting("cutoff_time", "11:30"), key="set_cutt")
        if st.button("💾 설정 저장", key="set_save"):
            set_setting("buffer_days", int(buf))
            set_setting("cutoff_days", int(cutd))
            set_setting("cutoff_time", cutt.strip() or "11:30")
            clear_cache()
            st.success("설정 저장 완료")
            st.rerun()

    buf = int(get_setting("buffer_days", "2"))
    cutd = int(get_setting("cutoff_days", "2"))
    cutt = get_setting("cutoff_time", "11:30")
    st.info(f"현재 기준: 소비기한 **{buf}일 전**까지 교체 · 발주는 납품일 **{cutd}일 전 {cutt}(KST)** 까지 "
            f"(예: 인천 월·목 / 경기서울 화·금 납품)")

    sched = replacement_schedule(buf, cutd, cutt)
    if sched.empty:
        st.info("소비기한이 입력된 재고 로트가 없거나, 제품에 납품 매장이 지정되지 않았습니다. "
                "[제품 관리 → 상세 → 납품 매장]과 [납품처 관리 → 납품요일]을 설정하세요.")
    else:
        view = sched.drop(columns=["_cutoff", "_L"], errors="ignore")
        view = search_box(view, "search_sched", "🔍 제품·매장 검색")

        def _hl_sched(row):
            s = str(row["상태"])
            if s.startswith(("🚨", "⛔")):
                return ["background-color: #FFEBEE"] * len(row)
            if s.startswith("🔥"):
                return ["background-color: #FFE0B2"] * len(row)
            if s.startswith("⏰"):
                return ["background-color: #FFF8E1"] * len(row)
            return [""] * len(row)

        st.dataframe(view.style.apply(_hl_sched, axis=1), use_container_width=True, hide_index=True)
        csv_button(view, "교체발주일정", "csv_sched")

        # ── 달력 보기 (공용 함수) ──
        st.divider()
        render_schedule_calendar(sched, cutt, key_prefix="sched")


# ══════════════════════════════════════════════
# 일자별 메모 — 독립 테이블(daily_memos), 날짜당 1건
# ══════════════════════════════════════════════
elif page == "🗒️ 일자별 메모":
    st.title("🗒️ 일자별 메모")
    st.caption("재고·발주와 별개로 그날그날 남기는 업무 메모입니다. 날짜당 한 건이며, 같은 날 다시 저장하면 덮어씁니다.")

    mdate = st.date_input("날짜", value=today_kst(), key="memo_date")
    mkey = mdate.strftime("%Y-%m-%d")
    cur = qdf("SELECT content FROM daily_memos WHERE mdate = :d", d=mkey)
    cur_text = cur.iloc[0]["content"] if not cur.empty else ""

    content = st.text_area("메모 내용", value=cur_text, height=220, key=f"memo_{mkey}",
                           placeholder="예) 가산점 점주 통화 — 다음주 화요일 물량 2배 요청 / 냉동차 예약 완료")

    c_save, c_del = st.columns([3, 1])
    with c_save:
        if st.button("💾 메모 저장", type="primary", use_container_width=True, key="memo_save"):
            run("""INSERT INTO daily_memos (mdate, content, created_at, updated_at)
                   VALUES (:d, :c, :t, :t)
                   ON CONFLICT (mdate) DO UPDATE SET content = :c, updated_at = :t""",
                d=mkey, c=content, t=KST_NOW())
            clear_cache()
            st.success(f"✅ {mkey} 메모 저장 완료")
            st.rerun()
    with c_del:
        if st.button("🗑️ 이 날짜 삭제", use_container_width=True, key="memo_del",
                     disabled=cur.empty):
            run("DELETE FROM daily_memos WHERE mdate = :d", d=mkey)
            clear_cache()
            st.success(f"{mkey} 메모 삭제 완료")
            st.rerun()

    st.divider()
    st.subheader("메모 목록")
    c1, c2 = st.columns(2)
    d1 = c1.date_input("시작일", value=today_kst().replace(day=1), key="memo_d1")
    d2 = c2.date_input("종료일", value=today_kst(), key="memo_d2")
    memos = df_memos(d1.strftime("%Y-%m-%d"), d2.strftime("%Y-%m-%d"))
    if memos.empty:
        st.info("이 기간에 저장된 메모가 없습니다.")
    else:
        st.dataframe(memos, use_container_width=True, hide_index=True,
                     column_config={"메모": st.column_config.TextColumn("메모", width="large")})
        csv_button(memos, "일자별메모", "csv_memos")



# ══════════════════════════════════════════════
# 6. 변경이력
# ══════════════════════════════════════════════
elif page == "📜 변경이력":
    st.title("📜 변경이력 (날짜별 자동 기록)")
    c1, c2 = st.columns(2)
    d1 = c1.date_input("시작일", value=today_kst().replace(day=1))
    d2 = c2.date_input("종료일", value=today_kst())
    logs = df_logs(d1.strftime("%Y-%m-%d"), d2.strftime("%Y-%m-%d"))
    logs = search_box(logs, "search_logs", "🔍 이력 검색 (제품/항목/값)")
    st.dataframe(logs, use_container_width=True, hide_index=True)
    st.caption(f"총 {len(logs)}건")
    csv_button(logs, "변경이력", "csv_logs")


# ══════════════════════════════════════════════
# 6. 엑셀 내보내기
# ══════════════════════════════════════════════
elif page == "⬇️ 엑셀 내보내기":
    st.title("⬇️ 엑셀 내보내기")
    st.caption("제품현황 · 입출고이력 · 변경이력 · 납품처 · 구성품이 시트별로 담긴 엑셀 파일을 내려받습니다.")

    scope = st.radio("범위", ["전체 기간", "오늘만", "기간 지정"], horizontal=True)
    d1 = d2 = None
    if scope == "오늘만":
        d1 = d2 = TODAY()
    elif scope == "기간 지정":
        c1, c2 = st.columns(2)
        d1 = c1.date_input("시작일", value=today_kst().replace(day=1)).strftime("%Y-%m-%d")
        d2 = c2.date_input("종료일", value=today_kst()).strftime("%Y-%m-%d")

    st.download_button("📥 엑셀 다운로드", data=build_excel(d1, d2),
                       file_name=f"재고관리_{TODAY()}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       type="primary", use_container_width=True)

    st.divider()
    st.subheader("미리보기 — 변경이력")
    st.dataframe(df_logs(d1, d2).head(20), use_container_width=True, hide_index=True)
    st.subheader("미리보기 — 입출고이력")
    st.dataframe(df_transactions(d1, d2).drop(columns=["id"], errors="ignore").head(20),
                 use_container_width=True, hide_index=True)
