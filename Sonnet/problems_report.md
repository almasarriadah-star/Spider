# 🕷 تقرير المشاكل — تعليق النظام + عدم الحركة

**التاريخ:** 2026-05-20  
**الملف المصدر:** `web_controller.py` (1902 سطر)

---

## 🔴 المشكلة الرئيسية: النظام بيتعلّق كامل

بعد فترة قصيرة من تشغيل أي نوع مشي (خطي أو سلس)، **كل النظام بيتجمّد** — حتى واجهة الويب ما بتستجيب.

---

## 🔍 الأسباب المكتشفة (مرتبة حسب الخطورة)

### 1. 🔴🔴🔴 `bno.heading` بيسبّد (BLOCKING SERIAL READ)

**المكان:** `read_bno_single()` سطر 41
```python
yaw, pitch, roll, ax, ay, az = bno.heading  # ← هنا بيتعلّق!
```

**المشكلة:** `bno.heading` بياخد البيانات من `/dev/serial0`. إذا البفر فارغ أو IMU توقفت، **القراءة بتحجب (block) للأبد** — ما فيها timeout حقيقي.

**التأثير:**
- `bno_lock` بيظل قفل → **كل اللي بيستنى `bno_lock` بيتعلّق**
- الـ Balance Loop بيتعلّق داخل `with bno_lock`
- الـ API endpoint `/api/imu/read` بيتعلّق
- حتى `/api/imu/zero` بيتعلّق

**الحل:** لازم نضيف timeout صريح أو نقرأ بـ thread منفصل مع timeout.

---

### 2. 🔴🔴🔴 `_ensure_servos_on()` بتنطبق **كل مرة** `set_servo` بتنتدبغ

**المكان:** `set_servo()` سطر 119-120
```python
def set_servo(key, angle):
    _ensure_servos_on()  # ← كل مرة! حتى لو شغّالة
```

**المشكلة:** `_ensure_servos_on()` بتفحص `_servos_initialized` — OK. بس خلال `apply_startup_calibration()` بيتحرك 18 محرك مع `time.sleep(0.03)` = **0.54 ثانية**. 

بس المشكلة الأكبر: كل مكالمة `set_servo` بتعمل function call + global check. خلال المشي، `set_servo` بتنتدبغ **مئات المرات بالثانية** (18 محرك × 3-10 steps × ~30-60 frames).

**التأثير:** overhead كبير على الـ CPU — خاصة على Raspberry Pi.

**الحل:** `_ensure_servos_on()` لازم تكون سريعة (boolean check فقط — وهي كده)، بس `apply_startup_calibration` لازم ما تتكرر.

---

### 3. 🔴🔴 `smooth_move_leg` — **قفل كامل خلال الحركة**

**المكان:** سطر 217-232
```python
def smooth_move_leg(keys, targets, steps=10, delay=0.03, easing=None):
    for step in range(1, steps + 1):
        if _force_stop:
            return
        for k in keys:
            set_servo(k, round(angle))  # ← I2C write لكل محرك
        time.sleep(delay)
```

**المشكلة:** 
- كل step بيعمل **18 كتابة I2C** (9 right + 9 left) لتحديث الزوايا
- بعدها `time.sleep(delay)` 
- في Tripod: `steps=6, delay=0.02` → كل فريم = 6 × (18 I2C + 0.02s) = **~0.22 ثانية**
- في Ripple: `steps=3, delay=0.01` → كل فريم = 3 × (18 I2C + 0.01s) = **~0.11 ثانية**
- **60 فريم لكل دورة Ripple** → **6.6 ثانية لكل دورة** 

هاد يعني الـ gait loop بياخد **100% من وقت الـ thread** وما بيخلي مجال لحدا تاني.

---

### 4. 🔴🔴 الـ Balance Loop و Gait Loop بيقتلوا على `set_servo`

**المشكلة:** K threads بيحاولوا يكتبوا على نفس الـ PCA9685 عبر I2C بنفس الوقت:
- **Gait thread** → `smooth_move_leg` → `set_servo` → I2C write
- **Balance thread** → `_apply_corrections` → `set_servo` → I2C write

**مافي أي قفل على I2C!** 
- الـ `bno_lock` بيحمي IMU بس
- مافي lock يحمي `right_pca` و `left_pca`

**التأثير:** 
- I2C bus contention → كتابات خاطئة
-PCA9685 ممكن يدخل بحالة غريبة
- **Segmentation fault** أو kernel I2C errors

---

### 5. 🔴🔴 `_force_stop` ما بيتصفر أبداً تلقائياً

**المكان:** سطر 201 و `api_off` سطر 556-561
```python
_force_stop = True    # api_off
gait_running = False
time.sleep(0.05)
_force_stop = False   # OK هنا بيتصفر
```

**المشكلة:** إذا حد ضغط "إيقاف طارئ" وطلع `KeyboardInterrupt` بين `True` و `False`، الـ `_force_stop` بيضل `True` → **كل حركة مستقبيلة بتكون مستحيلة!**

ما في طريقة لصرّفه من الـ UI.

---

### 6. 🔴 مشكلة المشي ما يتحرك للأمام

**السبب:** `get_leg_angles()` (سطر 235) للـ Tripod — الـ direction logic:

```python
direction = 1 if side == "R" else -1
# R: زيادة Coxa = أمام
# L: نقصان Coxa = أمام
```

