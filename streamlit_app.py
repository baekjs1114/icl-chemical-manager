# ICL V8 standalone website. Keep existing requirements and Streamlit Secrets.
import sys as _icl_sys
import types as _icl_types
_icl_guard = _icl_types.ModuleType("icl_ocr_ui_guard")
_icl_guard.__file__ = __file__ + ":icl_ocr_ui_guard"
exec(compile('"""Apply after all existing UI transformations; compatible with the supplied ICL app.\n\nThis module uses the application\'s existing requests and Streamlit objects. No ML\npackages or API key belong in the Streamlit repository.\n"""\nfrom __future__ import annotations\n\nimport ast\nimport hashlib\nimport json\n\n\ndef is_v8(result):\n    return isinstance(result, dict) and result.get("schema_version") == "icl.ocr.v8"\n\n\ndef candidates(result):\n    return list(result.get("review_candidates", [])) if is_v8(result) else []\n\n\ndef candidate_label(candidate):\n    sources = {"reviewed_reference": "확인한 제조사 참조", "official_product_page": "제조사 제품 페이지",\n               "pubchem_exact_cas_synonym": "PubChem CAS 대조", "inventory_unreviewed": "기존 재고 후보"}\n    parts = [str(candidate.get("name", ""))]\n    if candidate.get("cas"):\n        parts.append("CAS " + str(candidate["cas"]))\n    if candidate.get("catalog"):\n        parts.append(str(candidate.get("manufacturer", "")) + " " + str(candidate["catalog"]))\n    parts.append(sources.get(candidate.get("source"), "출처 확인 필요"))\n    return " | ".join(parts)\n\n\ndef metadata(result):\n    defaults = {"label_name": "", "verified_label_name": "", "verified_label_name_source": "",\n                "label_name_confidence": 0, "manufacturer": "", "catalog": "", "cas": "",\n                "catalog_candidates": [], "catalog_ambiguous": False, "catalog_confidence": 0,\n                "raw_cas_candidates": [], "cas_candidates": [], "cas_verified": False, "cas_ambiguous": False,\n                "lot": "", "purity": "", "amount": None, "unit": ""}\n    if is_v8(result):\n        defaults.update(result.get("metadata", {}))\n        defaults["label_name_confidence"] = defaults.get("label_name_confidence") or 0\n    return defaults\n\n\ndef review_key(result, candidate):\n    value = str(result.get("review_token", "")) + ":" + str(candidate.get("candidate_id", ""))\n    return "icl_v8_review_" + hashlib.sha256(value.encode()).hexdigest()[:20]\n\n\ndef choice_key(result):\n    value = str(result.get("scan_id")) + ":" + str(result.get("review_token", ""))\n    return "icl_v8_choice_" + hashlib.sha256(value.encode()).hexdigest()[:20]\n\n\ndef render_review(env, result):\n    st = env["st"]\n    if not is_v8(result):\n        st.warning("이전 OCR 결과입니다. 새 OCR API로 다시 판독하세요.")\n        return\n    state = result.get("verification_status")\n    if state == "PRODUCT_VERIFIED":\n        st.success("제품 정보 확인 — 저장할 이름은 확인된 제품명을 사용합니다.")\n    elif state == "IDENTITY_VERIFIED":\n        st.info("물질 확인 — 제조사/제품번호는 별도 확인이 필요할 수 있습니다.")\n    elif state == "CONFLICT":\n        st.error("식별 정보 충돌 — 사진 그룹 또는 라벨을 확인하세요. 자동 적용할 수 없습니다.")\n    else:\n        st.warning("미확정 — 아래 후보와 원본을 비교한 뒤 선택하세요.")\n    for item in result.get("conflicts", []):\n        st.error(item)\n    with st.expander("OCR 원문과 제품 조회 근거"):\n        st.code("\\n".join(o["text"] for o in result.get("observations", []) if o.get("pass_id") == "full"))\n        st.json({"fields": result.get("fields", {}), "lookups": result.get("lookup_events", []),\n                 "warnings": result.get("warnings", []), "runtime": result.get("runtime", {})})\n    packages = result.get("fields", {}).get("package", [])\n    if packages:\n        st.caption("판독한 포장 표기: " + " / ".join(dict.fromkeys(x["value"] for x in packages)) + " · 현재 잔량은 직접 입력하세요.")\n\n\ndef render_candidate(env, result, candidate):\n    if not is_v8(result):\n        return\n    st = env["st"]\n    st.write("적용할 이름: **" + str(candidate.get("name", "")) + "**")\n    st.write("CAS: " + str(candidate.get("cas", "")) + " · 제품번호: " + str(candidate.get("catalog", "")))\n    if candidate.get("source_url"):\n        st.markdown("[제품/물질 출처 확인](" + candidate["source_url"] + ")")\n    if not candidate.get("product_verified"):\n        st.checkbox("원본 라벨과 후보의 이름·CAS·제품번호를 대조했습니다.", key=review_key(result, candidate))\n\n\ndef _post(env, path, payload):\n    response = env["requests"].post(env["get_ocr_api_url"]().rstrip("/") + path,\n        headers={"X-API-Key": env["get_ocr_api_key"]()}, json=payload, timeout=60)\n    if response.status_code != 200:\n        raise ValueError("OCR API " + str(response.status_code) + ": " + response.text[:250])\n    return response.json()\n\n\ndef apply_result(env, result, original_bytes, filename, selected_candidate=None, source_scan_id=None, evidence_photos=None):\n    st = env["st"]\n    if not is_v8(result):\n        st.error("이전 OCR 결과를 적용할 수 없습니다. 새 API로 다시 판독하거나 수동으로 입력하세요.")\n        st.stop()\n    selected = selected_candidate or result.get("verified_identity")\n    if not selected:\n        st.warning("제품/물질 후보를 먼저 선택하세요. 확인할 수 없으면 수동 입력을 사용하세요.")\n        st.stop()\n    try:\n        draft = _post(env, "/draft", {"review_token": result.get("review_token"),\n            "candidate_id": selected.get("candidate_id"),\n            "reviewed": bool(st.session_state.get(review_key(result, selected), False))})\n    except Exception as exc:\n        st.error(str(exc))\n        st.stop()  # Prevent the old caller from displaying its success banner/rerun.\n    fields = draft["fields"]\n    for key, field in (("add_name", "name"), ("add_cas", "cas"), ("add_formula", "formula"),\n                       ("add_mw", "mw"), ("add_manufacturer", "manufacturer"), ("add_catalog", "catalog")):\n        value = fields.get(field)\n        st.session_state[key] = "" if value is None else str(value)\n    for key in ("add_lot", "add_purity", "add_description"):\n        st.session_state[key] = ""\n    # Clear a previous bottle\'s amount; no package quantity is inferred as remaining stock.\n    st.session_state["add_initial_amount"] = 0.0\n    st.session_state["add_remaining_amount"] = 0.0\n    st.session_state["add_unit"] = "g"\n    st.session_state["add_manufacturer_widget_needs_sync"] = True\n    st.session_state["add_ocr_result"] = result\n    st.session_state["add_ocr_original"] = original_bytes\n    st.session_state["add_ocr_filename"] = filename\n    st.session_state["icl_v8_registration_draft"] = draft\n    if evidence_photos is not None:\n        st.session_state["add_ocr_group_photos"] = evidence_photos\n    if source_scan_id:\n        st.session_state["add_source_scan_id"] = source_scan_id\n    st.session_state["_pending_navigation"] = "Add Chemical"\n    return draft\n\n\ndef merge_group(env, records, group_id):\n    successful = [r for r in records if not r.get("error") and r.get("result")]\n    base = {"group_id": group_id, "records": records, "result": {}, "error": "", "conflicts": []}\n    if not successful or not all(is_v8(r["result"]) for r in successful):\n        return {**base, "error": "새 OCR API로 이 그룹의 사진을 다시 판독하세요."}\n    unique = {r["result"]["scan_id"]: r for r in successful}\n    if len(unique) == 1:\n        return {**base, "result": next(iter(unique.values()))["result"], "conflicts": successful[0]["result"].get("conflicts", [])}\n    tokens = sorted(r["result"]["evidence_token"] for r in unique.values())\n    signature = hashlib.sha256(json.dumps(tokens).encode()).hexdigest()\n    cache = env["st"].session_state.setdefault("icl_v8_group_cache", {})\n    try:\n        if signature not in cache:\n            # Errors are deliberately not cached. A subsequent rerun can retry 503s.\n            result = _post(env, "/resolve-group", {"evidence_tokens": tokens})\n            if not result.get("ok"):\n                raise ValueError("Group resolution failed")\n            cache[signature] = result\n        result = dict(cache[signature])\n        result["label_crop_base64"] = successful[0]["result"].get("label_crop_base64", "")\n        return {**base, "result": result, "conflicts": result.get("conflicts", [])}\n    except Exception as exc:\n        return {**base, "error": str(exc)}\n\n\ndef transform_source(source):\n    """Targeted source-span patches preserve unrelated UI and database code.\n\n    Run on the final UI-transformed source, including all fallback compile paths.\n    Unsupported source is an error, never a silently partial safety patch.\n    """\n    if "# ICL_V8_GUARD_APPLIED" in source:\n        return source\n    tree = ast.parse(source)\n    lines = source.splitlines(keepends=True)\n    offsets = [0]\n    for line in lines:\n        offsets.append(offsets[-1] + len(line))\n    def offset(lineno, byte_column):\n        # AST columns are UTF-8 byte offsets; Python string indices are characters.\n        return offsets[lineno - 1] + len(lines[lineno - 1].encode()[:byte_column].decode())\n    edits, found = [], set()\n    def replace(node, text):\n        edits.append((offset(node.lineno, node.col_offset), offset(node.end_lineno, node.end_col_offset), text))\n    helper = \'__import__("icl_ocr_ui_guard")\'\n    bodies = {\n        "ocr_candidate_label": f"return {helper}.candidate_label(candidate)",\n        "resolve_current_bottle_metadata": f"return {helper}.metadata(result)",\n        "merge_add_ocr_group": f"return {helper}.merge_group(globals(), records, group_id)",\n        "load_add_form_from_ocr": f"return {helper}.apply_result(globals(), result, original_bytes, filename, selected_candidate, source_scan_id, evidence_photos)",\n        "load_add_form_from_existing_bottle": f"return {helper}.apply_result(globals(), result, original_bytes, filename, source_scan_id=source_scan_id)",\n    }\n    replaced_regions = []\n    for node in tree.body:\n        if isinstance(node, ast.FunctionDef) and node.name in bodies:\n            found.add(node.name)\n            replace(node, "def " + node.name + "(" + ast.unparse(node.args) + "):\\n    " + bodies[node.name])\n            replaced_regions.append((node.lineno, node.end_lineno))\n    missing = set(bodies) - found\n    if missing:\n        raise ValueError("Unsupported Streamlit source; missing functions: " + ", ".join(sorted(missing)))\n    for node in ast.walk(tree):\n        if not hasattr(node, "lineno") or any(a <= node.lineno <= b for a, b in replaced_regions):\n            continue\n        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "add_candidates" for t in node.targets):\n            indent = " " * node.col_offset\n            replace(node, f"add_candidates = {helper}.candidates(add_ocr_result)\\n{indent}{helper}.render_review(globals(), add_ocr_result)")\n        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "chosen_candidate":\n            first = node.body[0]\n            start = offset(first.lineno, first.col_offset)\n            edits.append((start, start, f"{helper}.render_candidate(globals(), add_ocr_result, chosen_candidate)\\n" + " " * first.col_offset))\n        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "st":\n            if node.func.attr == "radio" and any(k.arg == "key" and isinstance(k.value, ast.Constant) and k.value.value == "add_chemical_candidate_choice" for k in node.keywords):\n                for k in node.keywords:\n                    if k.arg == "index":\n                        replace(k.value, "None")\n                    elif k.arg == "key":\n                        replace(k.value, f"{helper}.choice_key(add_ocr_result)")\n            if node.func.attr == "success" and node.args and isinstance(node.args[0], ast.Constant) and str(node.args[0].value) in ("Label fields verified", "Core label fields verified"):\n                replace(node, \'st.caption("필드 표시 완료 · 제품/물질 검증 상태는 아래에서 확인하세요.")\')\n    for start, end, text in sorted(edits, reverse=True):\n        source = source[:start] + text + source[end:]\n    source += "\\n# ICL_V8_GUARD_APPLIED\\n"\n    compile(source, "<ICL v8 guarded source>", "exec")\n    return source\n', _icl_guard.__file__, "exec"), _icl_guard.__dict__)
_icl_sys.modules["icl_ocr_ui_guard"] = _icl_guard

import json
import base64
import hashlib
import re
import os
import zipfile
import queue
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

import psycopg
from psycopg.rows import dict_row
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from openpyxl.styles import Alignment, Font, PatternFill
from PIL import Image


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="ICL Chemical Manager",
    page_icon="🧪",
    layout="wide",
)

def _icl_exception_message(exc):
    message = str(exc).strip()
    if not message:
        message = "오류 상세 메시지가 없습니다. 오류 종류와 함께 이 화면을 알려주세요."
    return type(exc).__name__ + ": " + message


