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

    # إنشاء ورقة المسودات تلقائياً عند أول تشغيل.
    try:
        drafts_ws = sh.worksheet(DRAFTS_TAB)
    except gspread.WorksheetNotFound:
        drafts_ws = sh.add_worksheet(
            title=DRAFTS_TAB,
            rows=1000,
            cols=max(10, len(DRAFT_HEADERS)),
        )
        drafts_ws.update(
            range_name="A1",
            values=[DRAFT_HEADERS],
            value_input_option="RAW",
        )

    # التأكد من وجود عناوين الأعمدة.
    if not drafts_ws.row_values(1):
        drafts_ws.update(
            range_name="A1",
            values=[DRAFT_HEADERS],
            value_input_option="RAW",
        )

    # سجل نسخ احتياطية متتابعة، حتى يمكن استرجاع نسخة أقدم إذا استُبدلت
    # المسودة الحالية بإجابات فارغة أو غير مكتملة عن طريق الخطأ.
    try:
        draft_history_ws = sh.worksheet(DRAFT_HISTORY_TAB)
    except gspread.WorksheetNotFound:
        draft_history_ws = sh.add_worksheet(
            title=DRAFT_HISTORY_TAB,
            rows=3000,
            cols=max(10, len(DRAFT_HEADERS)),
        )
        draft_history_ws.update(
            range_name="A1",
            values=[DRAFT_HEADERS],
            value_input_option="RAW",
        )
    if not draft_history_ws.row_values(1):
        draft_history_ws.update(
            range_name="A1",
            values=[DRAFT_HEADERS],
            value_input_option="RAW",
        )

    return {
        "questions": questions_ws,
        "responses": responses_ws,
        "users": users_ws,
        "drafts": drafts_ws,
        "draft_history": draft_history_ws,
    }


try:
    sheets = get_worksheets()
    questions_ws = sheets["questions"]
    responses_ws = sheets["responses"]
    users_ws = sheets["users"]
    drafts_ws = sheets["drafts"]
    draft_history_ws = sheets["draft_history"]
except Exception as e:
    st.error(f"خطأ في الاتصال بقاعدة البيانات: {e}")
    st.stop()


def sheets_write_with_retry(operation, attempts=4):
    """إعادة المحاولة عند حدوث ضغط مؤقت أو خطأ 429 من Google Sheets."""
    last_error = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            message = str(exc).lower()
            retryable = (
                "429" in message
                or "rate limit" in message
                or "500" in message
                or "503" in message
                or "timed out" in message
            )
            if not retryable or attempt == attempts - 1:
                raise
            time.sleep(1.5 * (2 ** attempt))
    raise last_error


def now_uae():
    return datetime.now(DUBAI_TZ).strftime("%Y-%m-%d %H:%M:%S")


# =========================================================
# 3. تسجيل الدخول والمصادقة
# =========================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "entity_name" not in st.session_state:
    st.session_state.entity_name = ""
if "user_code" not in st.session_state:
    st.session_state.user_code = ""
if "allow_edit" not in st.session_state:
    st.session_state.allow_edit = True


@st.cache_data(ttl=300)
def load_users():
    records = users_ws.get_all_records()
    users_dict = {}
    for row in records:
        code = str(row.get("Code", "")).strip()
        entity = str(row.get("Entity_Name", "")).strip()
        status = str(row.get("Status", "active")).strip().lower()
        allow_edit_val = (
            str(row.get("Allow_Edit", "yes")).strip().lower()
            in {"yes", "y", "true", "1", "نعم"}
        )
        if code and entity and status == "active":
            users_dict[code] = {
                "entity": entity,
                "allow_edit": allow_edit_val,
            }
    return users_dict


