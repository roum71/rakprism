import json
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
import streamlit as st
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1


# =========================================================
# 1. إعدادات الصفحة والتصميم
# =========================================================
st.set_page_config(
    page_title="نموذج تقييم المخاطر",
    page_icon="📋",
    layout="centered",
)

st.markdown(
    """
    <style>
    .main, [data-testid="stAppViewContainer"] { direction: rtl; }
    label, p, h1, h2, h3, h4,
    [data-testid="stMarkdownContainer"] {
        text-align: right !important;
        direction: rtl !important;
    }
    div[data-testid="stRadio"] > label,
    div[data-testid="stMultiSelect"] > label,
    div[data-testid="stTextInput"] > label,
    div[data-testid="stSelectbox"] > label {
        direction: rtl;
        text-align: right;
    }
    div[data-baseweb="tab-list"] {
        direction: rtl;
        justify-content: flex-start;
    }
    div.stBlock, div.stTabs, div[data-testid="stVerticalBlock"] {
        gap: 0.5rem;
    }
    h3 {
        padding-top: 0.5rem !important;
        padding-bottom: 0.2rem !important;
        font-size: 1.1rem !important;
    }
    hr { margin: 0.8rem 0 !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# 2. إعدادات Google Sheets
# =========================================================
QUESTIONS_TAB = "Questions"
RESPONSES_TAB = "Responses"
USERS_TAB = "Users"
DRAFTS_TAB = "Drafts"
DRAFT_HISTORY_TAB = "Draft History"
DUBAI_TZ = ZoneInfo("Asia/Dubai")

REQUIRED_QUESTIONS_COLUMNS = [
    "ID", "Axis", "Question", "Type", "Allow_New", "Options"
]

DRAFT_HEADERS = [
    "User Code",
    "Entity Name",
    "Submission ID",
    "Updated At (UAE)",
    "Draft JSON",
]


@st.cache_resource
def get_worksheets():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=scopes,
    )
    client = gspread.authorize(credentials)
    spreadsheet_id = st.secrets["google_sheets"]["spreadsheet_id"]
    sh = client.open_by_key(spreadsheet_id)

    questions_ws = sh.worksheet(QUESTIONS_TAB)
    responses_ws = sh.worksheet(RESPONSES_TAB)
    users_ws = sh.worksheet(USERS_TAB)
