# 🕷 تقرير تفصيلي — توازن الجايروسكوب + المشي السلس

**الحالة:** قيد التخطيط — يحتاج Implementation Plan  
**التاريخ:** 2026-05-20  
**المهام:**  
1. **Gimbal Balance** — توازن الجسم مع الجايروسكوب على أراضي مائلة  
2. **Smooth Walking** — مشي سلس بدون اهتزاز  

---

## 📁 موقع الملفات — ماذا يوجد حالياً

```
~/Spider/
├── web_controller.py              ← المتحكم الرئيسي (1293 سطر)
├── stance_tune_bno.py             ← ضبط يدوي للتوازن مع BNO085
├── ground_calibration_test.py     ← معايرة وضعية الوقوف
├── gait_params.json               ← بيانات Tripod Gait
├── ripple_gait_params.json        ← بيانات Ripple Gait (مشي سلس)
├── positions.json                 ← وضعيات محفوظة
├── leg_defaults.json              ← قيم افتراضية فارغة {}
├── leg_presets.json               ← بريسيت لكل رجل
├── shutdown_servos.sh             ← إيقاف آمن عند shutdown
├── templates/
│   ├── main.html                  ← الصفحة الرئيسية
│   ├── index.html                 ← واجهة المحركات (sliders)
│   ├── spider_gait_controller.html ← واجهة المشي + D-Pad
│   └── spider_sim.html            ← محاكاة 3D (WebGL)
└── Sonnet/
    ├── README.md                  ← ملخص Tripod Gait
    ├── ripple_gait.md             ← شرح Ripple Gait
    ├── spider_buttons_guide.md    ← دليل الأزرار (564 سطر)
    ├── gait_params_reference.json ← مرجع بيانات المشي
    └── gimbal_balance_smooth_walking.md  ← ★ هاد الملف
```

---

## 🔧 الهاردوير المتاح

| المكون | التفاصيل | الحالة في الكود |
|--------|---------|----------------|
| **Servo Right PCA** | عنوان `0x40` — 9 قنوات (R0-R8) | ✅ يعمل |
| **Servo Left PCA** | عنوان `0x44` — 9 قنوات (L0-L8) | ✅ يعمل |
| **BNO085 IMU** | `/dev/serial0` — 115200 baud | ✅ يعمل (RVC mode) |
| **نطاق السيرفو** | `45° — 135°` (90° مدى) | ✅ |
| **نطاق حقيقي آمن** | `~50° — ~130°` | ⚠️ يجب مراعاة |

### بنية كل رجل (3 مفاصل)
```
Coxa (ch+0) → دوران أفقي (أمام/خلف)  ← R0, R3, R6 / L0, L3, L6
Femur (ch+1) → رفع/خفض (عمودي)        ← R1, R4, R7 / L1, L4, L7
Tibia (ch+2) → ثابت عادةً              ← R2, R5, R8 / L2, L5, L8
```

### بنية الأرجل الستة
```
يمين:  RF = R0,R1,R2  |  RM = R3,R4,R5  |  RR = R6,R7,R8
يسار:  LF = L0,L1,L2  |  LM = L3,L4,L5  |  LR = L6,L7,L8

Tripod Groups:
  GROUP_A = RF, LM, LR (أرجل 1,3,5)
  GROUP_B = LF, RM, RR (أرجل 2,4,6)

Ripple Order:
  RR → RM → RF → LR → LM → LF (موجة من الخلف للأمام)
```

---

## 📡 الـ API المتاح حالياً

### IMU (BNO085)
```
GET  /api/imu/status     → {"ready": true/false}
GET  /api/imu/read       → {roll, pitch, yaw, raw_roll, raw_pitch, ax, ay, az}
POST /api/imu/zero       → تصفير المرجع (يقرأ 20 عينة ويوسطن)
GET  /api/imu/stream     → {samples: [...]} (N عينات متتالية)
```

### المشي (Gait)
```
POST /api/gait/forward/start  → {speed, cycles, type} ← Tripod
POST /api/gait/forward/stop
GET  /api/gait/forward/status

POST /api/gait/ripple/start   → {speed, cycles, direction} ← Ripple
POST /api/gait/ripple/stop
GET  /api/gait/ripple/status

POST /api/gait/once           → {type, speed, cycles} ← N دورات فقط
```