if not st.session_state.authenticated:
    st.title("🔐 تسجيل الدخول لنموذج تقييم المخاطر")
    st.write("يرجى إدخال رمز الدخول (الكود السري) الخاص بجهتك للمتابعة.")

    with st.form("login_form"):
        entered_code = st.text_input("رمز الدخول:", type="password")
        submit_login = st.form_submit_button(
            "دخول",
            use_container_width=True,
        )

    if submit_login:
        try:
            valid_users = load_users()
            cleaned_code = entered_code.strip()
            if cleaned_code in valid_users:
                st.session_state.authenticated = True
                st.session_state.entity_name = valid_users[cleaned_code]["entity"]
                st.session_state.user_code = cleaned_code
                st.session_state.allow_edit = valid_users[cleaned_code]["allow_edit"]
                st.rerun()
            else:
                st.error("رمز الدخول غير صحيح أو غير مفعل.")
        except Exception as auth_err:
            st.error(f"حدث خطأ أثناء تسجيل الدخول: {auth_err}")
    st.stop()


# =========================================================
# 4. جلب الأسئلة والإجابات السابقة والمسودات
# =========================================================
def parse_yes_no(value):
    return str(value).strip().lower() in {"yes", "y", "true", "1", "نعم"}


def parse_options(value):
    if value is None:
        return []
    return [opt.strip() for opt in str(value).split("|") if opt.strip()]


@st.cache_data(ttl=300)
def load_questions():
    records = questions_ws.get_all_records()
    if not records:
        return []

    headers = list(records[0].keys())
    missing = [col for col in REQUIRED_QUESTIONS_COLUMNS if col not in headers]
    if missing:
        raise ValueError(f"أعمدة مفقودة في الأسئلة: {', '.join(missing)}")

    questions_list = []
    for sheet_row, record in enumerate(records, start=2):
        qid = int(record["ID"])
        questions_list.append({
            "id": qid,
            "axis": str(record["Axis"]).strip(),
            "question": str(record["Question"]).strip(),
            "type": str(record["Type"]).strip().lower(),
            "allow_new": parse_yes_no(record.get("Allow_New", "no")),
            "options": parse_options(record.get("Options", "")),
            "sheet_row": sheet_row,
        })
    return questions_list


@st.cache_data(ttl=120)
def get_existing_response(user_code):
    try:
        data = responses_ws.get_all_records()
        for idx, row in enumerate(data, start=2):
            if str(row.get("User Code", "")).strip() == str(user_code).strip():
                return row, idx
    except Exception:
        pass
    return None, None


def get_existing_draft(user_code):
    """إرجاع أحدث نسخة محفوظة من المسودة الحالية أو سجل النسخ الاحتياطية."""
    try:
        matches = []
        for worksheet in (drafts_ws, draft_history_ws):
            records = worksheet.get_all_records()
            for idx, row in enumerate(records, start=2):
                if str(row.get("User Code", "")).strip() == str(user_code).strip():
                    updated_at = str(row.get("Updated At (UAE)", "")).strip()
                    raw_json = row.get("Draft JSON", "")
                    if raw_json:
                        try:
                            payload = json.loads(raw_json)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        answer_values = payload.get("answers", {}) or {}
                        answered_count = sum(
                            1 for key, value in answer_values.items()
                            if str(key).startswith("Q") and str(value).strip()
                        )
                        matches.append((answered_count, updated_at, idx, payload))

        if not matches:
            return None, None, None

        # عند وجود نسخ متعددة، نقترح النسخة الأكثر احتفاظاً بالإجابات؛
        # وعند تساوي عدد الإجابات نختار الأحدث زمنياً.
        answered_count, updated_at, row_idx, payload = max(
            matches, key=lambda item: (item[0], item[1], item[2])
        )
        return payload, row_idx, updated_at
    except Exception as exc:
        st.warning(f"تعذر تحميل المسودة السابقة: {exc}")
    return None, None, None


try:
    questions = load_questions()
except Exception as error:
    st.error(f"تعذر تحميل الأسئلة: {error}")
    st.stop()

axes = list(dict.fromkeys(q["axis"] for q in questions))

existing_response, existing_row_idx = get_existing_response(
    st.session_state.user_code
)
existing_draft, existing_draft_row_idx, existing_draft_updated_at = get_existing_draft(
    st.session_state.user_code
)

