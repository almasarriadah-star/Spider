# خطة 04 — ضبط البارامترات الحي (للنظام الحالي زاوية-مباشرة، بلا IK)

> الأولوية: 🟠 عالية. يعتمد على الخطط 01–03.
> الهدف: تضبط **أي** سلوك للروبوت لاحقاً من الواجهة، بلا تعديل كود وبلا إعادة تشغيل.

---

## ⚠️ توضيح مهم (تصحيح للنسخة السابقة)

النسخة السابقة من هذه الخطة احتوت قسم **«معايرة سيرفو بالمنقلة»** — ذاك كان **لِـ IK** (قياس زاوية كل مفصل فيزيائياً لحساب `sign/ref_rad`). **بما أننا تركنا IK، هذا غير مطلوب إطلاقاً** ونُقل لمجلد «تطويرات وافاق مستقبلية».

**هذه الخطة الآن = ضبط حي لزوايا ومعاملات حركاتك الحالية فقط. لا منقلة، لا IK، لا رياضيات مفاصل.**

---

## ملخص تنفيذي

بارامتراتك الحالية متناثرة بين ملفات JSON وثوابت في الكود. الحل:
1. **محرّر إعدادات حي** عبر الواجهة — يقرأ/يكتب أي ملف JSON مع نسخة احتياطية واستعادة.
2. **ضابط حدود سيرفو آمن** — تحرّك محركاً وتشاهده وتحدّد حدّه الأدنى/الأقصى بالملاحظة (يحلّ L3، بلا منقلة).
3. **إخراج الثوابت المخفيّة** من الكود لملفات JSON (سرعات المحكّم، إزاحات الحركة الجانبية والجسم).
4. **سجل بارامترات موثّق** — كل بارامتر: ماذا يفعل ومداه وأثره.

---

## ملفات الإعدادات (الوضع الحالي)

```
config/
├── servo_limits.json        # حدود كل محرك (موجود ✅)
├── gait_params.json         # معاملات المشي + زوايا الأرجل (نقل من الجذر)
├── ripple_gait_params.json  # معاملات Ripple (نقل من الجذر)
├── balance_config.json      # PID التوازن (نقل من الجذر)
└── motion.json              # سرعات المحكّم + إزاحات الحركة (جديد — إخراج ثوابت)
```

### `config/motion.json` (جديد — إخراج الثوابت المخفيّة)
```json
{
  "arbiter": {
    "rate_hz": 50,
    "max_deg_per_tick": 4.0,
    "max_simultaneous": 18,
    "_doc": {
      "rate_hz": "تردد حلقة كتابة المحركات. 40-60 مناسب. أعلى=أنعم، حمل CPU أكبر.",
      "max_deg_per_tick": "أقصى تغيّر زاوية لكل محرك في كل دورة. أصغر=أبطأ وأأمن (تيار ذروة أقل). 3-6.",
      "max_simultaneous": "كم محرك يتحرك فعلياً في الدورة الواحدة. أصغر=تسلسل أكثر=تيار أقل لكن أبطأ. 6-18."
    }
  },
  "lateral": {
    "shift_deg": 14, "lift_deg": 14,
    "_doc": {"shift_deg":"مقدار مدّ/طيّ الـ Tibia للإزاحة الجانبية. أكبر=خطوة جانبية أوسع.",
             "lift_deg":"رفع القدم أثناء الإزاحة الجانبية."}
  },
  "body": {
    "delta": 12,
    "_doc": {"delta":"مقدار درجات الميل/الارتفاع في حركات الجسم (lean/twist/body_up)."}
  }
}
```
> هذه القيم حالياً مكتوبة في الكود (`SHIFT_DEG=14`, `LIFT_DEG=14` في `spider/gaits.py`؛ `BODY_DELTA=12` في `spider/moves.py`؛ سرعات المحكّم في `spider/safety.py`). إخراجها لـ JSON يجعلها قابلة للضبط الحي.

---

## (1) المحرّر الحي العام (أي ملف JSON)

نقطة API واحدة لقراءة/كتابة/استعادة أي إعداد:

