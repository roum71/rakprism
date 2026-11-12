import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
import streamlit as st
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1


# =========================================================
# 1. Page configuration
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
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# 2. Configuration and Google Sheets connection
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
def get_spreadsheet():
    """Connect to the spreadsheet using the service-account secret."""
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
    return client.open_by_key(spreadsheet_id)


try:
    spreadsheet = get_spreadsheet()
    questions_ws = spreadsheet.worksheet(QUESTIONS_TAB)
    responses_ws = spreadsheet.worksheet(RESPONSES_TAB)
    users_ws = spreadsheet.worksheet(USERS_TAB)
except Exception as e:
    st.error(
        f"خطأ في الاتصال بقاعدة البيانات أو التأكد من وجود الأوراق المطلوبة (Questions, Responses, Users): {e}"
    )
    st.stop()


# =========================================================
# 3. Authentication & Settings (Login via Users sheet)
# =========================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "entity_name" not in st.session_state:
    st.session_state.entity_name = ""
if "user_code" not in st.session_state:
    st.session_state.user_code = ""
if "allow_edit" not in st.session_state:
    st.session_state.allow_edit = True


@st.cache_data(ttl=30)
def load_users():
    """Load authorized entities, codes, and edit permissions from Users tab."""
    records = users_ws.get_all_records()
    users_dict = {}
    for r in records:
        code = str(r.get("Code", "")).strip()
        entity = str(r.get("Entity_Name", "")).strip()
        status = str(r.get("Status", "active")).strip().lower()
        allow_edit_val = str(r.get("Allow_Edit", "yes")).strip().lower() in {"yes", "y", "true", "1", "نعم"}
        
        if code and entity and status == "active":
            users_dict[code] = {
                "entity": entity,
                "allow_edit": allow_edit_val
            }
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
                    st.error("رمز الدخول غير صحيح أو غير مفعل. يرجى المراجعة.")
            except Exception as auth_err:
                st.error(f"حدث خطأ أثناء التحقق من الرمز: {auth_err}")

    st.stop()


# =========================================================
# 4. Load questionnaire & Existing User Response (if any)
# =========================================================
def parse_yes_no(value):
    return str(value).strip().lower() in {"yes", "y", "true", "1", "نعم"}


def parse_options(value):
    if value is None:
        return []
    return [
        option.strip()
        for option in str(value).split("|")
        if option.strip()
    ]


@st.cache_data(ttl=60)
def load_questions():
    records = questions_ws.get_all_records()
    if not records:
        raise ValueError("ورقة Questions لا تحتوي على أسئلة.")

    headers = questions_ws.row_values(1)
    missing = [
        column
        for column in REQUIRED_QUESTIONS_COLUMNS
        if column not in headers
    ]
    if missing:
        raise ValueError(
            "أعمدة غير موجودة في ورقة Questions: " + ", ".join(missing)
        )

    questions = []
    seen_ids = set()

    for sheet_row, record in enumerate(records, start=2):
        try:
            question_id = int(record["ID"])
        except (TypeError, ValueError):
            raise ValueError(
                f"رقم السؤال في الصف {sheet_row} يجب أن يكون رقماً صحيحاً."
            )

        if question_id in seen_ids:
            raise ValueError(f"رقم السؤال {question_id} مكرر.")
        seen_ids.add(question_id)

        axis = str(record["Axis"]).strip()
        question_text = str(record["Question"]).strip()
        question_type = str(record["Type"]).strip().lower()
        options = parse_options(record.get("Options", ""))

        if not axis or not question_text:
            raise ValueError(
                f"يرجى استكمال المحور ونص السؤال في الصف {sheet_row}."
            )

        if question_type not in {"single", "multiple"}:
            raise ValueError(
                f"نوع السؤال في الصف {sheet_row} يجب أن يكون "
                "'single' أو 'multiple'."
            )

        if not options:
            raise ValueError(
                f"لا توجد خيارات للسؤال رقم {question_id}."
            )

        questions.append(
            {
                "id": question_id,
                "axis": axis,
                "question": question_text,
                "type": question_type,
                "allow_new": parse_yes_no(record.get("Allow_New", "no")),
                "options": options,
                "sheet_row": sheet_row,
            }
        )

    return questions