# إذا وجدت مسودة، تكون هي المصدر الأحدث. وإلا تُحمّل آخر إجابة معتمدة.
if existing_draft is not None:
    saved_answers = existing_draft.get("answers", {})
    saved_axis_status = existing_draft.get("axis_status", {})
    saved_submission_id = existing_draft.get("submission_id")
else:
    saved_answers = existing_response or {}
    saved_axis_status = {}
    saved_submission_id = (
        existing_response.get("Submission ID")
        if existing_response else None
    )


# =========================================================
# 5. تهيئة حالة الإجابات في Streamlit Session State
# =========================================================
if "initialized_answers" not in st.session_state:
    st.session_state.initialized_answers = True

    for q in questions:
        qid = q["id"]
        answer_key = f"Q{qid}: {q['question']}"
        old_val = str(saved_answers.get(answer_key, "") or "")

        if q["type"] == "single":
            custom_label = "➕ إضافة خيار آخر"
            if old_val in q["options"]:
                st.session_state[f"q_{qid}"] = old_val
            elif old_val and q["allow_new"]:
                st.session_state[f"q_{qid}"] = custom_label
                st.session_state[f"custom_single_{qid}"] = old_val
            else:
                st.session_state[f"q_{qid}"] = "— اختر —"

        elif q["type"] == "multiple":
            def_multi = (
                [v.strip() for v in old_val.split("|") if v.strip()]
                if old_val else []
            )
            std_options = q["options"]
            custom_saved = [v for v in def_multi if v not in std_options]
            st.session_state[f"q_{qid}"] = [
                v for v in def_multi if v in std_options
            ]
            if q["allow_new"]:
                st.session_state[f"custom_multi_{qid}"] = ", ".join(custom_saved)

for axis_name in axes:
    toggle_key = f"axis_toggle_{axis_name}"
    if toggle_key not in st.session_state:
        old_status = saved_axis_status.get(
            axis_name,
            saved_answers.get(f"[حالة القسم] {axis_name}", "ينطبق"),
        )
        st.session_state[toggle_key] = old_status != "لا ينطبق"

if "axis_pages" not in st.session_state:
    st.session_state.axis_pages = {ax: 0 for ax in axes}
else:
    for ax in axes:
        st.session_state.axis_pages.setdefault(ax, 0)

if "submission_id" not in st.session_state:
    st.session_state.submission_id = (
        saved_submission_id or str(uuid.uuid4())
    )

if "questionnaire_submitted" not in st.session_state:
    st.session_state.questionnaire_submitted = False

if "active_axis" not in st.session_state:
    st.session_state.active_axis = axes[0] if axes else ""

if "draft_saved_at" not in st.session_state:
    st.session_state.draft_saved_at = ""