### حركة الجسم
```
POST /api/body/move  → {move, speed}
  الأوضاع: stand, body_up, body_down, lean_forward, lean_back,
           lean_left, lean_right, twist_left, twist_right
```

### حركات خاصة
```
POST /api/special/<name>  → {speed}
  الأسماء: wave, dance, shake, salute, roar, spin, bow,
           stretch, idle_sway, wake_up, sleep_pose
```

---

## 🧠 الكود الموجود — نقاط مهمة

### 1. قراءة BNO085 (في `web_controller.py`)
```python
def read_bno_single():
    yaw, pitch, roll, ax, ay, az = bno.heading
    return {
        "roll": round(roll - bno_zero_roll, 2),   # ← مصحح بالمرجع
        "pitch": round(pitch - bno_zero_pitch, 2), # ← مصحح بالمرجع
        "yaw": round(yaw, 2),
        "ax": round(ax, 3), "ay": round(ay, 3), "az": round(az, 3)
    }
```
- `bno_zero_roll/pitch` = المرجع (يتصفروا بـ `/api/imu/zero`)
- القيمة المُعادة = الانحراف عن المرجع

### 2. التحريك السلس (Smooth Move)
```python
def smooth_move_leg(keys, targets, steps=10, delay=0.03):
    """يحرّك مجموعة محركات سلس — بيحترم _force_stop"""
    starts = {k: current[k] for k in keys}
    for step in range(1, steps + 1):
        if _force_stop: return
        for k in keys:
            angle = starts[k] + (targets[k] - starts[k]) * step / steps
            set_servo(k, round(angle))
        time.sleep(delay)
```
- **استيفاء خطي فقط** (Linear Interpolation)
- ما في `easing` (سهولة) — حركة ممكن تكون خشنة

### 3. Tripod Gait (`_run_forward_gait`)
- 8 فريمات لكل دورة
- GROUP_A و GROUP_B متناوبتين (offset = نصف دورة)
- كل فريم = استيفاء خطي بخطوات `smooth_steps=6`
- **لا يقرأ IMU أثناء المشي** — حركة عمياء بدون feedback

### 4. Ripple Gait (`_run_ripple_gait`)
- 60 فريم لكل دورة — أنعم بكثير
- رفع = 14° فقط (بدل 28° في Tripod)
- رجل واحدة بالهوا بالتسلسل
- **أيضاً لا يقرأ IMU أثناء المشي**

### 5. قيم المعايرة الحالية (وضعية الوقوف)
```
RIGHT:  R0=90  R1=67  R2=91 | R3=92  R4=57  R5=92 | R6=93  R7=65  R8=94
LEFT:   L0=90  L1=54  L2=77 | L3=85  L4=74  L5=94 | L6=94  L7=64  L8=89
```

---

## 📋 المهمة 1: Gimbal Balance — توازن مع الجايروسكوب

### الفكرة
الروبوت يقرأ Roll و Pitch من BNO085 ويعدّل زوايا Femur تلقائياً عشان يبقى الجسم أفقي حتى لو الأرض مائلة.

### ما هو مطلوب تحقيقه
1. **حلقة توازن (Balance Loop)** — thread خلفي يقرأ IMU ويصحّح الزوايا باستمرار
2. **PID Controller** (أو PD على الأقل) — لحساب التعديل المطلوب بناءً على الانحراف
3. **توزيع التعديل على الأرجل** — Roll يعدّل يمين/يسار، Pitch يعدّل أمام/خلف
4. **دمج مع المشي** — التوازن يشتغل أثناء المشي (ما يتعارض)
5. **API endpoints جديدة** — تشغيل/إيقاف التوازن + ضبط معاملات PID

### المتغيرات المطلوبة
```
balance_enabled: bool
balance_running: bool
balance_thread: Thread
balance_roll_pid:  PID(kp=1.5, ki=0.1, kd=0.8)
balance_pitch_pid: PID(kp=1.5, ki=0.1, kd=0.8)
balance_max_correction: 15°  (حد أقصى للتعديل)
balance_frequency: 50Hz    (قراءة IMU + تصحيح)
```