st.markdown("""
<style>


/* =========================================================
   GLOBAL
   ========================================================= */

.stApp {
    background-color: #F7F9FC;
}

/* Main content shell — shared by ALL categories */
.block-container {
    width: 100%;
    max-width: 1180px !important;
    margin-left: auto !important;
    margin-right: auto !important;
    padding-top: 1.55rem !important;
    padding-bottom: 2.6rem !important;
    padding-left: 1.35rem !important;
    padding-right: 1.35rem !important;
}

/* Keep the whole application compact and centered on large monitors. */
@media (min-width: 1500px) {
    .block-container {
        max-width: 1180px !important;
    }
}

/* Tablet / small desktop */
@media (max-width: 1100px) {
    .block-container {
        max-width: 100% !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
    }
}

/* Mobile */
@media (max-width: 700px) {
    .block-container {
        padding-top: 1rem !important;
        padding-left: 0.75rem !important;
        padding-right: 0.75rem !important;
    }
}



/* =========================================================
   TYPOGRAPHY — original UI + 2px
   ========================================================= */

.stApp,
.stApp p,
.stApp li,
.stApp label,
.stApp input,
.stApp textarea,
.stApp button,
.stApp [data-baseweb="select"],
[data-testid="stSidebar"] {
    font-size: 18px !important;
}

.stApp [data-testid="stCaptionContainer"] {
    font-size: 16px !important;
}

h1 {
    font-size: 36px !important;
}

h2 {
    font-size: 30px !important;
}

h3 {
    font-size: 25px !important;
}

h4 {
    font-size: 21px !important;
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
    font-size: 17px;
    font-weight: 500;
}



/* =========================================================
   SHARED UX / UI — applied to ALL categories
   ========================================================= */

/* More deliberate page rhythm */
main [data-testid="stVerticalBlock"] {
    gap: 0.9rem;
}

main [data-testid="stHorizontalBlock"] {
    gap: 0.9rem;
}

/* Page headings: less oversized, easier to scan */
h1 {
    margin-top: 0.1rem !important;
    margin-bottom: 0.45rem !important;
    line-height: 1.18 !important;
}

h2 {
    margin-top: 0.55rem !important;
    margin-bottom: 0.35rem !important;
    line-height: 1.2 !important;
}

h3, h4 {
    margin-top: 0.45rem !important;
    margin-bottom: 0.25rem !important;
}

/* Labels become consistent across Search / Inventory / Add / Storage / SDS */
.stApp label {
    font-weight: 600 !important;
    color: #30394A !important;
}

/* Uniform control height and density */
.stTextInput input,
.stNumberInput input,
.stDateInput input,
.stTextArea textarea,
[data-baseweb="select"] > div {
    min-height: 44px;
}

.stTextArea textarea {
    min-height: 92px;
}

/* Expander = reusable compact card */
[data-testid="stExpander"] {
    background: #FFFFFF;
    border: 1px solid #E3E8F0 !important;
    border-radius: 12px !important;
    overflow: hidden;
}

[data-testid="stExpander"] details summary {
    padding-top: 0.55rem !important;
    padding-bottom: 0.55rem !important;
}

/* File uploader: smaller and less dominant */
[data-testid="stFileUploader"] {
    margin-top: 0.1rem;
    margin-bottom: 0.2rem;
}

[data-testid="stFileUploaderDropzone"] {
    background: #FFFFFF !important;
    border: 1px dashed #C7D0DF !important;
    border-radius: 12px !important;
    padding: 0.65rem 0.8rem !important;
    min-height: 76px !important;
}

[data-testid="stFileUploaderDropzoneInstructions"] {
    padding-top: 0 !important;
    padding-bottom: 0 !important;
}

/* Alerts should read like compact information panels */
[data-testid="stAlert"] {
    border-radius: 11px !important;
    padding: 0.7rem 0.9rem !important;
    margin-top: 0.15rem !important;
    margin-bottom: 0.35rem !important;
}

/* Forms across Add Chemical / Storage / Admin / SDS */
[data-testid="stForm"] {
    padding: 1rem 1.1rem !important;
    border-radius: 12px !important;
    box-shadow: 0 1px 2px rgba(24, 36, 56, 0.03);
}

/* Metrics: smaller cards so dashboards do not sprawl */
[data-testid="stMetric"] {
    padding: 0.8rem 0.9rem !important;
    min-height: 92px;
}

/* Tables stay inside the shared content shell and scroll when necessary */
[data-testid="stDataFrame"] {
    width: 100% !important;
    max-width: 100% !important;
}

/* Buttons: unified height */
.stButton > button,
.stDownloadButton > button,
[data-testid="stFormSubmitButton"] > button {
    min-height: 42px;
}

/* Divider density */
hr {
    margin-top: 0.7rem !important;
    margin-bottom: 0.7rem !important;
}

/* Captions are readable but visually secondary */
[data-testid="stCaptionContainer"] {
    color: #6B7280 !important;
    line-height: 1.45 !important;
}

/* Sidebar remains readable without growing with the main content. */
[data-testid="stSidebar"] {
    min-width: 245px;
}

@media (max-width: 900px) {
    main [data-testid="stHorizontalBlock"] {
        gap: 0.65rem;
    }

    [data-testid="stMetric"] {
        min-height: 82px;
    }
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

st.markdown(
    '''<style>
:root {
  --icl-navy:#01194C;
  --icl-navy-hover:#0A2A64;
  --icl-accent:#2F5FB7;
  --icl-ink:#172033;
  --icl-muted:#667085;
  --icl-faint:#8B95A5;
  --icl-canvas:#F7F8FA;
  --icl-surface:#FFFFFF;
  --icl-surface-muted:#F4F6F8;
  --icl-border:#E1E6EE;
  --icl-border-strong:#CBD4E1;
  --icl-focus:#2F5FB7;
  --icl-space-1:4px;
  --icl-space-2:8px;
  --icl-space-3:12px;
  --icl-space-4:16px;
  --icl-space-5:24px;
  --icl-space-6:32px;
  --icl-radius-sm:5px;
  --icl-radius-md:8px;
}

/* -------------------------------------------------------------------------- */
/* Page frame                                                                 */
/* -------------------------------------------------------------------------- */
.stApp {
  background:var(--icl-surface)!important;
  color:var(--icl-ink)!important;
}

.block-container {
  max-width:820px!important;
  padding-block-start:1rem!important;
  padding-block-end:2.5rem!important;
  padding-inline:1rem!important;
}

[data-testid="stHeader"] {
  background:transparent!important;
}

.stApp,
.stApp p,
.stApp li,
.stApp label,
.stApp input,
.stApp textarea,
.stApp button,
.stApp [data-baseweb="select"] {
  font-size:17px!important;
}

h1,h2,h3,h4 {
  color:var(--icl-navy)!important;
  letter-spacing:-.025em!important;
  text-wrap:balance;
}
h1 { font-size:32px!important; line-height:1.12!important; }
h2 { font-size:26px!important; line-height:1.18!important; }
h3 { font-size:21px!important; line-height:1.22!important; }
h4 { font-size:18px!important; line-height:1.28!important; }

[data-testid="stCaptionContainer"],
.stCaption,
small {
  color:var(--icl-muted)!important;
  line-height:1.45!important;
}

/* -------------------------------------------------------------------------- */
/* Sidebar — preserve the original ICL white-rail character                  */
/* -------------------------------------------------------------------------- */
[data-testid="stSidebar"] {
  min-width:270px!important;
  background:var(--icl-surface)!important;
  border-inline-end:1px solid var(--icl-border)!important;
}

[data-testid="stSidebar"] > div:first-child {
  padding-block-start:.4rem!important;
}

[data-testid="stSidebar"] img {
  display:block!important;
  width:auto!important;
  max-width:238px!important;
  height:auto!important;
  max-height:84px!important;
  object-fit:contain!important;
  margin:.05rem auto .08rem auto!important;
}

.icl-sidebar-product {
  margin:.16rem .1rem .55rem .1rem;
  color:var(--icl-faint);
  font-size:13px!important;
  font-weight:800;
  letter-spacing:.14em;
}

[data-testid="stSidebar"] [role="radiogroup"] {
  gap:2px!important;
}

[data-testid="stSidebar"] [role="radiogroup"] label,
[data-testid="stSidebar"] .stButton > button {
  min-height:42px!important;
  width:100%!important;
  justify-content:flex-start!important;
  text-align:start!important;
  margin:0!important;
  padding:.48rem .68rem!important;
  border:0!important;
  border-inline-start:3px solid transparent!important;
  border-radius:0!important;
  background:transparent!important;
  box-shadow:none!important;
  color:#252C37!important;
  font-size:17px!important;
  font-weight:620!important;
  letter-spacing:.01em!important;
  transition:none!important;
}

[data-testid="stSidebar"] [role="radiogroup"] label:hover,
[data-testid="stSidebar"] .stButton > button:hover {
  background:#F4F6F8!important;
  color:var(--icl-navy)!important;
}

[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked),
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
  background:#F1F4F8!important;
  border-inline-start-color:var(--icl-navy)!important;
  color:var(--icl-navy)!important;
  font-weight:700!important;
}

/* -------------------------------------------------------------------------- */
/* Shared workspace headers                                                   */
/* -------------------------------------------------------------------------- */
.icl-shell-header,
.icl-add-hero {
  width:100%!important;
  max-width:100%!important;
  box-sizing:border-box!important;
  margin:.1rem 0 1rem 0!important;
  border-radius:var(--icl-radius-md)!important;
  box-shadow:none!important;
}

.icl-shell-header {
  position:relative;
  display:flex!important;
  align-items:center!important;
  justify-content:space-between!important;
  gap:var(--icl-space-4)!important;
  min-height:72px;
  overflow:hidden;
  padding:13px 16px 13px 20px!important;
  background:var(--icl-surface)!important;
  border:1px solid var(--icl-border)!important;
}

.icl-shell-header::before {
  content:"";
  position:absolute;
  inset-block:0;
  inset-inline-start:0;
  width:4px;
  background:var(--icl-navy);
}

.icl-shell-copy {
  min-width:0;
}

.icl-shell-eyebrow,
.icl-add-kicker {
  margin:0 0 2px 0!important;
  font-size:12.5px!important;
  font-weight:800!important;
  letter-spacing:.13em!important;
  text-transform:uppercase;
}

.icl-shell-eyebrow {
  color:var(--icl-faint)!important;
}

.icl-shell-title,
.icl-add-title {
  margin:0!important;
  font-size:27px!important;
  font-weight:780!important;
  letter-spacing:-.035em!important;
  line-height:1.05!important;
}

.icl-shell-title {
  color:var(--icl-navy)!important;
}

.icl-shell-pills,
.icl-add-steps {
  display:flex;
  flex-wrap:wrap;
  align-items:center;
  justify-content:flex-end;
  gap:4px;
}

.icl-shell-pills span {
  display:inline-flex;
  align-items:center;
  min-height:24px;
  padding:.18rem .46rem;
  border:1px solid #DCE3EC;
  border-radius:999px;
  background:#F7F9FB;
  color:#536071;
  font-size:13.5px!important;
  font-weight:700;
  white-space:nowrap;
}

/* Add Chemical remains the visually distinct task surface, but uses the same
   geometry and alignment grid as the rest of the manager. */
.icl-add-hero {
  display:flex!important;
  align-items:center!important;
  justify-content:space-between!important;
  gap:var(--icl-space-4)!important;
  min-height:72px;
  padding:13px 16px 13px 20px!important;
  background:var(--icl-navy)!important;
  border:1px solid var(--icl-navy)!important;
  color:#fff!important;
}

.icl-add-kicker {
  color:#C7D2E8!important;
}

.icl-add-title {
  color:#fff!important;
}

.icl-add-steps {
  gap:6px!important;
}

.icl-add-steps span {
  display:inline-flex;
  align-items:center;
  gap:.28rem;
  min-height:27px;
  padding:.26rem .5rem;
  border:1px solid rgba(255,255,255,.25);
  border-radius:999px;
  background:rgba(255,255,255,.08);
  color:#fff;
  font-size:13.5px!important;
  font-weight:650;
  white-space:nowrap;
}

.icl-add-steps b {
  color:#C9D8FF;
  font-size:12px!important;
  letter-spacing:.04em;
}

/* -------------------------------------------------------------------------- */
/* Controls — one clear hierarchy                                             */
/* -------------------------------------------------------------------------- */
.stTextInput input,
.stNumberInput input,
.stDateInput input,
.stTextArea textarea,
[data-baseweb="select"] > div {
  min-height:44px!important;
  border:1px solid transparent!important;
  border-radius:6px!important;
  background:var(--icl-surface-muted)!important;
  box-shadow:inset 0 0 0 1px #DCE2EA!important;
}

.stTextArea textarea {
  min-height:96px!important;
}

.stTextInput input:focus,
.stNumberInput input:focus,
.stDateInput input:focus,
.stTextArea textarea:focus,
[data-baseweb="select"]:focus-within > div {
  border-color:transparent!important;
  box-shadow:inset 0 0 0 1px var(--icl-focus),0 0 0 3px rgba(47,95,183,.14)!important;
}

[data-testid="stWidgetLabel"] p {
  margin-block-end:3px!important;
  color:#3F4856!important;
  font-size:15.5px!important;
  font-weight:680!important;
}

main .stButton > button,
main .stDownloadButton > button,
main [data-testid="stFormSubmitButton"] > button,
main [data-testid="stLinkButton"] a {
  min-height:46px!important;
  padding:.58rem .95rem!important;
  border:1px solid var(--icl-border-strong)!important;
  border-radius:6px!important;
  background:#fff!important;
  box-shadow:none!important;
  color:var(--icl-navy)!important;
  font-size:17px!important;
  font-weight:700!important;
  line-height:1.2!important;
}

main .stButton > button:hover,
main .stDownloadButton > button:hover,
main [data-testid="stLinkButton"] a:hover {
  border-color:#9BAAC0!important;
  background:#F7F9FB!important;
}

main button[kind="primary"],
main button[kind="primary"] *,
main [data-testid="stFormSubmitButton"] button[kind="primary"],
main [data-testid="stFormSubmitButton"] button[kind="primary"] * {
  background:var(--icl-navy)!important;
  border-color:var(--icl-navy)!important;
  color:#fff!important;
  -webkit-text-fill-color:#fff!important;
}

main button[kind="primary"]:hover,
main button[kind="primary"]:hover * {
  background:var(--icl-navy-hover)!important;
  border-color:var(--icl-navy-hover)!important;
  color:#fff!important;
  -webkit-text-fill-color:#fff!important;
}

main button:disabled,
main button:disabled * {
  opacity:1!important;
  background:#EEF1F5!important;
  border-color:#E1E6ED!important;
  color:#8993A2!important;
  -webkit-text-fill-color:#8993A2!important;
}

main button:focus-visible,
[data-testid="stSidebar"] button:focus-visible,
main a:focus-visible,
[data-baseweb="select"]:focus-within {
  outline:2px solid var(--icl-focus)!important;
  outline-offset:2px!important;
}

/* -------------------------------------------------------------------------- */
/* Grouping and progressive disclosure                                        */
/* -------------------------------------------------------------------------- */
[data-testid="stForm"],
[data-testid="stExpander"],
[data-testid="stMetric"],
[data-testid="stDataFrame"] {
  border-color:var(--icl-border)!important;
  box-shadow:none!important;
}

[data-testid="stForm"] {
  border-radius:var(--icl-radius-md)!important;
  background:#fff!important;
}

[data-testid="stExpander"] {
  border-radius:6px!important;
  background:#FAFBFC!important;
}

[data-testid="stExpander"] summary {
  min-height:42px!important;
  font-weight:680!important;
}

[data-testid="stMetric"] {
  border-radius:6px!important;
  background:#fff!important;
}

[data-testid="stMetricValue"],
input[type="number"],
[data-testid="stDataFrame"] {
  font-variant-numeric:tabular-nums;
}

[data-testid="stAlert"] {
  border-radius:6px!important;
  box-shadow:none!important;
}

.icl-result-line {
  margin:var(--icl-space-2) 0 var(--icl-space-4) 0;
  padding:var(--icl-space-3) var(--icl-space-4);
  border:0;
  border-inline-start:4px solid var(--icl-navy);
  border-radius:3px;
  background:#F7F9FB;
}

/* -------------------------------------------------------------------------- */
/* Upload, camera and label-review surfaces                                   */
/* -------------------------------------------------------------------------- */
[data-testid="stFileUploaderDropzone"] {
  min-height:124px!important;
  padding:1.25rem 1.1rem!important;
  display:flex!important;
  align-items:center!important;
  border:1.5px dashed #94A3B8!important;
  border-radius:8px!important;
  background:#FBFCFD!important;
}

[data-testid="stFileUploaderDropzone"] > div {
  width:100%!important;
}

[data-testid="stFileUploaderDropzone"] p,
[data-testid="stFileUploaderDropzone"] span,
[data-testid="stFileUploaderDropzone"] small {
  font-size:16px!important;
  line-height:1.45!important;
}

[data-testid="stFileUploaderDropzone"] button {
  min-height:46px!important;
  padding:.56rem .9rem!important;
  font-size:17px!important;
  font-weight:700!important;
}

[data-testid="stCameraInput"] {
  padding:.42rem!important;
  border:1px solid var(--icl-border)!important;
  border-radius:8px!important;
  background:#fff!important;
}

[data-testid="stCameraInput"] button {
  min-height:48px!important;
  font-size:17px!important;
  font-weight:720!important;
}

main [data-testid="stImage"] img {
  height:auto!important;
  max-width:100%!important;
  border:1px solid #E3E8EF;
  border-radius:6px;
  background:#fff;
}

[data-baseweb="tab-list"] {
  gap:var(--icl-space-2)!important;
}

[data-baseweb="tab"] {
  min-height:44px!important;
  font-weight:700!important;
}

/* -------------------------------------------------------------------------- */
/* Inventory / scan / dialogs                                                 */
/* -------------------------------------------------------------------------- */
.icl-scan-hero {
  margin:.1rem 0 var(--icl-space-4) 0;
  padding:var(--icl-space-4) var(--icl-space-5);
  border:1px solid var(--icl-border);
  border-inline-start:4px solid var(--icl-navy);
  border-radius:var(--icl-radius-md);
  background:#fff;
}

.icl-scan-kicker {
  color:var(--icl-accent);
  font-size:12.5px!important;
  font-weight:800;
  letter-spacing:.13em;
  text-transform:uppercase;
}

.icl-scan-title {
  margin-block-start:2px;
  color:var(--icl-navy);
  font-size:25px!important;
  font-weight:760;
  letter-spacing:-.025em;
}

.icl-scan-flow,
.icl-scan-note-row {
  display:flex;
  flex-wrap:wrap;
  align-items:center;
}

.icl-scan-flow {
  gap:var(--icl-space-2);
  margin:.55rem 0 .45rem 0;
}

.icl-scan-flow span,
.icl-scan-note-row span {
  display:inline-flex;
  align-items:center;
  border-radius:999px;
  white-space:nowrap;
}

.icl-scan-flow span {
  gap:.32rem;
  padding:.28rem .55rem;
  border:1px solid #DCE3EC;
  background:#fff;
  color:#344054;
  font-size:14.5px!important;
  font-weight:650;
}

.icl-scan-flow span b {
  display:inline-grid;
  width:1.25rem;
  height:1.25rem;
  place-items:center;
  border-radius:50%;
  background:var(--icl-navy);
  color:#fff;
  font-size:12.5px!important;
}

.icl-scan-flow i {
  color:#98A2B3;
  font-style:normal;
}

.icl-scan-note-row {
  gap:6px;
}

.icl-scan-note-row span {
  min-height:24px;
  padding:.2rem .48rem;
  background:#F2F4F7;
  color:#667085;
  font-size:13px!important;
  font-weight:650;
}

[data-testid="stDataFrame"] {
  width:100%!important;
  max-width:100%!important;
  overflow-x:auto!important;
  border-radius:6px!important;
}

[data-testid="stDialog"] [role="dialog"] {
  border:1px solid #DCE2EA!important;
  border-radius:8px!important;
  box-shadow:0 22px 72px rgba(1,25,76,.16)!important;
}

/* -------------------------------------------------------------------------- */
/* Responsive + resilience                                                    */
/* -------------------------------------------------------------------------- */
@media (max-width:800px) {
  [data-testid="stSidebar"] {
    min-width:unset!important;
  }

  .block-container {
    max-width:100%!important;
    padding-block-start:.65rem!important;
    padding-inline:max(.75rem,env(safe-area-inset-left))!important;
  }

  .icl-shell-header,
  .icl-add-hero {
    min-height:unset!important;
    align-items:flex-start!important;
    flex-direction:column!important;
    padding:12px 14px 12px 18px!important;
    gap:10px!important;
  }

  .icl-shell-title,
  .icl-add-title {
    font-size:24px!important;
  }

  .icl-shell-pills {
    display:none!important;
  }

  .icl-add-steps {
    justify-content:flex-start!important;
    gap:5px!important;
  }

  .icl-add-steps span {
    font-size:13px!important;
  }

  h1 { font-size:28px!important; }
  h2 { font-size:23px!important; }
  h3 { font-size:19px!important; }

  [data-testid="stFileUploaderDropzone"] {
    min-height:112px!important;
    padding:1rem .85rem!important;
  }

  [data-testid="stCameraInput"] {
    padding:.25rem!important;
  }

  [data-testid="stCameraInput"] button,
  main .stButton > button,
  main .stDownloadButton > button,
  main [data-testid="stFormSubmitButton"] > button {
    min-height:46px!important;
    font-size:16.5px!important;
  }

  .icl-scan-hero {
    padding:14px 16px;
  }

  .icl-scan-title {
    font-size:22px!important;
  }

  .icl-scan-flow i {
    display:none;
  }

  [data-testid="stDataFrame"] {
    min-height:280px;
  }

  [data-testid="stDialog"] [role="dialog"] {
    width:calc(100vw - 1rem)!important;
    max-width:calc(100vw - 1rem)!important;
    margin:.5rem!important;
  }
}

@media (prefers-reduced-motion:reduce) {
  *,*::before,*::after {
    scroll-behavior:auto!important;
    animation-duration:.01ms!important;
    animation-iteration-count:1!important;
    transition-duration:.01ms!important;
  }
}


/* ICL sidebar v2 — compact navigation rail */
[data-testid="stSidebar"] {
  border-inline-end:1px solid #E5E9F0!important;
}

[data-testid="stSidebarUserContent"] {
  padding:14px 16px 18px 16px!important;
}

[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
  gap:.28rem!important;
}

[data-testid="stSidebar"] img {
  max-width:190px!important;
  max-height:66px!important;
  margin:0 auto .18rem auto!important;
}

.icl-sidebar-product {
  margin:.12rem 0 .45rem 0!important;
  padding-bottom:.52rem!important;
  border-bottom:1px solid #E8ECF2;
  font-size:11.5px!important;
  letter-spacing:.16em!important;
}

[data-testid="stSidebar"] hr {
  margin:.48rem 0!important;
  border-color:#E8ECF2!important;
}

[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
  font-size:12.5px!important;
  line-height:1.3!important;
  color:#8A94A3!important;
}

[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stMarkdown a {
  line-height:1.35!important;
}

[data-testid="stSidebar"] .stButton {
  margin:0!important;
}

[data-testid="stSidebar"] .stButton > button {
  min-height:36px!important;
  padding:.34rem .5rem!important;
  border:0!important;
  border-inline-start:2px solid transparent!important;
  border-radius:4px!important;
  background:transparent!important;
  color:#303846!important;
  box-shadow:none!important;
  font-size:15.5px!important;
  font-weight:590!important;
  letter-spacing:0!important;
}

[data-testid="stSidebar"] .stButton > button p {
  width:100%!important;
  margin:0!important;
  text-align:left!important;
  font-size:15.5px!important;
  line-height:1.2!important;
}

[data-testid="stSidebar"] .stButton > button:hover {
  background:#F6F8FA!important;
  color:var(--icl-navy)!important;
}

[data-testid="stSidebar"] .stButton > button[kind="primary"] {
  background:transparent!important;
  border-inline-start-color:var(--icl-navy)!important;
  color:var(--icl-navy)!important;
  font-weight:720!important;
}

[data-testid="stSidebar"] .stButton > button[kind="primary"] p {
  color:var(--icl-navy)!important;
  font-weight:720!important;
}

[data-testid="stSidebar"] button:focus-visible {
  outline:2px solid var(--icl-focus)!important;
  outline-offset:1px!important;
}

@media (max-width:800px) {
  [data-testid="stSidebarUserContent"] {
    padding:12px 14px 16px 14px!important;
  }

  [data-testid="stSidebar"] img {
    max-width:176px!important;
    max-height:61px!important;
  }

  [data-testid="stSidebar"] .stButton > button,
  [data-testid="stSidebar"] .stButton > button p {
    font-size:15px!important;
  }
}


/* ICL sidebar v3 — account + nested navigation hierarchy */
[data-testid="stSidebar"] hr {
  margin:.72rem 0!important;
  border:0!important;
  border-top:1px solid #D2D9E4!important;
  opacity:1!important;
}

.icl-account-label {
  margin:0 0 .24rem 0;
  color:#8A94A3;
  font-size:11px!important;
  font-weight:800;
  letter-spacing:.15em;
}

[data-testid="stSidebar"] .st-key-icl_logout {
  margin:.38rem 0 .05rem 0!important;
}

[data-testid="stSidebar"] .st-key-icl_logout button {
  min-height:32px!important;
  width:100%!important;
  justify-content:center!important;
  padding:.25rem .55rem!important;
  border:1px solid #D5DCE6!important;
  border-radius:5px!important;
  background:#FFFFFF!important;
  box-shadow:none!important;
  color:#515B6A!important;
  font-size:13.5px!important;
  font-weight:650!important;
}

[data-testid="stSidebar"] .st-key-icl_logout button p {
  width:auto!important;
  text-align:center!important;
  color:#515B6A!important;
  font-size:13.5px!important;
  font-weight:650!important;
}

[data-testid="stSidebar"] .st-key-icl_logout button:hover {
  border-color:#9CA9BC!important;
  background:#F8FAFC!important;
  color:var(--icl-navy)!important;
}

[data-testid="stSidebar"] .st-key-icl_nav_root_inventory button[kind="primary"],
[data-testid="stSidebar"] .st-key-icl_nav_root_excel button[kind="primary"] {
  background:transparent!important;
  border-inline-start-color:transparent!important;
  color:var(--icl-navy)!important;
  font-weight:730!important;
}

[data-testid="stSidebar"] .st-key-icl_nav_root_inventory button[kind="primary"] p,
[data-testid="stSidebar"] .st-key-icl_nav_root_excel button[kind="primary"] p {
  color:var(--icl-navy)!important;
  font-weight:730!important;
}

[data-testid="stSidebar"] .st-key-icl_nav_root_inventory button[kind="primary"] p::after,
[data-testid="stSidebar"] .st-key-icl_nav_root_excel button[kind="primary"] p::after {
  content:"⌄";
  float:right;
  margin-left:.5rem;
  color:#8C97A7;
  font-size:12px;
  font-weight:700;
}

[data-testid="stSidebar"] .st-key-icl_nav_inventory_chemicals,
[data-testid="stSidebar"] .st-key-icl_nav_inventory_scan,
[data-testid="stSidebar"] .st-key-icl_nav_excel_import,
[data-testid="stSidebar"] .st-key-icl_nav_excel_export {
  width:calc(100% - 18px)!important;
  margin-left:18px!important;
  padding-left:9px!important;
  border-left:1px solid #D9E0E9!important;
}

[data-testid="stSidebar"] .st-key-icl_nav_inventory_chemicals button,
[data-testid="stSidebar"] .st-key-icl_nav_inventory_scan button,
[data-testid="stSidebar"] .st-key-icl_nav_excel_import button,
[data-testid="stSidebar"] .st-key-icl_nav_excel_export button {
  min-height:30px!important;
  padding:.2rem .46rem!important;
  border-inline-start:0!important;
  border-radius:4px!important;
  background:transparent!important;
  color:#667085!important;
  font-size:14.25px!important;
  font-weight:560!important;
}

[data-testid="stSidebar"] .st-key-icl_nav_inventory_chemicals button p,
[data-testid="stSidebar"] .st-key-icl_nav_inventory_scan button p,
[data-testid="stSidebar"] .st-key-icl_nav_excel_import button p,
[data-testid="stSidebar"] .st-key-icl_nav_excel_export button p {
  color:inherit!important;
  font-size:14.25px!important;
  font-weight:inherit!important;
}

[data-testid="stSidebar"] .st-key-icl_nav_inventory_chemicals button:hover,
[data-testid="stSidebar"] .st-key-icl_nav_inventory_scan button:hover,
[data-testid="stSidebar"] .st-key-icl_nav_excel_import button:hover,
[data-testid="stSidebar"] .st-key-icl_nav_excel_export button:hover {
  background:#F7F9FB!important;
  color:var(--icl-navy)!important;
}

[data-testid="stSidebar"] .st-key-icl_nav_inventory_chemicals button[kind="primary"],
[data-testid="stSidebar"] .st-key-icl_nav_inventory_scan button[kind="primary"],
[data-testid="stSidebar"] .st-key-icl_nav_excel_import button[kind="primary"],
[data-testid="stSidebar"] .st-key-icl_nav_excel_export button[kind="primary"] {
  background:#F1F4F8!important;
  color:var(--icl-navy)!important;
  font-weight:720!important;
}

@media (max-width:800px) {
  [data-testid="stSidebar"] .st-key-icl_nav_inventory_chemicals,
  [data-testid="stSidebar"] .st-key-icl_nav_inventory_scan,
  [data-testid="stSidebar"] .st-key-icl_nav_excel_import,
  [data-testid="stSidebar"] .st-key-icl_nav_excel_export {
    width:calc(100% - 16px)!important;
    margin-left:16px!important;
    padding-left:8px!important;
  }
}

/* ICL sidebar v4 — signed-in block spacing */
.icl-account-label {
  display:block!important;
  min-height:18px!important;
  margin:0!important;
  padding:0 0 7px 0!important;
  line-height:1.35!important;
}

[data-testid="stSidebar"] .stMarkdown:has(.icl-account-label),
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"]:has(.icl-account-label) {
  min-height:25px!important;
  margin:0!important;
  padding:0!important;
}

[data-testid="stSidebar"] .st-key-icl_logout {
  margin:.62rem 0 .10rem 0!important;
}

</style>''',
    unsafe_allow_html=True,
)
st.markdown(
    "<style>" + '\n/* ICL UX v3.9 compact desktop layout */\n@media (min-width: 1101px) {\n  .block-container {\n    max-width: 820px !important;\n    margin-left: auto !important;\n    margin-right: auto !important;\n  }\n}\n\n/* The global Chemical Manager header and Add Chemical hero use the exact same\n   content width. The older shell intentionally used inline-flex/auto width,\n   which made it look shorter than the full-width Add Chemical card. */\n.icl-shell-header,\n.icl-add-hero {\n  width: 100% !important;\n  max-width: 100% !important;\n  box-sizing: border-box !important;\n}\n.icl-shell-header {\n  display: flex !important;\n  justify-content: space-between !important;\n}\n.icl-shell-pills {\n  margin-left: auto !important;\n}\n\n/* Defensive cap for Streamlit image DOM variants. Explicit st.image widths below\n   are the primary sizing mechanism; this is only a browser-side safeguard. */\nmain img {\n  max-width: 280px;\n  height: auto;\n}\n\n@media (max-width: 800px) {\n  .block-container { max-width: 100% !important; }\n  main img { max-width: min(280px, 100%); }\n  .icl-shell-header,\n  .icl-add-hero { width: 100% !important; }\n}\n' + "</style>",
    unsafe_allow_html=True,
)

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


class _RawConnectionPool:
    """Small thread-safe PostgreSQL connection pool for Streamlit reruns.

    Streamlit reruns the script for most widget interactions. The old app opened
    a brand-new TLS/PostgreSQL connection every time get_connection() was called,
    often several times during one rerun. Reusing a handful of live connections
    removes that handshake from normal clicks.
    """

    def __init__(self, max_size=6):
        self.max_size = int(max(1, max_size))
        self._available = queue.LifoQueue(maxsize=self.max_size)
        self._lock = threading.Lock()
        self._created = 0

    def _create(self):
        return psycopg.connect(
            _database_url(),
            row_factory=dict_row,
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=3,
        )

    def acquire(self, timeout=10):
        while True:
            try:
                raw = self._available.get_nowait()
            except queue.Empty:
                create_new = False
                with self._lock:
                    if self._created < self.max_size:
                        self._created += 1
                        create_new = True

                if create_new:
                    try:
                        return self._create()
                    except Exception:
                        with self._lock:
                            self._created = max(0, self._created - 1)
                        raise

                raw = self._available.get(timeout=timeout)

            if raw is None:
                continue

            if getattr(raw, "closed", False):
                with self._lock:
                    self._created = max(0, self._created - 1)
                continue

            return raw

    def release(self, raw):
        if raw is None:
            return

        if getattr(raw, "closed", False):
            with self._lock:
                self._created = max(0, self._created - 1)
            return

        # SELECT statements also open a transaction with psycopg's default
        # transaction mode. Reset before another session/request reuses it.
        try:
            raw.rollback()
        except Exception:
            try:
                raw.close()
            except Exception:
                pass
            with self._lock:
                self._created = max(0, self._created - 1)
            return

        try:
            self._available.put_nowait(raw)
        except queue.Full:
            try:
                raw.close()
            finally:
                with self._lock:
                    self._created = max(0, self._created - 1)


@st.cache_resource(show_spinner=False)
def _get_db_connection_pool():
    # Six connections is enough for the Streamlit UI plus a few concurrent lab
    # users without creating a large persistent Supabase connection footprint.
    return _RawConnectionPool(max_size=6)


class CompatConnection:
    def __init__(self, raw_conn, pool=None):
        self._conn = raw_conn
        self._pool = pool
        self._returned = False

    def execute(self, sql, params=None):
        cur = CompatCursor(self._conn.cursor(row_factory=dict_row))
        return cur.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._returned:
            return
        self._returned = True

        if self._pool is None:
            self._conn.close()
        else:
            self._pool.release(self._conn)


def get_connection():
    pool = _get_db_connection_pool()
    raw = pool.acquire()
    return CompatConnection(raw, pool=pool)


@st.cache_resource(show_spinner=False)
def init_db():
    """Initialize the Supabase schema once per app process.

    A PostgreSQL transaction-level advisory lock serializes schema initialization
    so simultaneous Streamlit sessions cannot deadlock while creating indexes.
    """
    conn = get_connection()

    # Prevent concurrent app sessions from running DDL/index creation together.
    conn.execute("SELECT pg_advisory_xact_lock(742013)")

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
        CREATE TABLE IF NOT EXISTS inventory_observations (
            id BIGSERIAL PRIMARY KEY,
            chemical_id BIGINT NOT NULL REFERENCES chemicals(id) ON DELETE CASCADE,
            original_file_name TEXT,
            original_image_data BYTEA,
            label_image_data BYTEA,
            ocr_result_json TEXT,
            observed_storage_unit_id BIGINT REFERENCES storage_units(id),
            observed_shelf_number INTEGER,
            captured_at TEXT NOT NULL,
            uploaded_by TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_inventory_observations_chemical
        ON inventory_observations(chemical_id, id DESC)
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
    return True


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


@st.cache_data(ttl=5, show_spinner=False)
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

@st.dialog("✏️ Edit Chemical", width="large")
def edit_chemical_dialog(chemical_id):
    conn = get_connection()
    chemical = conn.execute(
        "SELECT * FROM chemicals WHERE id=?",
        (chemical_id,),
    ).fetchone()
    conn.close()

    if not chemical:
        st.error("Chemical not found.")
        return

    st.markdown(f"### {chemical['chemical_name']}")
    st.caption(
        f"Bottle ID: {chemical['bottle_id'] or '-'}   |   "
        f"Legacy ID: {chemical['legacy_id'] or '-'}"
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
    current_status = chemical["status"] or "Unopened"
    if current_status not in status_options:
        status_options.append(current_status)

    with st.form(f"edit_chemical_form_{chemical_id}", enter_to_submit=False):
        edit_name = st.text_input(
            "Chemical Name *",
            value=chemical["chemical_name"] or "",
            key=f"edit_name_{chemical_id}",
        )
        edit_description = st.text_input(
            "Description / Grade",
            value=chemical["description"] or "",
            key=f"edit_description_{chemical_id}",
        )

        col1, col2 = st.columns(2)

        with col1:
            edit_cas = st.text_input(
                "CAS No.",
                value=chemical["cas_number"] or "",
                key=f"edit_cas_{chemical_id}",
            )
            edit_manufacturer = st.text_input(
                "Manufacturer",
                value=chemical["manufacturer"] or "",
                key=f"edit_manufacturer_{chemical_id}",
            )
            edit_catalog = st.text_input(
                "Catalog No.",
                value=chemical["catalog_number"] or "",
                key=f"edit_catalog_{chemical_id}",
            )
            edit_lot = st.text_input(
                "Lot No.",
                value=chemical["lot_number"] or "",
                key=f"edit_lot_{chemical_id}",
            )
            edit_initial = st.number_input(
                "Initial Amount",
                min_value=0.0,
                value=float(
                    chemical["initial_amount"]
                    if chemical["initial_amount"] is not None
                    else chemical["amount"] or 0
                ),
                key=f"edit_initial_{chemical_id}",
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
                key=f"edit_remaining_{chemical_id}",
            )
            edit_unit = st.selectbox(
                "Unit",
                unit_options,
                index=unit_options.index(current_unit),
                key=f"edit_unit_{chemical_id}",
            )
            edit_storage = st.selectbox(
                "Storage Location",
                storage_options,
                index=storage_index,
                format_func=lambda option: option[2],
                key=f"edit_storage_{chemical_id}",
            )
            edit_owner = st.text_input(
                "Owner / Person in Charge",
                value=chemical["owner"] or "",
                key=f"edit_owner_{chemical_id}",
            )
            edit_expiration = st.text_input(
                "Expiration Date",
                value=chemical["expiration_date"] or "",
                placeholder="YYYY-MM-DD",
                key=f"edit_expiration_{chemical_id}",
            )

        edit_status = st.selectbox(
            "Status",
            status_options,
            index=status_options.index(current_status),
            key=f"edit_status_{chemical_id}",
        )

        save_col, cancel_col = st.columns(2)
        with save_col:
            save_edit = st.form_submit_button(
                "Save Changes",
                type="primary",
                width="stretch",
            )
        with cancel_col:
            cancel_edit = st.form_submit_button(
                "Cancel",
                width="stretch",
            )

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
                    edit_storage[2] if edit_storage[0] is not None else "",
                    edit_owner.strip(),
                    edit_expiration.strip(),
                    edit_status,
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                    chemical_id,
                ),
            )
            write_audit_log(
                "EDIT_CHEMICAL",
                target_type="chemical",
                target_id=chemical_id,
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
            set_notice("✅ Chemical information has been updated.")
            st.rerun()

    if cancel_edit:
        st.rerun()

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
# OCR WORKER / INVENTORY OBSERVATIONS
# ============================================================


def _ocr_secret(name):
    """
    App-wide OCR backend configuration.

    Production policy:
    - OCR_API_URL and OCR_API_KEY live in Streamlit Secrets (or env vars).
    - They are shared by every logged-in user of this Streamlit deployment.
    - End users never enter backend credentials.
    """
    env_value = os.getenv(name, "").strip()
    if env_value:
        return env_value

    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def get_ocr_api_url():
    # The old OCR_API_URL may still point to v7. v8 uses this known deployment.
    return (_ocr_secret("OCR_V8_API_URL") or "https://eddiekim995--icl-reagent-ocr-v8-ocrservice-web.modal.run").rstrip("/")



def get_ocr_api_key():
    if _ocr_user_is_admin():
        temporary = str(st.session_state.get("icl_v8_temporary_api_key", "")).strip()
        if temporary:
            return temporary
    return _ocr_secret("OCR_API_KEY")



def ocr_backend_configured():
    return bool(
        get_ocr_api_url()
        and get_ocr_api_key()
    )


def _ocr_user_is_admin():
    current = globals().get("CURRENT_USER") or {}
    return str(
        current.get("role") or ""
    ).strip().lower() == "admin"


def _ocr_backend_summary(info):
    if not isinstance(info, dict):
        return ""

    parts = []

    backend = clean_text(info.get("backend"))
    if backend:
        parts.append(backend)

    device = clean_text(
        info.get("device")
        or info.get("gpu")
    )
    if device:
        parts.append(device)

    engine_patch = clean_text(
        info.get("engine_patch")
    )
    if engine_patch:
        parts.append(engine_patch)

    inventory_count = info.get(
        "inventory_count"
    )
    if inventory_count is not None:
        try:
            parts.append(
                f"{int(inventory_count):,} identities"
            )
        except Exception:
            pass

    return " · ".join(parts)


def render_ocr_backend_panel(
    key_prefix="ocr",
    expanded=False,
):
    """
    Compact Modal GPU status UI.

    /health is intentionally NOT called on every page render because a health
    request would wake a scale-to-zero Modal GPU just to draw the page.
    The normal inventory/OCR request verifies the backend when it is needed.
    Admins can explicitly run diagnostics.
    """
    panel_col, _panel_spacer = st.columns(
        [1.2, 2.0]
    )

    with panel_col:
        if not ocr_backend_configured():
            st.error(
                "☁️ Modal GPU OCR is not configured."
            )
            st.caption(
                "Admin: add OCR_API_URL and OCR_API_KEY "
                "to Streamlit Secrets."
            )
            return

        last_info = (
            st.session_state.get(
                "ocr_last_server_info"
            )
            or st.session_state.get(
                "ocr_last_health"
            )
            or {}
        )

        st.success(
            "☁️ Modal GPU OCR ready"
        )

        summary = _ocr_backend_summary(
            last_info
        )

        if summary:
            st.caption(summary)
        else:
            st.caption(
                "Serverless GPU backend · "
                "starts automatically when OCR is requested"
            )

        if _ocr_user_is_admin():
            with st.expander(
                "Admin diagnostics",
                expanded=expanded,
            ):
                endpoint = get_ocr_api_url()

                st.caption(
                    f"Endpoint: {endpoint}"
                )
                st.caption(
                    "API key: configured in Streamlit Secrets"
                )

                if st.button(
                    "Check Modal backend",
                    key=f"{key_prefix}_admin_health",
                    width="stretch",
                ):
                    try:
                        response = requests.get(
                            endpoint + "/health",
                            timeout=120,
                        )
                        response.raise_for_status()
                        info = response.json()

                    except Exception as exc:
                        st.session_state.pop(
                            "ocr_last_health",
                            None,
                        )
                        st.error(
                            f"Modal backend check failed: {exc}"
                        )

                    else:
                        st.session_state[
                            "ocr_last_health"
                        ] = info
                        st.success(
                            "Modal backend online"
                        )
                        st.json(info)


def _current_inventory_for_ocr():
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT
            c.chemical_name AS "Chemical Name",
            c.description AS "Description",
            c.cas_number AS "CAS No.",
            c.manufacturer AS "Manufacturer",
            c.catalog_number AS "Catalog No.",
            COALESCE(c.remaining_amount, c.amount) AS "Amount",
            c.unit AS "Unit",
            c.molecular_formula AS "Molecular Formula",
            c.molecular_weight AS "Molecular Weight",
            {LOCATION_EXPR} AS "Storage"
        FROM chemicals c
        LEFT JOIN storage_units su ON c.storage_unit_id = su.id
        WHERE c.status != 'Disposed'
        ORDER BY c.id
        """
    ).fetchall()
    conn.close()
    return pd.DataFrame([dict(row) for row in rows])


