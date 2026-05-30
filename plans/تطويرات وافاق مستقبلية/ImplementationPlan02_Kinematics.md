# خطة 02 — محرك الكينيماتيك العكسي (Inverse Kinematics Engine)

> الأولوية: 🔴 حرجة — أساس كل تطوير حركي لاحق.
> تحلّ: ديون التصميم #9، #10 + تجعل الزحف الجانبي/الميل/تكيّف التضاريس طبيعية وعامة.

---

## ملخص تنفيذي

حالياً كل حركة = زوايا سيرفو مكتوبة يدوياً، والـ Tibia مجمّد. هذا يجعل أي حركة جديدة تجربة-وخطأ، ويجعل الإزاحة الجانبية «خدعة» غير قابلة للتعميم. الحل: **طبقة كينيماتيك** تحوّل (موقع القدم المطلوب x,y,z في فضاء الجسم) → (زوايا Coxa/Femur/Tibia). فوقها نبني تحويل وضعية الجسم (6-DOF) ومنه تخرج كل الحركات: مشي لأي اتجاه، دوران، ميل، رفع/خفض، تكيّف ميلان — من معادلة واحدة.

المبدأ الذهبي: **لا أحد يكتب زوايا سيرفو مباشرة بعد اليوم. الجميع يطلب «مواقع أقدام»، والـ IK يحوّلها.**

---

## لماذا

- **التعميم:** بدل 23 دالة حركة مكتوبة يدوياً، لدينا مولّد مسار قدم واحد + IK. أي حركة = منحنى موقع القدم عبر الزمن.
- **الزحف الجانبي الحقيقي:** يصبح مجرد «سرعة جسم جانبية» (vy) — لا حاجة لخدعة Tibia.
- **استقرار وميل:** ميل الجسم = تحويل صلب للجسم مع إبقاء الأقدام ثابتة أرضياً → IK. التوازن بالـ IMU يصير تعديلاً على ارتفاع الجسم/ميله لا على Femur فقط.
- **تكيّف التضاريس:** نغيّر z المستهدف لكل قدم على حدة.
- **يزيل تكرار حساب القنوات:** خريطة واحدة Leg→قنوات.

---

## النموذج الهندسي للروبوت

### إطارات الإحداثيات
- **إطار الجسم (Body frame):** المركز وسط الجسم. x = للأمام، y = لليسار، z = للأعلى.
- لكل رجل **موضع تركيب** `(mount_x, mount_y)` و**زاوية تركيب** `mount_angle` (اتجاه الـ Coxa للخارج).
- **إطار الرجل:** أصله مفصل الـ Coxa، x للخارج على طول الرجل.

### أطوال الوصلات (يجب قياسها)
```
COXA_LEN   = مسافة محور Coxa → محور Femur   (أفقي تقريباً)
FEMUR_LEN  = محور Femur → محور Tibia
TIBIA_LEN  = محور Tibia → طرف القدم
```
⚠️ قِس هذه بالمليمتر من روبوتك الفعلي.

### معادلات IK لرجل واحدة (3-DOF)
المدخل: موقع القدم `(x, y, z)` في **إطار الرجل** (أصله Coxa).
```
coxa  = atan2(y, x)                                  # دوران أفقي
L     = hypot(x, y) - COXA_LEN                       # امتداد أفقي بعد الـ Coxa
D     = hypot(L, z)                                  # مسافة Femur→القدم
# قانون جيب التمام للمثلث (Femur, Tibia, D)
a_fem = atan2(-z, L) + acos((FEMUR_LEN**2 + D**2 - TIBIA_LEN**2)/(2*FEMUR_LEN*D))
a_tib = acos((FEMUR_LEN**2 + TIBIA_LEN**2 - D**2)/(2*FEMUR_LEN*TIBIA_LEN))
femur = a_fem
tibia = a_tib
```
(`z` سالب للأسفل.) لو `D > FEMUR_LEN+TIBIA_LEN` فالنقطة خارج المدى → نقصّها.

