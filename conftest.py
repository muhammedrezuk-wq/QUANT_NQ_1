# -*- coding: utf-8 -*-
"""عزل أسطول الفحوص عن سجل العيارات الحي — يسري على كل جلسة pytest من الجذر.

الدرس (166 المقيس + صيد ٢٦-٠٨): اعتماد المالك الحي (مثل ANALYSIS_SPEED=10)
كان سيُقرأ داخل الفحوص عبر approved_value فتنكسر على قيمة يومه لا على عقدها.
كل جلسة فحص تأخذ سجلًا مؤقتًا فارغًا: الساري داخل الفحوص هو قيم المانيفست
ونقاط التطابق دائمًا، واعتماد المالك يبقى ملكًا للنظام الحي وحده.
"""
import os
import tempfile

os.environ.setdefault(
    "QUANT_ANALYSIS_SETTINGS_DB",
    os.path.join(tempfile.mkdtemp(prefix="nq_params_test_"),
                 "analysis_settings.db"))