# =========================================================
# 6. بناء الإجابات والتحقق منها
# =========================================================
def collect_answers():
    answers = {}
    axis_errors = {}
    new_options = {}
    axis_status = {}

    for axis_name in axes:
        axis_errors[axis_name] = []
        applicable = st.session_state.get(
            f"axis_toggle_{axis_name}", True
        )
        status = "ينطبق" if applicable else "لا ينطبق"
        axis_status[axis_name] = status
        answers[f"[حالة القسم] {axis_name}"] = status

        axis_questions = [
            q for q in questions if q["axis"] == axis_name
        ]

        if not applicable:
            for q in axis_questions:
                answers[f"Q{q['id']}: {q['question']}"] = "لا ينطبق"
            continue

        for q in axis_questions:
            qid = q["id"]
            answer_key = f"Q{qid}: {q['question']}"

            if q["type"] == "single":
                selected = st.session_state.get(
                    f"q_{qid}", "— اختر —"
                )

                if selected == "➕ إضافة خيار آخر" and q["allow_new"]:
                    custom_value = st.session_state.get(
                        f"custom_single_{qid}", ""
                    ).strip()
                    answers[answer_key] = custom_value

                    if not custom_value:
                        axis_errors[axis_name].append(
                            f"السؤال {qid} غير مكتمل."
                        )
                    else:
                        new_options[qid] = custom_value

                elif selected == "— اختر —":
                    answers[answer_key] = ""
                    axis_errors[axis_name].append(
                        f"السؤال {qid} غير مكتمل."
                    )
                else:
                    answers[answer_key] = selected

            elif q["type"] == "multiple":
                selected_options = st.session_state.get(f"q_{qid}", [])
                final_values = list(selected_options)

                if q["allow_new"]:
                    custom_input = st.session_state.get(
                        f"custom_multi_{qid}", ""
                    ).strip()
                    if custom_input:
                        for item in custom_input.split(","):
                            item = item.strip()
                            if item and item not in final_values:
                                final_values.append(item)
                                if item.casefold() not in {
                                    opt.casefold() for opt in q["options"]
                                }:
                                    new_options[qid] = item

                answers[answer_key] = " | ".join(final_values)
                if not final_values:
                    axis_errors[axis_name].append(
                        f"السؤال {qid} غير مكتمل."
                    )

            else:
                # دعم الأنواع غير المعروفة دون إسقاط الإجابة.
                answers[answer_key] = str(
                    st.session_state.get(f"q_{qid}", "")
                )

    return answers, axis_errors, new_options, axis_status


def apply_draft_to_session(payload):
    """تحميل إجابات المسودة إلى عناصر الواجهة قبل إعادة رسمها."""
    restored_answers = payload.get("answers", {}) or {}
    restored_status = payload.get("axis_status", {}) or {}

    for q in questions:
        qid = q["id"]
        answer_key = f"Q{qid}: {q['question']}"
        old_val = str(restored_answers.get(answer_key, "") or "")

        if q["type"] == "single":
            custom_label = "➕ إضافة خيار آخر"
            if old_val in q["options"]:
                st.session_state[f"q_{qid}"] = old_val
                st.session_state.pop(f"custom_single_{qid}", None)
            elif old_val and q["allow_new"]:
                st.session_state[f"q_{qid}"] = custom_label
                st.session_state[f"custom_single_{qid}"] = old_val
            else:
                st.session_state[f"q_{qid}"] = "— اختر —"
                st.session_state.pop(f"custom_single_{qid}", None)

        elif q["type"] == "multiple":
            values = [v.strip() for v in old_val.split("|") if v.strip()]
            st.session_state[f"q_{qid}"] = [v for v in values if v in q["options"]]
            if q["allow_new"]:
                custom = [v for v in values if v not in q["options"]]
                st.session_state[f"custom_multi_{qid}"] = ", ".join(custom)

    for axis_name in axes:
        status = restored_status.get(
            axis_name,
            restored_answers.get(f"[حالة القسم] {axis_name}", "ينطبق"),
        )
        st.session_state[f"axis_toggle_{axis_name}"] = status != "لا ينطبق"

    restored_id = payload.get("submission_id")
    if restored_id:
        st.session_state.submission_id = restored_id

    st.session_state.axis_pages = {ax: 0 for ax in axes}
    st.session_state.active_axis = axes[0] if axes else ""
    if axes:
        st.session_state["axis_selector"] = axes[0]
    st.session_state.draft_saved_at = now_uae()
    st.session_state.restore_message = True


