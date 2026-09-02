// السكربتات (865) — أدوات فحص حقيقية بضغطة زر (منفذ حقيقي: /gov/tool/*).
// كلها قراءة/فحص — ولا وحدة بتغيّر شي بالنظام. الخام معروض جنب الترجمة (لا كذب).
import { useEffect, useState } from 'react'
import { useMarket } from '../core/market'

interface ToolResult { ok: boolean; code: number; output: string }

// ═══ فحوصات قسم أسمر (الكريبتو) — أمر المالك ٢٠٢٦-٠٨-٢٩ ═══
// TOOLS كلّها عقود فوركس؛ قسم الكريبتو كان بلا فحص يخصّه. هذه تقيس ما يخصّه
// وحده، وتظهر بقسمه فقط — كما أنّ فحوص الفوركس تبقى بقسم الفوركس وحده.
const CRYPTO_TOOLS: Array<{ id: string; title: string; desc: string; slow?: boolean }> = [
  {
    id: 'crypto_feed', title: 'فحص تغذية الكريبتو الحيّة', slow: true,
    desc: 'يراقب أرقام مصادر MEXC وBinance وسجلّ الرموز على نافذة مشتقّة من دورة كل مصدر، ويطالب بأن تتحرّك فعلًا — الأخضر الساكن ليس نجاحًا. ويفرّق بين العدّاد التراكمي والمستوى الثابت.',
  },
  {
    id: 'crypto_chain', title: 'فحص سلسلة الاستراتيجية (حتى بطاقة الإشارة)',
    desc: 'يمشي على حلقات السلسلة العشر من حواسّ الجلسة حتى بطاقة الإشارة ويقول أين وقفت. يفرّق صراحةً بين حلقة مقطوعة (عطل) وحلقة تنتظر مدخلها (سكوت السوق — ليس عطلًا).',
  },
  {
    id: 'crypto_isolation', title: 'فحص عزل الكريبتو عن الفوركس',
    desc: 'سبعة حواجز مقيسة: شجرتان منفصلتان · لا تصادم معرّفات · مخزنان مختلفان بعد فكّ وصلات ويندوز · نواتان بمنفذين · لا ذرّة فوركس محمّلة بنواة الكريبتو · ولا عيار قرار فوركسيّ بلوحته.',
  },
  {
    id: 'crypto_manual', title: 'فحص أنّ التنفيذ بشريّ (لا تداول آليّ)',
    desc: 'حاجز أمان: لا ذرّة كريبتو تَنشر أمر تنفيذ، ولا واحدة تكتب أمرًا على وسيط، ومُخرَج السلسلة بطاقة اقتراح لا أمر. أي خرق يعني أنّ صفقة قد تُفتح بلا يد إنسان.',
  },
  {
    id: 'crypto_ahmad', title: 'فحص مطابقة شجرة أحمد', slow: true,
    desc: 'يقارن شجرتنا بملفّ أحمد المُسلَّم بايتًا ببايت ويسمّي كل اختلاف. الفرقان المعلَنان بورقة التسليم يمرّان خضراء؛ وأي فرق غير معلَن يُفشل الفحص. غياب نسخة أحمد يُعلَن ولا يُعدّ نجاحًا.',
  },
]