def sync_inventory_to_ocr_worker(force=False):
    url = get_ocr_api_url()
    key = get_ocr_api_key()
    if not url or not key:
        raise RuntimeError("Modal OCR is not configured. Admin must set OCR_API_URL and OCR_API_KEY in Streamlit Secrets.")

    df = _current_inventory_for_ocr()
    csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
    signature = hashlib.sha1(csv_bytes).hexdigest()
    cache_key = f"{url}::{signature}"

    if (
        not force
        and st.session_state.get("ocr_inventory_sync_signature") == cache_key
    ):
        return {
            "ok": True,
            "cached": True,
            "inventory_count": st.session_state.get("ocr_inventory_sync_count", len(df)),
        }

    response = requests.post(
        url + "/inventory",
        headers={"X-API-Key": key},
        files={
            "file": (
                "chemical_manager_live_inventory.csv",
                csv_bytes,
                "text/csv",
            )
        },
        timeout=120,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Inventory sync {response.status_code}: {response.text}")

    payload = response.json()
    st.session_state["ocr_last_inventory_sync"] = payload

    if isinstance(payload, dict):
        previous_server_info = (
            st.session_state.get(
                "ocr_last_server_info"
            )
            or {}
        )
        st.session_state[
            "ocr_last_server_info"
        ] = {
            **previous_server_info,
            "backend": payload.get(
                "backend",
                previous_server_info.get(
                    "backend",
                    "Modal",
                ),
            ),
            "inventory_count": payload.get(
                "inventory_count",
                previous_server_info.get(
                    "inventory_count"
                ),
            ),
        }

    st.session_state["ocr_inventory_sync_signature"] = cache_key
    st.session_state["ocr_inventory_sync_count"] = payload.get(
        "inventory_count", len(df)
    )
    return payload


def _ocr_api_request_bytes(
    filename,
    data,
    mime_type,
    enable_pubchem=True,
    extract_only=False,
):
    """
    Thread-safe OCR HTTP request.

    This helper does not touch Streamlit session_state, so it can safely run
    inside the bulk ThreadPoolExecutor.
    """
    url = get_ocr_api_url()
    key = get_ocr_api_key()

    if not url or not key:
        raise RuntimeError(
            "Modal OCR is not configured. Admin must set "
            "OCR_API_URL and OCR_API_KEY in Streamlit Secrets."
        )

    response = requests.post(
        url + "/ocr",
        headers={
            "X-API-Key": key
        },
        files={
            "file": (
                filename,
                data,
                mime_type
                or "application/octet-stream",
            )
        },
        data={
            "enable_pubchem":
                str(
                    bool(
                        enable_pubchem
                    )
                ).lower(),
            "extract_only":
                str(
                    bool(
                        extract_only
                    )
                ).lower(),
        },
        timeout=60,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"OCR API {response.status_code}: "
            f"{response.text}"
        )

    return response.json()


def analyze_label_with_ocr(
    uploaded_file,
    enable_pubchem,
):
    payload = _ocr_api_request_bytes(
        uploaded_file.name,
        uploaded_file.getvalue(),
        uploaded_file.type
        or "application/octet-stream",
        enable_pubchem=enable_pubchem,
    )

    if isinstance(
        payload,
        dict,
    ):
        server_info = payload.get(
            "server"
        )

        if isinstance(
            server_info,
            dict,
        ):
            st.session_state[
                "ocr_last_server_info"
            ] = server_info

    return payload



class MemoryUploadedImage:
    """Small UploadedFile-compatible wrapper for images extracted from ZIP."""
    def __init__(self, name, data, mime_type="image/jpeg"):
        self.name = name
        self._data = data
        self.type = mime_type

    def getvalue(self):
        return self._data


def _guess_image_mime(filename):
    ext = str(filename or "").lower().rsplit(".", 1)[-1]
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }.get(ext, "application/octet-stream")


