# خطة 03 — ترتيب ملفات الحركة (نظام الحركات الحالي، زاوية-مباشرة، بلا IK)

> الأولوية: 🔴 عالية وحالية. هدفها تنظيم حركات الروبوت الموجودة فعلاً (التي تعمل وتكفي)
> دون تغيير سلوكها، مع إصلاح الثغرات المتبقية من التحقق.

---

## ملخص تنفيذي

`web_controller.py` صار ~2400 سطر يخلط: هاردوير، مشي، حركات استعراضية، توازن، IMU، و~60 route. صعب الصيانة. نقسّمه لوحدات نظيفة داخل حزمة `spider/` (الموجودة أصلاً من الخطة 01)، **بنفس منطق الحركة الحالي تماماً** — فقط نقل وتنظيم. وفي نفس العملية نُصلِح 3 ثغرات وجدها التحقق.

**مبدأ:** لا نغيّر زوايا أو خوارزميات أي حركة. الحركات الحالية تعمل وكافية. نرتّب فقط.

---

## الثغرات التي نصلحها أثناء الترتيب (من التحقق)

1. **`special_move` و`body_move` غير محميتين بالتحكيم** — التقرير ادّعى حمايتهما لكنهما بلا `acquire/release`. النتيجة: لو شغّال مشي، تكتب الحركة الاستعراضية أهدافاً مُنتحِلةً مالكَ المشي (لأن `set_servos_batch` يستعلم `arbiter.owner()`) → تنازع.
2. **`/api/off` لم يُصلَح** — ما زال فيه خطأ الطرف الأيسر (`ch <= 8` دائماً صحيح → كل المفاتيح تذهب لـ R بقيم اليسار، و`L*` لا يُكتب).
3. **مسارات الطوارئ مفقودة** — تُعالَج في الخطة 02.

---

## بنية الملفات المستهدفة

```
spider/
├── __init__.py          # موجود — يصدّر arbiter, current, goto…
├── hardware.py          # موجود ✅ (PCA9685 + GPIO + i2c_lock)
├── safety.py            # موجود ✅ (MotionArbiter)
├── motion.py            # موجود ✅ (goto/stream)
├── config.py            # موجود ✅ (servo_limit)
├── constants.py         # جديد ← DEFAULT_RIGHT/LEFT, SERVO_NAMES, LEG_GROUPS, _STAND
├── imu.py               # جديد ← BNO085: read_bno_single, zero, stream
├── gaits.py             # جديد ← get_leg_angles + _run_forward/lateral/ripple/smooth
├── moves.py             # جديد ← body_move + الـ 23 حركة استعراضية (_move_*)
├── balance.py           # جديد ← BalanceController + SimplePID
└── api/                 # جديد ← Flask Blueprints (تقسيم الـ routes)
    ├── __init__.py
    ├── servos.py        # /api/status, /api/servo, /api/calibrate, /api/off, presets…
    ├── gait.py          # /api/gait/* , /api/move (لاحقاً)
    ├── moves.py         # /api/body/move, /api/special/*
    ├── balance.py       # /api/balance/*
    ├── imu.py           # /api/imu/*
    └── system.py        # /api/estop, /api/estop/clear, /api/health, /api/reset

config/                  # ملفات JSON (موجودة جزئياً)
├── servo_limits.json    # موجود ✅
├── gait_params.json     # نقل من الجذر
├── ripple_gait_params.json
└── balance_config.json

web_controller.py        # يتقلّص لـ ~40 سطر: ينشئ app، يسجّل blueprints، يشغّل
```

---

## ماذا ينتقل أين (خريطة الترحيل)