### مبدأ العمل
```
كل 20ms (50Hz):
  1. اقرأ roll, pitch من BNO085
  2. احسب الخطأ: error_roll = 0 - roll, error_pitch = 0 - pitch
  3. PID → correction_roll, correction_pitch
  4. وزّع على الأرجل:
     - Roll > 0 (يمين مائل): يمين Femur ينزل (-corr), يسار Femur يرتفع (+corr)
     - Pitch > 0 (أمام مائل): أمام Femur ينزل (-corr), خلف Femur يرتفع (+corr)
  5. طبّق التعديل على current targets
```

### توزيع التعديل (الرياضيات)
```
لكل رجل i:
  femur_correction[i] = roll_correction × roll_weight[i] + pitch_correction × pitch_weight[i]

  roll_weight:
    RF=+0.5, RM=+0.5, RR=+0.5   (جانب أيمن)
    LF=-0.5, LM=-0.5, LR=-0.5   (جانب أيسر)
  
  pitch_weight:
    RF=-0.5, LF=-0.5             (أمامي)
    RM= 0.0, LM= 0.0             (وسطي — ما يتأثر)
    RR=+0.5, LR=+0.5             (خلفي)
```

### التحديات المتوقعة
- **تضارب مع المشي**: أثناء المشي الـ Femur بتتحرك — لازم التوازن يكون offset فوق حركة المشي
- **تأخر القراءة**: BNO085 بيتأخر ~5-10ms — لازم نراعي الـ latency
- **اهتزاز (Oscillation)**: إذا KP عالي → الرجل رح تهتز → لازم PID tuning
- **حدود السيرفو**: التصحيح ما لازم يخرج الزاوية عن 45-135

### API endpoints مطلوبة
```
POST /api/balance/start       → {kp, ki, kd, max_corr}
POST /api/balance/stop
GET  /api/balance/status      → {enabled, roll_corr, pitch_corr, roll, pitch}
POST /api/balance/tune        → {kp, ki, kd}  (ضبط أثناء العمل)
GET  /api/balance/debug       → {history: [{roll, pitch, corr_r, corr_p, t}]}
```

---

## 📋 المهمة 2: Smooth Walking — مشي سلس

### المشكلة الحالية
1. **استيفاء خطي** — الحركة بين نقطتين بخط مستقيم = حركة "ميكانيكية" خشنة
2. **Tripod بيرفع 3 أرجل** — 3 نقاط دعم = اهتزاز واضح
3. **Tibia ثابت دائماً** — ما بتتحرك = حركة غير طبيعية
4. **لا feedback من IMU** — المشي "أعمى" بدون تصحيح

### ما هو مطلوب تحقيقه
1. **Easing Functions** — بدل الاستيفاء الخطي، نستخدم smooth step أو Bezier
2. **دمج Ripple + Balance** — المشي السلس + التوازن معاً
3. **Tibia ديناميكي** — Tibia بتتحرك قليلاً لتعويض حركة Femur
4. **Foot Trajectory** — مسار القدم يكون قوس ناعم (شكل حرف D)

### الفرق بين الحالي والمطلوب
```
الحالي (Tripod):
  - استيفاء خطي → حركة خشنة
  - 3 أرجل بالهوا → اهتزاز
  - Tibia ثابت → غير طبيعي
  - بدون IMU → "أعمى"

المطلوب (Smooth):
  - Easing (Hermite/Cosine) → حركة ناعمة
  - Ripple (1 رجل بالهوا) → ثابت
  - Tibia ديناميكي → طبيعي
  - مع IMU feedback → ذكي
```

### Easing Functions المقترحة
```python
# Smoothstep (Hermite) — بداية ونهاية ناعمة
def smoothstep(t):
    return t * t * (3 - 2 * t)

# Cosine — أنعم من خطي
def cosine_ease(t):
    return (1 - math.cos(t * math.pi)) / 2

# Cubic Bezier — تحكم كامل بالمنحنى
def cubic_bezier(t, p0, p1, p2, p3):
    u = 1 - t
    return u**3*p0 + 3*u**2*t*p1 + 3*u*t**2*p2 + t**3*p3
```

### مسار القدم (Foot Trajectory)
```
الحالي: مربع — رفع ← تأرجح ← نزول ← دفع
المطلوب: قوس D — منحنى ناعم بدون زوايا حادة

        ╭───╮
       /     \        ← Femur يرسم قوس
      │       │
  ────╯       ╰────   ← Coxa يتحرك سلس
```