def save_draft(answers, axis_status):
    """حفظ نسخة عمل واحدة لكل جهة في ورقة Drafts."""
    payload = {
        "submission_id": st.session_state.submission_id,
        "answers": answers,
        "axis_status": axis_status,
    }
    json_value = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    def write():
        records = drafts_ws.get_all_records()
        matching_rows = [
            (str(row.get("Updated At (UAE)", "")).strip(), idx)
            for idx, row in enumerate(records, start=2)
            if str(row.get("User Code", "")).strip() == str(
                st.session_state.user_code
            ).strip()
        ]
        target_row = max(matching_rows, default=("", None))[1]

        values = [[
            st.session_state.user_code,
            st.session_state.entity_name,
            st.session_state.submission_id,
            now_uae(),
            json_value,
        ]]

        if target_row:
            old_values = drafts_ws.row_values(target_row)
            old_json = old_values[4] if len(old_values) >= 5 else ""
            if old_json and old_json != json_value:
                # لا نكرر النسخة نفسها؛ نحتفظ فقط بالتغييرات الفعلية.
                draft_history_ws.append_row(
                    old_values[:5],
                    value_input_option="RAW",
                    table_range="A1",
                )
            drafts_ws.update(
                range_name=f"A{target_row}:E{target_row}",
                values=values,
                value_input_option="RAW",
            )
        else:
            drafts_ws.append_row(
                values[0],
                value_input_option="RAW",
                table_range="A1",
            )

    sheets_write_with_retry(write)
    st.session_state.draft_saved_at = now_uae()


def delete_draft(user_code):
    """حذف المسودة ونسخها الاحتياطية بعد اعتماد الإرسال النهائي."""
    def delete():
        for worksheet in (drafts_ws, draft_history_ws):
            records = worksheet.get_all_records()
            matching_rows = [
                idx for idx, row in enumerate(records, start=2)
                if str(row.get("User Code", "")).strip() == str(user_code).strip()
            ]
            for idx in reversed(matching_rows):
                worksheet.delete_rows(idx)

    sheets_write_with_retry(delete)


def update_new_options(new_options_dict):
    if not new_options_dict:
        return

    sheet_headers = questions_ws.row_values(1)
    if "Options" not in sheet_headers:
        raise ValueError("عمود Options غير موجود في ورقة Questions.")

    options_col = sheet_headers.index("Options") + 1
    all_records = questions_ws.get_all_records()
    updates = []

    for sheet_row, record in enumerate(all_records, start=2):
        qid = int(record["ID"])
        if qid not in new_options_dict:
            continue

        old_options = parse_options(record.get("Options", ""))
        new_option = new_options_dict[qid].strip()

        if new_option and new_option.casefold() not in {
            item.casefold() for item in old_options
        }:
            updated_options = old_options + [new_option]
            updates.append({
                "range": rowcol_to_a1(sheet_row, options_col),
                "values": [[" | ".join(updated_options)]],
            })

    if updates:
        sheets_write_with_retry(
            lambda: questions_ws.batch_update(
                updates,
                value_input_option="RAW",
            )
        )


def save_or_update_response(answer_data, row_idx=None):
    existing_headers = responses_ws.row_values(1)
    headers = list(existing_headers)

    for key in answer_data:
        if key not in headers:
            headers.append(key)

    # تحديث صف العناوين مرة واحدة عند الحاجة.
    if headers != existing_headers:
        sheets_write_with_retry(
            lambda: responses_ws.update(
                range_name="A1",
                values=[headers],
                value_input_option="RAW",
            )
        )

    row_values = [answer_data.get(header, "") for header in headers]

    if row_idx:
        end_cell = rowcol_to_a1(row_idx, len(headers))
        sheets_write_with_retry(
            lambda: responses_ws.update(
                range_name=f"A{row_idx}:{end_cell}",
                values=[row_values],
                value_input_option="USER_ENTERED",
            )
        )
    else:
        sheets_write_with_retry(
            lambda: responses_ws.append_row(
                row_values,
                value_input_option="USER_ENTERED",
                table_range="A1",
            )
        )


# =========================================================
# 7. واجهة الاستبيان والتنقل بين الأقسام والصفحات
# =========================================================
st.title("📋 نموذج تقييم المخاطر")
st.caption(f"الجهة: {st.session_state.entity_name}")

