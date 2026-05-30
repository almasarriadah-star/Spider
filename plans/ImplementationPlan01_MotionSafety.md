# خطة 01 — أمان الحركة والإيقاف الطارئ الحقيقي

> الأولوية: 🔴 حرجة — تُنفّذ **قبل** أي شيء آخر.
> تحلّ: مشكلة انهيار السيرفوات (DEVELOPMENT_LOG §«مشكلة مفتوحة») + أخطاء التدقيق #1، #2، #4، #5، #11، #12.

---

## ملخص تنفيذي

السبب الجذري للانهيار ليس فقط الكهرباء — بل **أن أكثر من جهة تكتب المحركات في نفس اللحظة، ولا يوجد قطع تغذية فيزيائي، وزر الطوارئ نفسه يُطلق حركة متزامنة لـ 18 محرك**. الحل من أربع طبقات:

1. **مالك حركة واحد (Motion Arbiter):** thread وحيد يكتب المحركات عبر طابور. كل شيء آخر (مشي، حركات استعراضية، توازن، body move) يصبح «طلباً» لا يكتب مباشرة.
2. **إيقاف طارئ فيزيائي:** GPIO pin → MOSFET/Relay يقطع تغذية كل السيرفوات فوراً، مستقل عن I2C تماماً.
3. **انتقالات آمنة:** أي انتقال لوضعية جديدة يمرّ عبر مُخطِّط يحدّ سرعة الزاوية لكل محرك ويُسلسِل الأحمال الكبيرة (ما ننقل 18 محرك دفعة لو الفرق كبير).
4. **حدود لكل محرك + إصلاح زر الطوارئ في الواجهة** ليطفئ لا أن يحرّك.

---

## لماذا (التحليل التقني)

### المشكلة 1: تعدد الكتّاب على I2C
حالياً هذه الجهات كلها تكتب `set_servo/set_servos_batch` مباشرة:
- `_run_forward_gait` / `_run_ripple_gait` / `_run_smooth_ripple` / `_run_lateral_gait` (thread المشي)
- `_move_*` الـ 23 حركة استعراضية (thread منفصل لكل واحدة عبر `special_move`)
- `body_move` (في thread الـ request مباشرةً!)
- `BalanceController._loop` (thread التوازن، يكتب عبر `_apply_corrections`)
- `apply_startup_calibration` / `api_calibrate` / `api_off`

`i2c_lock` يمنع تلف البايتات لكنه **لا يمنع التضارب المنطقي**: حركتان تتبادلان الكتابة على نفس المحرك بقيم متناقضة بسرعة → السيرفو يهتز بعنف ويسحب تيار ذروة عالٍ → هبوط جهد → انهيار. وبما أن `special_move` لا يفحص `gait_event`، يمكن إطلاق حركة استعراضية أثناء المشي = الكارثة بالضبط.

### المشكلة 2: لا قطع فيزيائي
الإيقاف عبر I2C ينتظر بالطابور خلف `i2c_lock`. لو المحرك انحشر، الأمر يصل متأخراً. نحتاج خطاً مستقلاً تماماً.

### المشكلة 3: قفزات زاوية كبيرة
`flatten`→`stand` أو `prowl` تتطلب ~20–60° لبعض المحركات. تحريكها كلها معاً = تيار ذروة. نحتاج تحديد سرعة زاوية (deg/step) وتسلسل.

---

## الكود الكامل

### 1) وحدة الهاردوير مع قطع GPIO — `spider/hardware.py`

