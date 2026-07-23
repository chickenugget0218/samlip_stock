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
from zoneinfo import ZoneInfo

# ──────────────────────────────────────────────
# 기본 설정 — 모든 날짜/시간은 한국시간(KST) 기준
# ──────────────────────────────────────────────
KST = ZoneInfo("Asia/Seoul")
KST_NOW = lambda: datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
TODAY = lambda: datetime.now(KST).strftime("%Y-%m-%d")


def today_kst() -> date:
    return datetime.now(KST).date()

STORAGE_OPTIONS = ["상온", "냉장", "냉동"]
NEW_OLD_OPTIONS = ["신규", "기존"]
TTYPE_OPTIONS = ["입고", "출고", "발주"]
DAY_OPTIONS = ["월", "화", "수", "목", "금", "토", "일", "매일"]
DAY_COLORS = ["#FF6B6B", "#FFD93D", "#6BCB77", "#B983FF", "#4D96FF", "#00C2CB", "#FF9F45", "#9E9E9E"]  # 월~일·매일
DAY_COLOR_MAP = dict(zip(DAY_OPTIONS, DAY_COLORS))
KOR_WEEKDAY = ["월", "화", "수", "목", "금", "토", "일"]  # date.weekday() 매핑

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
    safety_ea INTEGER DEFAULT 0,
    image_name TEXT DEFAULT '',
    image_data BYTEA,
    memo TEXT DEFAULT '',
    safety_ea INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT DEFAULT ''
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
    phone TEXT DEFAULT '',
    memo TEXT DEFAULT '',
    note TEXT DEFAULT ''
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
CREATE TABLE IF NOT EXISTS daily_notes (
    id SERIAL PRIMARY KEY,
    ndate TEXT NOT NULL UNIQUE,
    content TEXT DEFAULT '',
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS stock_snapshots (
    id SERIAL PRIMARY KEY,
    sdate TEXT NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    stock_box INTEGER DEFAULT 0,
    stock_ea INTEGER DEFAULT 0,
    total_ea INTEGER DEFAULT 0,
    UNIQUE(sdate, product_id)
);
CREATE TABLE IF NOT EXISTS daily_memos (
    id SERIAL PRIMARY KEY,
    mdate TEXT NOT NULL UNIQUE,   -- 날짜당 메모 1건
    content TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT
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


SCHEMA_VERSION = 12  # ⚠️ 테이블/컬럼을 추가할 때마다 +1 하세요. 배포 시 자동으로 스키마가 갱신됩니다.


@st.cache_resource
def get_engine(schema_version: int):
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
            c.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS updated_at TEXT DEFAULT ''"))
            c.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS safety_ea INTEGER DEFAULT 0"))
            c.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS safety_ea INTEGER DEFAULT 0"))
            c.execute(text("ALTER TABLE stores ADD COLUMN IF NOT EXISTS phone TEXT DEFAULT ''"))
            c.execute(text("ALTER TABLE stores ADD COLUMN IF NOT EXISTS note TEXT DEFAULT ''"))
            c.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS safety_ea INTEGER DEFAULT 0"))
            c.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS safety_ea INTEGER DEFAULT 0"))
            c.execute(text("ALTER TABLE stores ADD COLUMN IF NOT EXISTS phone TEXT DEFAULT ''"))
            c.execute(text("ALTER TABLE stores ADD COLUMN IF NOT EXISTS note TEXT DEFAULT ''"))
            c.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS safety_ea INTEGER DEFAULT 0"))
            c.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS safety_ea INTEGER DEFAULT 0"))
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


engine = get_engine(SCHEMA_VERSION)


def run(sql: str, **params):
    with engine.begin() as c:
        c.execute(text(sql), params)


def run_batch(ops: list):
    """[(sql, params_dict), ...] 를 한 번의 트랜잭션(왕복 최소화)으로 실행 → 저장 속도 개선"""
    if not ops:
        return
    with engine.begin() as c:
        for sql, params in ops:
            c.execute(text(sql), params)


LOG_SQL = ("INSERT INTO change_logs (log_date, product_name, field, old_value, new_value, changed_at) "
           "VALUES (:d, :p, :f, :o, :n, :t)")


def log_op(product_name: str, field: str, old, new):
    """변경이력 1건을 일괄 저장용 (sql, params)로 반환"""
    return (LOG_SQL, dict(d=TODAY(), p=product_name,
                          f=FIELD_LABELS.get(field, field),
                          o=str(old), n=str(new), t=KST_NOW()))


def qdf(sql: str, **params) -> pd.DataFrame:
    return pd.read_sql_query(text(sql), engine, params=params)


SNAPSHOT_SQL = """
INSERT INTO stock_snapshots (sdate, product_id, stock_box, stock_ea, total_ea)
SELECT :d, id, stock_box, stock_ea, stock_box * GREATEST(box_qty, 1) + stock_ea FROM products
ON CONFLICT (sdate, product_id)
DO UPDATE SET stock_box = EXCLUDED.stock_box, stock_ea = EXCLUDED.stock_ea, total_ea = EXCLUDED.total_ea
"""


def snapshot_today():
    """오늘 날짜의 전 제품 재고를 스냅샷으로 저장 (하루에 한 행, 같은 날은 최신값으로 갱신)"""
    try:
        run(SNAPSHOT_SQL, d=TODAY())
    except Exception:
        pass  # 스냅샷 실패가 앱 사용을 막지 않도록


def clear_cache(*editor_keys):
    """저장 후: 오늘 재고 스냅샷 갱신 + 조회 캐시 초기화 + 표 편집기 상태 초기화.
    ※ st.data_editor는 key별로 수정 내역(delta)을 세션에 보관했다가 재실행 때 다시 덧씌운다.
       저장 후 이를 지우지 않으면 DB에는 반영돼도 화면이 옛 값으로 되돌아간 것처럼 보인다."""
    snapshot_today()
    st.cache_data.clear()
    for k in editor_keys:
        st.session_state.pop(k, None)


# ──────────────────────────────────────────────
# 공통 함수
# ──────────────────────────────────────────────
FIELD_LABELS = {
    "name": "제품명", "barcode": "바코드", "box_qty": "박스입수량", "spec": "규격(무게)",
    "delivery_ea": "납품갯수(낱개)",
    "normal_price": "정상가", "sale_price": "할인판매가", "storage": "보관방법",
    "is_new": "신규/기존", "stock_box": "현재고(박스)", "stock_ea": "현재고(낱개)",
    "safety_box": "안전재고(박스)", "safety_ea": "안전재고(낱개환산)", "memo": "메모", "image_name": "이미지", "updated_at": "저장시각",
}


def add_log(product_name: str, field: str, old, new):
    sql, params = log_op(product_name, field, old, new)
    run(sql, **params)


@st.cache_data(ttl=20, show_spinner=False)
def df_products() -> pd.DataFrame:
    # image_data(용량 큼)는 제외하고 조회 / 20초 캐싱으로 화면 전환 속도 개선
    return qdf(
        "SELECT id, name, barcode, box_qty, spec, normal_price, sale_price, storage, "
        "is_new, delivery_ea, stock_box, stock_ea, safety_box, safety_ea, image_name, memo, created_at, updated_at "
        "FROM products ORDER BY id")


@st.cache_data(ttl=20, show_spinner=False)
def df_stores() -> pd.DataFrame:
    return qdf("SELECT * FROM stores ORDER BY id")


@st.cache_data(ttl=20, show_spinner=False)
def manual_diffs() -> pd.DataFrame:
    """제품별: 현재고 vs 기록잔여(입고−출고, FIFO) 비교 → 수동조정분(diff) 산출"""
    prods = df_products()
    if prods.empty:
        return pd.DataFrame()
    net = qdf("""
        SELECT p.id AS pid,
               COALESCE(SUM(CASE WHEN t.ttype='입고' THEN t.qty_box*GREATEST(p.box_qty,1)+t.qty_ea
                                 WHEN t.ttype='출고' THEN -(t.qty_box*GREATEST(p.box_qty,1)+t.qty_ea)
                                 ELSE 0 END), 0) AS 순기록
        FROM products p
        LEFT JOIN transactions t ON t.product_id = p.id AND t.ttype IN ('입고','출고')
        GROUP BY p.id""")
    inout = qdf("""
        SELECT p.id AS pid,
               COALESCE(SUM(CASE WHEN t.ttype='입고' THEN t.qty_box*GREATEST(p.box_qty,1)+t.qty_ea ELSE 0 END),0) AS 입고합,
               COALESCE(SUM(CASE WHEN t.ttype='출고' THEN t.qty_box*GREATEST(p.box_qty,1)+t.qty_ea ELSE 0 END),0) AS 출고합
        FROM products p
        LEFT JOIN transactions t ON t.product_id = p.id AND t.ttype IN ('입고','출고')
        GROUP BY p.id""")
    io_map = {int(r["pid"]): (int(r["입고합"]), int(r["출고합"])) for _, r in inout.iterrows()}
    rows = []
    for _, r in prods.iterrows():
        pid = int(r["id"])
        cur = total_ea(r)
        IN_t, OUT_t = io_map.get(pid, (0, 0))
        M0 = cur - IN_t + OUT_t
        if M0 >= 0:
            out_for_lots = max(OUT_t - M0, 0)
            manual_left = M0 - (OUT_t - out_for_lots)
        else:
            out_for_lots = OUT_t + (-M0)
            manual_left = 0
        rec = max(IN_t - out_for_lots, 0)          # 소비기한 로트 잔여(합)
        manual = cur - rec                          # 수동조정분 (= manual_left + 안전망 잔차)
        rows.append(dict(pid=pid, 제품명=r["name"], box_qty=bq_of(r["box_qty"]),
                         현재고=cur, 기록잔여=rec, 수동조정분=manual))
    return pd.DataFrame(rows)


@st.cache_data(ttl=20, show_spinner=False)
def expiry_breakdown() -> pd.DataFrame:
    """제품별 · 소비기한별 잔여 낱개 계산 (현재 재고와 합계 일치 보정 포함).
    1) 입고 로트(소비기한별)에서 총출고량을 기한 빠른 순(FIFO)으로 차감
    2) 그 결과 합계를 '현재 재고(표에서 수동 수정한 값 포함)'와 비교해 보정:
       - 실재고가 더 적으면 → 차이를 기한 빠른 로트부터 추가 차감
       - 실재고가 더 많으면 → '(수동조정분)' 행으로 ± 표시
    → 잔여 합계가 항상 현재 재고(총낱개환산)와 일치"""
    prods = df_products()
    if prods.empty:
        return pd.DataFrame()
    lots = qdf("""
        SELECT p.id AS pid, p.name AS 제품명, p.box_qty,
               t.expiry_date AS 소비기한,
               SUM(t.qty_box * GREATEST(p.box_qty,1) + t.qty_ea) AS 입고낱개
        FROM transactions t JOIN products p ON p.id = t.product_id
        WHERE t.ttype = '입고'
        GROUP BY p.id, p.name, p.box_qty, t.expiry_date""")
    outs = qdf("""
        SELECT product_id AS pid,
               SUM(t.qty_box * GREATEST(p.box_qty,1) + t.qty_ea) AS 출고낱개
        FROM transactions t JOIN products p ON p.id = t.product_id
        WHERE t.ttype = '출고' GROUP BY product_id""")
    out_map = dict(zip(outs["pid"], outs["출고낱개"])) if not outs.empty else {}
    cur_map = {int(r["id"]): (r["name"], bq_of(r["box_qty"]), total_ea(r))
               for _, r in prods.iterrows()}

    rows = []
    lot_pids = set(lots["pid"].tolist()) if not lots.empty else set()
    for pid, (pname, bq, cur_total) in cur_map.items():
        # ── 차감 순서 원칙 ──
        #  수동으로 잡아둔 재고(기록 이전부터 있던 오래된 물량)가 먼저 나간 것으로 보고,
        #  출고는 ① 수동풀(M0)부터 소진 → ② 남으면 소비기한 빠른 로트 순(FIFO)으로 차감.
        #  → 일일기록으로 입고한 소비기한 로트가 수동재고 출고 때문에 깎여 보이는 문제 방지.
        IN_total = 0
        g = None
        if pid in lot_pids:
            g = lots[lots["pid"] == pid].copy()
            g["_ord"] = g["소비기한"].apply(lambda v: v if v else "9999-99-99")
            g = g.sort_values("_ord")
            IN_total = int(g["입고낱개"].sum())
        OUT_total = int(out_map.get(pid, 0))
        M0 = int(cur_total) - IN_total + OUT_total   # 기록 밖(수동) 기반 재고량

        if M0 >= 0:
            out_for_lots = max(OUT_total - M0, 0)     # 수동풀 먼저 소진 후 남는 출고량
            manual_left = M0 - (OUT_total - out_for_lots)
        else:
            out_for_lots = OUT_total + (-M0)          # 수동 감소분은 추가 출고처럼 로트에서 차감
            manual_left = 0

        leftovers = []
        if g is not None:
            remain_out = out_for_lots
            for _, r in g.iterrows():
                qty = int(r["입고낱개"])
                used = min(qty, remain_out)
                remain_out -= used
                if qty - used > 0:
                    leftovers.append([r["_ord"], r["소비기한"] or "(기한없음)", qty - used])
        if manual_left > 0:
            leftovers.append(["9999-99-98", "(수동조정분)", manual_left])
        # 안전망: 어떤 경우에도 합계 = 현재고
        residual = int(cur_total) - sum(x[2] for x in leftovers)
        if residual != 0:
            leftovers.append(["9999-99-97", "(수동조정분)", residual])

        for _ord, exp, left in leftovers:
            dday = ""
            try:
                dd = (pd.Timestamp(str(exp)).date() - today_kst()).days
                dday = f"D{dd:+d}" if dd < 0 else (f"D-{dd}" if dd > 0 else "D-DAY")
            except Exception:
                dday = ""  # 날짜가 아닌 항목((기한없음)/(기한미상)) 은 공란
            if left >= 0:
                bx = f"{left // bq}박스 {left % bq}낱개"
            else:
                bx = f"-{(-left) // bq}박스 {(-left) % bq}낱개"
            rows.append(dict(제품명=pname, 소비기한=exp,
                             잔여낱개환산=left,
                             박스환산=bx,
                             디데이=dday))
    return pd.DataFrame(rows)


@st.cache_data(ttl=20, show_spinner=False)
def df_memos(d1=None, d2=None) -> pd.DataFrame:
    q = "SELECT mdate AS 날짜, content AS 메모, updated_at AS 수정시각 FROM daily_memos"
    cond, params = [], {}
    if d1: cond.append("mdate >= :d1"); params["d1"] = d1
    if d2: cond.append("mdate <= :d2"); params["d2"] = d2
    if cond: q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY mdate DESC"
    return qdf(q, **params)


@st.cache_data(ttl=20, show_spinner=False)
def df_snapshots() -> pd.DataFrame:
    return qdf(
        """SELECT n.sdate AS 날짜, p.name AS 제품명, n.stock_box AS 박스, n.stock_ea AS 낱개,
                  n.total_ea AS 재고환산낱개
           FROM stock_snapshots n JOIN products p ON p.id = n.product_id
           ORDER BY n.sdate, p.name""")


def search_box(df: pd.DataFrame, key: str, label: str = "🔍 검색") -> pd.DataFrame:
    """표 위에 검색창을 붙이고, 입력어가 포함된 행만 반환 (모든 컬럼 대상, 대소문자 무시)"""
    q = st.text_input(label, key=key, placeholder="제품명·매장명 등 일부만 입력해도 검색됩니다")
    if q:
        mask = df.apply(lambda r: r.astype(str).str.contains(q, case=False, na=False, regex=False).any(), axis=1)
        return df[mask]
    return df


def csv_bytes(df: pd.DataFrame) -> bytes:
    """엑셀에서 한글 깨짐 없이 열리는 CSV (utf-8-sig)"""
    return df.to_csv(index=False).encode("utf-8-sig")


def csv_button(df: pd.DataFrame, name: str, key: str):
    st.download_button(f"📥 {name} CSV 내보내기", data=csv_bytes(df),
                       file_name=f"{name}_{TODAY()}.csv", mime="text/csv", key=key)


def _lighten(hex_color: str, ratio: float = 0.72) -> str:
    """셀 배경용으로 색을 밝게 (흰색과 혼합)"""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4))
    mix = lambda c: int(c + (255 - c) * ratio)
    return f"#{mix(r):02X}{mix(g):02X}{mix(b):02X}"