```python
import shutil, os, json
CONFIG_DIR = os.path.join(BASE_DIR, "config")
def _cfg_path(name): return os.path.join(CONFIG_DIR, name + ".json")

@app.route("/api/config/<name>", methods=["GET"])
def config_get(name):
    p = _cfg_path(name)
    if not os.path.exists(p): return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify(json.load(open(p, encoding="utf-8")))

@app.route("/api/config/<name>", methods=["POST"])
def config_set(name):
    p = _cfg_path(name)
    if os.path.exists(p): shutil.copy(p, p + ".bak")   # نسخة احتياطية قبل الكتابة
    json.dump(request.json, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    _reload_config(name)                                # إعادة تحميل حي
    return jsonify({"ok": True, "saved": name})

@app.route("/api/config/<name>/restore", methods=["POST"])
def config_restore(name):
    p = _cfg_path(name)
    if os.path.exists(p + ".bak"):
        shutil.copy(p + ".bak", p); _reload_config(name)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "no backup"}), 404

def _reload_config(name):
    """إعادة تحميل حيّ بلا إعادة تشغيل السيرفر."""
    if name == "servo_limits":
        from spider import config as _c; _c.reload()
    elif name == "balance_config":
        balance.reload()           # أضف balance.reload() يعيد قراءة الملف
    elif name == "motion":
        from spider import safety, gaits, moves
        safety.reload_motion(); gaits.reload_motion(); moves.reload_motion()
    # gait_params/ripple تُقرأ عند بدء كل مشي، فلا تحتاج reload خاص
```
> أضف `def reload()` بسيطة لكل وحدة تعيد `json.load`. `gait_params.json` يُقرأ عبر `_load_gait_params()` عند كل تشغيل مشي، فتعديله ينعكس على المشي التالي تلقائياً.

---

## (2) ضابط حدود السيرفو الآمن (بلا منقلة — بالملاحظة)

لحلّ L3 وأي محرك قريب من حدّه: حرّك المحرك تدريجياً وراقبه، وعند الزاوية القصوى الآمنة قبل التصادم، ثبّتها كحدّ.

```python
@app.route("/api/limit/nudge", methods=["POST"])
def limit_nudge():
    """حرّك محركاً واحداً لزاوية (للمعايرة بالملاحظة). يتجاوز حدوده مؤقتاً بحذر."""
    d = request.json; key = d["key"]; angle = int(d["angle"])
    arbiter.acquire("limit_tune")
    from spider import goto
    goto("limit_tune", {key: angle}, timeout=2.0)
    arbiter.release("limit_tune")
    return jsonify({"ok": True, "key": key, "angle": angle})

@app.route("/api/limit/set", methods=["POST"])
def limit_set():
    """يحفظ حدّاً آمناً لمحرك في servo_limits.json."""
    d = request.json
    data = json.load(open(_cfg_path("servo_limits"), encoding="utf-8"))
    data["overrides"][d["key"]] = {"min": int(d["min"]), "max": int(d["max"])}
    json.dump(data, open(_cfg_path("servo_limits"),"w",encoding="utf-8"), indent=2, ensure_ascii=False)
    from spider import config as _c; _c.reload()
    return jsonify({"ok": True})
```
> ⚠️ `limit_nudge` للمعايرة فقط — حرّك بخطوات صغيرة (2-3°) وراقب. هذا **ليس** قياساً بمنقلة، بل تحديد الحدّ الآمن بالعين.

---

## (3) سجل البارامترات الموثّق (مرجع الضبط — النظام الحالي)

### مشي Tripod (`gait_params.json`)
| البارامتر | يفعل | إذا زِدته |
|-----------|------|-----------|
| `total_frames` | عدد فريمات الدورة | حركة أنعم، أبطأ |
| `frame_delay_ms` | زمن كل فريم | أبطأ |
| `coxa_amplitude` | اتساع تأرجح Coxa (طول الخطوة) | خطوة أطول، أسرع، أقل ثباتاً |
| `smooth_steps` | خطوات التنعيم بين الفريمات | أنعم، أبطأ |
| `smooth_delay` | تأخير كل خطوة تنعيم | أنعم، أبطأ |
| `legs.<RF…>.coxa_stand` | زاوية وقوف Coxa لكل رجل | يضبط اتجاه/مركز الرجل |
| `legs.<RF…>.femur_stand` | زاوية وقوف Femur (ارتفاع) | أكبر/أصغر = الجسم أعلى/أوطأ لتلك الرجل |
| `legs.<RF…>.femur_lift` | زاوية رفع Femur أثناء swing | أعلى = رفعة أعلى للقدم |
| `legs.<RF…>.tibia` | زاوية Tibia (ثابتة بالمشي) | يضبط امتداد الرجل |

