# 🕷 Ripple Gait — مشي العنكبوت المتناغم

## الحالة: جاهز للتنفيذ
## التاريخ: 2026-05-15

---

## الفكرة الأساسية

بدل ما نرفع 3 أرجل دفعة (Tripod)، نرفع **رجل واحدة فقط** بالهوا بتسلسل متدرج — متل موجة بتمشي على طول الجسم من الخلف للأمام.

```
الوقت →     T0    T1    T2    T3    T4    T5
─────────────────────────────────────────────────
RF          ↑     ●     ●     ●     ●     ●
RM          ●     ↑     ●     ●     ●     ●
RR          ●     ●     ↑     ●     ●     ●
LF          ●     ●     ●     ↑     ●     ●
LM          ●     ●     ●     ●     ↑     ●
LR          ●     ●     ●     ●     ●     ↑

↑ = بالهوا    ● = على الأرض (تدفع)
```

**في كل لحظة: 5 أرجل على الأرض تدفع، رجل واحدة تطير للأمام.**

---

## لماذا بيبدو "بدون رفع"؟

Duty Factor عالي — كل رجل تكون على الأرض **83%** من الوقت وبالهوا **17%** فقط.

- الجسم ما يهتز لأن دايماً في 5 نقاط تدعمه
- الرفع قصير جداً (~14° بس بدل 28° في Tripod)
- الموجة المتدرجة تخلق حركة مستمرة بدون "وقفات"

---

## مقارنة مع Tripod الحالي

```
Tripod الحالي:          Ripple الجديد:
───────────────         ──────────────────
مجموعتان فقط (A/B)      6 phases منفصلة
رفع = 28°               رفع = 14° (نص!)
3 أرجل بالهوا دفعة     1 رجل بالهوا فقط
يهتز الجسم قليلاً      الجسم ثابت تقريباً
سريع                    أبطأ بس أثبت
```

---

## المعادلات

لكل رجل رقمها `i` (0 إلى 5):

```
phase_offset[i] = i × (2π / 6)   ← كل رجل بتتأخر بـ 60°
phase(t, i) = gaitTime + phase_offset[i]

lift  = max(0, sin(phase)) × LIFT_DEG     (LIFT_DEG = 14° فقط!)
swing = cos(phase) × SWING_DEG            (SWING_DEG = 20°)
```

الفرق الجوهري عن Tripod:
- `LIFT_DEG` أصغر بكثير (14° بدل 28°)
- كل رجل لها phase مختلف بدل مجموعتين فقط
- السر كله برقم واحد: **LIFT_DEG = 14** بدل 28

---

## الترتيب (Ripple Order)

```
الترتيب: RR → RM → RF → LR → LM → LF
```

موجة من الخلف للأمام على الجانب الأيمن، ثم من الخلف للأمام على الجانب الأيسر.

---

## التنفيذ في الكود

### ملف جديد: ripple_gait_params.json

```json
{
  "gait": "ripple",
  "total_frames": 60,
  "frame_delay_ms": 20,
  "coxa_amplitude": 20,
  "LIFT_DEG": 14,
  "SWING_DEG": 20,
  "RIPPLE_ORDER": ["RR", "RM", "RF", "LR", "LM", "LF"],
  "legs": {
    "RF": {"coxa_stand": 90, "femur_stand": 67, "tibia": 91,  "side": "R"},
    "RM": {"coxa_stand": 92, "femur_stand": 57, "tibia": 92,  "side": "R"},
    "RR": {"coxa_stand": 93, "femur_stand": 65, "tibia": 94,  "side": "R"},
    "LF": {"coxa_stand": 90, "femur_stand": 54, "tibia": 77,  "side": "L"},
    "LM": {"coxa_stand": 85, "femur_stand": 74, "tibia": 94,  "side": "L"},
    "LR": {"coxa_stand": 94, "femur_stand": 64, "tibia": 89,  "side": "L"}
  }
}
```

### الدوال الجديدة في web_controller.py

1. `_run_ripple_gait(params, speed, max_cycles, direction)` — حلقة المشي Ripple
2. `GET /api/gait/ripple/start` — تشغيل
3. `POST /api/gait/ripple/stop` — إيقاف

### المنطق:

```python
RIPPLE_ORDER = ['RR', 'RM', 'RF', 'LR', 'LM', 'LF']
LIFT_DEG = 14
SWING_DEG = 20

def _run_ripple_gait(params, speed=1.0, max_cycles=0, direction='forward'):
    delay = 0.02 / speed  # 20ms per frame
    legs = RIPPLE_ORDER
    frame = 0
    
    while gait_running:
        t = (frame / 60) * 2 * math.pi
        
        for idx, leg_name in enumerate(legs):
            leg = params["legs"][leg_name]
            ph = t + idx * (2 * math.pi / 6)
            
            side = leg["side"]
            sign = 1 if side == "R" else -1
            if direction == 'backward':
                sign = -sign
            
            lift  = max(0, math.sin(ph)) * LIFT_DEG
            swing = math.cos(ph) * SWING_DEG * sign
            
            coxa  = leg["coxa_stand"]  + swing
            femur = leg["femur_stand"] + lift
            tibia = leg["tibia"]       - lift * 0.4
            
            # بناء المفاتيح وحركة المحركات
            
        time.sleep(delay)
        frame += 1
```

---

## API Endpoints

```
POST /api/gait/ripple/start   → {"speed": 1.0, "direction": "forward"}
POST /api/gait/ripple/stop    → يوقف نظيف
GET  /api/gait/ripple/status  → {"running": true, "frame": 42}
```

---

## ملاحظات

- الرفع خفيف (14°) = ما بيعلق محركات = آمن
- دايماً 5 أرجل على الأرض = ثبات عالي
- مناسب للمشي البطيء المتناغم متل العنكبوت الحقيقي
- ممكن نعمل نسخة backward بنفس الكود بتغيير direction