| من `web_controller.py` (الحالي) | إلى |
|----------------------------------|-----|
| `DEFAULT_RIGHT/LEFT`, `SERVO_NAMES`, `LEG_GROUPS`, `_STAND`, `MIN/MAX_ANGLE` | `spider/constants.py` |
| `read_bno_single`, `_startup_imu_zero`, متغيرات BNO | `spider/imu.py` |
| `get_leg_angles`, `_run_forward_gait`, `_run_lateral_gait`, `_run_ripple_gait`, `_run_smooth_ripple`, `foot_arc_trajectory`, `tibia_compensation`, `_load_gait_params`, `_load_ripple_params` | `spider/gaits.py` |
| `body_move` المنطق + الـ 23 `_move_*` | `spider/moves.py` |
| `BalanceController`, `SimplePID`, `BALANCE_WEIGHTS`, خرائط القنوات | `spider/balance.py` |
| `smooth_move_leg`, `ease_*` | `spider/motion.py` (توسيع الموجود) |
| كل `@app.route(...)` | `spider/api/*.py` (blueprints) |

> الدوال تُنقل **كما هي** (نفس الأسطر) — فقط تغيّر الـ imports. لا تعديل منطق.

---

## أمثلة كود الترحيل

### `spider/constants.py` (جديد)
```python
# spider/constants.py
"""ثوابت الروبوت — مصدر واحد للأسماء والمجموعات والوضعيات الافتراضية."""
from spider.safety import DEFAULT_RIGHT, DEFAULT_LEFT   # معرّفة أصلاً في safety.py

MIN_ANGLE, MAX_ANGLE = 45, 135

SERVO_NAMES = {
    "R0":"RF Coxa","R1":"RF Femur","R2":"RF Tibia",
    "R3":"RM Coxa","R4":"RM Femur","R5":"RM Tibia",
    "R6":"RR Coxa","R7":"RR Femur","R8":"RR Tibia",
    "L0":"LF Coxa","L1":"LF Femur","L2":"LF Tibia",
    "L3":"LM Coxa","L4":"LM Femur","L5":"LM Tibia",
    "L6":"LR Coxa","L7":"LR Femur","L8":"LR Tibia",
}
LEG_GROUPS = {
    "RF":["R0","R1","R2"], "RM":["R3","R4","R5"], "RR":["R6","R7","R8"],
    "LF":["L0","L1","L2"], "LM":["L3","L4","L5"], "LR":["L6","L7","L8"],
}
_STAND = {}
for ch, a in DEFAULT_RIGHT.items(): _STAND[f"R{ch}"] = a
for ch, a in DEFAULT_LEFT.items():  _STAND[f"L{ch}"] = a
```

### `spider/moves.py` — مع إصلاح التحكيم (الثغرة #1)
الحركات الاستعراضية يجب أن **تحجز الملكية** فلا تتنازع مع المشي:
```python
# spider/moves.py
import threading, time, math, random
from spider.safety import arbiter
from spider.motion import smooth_move_leg     # بعد نقلها لـ motion.py
from spider.constants import _STAND

def run_special(move_name, fn, speed):
    """يشغّل حركة استعراضية بأمان: يحجز الملكية، وإلا يرفض لو مشغول."""
    if not arbiter.acquire("special"):
        return False                          # مشي/توازن شغّال — ارفض
    def _wrap():
        try:
            fn(speed)
        finally:
            # ارجع للوقوف ثم حرّر
            smooth_move_leg(list(_STAND.keys()), _STAND, steps=10, delay=0.03)
            arbiter.release("special")
    threading.Thread(target=_wrap, daemon=True).start()
    return True

# ... هنا تُنقل الـ 23 دالة _move_* كما هي بلا تغيير منطق ...
```
و route الحركات (في `spider/api/moves.py`) يصير:
```python
@bp.route("/api/special/<move_name>", methods=["POST"])
def special_move(move_name):
    fn = SPECIALS.get(move_name)
    if not fn:
        return jsonify({"ok": False, "error": f"unknown: {move_name}"}), 404
    speed = float((request.json or {}).get("speed", 1.0))
    if not run_special(move_name, fn, speed):
        return jsonify({"ok": False, "error": "busy — أوقف المشي أولاً"}), 409
    return jsonify({"ok": True, "move": move_name})
```
> نفس المعالجة لـ `body_move`: احجز `"body"`, نفّذ, حرّر.