try:
    questions = load_questions()
except Exception as error:
    st.error(f"تعذر تحميل إعدادات الاستبيان: {error}")
    st.stop()

axes = list(dict.fromkeys(q["axis"] for q in questions))


@st.cache_data(ttl=10)
def get_existing_response(user_code):
    """Check if the entity has already submitted a response and return it along with its row index."""
    try:
        data = responses_ws.get_all_records()
        headers = responses_ws.row_values(1)
        user_code_col = "User Code" if "User Code" in headers else None
        
        if not user_code_col:
            return None, None

        for idx, row in enumerate(data, start=2): # Start from row 2
            if str(row.get(user_code_col, "")).strip() == str(user_code).strip():
                return row, idx
    except Exception:
        pass
    return None, None


existing_response, existing_row_idx = get_existing_response(st.session_state.user_code)


# =========================================================
# 5. Helpers for saving / updating responses and options
# =========================================================
def update_new_options(new_options):
    if not new_options:
        return

    sheet_headers = questions_ws.row_values(1)
    options_col = sheet_headers.index("Options") + 1

    all_records = questions_ws.get_all_records()
    updates = []

    for sheet_row, record in enumerate(all_records, start=2):
        question_id = int(record["ID"])
        if question_id not in new_options:
            continue

        old_options = parse_options(record.get("Options", ""))
        new_option = new_options[question_id].strip()

        existing_lower = {item.casefold() for item in old_options}
        if new_option and new_option.casefold() not in existing_lower:
            updated_options = old_options + [new_option]
            cell = rowcol_to_a1(sheet_row, options_col)
            updates.append(
                {
                    "range": cell,
                    "values": [[" | ".join(updated_options)]],
                }
            )

    if updates:
        questions_ws.batch_update(updates, value_input_option="RAW")


def save_or_update_response(answer_data, row_idx=None):
    """Save new response or update existing row if already submitted."""
    existing_headers = responses_ws.row_values(1)

    headers = list(existing_headers)
    for key in answer_data:
        if key not in headers:
            headers.append(key)

    if not headers:
        headers = list(answer_data.keys())

    # Ensure headers row is up to date
    responses_ws.update(
        range_name="A1",
        values=[headers],
        value_input_option="RAW",
    )

    row_values = [answer_data.get(header, "") for header in headers]

    if row_idx:
        # Update existing row
        cell_range = f"A{row_idx}:" + rowcol_to_a1(row_idx, len(headers))
        responses_ws.update(
            range_name=cell_range,
            values=[row_values],
            value_input_option="USER_ENTERED",
        )
    else:
        # Append new row
        responses_ws.append_row(
            row_values,
            value_input_option="USER_ENTERED",
            table_range="A1",
        )


# =========================================================
# 6. Questionnaire interface
# =========================================================
col_title, col_logout = st.columns([4, 1])
with col_title:
    st.title("📋 نموذج تقييم المخاطر")
