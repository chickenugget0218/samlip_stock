# -*- coding: utf-8 -*-
"""core.py — DB 연결·스키마·조회/계산 로직 (UI 없음). app.py에서 import하여 사용."""
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



def get_secret(key: str, default: str = "") -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)




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


@st.cache_data(ttl=60, show_spinner=False)
def df_products() -> pd.DataFrame:
    # image_data(용량 큼)는 제외하고 조회 / 20초 캐싱으로 화면 전환 속도 개선
    return qdf(
        "SELECT id, name, barcode, box_qty, spec, normal_price, sale_price, storage, "
        "is_new, delivery_ea, stock_box, stock_ea, safety_box, safety_ea, image_name, memo, created_at, updated_at "
        "FROM products ORDER BY id")


@st.cache_data(ttl=60, show_spinner=False)
def df_stores() -> pd.DataFrame:
    return qdf("SELECT * FROM stores ORDER BY id")


@st.cache_data(ttl=60, show_spinner=False)
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


@st.cache_data(ttl=60, show_spinner=False)
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


@st.cache_data(ttl=60, show_spinner=False)
def df_memos(d1=None, d2=None) -> pd.DataFrame:
    q = "SELECT mdate AS 날짜, content AS 메모, updated_at AS 수정시각 FROM daily_memos"
    cond, params = [], {}
    if d1: cond.append("mdate >= :d1"); params["d1"] = d1
    if d2: cond.append("mdate <= :d2"); params["d2"] = d2
    if cond: q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY mdate DESC"
    return qdf(q, **params)


@st.cache_data(ttl=60, show_spinner=False)
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


@st.cache_data(ttl=60, show_spinner=False)
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


@st.cache_data(ttl=60, show_spinner=False)
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


@st.cache_data(ttl=60, show_spinner=False)
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


@st.cache_data(ttl=60, show_spinner=False)
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