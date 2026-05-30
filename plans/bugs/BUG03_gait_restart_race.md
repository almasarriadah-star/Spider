# BUG03 — المشي يعمل أول مرة فقط + الوقوف لا يعمل (سباق ملكية المحكّم)

**الخطورة:** 🔴 عالية (يعطّل تشغيل الروبوت فعلياً)
**يفسّر الأعراض:** «التقدّم للأمام يمشي صح أول مرة فقط، بعدها لا» + «ما عاد يعمل وقوف» + «المشي مش صح كل مرة».
**الخطة المرتبطة:** 01 (المحكّم) + 03 (الهيكلة)
**الحالة:** ✅ **حُلّ** (2026-05-30) — إيقاف متزامن + حارس خيط حيّ في `web_controller.py`

---

## الأعراض (كما وصفها المستخدم)
1. أول ضغطة «تقدّم» → مشي صحيح. الضغطات التالية → لا يمشي صح أو لا يمشي.
2. زر «وقوف» (الرجوع للوضعية الأساسية) ما عاد يشتغل.

---

## السبب الجذري: سباق على ملكية المحكّم عند إعادة التشغيل السريع

النظام الجديد يفرض **مالكاً واحداً** للحركة عبر `arbiter.acquire("gait")`. المشكلة في **طريقة الإيقاف غير المتزامنة** + **السماح بإعادة الاكتساب لنفس المالك**:

### تسلسل الخطأ (خطوة بخطوة)
1. ضغط «تقدّم #1» → `gait_event.set()`، يبدأ `thread1`، ينجح `acquire("gait")`، يمشي.
2. ضغط «وقوف» (`/api/gait/forward/stop`, سطر 869): فقط `gait_event.clear()` **ويعود فوراً** — **لا ينتظر** الخيط.
3. `thread1` يخرج من الحلقة، لكنه يبقى **يملك "gait"** أثناء «الرجوع لوضعية الوقوف» (`smooth_move_leg(steps=12)` ≈ 0.4 ثانية).
4. ضغط «تقدّم #2» خلال هذه الـ 0.4 ثانية:
   - حارس البدء (سطر 815) `if gait_event.is_set()` → **مكشوف** (الحدث مُسِح في الخطوة 2) → يمرّ.
   - يبدأ `thread2`، ينفّذ `acquire("gait")` → المالك ما زال "gait" (يحمله thread1) → **إعادة اكتساب لنفس المالك مسموحة** (`safety.py:54`) → ينجح! → الآن **خيطان** يمشيان معاً.
5. `thread1` ينهي «الرجوع للوقوف»، فيُنفّذ `finally` (gaits.py:281-283):
   - `gait_event.clear()` → **يمسح الحدث الذي يحتاجه thread2** → حلقة thread2 `while gait_event.is_set()` تصبح False → **thread2 يتوقف فوراً بعد إطار واحد**.
   - `arbiter.release("gait")` → المالك = None رغم أن thread2 كان «يملك» نفس الاسم.

### النتيجة
- المشي الثاني يمشي جزءاً من الثانية ثم يتوقف، أو يهتز (خيطان يتنازعان الأهداف). ← «مش صح كل مرة».
- تبقى الملكية بحالة غير متّسقة. وإذا حاول المستخدم «وقوف» (`bodyMove('stand')` → `run_body` → `acquire("body")`) بينما "gait" ما زال مملوكاً (من خيط لم ينتهِ) → **يُرفض (409)** → **الوقوف لا يعمل**.

### لماذا «أول مرة فقط»؟
أول دورة (بعد الإقلاع) المالك = None نظيف، فتعمل. بعد أول دورة، يدخل النظام في حالة السباق أعلاه لأن المستخدم بطبيعته يضغط «وقوف» ثم «تقدّم» بسرعة (أقل من 0.4 ثانية).

---

## الدليل (من الكود)
- `web_controller.py:869` `gait_forward_stop`: `gait_event.clear()` فقط، **بلا `join`**.
- `web_controller.py:815` حارس البدء يفحص `gait_event.is_set()` فقط (لا يفحص هل خيط المشي ما زال حياً).
- `spider/safety.py:54` `acquire`: `if self._owner is None or self._owner == owner` → إعادة اكتساب لنفس المالك مسموحة.
- `spider/gaits.py:281-283` `finally`: `gait_event.clear()` + `arbiter.release("gait")` — تمسح الحدث وتحرّر الملكية حتى لو بدأ خيط جديد.

---

## الإصلاح المقترح

**المبدأ:** الإيقاف يجب أن يكون **متزامناً** (ينتظر تحرير الملكية)، ومنع بدء خيط مشي جديد ما دام خيط قديم حياً.