def expand_inventory_scan_uploads(uploaded_files, max_images=120):
    """
    Accept direct images and/or ZIP files.
    ZIP safety:
    - image extensions only
    - ignore __MACOSX / hidden files
    - no extraction to disk
    - per-file and total uncompressed size limits
    - SHA1 duplicate removal
    """
    image_exts = {".jpg", ".jpeg", ".png", ".webp"}
    max_single = 30 * 1024 * 1024
    max_zip_total = 500 * 1024 * 1024

    items = []
    seen_hashes = set()
    warnings = []

    def add_item(name, data, source):
        if len(items) >= max_images:
            return
        if not data:
            return
        if len(data) > max_single:
            warnings.append(f"{name}: skipped (>30 MB)")
            return
        digest = hashlib.sha1(data).hexdigest()
        if digest in seen_hashes:
            warnings.append(f"{name}: duplicate image skipped")
            return
        seen_hashes.add(digest)
        items.append(
            {
                "scan_id": digest[:14],
                "filename": Path(name).name,
                "bytes": data,
                "mime": _guess_image_mime(name),
                "source": source,
            }
        )

    for uploaded in uploaded_files or []:
        name = uploaded.name
        suffix = Path(name).suffix.lower()
        raw = uploaded.getvalue()

        if suffix in image_exts:
            add_item(name, raw, "direct")
            continue

        if suffix != ".zip":
            warnings.append(f"{name}: unsupported file skipped")
            continue

        try:
            with zipfile.ZipFile(BytesIO(raw)) as zf:
                infos = [
                    info
                    for info in zf.infolist()
                    if not info.is_dir()
                ]

                total_uncompressed = sum(
                    max(0, int(info.file_size or 0))
                    for info in infos
                )
                if total_uncompressed > max_zip_total:
                    warnings.append(
                        f"{name}: ZIP skipped (uncompressed size >500 MB)"
                    )
                    continue

                for info in infos:
                    inner = info.filename.replace("\\", "/")
                    parts = [part for part in inner.split("/") if part]

                    if (
                        not parts
                        or "__MACOSX" in parts
                        or any(part.startswith(".") for part in parts)
                    ):
                        continue

                    if Path(inner).suffix.lower() not in image_exts:
                        continue

                    if info.file_size > max_single:
                        warnings.append(
                            f"{inner}: skipped (>30 MB)"
                        )
                        continue

                    try:
                        data = zf.read(info)
                    except Exception:
                        warnings.append(
                            f"{inner}: could not read from ZIP"
                        )
                        continue

                    add_item(inner, data, f"zip:{name}")

                    if len(items) >= max_images:
                        warnings.append(
                            f"Image limit reached ({max_images}). Remaining files skipped."
                        )
                        break
        except zipfile.BadZipFile:
            warnings.append(f"{name}: invalid ZIP")

        if len(items) >= max_images:
            break

    return items, warnings


def analyze_scan_memory_item(item, enable_pubchem=False):
    uploaded = MemoryUploadedImage(
        item["filename"],
        item["bytes"],
        item["mime"],
    )
    return analyze_label_with_ocr(
        uploaded,
        enable_pubchem=enable_pubchem,
    )



LABEL_VENDOR_ALIASES = {
    "TCI": (
        "TCI",
        "TOKYO CHEMICAL INDUSTRY",
        "TOKYOCHEMICALINDUSTRY",
    ),
    "Sigma-Aldrich": (
        "SIGMA-ALDRICH",
        "SIGMA ALDRICH",
        "SIGMAALDRICH",
        "ALDRICH",
        "SIGMA",
    ),
    "Acros Organics": (
        "ACROS ORGANICS",
        "ACROSORGANICS",
        "ACROS",
    ),
    "Alfa Aesar": (
        "ALFA AESAR",
        "ALFAAESAR",
    ),
    "Thermo Scientific": (
        "THERMO SCIENTIFIC",
        "THERMOSCIENTIFIC",
        "THERMO",
    ),
    "Fisher Scientific": (
        "FISHER SCIENTIFIC",
        "FISHERSCIENTIFIC",
        "FISHER",
    ),
    "Merck": ("MERCK",),
    "Junsei": ("JUNSEI",),
    "DAEJUNG": ("DAEJUNG",),
    "SAMCHUN": ("SAMCHUN",),
}


ADD_CHEMICAL_MANUFACTURERS = [
    "",
    "TCI",
    "Sigma-Aldrich",
    "Acros Organics",
    "Alfa Aesar",
    "Thermo Scientific",
    "Fisher Scientific",
    "Merck",
    "Junsei",
    "DAEJUNG",
    "SAMCHUN",
]

OTHER_MANUFACTURER_OPTION = "Other / Manual input"


def sync_add_manufacturer_widget():
    """Sync OCR-prefilled manufacturer into the selector/manual widgets."""
    raw_value = clean_text(
        st.session_state.get(
            "add_manufacturer",
            "",
        )
    )
    canonical = canonical_label_vendor(
        raw_value
    )

    if canonical in ADD_CHEMICAL_MANUFACTURERS:
        st.session_state[
            "add_manufacturer_choice"
        ] = canonical
        st.session_state[
            "add_manufacturer_manual"
        ] = ""
    elif raw_value:
        st.session_state[
            "add_manufacturer_choice"
        ] = OTHER_MANUFACTURER_OPTION
        st.session_state[
            "add_manufacturer_manual"
        ] = raw_value
    else:
        st.session_state[
            "add_manufacturer_choice"
        ] = ""
        st.session_state[
            "add_manufacturer_manual"
        ] = ""



def normalize_vendor_key(value):
    return re.sub(
        r"[^A-Z0-9]+",
        "",
        str(value or "").upper(),
    )


def canonical_label_vendor(value):
    raw = clean_text(value)
    if not raw:
        return ""

    normalized = normalize_vendor_key(raw)

    for display, aliases in LABEL_VENDOR_ALIASES.items():
        for alias in aliases:
            alias_norm = normalize_vendor_key(alias)
            if (
                normalized == alias_norm
                or alias_norm in normalized
            ):
                return display

    return raw


def detect_label_vendor_from_result(result):
    """
    Manufacturer of the CURRENT PHYSICAL BOTTLE.

    OCR label evidence wins over DB/PubChem candidate metadata.
    """
    metadata = result.get("metadata") or {}

    metadata_vendor = canonical_label_vendor(
        metadata.get("manufacturer")
    )
    if metadata_vendor:
        return metadata_vendor

    evidence_parts = [
        str(result.get("full_ocr_text") or ""),
    ]

    for query in result.get("ocr_name_queries") or []:
        evidence_parts.append(
            str(query.get("text") or "")
        )

    normalized = normalize_vendor_key(
        " ".join(evidence_parts)
    )

    # TCI is intentionally checked with the same alias table.
    for display, aliases in LABEL_VENDOR_ALIASES.items():
        for alias in aliases:
            alias_norm = normalize_vendor_key(alias)
            if alias_norm and alias_norm in normalized:
                return display

    return ""


def choose_label_catalog(result, manufacturer=""):
    """
    Catalog number printed on the CURRENT bottle.

    For TCI, prefer one-letter + four-digit codes.
    """
    metadata = result.get("metadata") or {}

    if bool(
        metadata.get(
            "catalog_ambiguous"
        )
    ):
        # v18: safer to leave the registration field blank than silently save
        # 185030 when independent OCR passes disagree with 185930.
        return ""

    catalogs = [
        clean_text(value)
        for value in (
            metadata.get("catalog_candidates") or []
        )
        if clean_text(value)
    ]

    if not catalogs:
        return ""

    maker = canonical_label_vendor(manufacturer)

    if maker == "TCI":
        # First: already-correct TCI pattern.
        for value in catalogs:
            compact = re.sub(
                r"[^A-Za-z0-9]",
                "",
                value,
            ).upper()

            if re.fullmatch(
                r"[A-Z]\d{4}",
                compact,
            ):
                return compact

        # Defensive repair for common OCR confusion in the first character:
        # B->8, S->5, O->0, I->1.
        first_char_repairs = {
            "8": "B",
            "5": "S",
            "0": "O",
            "1": "I",
        }

        for value in catalogs:
            compact = re.sub(
                r"[^A-Za-z0-9]",
                "",
                value,
            ).upper()

            if re.fullmatch(
                r"\d{5}",
                compact,
            ):
                first = compact[0]

                if first in first_char_repairs:
                    return (
                        first_char_repairs[first]
                        + compact[1:]
                    )

    return catalogs[0]


def resolve_current_bottle_metadata(result):
    return __import__("icl_ocr_ui_guard").metadata(result)



def normalize_chemical_name(value):
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        str(value or "").casefold(),
    ).strip()



# ============================================================
# ADD CHEMICAL — MULTI-PHOTO EVIDENCE GROUPS
# ============================================================

def _group_text_similarity(value_a, value_b):
    a = normalize_chemical_name(value_a)
    b = normalize_chemical_name(value_b)

    if not a or not b:
        return 0.0

    return (
        SequenceMatcher(
            None,
            a,
            b,
        ).ratio()
        * 100.0
    )


def _join_group_label_fragments(*values):
    parts = [
        clean_text(value)
        for value in values
        if clean_text(value)
    ]

    if not parts:
        return ""

    result = parts[0]

    for part in parts[1:]:
        if result.rstrip().endswith("-"):
            result = (
                result.rstrip()
                + part.lstrip()
            )
        else:
            result = (
                result.rstrip()
                + " "
                + part.lstrip()
            )

    result = re.sub(
        r"\(\s+",
        "(",
        result,
    )
    result = re.sub(
        r"\s+\)",
        ")",
        result,
    )
    return re.sub(
        r"\s+",
        " ",
        result,
    ).strip()


def _candidate_identity_key(candidate):
    cas = clean_text(
        candidate.get("cas")
    )

    if cas:
        return (
            "cas",
            re.sub(
                r"\s+",
                "",
                cas,
            ),
        )

    return (
        "name",
        normalize_chemical_name(
            candidate.get("name")
        ),
    )


def _candidate_rank(candidate):
    source = str(
        candidate.get("source")
        or ""
    ).upper()

    exact_bonus = 0

    if "CAS EXACT" in source:
        exact_bonus = 500
    elif "CATALOG EXACT" in source:
        exact_bonus = 450
    elif "EXACT" in source:
        exact_bonus = 400
    elif "PUBCHEM" in source:
        exact_bonus = 250
    elif "FUZZY" in source:
        exact_bonus = 100

    if candidate.get("auto_select"):
        exact_bonus += 60

    if candidate.get("recommended"):
        exact_bonus += 30

    try:
        score = float(
            candidate.get("score")
            or 0.0
        )
    except Exception:
        score = 0.0

    return (
        exact_bonus + score
    )


def _merge_group_candidates(records):
    """
    Merge identity candidates from several photos of ONE physical bottle.

    Candidate identity remains CAS/name based. No bottle manufacturer/catalog
    metadata is copied into the identity candidate.
    """
    best_by_identity = {}

    for record in records:
        result = (
            record.get("result")
            or {}
        )

        for candidate in (
            result.get("candidates")
            or []
        ):
            key = _candidate_identity_key(
                candidate
            )

            if not key[1]:
                continue

            previous = best_by_identity.get(
                key
            )

            if (
                previous is None
                or _candidate_rank(
                    candidate
                )
                > _candidate_rank(
                    previous
                )
            ):
                best_by_identity[key] = (
                    dict(candidate)
                )

    merged = sorted(
        best_by_identity.values(),
        key=_candidate_rank,
        reverse=True,
    )

    return merged[:8]


def _consensus_value(
    values,
    normalizer=lambda value: clean_text(value),
):
    prepared = []

    for value in values:
        cleaned = clean_text(value)

        if not cleaned:
            continue

        key = normalizer(cleaned)

        if not key:
            continue

        prepared.append(
            (
                key,
                cleaned,
            )
        )

    if not prepared:
        return ""

    counts = Counter(
        key
        for key, _value
        in prepared
    )

    winner_key = counts.most_common(
        1
    )[0][0]

    # Preserve the first human-readable spelling for the winning normalized key.
    for key, display in prepared:
        if key == winner_key:
            return display

    return ""


def _group_label_name_from_records(
    canonical_name,
    records,
):
    """
    Recover one current-bottle label name from multiple photos.

    This is the key cross-photo behavior:
    photo A may contain the beginning of a long name, while photo B contains
    the rest. We reuse OCR evidence already returned by each image and test
    single fragments + cross-photo fragment pairs against the verified
    canonical identity.

    No additional GPU OCR call is made.
    """
    canonical_name = clean_text(
        canonical_name
    )

    if not canonical_name:
        return (
            "",
            0.0,
            "no_canonical_identity",
        )

    fragments = []

    for record_index, record in enumerate(
        records
    ):
        result = (
            record.get("result")
            or {}
        )
        metadata = (
            result.get("metadata")
            or {}
        )

        label_name = clean_text(
            metadata.get("label_name")
        )

        if label_name:
            fragments.append(
                {
                    "text": label_name,
                    "record_index":
                        record_index,
                    "kind": "label_name",
                }
            )

        for query in (
            result.get(
                "ocr_name_queries"
            )
            or []
        ):
            query_text = clean_text(
                query.get("text")
            )

            if query_text:
                fragments.append(
                    {
                        "text":
                            query_text,
                        "record_index":
                            record_index,
                        "kind":
                            "ocr_name_query",
                    }
                )

    # Deduplicate while keeping the record association.
    deduped = []
    seen = set()

    for item in fragments:
        key = (
            item["record_index"],
            normalize_chemical_name(
                item["text"]
            ),
        )

        if not key[1] or key in seen:
            continue

        seen.add(key)
        deduped.append(item)

    fragments = deduped[:24]

    candidates = []

    # Single-photo names/fragments.
    for item in fragments:
        candidates.append(
            (
                item["text"],
                f"photo_{item['record_index'] + 1}:"
                f"{item['kind']}",
            )
        )

    # Cross-photo pairs in BOTH orders. This supports a long product name split
    # over two photographs taken from different sides/angles of the same bottle.
    for left_index, left in enumerate(
        fragments
    ):
        for right in fragments[
            left_index + 1:
        ]:
            if (
                left["record_index"]
                == right["record_index"]
            ):
                continue

            joined_lr = (
                _join_group_label_fragments(
                    left["text"],
                    right["text"],
                )
            )
            joined_rl = (
                _join_group_label_fragments(
                    right["text"],
                    left["text"],
                )
            )

            candidates.append(
                (
                    joined_lr,
                    "cross_photo_pair",
                )
            )
            candidates.append(
                (
                    joined_rl,
                    "cross_photo_pair",
                )
            )

    canonical_norm = normalize_chemical_name(
        canonical_name
    )

    best_text = ""
    best_score = 0.0
    best_source = ""

    for candidate_text, source in candidates:
        candidate_norm = (
            normalize_chemical_name(
                candidate_text
            )
        )

        if not candidate_norm:
            continue

        length_ratio = (
            len(candidate_norm)
            / max(
                1,
                len(canonical_norm),
            )
        )

        if not (
            0.70
            <= length_ratio
            <= 1.50
        ):
            continue

        score = _group_text_similarity(
            canonical_name,
            candidate_text,
        )

        if score > best_score:
            best_text = candidate_text
            best_score = score
            best_source = source

    # Same safety principle as v16: only a high-similarity current-label name
    # is allowed to become the registration display name.
    if (
        best_text
        and best_score >= 90.0
    ):
        return (
            best_text,
            round(
                best_score,
                1,
            ),
            best_source,
        )

    return (
        "",
        round(
            best_score,
            1,
        ),
        "no_high_confidence_group_label_name",
    )



