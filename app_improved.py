# أضف هذا الجزء داخل <style> في القسم 1 لدعم اتجاه التبويبات من اليمين لليسار:
"""
div[data-baseweb="tab-list"] {
    direction: rtl;
    justify-content: flex-start;
}
"""

# =========================================================
# 5. Questionnaire interface (Tabs + Axis Progress)
# =========================================================
st.title("📋 نموذج تقييم المخاطر")
st.write(
    "يرجى مراجعة الأسئلة والإجابة عنها في كل تبويب. "
    "يمكنك حفظ كل محور على حدة ثم اعتماد الإرسال النهائي."
)

if "questionnaire_submitted" not in st.session_state:
    st.session_state.questionnaire_submitted = False

if "submission_id" not in st.session_state:
    st.session_state.submission_id = str(uuid.uuid4())

if "saved_axes" not in st.session_state:
    st.session_state.saved_axes = set()

answers = {}
axis_errors = {}  # تخزين أخطاء كل محور على حدة
new_options = {}

# تجهيز أسماء التبويبات مع مؤشر حالة الإنجاز
tab_labels = []
for idx, axis_name in enumerate(axes):
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

            # زر حفظ / التحقق من المحور الحالي
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
                        st.success(f"تم تأكيد وحفظ إجابات محور «{axis_name}» مؤقتاً. انتقل للتبويب التالي.")
                        st.rerun()


# =========================================================
# 6. Final Submit and Confirmation
# =========================================================
st.divider()
st.subheader("📤 الاعتماد النهائي للاستبيان")

# شريط تقدم يوضح عدد المحاور المكتملة
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
            st.error("لا يمكن الإرسال لوجود أسئلة غير مكتملة في المحاور التالية:")
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