### Tibia ديناميكي
```python
# حالياً:
tibia = leg["tibia"]  # ثابت دائماً!

# المطلوب:
tibia = leg["tibia"] - lift * 0.4  # (موجود في Ripple بس!)
# التعديل: Tibia يعاكس Femur عشان القدم تبقى مستوية نسبياً
```

### التحديات المتوقعة
- **أداء Raspberry Pi**: 18 محرك × PID × Easing = حمل عالي
- **تجاوب السيرفو**: السيرفو económico تأخره ~20ms → لازم تبطئ التحديث
- **ضبط المعاملات**: Easing intensity, Tibia ratio, PID gains

---

## 🔗 الترابط بين المهمتين

```
                    ┌─────────────┐
                    │  BNO085 IMU │
                    │ Roll/Pitch  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ PID Balance │ ← المهمة 1
                    │  Controller │
                    └──────┬──────┘
                           │ femur_correction
                    ┌──────▼──────┐
                    │  Gait Engine│ ← المهمة 2
                    │  (Ripple +  │
                    │   Easing)   │
                    └──────┬──────┘
                           │ final_angles = gait_angles + balance_correction
                    ┌──────▼──────┐
                    │ 18 Servos   │
                    └─────────────┘
```

### الأولوية بالتنفيذ
1. **أولاً**: Easing في `smooth_move_leg` (أسهل تعديل — أكبر فرق)
2. **ثانياً**: Balance Loop مستقل (بدون مشي — واقف يوازن)
3. **ثالثاً**: دمج Balance + Ripple Gait معاً
4. **رابعاً**: Tibia ديناميكي + Foot Trajectory محسّن

---

## 📊 ملخص ما يحتاجه كل ملف

### `web_controller.py` — التعديلات المتوقعة
| القسم | المطلوب | السطر الحالي |
|-------|---------|-------------|
| `read_bno_single()` | إضافة moving average لتصفية الضوضاء | 37-53 |
| `smooth_move_leg()` | إضافة easing function (اختياري) | 168-179 |
| جديد: `BalanceController` class | PID + thread + توزيع تعديل | جديد |
| `_run_forward_gait()` | دمج balance correction | 345-402 |
| `_run_ripple_gait()` | دمج balance correction + Tibia ديناميكي | 811-909 |
| جديد: Balance API routes | 4-5 endpoints جديدة | جديد |

### ملفات JSON جديدة متوقعة
- `balance_config.json` — معاملات PID + حدود + تردد

### ملفات Sonnet جديدة متوقعة
- `balance_algorithm.md` — شرح مفصل للخوارزمية
- `smooth_walking_algorithm.md` — شرح Easing + Foot Trajectory

---

## ✅ نقاط القوة في الكود الحالي
- **BNO085 جاهز ويعمل** — لا حاجة لتعديل قراءة الحساس
- **Ripple Gait جاهز** — أساس ممتاز للمشي السلس
- **`bno_lock`** موجود — حماية thread-safe لقراءة IMU
- **`_force_stop`** موجود — آلية إيقاف طارئ
- **`smooth_move_leg`** مركزي — مكان واحد لتعديل طريقة التحريك
- **Tibia ديناميكي في Ripple** — `tibia = leg["tibia"] - lift * 0.4` موجود!

## ⚠️ نقاط الضعف
- **لا PID** — لا يوجد أي controller للتوازن
- **استيفاء خطي فقط** — حركة خشنة
- **Tibia ثابت في Tripod** — ما يتحرك أبداً
- **لا feedback loop** — المشي لا يستجيب للـ IMU
- **Tripod group خاطئ** — `GROUP_A = ["RF","LM","RR"]` لكن الجدول يقول `["RF","LM","LR"]` ⚠️

---

## 📌 ملاحظات للمخطط (Implementation Plan)

1. **لا تعدّل ملفات خارج `~/Spider/`**
2. **اختبر كل مرحلة لحالها** قبل الدمج
3. **PID tuning لازم يكون عملي** — ابدأ بقيم صغيرة وزيد تدريجياً
4. **`bno_lock` ضروري** — أي قراءة IMU لازم تكون داخل القفل
5. **حد التصحيح (max_correction)** — أهم معامل أمان (لا يزيد عن 15°)
6. **التردد** — 50Hz مبدئياً، ممكن ينزل لـ 30Hz إذا Pi ما لحق