const TOOLS: Array<{ id: string; title: string; desc: string; slow?: boolean }> = [
  {
    id: 'seal', title: 'فحص ختم النواة',
    desc: 'يقيس بصمة ملفات النواة المختومة V1.8.0 مقابل الختم — «سليمة ومطابقة» أو «منتهكة».',
  },
  {
    id: 'validator', title: 'المدقّق البنيوي',
    desc: 'يفحص كل الذرات على دستور القالب (23 قاعدة): عربي بالكود، الوقت، العزل، مطابقة العقد…',
  },
  {
    id: 'tests', title: 'اختبارات الذرات الكاملة',
    desc: 'يشغّل طقم اختبارات كل الذرات. تذكير: الاختبارات أرضية — الإثبات الحقيقي هو الحيّ.',
    slow: true,
  },
  {
    id: 'governance', title: 'فحص طبقة الحوكمة',
    desc: 'يتأكد أن أدوات اللوحة وفحوصاتها داخل الحوكمة وأن جذر المشروع الكامل يحتوي ملفاته الرسمية.',
  },
  {
    id: 'files', title: 'فحص ملفات المشروع',
    desc: 'يتأكد من 212 مانيفستًا، فرادة المعرّفات، وعدم وجود الاسم العربي أو ملفات تشغيل ناقصة.',
  },
  {
    id: 'events', title: 'فحص عقود الأحداث',
    desc: 'يفحص روابط السعر والمرجع والتنفيذ والرموز والتعلم والمحفظة، ويميّز المصادر الخارجية الاختيارية.',
  },
  {
    id: 'boot', title: 'فحص الإقلاع الفعلي',
    desc: 'يقرأ تقرير الإقلاع الحقيقي ويقبل فقط 212/212 بلا فشل أو استبعاد.',
  },
  {
    id: 'project', title: 'فحص المشروع الكامل',
    desc: 'يشغّل فحوص الحوكمة والملفات والأحداث والمدقق والإقلاع من زر واحد.',
    slow: true,
  },
  {
    id: 'safety', title: 'بوابة سلامة التنفيذ',
    desc: 'يمنع التشغيل إذا كانت 578 بلا حارس الفيضان ومقارنة صورة الوسيط الفعلية.',
  },
  {
    id: 'security', title: 'فحص الأمان وخزنة الأسرار',
    desc: 'يفحص طبقة الأمان والخزنة بلا عرض أي قيمة سرية. الخزنة غير المنشأة تظهر كتنبيه واضح.',
  },
  // أمر المالك 2026-08-13: الفحوص اللي كانت ملفّات بجذر المشروع صارت أزرارًا هون.
  {
    id: 'bridge', title: 'فحص جسر التداول (ميتاتريدر 5)',
    desc: 'يقرأ قاعدة الجسر: هل الإكسبرت شغّال وكاتب؟ الحساب والمراكز والأوامر والصفقات — أرقام حقيقية من الملف نفسه.',
  },
  {
    id: 'ctrader', title: 'فحص تغذية سي-تريدر',
    desc: 'يتأكد أن قارئ سي-تريدر عم يكتب تكّات وعمق فعليًا (لازم يطلع رقم أكبر من صفر). قراءة فقط.',
  },
  {
    id: 'gate', title: 'فحص بوّابة التنفيذ',
    desc: 'قراءة تشخيصيّة من رسالة صحّة 552 فقط — لا يحسم مفتوحة/مقفولة (هالمصدر لا يحمل enabled). الحالة الحيّة الدقيقة بصفحة «الرئيسية» أو «التنفيذ».',
  },
  {
    id: 'health', title: 'فحص صحّة الذرات الحيّة',
    desc: 'يسأل النواة الشغّالة عن حالة كل ذرة: سليمة، أو مجوّعة، أو واقفة — مع رسالة كل وحدة.',
  },
  {
    id: 'versions', title: 'فحص تطابق النسخ',
    desc: 'برهان ثلاثي: نسخة الكود = نسخة البطاقة = النسخة المحمّلة بالنواة الحيّة. أي افتراق يفشّل الفحص.',
  },
  {
    id: 'hedge', title: 'فحص عقد التحوّط (الحاسبة المستقلّة)',
    desc: 'حاسبة مكتوبة من الورقة وحدها، تقارن أرقامها بأرقام محرّك المركز الحقيقي حالة حالة.',
    slow: true,
  },
  {
    id: 'hedge_chain', title: 'فحص سلسلة التحوّط حتى البوّابة',
    desc: 'يمرّر قرارًا وهميًا عبر الذرات الحقيقية من محرّك المركز حتى بوّابة التنفيذ — ولا أمر يوصل السوق.',
    slow: true,
  },
  {
    id: 'weights', title: 'فحص عقد الأوزان (حساسية الاتجاه)',
    desc: 'حاسبة مستقلّة تثبت: كم مصدرًا اتجاهيًا يلزم لكل عتبة قوّة، وأن صوتًا واحدًا يبقى عاجزًا عن صنع إجماع.',
  },
  {
    id: 'contract405', title: 'فحص عقد 405 (الانعكاس)',
    desc: 'حاجزان معًا: الحواجز البنيوية (405 بلا شراء/بيع · 413 يستثنيه · 453 لا يعلنه)، وطرف-لطرف من بنية الاتجاه حتى القرار — انعكاس وحده لازم ينتهي انتظار بصافي صفر.',
  },
  {
    id: 'contract409', title: 'فحص عقد 409 (المدى)',
    desc: 'نفس حارس 405 بنفس المنطق، على توأمها: 409 بلا شراء/بيع · 413 يستثنيه · 453 لا يعلنه · ومدى وحده لازم ينتهي انتظار بصافي صفر.',
  },
  {
    id: 'contract166', title: 'فحص عقد 166 (دمج التحليل)',
    desc: 'يثبت أن 166 مجمّع سياقي مشتق من 151/152 — يبقى بالمقام ولا يدخل البسط الاتجاهي، ولا يستطيع صنع اتجاه وحده.',
  },
  {
    id: 'conviction', title: 'فحص عقد القناعة (الجذور الخمسة)',
    desc: 'الدرجة علم تحقّق ثابت (100/0) لا مقياس قوّة، والثقة تنتقل من الأب كما هي بلا تبديل ولا قصّ — ويُثبت أن الحساب يستعملها فعلًا.',
  },
  {
    id: 'budget', title: 'فحص عقد الميزانيّة (الخسارة المحقّقة)',
    desc: 'يثبت أن K بقي كما نصّ الدستور (لا يصير سالبًا)، وأن الخسارة المحقّقة صار لها مسار مستقلّ يوصلها للحارس — بخسارة محقّقة وبلا أي طفو، عبر الذرات الحقيقية 518 ← 519 ← 581.',
  },
  {
    id: 'stop', title: 'فحص عقد الستوب الفيزيائي (الملاذ الأخير)',
    desc: 'يثبت أن أمر الفتح يحمل ستوبًا حقيقيًا عند الوسيط، وأنه أوسع من مسافة الميزانيّة فتضرب الميزانيّة أولًا، وأن حالة صافي التعرض يساوي صفرًا لها بديل معلَن لا قيمة غائبة صامتة، وأن 512/525 يتّفقان رقمًا برقم — والسلسلة كاملة 578←586←585←516←551←584←552 ثم قاعدة الإكسبرت نفسها: غياب الوقف غير قابل للوصول.',
  },
  {
    id: 'dispatch', title: 'فحص عقد مسار التنفيذ (مصدر قرار واحد)',
    desc: 'يثبت أن 467 ما عاد ينشر أمر تنفيذ وأن 581 هو المنتج الوحيد لهالمسار، وأن حكم الفلاتر (454/460/466) يوصل 581 كمدخل صريح ومقفول افتراضًا — وأن قرارًا يرفضه الفلتر ما بيقدر يوصل التنفيذ بأي طريق.',
  },
  {
    id: 'specs', title: 'فحص عقد بثّ مواصفات الرموز (618)',
    desc: 'يشغّل 618 الحقيقية على جسر إس كيو لايت فعلي: الإعلان الأول يشتغل، والمستهلك اللي يبدأ متأخّر توصله المواصفات بلا ما يظهر رمز جديد، ونافذة العمى محروسة بسقف معلَن ما بيقدر يكبر بصمت.',
  },
  {
    id: 'shutdown', title: 'فحص عقد الإيقاف النظيف واللقطة',
    desc: 'بعمليّات حقيقية منفصلة: إشارة من الأب للابن ← إشارة الإيقاف ← التقاط كل الحالات ← ملفّات على القرص ← عمليّة ثانية تستعيد نفس الحالة حقلًا بحقل. ومرساة حرفية على كتلة الإيقاف بـملف تشغيل النواة حتى ما ينحرف الاختبار عن الكود. ما بيشغّل النواة الحقيقية ولا بيلمس مجلد اللقطات.',
    slow: true,
  },
  {
    id: 'protection', title: 'فحص عقد بقاء الحماية (إيقافك وتجميدك)',
    desc: 'إيقافك الطارئ وتجميدك وتحريرك اليدوي ما بينبنوا من أي حدث — لازم ينجوا. الفحص بعمليتين منفصلتين: بيصنع الحالات الأربع، بيموّت العملية، وبيتأكد إنها رجعت بنفس معناها (تحرير يدوي بيرجع تحرير يدوي مش مجمّد). وبيخرّب اللقطة عمدًا ليتأكد إن الفشل بيقفل مش بيفتح.',
    slow: true,
  },
  {
    id: 'held', title: 'فحص عقد كتاب الاتّجاه (الانعكاس عبر الحياد)',
    desc: 'يعرض الخرق بعينه: محرّك محتفظ بكتابه بيرفض الانقلاب (انعكاس عبر الحياد)، ومحرّك فقد كتابه بعد إقلاع بينقلب فورًا لمركز معاكس كامل. وبيثبت إن اللقطة بترجّع الكتاب، وما بتفرضه لو الكتاب الحيّ معاكس، ولو اللقطة تالفة ما بيفتح أي اتجاه.',
  },
  {
    id: 'limits', title: 'فحص عقد دفاتر الحدود (بعد الإيقاف النظيف)',
    desc: 'بعد ما صار الإيقاف النظيف شغّال، 611 بيحفظ آخر صفّ قرأه وما بيعيد البث — فدفاتر الحدود بتبدأ فاضية. الفحص بيثبت لكل ذرّة لحالها (506 · 507 · 658 · 666) إنها بتنجو، وإن 508 مشتقّة فعلًا من المراكز فما بتحتاج حفظ، وإن اللقطة الفاسدة بتخلّي الحدّ حارس مش مفتوح.',
    slow: true,
  },
  {
    id: 'hotreload', title: 'فحص عقد بقاء الحماية بالترقية الحيّة',
    desc: 'الترقية الحيّة بتبني نسخة جديدة بلا لقطة، و`حدث الإيقاف الطارئ` مش قابل للإعادة — فكان إيقافك بيضيع. الفحص بيشتغل على الناقل الحقيقي: بيبني الحالة، بينشرها، بيعمل نسخة جديدة متل المُحمِّل الحيّ بالضبط، وبيتأكد إن آخر أحداث الحالة وصلها وإن الحالة رجعت بنفس معناها بلا حلقة راجعة — والفاسد بيقفل مش بيفتح.',
  },
  {
    id: 'deltavis', title: 'فحص عقد رؤية فشل أوامر الفرق',
    desc: 'أوامر الفرق (أوامر الفروق) ما إلها إعادة ولا تصعيد — و578 كانت ساكتة عنها تمامًا: تفاصيل الصحّة نفسها حرفيًّا قبل الرفض وبعده، وحتى تهدئة الحارس ما بتتحرّك لأن الرفض ما بينادي `تسجيل الفشل`. الفحص بيثبت إن الفشل صار مرئي (عدّاد + سبب + تهدئة الحارس الحقيقيّة)، وإن ولا شي تاني تحرّك: نفس الأمر بالضبط، ولا زوج مصطنع، ولا تصعيد جديد، و576 و552 ما انلمسوا.',
  },
  {
    id: 'reqid', title: 'فحص عقد هويّة معرّف الطلب',
    desc: 'عدّاد 578 بيرجع صفر مع كل ترقية حيّة، و585 بيكتب فوق نفس `معرّف الطلب` بلا فحص — فبينولد معرّف جديد وحجزه القديم لسّا حيّ. الفحص بيثبت أوّلًا إنّو ولا مستهلك بالمشروع كلّه (ولا الإكسبرت) بيفسّر شكل المعرّف، وإن السلسلة الحقيقيّة بتمرّره حرفيًّا مهما كان عدد أجزاؤه — وبعدين بيجرّب 3 حالات ترقية (بعد ثانية · بنفس الثانية · قبل أوّل نبضة) ولازم بالثلاثة تطلع هويّة مختلفة قطعًا.',
    slow: true,
  },
  {
    id: 'alignment', title: 'فحص عقد مواءمة المرجع',
    desc: '582 كان بيقارن آخر تكّة سي‑تريدر بآخر تكّة ميتاتريدر 5 بلا مواءمة زمنيّة — فصار «الانحراف» بيقيس حركة السعر بين العيّنتين، لا فرق الوسيطين. مقيس حيًّا: 49.2٪ من الوقت «منحرف» والحجب شغّال، بينما التقادم 0.4٪ بس. الفحص بيثبت إن جوّا نافذة 0٫15 ثانية الانحراف الحقيقي فوق 50 لسّا بيطلع «منحرف»، وبرّاتها بتصير «غير متزامن» وما بتحجب، والحدّ نفسه (فرق الزمن يساوي 0٫15) محسوم بت ببت — وإن 578 والعتبة ومسار الحجب ما انلمسوا.',
  },
  {
    id: 'switches', title: 'فحص أمان مفاتيح الحرّاس',
    desc: 'مفتاح مفتاح التفعيل (بوّابة السوق 552 ومرسل الإدارة 575) كان بينكتب من مربّع نصّ بلا تحقّق — وكلمة «لا» كانت بتنحفظ نصًّا وقراءة «لا» كقيمة منطقية بيطلع صحيح **فتنفتح البوّابة**. الفحص بيثبت إنّو ما بينقبل غير قيمة منطقيّة صريحة («صحيح» أو «خطأ»)، وإن كل قيمة تانية بترفض بلا ما ينكتب حرف واحد بالبطاقة، وإن الأرقام وحدودها ما تغيّرت، وإن اللوحة صارت تعرضه مفتاح حقيقي — والذرّات وبوّابة 901 ما انلمسوا.',
  },
  {
    id: 'stoppath', title: 'فحص مسار الإيقاف واللقطة',
    desc: '«ملف غرفة القيادة عند الإيقاف» كان بيقتل النواة بـالقتل القسري فورًا — و17 ذرّة بتحفظ لقطة (إيقافك وتجميدك · كتاب الاتجاه · دفاتر الحدود) كانت بتضيع كلّها كل مرّة. الفحص بيثبت على عمليّات حقيقيّة: الساعي بينجح ⇒ إيقاف نظيف بلا القتل القسري أبدًا · الساعي بيفشل ⇒ بينزل للقسري **فورًا وبسبب مكتوب** · نواة عنيدة ⇒ بتنقتل بعد المهلة بلا تعليق أبدي · والمهلة نفسها مشتقّة من قياس (ذيل 0٫98 ثانية × 10).',
    slow: true,
  },
  {
    id: 'decimals', title: 'فحص عقد إعداد خانات السعر',
    desc: 'بطاقة 512 بتعلن خانات السعر العشرية **إلزامي**، والكود كان بيقرّب بثابت مكتوب — يعني الإعداد زينة: تعدّلو من اللوحة وتترقّى الذرّة وما بيتغيّر رقم واحد. الفحص بيثبت إنّو بينقرا فعليًا (2/5/8 بتعطي 3 نتائج مختلفة)، وإن القيمة الفاسدة بتنرفض **بإعلان حالة** لا برمي استثناء: لا سعر وقف بيطلع، والصحّة بتقول «خانات سعر غير صالحة».',
  },
  {
    id: 'telegram', title: 'فحص منصّة تلغرام (610)',
    desc: 'هل هي مفعّلة ومقترنة وشغّالة؟ ويثبت حواجزها: أوامرها تمرّ من بوّابة 901 نفسها بتأكيد بخطوتين، لا تكتب جسر الأوامر مباشرة، ما فيها أمر شراء/بيع، مقفولة على محادثتك وحدك، وجسر التداول عندها قراءة فقط — والتوكن لا يظهر أبدًا.',
  },
  {
    id: 'storagecap', title: 'فحص سقوف المخازن',
    desc: 'مخزن بلا سقف بايت بينمو لَيملا القرص. المقيس 2026-08-18: `analysis.db` كبرت 11٫87 جيجا/يوم وصفّها 28 ك.ب — فسقف الصفوف (مليون) أوّل ما بيعضّ عند 28 جيجا، و`retention_days: 90` بدّو 89 يوم، وأرشفة 714 بدّها 60. ولا واحد بيوصل. الفحص بيثبت شقّين: كل مخزن معلن سقف موجب **ومثبَّت بعقده** (`minimum: 1` فما بيرجع الصفر قيمة صالحة)، وإنّ السقف **بيقصّ فعلاً** على قاعدة حقيقيّة — والصفر ما بيقصّ ولا صفّ.',
  },
  {
    id: 'snapbutton', title: 'فحص زرّ اللقطة (نقطة الرجوع)',
    desc: 'زرّ «خُد لقطة» كان بيموت بـ`[Errno 9] Bad file descriptor` **بعد** ما يكتب الأرشيف ويتحقّق منّو، عند خطوة المتانة الأخيرة — لأنّ `os.fsync` على ملفّ مفتوح للقراءة فقط بيرفضها ويندوز ويقبلها لينكس. وبعدين مسار الخطأ بيحذف الأرشيف السليم، فما بيبقى لا نقطة رجوع ولا أثر. الفحص بيستدعي الزرّ **فعلاً** وبيقرا ناتجه من القرص: بيفتح؟ بيمرق `testzip`؟ فيه كل الملفّات الجوهريّة ووصفة الاسترجاع؟ ⚠️ كل تشغيلة بتترك لقطة حقيقيّة (~3٫5 م.ب) — وهاد مقصود، لأنّ فحص نسخة احتياطيّة بلا نسخة ما بيثبت شي.',
  },
]