### 1) اجعل الإيقاف متزامناً — `gait_forward_stop` (+ نظائره: ripple/smooth-ripple)
```python
@app.route("/api/gait/forward/stop", methods=["POST"])
def gait_forward_stop():
    global gait_running
    gait_running = False
    gait_event.clear()
    t = gait_thread
    if t and t.is_alive():
        t.join(timeout=2.0)     # انتظر تحرير الملكية والرجوع للوقوف
    return jsonify({"ok": True, "status": "stopped"})
```

### 2) امنع تعدّد خيوط المشي — حارس البدء (forward/start, once, ripple/start, smooth-ripple/start)
```python
if gait_event.is_set() or (gait_thread is not None and gait_thread.is_alive()):
    return jsonify({"ok": False, "error": "already running"})
```

### 3) (حزام أمان) لا تمسح gait_event من داخل `finally` الخيط
الأفضل أن **روتات الإيقاف وحدها** (أو اكتمال `max_cycles`) تتحكم بالحدث. في `spider/gaits.py` احذف `gait_event.clear()` من الـ`finally`، وبدلها — للإيقاف الطبيعي بعد `max_cycles` — امسح الحدث صراحةً قبل الخروج من الحلقة:
```python
# داخل الحلقة عند اكتمال الدورات:
if max_cycles > 0 and cycles >= max_cycles:
    if gait_event: gait_event.clear()
    break
...
finally:
    if phase_ref is not None: phase_ref[0] = "idle"
    if gait_running_ref is not None: gait_running_ref[0] = False
    arbiter.release("gait")     # ← بلا gait_event.clear()
```

### 4) (اختياري لكنه أنظف) امنع إعادة اكتساب «gait» لنفس الاسم من خيطين
بما أن البندين 1+2 يمنعان وجود خيطي مشي، يصبح هذا غير ضروري. لكن لمزيد من الصلابة، يمكن إعطاء كل خيط مشي معرّفاً فريداً (`f"gait-{id}"`) بدل اسم ثابت "gait"، فيمتنع التداخل تماماً.

> مع البندين 1+2 وحدهما تُحلّ المشكلة: الإيقاف ينتظر، والبدء يُرفض ما دام خيط حياً، فلا سباق ولا ملكية عالقة → الوقوف يعمل، والمشي يعمل كل مرة.

---

## معايير قبول الإصلاح
- ✅ «تقدّم» يعمل بشكل متطابق في كل مرة، حتى لو أُعيد فوراً بعد «وقوف».
- ✅ «وقوف» (`bodyMove('stand')`) يعمل دائماً بعد إيقاف المشي.
- ✅ لا يوجد أبداً خيطا مشي معاً.
- ✅ `arbiter.owner()` يعود `None` بعد أي إيقاف.

---

## ✅ ما تم تنفيذه فعلاً (2026-05-30)

**القرار:** الإصلاح كله في `web_controller.py` فقط (لم نلمس منطق المشي في `gaits.py`). بما أن البندين 1+2 يضمنان عدم وجود خيطي مشي معاً، أصبح `gait_event.clear()` داخل `finally` **غير ضار** (يطبّق على الخيط الوحيد فقط) ويبقى مطلوباً للإيقاف الطبيعي بعد `max_cycles` — فأبقيناه.

أُضيف مساعدان:
```python
def _gait_busy():
    # يفحص is_alive() وليس الحدث فقط — الخيط يبقى مالكاً أثناء الرجوع للوقوف
    return gait_event.is_set() or (gait_thread is not None and gait_thread.is_alive())

def _stop_gait_sync(timeout=2.0):
    global gait_running
    gait_running = False
    gait_event.clear()
    t = gait_thread
    if t is not None and t.is_alive():
        t.join(timeout=timeout)   # ← ينتظر تحرير الملكية فعلاً
```
- **روتات الإيقاف** (`forward/stop`, `ripple/stop`, `smooth-ripple/stop`) → تستدعي `_stop_gait_sync()` (متزامنة).
- **حُرّاس البدء** (`forward/start`, `once`, `ripple/start`, `smooth-ripple/start`) → `if _gait_busy(): return already running` + فحص `arbiter.in_estop()`.

**إثبات (محاكاة بخيط حقيقي + المحكّم الحقيقي):**
| السيناريو | acquire("body") لـ«stand» | owner بعد الإيقاف |
|-----------|---------------------------|-------------------|
| القديم (مسح فقط) | `False` ← الوقوف يفشل | عالق |
| الجديد (مسح + join) | `True` ← الوقوف يعمل | `None` |
