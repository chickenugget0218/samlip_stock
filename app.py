# -*- coding: utf-8 -*-
"""
삼립 재고·발주·유통 관리 (클라우드 버전)
- 호스팅: Streamlit Community Cloud (무료)
- DB: Supabase PostgreSQL (무료)  →  컴퓨터 꺼도 폰에서 접속 가능
- 접속 시 비밀번호 입력 필요 (Secrets의 APP_PASSWORD)
- 제품 이미지는 DB에 함께 저장되어 별도 스토리지 설정이 필요 없습니다.

[Secrets 설정 예시 - Streamlit Cloud > App > Settings > Secrets]
DB_URL = "postgresql+psycopg2://postgres.xxxx:비밀번호@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres"
APP_PASSWORD = "원하는접속비밀번호"
"""
import os
import io
from datetime import datetime, date

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

# ──────────────────────────────────────────────
# 기본 설정
# ──────────────────────────────────────────────
KST_NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
TODAY = lambda: date.today().strftime("%Y-%m-%d")

STORAGE_OPTIONS = ["상온", "냉장", "냉동"]
NEW_OLD_OPTIONS = ["신규", "기존"]
TTYPE_OPTIONS = ["입고", "출고", "발주"]
DAY_OPTIONS = ["월", "화", "수", "목", "금", "토", "일", "매일"]
KOR_WEEKDAY = ["월", "화", "수", "목", "금", "토", "일"]  # date.weekday() 매핑

st.set_page_config(page_title="아람비즈 재고·발주 관리", page_icon="📦", layout="wide")


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
    st.title("🔒 삼립 무인편의점 재고·발주 관리")
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


# ──────────────────────────────────────────────
# DB 연결 및 초기화
# ──────────────────────────────────────────────
DDL = """
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    barcode TEXT DEFAULT '',
    box_qty INTEGER DEFAULT 1,
    spec TEXT DEFAULT '',
    normal_price INTEGER DEFAULT 0,
    sale_price INTEGER DEFAULT 0,
    storage TEXT DEFAULT '상온',
    is_new TEXT DEFAULT '신규',
    delivery_ea INTEGER DEFAULT 0,
    stock_box INTEGER DEFAULT 0,
    stock_ea INTEGER DEFAULT 0,
    safety_box INTEGER DEFAULT 0,
    image_name TEXT DEFAULT '',
    image_data BYTEA,
    memo TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS product_items (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    item_name TEXT NOT NULL,
    qty INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS stores (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    location TEXT DEFAULT '',
    delivery_day TEXT DEFAULT '',
    memo TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS product_stores (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    UNIQUE(product_id, store_id)
);
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    tdate TEXT NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    ttype TEXT NOT NULL,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    qty_box INTEGER DEFAULT 0,
    qty_ea INTEGER DEFAULT 0,
    expiry_date TEXT DEFAULT '',
    memo TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS store_product_qty (
    id SERIAL PRIMARY KEY,
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    qty_box INTEGER DEFAULT 0,
    qty_ea INTEGER DEFAULT 0,
    memo TEXT DEFAULT '',
    updated_at TEXT,
    UNIQUE(store_id, product_id)
);
CREATE TABLE IF NOT EXISTS change_logs (
    id SERIAL PRIMARY KEY,
    log_date TEXT NOT NULL,
    product_name TEXT,
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT
);
"""