def _group_resolution_payload(
    successful,
    bottle_metadata,
):
    manufacturer = _consensus_value(
        [
            meta.get(
                "manufacturer"
            )
            for meta
            in bottle_metadata
        ],
        normalizer=lambda value:
            normalize_vendor_key(
                canonical_label_vendor(
                    value
                )
            ),
    )

    catalog_candidates = []
    cas_candidates = []
    name_queries = []

    seen_catalog = set()
    seen_cas = set()
    seen_name = set()

    for meta in bottle_metadata:
        for value in (
            [
                meta.get(
                    "catalog"
                )
            ]
            + list(
                meta.get(
                    "catalog_candidates"
                )
                or []
            )
        ):
            display = clean_text(
                value
            )

            key = re.sub(
                r"[^A-Z0-9]",
                "",
                display.upper(),
            )

            if (
                key
                and key not in seen_catalog
            ):
                seen_catalog.add(
                    key
                )
                catalog_candidates.append(
                    display
                )

        for cas in (
            list(
                meta.get(
                    "raw_cas_candidates"
                )
                or []
            )
            + (
                [
                    meta.get(
                        "cas"
                    )
                ]
                if meta.get(
                    "cas"
                )
                else []
            )
        ):
            cas = clean_text(
                cas
            )

            if (
                cas
                and cas not in seen_cas
            ):
                seen_cas.add(
                    cas
                )
                cas_candidates.append(
                    cas
                )

        for value in (
            meta.get(
                "verified_label_name"
            ),
            meta.get(
                "label_name"
            ),
        ):
            text = clean_text(
                value
            )
            key = (
                normalize_chemical_name(
                    text
                )
            )

            if (
                key
                and key not in seen_name
            ):
                seen_name.add(key)
                name_queries.append(
                    text
                )

    for record in successful:
        result = (
            record.get(
                "result"
            )
            or {}
        )

        for query in (
            result.get(
                "ocr_name_queries"
            )
            or []
        ):
            text = clean_text(
                (
                    query.get(
                        "text"
                    )
                    if isinstance(
                        query,
                        dict,
                    )
                    else query
                )
            )

            key = (
                normalize_chemical_name(
                    text
                )
            )

            if (
                key
                and key not in seen_name
            ):
                seen_name.add(key)
                name_queries.append(text)

    return {
        "manufacturer":
            manufacturer,
        "catalog_candidates":
            catalog_candidates,
        "cas_candidates":
            cas_candidates,
        "name_queries":
            name_queries[:24],
    }


def resolve_photo_group_identity(
    successful,
    bottle_metadata,
):
    payload = (
        _group_resolution_payload(
            successful,
            bottle_metadata,
        )
    )

    signature = hashlib.sha1(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        ).encode(
            "utf-8"
        )
    ).hexdigest()

    cache = st.session_state.setdefault(
        "add_group_resolution_cache",
        {},
    )

    if signature in cache:
        return cache[
            signature
        ]

    url = get_ocr_api_url()
    key = get_ocr_api_key()

    if not url or not key:
        return {
            "ok": False,
            "status":
                "BACKEND_NOT_CONFIGURED",
            "candidates": [],
            "selected_candidate_id":
                None,
            "resolution_method":
                "unavailable",
        }

    try:
        response = requests.post(
            url
            + "/resolve-group",
            headers={
                "X-API-Key": key
            },
            json=payload,
            timeout=60,
        )

        if response.status_code != 200:
            result = {
                "ok": False,
                "status":
                    (
                        "GROUP_RESOLVER_HTTP_"
                        f"{response.status_code}"
                    ),
                "candidates": [],
                "selected_candidate_id":
                    None,
                "resolution_method":
                    "backend_error",
                "error":
                    response.text,
            }
        else:
            result = (
                response.json()
            )

    except Exception as exc:
        result = {
            "ok": False,
            "status":
                "GROUP_RESOLVER_ERROR",
            "candidates": [],
            "selected_candidate_id":
                None,
            "resolution_method":
                "backend_error",
            "error":
                str(exc),
        }

    cache[
        signature
    ] = result

    return result


def _clear_add_form_identity_for_group_change():
    """
    Avoid showing Ammonium bicarbonate (or any previous bottle) while the user
    is currently reviewing another evidence group.
    """
    defaults = {
        "add_name": "",
        "add_description": "",
        "add_cas": "",
        "add_manufacturer": "",
        "add_manufacturer_choice": "",
        "add_manufacturer_manual": "",
        "add_catalog": "",
        "add_lot": "",
        "add_initial_amount": 0.0,
        "add_remaining_amount": 0.0,
        "add_unit": "g",
        "add_formula": "",
        "add_mw": "",
        "add_purity": "",
    }

    for key_name, value in defaults.items():
        st.session_state[
            key_name
        ] = value

    st.session_state[
        "add_manufacturer_widget_needs_sync"
    ] = True


def merge_add_ocr_group(records, group_id):
    return __import__("icl_ocr_ui_guard").merge_group(globals(), records, group_id)


def build_add_ocr_groups(records):
    grouped = {}

    for index, record in enumerate(
        records
    ):
        group_id = int(
            record.get(
                "group_id",
                index + 1,
            )
            or index + 1
        )

        grouped.setdefault(
            group_id,
            [],
        ).append(
            record
        )

    return [
        merge_add_ocr_group(
            grouped[group_id],
            group_id,
        )
        for group_id
        in sorted(grouped)
    ]


def same_vendor(value_a, value_b):
    a = canonical_label_vendor(value_a)
    b = canonical_label_vendor(value_b)

    if not a or not b:
        return False

    return normalize_vendor_key(a) == normalize_vendor_key(b)


def same_catalog(value_a, value_b):
    a = re.sub(
        r"[^A-Z0-9]",
        "",
        str(value_a or "").upper(),
    )
    b = re.sub(
        r"[^A-Z0-9]",
        "",
        str(value_b or "").upper(),
    )

    return bool(a and b and a == b)


def should_recommend_new_physical_bottle(
    bottle_matches,
    label_manufacturer="",
    label_catalog="",
):
    if not bottle_matches:
        return False

    if label_catalog:
        if not any(
            same_catalog(
                label_catalog,
                row["catalog_number"],
            )
            for row in bottle_matches
        ):
            return True

    if label_manufacturer:
        if not any(
            same_vendor(
                label_manufacturer,
                row["manufacturer"],
            )
            for row in bottle_matches
        ):
            return True

    return False



# Shared OCR review for registration and storage checks (V8/V8.1 API).
def _icl_result_key(result):
    return hashlib.sha256(str((result or {}).get('review_token') or (result or {}).get('scan_id') or '').encode()).hexdigest()[:20]


def _icl_review_ready(result, candidate, acknowledged):
    return bool(candidate and candidate.get('candidate_id') and acknowledged
                and result.get('schema_version') == 'icl.ocr.v8'
                and result.get('review_token')
                and result.get('verification_status') != 'CONFLICT'
                and not result.get('conflicts'))


def _icl_review_product(result, scope):
    """Decision-first V8 review UI. Verification semantics are unchanged."""
    guard = __import__('icl_ocr_ui_guard')
    if not guard.is_v8(result):
        st.warning('This OCR result needs to be refreshed. You can also enter the bottle manually.')
        return None, False

    state = result.get('verification_status')
    conflicts = list(result.get('conflicts') or [])
    meta = guard.metadata(result)
    verified = result.get('verified_identity') or {}
    choices = guard.candidates(result)

    quick_name = (
        meta.get('verified_label_name')
        or meta.get('label_name')
        or verified.get('name')
        or (choices[0].get('name') if len(choices) == 1 else '')
        or 'Not detected'
    )
    quick_cas = meta.get('cas') or verified.get('cas') or '—'
    quick_manufacturer = meta.get('manufacturer') or verified.get('manufacturer') or '—'
    quick_catalog = meta.get('catalog') or verified.get('catalog') or '—'

    with st.container(border=True):
        st.markdown('#### OCR result')
        q1, q2 = st.columns(2)
        with q1:
            st.caption('Chemical')
            st.markdown(f'**{quick_name}**')
            st.caption('Manufacturer')
            st.markdown(f'**{quick_manufacturer}**')
        with q2:
            st.caption('CAS No.')
            st.markdown(f'**{quick_cas}**')
            st.caption('Catalog No.')
            st.markdown(f'**{quick_catalog}**')

        if state == 'CONFLICT' or conflicts:
            st.error('Review needed · conflicting identity evidence')
        elif state == 'PRODUCT_VERIFIED':
            st.success('Product verified')
        elif state == 'IDENTITY_VERIFIED':
            st.info('Identity verified · check manufacturer / catalog')
        else:
            st.warning('Review needed · confirm the label against a candidate')

    if conflicts:
        with st.expander('Conflicts', expanded=False):
            for item in conflicts:
                st.error(str(item))

    key = scope + '_' + _icl_result_key(result)

    def _candidate_choice_label(index):
        if index == -1:
            return 'None of these · manual entry / retake photo'
        candidate = choices[index]
        parts = [str(candidate.get('name') or 'Unnamed candidate')]
        if candidate.get('cas'):
            parts.append('CAS ' + str(candidate.get('cas')))
        vendor_catalog = ' '.join(
            part for part in (
                str(candidate.get('manufacturer') or '').strip(),
                str(candidate.get('catalog') or '').strip(),
            ) if part
        )
        if vendor_catalog:
            parts.append(vendor_catalog)
        return ' · '.join(parts)

    if not choices:
        st.warning('No verified candidate was returned. Use manual entry or retake the label photo.')
        selected = None
    else:
        selected = st.radio(
            'Identity match',
            list(range(len(choices))) + [-1],
            index=None,
            format_func=_candidate_choice_label,
            key=key + '_candidate',
        )

    candidate = choices[selected] if selected is not None and 0 <= selected < len(choices) else None
    acknowledged = False

    if candidate:
        with st.container(border=True):
            st.caption('Selected identity')
            st.markdown('### ' + str(candidate.get('name') or 'Unnamed candidate'))
            a, b = st.columns(2)
            with a:
                st.caption('CAS No.')
                st.write(str(candidate.get('cas') or '—'))
                st.caption('Catalog No.')
                st.write(str(candidate.get('catalog') or '—'))
            with b:
                st.caption('Manufacturer')
                st.write(str(candidate.get('manufacturer') or '—'))
                st.caption('Formula')
                st.write(str(candidate.get('formula') or '—'))

            url = str(candidate.get('source_url') or '')
            if url.startswith(('https://', 'http://')):
                st.link_button('Open source', url)

            acknowledged = st.checkbox(
                'I compared the label name, CAS and catalog number with this candidate.',
                key=key + '_confirm_' + str(candidate.get('candidate_id')),
            )
            st.session_state[guard.review_key(result, candidate)] = bool(acknowledged)

    with st.expander('Evidence & lookup details', expanded=False):
        observations = result.get('observations') or []
        texts = [
            str(o.get('text', ''))
            for o in observations
            if o.get('pass_id') == 'full'
        ]
        if not texts:
            texts = [str(o.get('text', '')) for o in observations]
        st.code('\n'.join(texts))
        st.json({
            k: result.get(k)
            for k in ('fields', 'lookup_events', 'conflicts', 'warnings', 'runtime')
        })

    packages = (result.get('fields') or {}).get('package') or []
    if packages:
        package_text = ' / '.join(
            dict.fromkeys(str(p.get('value', '')) for p in packages if p.get('value'))
        )
        if package_text:
            st.caption('Package label · ' + package_text + ' · Enter the actual amount / remaining stock below.')

    return candidate, _icl_review_ready(result, candidate, acknowledged)


def _icl_exact_bottle_matches(candidate):
    # Catalog numbers are only unique within a manufacturer. Never match a
    # catalog from another vendor or ignore a conflicting recorded CAS.
    rows = find_existing_bottles(cas=candidate.get('cas', ''),
        chemical_name=candidate.get('name', ''), manufacturer=candidate.get('manufacturer', ''))
    found = []
    for row in rows:
        item = dict(row)
        expected = clean_text(candidate.get('cas'))
        actual = clean_text(item.get('cas_number'))
        if expected and actual and expected != actual:
            continue
        if not (expected and actual == expected):
            if normalize_chemical_name(item.get('chemical_name', '')) != normalize_chemical_name(candidate.get('name', '')):
                continue
        found.append(item)
    return found


def _icl_scan_save(record, candidate, bottle, storage, move):
    # Revalidate the signed candidate before making any database mutation.
    result = record['result']
    draft = __import__('icl_ocr_ui_guard')._post(globals(), '/draft', {
        'review_token': result.get('review_token'), 'candidate_id': candidate.get('candidate_id'), 'reviewed': True})
    conn = get_connection()
    try:
        current = conn.execute('SELECT * FROM chemicals WHERE id=? FOR UPDATE', (bottle['id'],)).fetchone()
        if not current or current['status'] == 'Disposed':
            raise ValueError('이 병이 삭제되었거나 폐기되었습니다. 재고를 다시 확인하세요.')
        expected = clean_text(draft['fields'].get('cas'))
        if expected and clean_text(current['cas_number']) and expected != clean_text(current['cas_number']):
            raise ValueError('선택한 병의 CAS가 변경되었습니다. 다시 확인하세요.')
        if (current['storage_unit_id'], current['shelf_number']) != (bottle['storage_unit_id'], bottle['shelf_number']):
            raise ValueError('다른 사용자가 보관 위치를 변경했습니다. 화면을 새로고침하고 확인하세요.')
        if move:
            conn.execute('UPDATE chemicals SET storage_unit_id=?, shelf_number=?, location=?, updated_at=? WHERE id=?',
                (storage[0], storage[1], storage[2] if storage[0] is not None else '',
                 datetime.now().strftime('%Y-%m-%d %H:%M'), bottle['id']))
            write_audit_log('MOVE_CHEMICAL', target_type='chemical', target_id=bottle['id'],
                bottle_id=bottle['bottle_id'], details=json.dumps({'location': storage[2]}, ensure_ascii=False), conn=conn)
        evidence = dict(result)
        evidence['_user_selected_identity'] = draft['fields']
        observation_id = save_inventory_observation(bottle['id'], record['filename'], record['bytes'],
            decode_ocr_label_crop(result), evidence, storage[0], storage[1], _conn=conn)
        conn.commit()
        return observation_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _icl_clear_add_identity():
    for key in ('add_name', 'add_cas', 'add_formula', 'add_mw', 'add_manufacturer', 'add_catalog',
                'add_lot', 'add_purity', 'add_description', 'add_owner', 'add_expiration'):
        st.session_state[key] = ''
    st.session_state['add_initial_amount'] = 0.0
    st.session_state['add_remaining_amount'] = 0.0
    st.session_state['add_unit'] = 'g'
    st.session_state['add_status'] = 'Unopened'
    st.session_state['add_manufacturer_widget_needs_sync'] = True
    for key in ('icl_v8_registration_draft', 'icl_applied_result_key', 'icl_applied_photos', 'add_source_scan_id'):
        st.session_state.pop(key, None)


def _icl_ocr_connection_panel(scope):
    """Keep backend diagnostics out of the normal user workflow."""
    if not get_ocr_api_key():
        st.warning('OCR is not configured. Ask an administrator to check the Streamlit OCR secret.')
        return
    if CURRENT_USER.get('role') != 'admin':
        return
    with st.expander('OCR diagnostics', expanded=False):
        if st.button('Check server · authentication · GPU', key=scope + '_connection'):
            try:
                with st.spinner('Checking OCR backend…'):
                    info = __import__('icl_ocr_ui_guard')._post(globals(), '/health/deep', {})
                if info.get('ok') and info.get('cuda'):
                    st.success('OCR backend ready · GPU available')
                else:
                    st.warning('Backend responded, but the GPU check needs review.')
                    st.json(info)
            except Exception as exc:
                st.error('Connection check failed: ' + _icl_exception_message(exc))


