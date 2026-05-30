# خطة 03 — محرك المشي الموحّد فوق الـ IK

> الأولوية: 🔴 حرجة. يعتمد على الخطة 01 (MotionArbiter) والخطة 02 (IK).
> تحلّ: أخطاء التدقيق #3 (مجموعات Tripod خاطئة)، #6، #7، #8 + يوحّد 5 محركات مشي في واحد.

---

## ملخص تنفيذي

نستبدل `execute_gait` و`_run_forward_gait` و`_run_ripple_gait` و`_run_smooth_ripple` و`_run_lateral_gait` (خمس نسخ متشابهة فيها أخطاء) بمحرك واحد. المدخل **متجه سرعة الجسم** `(vx, vy, ω)`:
- `vx` = أمام/خلف، `vy` = يسار/يمين (زحف حقيقي)، `ω` = دوران مكاني.
- أي تركيبة منها = حركة قطرية/لولبية بسلاسة. لا مزيد من «أنواع» منفصلة لكل اتجاه.
- نمط الأرجل (tripod / ripple / wave) = بارامتر واحد (duty factor + ترتيب الأطوار).

كل إطار: المحرك يحسب موقع كل قدم (مسار swing/stance حسب سرعة الجسم) → IK (الخطة 02) → `arbiter.set_target` (الخطة 01).

---

## لماذا