# استرجاع يدوي آمن: معاينة أولاً ثم تأكيد قبل استبدال الإجابات الحالية.
if not st.session_state.questionnaire_submitted:
    if st.button("↩️ البحث عن مسودة محفوظة", use_container_width=True):
        try:
            latest_payload, latest_row_idx, latest_timestamp = get_existing_draft(
                st.session_state.user_code
            )
            if latest_payload is None:
                st.session_state.pending_restore_draft = None
                st.warning("لم نعثر على مسودة محفوظة لهذا الرمز.")
            else:
                st.session_state.pending_restore_draft = {
                    "payload": latest_payload,
                    "row_idx": latest_row_idx,
                    "updated_at": latest_timestamp,
                }
                st.rerun()
        except Exception as restore_error:
            st.error(f"تعذر البحث عن المسودة: {restore_error}")

    pending_restore = st.session_state.get("pending_restore_draft")
    if pending_restore:
        draft_answers = pending_restore["payload"].get("answers", {}) or {}
        answered_count = sum(
            1 for q in questions
            if str(draft_answers.get(f"Q{q['id']}: {q['question']}", "")).strip()
        )
        st.info(
            f"عُثر على نسخة محفوظة بتاريخ **{pending_restore['updated_at'] or 'تاريخ غير متاح'}** "
            f"وتحتوي على إجابات لـ **{answered_count}** سؤالاً. "
            "لن يتم استبدال إجابات الشاشة الحالية إلا بعد تأكيدك."
        )
        confirm_col, cancel_col = st.columns(2)
        with confirm_col:
            if st.button("استرجاع هذه المسودة", type="primary", use_container_width=True):
                apply_draft_to_session(pending_restore["payload"])
                st.session_state.pending_restore_draft = None
                st.rerun()
        with cancel_col:
            if st.button("إلغاء", use_container_width=True):
                st.session_state.pending_restore_draft = None
                st.rerun()

if st.session_state.pop("restore_message", False):
    st.success("تم استرجاع المسودة بنجاح. يمكنك متابعة الإجابة من حيث توقفت.")

if existing_response and not st.session_state.allow_edit:
    st.warning("⚠️ إجابات جهتك مقفلة من الإعدادات ولا يمكن تعديلها.")
    st.json(existing_response)
    st.stop()

if not axes:
    st.warning("لا توجد أقسام أو أسئلة معرفة في ورقة Questions.")
    st.stop()

QUESTIONS_PER_PAGE = 5

# اختيار قسم واحد بدلاً من st.tabs:
# Streamlit لا يوفر حدثاً مباشراً عند الضغط على تبويب st.tabs،
# أما selectbox فيسمح بالتقاط تغيير القسم وحفظ المسودة أثناء إعادة التشغيل.
selected_axis = st.selectbox(
    "اختر القسم:",
    options=axes,
    index=axes.index(st.session_state.active_axis)
    if st.session_state.active_axis in axes else 0,
    key="axis_selector",
)

st.session_state.active_axis = selected_axis

# جمع جميع الإجابات من Session State قبل عرض القسم الحالي.
answers, axis_errors, new_options, axis_status = collect_answers()

# حفظ المسودة تلقائياً عند كل إعادة تشغيل للتطبيق بعد أي تفاعل.
# يشمل ذلك تغيير الإجابة، التنقل بين الصفحات، وتغيير القسم.
# يتم تخزين المسودة في صف واحد لكل جهة داخل ورقة Drafts.
try:
    if (
        not st.session_state.questionnaire_submitted
        and not st.session_state.get("pending_restore_draft")
    ):
        save_draft(answers, axis_status)
except Exception as draft_error:
    st.warning(
        "تعذر حفظ المسودة تلقائياً حالياً. "
        f"يرجى استخدام زر حفظ المسودة. التفاصيل: {draft_error}"
    )

# شريط التقدم
completed_axes = 0
for ax in axes:
    if not axis_status.get(ax):
        continue
    ax_qs = [q for q in questions if q["axis"] == ax]
    if not axis_status[ax] == "لا ينطبق":
        complete = True
        for q in ax_qs:
            val = answers.get(f"Q{q['id']}: {q['question']}", "")
            if not val:
                complete = False
                break
        if complete:
            completed_axes += 1
    else:
        completed_axes += 1

st.progress(
    completed_axes / len(axes) if axes else 0,
    text=f"الأقسام المكتملة: {completed_axes}/{len(axes)}",
)