def _icl_storage_scan():
    st.markdown(
        """
<div class="icl-scan-hero">
  <div class="icl-scan-kicker">INVENTORY</div>
  <div class="icl-scan-title">Storage Scan</div>
  <div class="icl-scan-flow">
    <span><b>1</b> Upload</span><i>→</i>
    <span><b>2</b> Verify</span><i>→</i>
    <span><b>3</b> Match bottle</span><i>→</i>
    <span><b>4</b> Save</span>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )
    _icl_ocr_connection_panel('scan')
    if not get_ocr_api_key():
        st.error('OCR is not configured. Ask an administrator to check the OCR secret.')
    uploads = st.file_uploader('Bottle photo(s) or ZIP', type=['jpg', 'jpeg', 'png', 'webp', 'zip'],
        accept_multiple_files=True, key='icl_scan_uploads')
    st.caption('Each photo is reviewed as one bottle. Use Add Chemical to group multiple views of the same bottle.')
    items, warnings = expand_inventory_scan_uploads(uploads)
    if warnings:
        with st.expander('Upload notes'):
            for warning in warnings:
                st.write(warning)
    signature = hashlib.sha256('|'.join(i['scan_id'] for i in items).encode()).hexdigest()
    previous = st.session_state.get('inventory_scan_batch') or []
    if items and previous and st.session_state.get('icl_scan_upload_signature') != signature:
        st.info('The upload list changed. Analyze again to refresh the review queue.')
    if st.button('Analyze photos', type='primary', disabled=not items or not get_ocr_api_key()):
        old = {r['scan_id']: r for r in previous}
        batch = []
        progress = st.progress(0.0)
        for index, item in enumerate(items):
            record = old.get(item['scan_id']) or dict(item)
            if not record.get('result') and not record.get('saved_observation_id'):
                try:
                    with st.spinner(f'{index + 1}/{len(items)} · {item["filename"]} 판독 중'):
                        record['result'] = analyze_scan_memory_item(item, enable_pubchem=True)
                    record.pop('error', None)
                except Exception as exc:
                    record['error'] = _icl_exception_message(exc)
            batch.append(record)
            progress.progress((index + 1) / len(items))
        st.session_state['inventory_scan_batch'] = batch
        st.session_state['icl_scan_upload_signature'] = signature
        st.session_state.pop('icl_scan_selected', None)
        st.rerun()
    batch = st.session_state.get('inventory_scan_batch') or []
    if not batch:
        st.info('Upload bottle photos to verify them and link observations to inventory.')
        return
    done = sum(bool(r.get('saved_observation_id') or r.get('registered_bottle_id') or r.get('skipped')) for r in batch)
    st.progress(done / len(batch), text=f'처리 완료 {done} / {len(batch)}')
    selected_index = st.selectbox('검토할 사진', range(len(batch)), key='icl_scan_selected',
        format_func=lambda i: f'{i + 1}. {batch[i]["filename"]}' + (' · 완료' if batch[i].get('saved_observation_id') or batch[i].get('registered_bottle_id') or batch[i].get('skipped') else ' · 검토 대기'))
    record = batch[selected_index]
    scope = 'scan_' + record['scan_id']
    st.image(record['bytes'], caption=record['filename'], width=440)
    if record.get('saved_observation_id') or record.get('registered_bottle_id'):
        st.success('저장 완료 · ' + str(record.get('registered_bottle_id') or '기존 병에 관찰 사진을 연결했습니다.'))
        return
    if record.get('skipped'):
        st.info('건너뛴 사진입니다.')
        if st.button('다시 검토', key=scope + '_unskip'):
            record.pop('skipped', None)
            st.rerun()
        return
    if record.get('error'):
        st.error('판독 실패: ' + record['error'])
    retry, skip = st.columns(2)
    if retry.button('이 사진 다시 판독', key=scope + '_retry'):
        try:
            with st.spinner('원본 사진을 다시 판독하고 있습니다.'):
                record['result'] = analyze_scan_memory_item(record, enable_pubchem=True)
            record.pop('error', None)
        except Exception as exc:
            record['error'] = _icl_exception_message(exc)
        st.rerun()
    if skip.button('이 사진 건너뛰기', key=scope + '_skip'):
        record['skipped'] = True
        st.rerun()
    result = record.get('result') or {}
    if not result or record.get('error'):
        return
    candidate, ready = _icl_review_product(result, scope)
    if not ready:
        st.caption('제품 선택과 원본 대조를 마치면 저장 방법이 표시됩니다.')
        return
    action = st.radio('이 사진의 병은?', ['기존 병 · 사진과 관찰 기록 추가', '새 병 · 새 Bottle ID로 등록'],
        index=None, key=scope + '_' + _icl_result_key(result) + '_' + candidate['candidate_id'] + '_action')
    if not action:
        return
    storage_options = get_storage_options()
    if action.startswith('새 병'):
        location = st.selectbox('새 병의 보관 위치', storage_options, format_func=lambda x: x[2], key=scope + '_new_storage')
        if st.button('등록 폼으로 가져오기', type='primary', key=scope + '_new'):
            _icl_clear_add_identity()
            # Clear an earlier Add Chemical group so it cannot replace this handoff.
            for k in ('add_ocr_batch', 'icl_add_batch', 'add_ocr_records', 'add_ocr_groups', 'add_ocr_group_photos', 'add_ocr_group_id'):
                st.session_state.pop(k, None)
            load_add_form_from_ocr(result, record['bytes'], record['filename'], selected_candidate=candidate,
                source_scan_id=record['scan_id'], evidence_photos=[record])
            st.session_state['icl_applied_result_key'] = _icl_result_key(result)
            st.session_state['icl_applied_photos'] = [record]
            st.session_state['icl_add_context'] = _icl_result_key(result)
            st.session_state['add_storage'] = location
            st.session_state['icl_scan_handoff'] = True
            st.session_state['icl_add_mode'] = '사진으로 입력'
            st.session_state['icl_previous_add_mode'] = '사진으로 입력'
            st.rerun()
        return
    matches = _icl_exact_bottle_matches(candidate)
    if not matches:
        st.info('일치하는 재고가 없습니다. 새 병 등록을 선택하거나 Inventory에서 기존 정보를 확인하세요.')
        return
    st.caption('같은 시약이라도 병은 여러 개일 수 있습니다. 실제 Bottle ID를 확인해 주세요.')
    mapping = {m['id']: m for m in matches}
    bottle_id = st.selectbox('실제 병 선택', list(mapping), index=None,
        format_func=lambda i: f'{mapping[i]["bottle_id"]} · {mapping[i]["chemical_name"]} · {mapping[i]["location_text"] or "미지정"}',
        key=scope + '_' + candidate['candidate_id'] + '_bottle')
    if bottle_id is None:
        return
    bottle = mapping[bottle_id]
    key = scope + '_' + _icl_result_key(result) + '_' + candidate['candidate_id'] + '_' + str(bottle_id)
    observed = st.selectbox('사진을 촬영한 보관 위치', storage_options,
        index=current_location_index(storage_options, bottle['storage_unit_id'], bottle['shelf_number']),
        format_func=lambda x: x[2], key=key + '_storage')
    move = st.checkbox('재고의 현재 보관 위치도 이 위치로 변경', value=False, key=key + '_move')
    st.write('연결할 병: **' + str(bottle['bottle_id']) + '**')
    st.write(('위치 변경: ' if move else '관찰 위치만 기록: ') + observed[2])
    confirm = st.checkbox('사진의 실제 병과 선택한 Bottle ID가 같으며, 위 처리 내용을 확인했습니다.',
        key=key + '_' + hashlib.sha256(repr((observed, move)).encode()).hexdigest()[:12] + '_save_confirm')
    if st.button('관찰 기록 저장', type='primary', disabled=not confirm, key=key + '_save'):
        try:
            record['saved_observation_id'] = _icl_scan_save(record, candidate, bottle, observed, move)
            record['resolved_action'] = 'existing_bottle_observation'
        except Exception as exc:
            st.error('저장하지 못했습니다: ' + _icl_exception_message(exc))
        else:
            st.rerun()


def _icl_add_photos():
    st.markdown(
        """
<div class="icl-add-hero">
  <div>
    <div class="icl-add-kicker">BOTTLE REGISTRATION</div>
    <div class="icl-add-title">Add Chemical</div>
  </div>
  <div class="icl-add-steps">
    <span><b>01</b> Upload</span>
    <span><b>02</b> Review</span>
    <span><b>03</b> Register</span>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )
    _icl_ocr_connection_panel('add')

    if st.session_state.get('icl_saved_notice'):
        st.success(st.session_state['icl_saved_notice'])
        if st.button('Back to Storage Scan'):
            st.session_state['_pending_navigation'] = 'Inventory Scan'
            st.rerun()

    mode_options = ['사진으로 입력', '직접 입력']
    mode = st.radio(
        'Input method',
        mode_options,
        horizontal=True,
        key='icl_add_mode',
        format_func=lambda value: 'Bottle photos' if value == '사진으로 입력' else 'Manual entry',
    )

    if st.session_state.get('icl_previous_add_mode') != mode:
        if st.session_state.get('icl_previous_add_mode') is not None:
            _icl_clear_add_identity()
            st.session_state.pop('icl_scan_handoff', None)
        st.session_state['icl_previous_add_mode'] = mode
        if not st.session_state.get('icl_scan_handoff'):
            st.session_state.pop('icl_add_context', None)

    if mode == '직접 입력':
        st.caption('Enter the exact label name and CAS number in Bottle details below.')
        return None, [], True

    if st.session_state.get('icl_scan_handoff'):
        result = st.session_state.get('add_ocr_result') or {}
        records = st.session_state.get('icl_applied_photos') or []
        st.info('Product selected in Storage Scan. Review the bottle details before registration.')
        if st.button('Start with different photos'):
            st.session_state.pop('icl_scan_handoff', None)
            st.session_state.pop('icl_add_batch', None)
            _icl_clear_add_identity()
            st.rerun()
    else:
        nonce = st.session_state.get('icl_add_upload_nonce', 0)
        st.markdown('#### Bottle photos')
        uploads = st.file_uploader(
            'Bottle label photo(s) or ZIP',
            type=['jpg', 'jpeg', 'png', 'webp', 'zip'],
            accept_multiple_files=True,
            key='icl_add_upload_' + str(nonce),
            help='Upload one or more views. ZIP files are expanded automatically.',
        )
        with st.expander('Use camera', expanded=False):
            camera = st.camera_input(
                'Capture the front label',
                key='icl_add_camera_' + str(nonce),
            )

        items, warnings = expand_inventory_scan_uploads(
            list(uploads or []) + ([camera] if camera else [])
        )
        if warnings:
            with st.expander('Upload notes', expanded=False):
                for warning in warnings:
                    st.write(warning)

        groups = {}
        if items:
            grouping_options = ['사진마다 서로 다른 병', '전부 같은 병의 여러 면', '병별로 직접 묶기']
            grouping = st.radio(
                'Grouping',
                grouping_options,
                key='icl_add_grouping',
                horizontal=True,
                format_func=lambda value: {
                    '사진마다 서로 다른 병': 'One bottle per photo',
                    '전부 같은 병의 여러 면': 'All photos = one bottle',
                    '병별로 직접 묶기': 'Custom grouping',
                }[value],
            )
            if grouping == '병별로 직접 묶기':
                with st.expander('Assign bottle numbers', expanded=True):
                    st.caption('Use the same number for photos of the same physical bottle.')
                    for i, item in enumerate(items):
                        groups[item['scan_id']] = int(st.number_input(
                            item['filename'],
                            min_value=1,
                            max_value=120,
                            value=i + 1,
                            step=1,
                            key='icl_group_' + item['scan_id'],
                        ))
            else:
                groups = {
                    item['scan_id']: (1 if grouping.startswith('전부') else i + 1)
                    for i, item in enumerate(items)
                }
            st.caption(f'{len(items)} photo(s) · {len(set(groups.values()))} bottle(s)')

        too_many = any(
            list(groups.values()).count(g) > 12
            for g in set(groups.values())
        )
        if too_many:
            st.warning('A single bottle can contain up to 12 evidence photos.')

        if st.button(
            'Analyze photos',
            type='primary',
            disabled=not items or too_many or not get_ocr_api_key(),
        ):
            batch = []
            progress = st.progress(0.0)
            for i, item in enumerate(items):
                record = dict(item, group_id=groups[item['scan_id']])
                try:
                    with st.spinner(f'{i + 1}/{len(items)} · analyzing {item["filename"]}'):
                        record['result'] = analyze_scan_memory_item(item, enable_pubchem=True)
                except Exception as exc:
                    record['error'] = _icl_exception_message(exc)
                batch.append(record)
                progress.progress((i + 1) / len(items))
            st.session_state['icl_add_batch'] = batch
            st.session_state.pop('icl_add_group_selected', None)
            st.session_state.pop('icl_saved_notice', None)
            st.rerun()

        batch = st.session_state.get('icl_add_batch') or []
        if not batch:
            st.caption('No photo is required for Manual entry.')
            return None, [], False

        group_ids = sorted(set(r['group_id'] for r in batch))
        group_id = st.selectbox(
            'Bottle to review',
            group_ids,
            key='icl_add_group_selected',
            format_func=lambda g: f'Bottle {g} · {sum(r["group_id"] == g for r in batch)} photo(s)',
        )
        selected_index = group_ids.index(group_id)
        st.caption(
            f'Bottle {selected_index + 1} of {len(group_ids)} · '
            f'{sum(r["group_id"] == group_id for r in batch)} photo(s)'
        )

        if len(group_ids) > 1:
            prev_col, spacer_col, next_col = st.columns([1.45, 3.8, 1.45])
            with prev_col:
                if st.button(
                    '← Previous bottle',
                    key=f'icl_add_prev_{group_id}',
                    width='stretch',
                    disabled=(selected_index <= 0),
                ):
                    st.session_state['icl_add_group_selected'] = group_ids[selected_index - 1]
                    st.rerun()
            with next_col:
                if st.button(
                    'Next bottle →',
                    key=f'icl_add_next_{group_id}',
                    width='stretch',
                    disabled=(selected_index >= len(group_ids) - 1),
                ):
                    st.session_state['icl_add_group_selected'] = group_ids[selected_index + 1]
                    st.rerun()

        records = [r for r in batch if r['group_id'] == group_id]
        if any(r.get('error') for r in records):
            for r in records:
                if r.get('error'):
                    st.error(r['filename'] + ': ' + r['error'])
            if st.button('Retry failed photo(s)'):
                for r in records:
                    if r.get('error'):
                        try:
                            r['result'] = analyze_scan_memory_item(r, enable_pubchem=True)
                            r.pop('error', None)
                        except Exception as exc:
                            r['error'] = _icl_exception_message(exc)
                st.rerun()
            return None, [], False

        merged = __import__('icl_ocr_ui_guard').merge_group(globals(), records, group_id)
        if merged.get('error'):
            st.error('Could not resolve this photo group: ' + merged['error'])
            return None, [], False
        result = merged.get('result') or {}

    context = _icl_result_key(result)
    if st.session_state.get('icl_add_context') != context:
        _icl_clear_add_identity()
        st.session_state['icl_add_context'] = context

    # Decision-first review: do not repeat full uploaded photos. Show the OCR label
    # crop first; source photo names remain available for provenance.
    label_crop = decode_ocr_label_crop(result)
    if label_crop:
        st.markdown('#### Detected label')
        st.image(label_crop, width=300)
    if records:
        with st.expander(f'Source photos ({len(records)})', expanded=False):
            for record in records:
                st.write('• ' + str(record.get('filename') or 'photo'))

    candidate, ready = _icl_review_product(result, 'add')
    if st.button('Apply result', type='primary', disabled=not ready):
        load_add_form_from_ocr(
            result,
            records[0]['bytes'] if records else None,
            records[0]['filename'] if records else 'bottle.jpg',
            selected_candidate=candidate,
            evidence_photos=records,
        )
        st.session_state.pop('_pending_navigation', None)
        st.session_state['icl_applied_result_key'] = context
        st.session_state['icl_applied_photos'] = list(records)
        st.rerun()

    applied = st.session_state.get('icl_applied_result_key') == context
    if applied:
        fields = (st.session_state.get('icl_v8_registration_draft') or {}).get('fields') or {}
        st.caption('Applied to Bottle details · ' + str(fields.get('name', '')))
        if candidate and candidate.get('name') != fields.get('name'):
            st.warning('The selected candidate changed. Apply the result again to update Bottle details.')
    return result, records, applied


def _icl_register_new_bottle(values, storage, photos, draft, source_scan_id):
    # Bottle, audit entry and all photographs share one transaction.
    conn = get_connection()
    try:
        columns = list(values)
        cursor = conn.execute('INSERT INTO chemicals (' + ', '.join(columns) + ') VALUES ('
            + ', '.join('?' for _ in columns) + ')', tuple(values[k] for k in columns))
        chemical_id = cursor.lastrowid
        bottle_id = f'ICL-{chemical_id:06d}'
        conn.execute('UPDATE chemicals SET bottle_id=?, registered_by=? WHERE id=?',
            (bottle_id, CURRENT_USER['email'], chemical_id))
        write_audit_log('ADD_CHEMICAL', target_type='chemical', target_id=chemical_id, bottle_id=bottle_id,
            details=json.dumps({'chemical_name': values['chemical_name'], 'cas_number': values['cas_number'],
                'location': storage[2], 'ocr_selected_fields': (draft or {}).get('fields'),
                'source_scan_id': source_scan_id}, ensure_ascii=False), conn=conn)
        for photo in photos:
            evidence = dict(photo.get('result') or {})
            evidence['_registration_selection'] = (draft or {}).get('fields') or {}
            save_inventory_observation(chemical_id, photo['filename'], photo['bytes'],
                decode_ocr_label_crop(evidence), evidence, storage[0], storage[1], _conn=conn)
        conn.commit()
        return bottle_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_add_form_from_ocr(result, original_bytes, filename, selected_candidate=None, source_scan_id=None, evidence_photos=None):
    return __import__("icl_ocr_ui_guard").apply_result(globals(), result, original_bytes, filename, selected_candidate, source_scan_id, evidence_photos)


def load_add_form_from_existing_bottle(existing_bottle, result, original_bytes, filename, source_scan_id=None):
    return __import__("icl_ocr_ui_guard").apply_result(globals(), result, original_bytes, filename, source_scan_id=source_scan_id)


def ocr_candidate_label(candidate):
    return __import__("icl_ocr_ui_guard").candidate_label(candidate)


def ocr_selected_candidate(result):
    selected_id = result.get("selected_candidate_id")
    if not selected_id:
        return None
    for candidate in result.get("candidates", []) or []:
        if candidate.get("candidate_id") == selected_id:
            return candidate
    return None


def decode_ocr_label_crop(result):
    encoded = str(result.get("label_crop_base64") or "").strip()
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded)
    except Exception:
        return None


def _image_bytes(value):
    if value is None:
        return None
    if isinstance(value, memoryview):
        return value.tobytes()
    return bytes(value)


def compress_observation_image(raw_bytes, max_size=(1600, 1600), quality=82):
    if not raw_bytes:
        return None
    try:
        image = Image.open(BytesIO(raw_bytes)).convert("RGB")
        image.thumbnail(max_size)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        return buffer.getvalue()
    except Exception:
        return raw_bytes


def save_inventory_observation(
    chemical_id,
    original_file_name,
    original_image_data,
    label_image_data,
    ocr_result,
    observed_storage_unit_id,
    observed_shelf_number,
    _conn=None,
):
    now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
    original_image_data = compress_observation_image(original_image_data)
    label_image_data = compress_observation_image(
        label_image_data,
        max_size=(1400, 1400),
        quality=86,
    )

    conn = _conn if _conn is not None else get_connection()
    cursor = conn.execute(
        """
        INSERT INTO inventory_observations (
            chemical_id,
            original_file_name,
            original_image_data,
            label_image_data,
            ocr_result_json,
            observed_storage_unit_id,
            observed_shelf_number,
            captured_at,
            uploaded_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chemical_id,
            original_file_name,
            original_image_data,
            label_image_data,
            json.dumps(ocr_result or {}, ensure_ascii=False),
            observed_storage_unit_id,
            observed_shelf_number,
            now,
            CURRENT_USER["email"],
        ),
    )
    observation_id = cursor.lastrowid

    chemical = conn.execute(
        "SELECT bottle_id, chemical_name FROM chemicals WHERE id=?",
        (chemical_id,),
    ).fetchone()

    write_audit_log(
        "INVENTORY_SCAN",
        target_type="chemical",
        target_id=chemical_id,
        bottle_id=chemical["bottle_id"] if chemical else None,
        details=json.dumps(
            {
                "observation_id": observation_id,
                "file_name": original_file_name,
                "storage_unit_id": observed_storage_unit_id,
                "shelf_number": observed_shelf_number,
            },
            ensure_ascii=False,
        ),
        conn=conn,
    )
    if _conn is None:
        conn.commit()
        conn.close()
    return observation_id


def get_inventory_observation(observation_id):
    conn = get_connection()
    row = conn.execute(
        """
        SELECT
            io.*,
            su.storage_name,
            c.bottle_id,
            c.chemical_name
        FROM inventory_observations io
        JOIN chemicals c ON c.id = io.chemical_id
        LEFT JOIN storage_units su ON su.id = io.observed_storage_unit_id
        WHERE io.id=?
        """,
        (observation_id,),
    ).fetchone()
    conn.close()
    return row


def get_inventory_observations(chemical_id, limit=8):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            io.*,
            su.storage_name,
            c.bottle_id,
            c.chemical_name
        FROM inventory_observations io
        JOIN chemicals c ON c.id = io.chemical_id
        LEFT JOIN storage_units su ON su.id = io.observed_storage_unit_id
        WHERE io.chemical_id=?
        ORDER BY io.id DESC
        LIMIT ?
        """,
        (chemical_id, limit),
    ).fetchall()
    conn.close()
    return rows


