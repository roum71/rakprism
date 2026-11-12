import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from datetime import datetime

# إعدادات الصفحة ودعم اللغة العربية (RTL)
st.set_page_config(page_title="نموذج التقييم بالمحاور", layout="centered")
st.markdown("""
    <style>
    .main, .stTextInput, .stSelectbox, .stMultiSelect, .stRadio, .stCheckbox, .stToggle {
        direction: rtl;
        text-align: right;
    }
    label, p, h1, h2, h3, div[data-testid="stMarkdownContainer"] {
        text-align: right !important;
        direction: rtl !important;
    }
    </style>
""", unsafe_allow_html=True)

# الاتصال بـ Google Sheets
@st.cache_resource
def init_connection():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

client = init_connection()
SHEET_NAME = "اسم_ملف_جوجل_شيت_الخاص_بك" # <-- عدّل هذا الاسم
spreadsheet = client.open(SHEET_NAME)
questions_sheet = spreadsheet.worksheet("Questions")
responses_sheet = spreadsheet.worksheet("Responses")

@st.cache_data(ttl=60)
def load_questions():
    data = questions_sheet.get_all_records()
    return pd.DataFrame(data)

df_questions = load_questions()

st.title("📋 نموذج التقييم والاستبيان (حسب المحاور)")
st.write("في حال عدم انطباق أي محور على جهتك/حالتك، يرجى إلغاء تفعيله وسيتم تجاوز جميع أسئلته تلقائياً.")
st.divider()

answers = {}
new_options_to_save = []

# استخراج المحاور بالترتيب الذي وردت به في الشيت
axes = df_questions['Axis'].unique()

for axis_idx, axis_name in enumerate(axes):
    # تصفية الأسئلة الخاصة بهذا المحور
    axis_questions = df_questions[df_questions['Axis'] == axis_name]
    
    # إنشاء حاوية بصرية لكل محور
    with st.container(border=True):
        st.header(f"📌 {axis_name}")
        
        # مفتاح تحديد هل المحور ينطبق أم لا (مفعّل افتراضياً)
        is_applicable = st.toggle(
            label="هذا المحور ينطبق",
            value=True,
            key=f"axis_toggle_{axis_idx}"
        )
        
        # تسجيل حالة المحور نفسه في الإجابات
        answers[f"[حالة المحور] {axis_name}"] = "ينطبق" if is_applicable else "لا ينطبق"
        
        # إذا كان المحور لا ينطبق: نعطي جميع أسئلته قيمة "لا ينطبق" ونخفيها
        if not is_applicable:
            st.info("تم استبعاد أسئلة هذا المحور لعدم الانطباق.")
            for _, row in axis_questions.iterrows():
                q_id = row['ID']
                answers[f"Q{q_id}: {row['Question']}"] = "لا ينطبق"
        
        # إذا كان المحور ينطبق: نعرض الأسئلة بشكل طبيعي
        else:
            st.markdown("---")
            for index, row in axis_questions.iterrows():
                q_id = row['ID']
                q_text = f"{q_id}. {row['Question']}"
                q_type = str(row['Type']).strip().lower()
                allow_new = str(row['Allow_New']).strip().lower() == 'yes'
                
                raw_options = str(row['Options'])
                options = [opt.strip() for opt in raw_options.split('|') if opt.strip()]
                
                st.subheader(q_text)
                
                # 1. اختيار واحد
                if q_type == 'single':
                    display_options = options + (["➕ إضافة خيار جديد..."] if allow_new else [])
                    selected = st.radio(
                        label=f"سؤال {q_id}",
                        options=display_options,
                        key=f"q_{q_id}",
                        label_visibility="collapsed"
                    )
                    
                    if selected == "➕ إضافة خيار جديد...":
                        custom_val = st.text_input("اكتب الخيار الجديد هنا:", key=f"custom_{q_id}")
                        answers[f"Q{q_id}: {row['Question']}"] = custom_val
                        if custom_val.strip():
                            # index + 2 يطابق رقم الصف الفعلي في جوجل شيت
                            new_options_to_save.append((index + 2, raw_options, custom_val.strip()))
                    else:
                        answers[f"Q{q_id}: {row['Question']}"] = selected

                # 2. اختيار متعدد
                elif q_type == 'multiple':
                    selected_list = st.multiselect(
                        label=f"سؤال {q_id}",
                        options=options,
                        placeholder="اختر من القائمة...",
                        key=f"q_{q_id}",
                        label_visibility="collapsed"
                    )
                    
                    if allow_new:
                        custom_val = st.text_input("أو أضف خياراً جديداً (اختياري):", key=f"custom_{q_id}")
                        if custom_val.strip():
                            selected_list.append(custom_val.strip())
                            new_options_to_save.append((index + 2, raw_options, custom_val.strip()))
                    
                    answers[f"Q{q_id}: {row['Question']}"] = " | ".join(selected_list)
                
                st.write("") # مسافة بسيطة بين الأسئلة

# زر الحفظ والإرسال
st.write("")
if st.button("✅ حفظ وإرسال جميع الإجابات", type="primary", use_container_width=True):
    with st.spinner("جاري حفظ البيانات في Google Sheets..."):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row_data = {"Timestamp": timestamp}
        row_data.update(answers)
        
        existing_headers = responses_sheet.row_values(1)
        if not existing_headers:
            responses_sheet.append_row(list(row_data.keys()))
            
        responses_sheet.append_row(list(row_data.values()))
        
        # تحديث الخيارات الجديدة في العمود السادس (Options أصبح العمود 6 بعد إضافة Axis)
        for sheet_row_idx, old_opts, new_opt in new_options_to_save:
            updated_opts = f"{old_opts} | {new_opt}" if old_opts else new_opt
            questions_sheet.update_cell(sheet_row_idx, 6, updated_opts)
            
        st.cache_data.clear()
        st.success("تم حفظ الإجابات وتحديث الخيارات بنجاح!")