function summarize(id: string, r: ToolResult): { text: string; good: boolean; warn?: boolean } {
  const out = r.output
  if (id === 'seal') {
    if (out.includes('سليمة ومطابقة')) return { text: '🟢 النواة سليمة ومطابقة للختم', good: true }
    return { text: '🛑 الختم ما تطابق — افحص فورًا', good: false }
  }
  if (id === 'validator') {
    const m = out.match(/فُحصت\s+(\d+)\s+ذرة[\s\S]*?مخالفات\s+(\d+)\s*·\s*تحذيرات\s+(\d+)/)
    if (m) {
      const bad = Number(m[2])
      return {
        text: bad === 0 ? `🟢 ${m[1]} ذرة — صفر مخالفات` : `🛑 ${m[2]} مخالفة من ${m[1]} ذرة`,
        good: bad === 0,
      }
    }
  }
  if (id === 'boot') {
    return out.includes('أثبت 212/212')
      ? { text: '🟢 الإقلاع الفعلي: 212/212', good: true }
      : { text: '🛑 الإقلاع لم يثبت 212/212', good: false }
  }
  if (id === 'safety') {
    return out.includes('EXECUTION_SAFETY=READY')
      ? { text: '🟢 بوابة سلامة التنفيذ جاهزة', good: true }
      : { text: '🛑 التنفيذ محجوب: 578 تحتاج حارس الفيضان', good: false }
  }
  if (id === 'security') {
    if (out.includes('SECURITY_STATE=READY')) return { text: '🟢 طبقة الأمان والخزنة جاهزتان', good: true }
    if (out.includes('SECURITY_STATE=NOT_CONFIGURED')) return { text: '🟠 كود الأمان سليم، لكن الخزنة غير منشأة', good: false, warn: true }
    if (out.includes('SECURITY_STATE=DEPENDENCY_MISSING')) return { text: '🟠 مكتبة الأمان ناقصة — شغّل تجهيز البيئة', good: false, warn: true }
    return { text: '🛑 الأمان أو الخزنة غير جاهزين', good: false }
  }
  if (id === 'project') {
    return out.includes('فحص المشروع البنيوي ناجح')
      ? { text: '🟢 فحص المشروع الكامل ناجح', good: true }
      : { text: '🛑 فحص المشروع يحتاج تحقيق', good: false }
  }
  if (id === 'contract405' || id === 'contract409' || id === 'conviction' || id === 'contract166'
      || id === 'budget' || id === 'stop' || id === 'dispatch' || id === 'specs'
      || id === 'shutdown' || id === 'protection' || id === 'held' || id === 'limits'
      || id === 'hotreload' || id === 'deltavis' || id === 'reqid' || id === 'alignment'
      || id === 'switches' || id === 'stoppath' || id === 'decimals'
      || id === 'storagecap' || id === 'snapbutton') {
    // لا نتيجة خضراء جزئية: أي حاجز يسقط = FAIL، حتى لو باقي السطور خضراء.
    const atom = id === 'contract405' ? '405' : id === 'contract409' ? '409'
      : id === 'contract166' ? '166' : id === 'budget' ? 'الميزانيّة'
      : id === 'stop' ? 'الستوب الفيزيائي'
      : id === 'dispatch' ? 'مسار التنفيذ'
      : id === 'specs' ? 'بثّ المواصفات'
      : id === 'shutdown' ? 'الإيقاف واللقطة'
      : id === 'protection' ? 'بقاء الحماية'
      : id === 'held' ? 'كتاب الاتّجاه'
      : id === 'limits' ? 'دفاتر الحدود'
      : id === 'hotreload' ? 'بقاء الحماية بالترقية'
      : id === 'deltavis' ? 'رؤية فشل الفرق'
      : id === 'reqid' ? 'هويّة معرّف الطلب'
      : id === 'alignment' ? 'مواءمة المرجع'
      : id === 'switches' ? 'أمان المفاتيح'
      : id === 'stoppath' ? 'مسار الإيقاف'
      : id === 'decimals' ? 'خانات السعر'
      : id === 'storagecap' ? 'سقوف المخازن'
      : id === 'snapbutton' ? 'زرّ اللقطة' : 'القناعة'
    const m = out.match(/الاختلافات\s*=\s*(\d+)/)
    if (!m) return { text: '🛑 الفحص ما كمّل — ما طلع عدّاد الاختلافات', good: false }
    const diffs = Number(m[1])
    if (diffs === 0 && r.ok) return { text: `🟢 عقد ${atom} سليم — حواجز بنيوية + طرف-لطرف، صفر اختلاف`, good: true }
    return { text: `🛑 عقد ${atom} انكسر — ${diffs} اختلاف (شوف الخام)`, good: false }
  }
  if (id === 'telegram') {
    // ثلاث حالات صادقة: حاجز ساقط · حواجز سليمة وناقصها فعل بيدك · سليمة وشغّالة.
    if (!r.ok) return { text: '🛑 حاجز من حواجز المنصّة ساقط — شوف الخام', good: false }
    if (out.includes('مفعّلة · مقترنة · شغّالة')) return { text: '🟢 تلغرام شغّال ومقترن — بوّابته هي بوّابة اللوحة', good: true }
    if (out.includes('غير مفعّلة بعد')) return { text: '🟠 الحواجز سليمة — ناقصها توكن من مدير البوتات', good: false, warn: true }
    if (out.includes('لم تقترن')) return { text: '🟠 مفعّلة — أرسل رمز الاقتران من موبايلك', good: false, warn: true }
    if (out.includes('متوقّفة')) return { text: '🟠 مفعّلة ومقترنة لكنها متوقّفة — شغّل غرفة القيادة', good: false, warn: true }
    return { text: '🟠 حالة غير معروفة — شوف الخام', good: false, warn: true }
  }
  if (['governance', 'files', 'events'].includes(id)) {
    return r.ok
      ? { text: '🟢 الفحص ناجح', good: true }
      : { text: '🛑 الفحص فشل', good: false }
  }
  if (id.startsWith('crypto_')) {
    // فحوص أسمر تكتب حكمها بآخر سطر معلَّم — تُعرض كما هي بلا إعادة صياغة،
    // ولها حالة ثالثة صادقة: 🟠 «سليم بنيويًّا وواقف بانتظار مدخله» ليست فشلًا.
    const lines = out.split(String.fromCharCode(10)).map((l) => l.trim()).filter(Boolean)
    const last = [...lines].reverse().find((l) => /^(🟢|🛑|🟠)/.test(l))
    if (!last) return { text: r.ok ? '🟢 خلصت بنجاح' : `🛑 فشلت (رمز ${r.code})`, good: r.ok }
    if (last.startsWith('🟠')) return { text: last, good: false, warn: true }
    return { text: last, good: r.ok && last.startsWith('🟢') }
  }
  return r.ok
    ? { text: '🟢 خلصت بنجاح', good: true }
    : { text: `🛑 فشلت (رمز ${r.code})`, good: false }
}