@st.cache_resource
def get_engine():
    url = get_secret("DB_URL")
    if not url:
        st.error("DB_URL이 설정되지 않았습니다. Streamlit Cloud의 Secrets를 확인하세요.")
        st.stop()
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    # 연결 정보 확인용 (비밀번호는 가려서 표시)
    try:
        from sqlalchemy.engine.url import make_url
        u = make_url(url)
        masked = f"{u.username}@{u.host}:{u.port}/{u.database}"
    except Exception:
        masked = "(URL 형식 해석 실패 — DB_URL 형식 자체가 잘못됨)"

    try:
        eng = create_engine(url, pool_pre_ping=True,
                            connect_args={"connect_timeout": 10})
        with eng.begin() as c:
            for stmt in DDL.split(";"):
                if stmt.strip():
                    c.execute(text(stmt))
            # 기존 배포 DB 마이그레이션: 신규 컬럼이 없으면 추가
            c.execute(text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS expiry_date TEXT DEFAULT ''"))
            c.execute(text("ALTER TABLE stores ADD COLUMN IF NOT EXISTS delivery_day TEXT DEFAULT ''"))
        return eng
    except Exception as e:
        raw = str(getattr(e, "orig", None) or e)
        st.error("🚫 데이터베이스 연결 실패 — 아래 진단을 확인하세요.")
        st.write(f"**현재 설정된 접속 정보**: `{masked}`")

        low = raw.lower()
        if "password authentication failed" in low or "sasl" in low:
            st.warning("🔑 **비밀번호 오류**: DB_URL 안의 비밀번호가 틀렸습니다. "
                       "Supabase → Settings → Database → Reset database password 로 "
                       "영문+숫자만 있는 새 비밀번호를 만들고, Secrets의 DB_URL에 반영하세요. "
                       "`[YOUR-PASSWORD]` 대괄호까지 지우고 실제 비밀번호로 바꿔야 합니다.")
        elif "could not translate host name" in low or "name or service not known" in low:
            st.warning("🌐 **호스트 주소 오류**: DB_URL의 서버 주소에 오타가 있습니다. "
                       "Supabase → Connect → Transaction pooler 의 URI를 다시 복사하세요.")
        elif "network is unreachable" in low or "timeout" in low or "timed out" in low:
            st.warning("🚧 **접속 불가(IPv6/네트워크)**: 직접 연결 주소(db.xxxx.supabase.co)를 쓰고 있을 가능성이 큽니다. "
                       "반드시 **Transaction pooler** 주소(...pooler.supabase.com:6543)를 사용하세요. "
                       "또는 Supabase 프로젝트가 일시정지(Paused) 상태인지 확인하고 Restore 하세요.")
        elif "tenant or user not found" in low:
            st.warning("👤 **사용자명 형식 오류**: pooler 접속 시 사용자명은 `postgres`가 아니라 "
                       "`postgres.프로젝트ID` 형태여야 합니다. Connect 화면의 URI를 그대로 복사하세요.")
        elif "too many connections" in low or "max client" in low:
            st.warning("🔌 **연결 수 초과**: 잠시 후 새로고침하거나, Streamlit Cloud에서 Reboot app을 해보세요.")
        else:
            st.warning("원인 미분류 오류입니다. 아래 원문 메시지를 확인하세요.")

        with st.expander("오류 원문 보기"):
            st.code(raw)
        st.stop()


engine = get_engine()


def run(sql: str, **params):
    with engine.begin() as c:
        c.execute(text(sql), params)


def qdf(sql: str, **params) -> pd.DataFrame:
    return pd.read_sql_query(text(sql), engine, params=params)


# ──────────────────────────────────────────────
# 공통 함수
# ──────────────────────────────────────────────
FIELD_LABELS = {
    "name": "제품명", "barcode": "바코드", "box_qty": "박스입수량", "spec": "규격(무게)",
    "delivery_ea": "납품갯수(낱개)",
    "normal_price": "정상가", "sale_price": "할인판매가", "storage": "보관방법",
    "is_new": "신규/기존", "stock_box": "현재고(박스)", "stock_ea": "현재고(낱개)",
    "safety_box": "안전재고(박스)", "memo": "메모", "image_name": "이미지",
}


def add_log(product_name: str, field: str, old, new):
    run(
        "INSERT INTO change_logs (log_date, product_name, field, old_value, new_value, changed_at) "
        "VALUES (:d, :p, :f, :o, :n, :t)",
        d=TODAY(), p=product_name, f=FIELD_LABELS.get(field, field),
        o=str(old), n=str(new), t=KST_NOW(),
    )


def df_products() -> pd.DataFrame:
    # image_data(용량 큼)는 제외하고 조회
    return qdf(
        "SELECT id, name, barcode, box_qty, spec, normal_price, sale_price, storage, "
        "is_new, delivery_ea, stock_box, stock_ea, safety_box, image_name, memo, created_at "
        "FROM products ORDER BY id")


def df_stores() -> pd.DataFrame:
    return qdf("SELECT * FROM stores ORDER BY id")


def df_transactions(d1=None, d2=None) -> pd.DataFrame:
    q = """
        SELECT t.id, t.tdate AS 날짜, p.name AS 제품명, t.ttype AS 구분,
               COALESCE(s.name,'(총량)') AS 매장, t.qty_box AS 박스, t.qty_ea AS 낱개,
               (t.qty_box * GREATEST(p.box_qty, 1) + t.qty_ea) AS 총낱개환산,
               t.expiry_date AS 소비기한, t.memo AS 메모, t.created_at AS 기록시각
        FROM transactions t
        JOIN products p ON p.id = t.product_id
        LEFT JOIN stores s ON s.id = t.store_id
    """
    cond = []
    params = {}
    if d1:
        cond.append("t.tdate >= :d1"); params["d1"] = d1
    if d2:
        cond.append("t.tdate <= :d2"); params["d2"] = d2
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY t.tdate DESC, t.id DESC"
    return qdf(q, **params)


def df_logs(d1=None, d2=None) -> pd.DataFrame:
    q = ("SELECT log_date AS 변경일자, product_name AS 제품명, field AS 변경항목, "
         "old_value AS 이전값, new_value AS 변경값, changed_at AS 변경시각 FROM change_logs")
    cond = []
    params = {}
    if d1:
        cond.append("log_date >= :d1"); params["d1"] = d1
    if d2:
        cond.append("log_date <= :d2"); params["d2"] = d2
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY id DESC"
    return qdf(q, **params)


def total_ea(row) -> int:
    return int(row["stock_box"]) * max(int(row["box_qty"]), 1) + int(row["stock_ea"])


def build_ledger(pid: int = None) -> pd.DataFrame:
    """일자별 수불부: 매일의 입고/출고 합(환산낱개)이 누적되어 오늘 재고에 도달.
    마지막 날의 누적재고가 현재 재고(총낱개환산)와 정확히 일치하도록 기초재고를 역산한다.
    (제품 관리에서 재고를 수동 조정한 경우 그 차이는 기초재고에 흡수됨)"""
    prods = df_products()
    if prods.empty:
        return pd.DataFrame()
    cond = "AND t.product_id = :p" if pid else ""
    daily = qdf(f"""
        SELECT p.id AS pid, p.name AS 제품명, t.tdate AS 날짜,
               SUM(CASE WHEN t.ttype='입고' THEN t.qty_box*GREATEST(p.box_qty,1)+t.qty_ea ELSE 0 END) AS 입고,
               SUM(CASE WHEN t.ttype='출고' THEN t.qty_box*GREATEST(p.box_qty,1)+t.qty_ea ELSE 0 END) AS 출고
        FROM transactions t JOIN products p ON p.id = t.product_id
        WHERE t.ttype IN ('입고','출고') {cond}
        GROUP BY p.id, p.name, t.tdate
        ORDER BY p.name, t.tdate""", **({"p": pid} if pid else {}))
    if daily.empty:
        return pd.DataFrame()
    cur_map = {int(r["id"]): total_ea(r) for _, r in prods.iterrows()}
    out = []
    for gpid, g in daily.groupby("pid", sort=False):
        g = g.sort_values("날짜").copy()
        g["일변동"] = g["입고"] - g["출고"]
        start = cur_map.get(int(gpid), 0) - int(g["일변동"].sum())  # 기초재고 역산
        g["누적재고"] = start + g["일변동"].cumsum()
        g["기초재고"] = start
        out.append(g)
    led = pd.concat(out, ignore_index=True)
    return led[["제품명", "날짜", "입고", "출고", "일변동", "누적재고", "기초재고", "pid"]]


def df_plan() -> pd.DataFrame:
    """납품 정리표: 매장×제품 수량"""
    return qdf(
        """SELECT q.id, s.name AS 매장명, s.delivery_day AS 납품요일, p.name AS 제품명,
                  q.qty_box AS 박스, q.qty_ea AS 낱개,
                  (q.qty_box * GREATEST(p.box_qty, 1) + q.qty_ea) AS 환산낱개,
                  q.memo AS 메모, q.updated_at AS 수정시각
           FROM store_product_qty q
           JOIN stores s ON s.id = q.store_id
           JOIN products p ON p.id = q.product_id
           ORDER BY s.name, p.name""")


def build_excel(d1=None, d2=None) -> bytes:
    """제품현황·입출고이력·변경이력·납품처·구성품 시트를 담은 엑셀 생성"""
    prods = df_products()
    if not prods.empty:
        prods = prods.copy()
        prods["총낱개환산"] = prods.apply(total_ea, axis=1)
        prods = prods[["name", "barcode", "is_new", "box_qty", "spec", "normal_price", "sale_price",
                       "storage", "delivery_ea", "stock_box", "stock_ea", "총낱개환산", "safety_box", "memo"]]
        prods.columns = ["제품명", "바코드", "구분", "박스입수량", "규격(무게)", "정상가", "할인판매가",
                         "보관방법", "납품갯수(낱개)", "재고(박스)", "재고(낱개)", "총낱개환산", "안전재고(박스)", "메모"]

    tx = df_transactions(d1, d2).drop(columns=["id"], errors="ignore")
    logs = df_logs(d1, d2)
    stores = df_stores().rename(columns={"name": "매장명", "location": "납품개소",
                                         "delivery_day": "납품요일", "memo": "메모"})
    if "id" in stores.columns:
        stores = stores.drop(columns=["id"])
    items = qdf(
        """SELECT p.name AS 제품명, i.item_name AS 낱개품목, i.qty AS 수량
           FROM product_items i JOIN products p ON p.id = i.product_id
           ORDER BY p.name""")
    plan = df_plan().drop(columns=["id"], errors="ignore")
    ledger = build_ledger()
    if not ledger.empty:
        ledger = ledger.drop(columns=["pid"])

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        (prods if not prods.empty else pd.DataFrame({"안내": ["제품 없음"]})).to_excel(writer, sheet_name="제품현황", index=False)
        (tx if not tx.empty else pd.DataFrame({"안내": ["기록 없음"]})).to_excel(writer, sheet_name="입출고이력", index=False)
        (logs if not logs.empty else pd.DataFrame({"안내": ["이력 없음"]})).to_excel(writer, sheet_name="변경이력", index=False)
        (stores if not stores.empty else pd.DataFrame({"안내": ["매장 없음"]})).to_excel(writer, sheet_name="납품처", index=False)
        (items if not items.empty else pd.DataFrame({"안내": ["구성품 없음"]})).to_excel(writer, sheet_name="구성품", index=False)
        (plan if not plan.empty else pd.DataFrame({"안내": ["정리표 없음"]})).to_excel(writer, sheet_name="납품정리표", index=False)
        (ledger if not ledger.empty else pd.DataFrame({"안내": ["수불부 없음"]})).to_excel(writer, sheet_name="수불부", index=False)
        for ws in writer.book.worksheets:
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(width * 1.8 + 2, 40)
    return buf.getvalue()


# ──────────────────────────────────────────────
# 사이드바 메뉴
# ──────────────────────────────────────────────
st.sidebar.title("📦 삼립 무인편의점 비즈 재고·발주")
page = st.sidebar.radio(
    "메뉴",
    ["📊 대시보드", "📝 일일 기록", "📈 일자별 누적(수불부)", "📦 제품 관리(엑셀표)", "🏬 납품처 관리(엑셀표)", "📋 납품 정리표(매장×제품)", "📜 변경이력", "⬇️ 엑셀 내보내기"],
    label_visibility="collapsed",
)
st.sidebar.caption(f"오늘: {TODAY()}")
if st.sidebar.button("🔓 로그아웃"):
    st.session_state["authenticated"] = False
    st.rerun()


# ══════════════════════════════════════════════
# 1. 대시보드
# ══════════════════════════════════════════════
if page == "📊 대시보드":
    st.title("📊 대시보드")
    prods = df_products()
    today = TODAY()
    tx_today = df_transactions(today, today)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("등록 제품 수", f"{len(prods)}개")
    c2.metric("오늘 입고", f"{len(tx_today[tx_today['구분']=='입고'])}건")
    c3.metric("오늘 출고", f"{len(tx_today[tx_today['구분']=='출고'])}건")
    c4.metric("오늘 발주", f"{len(tx_today[tx_today['구분']=='발주'])}건")

    # 오늘 요일에 납품 나가는 매장 (다중 요일 "화,금" 형식 지원)
    today_day = KOR_WEEKDAY[date.today().weekday()]
    stores_all = df_stores()
    if not stores_all.empty:
        def _due(s):
            days = str(s or "").split(",")
            return today_day in days or "매일" in days
        due = stores_all[stores_all["delivery_day"].apply(_due)]
        if not due.empty:
            st.info(f"🚚 오늘({today_day}요일) 납품 나가는 매장 {len(due)}곳: "
                    + ", ".join(due["name"].tolist()))

    if not prods.empty:
        low = prods[prods["stock_box"] <= prods["safety_box"]]
        if not low.empty:
            st.error(f"⚠️ 안전재고 이하 제품 {len(low)}개 — 발주 검토 필요")
            st.dataframe(
                low[["name", "stock_box", "stock_ea", "safety_box"]].rename(columns={
                    "name": "제품명", "stock_box": "현재고(박스)",
                    "stock_ea": "현재고(낱개)", "safety_box": "안전재고(박스)"}),
                use_container_width=True, hide_index=True)

    # 소비기한 임박 경고 (입고 기록 기준, 30일 이내)
    exp = qdf(
        """SELECT t.expiry_date AS 소비기한, p.name AS 제품명, t.tdate AS 입고일,
                  t.qty_box AS 박스, t.qty_ea AS 낱개, t.memo AS 메모
           FROM transactions t JOIN products p ON p.id = t.product_id
           WHERE t.ttype = '입고' AND t.expiry_date <> ''
           ORDER BY t.expiry_date""")
    if not exp.empty:
        from datetime import timedelta
        limit = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        soon = exp[exp["소비기한"] <= limit]
        if not soon.empty:
            passed = soon[soon["소비기한"] < TODAY()]
            if not passed.empty:
                st.error(f"🚨 소비기한 경과 {len(passed)}건")
            st.warning(f"⏰ 소비기한 30일 이내(경과 포함) {len(soon)}건")
            st.dataframe(soon, use_container_width=True, hide_index=True)

    st.subheader("현재 재고 현황")
    if prods.empty:
        st.info("등록된 제품이 없습니다. [제품 관리] 메뉴에서 제품을 추가하세요.")
    else:
        view = prods.copy()
        view["총낱개환산"] = view.apply(total_ea, axis=1)
        view = view[["name", "barcode", "is_new", "box_qty", "spec", "normal_price", "sale_price",
                     "storage", "delivery_ea", "stock_box", "stock_ea", "총낱개환산"]]
        view.columns = ["제품명", "바코드", "구분", "박스입수량", "규격(무게)", "정상가", "할인판매가",
                        "보관방법", "납품갯수(낱개)", "재고(박스)", "재고(낱개)", "총낱개환산"]
        st.dataframe(view, use_container_width=True, hide_index=True)

    st.subheader("오늘 기록")
    st.dataframe(tx_today.drop(columns=["id"]), use_container_width=True, hide_index=True)
    st.subheader("오늘 변경이력")
    st.dataframe(df_logs(today, today), use_container_width=True, hide_index=True)


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
        c1, c2, c3 = st.columns(3)
        tdate = c1.date_input("날짜", value=date.today())
        pname = c2.selectbox("제품", prods["name"].tolist())
        ttype = c3.radio("구분", TTYPE_OPTIONS, horizontal=True)

        prow = prods[prods["name"] == pname].iloc[0]
        box_qty = max(int(prow["box_qty"]), 1)

        c4, c5, c6, c7 = st.columns([1.2, 1, 1, 1.2])
        store_names = ["(총량 / 매장 미지정)"] + stores["name"].tolist()
        sname = c4.selectbox("매장(납품처)", store_names,
                             help="매장별로 나눌 필요 없으면 '(총량)'을 선택")
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
        exp_use = c8.checkbox("소비기한 입력", help="물건이 들어올 때(입고) 소비기한을 기록")
        exp_date = c8.date_input("소비기한", value=date.today(), label_visibility="collapsed")
        memo = c9.text_input("메모", placeholder="예: 국군복지단 정기 납품 / 로트번호 등")
        ok = st.button("💾 기록 저장", use_container_width=True, type="primary")

        if ok:
            if qty_box == 0 and qty_ea == 0:
                st.error("박스 또는 낱개 수량 중 하나 이상을 입력하세요.")
            else:
                pid = int(prow["id"])
                sid = None
                if sname != "(총량 / 매장 미지정)":
                    sid = int(stores[stores["name"] == sname].iloc[0]["id"])
                run(
                    "INSERT INTO transactions (tdate, product_id, ttype, store_id, qty_box, qty_ea, expiry_date, memo, created_at) "
                    "VALUES (:d, :p, :t, :s, :qb, :qe, :ex, :m, :c)",
                    d=tdate.strftime("%Y-%m-%d"), p=pid, t=ttype, s=sid,
                    qb=int(qty_box), qe=int(qty_ea),
                    ex=exp_date.strftime("%Y-%m-%d") if exp_use else "",
                    m=memo, c=KST_NOW())
                if ttype in ("입고", "출고"):
                    sign = 1 if ttype == "입고" else -1
                    new_box = int(prow["stock_box"]) + sign * int(qty_box)
                    new_ea = int(prow["stock_ea"]) + sign * int(qty_ea)
                    while new_ea < 0 and new_box > 0:
                        new_box -= 1
                        new_ea += box_qty
                    run("UPDATE products SET stock_box=:b, stock_ea=:e WHERE id=:i",
                        b=new_box, e=new_ea, i=pid)
                    add_log(pname, "stock_box" if qty_box else "stock_ea",
                            f"박스 {prow['stock_box']} / 낱개 {prow['stock_ea']}",
                            f"박스 {new_box} / 낱개 {new_ea} ({ttype} {qty_box}박스 {qty_ea}낱개 = 환산 {conv}낱개)")
                # 수량 입력칸 초기화
                for k in ("in_box", "in_ea"):
                    st.session_state.pop(k, None)
                st.success(f"✅ {tdate} · {pname} · {ttype} · 환산 {conv:,}낱개 기록 완료")
                st.rerun()

        # ── 일일 기록 엑셀 내보내기 ──
        st.divider()
        c_a, c_b = st.columns([1, 2])
        with c_a:
            exp_date = st.date_input("내보낼 날짜", value=date.today(), key="daily_exp")
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
        tx = df_transactions()
        if tx.empty:
            st.info("기록이 없습니다.")
        else:
            st.dataframe(tx.head(30).drop(columns=["id"]), use_container_width=True, hide_index=True)
            del_id = st.selectbox(
                "삭제할 기록 선택",
                options=[0] + tx["id"].tolist(),
                format_func=lambda i: "선택 안 함" if i == 0 else
                f"#{i} | " + " | ".join(map(str, tx[tx['id'] == i][['날짜','제품명','구분','매장','박스','낱개']].iloc[0].tolist())),
            )
            if del_id and st.button("🗑️ 선택 기록 삭제"):
                row = qdf("SELECT product_id, ttype, qty_box, qty_ea FROM transactions WHERE id=:i", i=del_id)
                if not row.empty:
                    r = row.iloc[0]
                    if r["ttype"] in ("입고", "출고"):
                        sign = -1 if r["ttype"] == "입고" else 1
                        run("UPDATE products SET stock_box=stock_box+:b, stock_ea=stock_ea+:e WHERE id=:i",
                            b=sign * int(r["qty_box"]), e=sign * int(r["qty_ea"]), i=int(r["product_id"]))
                    run("DELETE FROM transactions WHERE id=:i", i=del_id)
                    st.success("삭제 및 재고 복원 완료")
                    st.rerun()


# ══════════════════════════════════════════════
# 수불부 — 일자별 누적이 오늘 재고로 반영
# ══════════════════════════════════════════════
elif page == "📈 일자별 누적(수불부)":
    st.title("📈 일자별 누적 재고 (수불부)")
    st.caption("매일 기록한 입고·출고 합계가 날짜순으로 누적되어 오늘의 재고에 반영됩니다. 수량은 전부 낱개 환산 기준입니다.")

    prods = df_products()
    if prods.empty:
        st.info("제품을 먼저 등록하세요.")
    else:
        sel = st.selectbox("제품 선택", ["(전체 요약)"] + prods["name"].tolist())

        if sel == "(전체 요약)":
            led = build_ledger()
            if led.empty:
                st.info("입고/출고 기록이 아직 없습니다.")
            else:
                # 제품별 최종 누적 = 현재 재고 확인 요약
                last = led.sort_values("날짜").groupby("제품명").tail(1)
                summary = last[["제품명", "기초재고", "누적재고"]].copy()
                summary["총입고"] = led.groupby("제품명")["입고"].sum().reindex(summary["제품명"]).values
                summary["총출고"] = led.groupby("제품명")["출고"].sum().reindex(summary["제품명"]).values
                summary = summary[["제품명", "기초재고", "총입고", "총출고", "누적재고"]]
                summary.columns = ["제품명", "기초재고", "누적 입고합", "누적 출고합", "오늘 재고(환산낱개)"]
                st.subheader("제품별 누적 요약 — 기초 + 입고합 − 출고합 = 오늘 재고")
                st.dataframe(summary, use_container_width=True, hide_index=True)
                st.subheader("전체 일자별 내역")
                st.dataframe(led.drop(columns=["pid", "기초재고"]), use_container_width=True, hide_index=True)
        else:
            prow = prods[prods["name"] == sel].iloc[0]
            led = build_ledger(int(prow["id"]))
            if led.empty:
                st.info(f"'{sel}' 의 입고/출고 기록이 아직 없습니다.")
            else:
                start = int(led.iloc[0]["기초재고"])
                cur = total_ea(prow)
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("기초재고(환산낱개)", f"{start:,}")
                c2.metric("누적 입고합", f"{int(led['입고'].sum()):,}")
                c3.metric("누적 출고합", f"{int(led['출고'].sum()):,}")
                c4.metric("오늘 재고(환산낱개)", f"{cur:,}",
                          help="기초재고 + 입고합 − 출고합 (재고 박스×입수량+낱개와 일치)")
                st.line_chart(led.set_index("날짜")["누적재고"])
                view = led.drop(columns=["pid", "제품명", "기초재고"])
                st.dataframe(view, use_container_width=True, hide_index=True)
                # 일자별 수불부 엑셀
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as w:
                    view.to_excel(w, sheet_name="수불부", index=False)
                st.download_button(f"📥 '{sel}' 수불부 엑셀 다운로드", data=buf.getvalue(),
                                   file_name=f"수불부_{sel}_{TODAY()}.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   use_container_width=True)


# ══════════════════════════════════════════════
# 3. 제품 관리 — 엑셀형 그리드
# ══════════════════════════════════════════════
elif page == "📦 제품 관리(엑셀표)":
    st.title("📦 제품 관리 — 엑셀처럼 직접 수정")
    st.caption("셀을 터치/더블클릭해 수정 → [변경사항 저장]. 맨 아래 빈 줄에 입력하면 신규 제품 추가. 수정 내용은 변경이력에 자동 기록됩니다.")

    prods = df_products()
    grid_cols = ["id", "name", "barcode", "is_new", "box_qty", "spec", "normal_price", "sale_price",
                 "storage", "delivery_ea", "stock_box", "stock_ea", "safety_box", "memo"]
    grid = prods[grid_cols].copy() if not prods.empty else pd.DataFrame(columns=grid_cols)

    edited = st.data_editor(
        grid,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=["id"],
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
            "stock_box": st.column_config.NumberColumn("재고(박스)", step=1),
            "stock_ea": st.column_config.NumberColumn("재고(낱개)", step=1),
            "safety_box": st.column_config.NumberColumn("안전재고(박스)", step=1),
            "memo": st.column_config.TextColumn("메모"),
        },
        key="prod_editor",
    )

    if st.button("💾 변경사항 저장", type="primary", use_container_width=True):
        old_map = {int(r["id"]): r for _, r in prods.iterrows()} if not prods.empty else {}
        seen_ids, changes = set(), 0

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
                "stock_box": int(r["stock_box"]) if pd.notna(r["stock_box"]) else 0,
                "stock_ea": int(r["stock_ea"]) if pd.notna(r["stock_ea"]) else 0,
                "safety_box": int(r["safety_box"]) if pd.notna(r["safety_box"]) else 0,
                "memo": "" if pd.isna(r["memo"]) else str(r["memo"]),
            }
            if pd.notna(rid) and int(rid) in old_map:  # 기존 행 수정
                rid = int(rid); seen_ids.add(rid)
                old = old_map[rid]
                diff = {k: v for k, v in vals.items() if str(old[k]) != str(v)}
                if diff:
                    sets = ", ".join(f"{k}=:{k}" for k in diff)
                    run(f"UPDATE products SET {sets} WHERE id=:rid", rid=rid, **diff)
                    for k, v in diff.items():
                        add_log(vals["name"], k, old[k], v)
                    changes += len(diff)
            else:  # 신규 행
                dup = qdf("SELECT 1 FROM products WHERE name=:n", n=vals["name"])
                if not dup.empty:
                    st.warning(f"'{vals['name']}' 은(는) 이미 존재하는 제품명이라 건너뛰었습니다.")
                    continue
                cols = ", ".join(vals.keys())
                ph = ", ".join(f":{k}" for k in vals)
                run(f"INSERT INTO products ({cols}, created_at) VALUES ({ph}, :ca)",
                    ca=KST_NOW(), **vals)
                add_log(vals["name"], "name", "(신규등록)", vals["name"])
                changes += 1

        for rid, old in old_map.items():
            if rid not in seen_ids:
                run("DELETE FROM products WHERE id=:i", i=rid)
                add_log(old["name"], "name", old["name"], "(삭제됨)")
                changes += 1

        st.success(f"✅ 저장 완료 — 변경 {changes}건이 변경이력에 기록되었습니다.")
        st.rerun()

    # ── 제품 상세: 이미지 / 구성품 / 납품처 ──
    st.divider()
    st.subheader("🔍 제품 상세 (이미지 · 낱개 구성품 · 납품 매장)")
    prods = df_products()
    if prods.empty:
        st.info("제품을 먼저 등록하세요.")
    else:
        sel = st.selectbox("제품 선택", prods["name"].tolist())
        prow = prods[prods["name"] == sel].iloc[0]
        pid = int(prow["id"])

        col_img, col_detail = st.columns([1, 2])
        with col_img:
            img = qdf("SELECT image_data FROM products WHERE id=:i", i=pid)
            if not img.empty and img.iloc[0]["image_data"] is not None:
                st.image(bytes(img.iloc[0]["image_data"]), width=220, caption=sel)
            up = st.file_uploader("제품 이미지 업로드", type=["png", "jpg", "jpeg", "webp"], key=f"img{pid}")
            if up is not None:
                run("UPDATE products SET image_name=:n, image_data=:d WHERE id=:i",
                    n=up.name, d=up.getvalue(), i=pid)
                add_log(sel, "image_name", prow["image_name"] or "(없음)", up.name)
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
                run("DELETE FROM product_items WHERE product_id=:p", p=pid)
                names = []
                for _, ir in items_edit.iterrows():
                    nm = str(ir["낱개품목명"]).strip() if pd.notna(ir["낱개품목명"]) else ""
                    if nm:
                        q = int(ir["수량"]) if pd.notna(ir["수량"]) else 1
                        run("INSERT INTO product_items (product_id, item_name, qty) VALUES (:p, :n, :q)",
                            p=pid, n=nm, q=q)
                        names.append(f"{nm}x{q}")
                add_log(sel, "구성품", "(수정 전)", ", ".join(names) or "(없음)")
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
                    run("DELETE FROM product_stores WHERE product_id=:p", p=pid)
                    for nm in new_names:
                        sid = int(stores[stores["name"] == nm].iloc[0]["id"])
                        run("INSERT INTO product_stores (product_id, store_id) VALUES (:p, :s) "
                            "ON CONFLICT DO NOTHING", p=pid, s=sid)
                    add_log(sel, "납품매장", ", ".join(cur_names) or "(없음)",
                            ", ".join(new_names) or "(없음)")
                    st.success("납품 매장 저장 완료")


