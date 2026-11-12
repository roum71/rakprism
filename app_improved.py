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
    .main, [data-testid="stAppViewContainer"] {
        direction: rtl;
    }
    label, p, h1, h2, h3, h4,
    [data-testid="stMarkdownContainer"] {
        text-align: right !important;
        direction: rtl !important;
    }
    div[data-testid="stRadio"] > label,
    div[data-testid="stMultiSelect"] > label,
    div[data-testid="stTextInput"] > label {
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
    hr {
        margin: 0.8rem 0 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# 2. الاتصال بقاعدة البيانات (مُحسّن لمنع خطأ 429)
# =========================================================
QUESTIONS_TAB = "Questions"
RESPONSES_TAB = "Responses"
USERS_TAB = "Users"
DUBAI_TZ = ZoneInfo("Asia/Dubai")

REQUIRED_QUESTIONS_COLUMNS = [
    "ID",
    "Axis",
    "Question",
    "Type",
    "Allow_New",
    "Options",
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
    
    return {
        "questions": sh.worksheet(QUESTIONS_TAB),
        "responses": sh.worksheet(RESPONSES_TAB),
        "users": sh.worksheet(USERS_TAB)
    }

try:
    sheets = get_worksheets()
    questions_ws = sheets["questions"]
    responses_ws = sheets["responses"]
    users_ws = sheets["users"]
except Exception as e:
    st.error(f"خطأ في الاتصال بقاعدة البيانات: {e}")
    st.stop()


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
    for r in records:
        code = str(r.get("Code", "")).strip()
        entity = str(r.get("Entity_Name", "")).strip()
        status = str(r.get("Status", "active")).strip().lower()
        allow_edit_val = str(r.get("Allow_Edit", "yes")).strip().lower() in {"yes", "y", "true", "1", "نعم"}
        if code and entity and status == "active":
            users_dict[code] = {"entity": entity, "allow_edit": allow_edit_val}
    return users_dict


if not st.session_state.authenticated:
    st.title("🔐 تسجيل الدخول لنموذج تقييم المخاطر")
    st.write("يرجى إدخال رمز الدخول (الكود السري) الخاص بجهتك للمتابعة.")
    with st.form("login_form"):
        entered_code = st.text_input("رمز الدخول:", type="password")
        submit_login = st.form_submit_button("دخول", use_container_width=True)
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
                st.error(f"حدث خطأ: {auth_err}")
    st.stop()


# =========================================================
# 4. جلب الأسئلة والإجابات السابقة
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
    missing = [c for c in REQUIRED_QUESTIONS_COLUMNS if c not in headers]
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


try:
    questions = load_questions()
except Exception as error:
    st.error(f"تعذر تحميل الأسئلة: {error}")
    st.stop()

axes = list(dict.fromkeys(q["axis"] for q in questions))


@st.cache_data(ttl=120)
def get_existing_response(user_code):
    try:
        data = responses_ws.get_all_records()
        if not data:
            return None, None
            
        headers = list(data[0].keys())
        user_code_col = "User Code" if "User Code" in headers else None
        if not user_code_col:
            return None, None
            
        for idx, row in enumerate(data, start=2):
            if str(row.get(user_code_col, "")).strip() == str(user_code).strip():
                return row, idx
    except Exception:
        pass
    return None, None


existing_response, existing_row_idx = get_existing_response(st.session_state.user_code)


# =========================================================
# 5. تهيئة الحالة لضمان عدم ضياع الإجابات (Session State Initialization)
# =========================================================
if "initialized_answers" not in st.session_state:
    st.session_state.initialized_answers = True
    for q in questions:
        qid = q["id"]
        answer_key = f"Q{qid}: {q['question']}"
        old_val = existing_response.get(answer_key, "") if existing_response else ""
        
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
            def_multi = [v.strip() for v in old_val.split("|") if v.strip()] if old_val else []
            std_options = q["options"]
            custom_saved = [v for v in def_multi if v not in std_options]
            st.session_state[f"q_{qid}"] = [v for v in def_multi if v in std_options]
            if q["allow_new"]:
                st.session_state[f"custom_multi_{qid}"] = ", ".join(custom_saved)

for ax in axes:
    if f"axis_toggle_{ax}" not in st.session_state:
        axis_status_key = f"[حالة القسم] {ax}"
        default_val = True
        if existing_response and existing_response.get(axis_status_key) == "لا ينطبق":
            default_val = False
        st.session_state[f"axis_toggle_{ax}"] = default_val


# =========================================================
# 6. دوال الحفظ وتحديث الخيارات
# =========================================================
def update_new_options(new_options_dict):
    if not new_options_dict:
        return
    sheet_headers = questions_ws.row_values(1)
    options_col = sheet_headers.index("Options") + 1
    all_records = questions_ws.get_all_records()
    updates = []
    for sheet_row, record in enumerate(all_records, start=2):
        qid = int(record["ID"])
        if qid not in new_options_dict:
            continue
        old_opts = parse_options(record.get("Options", ""))
        new_opt = new_options_dict[qid].strip()
        if new_opt and new_opt.casefold() not in {i.casefold() for i in old_opts}:
            updated = old_opts + [new_opt]
            updates.append({"range": rowcol_to_a1(sheet_row, options_col), "values": [[" | ".join(updated)]]})
    if updates:
        questions_ws.batch_update(updates, value_input_option="RAW")


def save_or_update_response(answer_data, row_idx=None):
    existing_headers = responses_ws.row_values(1)
    headers = list(existing_headers)
    for key in answer_data:
        if key not in headers:
            headers.append(key)
    responses_ws.update(range_name="A1", values=[headers], value_input_option="RAW")
    row_vals = [answer_data.get(h, "") for h in headers]
    if row_idx:
        responses_ws.update(range_name=f"A{row_idx}:" + rowcol_to_a1(row_idx, len(headers)), values=[row_vals], value_input_option="USER_ENTERED")
    else:
        responses_ws.append_row(row_vals, value_input_option="USER_ENTERED", table_range="A1")


# =========================================================
# 7. واجهة الاستبيان
# =========================================================
st.title("📋 نموذج تقييم المخاطر")

if existing_response and not st.session_state.allow_edit:
    st.warning("⚠️ إجاباتك مقفلة من الإعدادات ولا يمكن تعديلها:")
    st.json(existing_response)
    st.stop()

if "questionnaire_submitted" not in st.session_state:
    st.session_state.questionnaire_submitted = False
if "submission_id" not in st.session_state:
    st.session_state.submission_id = existing_response.get("Submission ID", str(uuid.uuid4())) if existing_response else str(uuid.uuid4())
if "saved_axes" not in st.session_state:
    st.session_state.saved_axes = set(axes) if existing_response else set()
if "axis_pages" not in st.session_state:
    st.session_state.axis_pages = {ax: 0 for ax in axes}

# --- تجميع الإجابات والتحقق منها من الـ session_state مباشرة لضمان عدم ضياع أي شيء ---
answers = {}
axis_errors = {}
new_options = {}

for axis_name in axes:
    axis_errors[axis_name] = []
    applicable = st.session_state.get(f"axis_toggle_{axis_name}", True)
    answers[f"[حالة القسم] {axis_name}"] = "ينطبق" if applicable else "لا ينطبق"

    axis_questions = [q for q in questions if q["axis"] == axis_name]
    if not applicable:
        for q in axis_questions:
            answers[f"Q{q['id']}: {q['question']}"] = "لا ينطبق"
    else:
        for q in axis_questions:
            qid = q["id"]
            answer_key = f"Q{qid}: {q['question']}"
            
            if q["type"] == "single":
                sel = st.session_state.get(f"q_{qid}", "— اختر —")
                if sel == "➕ إضافة خيار آخر" and q["allow_new"]:
                    custom_val = st.session_state.get(f"custom_single_{qid}", "").strip()
                    answers[answer_key] = custom_val
                    if not custom_val:
                        axis_errors[axis_name].append(f"السؤال {qid} ناقص.")
                    else:
                        new_options[qid] = custom_val
                elif sel == "— اختر —":
                    answers[answer_key] = ""
                    axis_errors[axis_name].append(f"السؤال {qid} ناقص.")
                else:
                    answers[answer_key] = sel

            elif q["type"] == "multiple":
                selected_opts = st.session_state.get(f"q_{qid}", [])
                final_vals = list(selected_opts)
                if q["allow_new"]:
                    custom_input = st.session_state.get(f"custom_multi_{qid}", "").strip()
                    if custom_input:
                        for item in custom_input.split(","):
                            ic = item.strip()
                            if ic:
                                if ic not in final_vals:
                                    final_vals.append(ic)
                                if ic.casefold() not in {opt.casefold() for opt in q["options"]}:
                                    new_options[qid] = ic
                answers[answer_key] = " | ".join(final_vals)
                if not final_vals:
                    axis_errors[axis_name].append(f"السؤال {qid} ناقص.")

all_validation_errors = [e for errs in axis_errors.values() for e in errs]


# --- الشريط الجانبي للإدارة والاعتماد ---
with st.sidebar:
    st.subheader(f"🏢 {st.session_state.entity_name}")
    st.write("---")
    
    completed_count = len(st.session_state.saved_axes)
    st.progress(completed_count / len(axes) if axes else 0, text=f"الأقسام المكتملة: {completed_count}/{len(axes)}")
    
    st.write("---")
    if not st.session_state.questionnaire_submitted:
        btn_label = "✅ تحديث الإجابات" if existing_response else "✅ الاعتماد والإرسال النهائي"
        if st.button(btn_label, type="primary", use_container_width=True):
            if all_validation_errors:
                st.error("يوجد أسئلة غير مجابة في الأقسام:")
                for ax, errs in axis_errors.items():
                    if errs:
                        st.write(f"**{ax}**: {len(errs)} أسئلة")
            else:
                try:
                    timestamp = datetime.now(DUBAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
                    resp_data = {
                        "Submission ID": st.session_state.submission_id,
                        "Entity Name": st.session_state.entity_name,
                        "User Code": st.session_state.user_code,
                        "Timestamp (UAE)": timestamp,
                    }
                    resp_data.update(answers)
                    
                    save_or_update_response(resp_data, row_idx=existing_row_idx)
                    update_new_options(new_options)
                    
                    get_existing_response.clear()
                    load_questions.clear()
                    
                    st.session_state.questionnaire_submitted = True
                    st.success("تم الإرسال بنجاح!")
                    st.rerun()
                except Exception as err:
                    st.error(f"خطأ بالحفظ: {err}")
    else:
        st.success("تم إرسال الاستبيان بنجاح.")

    if st.button("تسجيل الخروج", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()


# --- عرض الأقسام والأسئلة ---
tab_labels = [f"{'✅' if ax in st.session_state.saved_axes else '⏳'} {ax}" for ax in axes]
tabs = st.tabs(tab_labels)
QUESTIONS_PER_PAGE = 5

for axis_index, (tab, axis_name) in enumerate(zip(tabs, axes)):
    axis_questions = [q for q in questions if q["axis"] == axis_name]

    with tab:
        with st.container():
            col_h, col_save = st.columns([3, 1])
            with col_h:
                st.toggle(
                    f"ينطبق هذا القسم على جهتي",
                    key=f"axis_toggle_{axis_name}",
                    disabled=st.session_state.questionnaire_submitted,
                )
            with col_save:
                if not st.session_state.questionnaire_submitted:
                    if st.button(f"💾 حفظ القسم", key=f"save_btn_{axis_index}", use_container_width=True):
                        st.session_state.saved_axes.add(axis_name)
                        st.success("تم الحفظ محلياً!")
                        st.rerun()

            applicable = st.session_state.get(f"axis_toggle_{axis_name}", True)

            if not applicable:
                st.caption("تم استبعاد هذا القسم من التقييم.")
            else:
                total_q = len(axis_questions)
                total_pages = (total_q + QUESTIONS_PER_PAGE - 1) // QUESTIONS_PER_PAGE
                current_page = st.session_state.axis_pages.get(axis_name, 0)
                
                start_idx = current_page * QUESTIONS_PER_PAGE
                end_idx = min(start_idx + QUESTIONS_PER_PAGE, total_q)
                page_questions = axis_questions[start_idx:end_idx]

                st.caption(f"صفحة ({current_page + 1}/{total_pages})")

                for q in page_questions:
                    qid = q["id"]
                    st.markdown(f"**{qid}. {q['question']}**")

                    if q["type"] == "single":
                        custom_label = "➕ إضافة خيار آخر"
                        display_options = ["— اختر —"] + q["options"]
                        if q["allow_new"]:
                            display_options.append(custom_label)

                        st.radio(
                            "اختر:",
                            options=display_options,
                            key=f"q_{qid}",
                            horizontal=True,
                            label_visibility="collapsed",
                            disabled=st.session_state.questionnaire_submitted,
                        )

                        # إظهار حقل كتابة الخيار الآخر إذا تم اختياره
                        current_sel = st.session_state.get(f"q_{qid}")
                        if current_sel == custom_label and q["allow_new"]:
                            st.text_input(
                                "اكتب الخيار:",
                                key=f"custom_single_{qid}",
                                label_visibility="collapsed",
                                disabled=st.session_state.questionnaire_submitted,
                            )

                    elif q["type"] == "multiple":
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

                    st.markdown("---")

                col_p, col_n = st.columns(2)
                with col_p:
                    if current_page > 0 and st.button("⬅️ السابق", key=f"prev_{axis_name}"):
                        st.session_state.axis_pages[axis_name] -= 1
                        st.rerun()
                with col_n:
                    if current_page < total_pages - 1 and st.button("التالي ➡️", key=f"next_{axis_name}"):
                        st.session_state.axis_pages[axis_name] += 1
                        st.rerun()
