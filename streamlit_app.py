import json
import re
import os

import psycopg
from psycopg.rows import dict_row
from datetime import date, datetime
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st
from openpyxl.styles import Alignment, Font, PatternFill


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="ICL Chemical Manager",
    page_icon="🧪",
    layout="wide",
)
st.markdown("""
<style>


/* =========================================================
   GLOBAL
   ========================================================= */

.stApp {
    background-color: #F7F9FC;
}

/* Main page width */
.block-container {
    padding-top: 2rem;
    padding-bottom: 3rem;
    max-width: 1450px;
}


/* =========================================================
   SIDEBAR
   ========================================================= */

[data-testid="stSidebar"] {
    background-color: #FFFFFF;
    border-right: 1px solid #E5E7EB;
}

[data-testid="stSidebar"] > div:first-child {
    padding-top: 1.5rem;
}


/* =========================================================
   TITLES
   ========================================================= */

h1 {
    color: #172033;
    font-weight: 700;
    letter-spacing: -0.5px;
}

h2, h3 {
    color: #25324B;
}


/* =========================================================
   INPUTS
   ========================================================= */

.stTextInput input {
    border-radius: 10px;
    border: 1px solid #D8DEE9;
    background-color: #FFFFFF;
}

.stTextInput input:focus {
    border-color: #3B82F6;
}


/* =========================================================
   SELECTBOX
   ========================================================= */

[data-baseweb="select"] > div {
    border-radius: 10px;
}


/* =========================================================
   BUTTONS
   ========================================================= */

.stButton > button {
    border-radius: 8px;
    padding: 0.45rem 1rem;
    font-weight: 500;
    border: 1px solid #D8DEE9;
    white-space: nowrap;
}

.stButton > button:hover {
    border-color: #3B82F6;
}


/* Primary button */
button[kind="primary"] {
    background-color: #2563EB;
    color: white;
    border: none;
}

button[kind="primary"]:hover {
    background-color: #1D4ED8;
}


/* =========================================================
   DATAFRAME
   ========================================================= */

[data-testid="stDataFrame"] {
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid #E5E7EB;
}


/* =========================================================
   METRIC / CARDS
   ========================================================= */

[data-testid="stMetric"] {
    background: #FFFFFF;
    padding: 18px;
    border-radius: 12px;
    border: 1px solid #E5E7EB;
}


/* =========================================================
   FORMS
   ========================================================= */

[data-testid="stForm"] {
    background-color: #FFFFFF;
    padding: 22px;
    border-radius: 14px;
    border: 1px solid #E5E7EB;
}


/* =========================================================
   TABS
   ========================================================= */

button[data-baseweb="tab"] {
    font-size: 15px;
    font-weight: 500;
}


/* =========================================================
   HIDE STREAMLIT DEFAULT UI
   ========================================================= */

#MainMenu {
    visibility: hidden;
}

footer {
    visibility: hidden;
}

</style>
""", unsafe_allow_html=True)

# ============================================================
# DATABASE — SUPABASE POSTGRESQL
# ============================================================


def _database_url():
    """Use Codespaces env var locally or Streamlit Secrets in deployment."""
    env_url = os.getenv("SUPABASE_DB_URL", "").strip()
    if env_url:
        return env_url

    # Streamlit Community Cloud exposes top-level secrets as environment
    # variables in many deployments, but also support direct st.secrets access.
    try:
        direct_url = str(st.secrets.get("SUPABASE_DB_URL", "")).strip()
        if direct_url:
            return direct_url
    except Exception:
        pass

    try:
        nested_url = str(st.secrets["database"]["url"]).strip()
        if nested_url:
            return nested_url
    except Exception as exc:
        raise RuntimeError(
            "Supabase database URL is not configured. "
            "Set SUPABASE_DB_URL locally or add it to Streamlit Secrets."
        ) from exc

    raise RuntimeError(
        "Supabase database URL is not configured. "
        "Set SUPABASE_DB_URL locally or add it to Streamlit Secrets."
    )


def _translate_qmarks(sql):
    """Translate SQLite-style ? placeholders to psycopg %s safely."""
    out = []
    in_single = False
    in_double = False
    i = 0

    while i < len(sql):
        ch = sql[i]

        if ch == "'" and not in_double:
            # SQL escaped quote: ''
            if in_single and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_single = not in_single
            out.append(ch)

        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)

        elif ch == "?" and not in_single and not in_double:
            out.append("%s")

        else:
            out.append(ch)

        i += 1

    return "".join(out)


