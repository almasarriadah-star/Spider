# BUG02 — دوال `_move_*` ميتة مكرّرة في `web_controller.py`

**الخطورة:** 🟡 منخفضة (كود ميت — لا يؤثر على التشغيل، لكنه يربك ويزيد خطر التعديل بالمكان الخطأ)
**الخطة المرتبطة:** 03 — ترتيب ملفات الحركة
**الحالة:** مفتوح

---

## الوصف

بعد نقل الحركات الاستعراضية إلى `spider/moves.py` وتحويل مسار `/api/special/<name>` لاستخدام `spider.moves.SPECIALS`، **بقيت النسخ القديمة للدوال الـ 23 (`_move_wave` … `_move_moonwalk`) داخل `web_controller.py`** بلا استخدام.

التقرير `Report_Plan03` نفسه أقرّ بهذا:
> «web_controller.py لا يزال يحتوي على دوال `_move_*` القديمة … غير مستخدمة ويمكن حذفها في خطة مستقبلية.»

## الدليل
```
grep "def _move_wave":  web_controller.py = 1  |  spider/moves.py = 1
مسار /api/special/<name> يستورد: from spider.moves import SPECIALS, run_special
→ نسخ web_controller غير مُشار إليها من أي مكان (ميتة)
```

> مرتبط بـ [BUG01](BUG01_gait_engine_duplicated.md): نفس نمط «نُسخ وبقي الأصل». الفرق أن الحركات الاستعراضية **حُوِّل مسارها فعلاً** لـ `spider.moves` (فالقديم ميت تماماً)، بينما المشي **لم يُحوّل** (فالقديم ما زال يعمل — أخطر).

---

## الأثر
- كود ميت (~350 سطر) يضخّم `web_controller.py` ويناقض هدف الترتيب.
- خطر: مطوّر يعدّل `_move_wave` في `web_controller.py` ظنّاً أنه الفعّال، فلا يرى أثراً (الفعّال في `spider/moves.py`).

---

## الإصلاح المقترح
1. **احذف** من `web_controller.py` كل الدوال الـ 23 `_move_*` ومنطق `body_move` القديم (المنقول لـ `spider/moves.py:get_body_targets`).
2. تأكّد أن لا شيء آخر في `web_controller.py` يستدعيها (المسارات تستخدم `spider.moves` أصلاً).
3. شغّل اختبار import + جرّب حركة استعراضية وحركة جسم للتأكد.

> الأفضل تنفيذه **مع** إصلاح [BUG01](BUG01_gait_engine_duplicated.md) في تمريرة تنظيف واحدة، فيتقلّص `web_controller.py` كثيراً ويصبح فعلاً «رقيقاً» كما تنص الخطة 03.

---

## معايير قبول الإصلاح
- ✅ لا تعريف `_move_*` في `web_controller.py`.
- ✅ كل الحركات الاستعراضية وحركات الجسم تعمل (من `spider/moves.py`).
- ✅ `web_controller.py` أصغر بوضوح.