```python
# spider/hardware.py
"""طبقة الهاردوير: PCA9685 + قاطع تغذية GPIO + قفل I2C موحّد."""
import threading
import time

# ── إعداد PCA9685 ──
HARDWARE = False
right_pca = left_pca = None
try:
    from adafruit_servokit import ServoKit
    right_pca = ServoKit(channels=16, address=0x40)
    left_pca = ServoKit(channels=16, address=0x44)
    HARDWARE = True
except Exception:
    pass

# ── قاطع التغذية الفيزيائي (GPIO + MOSFET/Relay) ──
# ⚠️ وصّل بوابة MOSFET قناة-N (مثل IRLZ44N) أو Relay module على هذا الـ pin.
#    عند HIGH = التغذية واصلة، عند LOW = مقطوعة. (اعكس المنطق لو Relay فعّال-منخفض)
POWER_GPIO_PIN = 17          # ⚠️ عدّل حسب توصيلك
POWER_ACTIVE_HIGH = True     # ⚠️ True لو MOSFET، False لو Relay فعّال-منخفض

_gpio = None
try:
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(POWER_GPIO_PIN, GPIO.OUT)
    _gpio = GPIO
except Exception:
    _gpio = None

# ── قفل I2C موحّد لكل المشروع ──
i2c_lock = threading.Lock()

# حالة التغذية
_power_on = False
_power_lock = threading.Lock()


def power_enable():
    """يصل تغذية السيرفوات فيزيائياً."""
    global _power_on
    with _power_lock:
        if _gpio:
            _gpio.output(POWER_GPIO_PIN, _gpio.HIGH if POWER_ACTIVE_HIGH else _gpio.LOW)
        _power_on = True


def power_cut():
    """يقطع تغذية كل السيرفوات فوراً — لا يمرّ عبر I2C إطلاقاً."""
    global _power_on
    with _power_lock:
        if _gpio:
            _gpio.output(POWER_GPIO_PIN, _gpio.LOW if POWER_ACTIVE_HIGH else _gpio.HIGH)
        _power_on = False


def is_powered():
    return _power_on


def pwm_release_all():
    """يوقف إشارة PWM لكل القنوات (duty=0) — يكمّل القطع الفيزيائي."""
    if not HARDWARE:
        return
    with i2c_lock:
        try:
            for ch in range(16):
                right_pca._pca.channels[ch].duty_cycle = 0
                left_pca._pca.channels[ch].duty_cycle = 0
        except Exception:
            pass


def write_servo_raw(key, angle):
    """كتابة محرك واحد مباشرة — يُستخدم فقط من داخل MotionArbiter."""
    if not HARDWARE:
        return
    side = key[0]
    ch = int(key[1:])
    with i2c_lock:
        if side == "R":
            right_pca.servo[ch].angle = angle
        else:
            left_pca.servo[ch].angle = angle


def write_batch_raw(updates):
    """كتابة دفعة محركات تحت قفل واحد — يُستخدم فقط من داخل MotionArbiter."""
    if not HARDWARE:
        return
    with i2c_lock:
        for key, angle in updates.items():
            side = key[0]
            ch = int(key[1:])
            if side == "R":
                right_pca.servo[ch].angle = angle
            else:
                left_pca.servo[ch].angle = angle
```

### 2) حدود لكل محرك — `config/servo_limits.json`

```json
{
  "_comment": "حدود زاوية آمنة لكل قناة. L3 (LM Coxa) أضيق لأنه قرب حدّه الميكانيكي.",
  "default": {"min": 45, "max": 135},
  "overrides": {
    "L3": {"min": 60, "max": 120},
    "R3": {"min": 55, "max": 125}
  }
}
```

تحميلها:
```python
import json, os
_LIM = json.load(open(os.path.join("config", "servo_limits.json")))
def servo_limit(key, angle):
    d = _LIM["default"]
    o = _LIM["overrides"].get(key, {})
    lo = o.get("min", d["min"]); hi = o.get("max", d["max"])
    return max(lo, min(hi, int(round(angle))))
```

### 3) المالك الوحيد للحركة — `spider/safety.py`

الفكرة: thread واحد (`MotionArbiter`) يملك حلقة كتابة بتردد ثابت (مثلاً 50Hz). كل شيء آخر يضع **هدفاً** (`set_target`) أو **حركة مُخطَّطة** (`enqueue_trajectory`). المُخطِّط يحدّ سرعة الزاوية لكل محرك ويُسلسِل لو الحمل كبير. الإيقاف الطارئ يقطع GPIO ويفرّغ الطابور.