### مشي Ripple (`ripple_gait_params.json`)
| البارامتر | يفعل |
|-----------|------|
| `LIFT_DEG` | ارتفاع رفع القدم |
| `SWING_DEG` | اتساع خطوة Coxa |
| `RIPPLE_ORDER` | ترتيب رفع الأرجل |
| `total_frames` / `frame_delay_ms` | نعومة/سرعة الدورة |

### التوازن (`balance_config.json`)
| البارامتر | يفعل | ضبط |
|-----------|------|-----|
| `kp` | قوة التصحيح الفوري | ابدأ 0.8، زِد حتى يبدأ التذبذب ثم انقص ~20% |
| `kd` | تخميد | زِده لو تذبذب |
| `ki` | إزالة انحراف دائم | ابقِه 0 غالباً |
| `deadzone_deg` | تجاهل ميل صغير | أكبر=أهدأ، أقل دقة |
| `max_correction` | سقف التصحيح | أكبر=أقوى، خطر تجاوز |
| `frequency_hz` | تردد حلقة التوازن | 30 مناسب |

### المحكّم والحركة (`motion.json`)
| البارامتر | يفعل | إذا زِدته |
|-----------|------|-----------|
| `arbiter.max_deg_per_tick` | حدّ سرعة المحرك | حركة أسرع، تيار ذروة أعلى |
| `arbiter.max_simultaneous` | محركات متزامنة | أسرع، حمل كهربائي أكبر |
| `lateral.shift_deg` | اتساع الإزاحة الجانبية | إزاحة أوسع |
| `body.delta` | مقدار ميل/ارتفاع حركات الجسم | ميل أكبر |

### حدود المحركات (`servo_limits.json`)
| البارامتر | يفعل |
|-----------|------|
| `default.min/max` | الحد العام لكل المحركات (45/135) |
| `overrides.<key>.min/max` | حدّ خاص لمحرك (مثل L3=[60,120]) لمنع التصادم |

---

## (4) واجهة تبويب «إعدادات متقدّمة» (الخطة 05)

- قائمة الملفات: `gait_params / ripple_gait_params / balance_config / motion / servo_limits`.
- محرّر JSON (textarea) + «حفظ» + «استعادة النسخة الاحتياطية».
- قسم «حدود المحركات»: لكل محرك أزرار nudge ± + حقلا min/max + «حفظ».
- لكل بارامتر تلميح من جدول هذا الملف.

(كود الواجهة في الخطة 05.)

---

## خطوات التطبيق
1. انقل `gait_params.json`, `ripple_gait_params.json`, `balance_config.json` إلى `config/` (حدّث المسارات في `spider/gaits.py` و`spider/balance.py`).
2. أنشئ `config/motion.json` وأخرج الثوابت إليه (`max_deg_per_tick`, `shift_deg`, `body.delta`…)، واقرأها في الوحدات.
3. أضف routes المحرّر الحي + ضابط الحدود + دوال `reload()`.
4. ابنِ تبويب الإعدادات (الخطة 05).
5. اضبط حدود L3 بالملاحظة واحفظ.

---

## معايير القبول
- ✅ كل بارامتر سلوكي في JSON قابل للضبط الحي من الواجهة بلا إعادة تشغيل.
- ✅ نسخة احتياطية/استعادة لكل ملف.
- ✅ حدود L3 مضبوطة بالملاحظة (بلا منقلة).
- ✅ الثوابت المخفيّة (سرعات المحكّم/إزاحات الحركة) صارت في JSON.
- ✅ جدول مرجعي يشرح كل بارامتر — **بلا أي ذكر لـ IK أو منقلة**.
```
