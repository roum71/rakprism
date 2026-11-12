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
DUBAI_TZ = ZoneInfo("Asia/Dubai")

REQUIRED_COLUMNS = [
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
except Exception as e:
    st.error(f"خطأ في الاتصال بقاعدة البيانات (Google Sheets): {e}")
    st.stop()


# =========================================================
# 3. Load and validate questionnaire configuration
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
    missing = [column for column in REQUIRED_COLUMNS if column not in headers]
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


# =========================================================
# 4. Helpers for saving responses and new options
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


def save_response(answer_data):
    existing_headers = responses_ws.row_values(1)

    headers = list(existing_headers)
    for key in answer_data:
        if key not in headers:
            headers.append(key)

    if not headers:
        headers = list(answer_data.keys())

    responses_ws.update(
        range_name="A1",
        values=[headers],
        value_input_option="RAW",
    )

    row = [answer_data.get(header, "") for header in headers]
    responses_ws.append_row(
        row,
        value_input_option="USER_ENTERED",
        table_range="A1",
    )


# =========================================================
# 5. Questionnaire interface (Tabs per Axis)
# =========================================================
st.title("📋 نموذج تقييم المخاطر")
st.write(
    "يرجى مراجعة الأسئلة والإجابة عنها في كل تبويب. "
    "يمكنك حفظ وتأكيد كل محور على حدة، ثم الاعتماد النهائي بالأسفل."
)

if "questionnaire_submitted" not in st.session_state:
    st.session_state.questionnaire_submitted = False

if "submission_id" not in st.session_state:
    st.session_state.submission_id = str(uuid.uuid4())

if "saved_axes" not in st.session_state:
    st.session_state.saved_axes = set()

answers = {}
axis_errors = {}
new_options = {}

tab_labels = []
for axis_name in axes:
    status_icon = "✅" if axis_name in st.session_state.saved_axes else "⏳"
    tab_labels.append(f"{status_icon} {axis_name}")

tabs = st.tabs(tab_labels)

for axis_index, (tab, axis_name) in enumerate(zip(tabs, axes)):
    axis_questions = [q for q in questions if q["axis"] == axis_name]
    axis_errors[axis_name] = []

    with tab:
        with st.container(border=True):
            st.header(f"📌 {axis_name}")

            applicable = st.toggle(
                "هذا المحور ينطبق على جهتي",
                value=True,
                key=f"axis_{axis_index}",
                disabled=st.session_state.questionnaire_submitted,
            )

            answers[f"[حالة المحور] {axis_name}"] = (
                "ينطبق" if applicable else "لا ينطبق"
            )

            if not applicable:
                st.caption("تم استبعاد هذا المحور؛ ستُسجل جميع أسئلته كـ «لا ينطبق».")
                for q in axis_questions:
                    answers[f"Q{q['id']}: {q['question']}"] = "لا ينطبق"
            else:
                st.divider()
                for q in axis_questions:
                    qid = q["id"]
                    answer_key = f"Q{qid}: {q['question']}"
                    st.subheader(f"{qid}. {q['question']}")

                    if q["type"] == "single":
                        custom_label = "➕ إضافة خيار جديد..."
                        display_options = ["— يرجى اختيار إجابة —"] + q["options"]
                        if q["allow_new"]:
                            display_options.append(custom_label)

                        selected = st.radio(
                            "اختر إجابة واحدة:",
                            options=display_options,
                            index=0,
                            key=f"q_{qid}",
                            disabled=st.session_state.questionnaire_submitted,
                        )

                        if selected == custom_label:
                            custom_value = st.text_input(
                                "اكتب الخيار الجديد:",
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
                        selected_values = st.multiselect(
                            "يمكن اختيار أكثر من إجابة:",
                            options=q["options"],
                            placeholder="اختر إجابة أو أكثر...",
                            key=f"q_{qid}",
                            disabled=st.session_state.questionnaire_submitted,
                        )

                        custom_value = ""
                        if q["allow_new"]:
                            custom_value = st.text_input(
                                "إضافة خيار غير موجود (اختياري):",
                                key=f"custom_{qid}",
                                disabled=st.session_state.questionnaire_submitted,
                            ).strip()

                        final_values = list(selected_values)
                        if custom_value:
                            if custom_value.casefold() not in {
                                v.casefold() for v in final_values
                            }:
                                final_values.append(custom_value)
                            if custom_value.casefold() not in {
                                v.casefold() for v in q["options"]
                            }:
                                new_options[qid] = custom_value

                        answers[answer_key] = " | ".join(final_values)
                        if not final_values:
                            axis_errors[axis_name].append(
                                f"يرجى اختيار إجابة واحدة على الأقل للسؤال رقم {qid}."
                            )

                    st.write("")

            # زر الحفظ المؤقت للمحور الحالي
            if not st.session_state.questionnaire_submitted:
                st.divider()
                if st.button(
                    f"💾 حفظ وتأكيد محور ({axis_name})",
                    key=f"save_btn_{axis_index}",
                    use_container_width=True,
                ):
                    if axis_errors[axis_name]:
                        st.error("يرجى استكمال النواقص في هذا المحور:")
                        for err in axis_errors[axis_name]:
                            st.write(f"- {err}")
                    else:
                        st.session_state.saved_axes.add(axis_name)
                        st.success(f"تم تأكيد إجابات محور «{axis_name}» بنجاح.")
                        st.rerun()


# =========================================================
# 6. Final Submit and Confirmation
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
    if st.button(
        "✅ إرسال جميع الإجابات إلى قاعدة البيانات",
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
                    "Timestamp (UAE)": timestamp,
                }
                response_data.update(answers)

                save_response(response_data)
                update_new_options(new_options)

                st.cache_data.clear()
                st.session_state.questionnaire_submitted = True
                st.success("تم إرسال الاستبيان بالكامل بنجاح!")
                st.rerun()
            except Exception as error:
                st.error(f"تعذر حفظ الإجابات: {error}")
else:
    st.success("تم استلام إجاباتك. شكراً لمشاركتك.")
    if st.button("بدء استجابة جديدة", use_container_width=True):
        keys_to_clear = [
            key
            for key in st.session_state.keys()
            if key.startswith(("axis_", "q_", "custom_"))
        ]
        for key in keys_to_clear:
            del st.session_state[key]

        st.session_state.saved_axes = set()
        st.session_state.submission_id = str(uuid.uuid4())
        st.session_state.questionnaire_submitted = False
        st.rerun()