```python
# spider/safety.py
import threading
import time
from collections import deque
from spider import hardware
from spider.config import servo_limit   # من البند 2

# ترتيب القنوات
ALL_KEYS = [f"{s}{c}" for s in ("R", "L") for c in range(9)]

# الحالة الحالية المعروفة (مصدر الحقيقة الوحيد)
current = {k: 90 for k in ALL_KEYS}


class MotionArbiter:
    """المالك الوحيد لكتابة المحركات. حلقة بتردد ثابت تقرّب الوضع الحالي نحو الهدف
    بسرعة زاوية محدودة، وتُسلسِل الأحمال الكبيرة لتفادي تيار الذروة."""

    def __init__(self, rate_hz=50, max_deg_per_tick=4.0, max_simultaneous=18):
        self.dt = 1.0 / rate_hz
        self.max_deg_per_tick = max_deg_per_tick    # سقف سرعة الزاوية لكل محرك
        self.max_simultaneous = max_simultaneous    # كم محرك يتحرك فعلياً في tick واحد
        self.target = dict(current)
        self._lock = threading.Lock()
        self._estop = threading.Event()             # مضبوط = طوارئ
        self._owner = None                          # اسم الحركة المالكة حالياً
        self._stop = threading.Event()
        self._thread = None

    # ── واجهة الطلبات (يستدعيها أي مكوّن) ──
    def acquire(self, owner):
        """يطلب ملكية الحركة. يُرجع True لو نجح. يمنع تعدد الكتّاب."""
        with self._lock:
            if self._estop.is_set():
                return False
            self._owner = owner
            return True

    def release(self, owner):
        with self._lock:
            if self._owner == owner:
                self._owner = None

    def owner(self):
        return self._owner

    def set_target(self, owner, updates):
        """يضع أهدافاً جديدة (dict key->angle). تُطبَّق تدريجياً بسرعة محدودة."""
        with self._lock:
            if self._estop.is_set() or (self._owner not in (owner, None)):
                return False
            for k, a in updates.items():
                self.target[k] = servo_limit(k, a)
            return True

    # ── الإيقاف الطارئ ──
    def emergency_stop(self):
        """قطع فيزيائي فوري + تفريغ الأهداف. لا ينتظر I2C."""
        self._estop.set()
        hardware.power_cut()        # 1) اقطع التغذية فوراً (GPIO)
        hardware.pwm_release_all()  # 2) صفّر PWM (أفضل جهد عبر I2C، لكنه ثانوي)
        with self._lock:
            self._owner = None
            self.target = dict(current)  # الهدف = المكان الحالي (لا حركة عند العودة)

    def clear_estop(self):
        """يلغي حالة الطوارئ ويعيد التغذية (لا يحرّك — الهدف=الحالي)."""
        with self._lock:
            self.target = dict(current)
        self._estop.clear()
        hardware.power_enable()

    def in_estop(self):
        return self._estop.is_set()

    # ── الحلقة الرئيسية ──
    def start(self):
        hardware.power_enable()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            t0 = time.monotonic()
            if not self._estop.is_set():
                self._tick()
            dt = self.dt - (time.monotonic() - t0)
            if dt > 0:
                time.sleep(dt)

    def _tick(self):
        """يقرّب current نحو target بخطوة محدودة، ويُسلسِل لو في محركات كثيرة بعيدة."""
        with self._lock:
            # احسب الفروق
            diffs = []
            for k in ALL_KEYS:
                d = self.target[k] - current[k]
                if abs(d) > 0.5:
                    diffs.append((abs(d), k, d))
            if not diffs:
                return
            # رتّب حسب الأكبر فرقاً، وحرّك فقط أكبر N (تسلسل الحمل)
            diffs.sort(reverse=True)
            batch = {}
            for _, k, d in diffs[: self.max_simultaneous]:
                step = max(-self.max_deg_per_tick, min(self.max_deg_per_tick, d))
                nv = servo_limit(k, current[k] + step)
                current[k] = nv
                batch[k] = nv
        hardware.write_batch_raw(batch)

    def shutdown(self):
        self._stop.set()


# نسخة وحيدة عامة
arbiter = MotionArbiter()
```

### 4) واجهة حركة مريحة فوق المالك — `spider/motion.py`

تستبدل `smooth_move_leg`. تنتظر حتى يصل الوضع للهدف (مع timeout) بدل عدّ خطوات يدوي.

```python
# spider/motion.py
import time
from spider.safety import arbiter, current

def goto(owner, targets, timeout=4.0, tol=1.5):
    """يضع الهدف وينتظر وصوله (أو timeout). الحركة الفعلية يديرها MotionArbiter
    بسرعة آمنة. يُرجع True لو وصل."""
    if not arbiter.set_target(owner, targets):
        return False
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if arbiter.in_estop():
            return False
        if all(abs(current[k] - a) <= tol for k, a in targets.items()):
            return True
        time.sleep(0.02)
    return False

def stream(owner, target_fn, fps=25):
    """للمشي: يستدعي target_fn() كل إطار ليحصل على dict أهداف، ويبثّها.
    target_fn يُرجع None لإنهاء."""
    period = 1.0 / fps
    while True:
        if arbiter.in_estop():
            return
        tgt = target_fn()
        if tgt is None:
            return
        arbiter.set_target(owner, tgt)
        time.sleep(period)
```

> ملاحظة مهمة: مع MotionArbiter، **لم نعد نحرّك المحركات بأنفسنا داخل دوال المشي**. دالة المشي فقط تحسب «أهداف الإطار» وتسلّمها. الأمان (حدّ السرعة/التسلسل/الطوارئ) صار في مكان واحد.

### 5) إصلاح زر الطوارئ في الواجهة

استبدل دالة `emergencyStop()` في `spider_gait_controller.html`:

```javascript
async function emergencyStop() {
  // إيقاف حقيقي: قطع تغذية فيزيائي — لا حركة معايرة!
  await fetch('/api/estop', {method:'POST'});
  document.getElementById('gait-status').textContent = '⛔ طوارئ — التغذية مقطوعة';
  document.getElementById('smoothStatus').textContent = '⛔ طوارئ';
  // أوقف كل المؤقتات
  [_gaitPoll, smoothPoll, balPoll].forEach(p => p && clearInterval(p));
  _gaitPoll = smoothPoll = balPoll = null;
  document.getElementById('btnBalStart').style.display = '';
  document.getElementById('btnBalStop').style.display = 'none';
}

async function clearEstop() {   // زر جديد «إلغاء الطوارئ»
  await fetch('/api/estop/clear', {method:'POST'});
  document.getElementById('gait-status').textContent = '⬛️ جاهز';
}
```