# ══════════════════════════════════════════════
# 4. 납품처 관리 — 엑셀형 그리드
# ══════════════════════════════════════════════
elif page == "🏬 납품처 관리(엑셀표)":
    st.title("🏬 납품처(매장) 관리 — 엑셀처럼 직접 수정")
    st.caption("셀을 터치/더블클릭해 수정하고 [저장]. 맨 아래 빈 줄에 입력하면 신규 매장 추가.")
    stores = df_stores()
    grid_cols = ["id", "name", "location", "delivery_day", "memo"]
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
            "location": st.column_config.TextColumn("납품개소/주소"),
            "delivery_day": st.column_config.MultiselectColumn(
                "납품요일", options=DAY_OPTIONS,
                help="이 지점에 물건이 나가는 요일을 모두 선택 (예: 화·금)"),
            "memo": st.column_config.TextColumn("메모"),
        }, key="store_editor")

    if st.button("💾 저장", type="primary", use_container_width=True):
        old_map = {int(r["id"]): r for _, r in stores.iterrows()} if not stores.empty else {}
        seen = set()
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
            mm = "" if pd.isna(r["memo"]) else str(r["memo"])
            if pd.notna(r["id"]) and int(r["id"]) in old_map:
                rid = int(r["id"]); seen.add(rid)
                old = old_map[rid]
                if (old["name"], old["location"], old["delivery_day"], old["memo"]) != (nm, loc, dd, mm):
                    run("UPDATE stores SET name=:n, location=:l, delivery_day=:d, memo=:m WHERE id=:i",
                        n=nm, l=loc, d=dd, m=mm, i=rid)
                    add_log(f"[매장] {nm}", "매장정보",
                            f"{old['name']} / {old['location']} / {old['delivery_day'] or '요일미지정'}",
                            f"{nm} / {loc} / {dd or '요일미지정'}")
            else:
                dup = qdf("SELECT 1 FROM stores WHERE name=:n", n=nm)
                if not dup.empty:
                    st.warning(f"'{nm}' 매장은 이미 존재합니다.")
                    continue
                run("INSERT INTO stores (name, location, delivery_day, memo) VALUES (:n, :l, :d, :m)",
                    n=nm, l=loc, d=dd, m=mm)
                add_log(f"[매장] {nm}", "매장정보", "(신규등록)", f"{nm} / {loc} / {dd or '요일미지정'}")
        for rid, old in old_map.items():
            if rid not in seen:
                run("DELETE FROM stores WHERE id=:i", i=rid)
                add_log(f"[매장] {old['name']}", "매장정보", old["name"], "(삭제됨)")
        st.success("저장 완료")
        st.rerun()

    st.divider()
    st.subheader("매장별 납품 제품 조회")
    ps = qdf(
        """SELECT s.name AS 매장명, s.location AS 납품개소, s.delivery_day AS 납품요일, p.name AS 제품명
           FROM product_stores x
           JOIN stores s ON s.id = x.store_id
           JOIN products p ON p.id = x.product_id
           ORDER BY s.name, p.name""")
    st.dataframe(ps, use_container_width=True, hide_index=True)


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
        grid_cols = ["id", "매장명", "제품명", "박스", "낱개", "메모"]
        grid = plan[grid_cols].copy() if not plan.empty else pd.DataFrame(columns=grid_cols)

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

        if st.button("💾 정리표 저장", type="primary", use_container_width=True):
            sid_map = dict(zip(stores["name"], stores["id"]))
            pid_map = dict(zip(prods["name"], prods["id"]))
            old_map = {}
            if not plan.empty:
                for _, r in plan.iterrows():
                    old_map[(r["매장명"], r["제품명"])] = r

            new_keys, changes = set(), 0
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
                    run("""INSERT INTO store_product_qty (store_id, product_id, qty_box, qty_ea, memo, updated_at)
                           VALUES (:s, :p, :qb, :qe, :m, :u)
                           ON CONFLICT (store_id, product_id)
                           DO UPDATE SET qty_box=:qb, qty_ea=:qe, memo=:m, updated_at=:u""",
                        s=int(sid_map[sname]), p=int(pid_map[pname]),
                        qb=qb, qe=qe, m=mm, u=KST_NOW())
                    add_log(f"[정리표] {sname} × {pname}", "납품수량",
                            "(신규)" if old is None else f"박스 {old['박스']} / 낱개 {old['낱개']}",
                            f"박스 {qb} / 낱개 {qe}")
                    changes += 1

            for key, old in old_map.items():
                if key not in new_keys:
                    run("DELETE FROM store_product_qty WHERE id=:i", i=int(old["id"]))
                    add_log(f"[정리표] {key[0]} × {key[1]}", "납품수량",
                            f"박스 {old['박스']} / 낱개 {old['낱개']}", "(삭제됨)")
                    changes += 1

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
# 6. 변경이력
# ══════════════════════════════════════════════
elif page == "📜 변경이력":
    st.title("📜 변경이력 (날짜별 자동 기록)")
    c1, c2 = st.columns(2)
    d1 = c1.date_input("시작일", value=date.today().replace(day=1))
    d2 = c2.date_input("종료일", value=date.today())
    logs = df_logs(d1.strftime("%Y-%m-%d"), d2.strftime("%Y-%m-%d"))
    st.dataframe(logs, use_container_width=True, hide_index=True)
    st.caption(f"총 {len(logs)}건")


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
        d1 = c1.date_input("시작일", value=date.today().replace(day=1)).strftime("%Y-%m-%d")
        d2 = c2.date_input("종료일", value=date.today()).strftime("%Y-%m-%d")

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