class CompatRow(dict):
    """Row compatible with sqlite3.Row: supports row['name'] and row[0]."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class CompatCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None

    def execute(self, sql, params=None):
        translated = _translate_qmarks(sql)
        stripped = translated.lstrip()
        is_insert = stripped.upper().startswith("INSERT INTO ")
        has_returning = bool(re.search(r"\bRETURNING\b", translated, flags=re.I))

        if is_insert and not has_returning:
            translated = translated.rstrip().rstrip(";") + " RETURNING id"

        self._cursor.execute(translated, params or ())

        if is_insert:
            try:
                returned = self._cursor.fetchone()
                if returned:
                    if isinstance(returned, dict):
                        self.lastrowid = returned.get("id")
                    else:
                        self.lastrowid = returned[0]
            except psycopg.ProgrammingError:
                self.lastrowid = None

        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        return CompatRow(row) if row is not None else None

    def fetchall(self):
        return [CompatRow(row) for row in self._cursor.fetchall()]

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        self._cursor.close()


class CompatConnection:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def execute(self, sql, params=None):
        cur = CompatCursor(self._conn.cursor(row_factory=dict_row))
        return cur.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_connection():
    raw = psycopg.connect(
        _database_url(),
        row_factory=dict_row,
        connect_timeout=10,
    )
    return CompatConnection(raw)


def init_db():
    """Ensure the Supabase schema exists. Existing migrated data are preserved."""
    conn = get_connection()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS storage_units (
            id BIGSERIAL PRIMARY KEY,
            storage_name TEXT NOT NULL,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_storage_units_name_lower
        ON storage_units (LOWER(storage_name))
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS storage_shelves (
            id BIGSERIAL PRIMARY KEY,
            storage_unit_id BIGINT NOT NULL REFERENCES storage_units(id),
            shelf_number INTEGER NOT NULL,
            UNIQUE(storage_unit_id, shelf_number)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chemicals (
            id BIGSERIAL PRIMARY KEY,
            chemical_name TEXT NOT NULL,
            cas_number TEXT,
            manufacturer TEXT,
            catalog_number TEXT,
            amount DOUBLE PRECISION,
            unit TEXT,
            location TEXT,
            status TEXT,
            created_at TEXT,
            bottle_id TEXT,
            legacy_id TEXT,
            company_management_id TEXT,
            category_classification TEXT,
            category_name TEXT,
            description TEXT,
            lot_number TEXT,
            initial_amount DOUBLE PRECISION,
            remaining_amount DOUBLE PRECISION,
            safety_stock TEXT,
            concentration_density TEXT,
            molecular_weight TEXT,
            mol TEXT,
            purity TEXT,
            solubility TEXT,
            molecular_formula TEXT,
            storage_temperature TEXT,
            flash_point TEXT,
            boiling_point TEXT,
            melting_point TEXT,
            owner TEXT,
            registered_by TEXT,
            purchaser TEXT,
            purchase_date TEXT,
            purchase_price TEXT,
            source_registered_date TEXT,
            source_last_modified TEXT,
            opened_date TEXT,
            expiration_date TEXT,
            comments TEXT,
            tags TEXT,
            legacy_location_path TEXT,
            legacy_storage_name TEXT,
            storage_unit_id BIGINT REFERENCES storage_units(id),
            shelf_number INTEGER,
            updated_at TEXT,
            disposed_at TEXT,
            disposal_reason TEXT,
            disposal_note TEXT,
            disposal_location TEXT,
            pubchem_cid TEXT,
            safety_signal_word TEXT,
            safety_h_codes TEXT,
            safety_hazard_statements TEXT,
            safety_pictograms TEXT,
            safety_source_url TEXT,
            safety_checked_at TEXT,
            safety_match_term TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sds_documents (
            id BIGSERIAL PRIMARY KEY,
            chemical_id BIGINT NOT NULL UNIQUE REFERENCES chemicals(id),
            file_name TEXT,
            file_data BYTEA,
            source_name TEXT,
            source_url TEXT,
            revision_date TEXT,
            uploaded_at TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_users (
            id BIGSERIAL PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT,
            role TEXT NOT NULL DEFAULT 'member',
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TEXT,
            updated_at TEXT,
            created_by TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id BIGSERIAL PRIMARY KEY,
            user_email TEXT NOT NULL,
            user_name TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id BIGINT,
            bottle_id TEXT,
            details TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_app_users_email ON app_users(LOWER(email))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_user_email ON audit_logs(LOWER(user_email))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action)"
    )

    # Keep status and amount fields normalized without changing user data.
    status_mapping = {
        "미개봉": "Unopened",
        "사용 중": "In Use",
        "소진": "Disposal Pending",
        "폐기 예정": "Disposal Pending",
        "폐기": "Disposed",
        "Empty": "Disposal Pending",
    }
    for old_value, new_value in status_mapping.items():
        conn.execute(
            "UPDATE chemicals SET status=? WHERE status=?",
            (new_value, old_value),
        )

    conn.execute(
        "UPDATE chemicals SET status='Unopened' "
        "WHERE status IS NULL OR TRIM(status)=''"
    )
    conn.execute(
        """
        UPDATE chemicals
        SET initial_amount = amount
        WHERE initial_amount IS NULL AND amount IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE chemicals
        SET remaining_amount = amount
        WHERE remaining_amount IS NULL AND amount IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE chemicals
        SET bottle_id = 'ICL-' || LPAD(id::text, 6, '0')
        WHERE bottle_id IS NULL OR TRIM(bottle_id)=''
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chem_name ON chemicals(chemical_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chem_cas ON chemicals(cas_number)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chem_status ON chemicals(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chem_legacy ON chemicals(legacy_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chem_storage ON chemicals(storage_unit_id)"
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# HELPERS
# ============================================================

LOCATION_EXPR = """
CASE
    WHEN su.storage_name IS NOT NULL AND c.shelf_number IS NOT NULL
        THEN su.storage_name || ' / Shelf ' || c.shelf_number
    WHEN su.storage_name IS NOT NULL
        THEN su.storage_name
    ELSE COALESCE(NULLIF(c.location, ''), 'Not Assigned')
END
"""


def clean_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    text = str(value).strip()
    if text.lower() == "nan" or text == "-":
        return ""
    return text


def clean_number(value):
    text = clean_text(value)
    if not text:
        return None

    try:
        return float(text.replace(",", ""))
    except Exception:
        return None


def clean_date(value):
    if value is None:
        return ""

    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")

    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")

    text = clean_text(value)
    if not text:
        return ""

    parsed = pd.to_datetime(text, errors="coerce")
    if not pd.isna(parsed):
        return parsed.strftime("%Y-%m-%d")

    return text


def normalize_unit(value):
    text = clean_text(value)
    mapping = {
        "ml": "mL",
        "milliliter": "mL",
        "milliliters": "mL",
        "l": "L",
        "liter": "L",
        "liters": "L",
        "g": "g",
        "mg": "mg",
        "kg": "kg",
        "ea": "ea",
    }
    return mapping.get(text.lower(), text)


def get_storage_options():
    conn = get_connection()
    units = conn.execute(
        "SELECT id, storage_name FROM storage_units ORDER BY storage_name"
    ).fetchall()
    shelves = conn.execute(
        """
        SELECT storage_unit_id, shelf_number
        FROM storage_shelves
        ORDER BY storage_unit_id, shelf_number
        """
    ).fetchall()
    conn.close()

    shelves_by_unit = {}
    for shelf in shelves:
        shelves_by_unit.setdefault(shelf["storage_unit_id"], []).append(
            shelf["shelf_number"]
        )

    options = [(None, None, "Not Assigned")]

    for unit in units:
        # Storage unit without a specific shelf
        options.append((unit["id"], None, unit["storage_name"]))

        for shelf_number in shelves_by_unit.get(unit["id"], []):
            options.append(
                (
                    unit["id"],
                    shelf_number,
                    f"{unit['storage_name']} / Shelf {shelf_number}",
                )
            )

    return options


def get_or_create_storage(conn, storage_name):
    storage_name = clean_text(storage_name)
    if not storage_name:
        return None

    row = conn.execute(
        "SELECT id FROM storage_units WHERE LOWER(storage_name)=LOWER(?)",
        (storage_name,),
    ).fetchone()

    if row:
        return row["id"]

    cursor = conn.execute(
        """
        INSERT INTO storage_units
        (storage_name, notes, created_at)
        VALUES (?, ?, ?)
        """,
        (
            storage_name,
            "Imported from Lab Manager Excel",
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        ),
    )
    return cursor.lastrowid


def current_location_index(options, storage_unit_id, shelf_number):
    for index, option in enumerate(options):
        if option[0] == storage_unit_id and option[1] == shelf_number:
            return index
    return 0


def set_notice(message):
    st.session_state["notice"] = message


# ============================================================
# AUTHENTICATION / USERS / AUDIT
# ============================================================

def _secret_text(name):
    """Read a top-level secret from env or Streamlit Secrets."""
    env_value = os.getenv(name, "").strip()
    if env_value:
        return env_value

    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def write_audit_log(
    action,
    target_type=None,
    target_id=None,
    bottle_id=None,
    details=None,
    conn=None,
    user=None,
):
    """Write one user activity record. Reuse an existing transaction when supplied."""
    actor = user or globals().get("CURRENT_USER") or {}
    user_email = str(actor.get("email") or "system").strip().lower()
    user_name = str(actor.get("name") or actor.get("display_name") or user_email).strip()

    owns_connection = conn is None
    if owns_connection:
        conn = get_connection()

    try:
        conn.execute(
            """
            INSERT INTO audit_logs (
                user_email, user_name, action, target_type,
                target_id, bottle_id, details, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_email,
                user_name,
                action,
                target_type,
                target_id,
                bottle_id,
                details,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()


def _get_app_user(email):
    email = str(email or "").strip().lower()
    if not email:
        return None

    conn = get_connection()
    row = conn.execute(
        """
        SELECT id, email, display_name, role, active
        FROM app_users
        WHERE LOWER(email)=LOWER(?)
        """,
        (email,),
    ).fetchone()
    conn.close()
    return row


def _bootstrap_admin_if_allowed(email, identity_name):
    """
    Create the first/declared administrator only when the signed-in email
    exactly matches BOOTSTRAP_ADMIN_EMAIL from Streamlit Secrets.
    """
    bootstrap_email = _secret_text("BOOTSTRAP_ADMIN_EMAIL").lower()
    email = str(email or "").strip().lower()

    if not bootstrap_email or email != bootstrap_email:
        return None

    existing = _get_app_user(email)
    if existing:
        return existing

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO app_users (
            email, display_name, role, active,
            created_at, updated_at, created_by
        )
        VALUES (?, ?, 'admin', TRUE, ?, ?, ?)
        ON CONFLICT(email) DO NOTHING
        """,
        (
            email,
            identity_name or email,
            now,
            now,
            "BOOTSTRAP_ADMIN_EMAIL",
        ),
    )
    conn.commit()
    conn.close()
    return _get_app_user(email)


def _login_screen():
    st.markdown(
        """
<div style="max-width:620px; margin:7rem auto 0 auto; background:#FFFFFF;
padding:36px; border:1px solid #E5E7EB; border-radius:18px; text-align:center;">
<div style="font-size:32px; font-weight:700; color:#172033;">🧪 ICL Chemical Manager</div>
<div style="margin-top:8px; color:#64748B;">
Laboratory Chemical Inventory Management System
</div>
</div>
        """,
        unsafe_allow_html=True,
    )
    st.write("")
    left, middle, right = st.columns([1, 1.3, 1])
    with middle:
        st.button(
            "Sign in with Google",
            type="primary",
            width="stretch",
            on_click=st.login,
        )


def require_authenticated_user():
    """Require Google OIDC login and an active app_users whitelist entry."""
    try:
        logged_in = bool(st.user.is_logged_in)
    except Exception:
        st.error(
            "Authentication is not configured yet. Add the [auth] Google OIDC "
            "settings to this app's Streamlit Secrets."
        )
        st.stop()

    if not logged_in:
        _login_screen()
        st.stop()

    identity = st.user.to_dict()
    email = str(identity.get("email") or "").strip().lower()
    identity_name = str(
        identity.get("name")
        or identity.get("given_name")
        or email
    ).strip()

    if not email:
        st.error("Your Google account did not provide an email address.")
        if st.button("Log out"):
            st.logout()
        st.stop()

    app_user = _get_app_user(email)
    if app_user is None:
        app_user = _bootstrap_admin_if_allowed(email, identity_name)

    if app_user is None or not bool(app_user["active"]):
        st.error("⛔ This Google account is not authorized to use ICL Chemical Manager.")
        st.caption(f"Signed in as: {email}")
        if not _secret_text("BOOTSTRAP_ADMIN_EMAIL"):
            st.info(
                "Initial setup: add BOOTSTRAP_ADMIN_EMAIL to Streamlit Secrets "
                "using the Google email that should become the first administrator."
            )
        st.button("Log out", on_click=st.logout)
        st.stop()

    current_user = {
        "id": app_user["id"],
        "email": str(app_user["email"]).strip().lower(),
        "name": str(app_user["display_name"] or identity_name or email).strip(),
        "role": str(app_user["role"] or "member").strip().lower(),
    }

    if not st.session_state.get("_login_audit_written"):
        write_audit_log(
            "LOGIN",
            target_type="session",
            details="Google OIDC login",
            user=current_user,
        )
        st.session_state["_login_audit_written"] = True

    return current_user


CURRENT_USER = require_authenticated_user()


# ============================================================
# SAFETY / PUBCHEM HELPERS
# ============================================================

PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov"
PUBCHEM_USER_AGENT = "ICL-Chemical-Manager/1.0"


def http_get_json(url, timeout=15):
    """Small JSON HTTP helper using only the Python standard library."""
    request = Request(
        url,
        headers={
            "User-Agent": PUBCHEM_USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None


def iter_pubchem_information(node):
    """Yield all PubChem PUG-View Information objects recursively."""
    if isinstance(node, dict):
        information = node.get("Information")
        if isinstance(information, list):
            for item in information:
                if isinstance(item, dict):
                    yield item

        for value in node.values():
            yield from iter_pubchem_information(value)

    elif isinstance(node, list):
        for item in node:
            yield from iter_pubchem_information(item)


def pubchem_string_values(info):
    values = []
    value = info.get("Value") or {}

    for item in value.get("StringWithMarkup", []) or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("String") or "").strip()
        if text:
            values.append(text)

    return values


def pubchem_pictogram_names(info):
    names = []

    for item in (info.get("Value") or {}).get("StringWithMarkup", []) or []:
        if not isinstance(item, dict):
            continue

        text = str(item.get("String") or "").strip()
        if text and text.lower() not in {"pictogram", "pictograms"}:
            names.append(text)

        for markup in item.get("Markup", []) or []:
            if not isinstance(markup, dict):
                continue
            extra = str(markup.get("Extra") or "").strip()
            if extra:
                names.append(extra)

    # Keep order while removing duplicates.
    return list(dict.fromkeys(names))


def find_pubchem_cid(cas_number, chemical_name):
    terms = []

    cas_number = clean_text(cas_number)
    chemical_name = clean_text(chemical_name)

    if cas_number:
        terms.append(cas_number)
    if chemical_name and chemical_name not in terms:
        terms.append(chemical_name)

    for term in terms:
        url = (
            f"{PUBCHEM_BASE}/rest/pug/compound/name/"
            f"{quote(term, safe='')}/cids/JSON"
        )
        data = http_get_json(url)
        if not data:
            continue

        cids = (data.get("IdentifierList") or {}).get("CID") or []
        if cids:
            return str(cids[0]), term

    return None, None


def fetch_pubchem_safety(cas_number, chemical_name):
    """
    Retrieve GHS screening information from PubChem.

    Important: this is NOT treated as the manufacturer's official SDS.
    """
    cid, matched_term = find_pubchem_cid(cas_number, chemical_name)

    if not cid:
        return None

    url = (
        f"{PUBCHEM_BASE}/rest/pug_view/data/compound/{cid}/JSON/"
        f"?heading={quote('GHS Classification', safe='')}"
    )
    data = http_get_json(url)

    if not data:
        return None

    signal_words = []
    hazard_statements = []
    pictograms = []

    for info in iter_pubchem_information(data):
        name = str(info.get("Name") or "").strip().lower()

        if name in {"signal", "signal word"}:
            signal_words.extend(pubchem_string_values(info))

        elif "ghs hazard statements" in name or name == "hazard statements":
            hazard_statements.extend(pubchem_string_values(info))

        elif "pictogram" in name:
            pictograms.extend(pubchem_pictogram_names(info))

    signal_words = list(dict.fromkeys(x for x in signal_words if x))
    hazard_statements = list(dict.fromkeys(x for x in hazard_statements if x))
    pictograms = list(dict.fromkeys(x for x in pictograms if x))

    h_codes = []
    for statement in hazard_statements:
        for code in re.findall(r"\b(?:H\d{3}[A-Z]?|EUH\d{3}[A-Z]?)\b", statement):
            if code not in h_codes:
                h_codes.append(code)

    # If multiple contributors exist, Danger takes precedence for the display.
    normalized_signals = [x.strip().title() for x in signal_words]
    if any(x.lower() == "danger" for x in normalized_signals):
        signal_word = "Danger"
    elif any(x.lower() == "warning" for x in normalized_signals):
        signal_word = "Warning"
    else:
        signal_word = normalized_signals[0] if normalized_signals else ""

    return {
        "cid": cid,
        "match_term": matched_term or "",
        "signal_word": signal_word,
        "h_codes": ", ".join(h_codes),
        "hazard_statements": "\n".join(hazard_statements),
        "pictograms": ", ".join(pictograms),
        "source_url": f"{PUBCHEM_BASE}/compound/{cid}",
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def save_pubchem_safety(chemical_id, safety):
    conn = get_connection()
    conn.execute(
        """
        UPDATE chemicals
        SET pubchem_cid=?,
            safety_signal_word=?,
            safety_h_codes=?,
            safety_hazard_statements=?,
            safety_pictograms=?,
            safety_source_url=?,
            safety_checked_at=?,
            safety_match_term=?,
            updated_at=?
        WHERE id=?
        """,
        (
            safety.get("cid", ""),
            safety.get("signal_word", ""),
            safety.get("h_codes", ""),
            safety.get("hazard_statements", ""),
            safety.get("pictograms", ""),
            safety.get("source_url", ""),
            safety.get("checked_at", ""),
            safety.get("match_term", ""),
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            chemical_id,
        ),
    )
    write_audit_log(
        "SAFETY_SCREENING",
        target_type="chemical",
        target_id=chemical_id,
        details=f"PubChem CID: {safety.get('cid', '')}",
        conn=conn,
    )
    conn.commit()
    conn.close()


def safety_badge(signal_word, hazard_statements, checked_at):
    signal = clean_text(signal_word).lower()

    if signal == "danger":
        return "🔴 Danger"
    if signal == "warning":
        return "🟠 Warning"
    if clean_text(hazard_statements):
        return "🟡 Hazard info"
    if clean_text(checked_at):
        return "⚪ Check SDS"
    return "⚪ Not checked"


def render_safety_panel(
    signal_word="",
    h_codes="",
    hazard_statements="",
    pictograms="",
    source_url="",
    checked_at="",
    source_label="PubChem safety screening",
):
    """Render safety screening without presenting it as an official SDS."""
    signal = clean_text(signal_word)
    h_codes = clean_text(h_codes)
    pictograms = clean_text(pictograms)
    source_url = clean_text(source_url)
    checked_at = clean_text(checked_at)
    statements = [
        line.strip()
        for line in str(hazard_statements or "").splitlines()
        if line.strip()
    ]

    if not checked_at:
        st.info("Safety screening has not been checked yet. Verify the official SDS before use.")
        return

    if signal.lower() == "danger":
        st.error("⚠️ DANGER — hazardous chemical. Review the official SDS before handling.")
    elif signal.lower() == "warning":
        st.warning("⚠️ WARNING — hazardous chemical. Review the official SDS before handling.")
    elif statements or pictograms:
        st.warning("⚠️ Hazard information is available. Review the official SDS before handling.")
    else:
        st.info(
            "No GHS hazard statement was returned by this PubChem screening. "
            "This does NOT mean the product is safe; verify the official SDS."
        )

    info1, info2, info3 = st.columns(3)
    info1.markdown(f"**Signal Word**\n\n{signal or '-'}")
    info2.markdown(f"**H-Codes**\n\n{h_codes or '-'}")
    info3.markdown(f"**Pictograms / Hazards**\n\n{pictograms or '-'}")

    if statements:
        with st.expander("Hazard Statements", expanded=signal.lower() == "danger"):
            for statement in statements[:20]:
                st.write(f"• {statement}")
            if len(statements) > 20:
                st.caption(f"Showing 20 of {len(statements)} statements.")

    footer = f"Source: {source_label}"
    if checked_at:
        footer += f"  |  Checked: {checked_at}"
    st.caption(footer)

    if source_url:
        st.link_button("Open PubChem Safety Source", source_url)


# ============================================================
# DIALOGS
# ============================================================

@st.dialog("🗑️ Dispose Chemical", width="medium")
def dispose_chemical_dialog(chemical_id):
    conn = get_connection()
    chemical = conn.execute(
        f"""
        SELECT
            c.id,
            c.bottle_id,
            c.legacy_id,
            c.chemical_name,
            c.cas_number,
            c.manufacturer,
            COALESCE(c.remaining_amount, c.amount) AS remaining_amount,
            c.unit,
            {LOCATION_EXPR} AS location_text
        FROM chemicals c
        LEFT JOIN storage_units su ON c.storage_unit_id = su.id
        WHERE c.id = ?
        """,
        (chemical_id,),
    ).fetchone()
    conn.close()

    if not chemical:
        st.error("Chemical not found.")
        return

    st.markdown(f"### {chemical['chemical_name']}")

    id_parts = []
    if chemical["bottle_id"]:
        id_parts.append(f"Bottle ID: {chemical['bottle_id']}")
    if chemical["legacy_id"]:
        id_parts.append(f"Legacy ID: {chemical['legacy_id']}")
    if id_parts:
        st.caption("   |   ".join(id_parts))

    info_col1, info_col2 = st.columns(2)

    with info_col1:
        st.markdown("**Manufacturer**")
        st.write(chemical["manufacturer"] or "-")

        st.markdown("**CAS No.**")
        st.write(chemical["cas_number"] or "-")

    with info_col2:
        st.markdown("**Location**")
        st.write(chemical["location_text"] or "Not Assigned")

        st.markdown("**Remaining**")
        if chemical["remaining_amount"] is not None:
            st.write(f"{chemical['remaining_amount']:g} {chemical['unit'] or ''}")
        else:
            st.write("-")

    st.divider()

    disposal_reason = st.selectbox(
        "Reason for Disposal *",
        [
            "Empty / Fully Used",
            "Expired",
            "Damaged Container",
            "Contaminated",
            "Quality Concern",
            "No Longer Needed",
            "Duplicate / Excess Stock",
            "Other",
        ],
        key=f"dialog_disposal_reason_{chemical_id}",
    )

    disposal_note = st.text_area(
        "Additional Note",
        placeholder="Optional details about the disposal",
        key=f"dialog_disposal_note_{chemical_id}",
    )

    confirm = st.checkbox(
        "I confirm that this chemical has been disposed.",
        key=f"dialog_disposal_confirm_{chemical_id}",
    )

    cancel_col, confirm_col = st.columns(2)

    with cancel_col:
        if st.button(
            "Cancel",
            key=f"dialog_cancel_disposal_{chemical_id}",
            width="stretch",
        ):
            st.rerun()

    with confirm_col:
        if st.button(
            "Confirm Disposal",
            type="primary",
            key=f"dialog_confirm_disposal_{chemical_id}",
            width="stretch",
        ):
            if not confirm:
                st.error("Please check the confirmation box.")
            else:
                now = datetime.now().strftime("%Y-%m-%d %H:%M")

                conn = get_connection()
                conn.execute(
                    """
                    UPDATE chemicals
                    SET status='Disposed',
                        disposed_at=?,
                        disposal_reason=?,
                        disposal_note=?,
                        disposal_location=?,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        now,
                        disposal_reason,
                        disposal_note.strip(),
                        chemical["location_text"],
                        now,
                        chemical_id,
                    ),
                )
                write_audit_log(
                    "DISPOSE_CHEMICAL",
                    target_type="chemical",
                    target_id=chemical_id,
                    bottle_id=chemical["bottle_id"],
                    details=json.dumps(
                        {
                            "chemical_name": chemical["chemical_name"],
                            "reason": disposal_reason,
                            "note": disposal_note.strip(),
                            "location": chemical["location_text"],
                        },
                        ensure_ascii=False,
                    ),
                    conn=conn,
                )
                conn.commit()
                conn.close()

                set_notice(
                    f"✅ {chemical['chemical_name']} has been disposed."
                )
                st.rerun()


# ============================================================
# HEADER / SIDEBAR
# ============================================================

st.markdown(
    """
<div style="background:#FFFFFF; padding:24px 28px; border-radius:16px; border:1px solid #E5E7EB; margin-bottom:28px;">
<div style="font-size:30px; font-weight:700; color:#172033;">🧪 ICL Chemical Manager</div>
<div style="margin-top:6px; font-size:14px; color:#64748B;">Laboratory Chemical Inventory Management System</div>
</div>
    """,
    unsafe_allow_html=True
)

st.sidebar.markdown("""
### 🧪 ICL
**Chemical Manager**
""")

st.sidebar.divider()

st.sidebar.caption("Signed in as")
st.sidebar.markdown(f"**{CURRENT_USER['name']}**")
st.sidebar.caption(CURRENT_USER["email"])
st.sidebar.caption(f"Role: {CURRENT_USER['role'].title()}")
st.sidebar.button("Log out", on_click=st.logout, width="stretch")

st.sidebar.divider()

menu_items = [
    "Chemical Search",
    "Inventory Management",
    "Storage Management",
    "Excel Import",
    "Excel Export",
    "Add Chemical",
    "SDS",
]

if CURRENT_USER["role"] == "admin":
    menu_items.extend(
        [
            "User Management",
            "Activity Log",
        ]
    )

menu_items.append("Settings")

menu = st.sidebar.radio(
    "Navigation",
    menu_items,
    label_visibility="collapsed"
)

if "notice" in st.session_state:
    st.success(st.session_state.pop("notice"))


# ============================================================
# CHEMICAL SEARCH
# ============================================================

if menu == "Chemical Search":

    st.header("🔍 Chemical Search")

    st.caption(
        "Search and browse chemicals registered in the laboratory."
    )

    # ========================================================
    # FILTER OPTIONS
    # ========================================================

    conn = get_connection()

    storage_rows = conn.execute(
        """
        SELECT storage_name
        FROM storage_units
        ORDER BY storage_name
        """
    ).fetchall()

    manufacturer_rows = conn.execute(
        """
        SELECT DISTINCT manufacturer
        FROM chemicals
        WHERE manufacturer IS NOT NULL
          AND TRIM(manufacturer) != ''
        ORDER BY manufacturer
        """
    ).fetchall()

    conn.close()

    storage_options = [
        "All Storage"
    ] + [
        row["storage_name"]
        for row in storage_rows
    ]

    manufacturer_options = [
        "All Manufacturers"
    ] + [
        row["manufacturer"]
        for row in manufacturer_rows
    ]

    status_options = [
        "Active Only",
        "Unopened",
        "In Use",
        "Disposal Pending",
        "Disposed",
        "All"
    ]

    # ========================================================
    # SEARCH BAR
    # ========================================================

    search = st.text_input(
        "Search",
        placeholder=(
            "Chemical name, CAS No., catalog number, "
            "manufacturer, Bottle ID..."
        ),
        label_visibility="collapsed"
    )

    # ========================================================
    # FILTERS
    # ========================================================

    filter_col1, filter_col2, filter_col3 = st.columns(
        [1.4, 1.4, 1]
    )

    with filter_col1:

        selected_storage = st.selectbox(
            "Storage",
            storage_options
        )

    with filter_col2:

        selected_manufacturer = st.selectbox(
            "Manufacturer",
            manufacturer_options
        )

    with filter_col3:

        selected_status = st.selectbox(
            "Status",
            status_options
        )

    st.write("")

    # ========================================================
    # BUILD QUERY
    # ========================================================

    conditions = []
    params = []

    # --------------------------------------------------------
    # Search text
    # --------------------------------------------------------

    if search.strip():

        like = f"%{search.strip()}%"

        conditions.append(
            """
            (
                   c.bottle_id ILIKE ?
                OR c.legacy_id ILIKE ?
                OR c.chemical_name ILIKE ?
                OR c.description ILIKE ?
                OR c.cas_number ILIKE ?
                OR c.manufacturer ILIKE ?
                OR c.catalog_number ILIKE ?
                OR c.lot_number ILIKE ?
                OR c.owner ILIKE ?
                OR su.storage_name ILIKE ?
            )
            """
        )

        params.extend(
            [like] * 10
        )

    # --------------------------------------------------------
    # Storage filter
    # --------------------------------------------------------

    if selected_storage != "All Storage":

        conditions.append(
            "su.storage_name = ?"
        )

        params.append(
            selected_storage
        )

    # --------------------------------------------------------
    # Manufacturer filter
    # --------------------------------------------------------

    if (
        selected_manufacturer
        != "All Manufacturers"
    ):

        conditions.append(
            "c.manufacturer = ?"
        )

        params.append(
            selected_manufacturer
        )

    # --------------------------------------------------------
    # Status filter
    # --------------------------------------------------------

    if selected_status == "Active Only":

        conditions.append(
            "c.status != 'Disposed'"
        )

    elif selected_status != "All":

        conditions.append(
            "c.status = ?"
        )

        params.append(
            selected_status
        )

    # --------------------------------------------------------
    # WHERE statement
    # --------------------------------------------------------

    if conditions:

        where_sql = (
            "WHERE "
            + " AND ".join(conditions)
        )

    else:

        where_sql = ""

    # ========================================================
    # COUNT RESULTS
    # ========================================================

    conn = get_connection()

    total_count = conn.execute(
        f"""
        SELECT COUNT(*)

        FROM chemicals c

        LEFT JOIN storage_units su
            ON c.storage_unit_id = su.id

        {where_sql}
        """,
        params
    ).fetchone()[0]

    # ========================================================
    # GET RESULTS
    # ========================================================

    rows = conn.execute(
        f"""
        SELECT
            c.id,

            c.bottle_id
                AS "Bottle ID",

            c.chemical_name
                AS "Chemical Name",

            c.cas_number
                AS "CAS No.",

            c.manufacturer
                AS "Manufacturer",

            COALESCE(
                c.remaining_amount,
                c.amount
            )
                AS "Remaining",

            c.unit
                AS "Unit",

            {LOCATION_EXPR}
                AS "Storage",

            c.status
                AS "Status"

        FROM chemicals c

        LEFT JOIN storage_units su
            ON c.storage_unit_id = su.id

        {where_sql}

        ORDER BY
            c.chemical_name,
            c.id

        LIMIT 300
        """,
        params
    ).fetchall()

    conn.close()

    # ========================================================
    # RESULT COUNT
    # ========================================================

    count_col1, count_col2 = st.columns(
        [3, 1]
    )

    with count_col1:

        st.markdown(
            f"### {total_count:,} chemicals"
        )

    with count_col2:

        if total_count > 300:

            st.caption(
                "Showing first 300 results"
            )

    # ========================================================
    # SEARCH RESULTS
    # ========================================================

    if rows:

        display_data = []

        chemical_ids = []

        for row in rows:

            chemical_ids.append(
                row["id"]
            )

            remaining = row["Remaining"]

            if remaining is None:
                remaining_text = "-"

            else:

                try:
                    remaining_number = float(
                        remaining
                    )

                    if remaining_number.is_integer():
                        remaining_number = int(
                            remaining_number
                        )

                    remaining_text = (
                        f"{remaining_number} "
                        f"{row['Unit'] or ''}"
                    )

                except Exception:

                    remaining_text = (
                        f"{remaining} "
                        f"{row['Unit'] or ''}"
                    )

            display_data.append(
                {
                    "Chemical Name":
                        row["Chemical Name"],

                    "CAS No.":
                        row["CAS No."],

                    "Manufacturer":
                        row["Manufacturer"],

                    "Remaining":
                        remaining_text,

                    "Storage":
                        row["Storage"],

                    "Status":
                        row["Status"]
                }
            )

        # ----------------------------------------------------
        # Clickable dataframe
        # ----------------------------------------------------

        selection = st.dataframe(
            display_data,
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="chemical_search_table"
        )

        # ====================================================
        # SELECTED CHEMICAL DETAILS
        # ====================================================

        if selection.selection.rows:

            selected_row_index = (
                selection.selection.rows[0]
            )

            selected_id = chemical_ids[
                selected_row_index
            ]

            conn = get_connection()

            detail = conn.execute(
                f"""
                SELECT
                    c.*,
                    {LOCATION_EXPR}
                        AS location_text

                FROM chemicals c

                LEFT JOIN storage_units su
                    ON c.storage_unit_id = su.id

                WHERE c.id = ?
                """,
                (
                    selected_id,
                )
            ).fetchone()

            conn.close()

            if detail:

                st.write("")
                st.divider()

                # ============================================
                # DETAIL HEADER
                # ============================================

                st.subheader(
                    f"🧪 {detail['chemical_name']}"
                )

                detail_col1, detail_col2 = (
                    st.columns([3, 1])
                )

                with detail_col1:

                    if detail["description"]:

                        st.caption(
                            detail["description"]
                        )

                with detail_col2:

                    st.markdown(
                        f"**{detail['status']}**"
                    )

                st.write("")

                # ============================================
                # BASIC INFORMATION
                # ============================================

                st.markdown(
                    "#### General Information"
                )

                col1, col2, col3 = st.columns(3)

                with col1:

                    st.markdown(
                        "**Bottle ID**"
                    )

                    st.write(
                        detail["bottle_id"]
                        or "-"
                    )

                    st.markdown(
                        "**Legacy ID**"
                    )

                    st.write(
                        detail["legacy_id"]
                        or "-"
                    )

                    st.markdown(
                        "**CAS No.**"
                    )

                    st.write(
                        detail["cas_number"]
                        or "-"
                    )

                with col2:

                    st.markdown(
                        "**Manufacturer**"
                    )

                    st.write(
                        detail["manufacturer"]
                        or "-"
                    )

                    st.markdown(
                        "**Catalog No.**"
                    )

                    st.write(
                        detail["catalog_number"]
                        or "-"
                    )

                    st.markdown(
                        "**Lot No.**"
                    )

                    st.write(
                        detail["lot_number"]
                        or "-"
                    )

                with col3:

                    st.markdown(
                        "**Owner**"
                    )

                    st.write(
                        detail["owner"]
                        or "-"
                    )

                    st.markdown(
                        "**Storage**"
                    )

                    st.write(
                        detail["location_text"]
                        or "-"
                    )

                    st.markdown(
                        "**Storage Temperature**"
                    )

                    st.write(
                        detail[
                            "storage_temperature"
                        ]
                        or "-"
                    )

                st.divider()

                # ============================================
                # INVENTORY INFORMATION
                # ============================================

                st.markdown(
                    "#### Inventory"
                )

                inv1, inv2, inv3, inv4 = (
                    st.columns(4)
                )

                with inv1:

                    st.metric(
                        "Initial Amount",
                        (
                            f"{detail['initial_amount']} "
                            f"{detail['unit'] or ''}"
                            if detail[
                                "initial_amount"
                            ] is not None
                            else "-"
                        )
                    )

                with inv2:

                    st.metric(
                        "Remaining",
                        (
                            f"{detail['remaining_amount']} "
                            f"{detail['unit'] or ''}"
                            if detail[
                                "remaining_amount"
                            ] is not None
                            else "-"
                        )
                    )

                with inv3:

                    st.metric(
                        "Purchase Date",
                        detail["purchase_date"]
                        or "-"
                    )

                with inv4:

                    st.metric(
                        "Expiration Date",
                        detail[
                            "expiration_date"
                        ]
                        or "-"
                    )

                st.divider()

                # ============================================
                # CHEMICAL INFORMATION
                # ============================================

                st.markdown(
                    "#### Chemical Information"
                )

                chem1, chem2, chem3 = (
                    st.columns(3)
                )

                with chem1:

                    st.markdown(
                        "**Molecular Formula**"
                    )

                    st.write(
                        detail[
                            "molecular_formula"
                        ]
                        or "-"
                    )

                    st.markdown(
                        "**Molecular Weight**"
                    )

                    st.write(
                        detail[
                            "molecular_weight"
                        ]
                        or "-"
                    )

                with chem2:

                    st.markdown(
                        "**Purity**"
                    )

                    st.write(
                        detail["purity"]
                        or "-"
                    )

                    st.markdown(
                        "**Concentration / Density**"
                    )

                    st.write(
                        detail[
                            "concentration_density"
                        ]
                        or "-"
                    )

                with chem3:

                    st.markdown(
                        "**Opened Date**"
                    )

                    st.write(
                        detail["opened_date"]
                        or "-"
                    )

                    st.markdown(
                        "**Registered Date**"
                    )

                    st.write(
                        detail[
                            "source_registered_date"
                        ]
                        or detail["created_at"]
                        or "-"
                    )

                st.divider()

                st.markdown("#### Safety")

                render_safety_panel(
                    signal_word=detail["safety_signal_word"],
                    h_codes=detail["safety_h_codes"],
                    hazard_statements=detail["safety_hazard_statements"],
                    pictograms=detail["safety_pictograms"],
                    source_url=detail["safety_source_url"],
                    checked_at=detail["safety_checked_at"],
                )

                if detail["comments"]:

                    st.divider()

                    st.markdown(
                        "#### Notes"
                    )

                    st.write(
                        detail["comments"]
                    )

    else:

        st.info(
            "No chemicals match the selected filters."
        )


# ============================================================
# ADD CHEMICAL
# ============================================================

elif menu == "Add Chemical":
    st.header("➕ Add Chemical")
    st.caption(
        "For individual bottles. After registration, the app automatically performs "
        "a PubChem GHS safety screening using CAS No. first and chemical name second."
    )

    st.info(
        "The automatic safety check is a screening tool, not the manufacturer's official SDS. "
        "Upload or link the official SDS from the SDS menu."
    )

    storage_options = get_storage_options()

    with st.form("add_chemical_form", clear_on_submit=True, enter_to_submit=False):
        chemical_name = st.text_input("Chemical Name *")
        description = st.text_input("Description / Grade")

        col1, col2 = st.columns(2)

        with col1:
            cas_number = st.text_input("CAS No.")
            manufacturer = st.text_input("Manufacturer")
            catalog_number = st.text_input("Catalog No.")
            lot_number = st.text_input("Lot No.")
            initial_amount = st.number_input(
                "Initial Amount", min_value=0.0, value=0.0
            )

        with col2:
            remaining_amount = st.number_input(
                "Remaining Amount", min_value=0.0, value=0.0
            )
            unit = st.selectbox("Unit", ["g", "mg", "kg", "mL", "L", "ea"])
            storage = st.selectbox(
                "Storage Location",
                storage_options,
                format_func=lambda option: option[2],
            )
            owner = st.text_input("Owner / Person in Charge")
            expiration_date = st.text_input(
                "Expiration Date", placeholder="YYYY-MM-DD"
            )

        status = st.selectbox(
            "Status",
            ["Unopened", "In Use", "Disposal Pending"],
        )

        submitted = st.form_submit_button("Register Chemical", type="primary")

    if submitted:
        if not chemical_name.strip():
            st.error("Chemical name is required.")
        else:
            conn = get_connection()
            cursor = conn.execute(
                """
                INSERT INTO chemicals (
                    chemical_name, description, cas_number, manufacturer,
                    catalog_number, lot_number, amount, initial_amount,
                    remaining_amount, unit, storage_unit_id, shelf_number,
                    location, owner, expiration_date, status, created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chemical_name.strip(),
                    description.strip(),
                    cas_number.strip(),
                    manufacturer.strip(),
                    catalog_number.strip(),
                    lot_number.strip(),
                    initial_amount,
                    initial_amount,
                    remaining_amount,
                    unit,
                    storage[0],
                    storage[1],
                    storage[2] if storage[0] is not None else "",
                    owner.strip(),
                    expiration_date.strip(),
                    status,
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                ),
            )
            chemical_id = cursor.lastrowid
            bottle_id = f"ICL-{chemical_id:06d}"
            conn.execute(
                "UPDATE chemicals SET bottle_id=?, registered_by=? WHERE id=?",
                (bottle_id, CURRENT_USER["email"], chemical_id),
            )
            write_audit_log(
                "ADD_CHEMICAL",
                target_type="chemical",
                target_id=chemical_id,
                bottle_id=bottle_id,
                details=json.dumps(
                    {
                        "chemical_name": chemical_name.strip(),
                        "cas_number": cas_number.strip(),
                        "manufacturer": manufacturer.strip(),
                        "location": storage[2] if storage[0] is not None else "Not Assigned",
                    },
                    ensure_ascii=False,
                ),
                conn=conn,
            )
            conn.commit()
            conn.close()

            st.success(f"✅ {chemical_name} registered as {bottle_id}.")

            with st.spinner("Checking PubChem GHS safety information..."):
                safety = fetch_pubchem_safety(cas_number, chemical_name)

            if safety:
                save_pubchem_safety(chemical_id, safety)
                st.markdown("#### Automatic Safety Screening")
                render_safety_panel(
                    signal_word=safety["signal_word"],
                    h_codes=safety["h_codes"],
                    hazard_statements=safety["hazard_statements"],
                    pictograms=safety["pictograms"],
                    source_url=safety["source_url"],
                    checked_at=safety["checked_at"],
                )
            else:
                st.warning(
                    "⚠️ Safety information could not be matched automatically. "
                    "Please verify and upload the official SDS in SDS Management."
                )


# ============================================================
# EXCEL IMPORT - CURRENT LAB MANAGER EXPORT FORMAT
# ============================================================

elif menu == "Excel Import":
    st.header("📥 Excel Import")
    st.caption(
        "Designed for the current Lab Manager export. The first row is a title and the second row contains the column headers."
    )

    st.info(
        "Storage names from the Excel file will be linked to Storage Units. "
        "If a storage name does not exist yet, it will be created automatically. "
        "You can rename the Storage Unit later without editing every chemical."
    )

    uploaded_excel = st.file_uploader(
        "Upload Lab Manager Excel File",
        type=["xlsx"],
    )

    if uploaded_excel is not None:
        try:
            # The current export has title in row 1 and headers in row 2.
            df = pd.read_excel(
                uploaded_excel,
                sheet_name="item list",
                header=1,
                dtype=object,
            )
            df = df.dropna(how="all")
            df.columns = [str(column).strip() for column in df.columns]

            if "물품명" not in df.columns:
                st.error(
                    "The expected '물품명' column was not found. Please check that this is a Lab Manager item-list export."
                )
            else:
                only_reagents = st.checkbox(
                    "Import only rows where 카테고리 이름 = 시약",
                    value=True,
                )

                working_df = df.copy()

                if only_reagents and "카테고리 이름" in working_df.columns:
                    working_df = working_df[
                        working_df["카테고리 이름"]
                        .fillna("")
                        .astype(str)
                        .str.strip()
                        .eq("시약")
                    ]

                working_df = working_df[
                    working_df["물품명"].fillna("").astype(str).str.strip().ne("")
                ].copy()

                st.success(
                    f"✅ File loaded. {len(working_df):,} chemical row(s) selected for review."
                )

                # ------------------------------------------------
                # Prepare existing legacy IDs for duplicate check
                # ------------------------------------------------
                conn = get_connection()
                existing_legacy_ids = {
                    row["legacy_id"]
                    for row in conn.execute(
                        """
                        SELECT legacy_id
                        FROM chemicals
                        WHERE legacy_id IS NOT NULL
                          AND TRIM(legacy_id) != ''
                        """
                    ).fetchall()
                }
                existing_storage_names = {
                    row["storage_name"].lower(): row["storage_name"]
                    for row in conn.execute(
                        "SELECT storage_name FROM storage_units"
                    ).fetchall()
                }
                conn.close()

                prepared_rows = []
                skipped_existing = 0
                skipped_duplicate_in_file = 0
                seen_legacy_ids = set()
                new_storage_names = set()

                for _, source in working_df.iterrows():
                    legacy_id = clean_text(source.get("관리번호"))

                    if legacy_id:
                        if legacy_id in existing_legacy_ids:
                            skipped_existing += 1
                            continue
                        if legacy_id in seen_legacy_ids:
                            skipped_duplicate_in_file += 1
                            continue
                        seen_legacy_ids.add(legacy_id)

                    storage_name = clean_text(source.get("보관함 경로"))
                    if storage_name and storage_name.lower() not in existing_storage_names:
                        new_storage_names.add(storage_name)

                    initial_amount = clean_number(source.get("물품사이즈(용량)"))
                    remaining_amount = clean_number(source.get("잔여 용량"))
                    opened_date = clean_date(source.get("개봉일"))

                    if remaining_amount is None:
                        remaining_amount = initial_amount

                    status = "In Use" if opened_date else "Unopened"

                    prepared_rows.append(
                        {
                            "category_classification": clean_text(
                                source.get("카테고리 분류")
                            ),
                            "category_name": clean_text(source.get("카테고리 이름")),
                            "legacy_id": legacy_id,
                            "company_management_id": clean_text(
                                source.get("사내관리번호")
                            ),
                            "chemical_name": clean_text(source.get("물품명")),
                            "description": clean_text(source.get("서브네임")),
                            "legacy_location_path": clean_text(source.get("위치 경로")),
                            "legacy_storage_name": storage_name,
                            "manufacturer": clean_text(source.get("브랜드")),
                            "registered_by": clean_text(source.get("등록자")),
                            "owner": clean_text(source.get("담당자")),
                            "purchaser": clean_text(source.get("구매자")),
                            "purchase_date": clean_date(source.get("구매일")),
                            "purchase_price": clean_text(source.get("구매 가격")),
                            "source_registered_date": clean_date(source.get("등록일")),
                            "source_last_modified": clean_date(source.get("최근 변경일시")),
                            "comments": clean_text(source.get("코멘트")),
                            "tags": clean_text(source.get("태그")),
                            "catalog_number": clean_text(source.get("제품번호")),
                            "lot_number": clean_text(source.get("Lot No.")),
                            "cas_number": clean_text(source.get("Cas No.")),
                            "initial_amount": initial_amount,
                            "remaining_amount": remaining_amount,
                            "unit": normalize_unit(source.get("물품사이즈(용량) 단위")),
                            "safety_stock": clean_text(source.get("안전재고량")),
                            "concentration_density": clean_text(source.get("농도(밀도)")),
                            "molecular_weight": clean_text(source.get("분자량")),
                            "mol": clean_text(source.get("MOL")),
                            "purity": clean_text(source.get("순도")),
                            "solubility": clean_text(source.get("용해도")),
                            "molecular_formula": clean_text(source.get("분자식")),
                            "storage_temperature": clean_text(source.get("보관 온도")),
                            "flash_point": clean_text(source.get("발화점")),
                            "boiling_point": clean_text(source.get("끓는점")),
                            "melting_point": clean_text(source.get("녹는점")),
                            "opened_date": opened_date,
                            "expiration_date": clean_date(source.get("유효기간")),
                            "status": status,
                        }
                    )

                # ------------------------------------------------
                # Summary
                # ------------------------------------------------
                summary_cols = st.columns(4)
                summary_cols[0].metric("Ready to Import", f"{len(prepared_rows):,}")
                summary_cols[1].metric("Already in DB", f"{skipped_existing:,}")
                summary_cols[2].metric(
                    "Duplicate IDs in File", f"{skipped_duplicate_in_file:,}"
                )
                summary_cols[3].metric("New Storage Units", len(new_storage_names))

                if new_storage_names:
                    with st.expander("Storage Units that will be created automatically"):
                        for name in sorted(new_storage_names):
                            st.write(f"• {name}")

                # ------------------------------------------------
                # Preview
                # ------------------------------------------------
                st.subheader("📋 Import Preview")

                preview = pd.DataFrame(prepared_rows)
                if not preview.empty:
                    preview = preview.rename(
                        columns={
                            "legacy_id": "Legacy ID",
                            "chemical_name": "Chemical Name",
                            "description": "Description / Grade",
                            "cas_number": "CAS No.",
                            "manufacturer": "Manufacturer",
                            "catalog_number": "Catalog No.",
                            "initial_amount": "Initial Amount",
                            "remaining_amount": "Remaining Amount",
                            "unit": "Unit",
                            "legacy_storage_name": "Storage Name",
                            "owner": "Owner",
                            "opened_date": "Opened Date",
                            "expiration_date": "Expiration Date",
                            "status": "Status",
                        }
                    )
                    preview_columns = [
                        "Legacy ID",
                        "Chemical Name",
                        "Description / Grade",
                        "CAS No.",
                        "Manufacturer",
                        "Catalog No.",
                        "Initial Amount",
                        "Remaining Amount",
                        "Unit",
                        "Storage Name",
                        "Owner",
                        "Opened Date",
                        "Expiration Date",
                        "Status",
                    ]
                    st.dataframe(
                        preview[preview_columns].head(200),
                        width="stretch",
                        hide_index=True,
                    )
                    if len(preview) > 200:
                        st.caption(
                            f"Preview shows the first 200 of {len(preview):,} rows. All reviewed rows will be imported."
                        )

                    st.divider()
                    confirm_import = st.checkbox(
                        "I reviewed the preview and want to import these chemicals."
                    )

                    if st.button(
                        "📥 Import Chemicals",
                        type="primary",
                        disabled=not confirm_import,
                    ):
                        conn = get_connection()
                        imported_count = 0

                        try:
                            for item in prepared_rows:
                                storage_unit_id = get_or_create_storage(
                                    conn, item["legacy_storage_name"]
                                )

                                cursor = conn.execute(
                                    """
                                    INSERT INTO chemicals (
                                        category_classification,
                                        category_name,
                                        legacy_id,
                                        company_management_id,
                                        chemical_name,
                                        description,
                                        legacy_location_path,
                                        legacy_storage_name,
                                        manufacturer,
                                        registered_by,
                                        owner,
                                        purchaser,
                                        purchase_date,
                                        purchase_price,
                                        source_registered_date,
                                        source_last_modified,
                                        comments,
                                        tags,
                                        catalog_number,
                                        lot_number,
                                        cas_number,
                                        amount,
                                        initial_amount,
                                        remaining_amount,
                                        unit,
                                        safety_stock,
                                        concentration_density,
                                        molecular_weight,
                                        mol,
                                        purity,
                                        solubility,
                                        molecular_formula,
                                        storage_temperature,
                                        flash_point,
                                        boiling_point,
                                        melting_point,
                                        opened_date,
                                        expiration_date,
                                        storage_unit_id,
                                        shelf_number,
                                        status,
                                        created_at,
                                        updated_at
                                    )
                                    VALUES (
                                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                        ?, ?, ?, ?, ?, ?, ?
                                    )
                                    """,
                                    (
                                        item["category_classification"],
                                        item["category_name"],
                                        item["legacy_id"],
                                        item["company_management_id"],
                                        item["chemical_name"],
                                        item["description"],
                                        item["legacy_location_path"],
                                        item["legacy_storage_name"],
                                        item["manufacturer"],
                                        item["registered_by"],
                                        item["owner"],
                                        item["purchaser"],
                                        item["purchase_date"],
                                        item["purchase_price"],
                                        item["source_registered_date"],
                                        item["source_last_modified"],
                                        item["comments"],
                                        item["tags"],
                                        item["catalog_number"],
                                        item["lot_number"],
                                        item["cas_number"],
                                        item["initial_amount"],
                                        item["initial_amount"],
                                        item["remaining_amount"],
                                        item["unit"],
                                        item["safety_stock"],
                                        item["concentration_density"],
                                        item["molecular_weight"],
                                        item["mol"],
                                        item["purity"],
                                        item["solubility"],
                                        item["molecular_formula"],
                                        item["storage_temperature"],
                                        item["flash_point"],
                                        item["boiling_point"],
                                        item["melting_point"],
                                        item["opened_date"],
                                        item["expiration_date"],
                                        storage_unit_id,
                                        None,
                                        item["status"],
                                        datetime.now().strftime("%Y-%m-%d %H:%M"),
                                        datetime.now().strftime("%Y-%m-%d %H:%M"),
                                    ),
                                )

                                chemical_id = cursor.lastrowid
                                bottle_id = f"ICL-{chemical_id:06d}"
                                conn.execute(
                                    "UPDATE chemicals SET bottle_id=? WHERE id=?",
                                    (bottle_id, chemical_id),
                                )
                                imported_count += 1

                            write_audit_log(
                                "EXCEL_IMPORT",
                                target_type="bulk_import",
                                details=json.dumps(
                                    {
                                        "imported_count": imported_count,
                                        "source": uploaded_excel.name,
                                    },
                                    ensure_ascii=False,
                                ),
                                conn=conn,
                            )
                            conn.commit()
                            set_notice(
                                f"✅ {imported_count:,} chemical(s) imported successfully."
                            )
                            st.rerun()

                        except Exception as exc:
                            conn.rollback()
                            st.error(f"Import failed. No changes were committed.\n\n{exc}")
                        finally:
                            conn.close()
                else:
                    st.info("There are no new rows to import.")

        except ValueError as exc:
            st.error(
                "The 'item list' sheet could not be found. Please upload the original Lab Manager export."
            )
            st.code(str(exc))
        except Exception as exc:
            st.error(f"Unable to read the Excel file: {exc}")


# ============================================================
# STORAGE MANAGEMENT
# ============================================================

elif menu == "Storage Management":

    st.header("🗄️ Storage Management")

    st.caption(
        "Manage storage units, shelves, and chemical locations."
    )

    # ========================================================
    # TABS
    # ========================================================

    tab1, tab2 = st.tabs(
        [
            "🗄️ Storage Overview",
            "➕ Add Storage Unit"
        ]
    )

    # ========================================================
    # TAB 1 - STORAGE OVERVIEW
    # ========================================================

    with tab1:

        # ----------------------------------------------------
        # Load storage units
        # ----------------------------------------------------

        conn = get_connection()

        storage_units = conn.execute(
            """
            SELECT
                su.id,
                su.storage_name,
                su.notes,

                COUNT(
                    DISTINCT ss.id
                ) AS shelf_count,

                COUNT(
                    DISTINCT CASE
                        WHEN c.status != 'Disposed'
                        THEN c.id
                    END
                ) AS chemical_count

            FROM storage_units su

            LEFT JOIN storage_shelves ss
                ON su.id = ss.storage_unit_id

            LEFT JOIN chemicals c
                ON su.id = c.storage_unit_id

            GROUP BY
                su.id,
                su.storage_name,
                su.notes

            ORDER BY
                su.storage_name
            """
        ).fetchall()

        conn.close()

        # ----------------------------------------------------
        # Summary
        # ----------------------------------------------------

        if storage_units:

            total_chemicals = sum(
                storage["chemical_count"]
                for storage in storage_units
            )

            col1, col2 = st.columns(2)

            col1.metric(
                "Storage Units",
                len(storage_units)
            )

            col2.metric(
                "Chemicals in Storage",
                total_chemicals
            )

            st.write("")

            # =================================================
            # STORAGE CARDS
            # =================================================

            # Two cards per row
            for i in range(
                0,
                len(storage_units),
                2
            ):

                card_columns = st.columns(2)

                for j in range(2):

                    index = i + j

                    if index >= len(storage_units):
                        break

                    storage = storage_units[index]

                    with card_columns[j]:

                        with st.container(
                            border=True
                        ):

                            st.subheader(
                                f"🗄️ {storage['storage_name']}"
                            )

                            if storage["notes"]:

                                st.caption(
                                    storage["notes"]
                                )

                            metric1, metric2 = (
                                st.columns(2)
                            )

                            metric1.metric(
                                "Chemicals",
                                storage[
                                    "chemical_count"
                                ]
                            )

                            metric2.metric(
                                "Shelves",
                                storage[
                                    "shelf_count"
                                ]
                            )

                            button1, button2 = (
                                st.columns(2)
                            )

                            # ------------------------------
                            # OPEN BUTTON
                            # ------------------------------

                            if button1.button(
                                "Open",
                                key=(
                                    f"open_storage_"
                                    f"{storage['id']}"
                                ),
                                width="stretch"
                            ):

                                st.session_state[
                                    "selected_storage"
                                ] = storage["id"]

                                st.session_state.pop(
                                    "edit_storage",
                                    None
                                )

                                st.rerun()

                            # ------------------------------
                            # EDIT BUTTON
                            # ------------------------------

                            if button2.button(
                                "Edit",
                                key=(
                                    f"edit_storage_"
                                    f"{storage['id']}"
                                ),
                                width="stretch"
                            ):

                                st.session_state[
                                    "edit_storage"
                                ] = storage["id"]

                                st.session_state.pop(
                                    "selected_storage",
                                    None
                                )

                                st.rerun()

        else:

            st.info(
                "No storage units have been created yet."
            )

        # ====================================================
        # STORAGE DETAIL
        # ====================================================

        if (
            "selected_storage"
            in st.session_state
        ):

            storage_id = st.session_state[
                "selected_storage"
            ]

            conn = get_connection()

            storage = conn.execute(
                """
                SELECT
                    id,
                    storage_name,
                    notes
                FROM storage_units
                WHERE id = ?
                """,
                (storage_id,)
            ).fetchone()

            shelves = conn.execute(
                """
                SELECT
                    shelf_number
                FROM storage_shelves

                WHERE storage_unit_id = ?

                ORDER BY shelf_number
                """,
                (storage_id,)
            ).fetchall()

            conn.close()

            if storage:

                st.divider()

                # --------------------------------------------
                # DETAIL HEADER
                # --------------------------------------------

                header_col1, header_col2 = (
                    st.columns([5, 1])
                )

                with header_col1:

                    st.subheader(
                        f"📍 {storage['storage_name']}"
                    )

                    if storage["notes"]:

                        st.caption(
                            storage["notes"]
                        )

                with header_col2:

                    if st.button(
                        "Close",
                        key="close_storage_detail",
                        width="stretch"
                    ):

                        del st.session_state[
                            "selected_storage"
                        ]

                        st.rerun()

                # ============================================
                # SHELF FILTER
                # ============================================

                shelf_options = [
                    "All Locations",
                    "Unassigned"
                ]

                shelf_options += [
                    f"Shelf {shelf['shelf_number']}"
                    for shelf in shelves
                ]

                selected_shelf = st.selectbox(
                    "Shelf",
                    shelf_options,
                    key=(
                        f"shelf_filter_"
                        f"{storage_id}"
                    )
                )

                # ============================================
                # BUILD CHEMICAL QUERY
                # ============================================

                conditions = [
                    "c.storage_unit_id = ?",
                    "c.status != 'Disposed'"
                ]

                params = [
                    storage_id
                ]

                if selected_shelf == "Unassigned":

                    conditions.append(
                        "c.shelf_number IS NULL"
                    )

                elif selected_shelf.startswith(
                    "Shelf "
                ):

                    shelf_number = int(
                        selected_shelf.replace(
                            "Shelf ",
                            ""
                        )
                    )

                    conditions.append(
                        "c.shelf_number = ?"
                    )

                    params.append(
                        shelf_number
                    )

                where_sql = (
                    " AND ".join(conditions)
                )

                conn = get_connection()

                chemicals = conn.execute(
                    f"""
                    SELECT
                        c.id,

                        c.bottle_id
                            AS "Bottle ID",

                        c.chemical_name
                            AS "Chemical Name",

                        c.cas_number
                            AS "CAS No.",

                        c.manufacturer
                            AS "Manufacturer",

                        COALESCE(
                            c.remaining_amount,
                            c.amount
                        ) AS "Remaining",

                        c.unit
                            AS "Unit",

                        CASE

                            WHEN c.shelf_number
                                 IS NOT NULL

                            THEN
                                'Shelf '
                                || c.shelf_number

                            ELSE
                                'Unassigned'

                        END AS "Shelf",

                        c.status
                            AS "Status"

                    FROM chemicals c

                    WHERE {where_sql}

                    ORDER BY
                        c.shelf_number,
                        c.chemical_name
                    """,
                    params
                ).fetchall()

                conn.close()

                # --------------------------------------------
                # Count
                # --------------------------------------------

                st.markdown(
                    f"### {len(chemicals):,} chemicals"
                )

                # --------------------------------------------
                # Chemical list
                # --------------------------------------------

                if chemicals:

                    display_data = []

                    for chemical in chemicals:

                        remaining = chemical[
                            "Remaining"
                        ]

                        if remaining is None:

                            remaining_text = "-"

                        else:

                            try:

                                remaining_number = float(
                                    remaining
                                )

                                if (
                                    remaining_number
                                    .is_integer()
                                ):

                                    remaining_number = int(
                                        remaining_number
                                    )

                                remaining_text = (
                                    f"{remaining_number} "
                                    f"{chemical['Unit'] or ''}"
                                )

                            except Exception:

                                remaining_text = (
                                    f"{remaining} "
                                    f"{chemical['Unit'] or ''}"
                                )

                        display_data.append(
                            {
                                "Bottle ID":
                                    chemical["Bottle ID"],

                                "Chemical Name":
                                    chemical[
                                        "Chemical Name"
                                    ],

                                "CAS No.":
                                    chemical["CAS No."],

                                "Manufacturer":
                                    chemical[
                                        "Manufacturer"
                                    ],

                                "Remaining":
                                    remaining_text,

                                "Shelf":
                                    chemical["Shelf"],

                                "Status":
                                    chemical["Status"]
                            }
                        )

                    st.dataframe(
                        display_data,
                        width="stretch",
                        hide_index=True
                    )

                else:

                    st.info(
                        "No chemicals found in "
                        "this location."
                    )

                # ============================================
                # SHELF MANAGEMENT
                # ============================================

                st.write("")
                st.markdown(
                    "#### Shelf Management"
                )

                if shelves:

                    shelf_cols = st.columns(
                        min(
                            len(shelves),
                            6
                        )
                    )

                    for index, shelf in enumerate(
                        shelves
                    ):

                        shelf_cols[
                            index % len(shelf_cols)
                        ].button(
                            (
                                f"📦 Shelf "
                                f"{shelf['shelf_number']}"
                            ),
                            disabled=True,
                            key=(
                                f"storage_shelf_"
                                f"{storage_id}_"
                                f"{shelf['shelf_number']}"
                            )
                        )

                else:

                    st.caption(
                        "No shelves have been "
                        "defined for this storage unit."
                    )

                # --------------------------------------------
                # Add Shelf
                # --------------------------------------------

                if st.button(
                    "➕ Add Shelf",
                    key=f"add_shelf_{storage_id}"
                ):

                    conn = get_connection()

                    max_shelf = conn.execute(
                        """
                        SELECT
                            MAX(shelf_number)
                        FROM storage_shelves
                        WHERE storage_unit_id = ?
                        """,
                        (storage_id,)
                    ).fetchone()[0]

                    next_shelf = (
                        (max_shelf or 0) + 1
                    )

                    conn.execute(
                        """
                        INSERT INTO storage_shelves (
                            storage_unit_id,
                            shelf_number
                        )
                        VALUES (?, ?)
                        """,
                        (
                            storage_id,
                            next_shelf
                        )
                    )
                    write_audit_log(
                        "ADD_SHELF",
                        target_type="storage_unit",
                        target_id=storage_id,
                        details=f"Added Shelf {next_shelf} to {storage['storage_name']}",
                        conn=conn,
                    )

                    conn.commit()
                    conn.close()

                    st.rerun()

        # ====================================================
        # EDIT STORAGE
        # ====================================================

        if "edit_storage" in st.session_state:

            storage_id = st.session_state[
                "edit_storage"
            ]

            conn = get_connection()

            storage = conn.execute(
                """
                SELECT
                    id,
                    storage_name,
                    notes
                FROM storage_units
                WHERE id = ?
                """,
                (storage_id,)
            ).fetchone()

            conn.close()

            if storage:

                st.divider()

                st.subheader(
                    f"✏️ Edit Storage Unit"
                )

                st.caption(
                    "Renaming a storage unit will "
                    "automatically update the displayed "
                    "location for all linked chemicals."
                )

                with st.form(
                    f"edit_storage_form_{storage_id}",
                    enter_to_submit=False
                ):

                    new_storage_name = (
                        st.text_input(
                            "Storage Name *",
                            value=storage[
                                "storage_name"
                            ]
                        )
                    )

                    new_notes = st.text_area(
                        "Notes",
                        value=(
                            storage["notes"]
                            or ""
                        )
                    )

                    save_col, cancel_col = (
                        st.columns(2)
                    )

                    with save_col:

                        save_storage = (
                            st.form_submit_button(
                                "Save Changes",
                                type="primary"
                            )
                        )

                    with cancel_col:

                        cancel_storage = (
                            st.form_submit_button(
                                "Cancel"
                            )
                        )

                    # -----------------------------------------
                    # SAVE
                    # -----------------------------------------

                    if save_storage:

                        if not new_storage_name.strip():

                            st.error(
                                "Storage name is required."
                            )

                        else:

                            conn = get_connection()

                            duplicate = conn.execute(
                                """
                                SELECT id
                                FROM storage_units

                                WHERE
                                    LOWER(storage_name)
                                    = LOWER(?)

                                    AND id != ?
                                """,
                                (
                                    new_storage_name.strip(),
                                    storage_id
                                )
                            ).fetchone()

                            if duplicate:

                                st.error(
                                    "Another storage unit "
                                    "already uses this name."
                                )

                            else:

                                conn.execute(
                                    """
                                    UPDATE storage_units

                                    SET
                                        storage_name = ?,
                                        notes = ?

                                    WHERE id = ?
                                    """,
                                    (
                                        new_storage_name.strip(),
                                        new_notes.strip(),
                                        storage_id
                                    )
                                )
                                write_audit_log(
                                    "EDIT_STORAGE",
                                    target_type="storage_unit",
                                    target_id=storage_id,
                                    details=json.dumps(
                                        {
                                            "old_name": storage["storage_name"],
                                            "new_name": new_storage_name.strip(),
                                            "notes": new_notes.strip(),
                                        },
                                        ensure_ascii=False,
                                    ),
                                    conn=conn,
                                )

                                conn.commit()

                                del st.session_state[
                                    "edit_storage"
                                ]

                                st.success(
                                    "✅ Storage unit updated."
                                )

                            conn.close()

                            if not duplicate:
                                st.rerun()

                    # -----------------------------------------
                    # CANCEL
                    # -----------------------------------------

                    if cancel_storage:

                        del st.session_state[
                            "edit_storage"
                        ]

                        st.rerun()


    # ========================================================
    # TAB 2 - ADD STORAGE UNIT
    # ========================================================

    with tab2:

        st.subheader(
            "➕ Add Storage Unit"
        )

        st.caption(
            "Create a new cabinet, refrigerator, "
            "desiccator, glovebox, or other storage location."
        )

        with st.form(
            "add_storage_unit_form",
            clear_on_submit=True,
            enter_to_submit=False
        ):

            storage_name = st.text_input(
                "Storage Name *",
                placeholder=(
                    "e.g. Desiccator, Refrigerator 1, "
                    "Chemical Cabinet 1"
                )
            )

            shelf_count = st.number_input(
                "Number of Shelves",
                min_value=0,
                max_value=30,
                value=0,
                step=1,
                help=(
                    "Enter 0 if the storage unit "
                    "does not use shelves."
                )
            )

            notes = st.text_area(
                "Notes",
                placeholder=(
                    "Optional description or "
                    "storage information"
                )
            )

            create_storage = (
                st.form_submit_button(
                    "Create Storage Unit",
                    type="primary"
                )
            )

            if create_storage:

                if not storage_name.strip():

                    st.error(
                        "Storage name is required."
                    )

                else:

                    conn = get_connection()

                    duplicate = conn.execute(
                        """
                        SELECT id
                        FROM storage_units

                        WHERE
                            LOWER(storage_name)
                            = LOWER(?)
                        """,
                        (
                            storage_name.strip(),
                        )
                    ).fetchone()

                    if duplicate:

                        st.error(
                            "A storage unit with "
                            "this name already exists."
                        )

                    else:

                        cursor = conn.execute(
                            """
                            INSERT INTO storage_units (
                                storage_name,
                                notes,
                                created_at
                            )

                            VALUES (?, ?, ?)
                            """,
                            (
                                storage_name.strip(),
                                notes.strip(),
                                datetime.now().strftime(
                                    "%Y-%m-%d %H:%M"
                                )
                            )
                        )

                        new_storage_id = (
                            cursor.lastrowid
                        )

                        # Create shelves
                        for shelf in range(
                            1,
                            int(shelf_count) + 1
                        ):

                            conn.execute(
                                """
                                INSERT INTO storage_shelves (
                                    storage_unit_id,
                                    shelf_number
                                )

                                VALUES (?, ?)
                                """,
                                (
                                    new_storage_id,
                                    shelf
                                )
                            )

                        write_audit_log(
                            "ADD_STORAGE",
                            target_type="storage_unit",
                            target_id=new_storage_id,
                            details=json.dumps(
                                {
                                    "storage_name": storage_name.strip(),
                                    "shelf_count": int(shelf_count),
                                    "notes": notes.strip(),
                                },
                                ensure_ascii=False,
                            ),
                            conn=conn,
                        )
                        conn.commit()
                        conn.close()

                        st.success(
                            f"✅ {storage_name} created."
                        )

                        st.rerun()


# ============================================================
# EXCEL EXPORT
# ============================================================

elif menu == "Excel Export":
    st.header("📤 Excel Export")
    st.caption("Export the current chemical inventory as an Excel workbook.")

    scope = st.selectbox(
        "Export Scope",
        ["Active Inventory", "All Chemicals", "Disposed Chemicals"],
    )

    include_details = st.checkbox(
        "Include detailed chemical information",
        value=True,
        help=(
            "Includes formula, molecular weight, purity, storage temperature, "
            "purchase/opened/expiration dates, comments, and disposal information."
        ),
    )

    if scope == "Active Inventory":
        status_clause = "WHERE c.status != 'Disposed'"
        sheet_name = "Active Inventory"
        file_label = "Active_Inventory"
    elif scope == "Disposed Chemicals":
        status_clause = "WHERE c.status = 'Disposed'"
        sheet_name = "Disposed Chemicals"
        file_label = "Disposed_Chemicals"
    else:
        status_clause = ""
        sheet_name = "All Chemicals"
        file_label = "All_Chemicals"

    basic_columns = f"""
        SELECT
            c.bottle_id AS "Bottle ID",
            c.legacy_id AS "Legacy ID",
            c.chemical_name AS "Chemical Name",
            c.description AS "Description / Grade",
            c.cas_number AS "CAS No.",
            c.manufacturer AS "Manufacturer",
            c.catalog_number AS "Catalog No.",
            c.lot_number AS "Lot No.",
            c.initial_amount AS "Initial Amount",
            COALESCE(c.remaining_amount, c.amount) AS "Remaining Amount",
            c.unit AS "Unit",
            {LOCATION_EXPR} AS "Storage Location",
            c.owner AS "Owner",
            c.status AS "Status"
    """

    detailed_columns = f"""
        SELECT
            c.bottle_id AS "Bottle ID",
            c.legacy_id AS "Legacy ID",
            c.company_management_id AS "Company Management ID",
            c.category_classification AS "Category Classification",
            c.category_name AS "Category Name",
            c.chemical_name AS "Chemical Name",
            c.description AS "Description / Grade",
            c.cas_number AS "CAS No.",
            c.manufacturer AS "Manufacturer",
            c.catalog_number AS "Catalog No.",
            c.lot_number AS "Lot No.",
            c.initial_amount AS "Initial Amount",
            COALESCE(c.remaining_amount, c.amount) AS "Remaining Amount",
            c.unit AS "Unit",
            {LOCATION_EXPR} AS "Storage Location",
            c.shelf_number AS "Shelf Number",
            c.owner AS "Owner",
            c.registered_by AS "Registered By",
            c.purchaser AS "Purchaser",
            c.purchase_date AS "Purchase Date",
            c.purchase_price AS "Purchase Price",
            c.opened_date AS "Opened Date",
            c.expiration_date AS "Expiration Date",
            c.concentration_density AS "Concentration / Density",
            c.molecular_weight AS "Molecular Weight",
            c.mol AS "MOL",
            c.purity AS "Purity",
            c.solubility AS "Solubility",
            c.molecular_formula AS "Molecular Formula",
            c.storage_temperature AS "Storage Temperature",
            c.flash_point AS "Flash Point",
            c.boiling_point AS "Boiling Point",
            c.melting_point AS "Melting Point",
            c.comments AS "Comments",
            c.tags AS "Tags",
            c.status AS "Status",
            c.created_at AS "Created At",
            c.updated_at AS "Updated At",
            c.disposed_at AS "Disposed At",
            c.disposal_reason AS "Disposal Reason",
            c.disposal_note AS "Disposal Note",
            c.disposal_location AS "Disposal Location"
    """

    select_sql = detailed_columns if include_details else basic_columns

    conn = get_connection()
    export_df = pd.read_sql_query(
        f"""
        {select_sql}
        FROM chemicals c
        LEFT JOIN storage_units su ON c.storage_unit_id = su.id
        {status_clause}
        ORDER BY c.chemical_name, c.id
        """,
        conn,
    )
    conn.close()

    summary_col1, summary_col2 = st.columns(2)
    summary_col1.metric("Chemicals to Export", f"{len(export_df):,}")
    summary_col2.metric("Columns", len(export_df.columns))

    st.subheader("Preview")

    if export_df.empty:
        st.info("No chemicals are available for the selected export scope.")
    else:
        st.dataframe(export_df.head(100), width="stretch", hide_index=True)

        if len(export_df) > 100:
            st.caption(
                f"Preview shows the first 100 of {len(export_df):,} rows. "
                "The Excel file will include all rows."
            )

        output = BytesIO()

        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            export_df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
            worksheet = writer.book[sheet_name[:31]]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            header_fill = PatternFill(fill_type="solid", fgColor="25324B")
            header_font = Font(bold=True, color="FFFFFF")

            for cell in worksheet[1]:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center")

            for column_cells in worksheet.columns:
                max_length = 0
                column_letter = column_cells[0].column_letter

                for cell in column_cells[:300]:
                    value = "" if cell.value is None else str(cell.value)
                    max_length = max(max_length, len(value))

                worksheet.column_dimensions[column_letter].width = min(
                    max(max_length + 2, 10), 35
                )

        output.seek(0)
        export_date = datetime.now().strftime("%Y-%m-%d")
        filename = f"ICL_{file_label}_{export_date}.xlsx"

        st.download_button(
            "📥 Download Excel File",
            data=output.getvalue(),
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            width="stretch",
        )

        st.caption(
            "The export is generated from the current database at the moment you click Download."
        )


# ============================================================
# INVENTORY MANAGEMENT
# ============================================================

elif menu == "Inventory Management":
    st.header("📦 Inventory Management")

    tab1, tab2 = st.tabs(["📦 Active Inventory", "📋 Disposal History"])

    # --------------------------------------------------------
    # Active inventory
    # --------------------------------------------------------
    with tab1:
        inventory_search = st.text_input(
            "Search Active Inventory",
            placeholder="Bottle ID, chemical, CAS No., manufacturer, owner, location...",
        )

        search_clause = ""
        params = []
        if inventory_search:
            like = f"%{inventory_search}%"
            search_clause = """
            AND (
                   c.bottle_id ILIKE ?
                OR c.legacy_id ILIKE ?
                OR c.chemical_name ILIKE ?
                OR c.cas_number ILIKE ?
                OR c.manufacturer ILIKE ?
                OR c.owner ILIKE ?
                OR su.storage_name ILIKE ?
            )
            """
            params = [like] * 7

        conn = get_connection()
        total_rows = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM chemicals c
            LEFT JOIN storage_units su ON c.storage_unit_id = su.id
            WHERE c.status != 'Disposed'
            {search_clause}
            """,
            params,
        ).fetchone()[0]

        page_size = 30
        max_page = max(1, (total_rows + page_size - 1) // page_size)
        page = st.selectbox("Page", list(range(1, max_page + 1)))
        offset = (page - 1) * page_size

        rows = conn.execute(
            f"""
            SELECT
                c.id,
                c.bottle_id,
                c.legacy_id,
                c.chemical_name,
                c.description,
                c.cas_number,
                c.manufacturer,
                c.catalog_number,
                c.lot_number,
                c.initial_amount,
                COALESCE(c.remaining_amount, c.amount) AS remaining_amount,
                c.unit,
                c.owner,
                c.expiration_date,
                c.status,
                c.storage_unit_id,
                c.shelf_number,
                {LOCATION_EXPR} AS location_text
            FROM chemicals c
            LEFT JOIN storage_units su ON c.storage_unit_id = su.id
            WHERE c.status != 'Disposed'
            {search_clause}
            ORDER BY c.chemical_name, c.id
            LIMIT ? OFFSET ?
            """,
            params + [page_size, offset],
        ).fetchall()
        conn.close()

        st.caption(f"{total_rows:,} active chemical(s)")

        if rows:
            header = st.columns([1.5, 3.0, 2.0, 1.3, 2.7, 1.4, 2.8])
            header[0].markdown("**Bottle ID**")
            header[1].markdown("**Chemical**")
            header[2].markdown("**Manufacturer**")
            header[3].markdown("**Remaining**")
            header[4].markdown("**Location**")
            header[5].markdown("**Status**")
            header[6].markdown("**Action**")
            st.divider()

            for chemical in rows:
                cols = st.columns([1.5, 3.0, 2.0, 1.3, 2.7, 1.4, 2.8])
                cols[0].write(chemical["bottle_id"] or "-")
                cols[1].write(chemical["chemical_name"])
                cols[2].write(chemical["manufacturer"] or "-")

                amount_text = "-"
                if chemical["remaining_amount"] is not None:
                    amount_text = f"{chemical['remaining_amount']:g} {chemical['unit'] or ''}"
                cols[3].write(amount_text)
                cols[4].write(chemical["location_text"])
                cols[5].write(chemical["status"])

                with cols[6]:
                    edit_col, dispose_col = st.columns([1, 1.35])
                    if edit_col.button("Edit", key=f"edit_{chemical['id']}"):
                        st.session_state["edit_candidate"] = chemical["id"]
                        st.rerun()

                    if dispose_col.button(
                        "Dispose", key=f"dispose_{chemical['id']}"
                    ):
                        st.session_state.pop("edit_candidate", None)
                        dispose_chemical_dialog(chemical["id"])
                st.divider()
        else:
            st.info("No active chemicals found.")

        # ----------------------------------------------------
        # Edit chemical
        # ----------------------------------------------------
        if "edit_candidate" in st.session_state:
            edit_id = st.session_state["edit_candidate"]
            conn = get_connection()
            chemical = conn.execute(
                "SELECT * FROM chemicals WHERE id=?",
                (edit_id,),
            ).fetchone()
            conn.close()

            if chemical:
                st.subheader(f"✏️ Edit Chemical — {chemical['chemical_name']}")
                st.caption(
                    f"Bottle ID: {chemical['bottle_id'] or '-'}   |   Legacy ID: {chemical['legacy_id'] or '-'}"
                )

                storage_options = get_storage_options()
                storage_index = current_location_index(
                    storage_options,
                    chemical["storage_unit_id"],
                    chemical["shelf_number"],
                )

                unit_options = ["g", "mg", "kg", "mL", "L", "ea"]
                current_unit = chemical["unit"] or "g"
                if current_unit not in unit_options:
                    unit_options.append(current_unit)

                status_options = ["Unopened", "In Use", "Disposal Pending"]
                current_status = chemical["status"]
                if current_status not in status_options:
                    status_options.append(current_status)

                with st.form("edit_chemical_form", enter_to_submit=False):
                    edit_name = st.text_input(
                        "Chemical Name *", value=chemical["chemical_name"] or ""
                    )
                    edit_description = st.text_input(
                        "Description / Grade", value=chemical["description"] or ""
                    )

                    col1, col2 = st.columns(2)
                    with col1:
                        edit_cas = st.text_input(
                            "CAS No.", value=chemical["cas_number"] or ""
                        )
                        edit_manufacturer = st.text_input(
                            "Manufacturer", value=chemical["manufacturer"] or ""
                        )
                        edit_catalog = st.text_input(
                            "Catalog No.", value=chemical["catalog_number"] or ""
                        )
                        edit_lot = st.text_input(
                            "Lot No.", value=chemical["lot_number"] or ""
                        )
                        edit_initial = st.number_input(
                            "Initial Amount",
                            min_value=0.0,
                            value=float(
                                chemical["initial_amount"]
                                if chemical["initial_amount"] is not None
                                else chemical["amount"] or 0
                            ),
                        )

                    with col2:
                        edit_remaining = st.number_input(
                            "Remaining Amount",
                            min_value=0.0,
                            value=float(
                                chemical["remaining_amount"]
                                if chemical["remaining_amount"] is not None
                                else chemical["amount"] or 0
                            ),
                        )
                        edit_unit = st.selectbox(
                            "Unit",
                            unit_options,
                            index=unit_options.index(current_unit),
                        )
                        edit_storage = st.selectbox(
                            "Storage Location",
                            storage_options,
                            index=storage_index,
                            format_func=lambda option: option[2],
                        )
                        edit_owner = st.text_input(
                            "Owner / Person in Charge", value=chemical["owner"] or ""
                        )
                        edit_expiration = st.text_input(
                            "Expiration Date",
                            value=chemical["expiration_date"] or "",
                            placeholder="YYYY-MM-DD",
                        )

                    edit_status = st.selectbox(
                        "Status",
                        status_options,
                        index=status_options.index(current_status),
                    )

                    save_col, cancel_col = st.columns(2)
                    with save_col:
                        save_edit = st.form_submit_button(
                            "Save Changes", type="primary"
                        )
                    with cancel_col:
                        cancel_edit = st.form_submit_button("Cancel")

                    if save_edit:
                        if not edit_name.strip():
                            st.error("Chemical name is required.")
                        else:
                            conn = get_connection()
                            conn.execute(
                                """
                                UPDATE chemicals
                                SET chemical_name=?,
                                    description=?,
                                    cas_number=?,
                                    manufacturer=?,
                                    catalog_number=?,
                                    lot_number=?,
                                    amount=?,
                                    initial_amount=?,
                                    remaining_amount=?,
                                    unit=?,
                                    storage_unit_id=?,
                                    shelf_number=?,
                                    location=?,
                                    owner=?,
                                    expiration_date=?,
                                    status=?,
                                    updated_at=?
                                WHERE id=?
                                """,
                                (
                                    edit_name.strip(),
                                    edit_description.strip(),
                                    edit_cas.strip(),
                                    edit_manufacturer.strip(),
                                    edit_catalog.strip(),
                                    edit_lot.strip(),
                                    edit_initial,
                                    edit_initial,
                                    edit_remaining,
                                    edit_unit,
                                    edit_storage[0],
                                    edit_storage[1],
                                    edit_storage[2]
                                    if edit_storage[0] is not None
                                    else "",
                                    edit_owner.strip(),
                                    edit_expiration.strip(),
                                    edit_status,
                                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                                    edit_id,
                                ),
                            )
                            write_audit_log(
                                "EDIT_CHEMICAL",
                                target_type="chemical",
                                target_id=edit_id,
                                bottle_id=chemical["bottle_id"],
                                details=json.dumps(
                                    {
                                        "chemical_name": edit_name.strip(),
                                        "remaining_amount": edit_remaining,
                                        "unit": edit_unit,
                                        "location": (
                                            edit_storage[2]
                                            if edit_storage[0] is not None
                                            else "Not Assigned"
                                        ),
                                        "owner": edit_owner.strip(),
                                        "status": edit_status,
                                    },
                                    ensure_ascii=False,
                                ),
                                conn=conn,
                            )
                            conn.commit()
                            conn.close()
                            st.session_state.pop("edit_candidate", None)
                            set_notice("✅ Chemical information has been updated.")
                            st.rerun()

                    if cancel_edit:
                        st.session_state.pop("edit_candidate", None)
                        st.rerun()

    # --------------------------------------------------------
    # Disposal history
    # --------------------------------------------------------
    with tab2:
        st.subheader("Disposal History")

        col1, col2 = st.columns([3, 2])
        with col1:
            disposal_search = st.text_input(
                "Search Disposal History",
                placeholder="Bottle ID, chemical name, CAS No., manufacturer...",
            )
        with col2:
            reason_filter = st.selectbox(
                "Disposal Reason",
                [
                    "All",
                    "Empty / Fully Used",
                    "Expired",
                    "Damaged Container",
                    "Contaminated",
                    "Quality Concern",
                    "No Longer Needed",
                    "Duplicate / Excess Stock",
                    "Other",
                ],
            )

        query = f"""
        SELECT
            c.bottle_id AS "Bottle ID",
            c.legacy_id AS "Legacy ID",
            c.chemical_name AS "Chemical Name",
            c.cas_number AS "CAS No.",
            c.manufacturer AS "Manufacturer",
            COALESCE(
                NULLIF(c.disposal_location, ''),
                {LOCATION_EXPR}
            ) AS "Previous Location",
            COALESCE(NULLIF(c.disposal_reason, ''), 'Legacy / Unspecified') AS "Reason",
            c.disposal_note AS "Note",
            c.disposed_at AS "Disposal Date"
        FROM chemicals c
        LEFT JOIN storage_units su ON c.storage_unit_id = su.id
        WHERE c.status='Disposed'
        """
        params = []

        if disposal_search:
            like = f"%{disposal_search}%"
            query += """
            AND (
                   c.bottle_id ILIKE ?
                OR c.legacy_id ILIKE ?
                OR c.chemical_name ILIKE ?
                OR c.cas_number ILIKE ?
                OR c.manufacturer ILIKE ?
            )
            """
            params.extend([like] * 5)

        if reason_filter != "All":
            query += " AND c.disposal_reason=? "
            params.append(reason_filter)

        query += " ORDER BY c.disposed_at DESC, c.id DESC "

        conn = get_connection()
        disposed = conn.execute(query, params).fetchall()
        conn.close()

        if disposed:
            st.dataframe(
                [dict(row) for row in disposed],
                width="stretch",
                hide_index=True,
            )
            st.caption(f"{len(disposed):,} disposal record(s)")
        else:
            st.info("No disposal records found.")


# ============================================================
# SDS
# ============================================================

elif menu == "SDS":
    st.header("📄 SDS & Safety Management")
    st.caption(
        "Review automatic PubChem GHS screening and attach the official manufacturer SDS."
    )

    st.warning(
        "PubChem safety screening is not a substitute for the official product SDS. "
        "Signal words, H-codes, handling instructions, PPE, storage, and incompatibility "
        "must be verified against the manufacturer's current SDS."
    )

    include_disposed = st.checkbox("Include disposed chemicals", value=False)
    sds_search = st.text_input(
        "Search Chemical",
        placeholder="Chemical name, Bottle ID, CAS No., manufacturer, catalog number...",
    )

    conditions = []
    params = []

    if not include_disposed:
        conditions.append("c.status != 'Disposed'")

    if sds_search.strip():
        like = f"%{sds_search.strip()}%"
        conditions.append(
            """
            (
                   c.bottle_id ILIKE ?
                OR c.legacy_id ILIKE ?
                OR c.chemical_name ILIKE ?
                OR c.cas_number ILIKE ?
                OR c.manufacturer ILIKE ?
                OR c.catalog_number ILIKE ?
            )
            """
        )
        params.extend([like] * 6)

    where_sql = ""
    if conditions:
        where_sql = "WHERE " + " AND ".join(conditions)

    conn = get_connection()
    sds_rows = conn.execute(
        f"""
        SELECT
            c.id,
            c.bottle_id,
            c.chemical_name,
            c.cas_number,
            c.manufacturer,
            c.catalog_number,
            c.status,
            c.safety_signal_word,
            c.safety_hazard_statements,
            c.safety_checked_at,
            CASE WHEN sd.chemical_id IS NULL THEN 0 ELSE 1 END AS has_sds
        FROM chemicals c
        LEFT JOIN sds_documents sd ON sd.chemical_id = c.id
        {where_sql}
        ORDER BY c.chemical_name, c.id
        LIMIT 300
        """,
        params,
    ).fetchall()
    conn.close()

    if not sds_rows:
        st.info("No chemicals match the search.")
    else:
        st.caption(f"{len(sds_rows):,} result(s) shown")

        selection_options = [row["id"] for row in sds_rows]
        rows_by_id = {row["id"]: row for row in sds_rows}

        selected_id = st.selectbox(
            "Select Chemical",
            selection_options,
            format_func=lambda chemical_id: (
                f"{rows_by_id[chemical_id]['chemical_name']}  |  "
                f"{rows_by_id[chemical_id]['bottle_id'] or '-'}  |  "
                f"CAS {rows_by_id[chemical_id]['cas_number'] or '-'}  |  "
                f"{safety_badge(rows_by_id[chemical_id]['safety_signal_word'], rows_by_id[chemical_id]['safety_hazard_statements'], rows_by_id[chemical_id]['safety_checked_at'])}  |  "
                f"{'📄 SDS attached' if rows_by_id[chemical_id]['has_sds'] else 'No SDS'}"
            ),
        )

        conn = get_connection()
        chemical = conn.execute(
            "SELECT * FROM chemicals WHERE id=?",
            (selected_id,),
        ).fetchone()
        sds_doc = conn.execute(
            "SELECT * FROM sds_documents WHERE chemical_id=?",
            (selected_id,),
        ).fetchone()
        conn.close()

        if chemical:
            st.divider()
            st.subheader(f"🧪 {chemical['chemical_name']}")
            st.caption(
                f"Bottle ID: {chemical['bottle_id'] or '-'}   |   "
                f"CAS No.: {chemical['cas_number'] or '-'}   |   "
                f"Manufacturer: {chemical['manufacturer'] or '-'}   |   "
                f"Catalog No.: {chemical['catalog_number'] or '-'}"
            )

            st.markdown("### 1. Safety Screening")

            action1, action2 = st.columns([1, 2])
            with action1:
                check_label = (
                    "Refresh Safety Screening"
                    if chemical["safety_checked_at"]
                    else "Check Safety Screening"
                )
                check_safety = st.button(
                    check_label,
                    type="primary",
                    width="stretch",
                    key=f"check_safety_{selected_id}",
                )

            with action2:
                if chemical["safety_checked_at"]:
                    st.caption(
                        f"Last checked: {chemical['safety_checked_at']}  |  "
                        f"Matched using: {chemical['safety_match_term'] or '-'}"
                    )

            if check_safety:
                with st.spinner("Checking PubChem GHS information..."):
                    safety = fetch_pubchem_safety(
                        chemical["cas_number"],
                        chemical["chemical_name"],
                    )

                if safety:
                    save_pubchem_safety(selected_id, safety)
                    set_notice("✅ Safety screening has been updated.")
                    st.rerun()
                else:
                    st.error(
                        "No reliable PubChem match was found from the CAS No. or chemical name. "
                        "Do not assume the chemical is non-hazardous; verify the official SDS."
                    )

            render_safety_panel(
                signal_word=chemical["safety_signal_word"],
                h_codes=chemical["safety_h_codes"],
                hazard_statements=chemical["safety_hazard_statements"],
                pictograms=chemical["safety_pictograms"],
                source_url=chemical["safety_source_url"],
                checked_at=chemical["safety_checked_at"],
            )

            st.divider()
            st.markdown("### 2. Official SDS")

            if sds_doc:
                st.success("✅ An official SDS record is attached to this bottle.")

                meta1, meta2, meta3 = st.columns(3)
                meta1.markdown(
                    f"**Source**\n\n{sds_doc['source_name'] or chemical['manufacturer'] or '-'}"
                )
                meta2.markdown(
                    f"**Revision Date**\n\n{sds_doc['revision_date'] or '-'}"
                )
                meta3.markdown(
                    f"**Uploaded**\n\n{sds_doc['uploaded_at'] or '-'}"
                )

                if sds_doc["source_url"]:
                    st.link_button("Open Official SDS Source", sds_doc["source_url"])

                if sds_doc["file_data"]:
                    st.download_button(
                        "📄 Download Attached SDS PDF",
                        data=bytes(sds_doc["file_data"]),
                        file_name=sds_doc["file_name"] or "SDS.pdf",
                        mime="application/pdf",
                        key=f"download_sds_{selected_id}",
                    )

                st.caption(
                    "Uploading a new PDF below will replace the current attached SDS record."
                )
            else:
                st.info(
                    "No official manufacturer SDS is attached yet. "
                    "Upload the current product SDS or save its official source link below."
                )

            default_source_name = ""
            default_source_url = ""
            default_revision_date = ""

            if sds_doc:
                default_source_name = sds_doc["source_name"] or ""
                default_source_url = sds_doc["source_url"] or ""
                default_revision_date = sds_doc["revision_date"] or ""
            else:
                default_source_name = chemical["manufacturer"] or ""

            with st.form(
                f"official_sds_form_{selected_id}",
                clear_on_submit=False,
                enter_to_submit=False,
            ):
                source_name = st.text_input(
                    "SDS Source / Manufacturer",
                    value=default_source_name,
                    placeholder="e.g. Sigma-Aldrich",
                )
                source_url = st.text_input(
                    "Official SDS URL",
                    value=default_source_url,
                    placeholder="https://...",
                )
                revision_date = st.text_input(
                    "SDS Revision Date",
                    value=default_revision_date,
                    placeholder="YYYY-MM-DD",
                )
                sds_pdf = st.file_uploader(
                    "Upload Official SDS PDF",
                    type=["pdf"],
                    key=f"official_sds_pdf_{selected_id}",
                )

                save_sds = st.form_submit_button(
                    "Save Official SDS",
                    type="primary",
                )

            if save_sds:
                existing_file_name = sds_doc["file_name"] if sds_doc else None
                existing_file_data = sds_doc["file_data"] if sds_doc else None

                if sds_pdf is not None:
                    file_name = sds_pdf.name
                    file_data = sds_pdf.getvalue()
                else:
                    file_name = existing_file_name
                    file_data = existing_file_data

                if not file_data and not source_url.strip():
                    st.error(
                        "Upload an SDS PDF or provide the official SDS source URL."
                    )
                else:
                    conn = get_connection()
                    conn.execute(
                        """
                        INSERT INTO sds_documents (
                            chemical_id, file_name, file_data,
                            source_name, source_url, revision_date, uploaded_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(chemical_id) DO UPDATE SET
                            file_name=excluded.file_name,
                            file_data=excluded.file_data,
                            source_name=excluded.source_name,
                            source_url=excluded.source_url,
                            revision_date=excluded.revision_date,
                            uploaded_at=excluded.uploaded_at
                        """,
                        (
                            selected_id,
                            file_name,
                            file_data,
                            source_name.strip(),
                            source_url.strip(),
                            revision_date.strip(),
                            datetime.now().strftime("%Y-%m-%d %H:%M"),
                        ),
                    )
                    write_audit_log(
                        "SAVE_SDS",
                        target_type="chemical",
                        target_id=selected_id,
                        bottle_id=chemical["bottle_id"],
                        details=json.dumps(
                            {
                                "source_name": source_name.strip(),
                                "source_url": source_url.strip(),
                                "revision_date": revision_date.strip(),
                                "file_name": file_name,
                            },
                            ensure_ascii=False,
                        ),
                        conn=conn,
                    )
                    conn.commit()
                    conn.close()
                    set_notice("✅ Official SDS record has been saved.")
                    st.rerun()

            if sds_doc:
                if st.button(
                    "Remove Attached SDS Record",
                    key=f"remove_sds_{selected_id}",
                ):
                    conn = get_connection()
                    conn.execute(
                        "DELETE FROM sds_documents WHERE chemical_id=?",
                        (selected_id,),
                    )
                    write_audit_log(
                        "REMOVE_SDS",
                        target_type="chemical",
                        target_id=selected_id,
                        bottle_id=chemical["bottle_id"],
                        details="Removed attached official SDS record",
                        conn=conn,
                    )
                    conn.commit()
                    conn.close()
                    set_notice("SDS record removed.")
                    st.rerun()

    st.divider()
    with st.expander("How this safety system works"):
        st.write(
            """
            1. New manually registered chemicals are screened automatically using CAS No. first.
            2. PubChem GHS data are stored as a safety screening record.
            3. Danger/Warning information is displayed prominently when available.
            4. The manufacturer SDS is managed separately as the official safety document.
            5. A missing PubChem warning must never be interpreted as proof that a chemical is safe.
            """
        )



# ============================================================
# USER MANAGEMENT
# ============================================================

elif menu == "User Management":
    if CURRENT_USER["role"] != "admin":
        st.error("Administrator access is required.")
        st.stop()

    st.header("👥 User Management")
    st.caption(
        "Only active users listed here can access the laboratory inventory after Google sign-in."
    )

    conn = get_connection()
    users = conn.execute(
        """
        SELECT id, email, display_name, role, active, created_at, updated_at, created_by
        FROM app_users
        ORDER BY active DESC, role, LOWER(email)
        """
    ).fetchall()
    conn.close()

    active_users = sum(1 for user in users if bool(user["active"]))
    admin_users = sum(
        1
        for user in users
        if bool(user["active"]) and str(user["role"]).lower() == "admin"
    )

    metric1, metric2, metric3 = st.columns(3)
    metric1.metric("Registered Users", len(users))
    metric2.metric("Active Users", active_users)
    metric3.metric("Active Admins", admin_users)

    st.divider()
    st.subheader("Add or Update User")

    user_choices = ["➕ New user"] + [user["email"] for user in users]
    selected_user_choice = st.selectbox(
        "User",
        user_choices,
        key="user_management_selector",
    )

    selected_user = None
    if selected_user_choice != "➕ New user":
        selected_user = next(
            (user for user in users if user["email"] == selected_user_choice),
            None,
        )

    default_email = selected_user["email"] if selected_user else ""
    default_name = selected_user["display_name"] if selected_user else ""
    default_role = (
        str(selected_user["role"] or "member").lower()
        if selected_user
        else "member"
    )
    default_active = bool(selected_user["active"]) if selected_user else True

    role_options = ["member", "admin"]
    if default_role not in role_options:
        role_options.append(default_role)

    with st.form("manage_user_form", enter_to_submit=False):
        user_email = st.text_input(
            "Google Account Email *",
            value=default_email,
            disabled=selected_user is not None,
            placeholder="name@example.com",
        )
        display_name = st.text_input(
            "Display Name",
            value=default_name or "",
            placeholder="Name shown in audit records",
        )
        role = st.selectbox(
            "Role",
            role_options,
            index=role_options.index(default_role),
            help="Admins can manage users and view the full activity log.",
        )
        active = st.checkbox(
            "Active",
            value=default_active,
            help="Inactive users can sign in to Google but cannot access the inventory.",
        )
        save_user = st.form_submit_button(
            "Save User",
            type="primary",
        )

    if save_user:
        normalized_email = user_email.strip().lower()
        normalized_name = display_name.strip() or normalized_email

        if not normalized_email or "@" not in normalized_email:
            st.error("Enter a valid Google account email.")
        elif (
            normalized_email == CURRENT_USER["email"]
            and (not active or role != "admin")
        ):
            st.error(
                "You cannot deactivate or remove admin rights from your own current account."
            )
        else:
            conn = get_connection()
            existing = conn.execute(
                """
                SELECT id, email, role, active
                FROM app_users
                WHERE LOWER(email)=LOWER(?)
                """,
                (normalized_email,),
            ).fetchone()

            # Protect the last active administrator.
            if (
                existing
                and str(existing["role"]).lower() == "admin"
                and bool(existing["active"])
                and (role != "admin" or not active)
            ):
                active_admin_count = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM app_users
                    WHERE active=TRUE AND LOWER(role)='admin'
                    """
                ).fetchone()[0]

                if active_admin_count <= 1:
                    conn.close()
                    st.error("At least one active administrator must remain.")
                    st.stop()

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if existing:
                conn.execute(
                    """
                    UPDATE app_users
                    SET display_name=?,
                        role=?,
                        active=?,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        normalized_name,
                        role,
                        active,
                        now,
                        existing["id"],
                    ),
                )
                user_id = existing["id"]
                audit_action = "EDIT_USER"
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO app_users (
                        email, display_name, role, active,
                        created_at, updated_at, created_by
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_email,
                        normalized_name,
                        role,
                        active,
                        now,
                        now,
                        CURRENT_USER["email"],
                    ),
                )
                user_id = cursor.lastrowid
                audit_action = "ADD_USER"

            write_audit_log(
                audit_action,
                target_type="user",
                target_id=user_id,
                details=json.dumps(
                    {
                        "email": normalized_email,
                        "display_name": normalized_name,
                        "role": role,
                        "active": active,
                    },
                    ensure_ascii=False,
                ),
                conn=conn,
            )
            conn.commit()
            conn.close()
            set_notice(f"✅ User settings saved for {normalized_email}.")
            st.rerun()

    st.divider()
    st.subheader("Registered Users")

    if users:
        user_table = []
        for user in users:
            user_table.append(
                {
                    "Name": user["display_name"] or "-",
                    "Email": user["email"],
                    "Role": str(user["role"] or "member").title(),
                    "Status": "Active" if bool(user["active"]) else "Inactive",
                    "Created": user["created_at"] or "-",
                }
            )

        st.dataframe(
            user_table,
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("No users are registered yet.")


# ============================================================
# ACTIVITY LOG
# ============================================================

elif menu == "Activity Log":
    if CURRENT_USER["role"] != "admin":
        st.error("Administrator access is required.")
        st.stop()

    st.header("📋 Activity Log")
    st.caption(
        "Shows who registered, edited, moved, disposed of, or otherwise changed laboratory records."
    )

    conn = get_connection()
    action_rows = conn.execute(
        """
        SELECT DISTINCT action
        FROM audit_logs
        WHERE action IS NOT NULL AND TRIM(action) != ''
        ORDER BY action
        """
    ).fetchall()
    conn.close()

    action_options = ["All Actions"] + [row["action"] for row in action_rows]

    filter1, filter2 = st.columns([2.5, 1.2])
    with filter1:
        activity_search = st.text_input(
            "Search Activity",
            placeholder="User, email, action, Bottle ID, details...",
        )
    with filter2:
        selected_action = st.selectbox(
            "Action",
            action_options,
        )

    conditions = []
    params = []

    if activity_search.strip():
        like = f"%{activity_search.strip()}%"
        conditions.append(
            """
            (
                   user_name ILIKE ?
                OR user_email ILIKE ?
                OR action ILIKE ?
                OR bottle_id ILIKE ?
                OR details ILIKE ?
            )
            """
        )
        params.extend([like] * 5)

    if selected_action != "All Actions":
        conditions.append("action=?")
        params.append(selected_action)

    where_sql = ""
    if conditions:
        where_sql = "WHERE " + " AND ".join(conditions)

    conn = get_connection()
    logs = conn.execute(
        f"""
        SELECT
            created_at,
            user_name,
            user_email,
            action,
            target_type,
            target_id,
            bottle_id,
            details
        FROM audit_logs
        {where_sql}
        ORDER BY id DESC
        LIMIT 1000
        """,
        params,
    ).fetchall()
    conn.close()

    st.caption(f"{len(logs):,} activity record(s) shown")

    if logs:
        display_logs = []
        for log in logs:
            display_logs.append(
                {
                    "Time": log["created_at"],
                    "User": log["user_name"] or "-",
                    "Email": log["user_email"],
                    "Action": log["action"],
                    "Bottle ID": log["bottle_id"] or "-",
                    "Target": (
                        f"{log['target_type'] or '-'}"
                        + (
                            f" #{log['target_id']}"
                            if log["target_id"] is not None
                            else ""
                        )
                    ),
                    "Details": log["details"] or "-",
                }
            )

        st.dataframe(
            display_logs,
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("No activity records match the selected filters.")


# ============================================================
# SETTINGS
# ============================================================

elif menu == "Settings":
    st.header("⚙️ Settings")

    conn = get_connection()
    active_count = conn.execute(
        "SELECT COUNT(*) FROM chemicals WHERE status != 'Disposed'"
    ).fetchone()[0]
    disposed_count = conn.execute(
        "SELECT COUNT(*) FROM chemicals WHERE status='Disposed'"
    ).fetchone()[0]
    storage_count = conn.execute("SELECT COUNT(*) FROM storage_units").fetchone()[0]
    user_count = conn.execute(
        "SELECT COUNT(*) FROM app_users WHERE active=TRUE"
    ).fetchone()[0]
    audit_count = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    conn.close()

    st.subheader("System Information")
    st.write("Database: Supabase PostgreSQL")
    st.write(f"Signed in as: {CURRENT_USER['name']} ({CURRENT_USER['email']})")
    st.write(f"Role: {CURRENT_USER['role'].title()}")
    st.write(f"Active chemicals: {active_count:,}")
    st.write(f"Disposed chemicals: {disposed_count:,}")
    st.write(f"Storage units: {storage_count:,}")
    st.write(f"Active users: {user_count:,}")
    st.write(f"Audit records: {audit_count:,}")

    st.divider()
    st.subheader("Current Features")
    st.write(
        """
        - Google OIDC login
        - Approved-user whitelist with Admin / Member roles
        - User activity / audit logging
        - Manual chemical registration
        - Lab Manager Excel bulk import
        - Excel inventory export
        - Automatic Bottle ID generation
        - Legacy management ID retention
        - Storage-unit auto creation from Excel
        - Storage renaming and shelf management
        - Chemical search
        - Active inventory pagination
        - Chemical editing and relocation
        - Disposal with predefined reasons
        - Disposal history
        - Automatic PubChem GHS safety screening for new chemicals
        - Official SDS PDF / source-link management
        - Safety warnings in chemical detail view
        """
    )

    st.divider()
    st.subheader("Planned Features")
    st.write(
        """
        - QR code generation
        - Manufacturer-specific SDS auto-discovery
        - AI-assisted chemical label recognition
        - AI SDS summarization
        - Natural-language inventory search
        - Automated backup / reporting
        """
    )