### 6) Routes جديدة للطوارئ

```python
@app.route("/api/estop", methods=["POST"])
def api_estop():
    arbiter.emergency_stop()
    return jsonify({"ok": True, "estop": True, "power": hardware.is_powered()})

@app.route("/api/estop/clear", methods=["POST"])
def api_estop_clear():
    arbiter.clear_estop()
    return jsonify({"ok": True, "estop": False, "power": hardware.is_powered()})
```

### 7) إقلاع آمن متسلسل

استبدل `apply_startup_calibration` بحيث يضع الهدف فقط ويترك MotionArbiter يقرّبه بأمان:

```python
def apply_startup_calibration():
    defaults = load_leg_defaults()
    targets = {}
    for group, keys in LEG_GROUPS.items():
        for key in keys:
            base = DEFAULT_RIGHT[int(key[1:])] if key[0]=="R" else DEFAULT_LEFT[int(key[1:])]
            targets[key] = defaults.get(group, {}).get(key, base)
    # المالك «startup»؛ MotionArbiter يقرّب بحد 4°/tick ويُسلسِل تلقائياً
    arbiter.acquire("startup")
    motion.goto("startup", targets, timeout=8.0)
    arbiter.release("startup")
```

---

## خطوات التطبيق (بالترتيب)

1. **الهاردوير أولاً:** ركّب MOSFET/Relay على خط تغذية السيرفوات، وصّله بـ GPIO17 (أو أي pin)، واختبر `power_cut()`/`power_enable()` بمصباح LED قبل توصيل المحركات.
2. أضف `spider/hardware.py` و`spider/safety.py` و`spider/motion.py` و`config/servo_limits.json`.
3. شغّل `arbiter.start()` عند بدء `web_controller.py` (قبل `app.run`).
4. حوّل **دالة واحدة فقط أولاً** (`_run_forward_gait`) لتستخدم `arbiter.set_target` بدل `smooth_move_leg` (انظر الخطة 03). اختبرها.
5. أضف `/api/estop` و`/api/estop/clear` وعدّل زر الطوارئ بالواجهة. **اختبر الطوارئ قبل أي حركة أخرى.**
6. حوّل باقي دوال الحركة تدريجياً.
7. اجعل `BalanceController` يكتب عبر `arbiter.set_target("balance", …)` بدل `set_servo` المباشر، ويملك الحركة فقط حين لا يوجد مالك آخر.
8. احذف `/api/calibrate` من مسار زر الطوارئ نهائياً (احتفظ به كزر معايرة منفصل واضح).

---

## الاختبار

| اختبار | المتوقّع |
|--------|----------|
| اضغط الطوارئ أثناء المشي | كل المحركات تتوقف فوراً (تغذية مقطوعة)، لا اهتزاز |
| أطلق حركة استعراضية أثناء المشي | تُرفض (مالك مشغول) — لا تضارب |
| انتقال `flatten`→`stand` | تدريجي، لا تيار ذروة، لا انهيار |
| افصل GPIO يدوياً | المحركات ترتخي فوراً |
| `clear_estop` بعد طوارئ | تعود التغذية، لا حركة مفاجئة (الهدف=الحالي) |
| راقب تيار المصدر أثناء انتقال كبير | لا يتجاوز ~حدّ المصدر (بفضل `max_simultaneous`) |

---

## معايير القبول

- ✅ زر الطوارئ يقطع الحركة فيزيائياً خلال < 50ms ولا يُطلق أي حركة.
- ✅ مستحيل أن تكتب جهتان المحركات في آن واحد (مالك واحد).
- ✅ لا انهيار للسيرفو في انتقال `flatten`→`stand` ولا في `prowl`.
- ✅ `L3` لا يتجاوز [60,120] مهما حصل.
- ✅ الإقلاع لا يسبب قفزة متزامنة كبيرة.

---

## ملاحظات هاردوير مكمّلة (من DEVELOPMENT_LOG)

نفّذها بالتوازي لأنها تحلّ السبب الكهربائي الأساسي:
- مصدر **6V / 10A+** مستقر مخصّص للسيرفوات (منفصل عن تغذية الـ Pi).
- مكثّف **1000µF+** على خط تغذية كل PCA9685.
- أسلاك تغذية اليسار بنفس سماكة اليمين وأقصر ما يمكن.
- (لاحقاً، الخطة 04) حساس تيار **INA219** على خط السيرفو → قطع تلقائي لو تجاوز التيار حداً.
```