### تحويل زوايا المفاصل → زوايا السيرفو
المفاصل الهندسية (راديان، صفرها وضع مرجعي) تُحوّل لزوايا السيرفو (45–135) عبر معايرة لكل محرك:
```
servo = servo_center + sign * degrees(joint_angle - joint_ref)
```
حيث `servo_center` و`sign` و`joint_ref` تأتي من **معايرة** (الخطة 04). لتسهيل الهجرة: نعاير بحيث تُنتج وضعية الوقوف الحالية نفس زوايا `gait_params.json` المعروفة (نقطة ربط واحدة معروفة).

---

## الكود الكامل — `spider/kinematics.py`

```python
# spider/kinematics.py
"""محرك كينيماتيك هكسابود: IK لكل رجل + تحويل وضعية الجسم."""
import json, os, math

CFG = json.load(open(os.path.join("config", "robot_geometry.json")))

COXA_LEN  = CFG["coxa_len"]
FEMUR_LEN = CFG["femur_len"]
TIBIA_LEN = CFG["tibia_len"]

# ترتيب الأرجل وقنواتها وموضع/زاوية التركيب
# leg: {channels:[c,f,t], mount:[x,y], mount_angle_deg, side, servo_cal:{...}}
LEGS = CFG["legs"]
LEG_ORDER = CFG["leg_order"]   # ["RF","RM","RR","LF","LM","LR"]


def leg_ik(x, y, z):
    """موقع القدم في إطار الرجل → (coxa, femur, tibia) راديان. None لو خارج المدى."""
    coxa = math.atan2(y, x)
    L = math.hypot(x, y) - COXA_LEN
    D = math.hypot(L, z)
    reach = FEMUR_LEN + TIBIA_LEN
    if D > reach * 0.999:          # خارج المدى — قصّ
        D = reach * 0.999
    if D < abs(FEMUR_LEN - TIBIA_LEN) * 1.001:
        D = abs(FEMUR_LEN - TIBIA_LEN) * 1.001
    cos_fem = (FEMUR_LEN**2 + D**2 - TIBIA_LEN**2) / (2*FEMUR_LEN*D)
    cos_tib = (FEMUR_LEN**2 + TIBIA_LEN**2 - D**2) / (2*FEMUR_LEN*TIBIA_LEN)
    cos_fem = max(-1, min(1, cos_fem)); cos_tib = max(-1, min(1, cos_tib))
    femur = math.atan2(-z, L) + math.acos(cos_fem)
    tibia = math.acos(cos_tib)
    return coxa, femur, tibia


def joint_to_servo(leg_name, joint, value_rad):
    """يحوّل زاوية مفصل (راديان) لزاوية سيرفو (درجة) عبر معايرة المحرك."""
    cal = LEGS[leg_name]["servo_cal"][joint]   # {center, sign, ref_rad}
    deg = math.degrees(value_rad - cal["ref_rad"])
    return cal["center"] + cal["sign"] * deg


def foot_world_to_leg(leg_name, fx, fy, fz):
    """يحوّل موقع قدم في إطار الجسم → إطار الرجل (إزاحة التركيب + دوران زاوية التركيب)."""
    leg = LEGS[leg_name]
    mx, my = leg["mount"]
    ang = math.radians(leg["mount_angle_deg"])
    dx, dy = fx - mx, fy - my
    # دوّر بعكس زاوية التركيب ليصير x على طول الرجل
    lx =  dx*math.cos(-ang) - dy*math.sin(-ang)
    ly =  dx*math.sin(-ang) + dy*math.cos(-ang)
    return lx, ly, fz


def foot_to_servos(leg_name, fx, fy, fz):
    """موقع قدم في إطار الجسم → dict زوايا سيرفو {c_key:.., f_key:.., t_key:..}."""
    lx, ly, lz = foot_world_to_leg(leg_name, fx, fy, fz)
    coxa, femur, tibia = leg_ik(lx, ly, lz)
    ch = LEGS[leg_name]["channels"]
    side = LEGS[leg_name]["side"]
    return {
        f"{side}{ch[0]}": joint_to_servo(leg_name, "coxa",  coxa),
        f"{side}{ch[1]}": joint_to_servo(leg_name, "femur", femur),
        f"{side}{ch[2]}": joint_to_servo(leg_name, "tibia", tibia),
    }


# ── تحويل وضعية الجسم (6-DOF) ──
def body_transform(foot_default, body):
    """يطبّق إزاحة/دوران الجسم على مواقع الأقدام الافتراضية.
    body = {tx,ty,tz, roll,pitch,yaw}  (متر/متر/متر, راديان)
    المبدأ: لتحريك/إمالة الجسم مع بقاء الأقدام على الأرض، نطبّق التحويل العكسي
    على موقع كل قدم في إطار الجسم."""
    tx, ty, tz = body.get("tx",0), body.get("ty",0), body.get("tz",0)
    r, p, yaw  = body.get("roll",0), body.get("pitch",0), body.get("yaw",0)
    cr,sr=math.cos(r),math.sin(r); cp,sp=math.cos(p),math.sin(p); cy,sy=math.cos(yaw),math.sin(yaw)
    out = {}
    for name,(fx,fy,fz) in foot_default.items():
        # دوران عكسي ثم إزاحة عكسية
        x,y,z = fx,fy,fz
        # yaw (حول z)
        x,y = cy*x+sy*y, -sy*x+cy*y
        # pitch (حول y)
        x,z = cp*x - sp*z, sp*x + cp*z
        # roll (حول x)
        y,z = cr*y + sr*z, -sr*y + cr*z
        out[name] = (x - tx, y - ty, z - tz)
    return out


def stand_servos(foot_default, body=None):
    """يحسب زوايا كل المحركات لوضعية وقوف (مع وضعية جسم اختيارية)."""
    feet = body_transform(foot_default, body) if body else foot_default
    res = {}
    for name,(fx,fy,fz) in feet.items():
        res.update(foot_to_servos(name, fx, fy, fz))
    return res
```