- **يصلح خطأ الـ Tripod:** المجموعات تأتي من تعريف واحد صحيح، لا من ثابت مكرر مغلوط (#3).
- **يوحّد الحالة:** متغيّر حالة واحد بدل خلط `gait_running`/`gait_event`/`gait_phase`/`gait_current_phase` (#6،#7،#8).
- **الزحف الجانبي والدوران مجاناً:** `vy` و`ω` يدخلان نفس المعادلة — لا حاجة لـ `_run_lateral_gait` المنفصل.
- **أنماط المشي = بارامترات:** prowl/high_step/glide/creep تصير قيم (ارتفاع الجسم، ارتفاع الرفع، طول الخطوة، التردد) لا فروع كود.

---

## مفاهيم المشي

### متجه السرعة
```
gait_cmd = {vx, vy, omega}   # vx,vy بالمم/دورة, omega بالراديان/دورة (تقريبياً)
```

### الطور لكل رجل (phase)
لكل رجل طور `φ ∈ [0,1)`. تُوزَّع الأطوار حسب النمط:
- **Tripod:** مجموعتان بفارق 0.5. duty=0.5 (نصف الوقت بالهواء). أسرع.
- **Ripple:** 6 أطوار متتالية بفارق 1/6. duty=5/6. أنعم/أبطأ، رجل واحدة بالهواء.
- **Wave:** مثل ripple لكن duty≈5/6 بترتيب موجة. الأكثر ثباتاً.

### مسار القدم لكل إطار
- **Stance (على الأرض):** القدم تتحرك عكس اتجاه السرعة (تدفع الجسم). إزاحتها الأفقية مشتقّة من `(vx,vy,ω)` وموضع الرجل.
- **Swing (بالهواء):** ترتفع بقوس وتعود لبداية الـ stance التالية.

دوران `ω`: كل قدم تضيف مكوّن سرعة عمودي على متجه (المركز→الرجل): `v_rot = ω × r`.

---

## الكود الكامل — `spider/gait.py`

```python
# spider/gait.py
"""محرك مشي موحّد: سرعة جسم (vx,vy,ω) → مسارات أقدام → IK → MotionArbiter."""
import json, os, math, threading, time
from spider import kinematics as kin
from spider.safety import arbiter, current
from spider import motion

STANCE = json.load(open(os.path.join("config","stance.json")))["default"]
GAITS  = json.load(open(os.path.join("config","gaits.json")))

class GaitEngine:
    def __init__(self):
        self._thread = None
        self._run = threading.Event()
        self.cmd = {"vx":0.0,"vy":0.0,"omega":0.0}
        self.gait_name = "tripod"
        self.phase = 0.0
        self.state = "idle"        # idle | walking | returning

    # ── واجهة عامة ──
    def set_cmd(self, vx=0.0, vy=0.0, omega=0.0):
        self.cmd = {"vx":float(vx),"vy":float(vy),"omega":float(omega)}

    def start(self, gait_name="tripod", vx=0.0, vy=0.0, omega=0.0, body=None):
        if self._run.is_set():
            self.set_cmd(vx,vy,omega); self.gait_name=gait_name; return False
        if not arbiter.acquire("gait"):
            return False
        self.gait_name = gait_name
        self.body = body or {}
        self.set_cmd(vx,vy,omega)
        self._run.set()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._run.clear()

    def status(self):
        return {"state":self.state,"gait":self.gait_name,"phase":round(self.phase,3),
                "cmd":self.cmd,"owner":arbiter.owner()}

    # ── الحلقة ──
    def _loop(self):
        self.state = "walking"
        g = GAITS[self.gait_name]
        fps          = g["fps"]
        duty         = g["duty"]          # كسر الوقت على الأرض
        lift_h       = g["lift_height"]   # مم
        cycle_frames = g["cycle_frames"]
        phase_off    = g["phase_offsets"] # {leg: 0..1}
        body_z       = g.get("body_z", 0) # خفض/رفع الجسم (prowl/tippy)
        period = 1.0 / fps
        f = 0
        while self._run.is_set() and not arbiter.in_estop():
            self.phase = (f % cycle_frames) / cycle_frames
            targets = self._frame_targets(g, self.phase, duty, lift_h, phase_off, body_z)
            arbiter.set_target("gait", targets)
            f += 1
            time.sleep(period)
        # رجوع نظيف لوضعية الوقوف
        self.state = "returning"
        motion.goto("gait", kin.stand_servos(STANCE, {"tz":0}), timeout=3.0)
        arbiter.release("gait")
        self.state = "idle"

    def _frame_targets(self, g, global_phase, duty, lift_h, phase_off, body_z):
        vx,vy,om = self.cmd["vx"], self.cmd["vy"], self.cmd["omega"]
        targets = {}
        for leg, base in STANCE.items():
            bx,by,bz = base
            ph = (global_phase + phase_off[leg]) % 1.0
            # سرعة هذه القدم على الأرض = - (سرعة الجسم + دوران)
            # مكوّن الدوران: ω × r حيث r=(bx,by)
            rot_x = -om * by
            rot_y =  om * bx
            step_x = -(vx + rot_x)
            step_y = -(vy + rot_y)
            if ph >= (1.0 - duty):
                # STANCE: القدم تتحرك خلفاً (تدفع)، z على الأرض
                t = (ph - (1.0 - duty)) / duty          # 0..1
                ox = step_x * (0.5 - t)
                oy = step_y * (0.5 - t)
                oz = 0.0
            else:
                # SWING: القدم تعود للأمام وترتفع بقوس
                t = ph / (1.0 - duty)                    # 0..1
                ox = step_x * (-0.5 + t)
                oy = step_y * (-0.5 + t)
                oz = lift_h * math.sin(math.pi * t)
            fx, fy, fz = bx + ox, by + oy, bz + oz
            # تصحيح التوازن (الخطة: balance يكتب body tilt، هنا نطبّق z-offset للأرجل على الأرض)
            if oz < 0.5:
                fz += balance_z_offset(leg)
            targets.update(kin.foot_to_servos(leg, fx, fy, fz + body_z))
        return targets


def balance_z_offset(leg):
    """يُربط لاحقاً بـ BalanceController (الخطة: تصحيح ارتفاع القدم بدل Femur خام)."""
    try:
        from spider.balance import balance
        return balance.leg_z_offsets.get(leg, 0.0) if balance.enabled else 0.0
    except Exception:
        return 0.0


gait = GaitEngine()
```

## ملف الأنماط — `config/gaits.json`

```json
{
  "tripod": {
    "fps": 50, "duty": 0.5, "lift_height": 35, "cycle_frames": 40, "body_z": 0,
    "phase_offsets": {"RF":0.0,"LM":0.0,"RR":0.0,"LF":0.5,"RM":0.5,"LR":0.5}
  },
  "ripple": {
    "fps": 50, "duty": 0.833, "lift_height": 30, "cycle_frames": 60, "body_z": 0,
    "phase_offsets": {"RR":0.0,"RM":0.1667,"RF":0.3333,"LR":0.5,"LM":0.6667,"LF":0.8333}
  },
  "wave": {
    "fps": 50, "duty": 0.833, "lift_height": 28, "cycle_frames": 72, "body_z": 0,
    "phase_offsets": {"RF":0.0,"RM":0.1667,"RR":0.3333,"LR":0.5,"LM":0.6667,"LF":0.8333}
  },
  "prowl":   {"fps":50,"duty":0.75,"lift_height":18,"cycle_frames":60,"body_z":-25,
              "phase_offsets":{"RF":0.0,"LM":0.0,"RR":0.0,"LF":0.5,"RM":0.5,"LR":0.5}},
  "high_step":{"fps":50,"duty":0.5,"lift_height":55,"cycle_frames":48,"body_z":0,
              "phase_offsets":{"RF":0.0,"LM":0.0,"RR":0.0,"LF":0.5,"RM":0.5,"LR":0.5}},
  "glide":   {"fps":60,"duty":0.5,"lift_height":12,"cycle_frames":36,"body_z":0,
              "phase_offsets":{"RF":0.0,"LM":0.0,"RR":0.0,"LF":0.5,"RM":0.5,"LR":0.5}}
}
```

> لاحظ: tripod هنا **صحيح** — `{RF,LM,RR}` معاً مقابل `{LF,RM,LR}` (يطابق `gait_params.json`، ويصلح خطأ `execute_gait`).

---

## ربط الـ API (طبقة توافق)

نُبقي مسارات الواجهة القديمة لكن نوجّهها للمحرك الجديد، فلا تنكسر الواجهة:

```python
# تحويل "نوع" قديم → (gait, vx, vy, omega)
SPEED_MM = 70   # مم/دورة عند speed=1.0 — قابل للضبط (الخطة 04)
def cmd_from_type(gait_type, speed):
    s = SPEED_MM * speed
    table = {
        "forward":   ("tripod",  s, 0, 0),   "backward": ("tripod", -s, 0, 0),
        "turn_left": ("tripod", 0, 0,  0.6*speed), "turn_right":("tripod",0,0,-0.6*speed),
        "shift_left":("tripod", 0,  s, 0),   "shift_right":("tripod",0,-s,0),
        "strafe_left":("ripple",0,  s, 0),   "strafe_right":("ripple",0,-s,0),
        "crab_walk": ("tripod", 0.5*s, s, 0),
        "creep":     ("wave",   0.4*s,0,0),  "prowl":("prowl", 0.6*s,0,0),
        "high_step": ("high_step", s,0,0),   "glide":("glide", 1.2*s,0,0),
        "climb":     ("high_step", 0.7*s,0,0),
    }
    return table.get(gait_type, ("tripod", s, 0, 0))

@app.route("/api/gait/forward/start", methods=["POST"])
def gait_forward_start():
    d = request.json or {}
    name, vx, vy, om = cmd_from_type(d.get("type","forward"), float(d.get("speed",1.0)))
    ok = gait.start(name, vx, vy, om)
    return jsonify({"ok": ok, "status": gait.status()})

@app.route("/api/gait/forward/stop", methods=["POST"])
def gait_forward_stop():
    gait.stop(); return jsonify({"ok": True})

@app.route("/api/gait/forward/status", methods=["GET"])
def gait_forward_status():
    return jsonify(gait.status())
```

> الواجهة الجديدة (الخطة 05) ستستخدم مسار أنظف `/api/move {vx,vy,omega,gait}` مباشرة بدل جدول الأنواع — لكن جدول التوافق يبقي الواجهة الحالية شغّالة أثناء الهجرة.

---

## دمج التوازن (BalanceController)

بدل أن يكتب `balance` المحركات مباشرة (مصدر تضارب)، يصبح دوره **حساب تصحيح** فقط:
- في الوقوف: يطلب `gait`/`arbiter` تطبيق `body = {roll: -k*roll_err, pitch: -k*pitch_err}` عبر `kin.stand_servos`.
- في المشي: يضع `balance.leg_z_offsets[leg]` ويقرأها محرك المشي (دالة `balance_z_offset` أعلاه).
- لا يملك الحركة إلا حين `arbiter.owner() is None`.

(تفاصيل إعادة هيكلة `balance` في ملف لاحق ضمن نفس النمط — المبدأ: balance = مُصحِّح، MotionArbiter = الكاتب الوحيد.)

---

## خطوات التطبيق

1. أكمل الخطتين 01 و02.
2. أضف `spider/gait.py` و`config/gaits.json`.
3. اختبر `tripod` للأمام فقط (`vx>0`) بسرعة منخفضة.
4. جرّب `vy` (زحف) و`omega` (دوران) — تأكد أنها طبيعية.
5. وجّه مسارات الـ API القديمة للمحرك الجديد (جدول التوافق).
6. احذف الدوال الخمس القديمة بعد التأكد.
7. اربط `balance` كمُصحِّح.

---

## الاختبار

| اختبار | المتوقّع |
|--------|----------|
| `vx>0` tripod | مشي أمامي مستقر، مثلث ثبات صحيح (RF,LM,RR) |
| `vy>0` فقط | زحف جانبي حقيقي (بفضل IK، لا خدعة Tibia) |
| `omega>0` فقط | دوران مكاني نظيف |
| `vx>0, omega>0` | قوس — يمشي ويلتف معاً بسلاسة |
| تبديل tripod↔ripple أثناء المشي | انتقال سلس |
| طوارئ أثناء المشي | يتوقف فوراً (MotionArbiter) |

---

## معايير القبول

- ✅ محرك مشي واحد يغطي كل الاتجاهات والأنماط.
- ✅ Tripod صحيح هندسياً ({RF,LM,RR} / {LF,RM,LR}).
- ✅ الزحف الجانبي والدوران من نفس المعادلة (vx,vy,ω).
- ✅ متغيّر حالة واحد (`gait.status()`), لا خلط أعلام.
- ✅ لا تكتب أي دالة مشي المحركات مباشرة — فقط `arbiter.set_target`.
```