# قائمة توضح حالة كل قسم.
status_labels = []
for ax in axes:
    if axis_status.get(ax) == "لا ينطبق":
        status_labels.append(f"🚫 {ax}")
    else:
        ax_qs = [q for q in questions if q["axis"] == ax]
        is_complete = all(
            answers.get(f"Q{q['id']}: {q['question']}", "")
            for q in ax_qs
        )
        status_labels.append(
            f"{'✅' if is_complete else '⏳'} {ax}"
        )

st.caption("حالة الأقسام: " + "  |  ".join(status_labels))
st.divider()

axis_questions = [q for q in questions if q["axis"] == selected_axis]
current_page = st.session_state.axis_pages.get(selected_axis, 0)
total_questions = len(axis_questions)
total_pages = max(
    1,
    (total_questions + QUESTIONS_PER_PAGE - 1) // QUESTIONS_PER_PAGE,
)
current_page = min(current_page, total_pages - 1)
st.session_state.axis_pages[selected_axis] = current_page

st.subheader(selected_axis)

st.toggle(
    "ينطبق هذا القسم على جهتي",
    key=f"axis_toggle_{selected_axis}",
    disabled=st.session_state.questionnaire_submitted,
)

applicable = st.session_state.get(
    f"axis_toggle_{selected_axis}", True
)

if not applicable:
    st.info("تم استبعاد هذا القسم من التقييم، وسيتم حفظ إجاباته كـ «لا ينطبق».")
else:
    start_idx = current_page * QUESTIONS_PER_PAGE
    end_idx = min(start_idx + QUESTIONS_PER_PAGE, total_questions)
    page_questions = axis_questions[start_idx:end_idx]

    st.caption(f"الصفحة ({current_page + 1} من {total_pages})")

    for q in page_questions:
        qid = q["id"]
        st.markdown(f"**{qid}. {q['question']}**")

        if q["type"] == "single":
            custom_label = "➕ إضافة خيار آخر"
            display_options = ["— اختر —"] + q["options"]
            if q["allow_new"]:
                display_options.append(custom_label)

            # حماية القيمة القديمة إذا تغيرت خيارات السؤال في Google Sheets.
            widget_key = f"q_{qid}"
            if st.session_state.get(widget_key) not in display_options:
                st.session_state[widget_key] = "— اختر —"

            st.radio(
                "اختر:",
                options=display_options,
                key=widget_key,
                horizontal=True,
                label_visibility="collapsed",
                disabled=st.session_state.questionnaire_submitted,
            )

            if (
                st.session_state.get(widget_key) == custom_label
                and q["allow_new"]
            ):
                st.text_input(
                    "اكتب الخيار:",
                    key=f"custom_single_{qid}",
                    label_visibility="collapsed",
                    disabled=st.session_state.questionnaire_submitted,
                )

        elif q["type"] == "multiple":
            valid_selected = st.session_state.get(f"q_{qid}", [])
            st.session_state[f"q_{qid}"] = [
                value for value in valid_selected if value in q["options"]
            ]

            st.multiselect(
                "اختر:",
                options=q["options"],
                key=f"q_{qid}",
                label_visibility="collapsed",
                disabled=st.session_state.questionnaire_submitted,
            )

            if q["allow_new"]:
                st.text_input(
                    "أضف خياراً آخر (افصل بين الخيارات بفاصلة):",
                    key=f"custom_multi_{qid}",
                    label_visibility="collapsed",
                    disabled=st.session_state.questionnaire_submitted,
                )
        else:
            st.warning(
                f"نوع السؤال ({q['type']}) غير مدعوم كعنصر إدخال حالياً."
            )

        st.markdown("---")


# أزرار التنقل وحفظ القسم.
col_prev, col_save, col_next = st.columns(3)

with col_prev:
    if st.button(
        "⬅️ السابق",
        use_container_width=True,
        disabled=(current_page == 0 or st.session_state.questionnaire_submitted),
    ):
        # حفظ فوري قبل الانتقال.
        current_answers, _, _, current_status = collect_answers()
        save_draft(current_answers, current_status)
        st.session_state.axis_pages[selected_axis] = max(0, current_page - 1)
        st.rerun()