// قاموس المصطلحات المعروفة → عربي؛ والمجهول يبقى ظاهرًا كما هو. القناع القديم
// («رمز تقني» لكل كلمة لاتينية) كان يمسخ نتائج الفحوص كلها فلا يُقرأ منها شيء —
// عكس مقصد قاعدة «لا إنكليزي»: الصدق المقروء أولى من إخفاء الحقيقة.
const TECH_AR: Record<string, string> = {
  BLOCKED: 'محجوب', READY: 'جاهز', HOLD: 'تثبيت', ADD: 'إضافة', REDUCE: 'تقليص',
  REBALANCE: 'موازنة', HEDGE: 'تحوّط', WARNING: 'تحذير', FROZEN: 'مجمّد',
  PAUSED: 'موقَف', NORMAL: 'طبيعي', HEDGING: 'تحوّط', NETTING: 'تصفية',
  UNKNOWN: 'مجهول', PENDING: 'معلّق', PASSED: 'مرّت', FAILED: 'فشلت',
  ERROR: 'خطأ', OK: 'سليم', HEALTHY: 'سليمة', DEGRADED: 'متعثّرة',
  action: 'الفعل', status: 'الحالة', reason: 'السبب',
  target_net: 'الصافي_المستهدف', target_gross: 'الإجمالي_المستهدف',
  target_buy: 'الشراء_المستهدف', target_sell: 'البيع_المستهدف',
  delta_buy: 'فرق_الشراء', delta_sell: 'فرق_البيع', delta_net: 'فرق_الصافي',
  SYSTEM_NOT_ALIVE: 'النظام_غير_حي', PORTFOLIO_STATE_MISSING: 'حالة_المحفظة_غائبة',
  MISSING_R_PRICE_DIAL_OR_SPECS: 'ناقص_ميزانية_أو_سعر_أو_عيار_أو_مواصفات',
  HARD_STOP_FROZEN: 'الستوب_الصلب_مجمّد', PORTFOLIO_FROZEN: 'المحفظة_مجمّدة',
  NEUTRAL_KEEP_GROSS: 'حياد_مع_حفظ_الإجمالي', RISK_REBALANCE: 'موازنة_مخاطر',
  NO_DIRECTION: 'بلا_اتجاه', NETTING_UNSUPPORTED: 'التصفية_غير_مدعومة',
  ACCOUNT_MODE_UNKNOWN: 'نمط_الحساب_مجهول', True: 'صحيح', False: 'خطأ',
}
const arabicOutput = (value: string) =>
  value.replace(/[A-Za-z][A-Za-z0-9_.\\/:-]*/g, (token) => TECH_AR[token] ?? token)