def render_observation_card(observation, expanded=True):
    if not observation:
        return

    storage_text = observation["storage_name"] or "Not Assigned"
    if observation["observed_shelf_number"] is not None:
        storage_text += f" / Shelf {observation['observed_shelf_number']}"

    with st.expander(
        f"📷 Scan #{observation['id']} · {observation['captured_at']} · {storage_text}",
        expanded=expanded,
    ):
        image_col1, image_col2 = st.columns(2)

        original = _image_bytes(observation["original_image_data"])
        label = _image_bytes(observation["label_image_data"])

        with image_col1:
            if original:
                st.image(original, caption="Original bottle", width="stretch")
            else:
                st.caption("Original image unavailable")

        with image_col2:
            if label:
                st.image(label, caption="Detected label", width="stretch")
            else:
                st.caption("Label crop unavailable")

        st.caption(
            f"Bottle: {observation['bottle_id'] or '-'} · "
            f"Uploaded by {observation['uploaded_by'] or '-'}"
        )

        try:
            ocr_data = json.loads(observation["ocr_result_json"] or "{}")
        except Exception:
            ocr_data = {}

        if ocr_data:
            with st.expander("OCR evidence"):
                st.json(
                    {
                        "status": ocr_data.get("status"),
                        "metadata": ocr_data.get("metadata"),
                        "stages_run": ocr_data.get("stages_run"),
                        "elapsed_sec": ocr_data.get("elapsed_sec"),
                    }
                )


def render_bottle_scan_history(chemical_id, limit=8):
    rows = get_inventory_observations(chemical_id, limit=limit)
    if not rows:
        st.info("No physical bottle scan has been saved for this bottle yet.")
        return
    st.caption(f"{len(rows)} most recent scan observation(s)")
    for observation in rows:
        render_observation_card(observation, expanded=False)


def find_existing_bottles(catalog="", cas="", chemical_name="", manufacturer=""):
    catalog = clean_text(catalog)
    cas = clean_text(cas)
    chemical_name = clean_text(chemical_name)
    manufacturer = clean_text(manufacturer)

    conn = get_connection()

    if catalog:
        rows = conn.execute(
            f"""
            SELECT c.*, {LOCATION_EXPR} AS location_text
            FROM chemicals c
            LEFT JOIN storage_units su ON c.storage_unit_id = su.id
            WHERE c.status != 'Disposed'
              AND LOWER(TRIM(COALESCE(c.catalog_number,'')))=LOWER(?)
            ORDER BY c.id
            """,
            (catalog,),
        ).fetchall()
        if rows:
            conn.close()
            return rows

    if cas:
        rows = conn.execute(
            f"""
            SELECT c.*, {LOCATION_EXPR} AS location_text
            FROM chemicals c
            LEFT JOIN storage_units su ON c.storage_unit_id = su.id
            WHERE c.status != 'Disposed'
              AND TRIM(COALESCE(c.cas_number,''))=?
            ORDER BY c.id
            """,
            (cas,),
        ).fetchall()
        if rows:
            conn.close()
            return rows

    if chemical_name:
        like = f"%{chemical_name}%"
        params = [like]
        maker_sql = ""
        if manufacturer:
            maker_sql = " AND c.manufacturer ILIKE ? "
            params.append(f"%{manufacturer}%")
        rows = conn.execute(
            f"""
            SELECT c.*, {LOCATION_EXPR} AS location_text
            FROM chemicals c
            LEFT JOIN storage_units su ON c.storage_unit_id = su.id
            WHERE c.status != 'Disposed'
              AND c.chemical_name ILIKE ?
              {maker_sql}
            ORDER BY c.id
            LIMIT 30
            """,
            params,
        ).fetchall()
        if rows:
            conn.close()
            return rows

    # No usable Catalog / CAS / name evidence.
    # Do not surface arbitrary inventory rows as false "matches".
    conn.close()
    return []



# ICL UX v2 — inventory detail popup.
def _icl_inventory_detail_body(detail):
    st.subheader(detail["chemical_name"] or "Chemical")

    top1, top2 = st.columns([3, 1])
    with top1:
        if detail["description"]:
            st.caption(detail["description"])
    with top2:
        st.markdown(f"**{detail['status'] or '-'}**")

    latest_scans = get_inventory_observations(
        detail["id"],
        limit=1,
    )

    if latest_scans:
        latest_scan = latest_scans[0]
        original_image = _image_bytes(
            latest_scan["original_image_data"]
        )
        label_image = _image_bytes(
            latest_scan["label_image_data"]
        )

        if original_image or label_image:
            st.markdown("#### Bottle image")

            if original_image and label_image:
                image_col1, image_col2 = st.columns([1.35, 1])

                with image_col1:
                    st.image(
                        original_image,
                        caption="Bottle photo",
                        width="stretch",
                    )

                with image_col2:
                    st.image(
                        label_image,
                        caption="Detected label",
                        width="stretch",
                    )
            elif original_image:
                st.image(
                    original_image,
                    caption="Bottle photo",
                    width="stretch",
                )
            else:
                st.image(
                    label_image,
                    caption="Detected label",
                    width="stretch",
                )

            storage_text = latest_scan["storage_name"] or "Not Assigned"
            if latest_scan["observed_shelf_number"] is not None:
                storage_text += (
                    f" / Shelf {latest_scan['observed_shelf_number']}"
                )

            st.caption(
                f"Latest scan · {latest_scan['captured_at']} · {storage_text}"
            )
    else:
        st.caption("No bottle image has been saved for this bottle yet.")

    st.markdown("#### General Information")
    c1, c2, c3 = st.columns(3)

    with c1:
        st.caption("Bottle ID")
        st.write(detail["bottle_id"] or "-")
        st.caption("CAS No.")
        st.write(detail["cas_number"] or "-")
        st.caption("Legacy ID")
        st.write(detail["legacy_id"] or "-")

    with c2:
        st.caption("Manufacturer")
        st.write(detail["manufacturer"] or "-")
        st.caption("Catalog No.")
        st.write(detail["catalog_number"] or "-")
        st.caption("Lot No.")
        st.write(detail["lot_number"] or "-")

    with c3:
        st.caption("Storage")
        st.write(detail["location_text"] or "-")
        st.caption("Owner")
        st.write(detail["owner"] or "-")
        st.caption("Storage temperature")
        st.write(detail["storage_temperature"] or "-")

    st.markdown("#### Inventory")
    i1, i2, i3, i4 = st.columns(4)
    i1.metric(
        "Initial",
        f"{detail['initial_amount']} {detail['unit'] or ''}"
        if detail["initial_amount"] is not None else "-",
    )
    i2.metric(
        "Remaining",
        f"{detail['remaining_amount']} {detail['unit'] or ''}"
        if detail["remaining_amount"] is not None else "-",
    )
    i3.metric("Purchased", detail["purchase_date"] or "-")
    i4.metric("Expires", detail["expiration_date"] or "-")

    with st.expander("Chemical information", expanded=False):
        p1, p2, p3 = st.columns(3)
        with p1:
            st.caption("Molecular formula")
            st.write(detail["molecular_formula"] or "-")
            st.caption("Molecular weight")
            st.write(detail["molecular_weight"] or "-")
        with p2:
            st.caption("Purity")
            st.write(detail["purity"] or "-")
            st.caption("Concentration / density")
            st.write(detail["concentration_density"] or "-")
        with p3:
            st.caption("Opened")
            st.write(detail["opened_date"] or "-")
            st.caption("Registered")
            st.write(
                detail["source_registered_date"]
                or detail["created_at"]
                or "-"
            )

    with st.expander("Safety", expanded=False):
        render_safety_panel(
            signal_word=detail["safety_signal_word"],
            h_codes=detail["safety_h_codes"],
            hazard_statements=detail["safety_hazard_statements"],
            pictograms=detail["safety_pictograms"],
            source_url=detail["safety_source_url"],
            checked_at=detail["safety_checked_at"],
        )

    if detail["comments"]:
        with st.expander("Notes", expanded=False):
            st.write(detail["comments"])

    with st.expander("Physical bottle / scan history", expanded=False):
        render_bottle_scan_history(detail["id"], limit=8)


if hasattr(st, "dialog"):
    @st.dialog("Chemical inventory", width="large")
    def _icl_inventory_dialog(detail):
        _icl_inventory_detail_body(detail)
else:
    def _icl_inventory_dialog(detail):
        _icl_inventory_detail_body(detail)