def table_png(df: pd.DataFrame, day_col: str = "납품요일") -> bytes:
    """표를 PNG 이미지로 렌더링. 납품요일 셀은 요일별 색상(월/수/금 등 각각 다름)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    # 한글 폰트 (Streamlit Cloud: packages.txt에 fonts-nanum 필요)
    for fp in ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
               "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf"):
        if os.path.exists(fp):
            font_manager.fontManager.addfont(fp)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=fp).get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False

    df = df.fillna("").astype(str)
    n_rows, n_cols = len(df), len(df.columns)
    # 여러 줄 셀(줄바꿈 포함) 대응: 행별 최대 줄 수 계산
    row_lines = [max(str(v).count("\n") + 1 for v in df.iloc[i]) for i in range(n_rows)]
    total_lines = sum(row_lines)
    fig, ax = plt.subplots(figsize=(max(8, n_cols * 1.7), max(2.2, 0.85 + 0.42 * total_lines)))
    ax.axis("off")
    tbl = ax.table(cellText=df.values, colLabels=df.columns, loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    tbl.scale(1, 1.5)
    tbl.auto_set_column_width(col=list(range(n_cols)))
    # 줄 수에 비례해 행 높이 확장
    for i in range(1, n_rows + 1):
        if row_lines[i - 1] > 1:
            for j in range(n_cols):
                cell = tbl[i, j]
                cell.set_height(cell.get_height() * row_lines[i - 1] * 0.9)
    # 헤더 스타일
    for j in range(n_cols):
        tbl[0, j].set_facecolor("#37474F")
        tbl[0, j].get_text().set_color("white")
        tbl[0, j].get_text().set_weight("bold")
    # 요일 셀 색상 (여러 요일이면 첫 요일 기준 배경 + 전체 텍스트 표시)
    if day_col in df.columns:
        j = list(df.columns).index(day_col)
        for i, v in enumerate(df[day_col].tolist(), start=1):
            first = str(v).split(",")[0].strip()
            if first in DAY_COLOR_MAP:
                tbl[i, j].set_facecolor(_lighten(DAY_COLOR_MAP[first]))
                tbl[i, j].get_text().set_weight("bold")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


@st.cache_data(ttl=20, show_spinner=False)
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


@st.cache_data(ttl=20, show_spinner=False)
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


def bq_of(v) -> int:
    """박스입수량 안전 변환 (미입력/NaN/0 → 1)"""
    try:
        n = int(v)
    except Exception:
        return 1
    return max(n, 1)


def fmt_stock(box, ea, box_qty) -> str:
    """재고를 박스 단위로 자동 정규화해 표시 (보기 전용, 저장값은 그대로).
    예: 박스입수량 12, 재고 낱개 75 → '6박스 3낱개'"""
    bq = bq_of(box_qty)
    total = int(box or 0) * bq + int(ea or 0)
    if total < 0:
        return f"{total}낱개(음수)"
    return f"{total // bq}박스 {total % bq}낱개"


def safety_mark(total: int, safety) -> str:
    """안전재고 대비 상태: 🟢 충족 / 🔴 부족 / ⚪ 미설정"""
    s = int(safety) if pd.notna(safety) else 0
    if s <= 0:
        return "⚪"
    return "🟢" if total >= s else "🔴"


def total_ea(row) -> int:
    return int(row["stock_box"] or 0) * bq_of(row["box_qty"]) + int(row["stock_ea"] or 0)


def normalize_stock(box: int, ea: int, box_qty: int):
    """낱개가 박스입수량 이상이면 박스로 자동 변환. 예) 입수량12, 낱개30 → 박스+2 낱개6"""
    bq = bq_of(box_qty)
    total = int(box or 0) * bq + int(ea or 0)
    if total >= 0:
        return total // bq, total % bq
    return 0, total  # 음수 재고는 낱개에 표시


def normalize_stock(box: int, ea: int, box_qty: int):
    """낱개가 박스입수량 이상이면 박스로 자동 환산 (예: 입수량 12, 낱개 50 → 박스 +4, 낱개 2)"""
    bq = bq_of(box_qty)
    total = int(box or 0) * bq + int(ea or 0)
    if total < 0:
        return int(box), int(ea)
    return total // bq, total % bq


@st.cache_data(ttl=20, show_spinner=False)
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


@st.cache_data(ttl=20, show_spinner=False)
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
                       "storage", "delivery_ea", "stock_box", "stock_ea", "총낱개환산", "safety_ea", "memo", "updated_at"]]
        prods.columns = ["제품명", "바코드", "구분", "박스입수량", "규격(무게)", "정상가", "할인판매가",
                         "보관방법", "납품갯수(낱개)", "재고(박스)", "재고(낱개)", "총낱개환산", "안전재고(낱개환산)", "메모", "저장시각"]

    tx = df_transactions(d1, d2).drop(columns=["id"], errors="ignore")
    logs = df_logs(d1, d2)
    stores = df_stores().rename(columns={"name": "매장명", "location": "납품개소",
                                         "delivery_day": "납품요일", "phone": "점주전화번호",
                                         "memo": "메모", "note": "특이사항"})
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
        memos_all = df_memos(d1, d2)
        (memos_all if not memos_all.empty else pd.DataFrame({"안내": ["메모 없음"]})).to_excel(writer, sheet_name="일자메모", index=False)
        for ws in writer.book.worksheets:
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(width * 1.8 + 2, 40)
    return buf.getvalue()


# ──────────────────────────────────────────────
# 사이드바 메뉴
# ──────────────────────────────────────────────
# 접속 시 오늘 재고 스냅샷 자동 저장 (세션당 1회)
if not st.session_state.get("snapshot_done"):
    snapshot_today()
    st.session_state["snapshot_done"] = True

st.sidebar.title("📦 삼립 무인편의점 재고관리")
page = st.sidebar.radio(
    "메뉴",
    ["📊 대시보드", "📝 일일 기록", "📈 일자별 누적(수불부)", "📦 제품 관리(엑셀표)", "🏬 납품처 관리(엑셀표)", "📋 납품 정리표(매장×제품)", "🗒️ 일자별 메모", "📜 변경이력", "⬇️ 엑셀 내보내기"],
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

    if not prods.empty:
        _p = prods.copy()
        _p["총낱개환산"] = _p.apply(total_ea, axis=1)
        low = _p[(_p["safety_ea"] > 0) & (_p["총낱개환산"] < _p["safety_ea"])]
        if not low.empty:
            st.error(f"⚠️ 안전재고 미달 제품 {len(low)}개 — 발주 검토 필요")
            st.dataframe(
                low[["name", "stock_box", "stock_ea", "총낱개환산", "safety_ea"]].rename(columns={
                    "name": "제품명", "stock_box": "현재고(박스)", "stock_ea": "현재고(낱개)",
                    "safety_ea": "안전재고(낱개환산)"}),
                use_container_width=True, hide_index=True)

    # 소비기한 관련 표는 모두 하나의 계산(expiry_breakdown)에서 파생 → 수치 불일치 방지
    bd = expiry_breakdown()

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
            tab_raw, tab_calc = st.tabs(["📄 일일기록 그대로 (입력값)", "🧮 잔여 계산 (출고·수동조정 반영)"])

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
                    raw["디데이"] = raw["소비기한"].apply(
                        lambda e: (lambda dd: f"D{dd:+d}" if dd < 0 else (f"D-{dd}" if dd > 0 else "D-DAY"))(
                            (pd.Timestamp(e).date() - today_kst()).days))
                    def _hl_raw(row):
                        try:
                            dd = (pd.Timestamp(str(row["소비기한"])).date() - today_kst()).days
                        except Exception:
                            return [""] * len(row)
                        if dd < 0:
                            return ["background-color: #FFEBEE"] * len(row)
                        if dd <= 30:
                            return ["background-color: #FFF8E1"] * len(row)
                        return [""] * len(row)
                    raw_view = search_box(raw, "search_raw_exp", "🔍 제품명 검색")
                    st.dataframe(raw_view.style.apply(_hl_raw, axis=1),
                                 use_container_width=True, hide_index=True)
                    tot = raw_view.groupby("제품명", as_index=False)["환산낱개"].sum()
                    st.caption("제품별 입고 합계(환산낱개): " + " · ".join(
                        f"**{r['제품명']}** {int(r['환산낱개']):,}" for _, r in tot.iterrows()))
                    csv_button(raw_view, "소비기한_일일기록그대로", "csv_raw_exp")
                    st.caption("일일 기록에 입력한 입고 로트를 날짜·소비기한·수량 그대로 보여줍니다 (출고 차감 없음).")

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
                st.dataframe(show.style.apply(_hl_exp, axis=1),
                             use_container_width=True, hide_index=True)
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
                st.caption("입고 로트에서 출고량을 기한 빠른 순(FIFO)으로 차감한 뒤, 현재 재고(제품 관리 표에서 수동 수정한 값 포함)와 "
                           "합계가 일치하도록 자동 보정합니다. 일일기록의 소비기한 로트는 그대로 두고, 표에서 수동으로 조정한 차이는 '(수동조정분)' 행에 ±로 모입니다. 수동조정분을 일일기록(입고/출고)으로 옮겨 적으면 이 행은 0이 되어 사라집니다.")

    st.subheader("재고 현황")
    if prods.empty:
        st.info("등록된 제품이 없습니다. [제품 관리] 메뉴에서 제품을 추가하세요.")
    else:
        tab_now, tab_trend = st.tabs(["📋 현재 재고 현황", "📈 일자별 재고 추세 (자동 저장)"])
        with tab_now:
            view = prods.copy()
            view["총낱개환산"] = view.apply(total_ea, axis=1)
            view = view[["name", "barcode", "is_new", "box_qty", "spec", "normal_price", "sale_price",
                         "storage", "delivery_ea", "stock_box", "stock_ea", "총낱개환산", "safety_ea"]]
            view.columns = ["제품명", "바코드", "구분", "박스입수량", "규격(무게)", "정상가", "할인판매가",
                            "보관방법", "납품갯수(낱개)", "재고(박스)", "재고(낱개)", "총낱개환산", "안전재고(낱개환산)"]

            def _row_style(row):
                s = int(row["안전재고(낱개환산)"] or 0)
                if s > 0:
                    ok = int(row["총낱개환산"]) >= s
                    return [f"background-color: {'#E8F5E9' if ok else '#FFEBEE'}"] * len(row)
                return [""] * len(row)

            st.dataframe(view.style.apply(_row_style, axis=1),
                         use_container_width=True, hide_index=True)
            st.caption("🟩 연초록 = 안전재고 충족 · 🟥 연빨강 = 안전재고 미달 · 무색 = 안전재고 미설정 (제품 관리에서 제품별 입력)")
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
                    st.caption("제품 관리(엑셀표)에서 수정한 재고를 포함해, 매일의 마지막 재고 상태가 날짜별로 자동 저장됩니다. "
                               "세로축 = 재고(총낱개환산).")
                    csv_button(sub, "재고추세", "csv_snap")

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
            exp_date = c8.date_input("소비기한", value=today_kst(), label_visibility="collapsed")
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
                    ops = [(
                        "INSERT INTO transactions (tdate, product_id, ttype, store_id, qty_box, qty_ea, expiry_date, memo, created_at) "
                        "VALUES (:d, :p, :t, :s, :qb, :qe, :ex, :m, :c)",
                        dict(d=tdate.strftime("%Y-%m-%d"), p=pid, t=ttype, s=sid,
                             qb=int(qty_box), qe=int(qty_ea),
                             ex=exp_date.strftime("%Y-%m-%d") if exp_use else "",
                             m=memo, c=KST_NOW()))]
                    if ttype in ("입고", "출고"):
                        sign = 1 if ttype == "입고" else -1
                        new_box = int(prow["stock_box"]) + sign * int(qty_box)
                        new_ea = int(prow["stock_ea"]) + sign * int(qty_ea)
                        while new_ea < 0 and new_box > 0:
                            new_box -= 1
                            new_ea += box_qty
                        ops.append(("UPDATE products SET stock_box=:b, stock_ea=:e, updated_at=:u WHERE id=:i",
                                    dict(b=new_box, e=new_ea, u=KST_NOW(), i=pid)))
                        ops.append(log_op(pname, "stock_box" if qty_box else "stock_ea",
                                          f"박스 {prow['stock_box']} / 낱개 {prow['stock_ea']}",
                                          f"박스 {new_box} / 낱개 {new_ea} ({ttype} {qty_box}박스 {qty_ea}낱개 = 환산 {conv}낱개)"))
                    run_batch(ops)  # 한 번의 트랜잭션으로 저장
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

            store_opts = ["(총량 / 매장 미지정)"] + stores["name"].tolist()
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
                        sid_map = dict(zip(stores["name"], stores["id"]))
                        prow_map = {r["name"]: r for _, r in prods.iterrows()}
                        ops = []
                        stock_delta = {}  # pid → 환산낱개 변화 합 (입고+, 출고-)
                        for _, r in valid.iterrows():
                            pr = prow_map[r["제품명"]]
                            pid = int(pr["id"])
                            sid = sid_map.get(r["매장"]) if r["매장"] in sid_map else None
                            ex = "" if pd.isna(r["소비기한"]) else pd.Timestamp(r["소비기한"]).strftime("%Y-%m-%d")
                            ops.append((
                                "INSERT INTO transactions (tdate, product_id, ttype, store_id, qty_box, qty_ea, expiry_date, memo, created_at) "
                                "VALUES (:d, :p, :t, :s, :qb, :qe, :ex, :m, :c)",
                                dict(d=bdate.strftime("%Y-%m-%d"), p=pid, t=r["구분"],
                                     s=int(sid) if sid is not None else None,
                                     qb=int(r["박스"]), qe=int(r["낱개"]), ex=ex, m=r["메모"], c=KST_NOW())))
                            if r["구분"] in ("입고", "출고"):
                                sign = 1 if r["구분"] == "입고" else -1
                                bq = max(int(pr["box_qty"]), 1)
                                stock_delta[pid] = stock_delta.get(pid, 0) + sign * (int(r["박스"]) * bq + int(r["낱개"]))
                        # 제품별 재고 일괄 반영 (환산낱개 → 박스/낱개 재배분)
                        for pid, delta in stock_delta.items():
                            pr = next(p for p in prow_map.values() if int(p["id"]) == pid)
                            bq = max(int(pr["box_qty"]), 1)
                            total = int(pr["stock_box"]) * bq + int(pr["stock_ea"]) + delta
                            new_box, new_ea = (total // bq, total % bq) if total >= 0 else (0, total)
                            ops.append(("UPDATE products SET stock_box=:b, stock_ea=:e, updated_at=:u WHERE id=:i",
                                        dict(b=new_box, e=new_ea, u=KST_NOW(), i=pid)))
                            ops.append(log_op(pr["name"], "stock_box",
                                              f"박스 {pr['stock_box']} / 낱개 {pr['stock_ea']}",
                                              f"박스 {new_box} / 낱개 {new_ea} (일괄기록 환산 {'+' if delta>=0 else ''}{delta}낱개)"))
                        run_batch(ops)
                        clear_cache("daily_bulk_editor")
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
                format_func=lambda i: "선택 안 함" if i == 0 else
                f"#{i} | " + " | ".join(map(str, tx[tx['id'] == i][['날짜','제품명','구분','매장','박스','낱개']].iloc[0].tolist())),
            )
            if del_id and st.button("🗑️ 선택 기록 삭제"):
                row = qdf("SELECT product_id, ttype, qty_box, qty_ea FROM transactions WHERE id=:i", i=del_id)
                if not row.empty:
                    r = row.iloc[0]
                    ops = []
                    if r["ttype"] in ("입고", "출고"):
                        sign = -1 if r["ttype"] == "입고" else 1
                        pr2 = prods[prods["id"] == int(r["product_id"])].iloc[0]
                        nb2, ne2 = normalize_stock(
                            int(pr2["stock_box"]) + sign * int(r["qty_box"]),
                            int(pr2["stock_ea"]) + sign * int(r["qty_ea"]),
                            int(pr2["box_qty"]))
                        ops.append(("UPDATE products SET stock_box=:b, stock_ea=:e, updated_at=:u WHERE id=:i",
                                    dict(b=nb2, e=ne2, u=KST_NOW(), i=int(r["product_id"]))))
                    ops.append(("DELETE FROM transactions WHERE id=:i", {"i": del_id}))
                    run_batch(ops)
                    clear_cache()
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
                led_view = search_box(led.drop(columns=["pid", "기초재고"]), "search_led", "🔍 제품 검색")
                st.dataframe(led_view, use_container_width=True, hide_index=True)
                csv_button(led.drop(columns=["pid"]), "수불부전체", "csv_led")
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
                 "storage", "delivery_ea", "stock_box", "stock_ea", "safety_ea", "memo", "updated_at"]
    grid = prods_view[grid_cols].copy() if not prods_view.empty else pd.DataFrame(columns=grid_cols)

    edited = st.data_editor(
        grid,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=["id", "updated_at", "박스환산"],
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
            "stock_ea": st.column_config.NumberColumn(
                "재고(낱개)", step=1,
                help="박스입수량 이상 입력하면 저장 시 박스로 자동 변환됩니다"),
            "safety_ea": st.column_config.NumberColumn(
                "안전재고(낱개환산)", min_value=0, step=1,
                help="제품별 안전재고. 총재고(낱개환산)가 이 값 이상이면 대시보드에서 초록 배경으로 표시"),
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
                "stock_box": int(r["stock_box"]) if pd.notna(r["stock_box"]) else 0,
                "stock_ea": int(r["stock_ea"]) if pd.notna(r["stock_ea"]) else 0,
                "safety_ea": int(r["safety_ea"]) if pd.notna(r["safety_ea"]) else 0,
                "memo": "" if pd.isna(r["memo"]) else str(r["memo"]),
            }
            # 낱개 → 박스 자동 변환 (예: 입수량 12, 낱개 30 → 박스 +2, 낱개 6)
            vals["stock_box"], vals["stock_ea"] = normalize_stock(
                vals["stock_box"], vals["stock_ea"], vals["box_qty"])
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

    # ── 수동입력분 정리 도구 ──
    st.divider()
    with st.expander("🧹 수동입력분 정리 — 소비기한 수량 맞추기", expanded=False):
        md = manual_diffs()
        targets = md[md["수동조정분"] != 0] if not md.empty else pd.DataFrame()
        if targets.empty:
            st.success("✅ 모든 제품의 재고가 입출고 기록과 일치합니다. 정리할 수동입력분이 없습니다.")
        else:
            st.caption("현재고와 입출고 기록이 어긋난 제품 목록입니다. 제품을 골라 처리 방법을 선택하세요.")
            st.dataframe(targets[["제품명", "현재고", "기록잔여", "수동조정분"]],
                         use_container_width=True, hide_index=True)

            fix_name = st.selectbox("정리할 제품", targets["제품명"].tolist(), key="fix_prod")
            row = targets[targets["제품명"] == fix_name].iloc[0]
            fpid, fbq = int(row["pid"]), int(row["box_qty"])
            fdiff = int(row["수동조정분"])
            st.info(f"**{fix_name}** — 현재고 {row['현재고']:,}낱개 / 기록잔여 {row['기록잔여']:,}낱개 / "
                    f"수동조정분 **{fdiff:+,}낱개**")

            method = st.radio("처리 방법", [
                "🗑️ 수동분 삭제 — 재고를 기록 기준으로 되돌림 (소비기한 표와 즉시 일치)",
                "📝 기록으로 전환 — 재고는 유지하고 입고/출고 기록을 생성 (소비기한 지정 가능)",
            ], key="fix_method")

            exp_use2 = exp_date2 = None
            if method.startswith("📝") and fdiff > 0:
                exp_use2 = st.checkbox("전환되는 입고분에 소비기한 입력", key="fix_exp_use")
                exp_date2 = st.date_input("소비기한", value=today_kst(), key="fix_exp_date",
                                          label_visibility="collapsed")

            if st.button("⚡ 정리 실행", type="primary", use_container_width=True, key="fix_run"):
                ops = []
                if method.startswith("🗑️"):
                    rec = int(row["기록잔여"])
                    nb, ne = rec // fbq, rec % fbq
                    ops.append(("UPDATE products SET stock_box=:b, stock_ea=:e, updated_at=:u WHERE id=:i",
                                dict(b=nb, e=ne, u=KST_NOW(), i=fpid)))
                    ops.append(log_op(fix_name, "stock_box",
                                      f"수동조정분 {fdiff:+,}낱개 포함 {row['현재고']:,}낱개",
                                      f"기록 기준 {rec:,}낱개(박스 {nb}/낱개 {ne})로 정리 [수동분 삭제]"))
                else:
                    ttype2 = "입고" if fdiff > 0 else "출고"
                    qty = abs(fdiff)
                    ex2 = exp_date2.strftime("%Y-%m-%d") if (exp_use2 and exp_date2) else ""
                    ops.append((
                        "INSERT INTO transactions (tdate, product_id, ttype, store_id, qty_box, qty_ea, expiry_date, memo, created_at) "
                        "VALUES (:d, :p, :t, NULL, 0, :qe, :ex, :m, :c)",
                        dict(d=TODAY(), p=fpid, t=ttype2, qe=qty, ex=ex2,
                             m="[수동분 기록전환] 재고-기록 차이 정리", c=KST_NOW())))
                    ops.append(log_op(fix_name, "stock_box",
                                      f"수동조정분 {fdiff:+,}낱개",
                                      f"{ttype2} {qty:,}낱개 기록으로 전환 (재고 변동 없음)"))
                run_batch(ops)
                clear_cache("prod_editor")
                st.success(f"✅ '{fix_name}' 정리 완료 — 소비기한별 잔여 수량이 이제 일치합니다.")
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
    grid_cols = ["id", "name", "location", "delivery_day", "phone", "memo", "note"]
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
            "phone": st.column_config.TextColumn(
                "점주 전화번호", validate=r"^[0-9\-\s]*$",
                help="숫자와 하이픈(-)만 입력. 예: 010-1234-5678"),
            "memo": st.column_config.TextColumn("메모"),
            "note": st.column_config.TextColumn("특이사항"),
        }, key="store_editor")
    export_df = grid.rename(columns={"name": "매장명", "location": "납품개소",
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
            if pd.notna(r["id"]) and int(r["id"]) in old_map:
                rid = int(r["id"]); seen.add(rid)
                old = old_map[rid]
                if (old["name"], old["location"], old["delivery_day"], old["phone"], old["memo"], old["note"]) != (nm, loc, dd, ph, mm, nt):
                    ops.append(("UPDATE stores SET name=:n, location=:l, delivery_day=:d, phone=:p, memo=:m, note=:nt WHERE id=:i",
                                dict(n=nm, l=loc, d=dd, p=ph, m=mm, nt=nt, i=rid)))
                    ops.append(log_op(f"[매장] {nm}", "매장정보",
                                      f"{old['name']} / {old['location']} / {old['delivery_day'] or '요일미지정'} / {old['phone'] or '번호없음'}",
                                      f"{nm} / {loc} / {dd or '요일미지정'} / {ph or '번호없음'}"))
            else:
                if nm in existing_store_names:
                    st.warning(f"'{nm}' 매장은 이미 존재합니다.")
                    continue
                existing_store_names.add(nm)
                ops.append(("INSERT INTO stores (name, location, delivery_day, phone, memo, note) VALUES (:n, :l, :d, :p, :m, :nt)",
                            dict(n=nm, l=loc, d=dd, p=ph, m=mm, nt=nt)))
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