### إصلاح `/api/off` (الثغرة #2) — في `spider/api/servos.py`
الخطأ: المفاتيح كلها 0–8 فتذهب كلها لـ R. الإصلاح: استخدم `_STAND` مباشرةً (مفاتيحه صحيحة R0..R8/L0..L8):
```python
@bp.route("/api/off", methods=["POST"])
def api_off():
    # 1) أوقف أي حركة
    gait_event.clear()
    arbiter.acquire("off")
    # 2) ارجع لوضعية الوقوف الصحيحة (سلس عبر المحكّم)
    from spider import goto
    goto("off", dict(_STAND), timeout=4.0)
    # 3) صفّر PWM (ترخية)
    from spider.hardware import pwm_release_all
    pwm_release_all()
    arbiter.release("off")
    return jsonify({"ok": True, "msg": "stance ثم إطفاء"})
```

---

## خطوات التطبيق (آمنة، تدريجية)

> القاعدة الذهبية: انقل وحدة واحدة، شغّل، تأكّد، ثم التالية. احتفظ بنسخة git قبل البدء.

1. **commit الحالة الحالية** (الخطة 01 المنفّذة) كنقطة رجوع.
2. أنشئ `spider/constants.py` وغيّر `web_controller.py` ليستورد منه (احذف التعريفات المكرّرة). شغّل، تأكّد.
3. انقل `smooth_move_leg`/`ease_*` لـ `spider/motion.py`. شغّل.
4. انقل `spider/imu.py`. شغّل.
5. انقل `spider/balance.py`. شغّل، اختبر التوازن.
6. انقل `spider/gaits.py` (المشي). اختبر كل أنماط المشي.
7. انقل `spider/moves.py` **مع إصلاح التحكيم**. اختبر حركة استعراضية أثناء وقوف وأثناء مشي (يجب أن تُرفض أثناء المشي).
8. أصلِح `/api/off`. اختبر الإطفاء (الطرفان معاً).
9. (الخطة 02) أضف مسارات الطوارئ.
10. قسّم الـ routes لـ blueprints. اختبر كل صفحة.
11. قلّص `web_controller.py`:
```python
from flask import Flask
from spider.api import register_blueprints
from spider.startup import boot          # IMU zero + arbiter + calibration
app = Flask(__name__)
register_blueprints(app)
if __name__ == "__main__":
    boot()
    app.run(host="0.0.0.0", port=5000)
```

---

## فهرس الحركات الحالية (مرجع — تبقى كما هي)

### أنماط المشي (`/api/gait/forward/start` type=…)
`forward, backward, turn_left, turn_right, shift_left, shift_right, strafe_left, strafe_right, crab_walk, climb, creep, prowl, high_step, glide`

### حركات الجسم (`/api/body/move` move=…)
`body_up, body_down, stand, lean_forward, lean_back, lean_left, lean_right, twist_left, twist_right`

### حركات استعراضية (`/api/special/<name>`)
`wave, dance, shake, salute, roar, spin, bow, stretch, idle_sway, wake_up, sleep_pose, flatten, push_up, tippy_toes, look_around, pounce, stomp, scared, dizzy, wiggle, peek, gallop, moonwalk`

> توثيق كامل لكلٍّ في `DEVELOPMENT_LOG.md`.

---

## معايير القبول
- ✅ `web_controller.py` < 60 سطراً، المنطق في وحدات `spider/`.
- ✅ **سلوك كل الحركات مطابق تماماً** لما قبل الترتيب (لا تغيير زوايا).
- ✅ الحركات الاستعراضية و`body_move` تحجز الملكية (لا تتنازع مع المشي).
- ✅ `/api/off` يطفئ الطرفين صحيحاً.
- ✅ كل ملف وحدة مسؤوليته واضحة ومستقلة.
- ✅ ملفات JSON في `config/`.
```