# ICL UX v3 — storage detail popup.
def _icl_storage_detail_body(storage_id):
    conn = get_connection()
    storage = conn.execute(
        """
        SELECT id, storage_name, notes
        FROM storage_units
        WHERE id = ?
        """,
        (storage_id,),
    ).fetchone()
    shelves = conn.execute(
        """
        SELECT shelf_number
        FROM storage_shelves
        WHERE storage_unit_id = ?
        ORDER BY shelf_number
        """,
        (storage_id,),
    ).fetchall()
    conn.close()

    if not storage:
        st.error("Storage unit not found.")
        return

    st.subheader(storage["storage_name"])
    if storage["notes"]:
        st.caption(storage["notes"])

    shelf_options = ["All Locations", "Unassigned"] + [
        f"Shelf {shelf['shelf_number']}" for shelf in shelves
    ]
    selected_shelf = st.selectbox(
        "Shelf",
        shelf_options,
        key=f"storage_dialog_shelf_{storage_id}",
    )

    conditions = ["c.storage_unit_id = ?", "c.status != 'Disposed'"]
    params = [storage_id]
    if selected_shelf == "Unassigned":
        conditions.append("c.shelf_number IS NULL")
    elif selected_shelf.startswith("Shelf "):
        shelf_number = int(selected_shelf.replace("Shelf ", ""))
        conditions.append("c.shelf_number = ?")
        params.append(shelf_number)

    where_sql = " AND ".join(conditions)
    conn = get_connection()
    chemicals = conn.execute(
        f"""
        SELECT
            c.bottle_id AS "Bottle ID",
            c.chemical_name AS "Chemical Name",
            c.cas_number AS "CAS No.",
            c.manufacturer AS "Manufacturer",
            COALESCE(c.remaining_amount, c.amount) AS "Remaining",
            c.unit AS "Unit",
            CASE
                WHEN c.shelf_number IS NOT NULL THEN 'Shelf ' || c.shelf_number
                ELSE 'Unassigned'
            END AS "Shelf",
            c.status AS "Status"
        FROM chemicals c
        WHERE {where_sql}
        ORDER BY c.shelf_number, c.chemical_name
        """,
        params,
    ).fetchall()
    conn.close()

    st.caption(f"{len(chemicals):,} active bottle(s)")
    if chemicals:
        display_data = []
        for chemical in chemicals:
            remaining = chemical["Remaining"]
            try:
                value = float(remaining)
                if value.is_integer():
                    value = int(value)
                remaining_text = f"{value} {chemical['Unit'] or ''}".strip()
            except Exception:
                remaining_text = (
                    f"{remaining} {chemical['Unit'] or ''}".strip()
                    if remaining is not None else "-"
                )
            display_data.append(
                {
                    "Bottle ID": chemical["Bottle ID"],
                    "Chemical Name": chemical["Chemical Name"],
                    "CAS No.": chemical["CAS No."],
                    "Manufacturer": chemical["Manufacturer"],
                    "Remaining": remaining_text,
                    "Shelf": chemical["Shelf"],
                    "Status": chemical["Status"],
                }
            )
        st.dataframe(display_data, width="stretch", hide_index=True)
    else:
        st.info("No chemicals found in this location.")

    if st.button("Add shelf", key=f"storage_dialog_add_shelf_{storage_id}"):
        conn = get_connection()
        max_shelf = conn.execute(
            """
            SELECT MAX(shelf_number)
            FROM storage_shelves
            WHERE storage_unit_id = ?
            """,
            (storage_id,),
        ).fetchone()[0]
        next_shelf = (max_shelf or 0) + 1
        conn.execute(
            """
            INSERT INTO storage_shelves (storage_unit_id, shelf_number)
            VALUES (?, ?)
            """,
            (storage_id, next_shelf),
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


if hasattr(st, "dialog"):
    @st.dialog("Storage", width="large")
    def _icl_storage_dialog(storage_id):
        _icl_storage_detail_body(storage_id)
else:
    def _icl_storage_dialog(storage_id):
        _icl_storage_detail_body(storage_id)


# ICL UX v3.6 — in-memory camera upload adapter.
class _ICLCameraUpload:
    def __init__(self, record):
        self.name = record["name"]
        self.type = record.get("mime_type") or "image/jpeg"
        self.group_id = int(record.get("group_id") or 1)
        self._data = record["bytes"]

    def getvalue(self):
        return self._data


# ICL UX v4.0 — native one-step browser back buffer.
#
# Streamlit writes query-parameter changes to the top-level app URL. We change
# one harmless parameter exactly once per Streamlit session, which gives mobile
# browsers one same-app history entry before the page that opened this app.
#
# Important: after Back removes/reverts this parameter, session_state remains
# seeded, so we DO NOT immediately add it again and trap the user in the app.
if not st.session_state.get("_icl_back_guard_seeded", False):
    _icl_existing_guard = str(
        st.query_params.get("_icl_guard", "")
        or ""
    )
    st.session_state["_icl_back_guard_seeded"] = True

    # Always change the value, even if somebody bookmarked a guarded URL. That
    # guarantees a fresh same-app history entry for this browser session.
    st.query_params["_icl_guard"] = (
        "2"
        if _icl_existing_guard == "1"
        else "1"
    )


# ============================================================
# HEADER / SIDEBAR
# ============================================================

st.markdown(
    """
<div class="icl-shell-header">
  <div class="icl-shell-copy">
    <div class="icl-shell-eyebrow">ICL · INORGANIC CHEMISTRY LABORATORY</div>
    <div class="icl-shell-title">Chemical Manager</div>
  </div>
  <div class="icl-shell-pills" aria-label="Workspace areas">
    <span>Inventory</span><span>Storage</span><span>Safety</span>
  </div>
</div>
    """,
    unsafe_allow_html=True,
)

# Render the original repository PNG directly. Streamlit preserves the aspect
# ratio, so the laboratory logo is never vertically compressed.
st.sidebar.image(
    "assets/logo_full.png",
    width=238,
)
st.sidebar.markdown(
    '<div class="icl-sidebar-product">CHEMICAL MANAGER</div>',
    unsafe_allow_html=True,
)

st.sidebar.divider()

st.sidebar.markdown(
    '<div class="icl-account-label">SIGNED IN</div>',
    unsafe_allow_html=True,
)
st.sidebar.markdown(f"**{CURRENT_USER['name']}**")
st.sidebar.caption(CURRENT_USER["email"])
st.sidebar.caption(f"Role · {CURRENT_USER['role'].title()}")
st.sidebar.button(
    "Log out",
    key="icl_logout",
    on_click=st.logout,
    width="stretch",
)

st.sidebar.divider()

# ICL UX v3.2 — hierarchical navigation.
_pending = st.session_state.pop("_pending_navigation", None)

_pending_map = {
    "Chemical Search": ("Inventory", "Chemicals"),
    "Inventory Management": ("Inventory", "Chemicals"),
    "Inventory Scan": ("Inventory", "Scan"),
    "Storage Management": ("Storage", None),
    "Excel Import": ("Excel", "Import"),
    "Excel Export": ("Excel", "Export"),
    "Add Chemical": ("Add Chemical", None),
    "SDS": ("SDS", None),
    "User Management": ("User Management", None),
    "Activity Log": ("Activity Log", None),
    "Settings": ("Settings", None),
}

_root_items = [
    "Inventory",
    "Storage",
    "Excel",
    "Add Chemical",
    "SDS",
]
if CURRENT_USER["role"] == "admin":
    _root_items.extend(["User Management", "Activity Log"])
_root_items.append("Settings")

if _pending in _pending_map:
    _pending_root, _pending_sub = _pending_map[_pending]
    st.session_state["icl_nav_root"] = _pending_root
    if _pending_root == "Inventory" and _pending_sub:
        st.session_state["icl_inventory_sub"] = _pending_sub
    elif _pending_root == "Excel" and _pending_sub:
        st.session_state["icl_excel_sub"] = _pending_sub

if st.session_state.get("icl_nav_root") not in _root_items:
    st.session_state["icl_nav_root"] = "Inventory"
if st.session_state.get("icl_inventory_sub") not in ["Chemicals", "Scan"]:
    st.session_state["icl_inventory_sub"] = "Chemicals"
if st.session_state.get("icl_excel_sub") not in ["Import", "Export"]:
    st.session_state["icl_excel_sub"] = "Import"


def _icl_root_button(label, key):
    active = st.session_state.get("icl_nav_root") == label
    if st.sidebar.button(
        label,
        key=key,
        type="primary" if active else "secondary",
        width="stretch",
    ):
        st.session_state["icl_nav_root"] = label
        st.rerun()
    return st.session_state.get("icl_nav_root") == label


def _icl_child_button(label, state_key, state_value, key):
    active = st.session_state.get(state_key) == state_value
    if st.sidebar.button(
        label,
        key=key,
        type="primary" if active else "secondary",
        width="stretch",
    ):
        st.session_state[state_key] = state_value
        st.rerun()
    return st.session_state.get(state_key) == state_value


if _icl_root_button("Inventory", "icl_nav_root_inventory"):
    _icl_child_button(
        "Chemicals",
        "icl_inventory_sub",
        "Chemicals",
        "icl_nav_inventory_chemicals",
    )
    _icl_child_button(
        "Scan",
        "icl_inventory_sub",
        "Scan",
        "icl_nav_inventory_scan",
    )

_icl_root_button("Storage", "icl_nav_root_storage")

if _icl_root_button("Excel", "icl_nav_root_excel"):
    _icl_child_button(
        "Import",
        "icl_excel_sub",
        "Import",
        "icl_nav_excel_import",
    )
    _icl_child_button(
        "Export",
        "icl_excel_sub",
        "Export",
        "icl_nav_excel_export",
    )

_icl_root_button("Add Chemical", "icl_nav_root_add")
_icl_root_button("SDS", "icl_nav_root_sds")

if CURRENT_USER["role"] == "admin":
    _icl_root_button("User Management", "icl_nav_root_users")
    _icl_root_button("Activity Log", "icl_nav_root_activity")

_icl_root_button("Settings", "icl_nav_root_settings")

_root_menu = st.session_state["icl_nav_root"]

if _root_menu == "Inventory":
    # Chemicals is the unified searchable + management workspace.
    # Scan remains the only child with a separate workflow.
    menu = (
        "Inventory Scan"
        if st.session_state["icl_inventory_sub"] == "Scan"
        else "Inventory Management"
    )
elif _root_menu == "Storage":
    menu = "Storage Management"
elif _root_menu == "Excel":
    menu = (
        "Excel Import"
        if st.session_state["icl_excel_sub"] == "Import"
        else "Excel Export"
    )
else:
    menu = _root_menu

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
                _icl_inventory_dialog(detail)

    else:

        st.info(
            "No chemicals match the selected filters."
        )


# ============================================================
# INVENTORY SCAN
# ============================================================

elif menu == "Inventory Scan":
    _icl_storage_scan()

elif menu == "Add Chemical":
    if st.session_state.pop('icl_reset_after_save', False):
        _icl_clear_add_identity()
        for k in ('icl_scan_handoff', 'icl_add_context', 'add_ocr_result', 'add_ocr_original',
                  'add_ocr_group_photos', 'add_ocr_group_id'):
            st.session_state.pop(k, None)
    add_ocr_result, active_photos, can_register = _icl_add_photos()
    st.divider()
    st.markdown('### Bottle details')
    if not can_register:
        st.info('Confirm an OCR identity and apply it, or switch to Manual entry.')

    defaults = {
        "add_name": "",
        "add_description": "",
        "add_cas": "",
        "add_manufacturer": "",
        "add_manufacturer_choice": "",
        "add_manufacturer_manual": "",
        "add_catalog": "",
        "add_lot": "",
        "add_initial_amount": 0.0,
        "add_remaining_amount": 0.0,
        "add_unit": "g",
        "add_formula": "",
        "add_mw": "",
        "add_purity": "",
        "add_owner": "",
        "add_expiration": "",
        "add_status": "Unopened",
    }
    for key_name, default_value in defaults.items():
        st.session_state.setdefault(key_name, default_value)

    if st.session_state.pop(
        "add_manufacturer_widget_needs_sync",
        False,
    ):
        sync_add_manufacturer_widget()

    storage_options = get_storage_options()
    registration_date = datetime.now(ZoneInfo("Asia/Seoul")).date()

    with st.form("add_chemical_form_" + str(st.session_state.get("icl_add_upload_nonce", 0)) + "_" + str(st.session_state.get("icl_add_context", "manual")), clear_on_submit=False, enter_to_submit=False):
        chemical_name = st.text_input("Chemical name *", key="add_name")
        left, right = st.columns(2)
        with left:
            cas_number = st.text_input("CAS No.", key="add_cas")
            manufacturer_choice = st.selectbox(
                "Manufacturer",
                ADD_CHEMICAL_MANUFACTURERS + [OTHER_MANUFACTURER_OPTION],
                key="add_manufacturer_choice",
                format_func=lambda value: "Select manufacturer" if value == "" else value,
            )
            if manufacturer_choice == OTHER_MANUFACTURER_OPTION:
                manufacturer = st.text_input("Manufacturer (manual)", key="add_manufacturer_manual")
            else:
                manufacturer = manufacturer_choice
            catalog_number = st.text_input("Catalog No.", key="add_catalog")
        with right:
            ac, uc = st.columns([2, 1])
            with ac:
                initial_amount = st.number_input("Amount", min_value=0.0, key="add_initial_amount")
            with uc:
                unit = st.selectbox("Unit", ["g", "mg", "kg", "mL", "L", "ea"], key="add_unit")
            storage = st.selectbox(
                "Storage location", storage_options,
                format_func=lambda option: option[2], key="add_storage",
            )
        remaining_amount = st.number_input("Remaining amount", min_value=0.0, key="add_remaining_amount")
        full_bottle = st.checkbox("Unopened bottle · set remaining amount equal to Amount", value=False)
        if full_bottle:
            remaining_amount = initial_amount
        with st.expander("More details", expanded=False):
            description = st.text_input("Description / grade", key="add_description")
            dl, dr = st.columns(2)
            with dl:
                lot_number = st.text_input("Lot No.", key="add_lot")
                owner = st.text_input("Owner / person in charge", key="add_owner")
                expiration_date = st.text_input("Expiration date", placeholder="YYYY-MM-DD", key="add_expiration")
            with dr:
                molecular_formula = st.text_input("Molecular formula", key="add_formula")
                molecular_weight = st.text_input("Molecular weight", key="add_mw")
                purity = st.text_input("Purity", key="add_purity")
                status = st.selectbox("Status", ["Unopened", "In Use", "Disposal Pending"], key="add_status")
            st.date_input("Registration date", value=registration_date, disabled=True, format="YYYY-MM-DD")
        registration_confirmed = st.checkbox('I reviewed the identity, amount and storage location for this new bottle.', value=False)
        submitted = st.form_submit_button("Register bottle", type="primary", disabled=not can_register)

    if submitted:
        if not can_register or not registration_confirmed:
            st.error('Review the bottle information and check the confirmation box.')
        elif not chemical_name.strip():
            st.error('Chemical name is required.')
        elif remaining_amount > initial_amount:
            st.error('Remaining amount cannot exceed the initial amount.')
        else:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
            values = dict(chemical_name=chemical_name.strip(), description=description.strip(),
                cas_number=cas_number.strip(), manufacturer=manufacturer.strip(), catalog_number=catalog_number.strip(),
                lot_number=lot_number.strip(), amount=initial_amount, initial_amount=initial_amount,
                remaining_amount=remaining_amount, unit=unit, storage_unit_id=storage[0], shelf_number=storage[1],
                location=storage[2] if storage[0] is not None else '', owner=owner.strip(),
                expiration_date=expiration_date.strip(), status=status,
                source_registered_date=registration_date.strftime('%Y-%m-%d'), molecular_formula=molecular_formula.strip(),
                molecular_weight=molecular_weight.strip(), purity=purity.strip(), created_at=timestamp, updated_at=timestamp)
            draft = st.session_state.get('icl_v8_registration_draft') if add_ocr_result else None
            photos = st.session_state.get('icl_applied_photos', []) if add_ocr_result else []
            source_scan_id = st.session_state.get('add_source_scan_id')
            try:
                bottle_id = _icl_register_new_bottle(values, storage, photos, draft, source_scan_id)
            except Exception as exc:
                st.error('등록하지 못했습니다: ' + _icl_exception_message(exc))
            else:
                for record in st.session_state.get('inventory_scan_batch', []):
                    if record['scan_id'] == source_scan_id:
                        record['registered_bottle_id'] = bottle_id
                # Remove the completed group; preserve other bottles awaiting review.
                completed_hashes = {p.get('scan_id') for p in photos}
                st.session_state['icl_add_batch'] = [r for r in st.session_state.get('icl_add_batch', [])
                    if r.get('scan_id') not in completed_hashes]
                st.session_state.pop('icl_add_group_selected', None)
                st.session_state['icl_saved_notice'] = f'{chemical_name} · {bottle_id} registered · {len(photos)} photo(s) linked'
                try:
                    with st.spinner('안전 정보 확인 중…'):
                        safety = fetch_pubchem_safety(cas_number, chemical_name)
                    if safety:
                        save_pubchem_safety(int(bottle_id.rsplit('-', 1)[1]), safety)
                    else:
                        st.session_state['icl_saved_notice'] += ' · Review safety information in SDS.'
                except Exception:
                    st.session_state['icl_saved_notice'] += ' · Safety lookup failed; review SDS.'
                st.session_state['icl_reset_after_save'] = True
                st.session_state['icl_add_upload_nonce'] = st.session_state.get('icl_add_upload_nonce', 0) + 1
                st.rerun()

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
                    "The expected source Item Name column was not found. Please check that this is a Lab Manager item-list export."
                )
            else:
                only_reagents = st.checkbox(
                    "Import only rows categorized as reagents",
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
    if st.button('사진으로 보관 시약 확인 · Storage Scan', key='storage_open_scan'):
        st.session_state['_pending_navigation'] = 'Inventory Scan'
        st.rerun()


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
        # STORAGE DETAIL — popup
        # ====================================================
        if "selected_storage" in st.session_state:
            _icl_storage_dialog(st.session_state["selected_storage"])

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
    export_rows = conn.execute(
        f"""
        {select_sql}
        FROM chemicals c
        LEFT JOIN storage_units su ON c.storage_unit_id = su.id
        {status_clause}
        ORDER BY c.chemical_name, c.id
        """
    ).fetchall()
    conn.close()

    # Build the DataFrame from psycopg rows directly.
    # pandas.read_sql_query expects a DB-API connection exposing .cursor(),
    # while this app intentionally uses CompatConnection.
    export_df = pd.DataFrame([dict(row) for row in export_rows])

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
    st.header("Chemicals")

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
                COALESCE(
                    NULLIF(c.source_registered_date, ''),
                    LEFT(c.created_at, 10),
                    '-'
                ) AS registered_date,
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
            display_rows = []

            for chemical in rows:
                amount_text = "-"
                if chemical["remaining_amount"] is not None:
                    amount_text = (
                        f"{chemical['remaining_amount']:g} "
                        f"{chemical['unit'] or ''}"
                    )

                display_rows.append(
                    {
                        "Bottle ID": chemical["bottle_id"] or "-",
                        "Chemical": chemical["chemical_name"] or "-",
                        "Manufacturer": chemical["manufacturer"] or "-",
                        "Remaining": amount_text,
                        "Location": chemical["location_text"] or "-",
                        "Status": chemical["status"] or "-",
                        "Registered": chemical["registered_date"] or "-",
                    }
                )

            inventory_selection = st.dataframe(
                display_rows,
                width="stretch",
                height=470,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="inventory_management_table",
            )

            selected_chemical = None
            if inventory_selection.selection.rows:
                selected_index = inventory_selection.selection.rows[0]
                selected_chemical = rows[selected_index]

                selected_id = selected_chemical["id"]

                # Open the information popup only when the selected bottle
                # changes. This prevents the dialog from reopening on every
                # Edit/Dispose interaction while preserving row-click behavior.
                if st.session_state.get("_icl_last_detail_id") != selected_id:
                    conn = get_connection()
                    detail = conn.execute(
                        f"""
                        SELECT
                            c.*,
                            {LOCATION_EXPR} AS location_text
                        FROM chemicals c
                        LEFT JOIN storage_units su
                            ON c.storage_unit_id = su.id
                        WHERE c.id = ?
                        """,
                        (selected_id,),
                    ).fetchone()
                    conn.close()

                    if detail:
                        st.session_state["_icl_last_detail_id"] = selected_id
                        _icl_inventory_dialog(detail)
            else:
                st.session_state.pop("_icl_last_detail_id", None)

            # Search and management are merged, but management itself is not
            # removed: actions remain visible at all times and activate when a
            # bottle row is selected.
            with st.container(border=True):
                st.markdown("**Bottle actions**")

                if selected_chemical is None:
                    st.caption("Select a bottle above to view or manage it.")
                else:
                    st.caption(
                        f"{selected_chemical['bottle_id'] or '-'} · "
                        f"{selected_chemical['chemical_name'] or '-'} · "
                        f"{selected_chemical['manufacturer'] or '-'} · "
                        f"{selected_chemical['location_text'] or '-'}"
                    )

                action_col1, action_col2, action_col3 = st.columns(3)

                if action_col1.button(
                    "View details",
                    key="inventory_selected_view",
                    width="stretch",
                    disabled=(selected_chemical is None),
                ):
                    conn = get_connection()
                    detail = conn.execute(
                        f"""
                        SELECT
                            c.*,
                            {LOCATION_EXPR} AS location_text
                        FROM chemicals c
                        LEFT JOIN storage_units su
                            ON c.storage_unit_id = su.id
                        WHERE c.id = ?
                        """,
                        (selected_chemical["id"],),
                    ).fetchone()
                    conn.close()
                    if detail:
                        _icl_inventory_dialog(detail)

                if action_col2.button(
                    "Edit bottle",
                    key="inventory_selected_edit",
                    width="stretch",
                    disabled=(selected_chemical is None),
                ):
                    edit_chemical_dialog(selected_chemical["id"])

                if action_col3.button(
                    "Dispose",
                    key="inventory_selected_dispose",
                    width="stretch",
                    disabled=(selected_chemical is None),
                ):
                    dispose_chemical_dialog(selected_chemical["id"])

        else:
            st.info("No active chemicals found.")

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
        - Modal T4 serverless GPU OCR (ReagentOCRV7)
        - Multi-image / ZIP Inventory Scan with 3×2 review cards
        - Existing bottle reconciliation + new physical-bottle registration
        - Physical bottle image / scan history
        - OCR-assisted Add Chemical prefill
        - App-wide OCR backend shared by all logged-in users
        """
    )

    st.divider()
    st.subheader("Planned Features")
    st.write(
        """
        - QR code generation
        - Manufacturer-specific SDS auto-discovery
        - AI SDS summarization
        - Natural-language inventory search
        - Automated backup / reporting
        """
    )

# ICL_V8_GUARD_APPLIED


def _icl_live_response(response):
    status = response.status_code
    if status == 401:
        raise RuntimeError("인증 실패(401): Streamlit Secrets의 OCR_API_KEY와 Modal Secret icl-ocr-api의 OCR_API_KEY가 일치해야 합니다. 관리자라면 아래 임시 키 입력으로 확인할 수 있습니다.")
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise RuntimeError("OCR 서버 HTTP " + str(status) + ": JSON 응답이 아닙니다. 서버 실행 로그를 확인하세요.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("OCR 서버 HTTP " + str(status) + ": 응답 형식이 올바르지 않습니다.")
    failed = status != 200 or payload.get("ok") is False or bool(payload.get("error"))
    if failed:
        kind = str(payload.get("error_type") or "ServerError")
        detail = str(payload.get("error") or "서버가 실패 응답을 반환했지만 상세 메시지가 없습니다.").strip()
        if not detail:
            detail = "서버 오류 메시지가 비어 있습니다."
        raise RuntimeError("OCR 서버 HTTP " + str(status) + " · " + kind + ": " + detail[:500])
    return payload



def _icl_public_report(value):
    # Signed tokens are credentials; diagnostic downloads omit them and images.
    excluded = {"review_token", "evidence_token", "draft_token", "label_crop_base64", "preview_base64"}
    if isinstance(value, dict):
        return {k: _icl_public_report(v) for k, v in value.items() if k not in excluded}
    if isinstance(value, list):
        return [_icl_public_report(v) for v in value]
    return value


def render_icl_live_test():
    st.header("OCR Live Test")
    st.caption("화면 버전: 20260907-fix2 · 전체 시약관리 사이트 내 테스트")
    st.caption("사진을 올려 원문과 검증된 제품 정보를 비교하세요. 이 화면의 테스트는 재고 DB에 저장하지 않습니다.")
    st.caption("연결 주소: " + get_ocr_api_url())
    if _ocr_user_is_admin():
        with st.expander("인증 문제가 있을 때만: 임시 API 키"):
            st.text_input("OCR_API_KEY 값", type="password", key="icl_v8_temporary_api_key", help="현재 관리자 세션에서만 사용합니다. 정상 연결되면 Streamlit Secrets에도 같은 값을 설정하세요.")
            st.caption("비워 두면 기존 Streamlit Secrets의 키를 사용합니다. Modal 로그인 토큰과는 다른 값입니다.")
    key = get_ocr_api_key()
    if not key:
        st.warning("관리자가 Streamlit Secrets에 OCR_API_KEY를 설정해야 합니다.")
    if st.button("서버·인증·GPU 연결 확인", key="icl_live_health", disabled=not bool(key)):
        try:
            with st.spinner("서버와 GPU를 확인하는 중입니다. 첫 실행은 시간이 걸릴 수 있습니다."):
                state = _icl_live_response(requests.post(get_ocr_api_url() + "/health/deep", headers={"X-API-Key": key}, json={}, timeout=240))
            if not state.get("ok") or not state.get("cuda"):
                st.error("GPU 실행 확인에 실패했습니다.")
            else:
                st.success("인증 성공 · GPU OCR 실행 확인")
            st.json(state)
        except Exception as exc:
            st.error(_icl_exception_message(exc))
    uploaded = st.file_uploader("시약 사진", type=["jpg", "jpeg", "png", "webp"], key="icl_live_photo")
    if uploaded is None:
        return
    data = uploaded.getvalue()
    image_id = hashlib.sha256(data).hexdigest()
    st.image(data, width=450)
    if st.button("이 사진 OCR 실행", key="icl_live_run", disabled=not bool(key)):
        st.session_state.pop("icl_live_result", None)
        st.session_state.pop("icl_live_draft", None)
        try:
            with st.spinner("원본 판독 및 제품 정보를 확인하고 있습니다."):
                response = requests.post(get_ocr_api_url() + "/ocr", headers={"X-API-Key": key}, files={"file": (uploaded.name, data, uploaded.type or "image/jpeg")}, timeout=240)
                result = _icl_live_response(response)
                if result.get("schema_version") != "icl.ocr.v8":
                    raise RuntimeError("V8 서버 주소인지 확인하세요.")
            st.session_state["icl_live_result"] = {"image_id": image_id, "result": result}
        except Exception as exc:
            st.error(_icl_exception_message(exc))
    saved = st.session_state.get("icl_live_result", {})
    if saved.get("image_id") != image_id:
        return
    result = saved["result"]
    guard = __import__("icl_ocr_ui_guard")
    guard.render_review(globals(), result)
    choices = guard.candidates(result)
    if not choices:
        st.warning("확인 가능한 제품 후보가 없습니다. 라벨을 다른 각도에서 촬영하거나 수동 등록하세요.")
    else:
        selected = st.radio("제품 후보 선택", options=range(len(choices)), index=None, format_func=lambda i: guard.candidate_label(choices[i]), key="live_" + guard.choice_key(result))
        if selected is not None:
            candidate = choices[selected]
            guard.render_candidate(globals(), result, candidate)
            reviewed = bool(st.session_state.get(guard.review_key(result, candidate), False))
            blocked = result.get("verification_status") == "CONFLICT" or (not candidate.get("product_verified") and not reviewed)
            if st.button("등록에 적용될 값 확인", disabled=blocked, key="icl_live_draft_check"):
                st.session_state.pop("icl_live_draft", None)
                try:
                    draft = _icl_live_response(requests.post(get_ocr_api_url() + "/draft", headers={"X-API-Key": key}, json={"review_token": result.get("review_token"), "candidate_id": candidate["candidate_id"], "reviewed": reviewed}, timeout=60))
                    if draft["fields"].get("name") != candidate.get("name"):
                        raise RuntimeError("선택한 이름과 적용할 이름이 다릅니다. 적용을 중단했습니다.")
                    st.session_state["icl_live_draft"] = {"image_id": image_id, "review_token": result.get("review_token"), "candidate_id": candidate["candidate_id"], "fields": draft["fields"]}
                except Exception as exc:
                    st.error(_icl_exception_message(exc))
            draft = st.session_state.get("icl_live_draft", {})
            if draft.get("image_id") == image_id and draft.get("review_token") == result.get("review_token") and draft.get("candidate_id") == candidate["candidate_id"]:
                st.success("선택한 제품명과 적용할 이름이 일치합니다. DB에는 저장하지 않았습니다.")
                st.json(draft["fields"])
    st.download_button("진단 결과 다운로드", data=json.dumps(_icl_public_report(result), ensure_ascii=False, indent=2), file_name="ocr_live_result.json", mime="application/json")


if menu == "OCR Live Test":
    render_icl_live_test()