with col_save:
    if st.button(
        "💾 حفظ القسم",
        use_container_width=True,
        disabled=st.session_state.questionnaire_submitted,
    ):
        current_answers, _, _, current_status = collect_answers()
        save_draft(current_answers, current_status)
        st.success("تم حفظ المسودة في قاعدة البيانات.")
        st.rerun()

with col_next:
    if st.button(
        "التالي ➡️",
        use_container_width=True,
        disabled=(
            current_page >= total_pages - 1
            or st.session_state.questionnaire_submitted
        ),
    ):
        current_answers, _, _, current_status = collect_answers()
        save_draft(current_answers, current_status)
        st.session_state.axis_pages[selected_axis] = min(
            total_pages - 1,
            current_page + 1,
        )
        st.rerun()


# حفظ المسودة يدوياً في أي وقت.
if not st.session_state.questionnaire_submitted:
    if st.button("💾 حفظ جميع الإجابات كمسودة", use_container_width=True):
        current_answers, _, _, current_status = collect_answers()
        try:
            save_draft(current_answers, current_status)
            st.success("تم حفظ جميع الإجابات كمسودة. يمكنك العودة لاحقاً.")
        except Exception as exc:
            st.error(f"تعذر حفظ المسودة: {exc}")


# =========================================================
# 8. الاعتماد والإرسال النهائي
# =========================================================
st.divider()
st.subheader("الاعتماد النهائي")

if st.session_state.questionnaire_submitted:
    st.success("تم إرسال الاستبيان بنجاح.")
else:
    st.write(
        "بعد استكمال جميع الأقسام، يمكنك اعتماد الإجابات وإرسالها نهائياً."
    )

    if st.button(
        "✅ الاعتماد والإرسال النهائي",
        type="primary",
        use_container_width=True,
        disabled=st.session_state.questionnaire_submitted,
    ):
        final_answers, final_errors, final_new_options, final_axis_status = (
            collect_answers()
        )
        all_errors = [
            (axis_name, error)
            for axis_name, errors in final_errors.items()
            for error in errors
        ]

        if all_errors:
            st.error("يرجى استكمال الأسئلة التالية قبل الإرسال النهائي:")
            for axis_name, error in all_errors:
                st.write(f"- **{axis_name}:** {error}")
        else:
            try:
                final_data = {
                    "Submission ID": st.session_state.submission_id,
                    "Entity Name": st.session_state.entity_name,
                    "User Code": st.session_state.user_code,
                    "Timestamp (UAE)": now_uae(),
                    "Submission Status": "معتمد",
                }
                final_data.update(final_answers)

                save_or_update_response(
                    final_data,
                    row_idx=existing_row_idx,
                )
                update_new_options(final_new_options)
                delete_draft(st.session_state.user_code)

                get_existing_response.clear()
                load_questions.clear()

                st.session_state.questionnaire_submitted = True
                st.success("تم الإرسال النهائي بنجاح!")
                st.rerun()

            except Exception as err:
                st.error(f"حدث خطأ أثناء الإرسال النهائي: {err}")


# =========================================================
# 9. تسجيل الخروج
# =========================================================
st.sidebar.subheader(f"🏢 {st.session_state.entity_name}")
st.sidebar.caption("يتم حفظ المسودة تلقائياً أثناء استخدام النموذج.")
if st.session_state.draft_saved_at:
    st.sidebar.caption(
        f"آخر حفظ للمسودة: {st.session_state.draft_saved_at}"
    )

if st.sidebar.button("تسجيل الخروج", use_container_width=True):
    # حفظ أخير قبل إنهاء الجلسة.
    try:
        logout_answers, _, _, logout_status = collect_answers()
        if not st.session_state.questionnaire_submitted:
            save_draft(logout_answers, logout_status)
    except Exception:
        pass

    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()