with col_logout:
    st.write("")
    if st.button("تسجيل الخروج"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

st.info(f"مرحباً بك: **{st.session_state.entity_name}**")

# إذا كان هناك رد سابق والتعديل مغلق حسب الإعدادات
if existing_response and not st.session_state.allow_edit:
    st.warning("⚠️ لقد قمت بتقديم إجاباتك مسبقاً، وحالة الحساب الحالية **غير مسموح لها بالتعديل** (مقفل من الإعدادات). يمكنك مراجعة إجاباتك أدناه:")
    st.json(existing_response)
    st.stop()

elif existing_response and st.session_state.allow_edit:
    st.success("📝 لديك إجابات سابقة مسجلة. يمكنك مراجعتها، تعديل ما ترغب، ثم حفظ التحديثات.")

st.write(
    "يرجى الإجابة عن الأسئلة في كل محور. "
    "تم تقسيم الأسئلة لتسهيل التصفح وتقليل التمرير الطولي."
)

if "questionnaire_submitted" not in st.session_state:
    st.session_state.questionnaire_submitted = False

if "submission_id" not in st.session_state:
    st.session_state.submission_id = existing_response.get("Submission ID", str(uuid.uuid4())) if existing_response else str(uuid.uuid4())

if "saved_axes" not in st.session_state:
    st.session_state.saved_axes = set(axes) if existing_response else set()

if "axis_pages" not in st.session_state:
    st.session_state.axis_pages = {ax: 0 for ax in axes}

answers = {}
axis_errors = {}
new_options = {}

tab_labels = []
for axis_name in axes:
    status_icon = "✅" if axis_name in st.session_state.saved_axes else "⏳"
    tab_labels.append(f"{status_icon} {axis_name}")

tabs = st.tabs(tab_labels)
QUESTIONS_PER_PAGE = 4

for axis_index, (tab, axis_name) in enumerate(zip(tabs, axes)):
    axis_questions = [q for q in questions if q["axis"] == axis_name]
    axis_errors[axis_name] = []

    with tab:
        with st.container(border=True):
            st.header(f"📌 {axis_name}")

            # استرجاع حالة المحور القديمة إن وجدت
            axis_status_key = f"[حالة المحور] {axis_name}"
            default_axis_val = True
            if existing_response and existing_response.get(axis_status_key) == "لا ينطبق":
                default_axis_val = False

            applicable = st.toggle(
                "هذا المحور ينطبق على جهتي",
                value=default_axis_val,
                key=f"axis_{axis_index}",
                disabled=st.session_state.questionnaire_submitted,
            )

            answers[axis_status_key] = "ينطبق" if applicable else "لا ينطبق"

            if not applicable:
                st.caption("تم استبعاد هذا المحور؛ ستُسجل جميع أسئلته كـ «لا ينطبق».")
                for q in axis_questions:
                    answers[f"Q{q['id']}: {q['question']}"] = "لا ينطبق"
            else:
                st.divider()
                
                total_q = len(axis_questions)
                total_pages = (total_q + QUESTIONS_PER_PAGE - 1) // QUESTIONS_PER_PAGE
                current_page = st.session_state.axis_pages.get(axis_name, 0)
                
                start_idx = current_page * QUESTIONS_PER_PAGE
                end_idx = min(start_idx + QUESTIONS_PER_PAGE, total_q)
                page_questions = axis_questions[start_idx:end_idx]

                st.caption(f"الصفحة ({current_page + 1} من {total_pages}) لهذا المحور")

                for q in page_questions:
                    qid = q["id"]
                    answer_key = f"Q{qid}: {q['question']}"
                    st.subheader(f"{qid}. {q['question']}")

                    # استرجاع الإجابة القديمة إن وجدت
                    old_val = existing_response.get(answer_key, "") if existing_response else ""

                    if q["type"] == "single":
                        custom_label = "➕ إضافة خيار جديد..."
                        display_options = ["— يرجى اختيار إجابة —"] + q["options"]
                        if q["allow_new"]:
                            display_options.append(custom_label)

                        # تحديد الإفتراضي بناءً على الإجابة القديمة
                        default_index = 0
                        if old_val in q["options"]:
                            default_index = display_options.index(old_val)
                        elif old_val and q["allow_new"]:
                            display_options.insert(-1, old_val)
                            default_index = display_options.index(old_val)

                        selected = st.radio(
                            "اختر إجابة واحدة:",
                            options=display_options,
                            index=default_index,
                            key=f"q_{qid}",
                            disabled=st.session_state.questionnaire_submitted,
                        )

                        if selected == custom_label:
                            custom_value = st.text_input(
                                "اكتب الخيار الجديد:",
                                value=old_val if old_val not in q["options"] and old_val else "",
                                key=f"custom_{qid}",
                                disabled=st.session_state.questionnaire_submitted,
                            ).strip()
                            answers[answer_key] = custom_value
                            if custom_value:
                                new_options[qid] = custom_value
                            else:
                                axis_errors[axis_name].append(
                                    f"يرجى كتابة الخيار الجديد للسؤال رقم {qid}."
                                )

                        elif selected == "— يرجى اختيار إجابة —":
                            answers[answer_key] = ""
                            axis_errors[axis_name].append(
                                f"يرجى الإجابة عن السؤال رقم {qid}."
                            )
                        else:
                            answers[answer_key] = selected

                    elif q["type"] == "multiple":
                        default_multi = [v.strip() for v in old_val.split("|") if v.strip()] if old_val else []
                        
                        selected_values = st.multiselect(
                            "يمكن اختيار أكثر من إجابة:",
                            options=q["options"],
                            default=[v for v in default_multi if v in q["options"]],
                            placeholder="اختر إجابة أو أكثر...",
                            key=f"q_{qid}",
                            disabled=st.session_state.questionnaire_submitted,
                        )

                        custom_value = ""
                        extra_defaults = [v for v in default_multi if v not in q["options"]]
                        if q["allow_new"]:
                            custom_value = st.text_input(
                                "إضافة خيار غير موجود (اختياري):",
                                value=", ".join(extra_defaults),
                                key=f"custom_{qid}",
                                disabled=st.session_state.questionnaire_submitted,
                            ).strip()

                        final_values = list(selected_values)
                        if custom_value:
                            for cv in custom_value.split(","):
                                cv_clean = cv.strip()
                                if cv_clean:
                                    if cv_clean.casefold() not in {v.casefold() for v in final_values}:
                                        final_values.append(cv_clean)
                                    if cv_clean.casefold() not in {v.casefold() for v in q["options"]}:
                                        new_options[qid] = cv_clean

                        answers[answer_key] = " | ".join(final_values)
                        if not final_values:
                            axis_errors[axis_name].append(
                                f"يرجى اختيار إجابة واحدة على الأقل للسؤال رقم {qid}."
                            )

                    st.write("---")

                col_prev, col_next = st.columns(2)
                with col_prev:
                    if current_page > 0:
                        if st.button("⬅️ الأسئلة السابقة", key=f"prev_{axis_name}"):
                            st.session_state.axis_pages[axis_name] -= 1
                            st.rerun()
                with col_next:
                    if current_page < total_pages - 1:
                        if st.button("الأسئلة التالية ➡️", key=f"next_{axis_name}"):
                            st.session_state.axis_pages[axis_name] += 1
                            st.rerun()

            if not st.session_state.questionnaire_submitted:
                st.write("")
                if st.button(
                    f"💾 حفظ وتأكيد محور ({axis_name})",
                    key=f"save_btn_{axis_index}",
                    use_container_width=True,
                    type="secondary"
                ):
                    st.session_state.saved_axes.add(axis_name)
                    st.success(f"تم تأكيد إجابات محور «{axis_name}» بنجاح.")
                    st.rerun()


# =========================================================
# 7. Final Submit and Update
# =========================================================
st.divider()
st.subheader("📤 الاعتماد النهائي للاستبيان")

completed_count = len(st.session_state.saved_axes)
st.progress(
    completed_count / len(axes) if axes else 0,
    text=f"المحاور المؤكدة: {completed_count} من {len(axes)}",
)

all_validation_errors = [
    err for errs in axis_errors.values() for err in errs
]

if not st.session_state.questionnaire_submitted:
    button_label = "✅ تحديث الإجابات في قاعدة البيانات" if existing_response else "✅ إرسال جميع الإجابات إلى قاعدة البيانات"
    
    if st.button(
        button_label,
        type="primary",
        use_container_width=True,
    ):
        if all_validation_errors:
            st.error("لا يمكن الإرسال لوجود أسئلة غير مكتملة في المحاور:")
            for ax_name, errs in axis_errors.items():
                if errs:
                    st.write(f"**📌 {ax_name}:** ({len(errs)} أسئلة متبقية)")
        else:
            try:
                timestamp = datetime.now(DUBAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
                response_data = {
                    "Submission ID": st.session_state.submission_id,
                    "Entity Name": st.session_state.entity_name,
                    "User Code": st.session_state.user_code,
                    "Timestamp (UAE)": timestamp,
                }
                response_data.update(answers)

                # حفظ جديد أو تحديث الصف الحالي بناءً على وجود رد سابق
                save_or_update_response(response_data, row_idx=existing_row_idx)
                update_new_options(new_options)

                st.cache_data.clear()
                st.session_state.questionnaire_submitted = True
                st.success("تم اعتماد وإرسال الإجابات بنجاح!")
                st.rerun()
            except Exception as error:
                st.error(f"تعذر حفظ الإجابات: {error}")
else:
    st.success("تم استلام إجاباتك وحفظها بنجاح. شكراً لمشاركتك.")
    if st.button("تسجيل الخروج", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()
