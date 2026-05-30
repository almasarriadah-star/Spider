# BUG04 — الإيقاف الطارئ سلوكه مختلف كل مرة

**الخطورة:** 🟠 متوسطة-عالية (سلامة)
**يفسّر العرض:** «الإيقاف الطارئ كل مرة سلوك مختلف».
**الخطة المرتبطة:** 02 (الإيقاف الطارئ)
**الحالة:** ✅ **حُلّ** (2026-05-30) — قطع PWM أولاً + تزامن العلم + حارس estop على البدء

---

## السبب الجذري: ثلاث مشاكل في مسار الطوارئ

### المشكلة A — تأخّر متغيّر قبل قطع PWM (تباين التوقيت)
`api_estop` (web_controller.py:370) يستدعي `balance.stop()` **قبل** `arbiter.emergency_stop()`:
```python
def api_estop():
    _force_stop = True
    gait_running = False
    gait_event.clear()
    balance.stop()              # ← يحجب حتى 2 ثانية (join على خيط التوازن)
    arbiter.emergency_stop()    # ← قطع PWM يتأخّر بمقدار ما يأخذه balance.stop
```
`balance.stop()` ينفّذ `self._thread.join(timeout=2.0)`. فإذا كان التوازن شغّالاً، **يتأخّر قطع PWM حتى ثانيتين**؛ وإذا كان مطفأً، فوري. ← نفس الزر يعطي زمن استجابة مختلف كل مرة = «سلوك مختلف كل مرة».

### المشكلة B — `_force_stop` غير متزامن مع طبقة spider
`api_estop` يضبط `_force_stop = True` على **متغيّر web_controller العام فقط**، بينما خيوط المشي/الحركات في `spider/` تفحص `_force_stop_ref()` = `spider.safety._force_stop_holder[0]` — **متغيّر مختلف تماماً**. فالعلم لا يصل لطبقة spider، و`smooth_move_leg`/`_smooth_move` لا تتوقف عبره (تعتمد فقط على `arbiter.in_estop()`).
- الموجود الصحيح: دالة `_sync_force_stop()` (تضبط الاثنين معاً) — لكن `api_estop`/`api_estop_clear` لا تستعملانها (يستعملها `/api/off` فقط). تناقض.

### المشكلة C — بعد الطوارئ، كل شيء «ميت» بصمت حتى الضغط على «إلغاء الطوارئ»
بعد `emergency_stop()` يبقى `arbiter._estop = True`. أي بدء مشي بعدها:
- حارس البدء **لا يفحص** `arbiter.in_estop()` → يردّ «started» (ok).
- لكن خيط المشي `acquire("gait")` يفشل (estop مضبوط) → مسار الفشل يمسح `gait_event` بصمت ويعود.
- النتيجة: الواجهة تقول «بدأ» والروبوت لا يتحرك ولا رسالة خطأ → يبدو «عشوائياً/ميتاً». ولو لم يعرف المستخدم أن عليه ضغط «إلغاء الطوارئ»، يظنّ النظام معطوباً.

---

## الإصلاح المقترح

### `api_estop` — اقطع أولاً، ووحّد العلم
```python
@app.route("/api/estop", methods=["POST"])
def api_estop():
    global gait_running
    arbiter.emergency_stop()    # 1) أولاً: قطع PWM/طاقة فوري (لا تأخير)
    _sync_force_stop(True)       # 2) وحّد العلم في web + spider
    gait_running = False
    gait_event.clear()
    try:
        balance.stop()           # 3) أخيراً: إيقاف التوازن (البطيء)
    except Exception:
        pass
    return jsonify({"ok": True, "estop": True})
```

### `api_estop_clear` — استخدم المزامنة
```python
@app.route("/api/estop/clear", methods=["POST"])
def api_estop_clear():
    _sync_force_stop(False)      # بدل _force_stop = False
    arbiter.clear_estop()
    return jsonify({"ok": True, "estop": False})
```

### حارس البدء — رسالة واضحة عند الطوارئ
في كل روتات بدء المشي (forward/start, once, ripple/start, smooth-ripple/start):
```python
if arbiter.in_estop():
    return jsonify({"ok": False, "error": "الطوارئ مفعّل — اضغط «إلغاء الطوارئ» أولاً"}), 409
```
وكذلك يُفضّل في `body_move`/`special_move` (عبر `run_body`/`run_special` التي تفشل أصلاً عند estop لأن `acquire` يرفض — لكن أضف رسالة واضحة).

### (واجهة) أظهر حالة الطوارئ
في `gait_forward_status` أضف `"estop": arbiter.in_estop()`، وفي الواجهة اعرض شارة «⛔ الطوارئ مفعّل — اضغط إلغاء» ما دامت مضبوطة، فلا يحتار المستخدم.

---

## معايير قبول الإصلاح
- ✅ زمن استجابة الطوارئ ثابت وفوري (قطع PWM قبل أي عملية بطيئة).
- ✅ علم `_force_stop` متزامن بين web و spider.
- ✅ بعد الطوارئ، محاولة المشي تعطي رسالة واضحة «اضغط إلغاء الطوارئ» بدل صمت.
- ✅ بعد «إلغاء الطوارئ» كل شيء يعود طبيعياً.

---

## ✅ ما تم تنفيذه فعلاً (2026-05-30)

**`api_estop`** أُعيد ترتيبه: `arbiter.emergency_stop()` **أولاً** (قطع فوري ثابت)، ثم `_sync_force_stop(True)` (يوحّد علم web + spider)، ثم `gait_event.clear()`، وأخيراً `balance.stop()` داخل `try/except` (البطيء في النهاية فلا يؤخّر القطع).

**`api_estop_clear`** صار يستخدم `_sync_force_stop(False)` بدل `_force_stop = False`.

**حُرّاس البدء** للحلقات الأربع أُضيف لها:
```python
if arbiter.in_estop():
    return jsonify({"ok": False, "error": "الطوارئ مفعّل — اضغط «إلغاء الطوارئ» أولاً"}), 409
```
و**`/api/gait/forward/status`** صار يرجّع `"estop": arbiter.in_estop()` لتعرضه الواجهة.

**إثبات (المحكّم الحقيقي):**
- `set_force_stop(True/False)` ينعكس فوراً على `_force_stop_ref()` (طبقة spider) ✅
- بعد `emergency_stop()`: `in_estop()=True` و`acquire("gait")=False` (البدء مرفوض) ✅
- بعد `clear_estop()`: `in_estop()=False` و`acquire("gait")=True` ✅
