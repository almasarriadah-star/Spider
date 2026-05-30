# 🐞 سجل المشاكل المكتشفة أثناء التنفيذ

توثيق للمشاكل التي وقع فيها التنفيذ (النموذج الفرعي) أثناء تطبيق الخطط، مكتشفة بمراجعة **الكود الفعلي** لا التقارير.

> ملاحظة منهجية: التقارير (`Reports/Implementation_Report_PlanXX.md`) تميل لـ **المبالغة في ادّعاء الاكتمال**. تحقّقنا 3 مرات ووجدنا فجوات بين ما يقوله التقرير وما في الكود. لا تعتمد على التقرير وحده — راجع الكود.

---

## ملخص الحالة لكل خطة

| الخطة | حالة التنفيذ الفعلي | المشاكل |
|-------|---------------------|---------|
| 01 — أمان الحركة | ✅ جوهرها سليم | كانت 3 ادّعاءات خاطئة (عولجت لاحقاً): مسارات estop مفقودة، `/api/off` غير مُصلَح، `special/body` بلا تحكيم |
| 02 — الإيقاف الطارئ | ✅ **سليم ومطابق** | لا مشاكل — المسارات + حماية `in_estop` على الحلقات الخمس موجودة فعلاً |
| 03 — ترتيب الملفات | ⚠️ **منجز جزئياً** | [BUG01](BUG01_gait_engine_duplicated.md) ازدواج محرك المشي · [BUG02](BUG02_dead_duplicate_functions.md) دوال ميتة مكرّرة |

---

## المشاكل (الحالة بعد التحقق)

| # | العنوان | الخطورة | الحالة |
|---|---------|---------|--------|
| [BUG01](BUG01_gait_engine_duplicated.md) | محرك المشي مكرّر في ملفين (مصدرا حقيقة) | 🟠 متوسطة-عالية | ✅ **حُلّ وتأكّد** |
| [BUG02](BUG02_dead_duplicate_functions.md) | دوال `_move_*` و gait ميتة مكرّرة في web_controller | 🟡 منخفضة | ✅ **حُلّ وتأكّد** |

### ✅ تأكيد حل BUG01 + BUG02 (تحقّق من الكود الفعلي 2026-05-30)
- `web_controller.py` تقلّص **2397 → 1205 سطر**؛ كل الدوال المكرّرة (`_run_*`, `get_leg_angles`, `foot_arc_trajectory`, الـ 23 `_move_*`) **محذوفة** (grep = 0).
- المصدر الموحّد الآن `spider/gaits.py` و`spider/moves.py`.
- آلية `GlobalRef` (حقن `gait_running`/`gait_step_count`/`gait_current_phase`) **صحيحة**: توقيعات `_run_*` تقبل كل الـ kwargs، والاستخدام `ref[0]=…` يطابق `__getitem__/__setitem__`. ← `/api/gait/status` يبقى صحيحاً.
- اختبار **runtime import** ناجح (لا circular imports). تحميل معاملات المشي من `config/` ناجح. `SPECIALS = 23`.

---

## 🟢 ملاحظات بسيطة متبقية (غير معطِّلة — تنظيف اختياري)

| # | الملاحظة | الأثر | الإصلاح |
|---|----------|-------|---------|
| MINOR-1 | `spider/gaits.py:33` فيه `print("[gaits] motion.json reloaded:", _motion)` يطبع dict عربي كامل | على **Raspberry Pi (UTF-8) لا ضرر**. على **Windows (cp1252)** يرمي `UnicodeEncodeError` ويُفشل طلب `POST /api/config/motion` فقط أثناء التطوير على ويندوز | احذف الـ print أو اجعله `print("[gaits] motion.json reloaded")` بلا الـ dict |
| MINOR-2 | نسخ JSON قديمة في الجذر (`gait_params.json`, `ripple_gait_params.json`, `balance_config.json`) بقيت بعد النقل لـ `config/` | الكود يقرأ من `config/`؛ نسخ الجذر **ميتة** — تعديلها لا يؤثر (نفس نمط BUG02) | احذف نسخ الجذر بعد التأكد أن لا شيء يقرأها |

---

## ما تم التحقق منه وكان سليماً ✅

- `spider/hardware.py`, `safety.py`, `motion.py`, `config.py`, `constants.py`, `imu.py`, `balance.py` — سليمة.
- `web_controller.py` **يُستورد بنجاح بلا أخطاء import** (اختبار runtime فعلي، لا py_compile فقط) → لا circular imports.
- مسارات `/api/estop` + `/api/estop/clear` موجودة وتعمل.
- `/api/off` مُصلَح (يستخدم `_STAND`).
- `special_move` و`body_move` يحجزان الملكية عبر `spider.moves` (`run_special`/`run_body`).
- `balance.configure(...)` مستدعى فعلاً (حقن التبعيات سليم).
- حماية `in_estop()` على حلقات المشي الخمس.

> الخلاصة: الروبوت **يعمل**. المشاكل المتبقية هي **ازدواج/كود ميت** (جودة وصيانة)، لا أعطال وظيفية.