## ملف الهندسة — `config/robot_geometry.json`

```json
{
  "_comment": "⚠️ قِس الأطوال بالمليمتر من روبوتك. mount بإطار الجسم (مم). mount_angle_deg اتجاه الرجل للخارج.",
  "coxa_len": 45,
  "femur_len": 75,
  "tibia_len": 110,
  "leg_order": ["RF","RM","RR","LF","LM","LR"],
  "legs": {
    "RF": {"channels":[0,1,2],"side":"R","mount":[ 60,-40],"mount_angle_deg":-45,
           "servo_cal":{"coxa":{"center":90,"sign":1,"ref_rad":0.0},
                        "femur":{"center":67,"sign":-1,"ref_rad":0.0},
                        "tibia":{"center":91,"sign":-1,"ref_rad":1.57}}},
    "RM": {"channels":[3,4,5],"side":"R","mount":[  0,-55],"mount_angle_deg":  0,
           "servo_cal":{"coxa":{"center":92,"sign":1,"ref_rad":0.0},
                        "femur":{"center":57,"sign":-1,"ref_rad":0.0},
                        "tibia":{"center":92,"sign":-1,"ref_rad":1.57}}},
    "RR": {"channels":[6,7,8],"side":"R","mount":[-60,-40],"mount_angle_deg": 45,
           "servo_cal":{"coxa":{"center":93,"sign":1,"ref_rad":0.0},
                        "femur":{"center":65,"sign":-1,"ref_rad":0.0},
                        "tibia":{"center":94,"sign":-1,"ref_rad":1.57}}},
    "LF": {"channels":[0,1,2],"side":"L","mount":[ 60, 40],"mount_angle_deg":-135,
           "servo_cal":{"coxa":{"center":90,"sign":-1,"ref_rad":0.0},
                        "femur":{"center":54,"sign":1,"ref_rad":0.0},
                        "tibia":{"center":77,"sign":1,"ref_rad":1.57}}},
    "LM": {"channels":[3,4,5],"side":"L","mount":[  0, 55],"mount_angle_deg":180,
           "servo_cal":{"coxa":{"center":85,"sign":-1,"ref_rad":0.0},
                        "femur":{"center":74,"sign":1,"ref_rad":0.0},
                        "tibia":{"center":94,"sign":1,"ref_rad":1.57}}},
    "LR": {"channels":[6,7,8],"side":"L","mount":[-60, 40],"mount_angle_deg":135,
           "servo_cal":{"coxa":{"center":94,"sign":-1,"ref_rad":0.0},
                        "femur":{"center":64,"sign":1,"ref_rad":0.0},
                        "tibia":{"center":89,"sign":1,"ref_rad":1.57}}}
  }
}
```