**المشكلة المحتملة:** 
- الـ `coxa_amplitude = 22` مع `total_frames = 8`
- كل فريم = `22/4 = 5.5°` تحرك — صغير جداً
- الـ swing بيكون من `-amp` لـ `+amp` → range = `44°` → محرك بيمشي من `68°` لـ `112°`
- **بس الـ stance بيكون عكسه بالضبط** → صافي الحركة = **صفر!**

الروبوت بيحرك أرجله بس ما بتتقدم لأن الـ swing و stance بيكونوا بنفس الحجم وبيلغوا بعض.

**الحل:** لازم نتأكد إنه الـ stance بيكون **أطول** من الـ swing، أو إنه في translation حقيقي.

---

### 7. 🔴 مشكلة مشابهة في Ripple و Smooth Ripple

**Ripple** (سطر 1274-1275):
```python
lift  = max(0, math.sin(ph)) * LIFT_DEG
swing = math.cos(ph) * SWING_DEG * sign
```

- `swing = cos(ph) * 20` → يتأرجح بين -20 و +20
- coxa يتأرجح حول `coxa_stand` → **بترجع لنفس المكان!**
- ما في net displacement لأن sin/cos دورية

**السبب الجذري:** الكود بيحسب **وضعية** كل فريم، مش **إزاحة**. 
كل فريم بيحط الأرجل بموقع متناسب مع `sin(ph)` → بعد دورة كاملة، `sin` بيرجع لـ 0 → **الأرجل بترجع لمكانها الأول!**

---

### 8. 🟡 `gait_running` — متغير عام بدون حماية

```python
gait_running = False  # thread 1 بيغيره
# thread 2 بيقرأه
```

**المشكلة:** لا `volatile` ولا `threading.Event` — Python's GIL بيحمي جزئياً بس مش مضمون.
الأحسن استخدام `threading.Event()`.

---

### 9. 🟡 `debug_history` — قائمة بتنكبر للأبد (شبه)

```python
self.debug_history.append(entry)
if len(self.debug_history) > self.MAX_HISTORY:
    self.debug_history.pop(0)
```

`MAX_HISTORY = 100` — محدود، OK. بس `pop(0)` على قائمة = **O(n)** كل مرة.

---

## 📋 ملخص المشاكل مرتبة بالأولوية

| # | المشكلة | خطورتها | تأثيرها |
|---|---------|---------|---------|
| 1 | `bno.heading` blocking read | 🔴🔴🔴 | تعليق كامل للنظام |
| 2 | لا I2C lock بين threads | 🔴🔴🔴 | كتابات خاطئة + تعليق |
| 3 | `smooth_move_leg` بطيء (18×I2C × steps) | 🔴🔴 | حركة خشنة + CPU عالي |
| 4 | المشي ما يتحرك (sin/cos دورية) | 🔴🔴 | الروبوت يحرك أرجل بس ما يمشي |
| 5 | `_force_stop` ممكن يضل True | 🔴 | استحالة الحركة بعد إيقاف |
| 6 | `gait_running` بدون thread-safety | 🟡 | race conditions |

---

## ✅ الحلول المطلوبة

### الحل 1: IMU timeout (أهم شي!)
```python
def read_bno_single():
    if not BNO_READY:
        return None
    try:
        # ضع timeout صريح على القراءة
        old_timeout = uart.timeout
        uart.timeout = 0.1  # 100ms max
        yaw, pitch, roll, ax, ay, az = bno.heading
        uart.timeout = old_timeout
        return {...}
    except Exception:
        return None
```

### الحل 2: I2C Lock
```python
i2c_lock = threading.Lock()

def set_servo(key, angle):
    _ensure_servos_on()
    angle = limit_angle(angle)
    side = key[0]
    ch = int(key[1:])
    if HARDWARE:
        with i2c_lock:  # ← حماية
            if side == "R":
                right_pca.servo[ch].angle = angle
            else:
                left_pca.servo[ch].angle = angle
    current[key] = angle
```

### الحل 3: gait_running كـ Event
```python
gait_event = threading.Event()

# بدل while gait_running:
while gait_event.is_set():
    ...

# بدل gait_running = True:
gait_event.set()

# بدل gait_running = False:
gait_event.clear()
```

### الحل 4: إصلاح عدم الحركة
المشكلة الجوهرية: **الكود بيحسب وضعية نسبية (position-based)، مش إزاحة (displacement-based).**

لازم يتغير المنطق:
- **Swing phase:** القدم تتحرك من موقع خلفي لموقع أمامي (إزاحة حقيقية)
- **Stance phase:** القدم تتحرك من أمامي لخلفي (دفع حقيقي)
- **بعد كل دورة كاملة:** الروبوت يكون متقدم فعلاً

الحل الأبسط: نحسب **offset تراكمي** — كل دورة نزود offset ثابت على `coxa_stand`.

### الحل 5: تصفير `_force_stop`
إضافة endpoint:
```python
@app.route('/api/reset', methods=['POST'])
def api_reset():
    global _force_stop
    _force_stop = False
    return jsonify({"ok": True})
```

---

## 🎯 الخلاصة

**سبب التعليق الرئيسي:** `bno.heading` حجب (blocking) + لا I2C lock → threads بتقتل على بعض.

**سبب عدم الحركة:** `sin/cos` دورية بترجع الأرجل لنفس المكان بعد كل دورة كاملة → ما في إزاحة صافية.

**الإصلاح المطلوب فوراً:**
1. IMU timeout
2. I2C lock
3. إصلاح منطق المشي (displacement-based)
