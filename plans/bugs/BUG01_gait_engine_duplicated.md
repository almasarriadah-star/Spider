# BUG01 — محرك المشي مكرّر في ملفين (مصدرا حقيقة)

**الخطورة:** 🟠 متوسطة-عالية (لا عطل وظيفي، لكن خطر تباعد + الهدف من الخطة 03 غير محقّق للمشي)
**الخطة المرتبطة:** 03 — ترتيب ملفات الحركة
**الحالة:** مفتوح

---

## الوصف

الخطة 03 كان هدفها **نقل** محركات المشي إلى `spider/gaits.py`. ما حصل فعلياً: **نُسِخت** إلى `spider/gaits.py` لكن **النسخ الأصلية بقيت في `web_controller.py` وهي التي تعمل فعلاً**. فصار محرك المشي موجوداً مرّتين.

### الدوال المكرّرة (معرّفة في الملفين معاً)
`get_leg_angles`, `_run_forward_gait`, `_run_lateral_gait`, `_run_ripple_gait`, `_run_smooth_ripple`, `foot_arc_trajectory`, `tibia_compensation`, `_load_gait_params`, `_load_ripple_params`.

### أيّ نسخة تعمل؟ (الأهم)
- مسارات `/api/gait/*` (forward/start, once, ripple/start, smooth-ripple/start) → تستدعي **نسخ `web_controller.py` المحلية**.
- `spider/moves.py` (الحركات: `spin`, `gallop`, `moonwalk`) → تستدعي **نسخ `spider/gaits.py`**.

فالمشي العادي والمشي داخل الحركات الاستعراضية يأتيان من **محرّكَين مختلفين**.

---

## الدليل

```
grep "def _run_forward_gait":  web_controller.py = 1  |  spider/gaits.py = 1   ← مكرّر
grep "def get_leg_angles":     web_controller.py = 1  |  spider/gaits.py = 1   ← مكرّر

web_controller.py: مسارات gait تستدعي _run_forward_gait المحلي (أسطر 1278, 1337, 1452, 1719, 1741, 1959)
spider/moves.py:163,410,430:  from spider.gaits import _run_forward_gait   ← تستخدم نسخة gaits
```

### فرق إضافي مهم: التوقيعات مختلفة
- `web_controller._run_forward_gait(params, speed, max_cycles, gait_type)` — يعتمد على **globals** (`gait_event`, `balance`, `gait_running`).
- `spider/gaits._run_forward_gait(..., gait_event=None, gait_running_ref=None, balance=None)` — يعتمد على **حقن تبعيات** (تصميم أنظف).

أي أن النسختين ليستا متطابقتين في الاستدعاء — لا يمكن استبدال إحداهما بالأخرى بمجرد import.

---

## الأثر

1. **مصدرا حقيقة:** ضبط/تعديل المشي في `spider/gaits.py` **لا يؤثر** على المشي العادي (الذي يستخدم نسخة web_controller)، والعكس. مصدر إرباك وأخطاء صيانة مستقبلية.
2. **خطر التباعد:** أي تحسين/إصلاح يُطبَّق على نسخة وينسى الأخرى → سلوك مختلف بين `forward` و`gallop` مثلاً رغم أنهما نفس المشي.
3. **هدف الخطة 03 غير محقّق للمشي:** «ترتيب ملفات الحركة» تمّ للثوابت/IMU/التوازن/الحركات الاستعراضية، لكن **المشي نفسه ما زال في `web_controller.py`** مزدوجاً.
4. التقرير `Report_Plan03` ادّعى «نقل جميع محركات المشي» — غير دقيق؛ الصحيح «نسخ، والأصل ما زال يعمل».

---

## الإصلاح المقترح (توحيد المصدر)

اجعل `spider/gaits.py` **المصدر الوحيد**، واحذف نسخ `web_controller.py`:

1. في `web_controller.py` **احذف** التعريفات المحلية:
   `get_leg_angles`, `_run_forward_gait`, `_run_lateral_gait`, `_run_ripple_gait`, `_run_smooth_ripple`, `foot_arc_trajectory`, `tibia_compensation`, `_load_gait_params`, `_load_ripple_params`.

2. **استورد** من الوحدة:
   ```python
   from spider.gaits import (
       _run_forward_gait, _run_lateral_gait, _run_ripple_gait,
       _run_smooth_ripple, get_leg_angles, _load_gait_params, _load_ripple_params,
   )
   ```

3. **مرّر التبعيات** عند الاستدعاء في مسارات الـ gait. مثال لـ forward/start:
   ```python
   def _set_gait_running(v):
       global gait_running
       gait_running = v

   gait_thread = threading.Thread(
       target=_run_forward_gait,
       args=(params, speed, max_cycles, gait_type),
       kwargs=dict(gait_event=gait_event, gait_running_ref=_set_gait_running, balance=balance),
       daemon=True,
   )
   ```
   كرّر لباقي المسارات (once, ripple/start, smooth-ripple/start, lateral).

4. تأكّد أن `spider/gaits.py` يكتب حالة `gait_current_phase`/`gait_step_count` عبر مرجع مُمرَّر أو يُرجعها، لتبقى `/api/gait/status` صحيحة.

5. اختبر **كل** أنماط المشي + `spin/gallop/moonwalk` بعد التوحيد.

> بديل أبسط (غير موصى به): اعتبر `web_controller` نسخة الـ routes، واجعل `spider/moves.py` يستورد منها — لكن هذا يبقي المشي في `web_controller` ويعاكس هدف الترتيب. الأفضل التوحيد في `spider/gaits.py`.

---

## معايير قبول الإصلاح
- ✅ تعريف واحد فقط لكل دالة مشي (في `spider/gaits.py`).
- ✅ مسارات `/api/gait/*` وحركات `spin/gallop/moonwalk` تستخدم نفس المحرّك.
- ✅ `/api/gait/status` يعرض الحالة صحيحة.
- ✅ سلوك كل أنماط المشي مطابق لما قبل التوحيد.