> القيم `center` مأخوذة من `gait_params.json` الحالي (زوايا الوقوف المعروفة) — نقطة ربط جاهزة. الإشارات (`sign`) من `FEMUR_DIRECTION` الموجود. تبقى المعايرة الدقيقة (`ref_rad`, الأطوال) في الخطة 04.

## مواقع الأقدام الافتراضية — `config/stance.json`

```json
{
  "_comment": "موقع كل قدم في إطار الجسم عند الوقوف (مم). z سالب=تحت الجسم.",
  "default": {
    "RF": [110,-95,-90], "RM": [0,-120,-90], "RR": [-110,-95,-90],
    "LF": [110, 95,-90], "LM": [0, 120,-90], "LR": [-110, 95,-90]
  }
}
```

---

## إجراء المعايرة (الربط بالواقع)

دالة معايرة شبه-آلية: نحرّك كل مفصل لزاوية سيرفو معروفة، نقيس الزاوية الفيزيائية، ونحسب `sign/ref_rad`. مفصّلة في الخطة 04. للبدء السريع يكفي:
1. ضع الروبوت بوضع الوقوف الحالي (زوايا `gait_params.json`).
2. عدّل `mount`/`mount_angle_deg` و`stance.json` حتى `stand_servos(default)` يُنتج نفس زوايا الوقوف ±2°.
3. تحقّق أن رفع z لقدم واحدة (مثلاً `RF z=-60`) يرفعها فعلاً عمودياً.

---

## خطوات التطبيق

1. أضف `spider/kinematics.py` و`config/robot_geometry.json` و`config/stance.json`.
2. سكربت تحقّق (بدون هاردوير): اطبع `stand_servos(default)` وقارن بزوايا الوقوف المعروفة.
3. على الهاردوير: `arbiter.acquire("ik_test"); motion.goto("ik_test", stand_servos(default))`. عدّل الهندسة حتى يقف صح.
4. اختبر `body_transform`: ميل pitch بسيط، إزاحة tz (رفع/خفض)، إزاحة ty (جانبي) — كل قدم ثابتة أرضياً.
5. الخطة 03 تبني المشي فوق هذا.

---

## الاختبار

| اختبار | المتوقّع |
|--------|----------|
| `stand_servos(default)` بلا هاردوير | يطابق زوايا الوقوف ±2° |
| `tz = -20` (خفض الجسم) | كل الأقدام ثابتة، الجسم ينخفض، Femur/Tibia تتغيّر منطقياً |
| `ty = +20` (إزاحة لليسار) | الجسم ينزاح يساراً والأقدام ثابتة — **زحف جانبي حقيقي بلا خدعة** |
| `pitch = 5°` | الجسم يميل أماماً، الأقدام ثابتة |
| رفع قدم واحدة `z+=40` | ترتفع عمودياً فقط |

---

## معايير القبول

- ✅ IK يُنتج زوايا الوقوف الحالية من مواقع الأقدام الافتراضية (±2°).
- ✅ تحويل وضعية الجسم (ميل/إزاحة/رفع) يعمل والأقدام تبقى ثابتة أرضياً.
- ✅ لا توجد بعد اليوم زاوية سيرفو مكتوبة يدوياً في منطق الحركة — كلها عبر IK.
- ✅ خريطة قنوات واحدة (لا تكرار `(leg_idx-3)*3`).
```