function ToolCard({ id, title, desc, slow }: { id: string; title: string; desc: string; slow?: boolean }) {
  const [state, setState] = useState<'idle' | 'running' | 'done'>('idle')
  const [result, setResult] = useState<ToolResult | null>(null)
  const [showRaw, setShowRaw] = useState(false)

  const run = async () => {
    setState('running'); setResult(null)
    try {
      const r = await fetch(`/gov/tool/${id}`, { method: 'POST' })
      const j = (await r.json()) as ToolResult & { error?: string }
      setResult(j.error ? { ok: false, code: -1, output: j.error } : j)
    } catch (e) {
      setResult({ ok: false, code: -1, output: 'خطأ اتصال — أعد فتح غرفة القيادة لو المنفذ جديد: ' + String(e) })
    }
    setState('done')
  }

  const sum = result ? summarize(id, result) : null
  return (
    <div className="scard">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div className="st" style={{ fontSize: 15, color: 'var(--ink)' }}>{title}</div>
          <div className="ss dim">{desc}{slow ? ' (بياخد دقايق — استنّى)' : ''}</div>
        </div>
        <button className="btn" disabled={state === 'running'} onClick={run}>
          {state === 'running' ? '⏳ عم يشتغل…' : 'شغّل'}
        </button>
      </div>
      {sum ? (
        <div style={{ marginTop: 10, fontWeight: 700, color: sum.warn ? 'var(--amber)' : sum.good ? 'var(--green)' : 'var(--red)' }}>{sum.text}</div>
      ) : null}
      {result ? (
        <div style={{ marginTop: 6 }}>
          <button className="btn" style={{ fontSize: 11, padding: '3px 10px' }} onClick={() => setShowRaw(!showRaw)}>
            {showRaw ? 'خبّي الخام' : 'ورّيني الخام'}
          </button>
          {showRaw ? (
            <pre dir="ltr" style={{ marginTop: 6, maxHeight: 260, overflow: 'auto', fontSize: 11.5, background: 'rgba(0,0,0,.25)', borderRadius: 8, padding: 10, whiteSpace: 'pre-wrap' }}>
              {arabicOutput(result.output || '(بلا مخرجات)')}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

interface BackupScan { count: number; last_name?: string; last_ts?: number; last_mb?: number }

function backupLine(title: string, s?: BackupScan): string {
  if (!s || !s.count) return `${title}: لا نسخ بعد`
  const when = s.last_ts ? new Date(s.last_ts * 1000).toLocaleString('ar-EG-u-nu-latn', { hour12: false, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''
  return `${title}: آخر نسخة ${when} (${s.last_mb} م.ب.) · ${s.count} محفوظة`
}

function BackupCard() {
  const [state, setState] = useState<'idle' | 'running' | 'done'>('idle')
  const [msg, setMsg] = useState('')
  const [ok, setOk] = useState(true)
  const [scan, setScan] = useState<{ auto?: BackupScan; manual?: BackupScan } | null>(null)
  const loadScan = () => fetch('/gov/backups').then((r) => r.json()).then(setScan).catch(() => {})
  useEffect(() => { loadScan(); const t = setInterval(loadScan, 30000); return () => clearInterval(t) }, [])
  const run = async () => {
    if (!window.confirm('أخذ لقطة كاملة للنظام (الذرات + النواة + الإعدادات + الورق) — نقطة رجوع بـ مجلد النسخ الاحتياطية؟')) return
    setState('running')
    try {
      const r = await fetch('/gov/backup', { method: 'POST' })
      const j = (await r.json()) as { ok: boolean; message: string }
      setOk(j.ok); setMsg(j.message)
    } catch (e) { setOk(false); setMsg('خطأ اتصال — أعد فتح غرفة القيادة لو المنفذ جديد: ' + String(e)) }
    setState('done'); loadScan()
  }
  return (
    <div className="scard">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div className="st" style={{ fontSize: 15, color: 'var(--ink)' }}>💾 النسخة الاحتياطية الموحّدة</div>
          <div className="ss dim">لقطة كاملة بزر: الذرات + النواة + الإعدادات + الحوكمة + الورق + وصفة الاسترجاع جوّاتها (نقطة رجوع). بيانات السوق الضخمة مستثناة — إلها النسخة الآلية اليومية.</div>
        </div>
        <button className="btn" disabled={state === 'running'} onClick={run}>
          {state === 'running' ? '⏳ عم يلقط…' : 'خُد لقطة'}
        </button>
      </div>
      <div className="ss" style={{ marginTop: 8 }}>
        <div style={{ color: scan?.auto?.count ? 'var(--green)' : 'var(--amber)' }}>● {backupLine('الآلية اليومية (ذرة 800 · بيانات السوق والإعدادات)', scan?.auto)}</div>
        <div style={{ color: scan?.manual?.count ? 'var(--green)' : 'var(--dim)' }}>● {backupLine('اليدوية (لقطات النظام)', scan?.manual)}</div>
      </div>
      {msg ? <div style={{ marginTop: 10, fontWeight: 700, color: ok ? 'var(--green)' : 'var(--red)' }}>{ok ? '🟢 ' : '🛑 '}{msg}</div> : null}
    </div>
  )
}

function RescanCard() {
  const [msg, setMsg] = useState('')
  const run = async () => {
    try {
      const r = await fetch('/gov/rescan', { method: 'POST' })
      setMsg(r.ok ? '🟢 طُلبت إعادة الفحص — أي ذرة جديدة أو مرقّاة بتنزل حيّة خلال ثوانٍ (شوفها بقسم الذرات).' : '🛑 النواة ما ردّت')
    } catch { setMsg('🛑 خطأ اتصال — أعد فتح غرفة القيادة لو المنفذ جديد') }
  }
  return (
    <div className="scard">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div className="st" style={{ fontSize: 15, color: 'var(--ink)' }}>🔎 إعادة فحص الذرات</div>
          <div className="ss dim">يطلب من النواة تلقّط أي ذرة جديدة انبنت أو نسخة اترقّت — تحميل حيّ بلا إعادة تشغيل (خُطّاف النواة الرسمي).</div>
        </div>
        <button className="btn" onClick={run}>افحص</button>
      </div>
      {msg ? <div style={{ marginTop: 10, fontWeight: 700 }}>{msg}</div> : null}
    </div>
  )
}

export default function Scripts() {
  const market = useMarket()
  const crypto = market === 'crypto'
  // فحوص العقود الفوركسيّة (405 · 409 · التحوّط · الجسر · سي-تريدر…) لا معنى لها
  // بقسم أسمر، وفحوص أسمر لا معنى لها بالفوركس. المشتركة (الختم · المدقّق ·
  // الملفّات · الإقلاع · الأمان · الصحّة) تبقى للسوقين.
  const SHARED = ['seal', 'validator', 'tests', 'governance', 'files', 'events',
    'boot', 'project', 'security', 'health', 'versions', 'storagecap', 'snapbutton']
  const list = crypto ? TOOLS.filter((t) => SHARED.includes(t.id)) : TOOLS

  return (
    <div className="section" style={{ display: 'flex', flexDirection: 'column', gap: 14, overflow: 'auto' }}>
      <BackupCard />
      <RescanCard />
      {crypto ? (
        <>
          <div className="scard">
            <div className="st" style={{ fontWeight: 700 }}>فحوصات قسم أسمر (الكريبتو)</div>
            <div className="ss dim">
              تقيس ما يخصّ هذا السوق وحده: تغذيته · سلسلة استراتيجيّته · عزله عن الفوركس ·
              بشريّة تنفيذه · ومطابقته لملفّ أحمد. كلّها قراءة فقط على النظام الحيّ.
            </div>
          </div>
          {CRYPTO_TOOLS.map((t) => <ToolCard key={t.id} {...t} />)}
          <div className="scard">
            <div className="st" style={{ fontWeight: 700 }}>فحوص عامّة (تصلح للسوقين)</div>
            <div className="ss dim">
              فحوص العقود الفوركسيّة (405 · 409 · التحوّط · جسر ميتاتريدر · سي-تريدر…)
              مخفيّة هنا لأنّها لا تقيس شيئًا بهذا السوق — مكانها قسم الفوركس.
            </div>
          </div>
        </>
      ) : null}
      {list.map((t) => <ToolCard key={t.id} {...t} />)}
      <div className="ss dim">
        فحوص الحوكمة والملفات والأحداث والإقلاع والأمان قراءة فقط · ما بتعرض أي قيمة سرية · الاختبارات قد تأخذ دقائق · الترجمة فوق والنتيجة الخام بزر «ورّيني الخام».
      </div>
    </div>
  )
}
