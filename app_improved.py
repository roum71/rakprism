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

    # Put the spreadsheet ID (the long key in its URL) in secrets.toml.
    spreadsheet_id = st.secrets["google_sheets"]["spreadsheet_id"]
    return client.open_by_key(spreadsheet_id)


spreadsheet = get_spreadsheet()
questions_ws = spreadsheet.worksheet(QUESTIONS_TAB)
responses_ws = spreadsheet.worksheet(RESPONSES_TAB)


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

if len(axes) != 5:
    st.warning(
        f"تنبيه: الإعداد الحالي يحتوي على {len(axes)} محاور، "
        "بينما التوزيع الافتراضي المطلوب هو 5 محاور."
    )

if len(questions) != 32:
    st.warning(
        f"تنبيه: الإعداد الحالي يحتوي على {len(questions)} سؤالاً، "
        "بينما العدد الافتراضي المطلوب هو 32 سؤالاً."
    )


# =========================================================
# 4. Helpers for saving responses and new options
# =========================================================
def update_new_options(new_options):
    """
    Update the Options cells in one batch.
    new_options is a dictionary: question_id -> new option.
    """
    if not new_options:
        return

    sheet_headers = questions_ws.row_values(1)
    options_col = sheet_headers.index("Options") + 1
    id_col = sheet_headers.index("ID") + 1

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
    """Ensure response headers exist, then append one complete response."""
    existing_headers = responses_ws.row_values(1)

    # Keep existing columns and append newly introduced question columns.
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
# 5. Questionnaire interface
# =========================================================
st.title("📋 نموذج تقييم المخاطر")
st.write(
    "يرجى مراجعة الأسئلة والإجابة عنها. "
    "يمكنك استبعاد أي محور لا ينطبق على الجهة."
)
st.info(
    "السؤال من نوع «اختيار واحد» يسمح بإجابة واحدة، "
    "أما «اختيار متعدد» فيسمح بتحديد أكثر من إجابة. "
    "يمكن إضافة خيار جديد عندما تكون هذه الخاصية مفعّلة."
)

st.divider()

if "questionnaire_submitted" not in st.session_state:
    st.session_state.questionnaire_submitted = False

answers = {}
validation_errors = []
new_options = {}

for axis_index, axis_name in enumerate(axes):
    axis_questions = [q for q in questions if q["axis"] == axis_name]

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
            st.caption("سيتم تجاوز أسئلة هذا المحور وتسجيلها كـ «لا ينطبق».")
            for q in axis_questions:
                answers[f"Q{q['id']}: {q['question']}"] = "لا ينطبق"
            continue

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

                elif selected == "— يرجى اختيار إجابة —":
                    answers[answer_key] = ""
                    validation_errors.append(
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
                        value.casefold() for value in final_values
                    }:
                        final_values.append(custom_value)

                    if custom_value.casefold() not in {
                        value.casefold() for value in q["options"]
                    }:
                        new_options[qid] = custom_value

                answers[answer_key] = " | ".join(final_values)

                if not final_values:
                    validation_errors.append(
                        f"يرجى اختيار إجابة واحدة على الأقل للسؤال رقم {qid}."
                    )

            st.write("")


# =========================================================
# 6. Submit and confirmation
# =========================================================
st.divider()

if not st.session_state.questionnaire_submitted:
    if st.button(
        "✅ حفظ وإرسال الإجابات",
        type="primary",
        use_container_width=True,
    ):
        if validation_errors:
            st.error("يرجى استكمال الإجابات التالية قبل الإرسال:")
            for message in validation_errors:
                st.write(f"- {message}")
        else:
            try:
                timestamp = datetime.now(DUBAI_TZ).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

                response_data = {
                    "Submission ID": str(uuid.uuid4()),
                    "Timestamp (UAE)": timestamp,
                }
                response_data.update(answers)

                # Save the response first. Then update the reusable options.
                save_response(response_data)

                try:
                    update_new_options(new_options)
                    options_message = (
                        "تم تحديث الخيارات الجديدة بنجاح."
                        if new_options
                        else "لم تتم إضافة خيارات جديدة."
                    )
                except Exception as option_error:
                    options_message = (
                        "تم حفظ الإجابات، لكن تعذر تحديث بعض الخيارات الجديدة. "
                        f"التفاصيل: {option_error}"
                    )

                st.cache_data.clear()
                st.session_state.questionnaire_submitted = True
                st.success("تم إرسال الاستبيان بنجاح.")
                st.info(options_message)
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

        st.session_state.questionnaire_submitted = False
        st.rerun()
