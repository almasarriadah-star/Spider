# خطة 09 — رطوبة التربة وكشف الأعشاب الضارة على الخريطة

> الأولوية: 🟢 لاحقة (لكنها هدف المشروع النهائي). تعتمد على الخطط 06 (كاميرا) و07 (خريطة/POI).

---

## ملخص تنفيذي

المهمة الزراعية للروبوت:
1. **رطوبة التربة:** عند كل نقطة قياس، نقرأ حساس الرطوبة ونضيف نقطة على الخريطة مكتوب عليها **نسبة الرطوبة %**.
2. **خطر النباتات:** نحلّل صورة الكاميرا عند النقطة، نكتشف هل توجد أعشاب ضارة، ونضيف نقطة خطر على الخريطة بتصنيف (آمن/خطر) ونوع العشب.
3. كل النقاط تُحفظ في نموذج POI الموحّد (الخطة 07) وتُعرض على الخريطة مع وسومها.

نوفّر **وضع مسح آلي**: أثناء المشي، الروبوت يأخذ قياس رطوبة + لقطة كل مسافة/زمن محدّد ويوسمها تلقائياً.

---

## (أ) رطوبة التربة

### العتاد
- حساس رطوبة تربة (capacitive موصى به — لا يتآكل). خرجه **تناظري**.
- الـ Pi لا منفذ تناظري → نحتاج **ADC مثل ADS1115** (I2C, عنوان 0x48). يقرأ الجهد ونحوّله لنسبة %.
- المعايرة: قراءة في الهواء (جاف=0%) وفي ماء/تربة مشبعة (100%).

### الكود — `spider/sensors/soil.py`
```python
# spider/sensors/soil.py
import threading, time

class SoilSensor:
    def __init__(self, i2c_lock=None, channel=0,
                 dry_raw=26000, wet_raw=11000, simulate=False):
        """dry_raw/wet_raw: قراءات ADC الخام عند 0% و100% — ⚠️ عايرها."""
        self.lock = i2c_lock
        self.channel = channel
        self.dry, self.wet = dry_raw, wet_raw
        self.simulate = simulate
        self.ready = False
        self._chan = None
        if not simulate:
            try:
                import board, busio
                import adafruit_ads1x15.ads1115 as ADS
                from adafruit_ads1x15.analog_in import AnalogIn
                i2c = busio.I2C(board.SCL, board.SDA)
                ads = ADS.ADS1115(i2c)
                self._chan = AnalogIn(ads, getattr(ADS, f"P{channel}"))
                self.ready = True
            except Exception:
                self.simulate = True

    def read_raw(self):
        if self.simulate:
            import random; return random.randint(self.wet, self.dry)
        try:
            if self.lock: self.lock.acquire()
            return self._chan.value
        finally:
            if self.lock: self.lock.release()

    def read_percent(self):
        raw = self.read_raw()
        # كلما زادت الرطوبة قلّت القراءة (capacitive)
        pct = (self.dry - raw) / max(1, (self.dry - self.wet)) * 100.0
        return max(0.0, min(100.0, round(pct, 1)))
```

### Route — قياس + وسم على الخريطة
```python
from spider.sensors.soil import SoilSensor
from spider.sensors.gps import gps
from spider.telemetry import store
from spider.hardware import i2c_lock

soil = SoilSensor(i2c_lock=i2c_lock, simulate=True)  # ⚠️ False عند التوصيل

@app.route("/api/soil/read")
def soil_read():
    return jsonify({"ok": soil.ready or soil.simulate,
                    "moisture": soil.read_percent()})

@app.route("/api/soil/sample", methods=["POST"])
def soil_sample():
    """يأخذ قياس رطوبة عند الموقع الحالي ويضيفه للخريطة."""
    g = gps.data
    if not g["fix"]:
        return jsonify({"ok": False, "error": "no gps fix"})
    pct = soil.read_percent()
    poi = store.add_poi("soil", g["lat"], g["lon"], {"moisture": pct})
    return jsonify({"ok": True, "poi": poi})
```

على الخريطة (الخطة 07) تظهر نقطة `soil` بوسم «رطوبة 42%» تلقائياً.

---

## (ب) كشف الأعشاب الضارة (تحليل الصور)

### نهج متدرّج (من البسيط للذكي)
1. **مرحلة 1 — كشف الغطاء النباتي (سريع، بلا تدريب):** مؤشّر **ExG** (Excess Green = `2G − R − B`) لتحديد نسبة النبات الأخضر في الصورة. عتبة عالية + شكل غير منتظم ⇒ احتمال عشب بين المحصول. يعطي «نسبة تغطية خضراء» و«مناطق مشبوهة».
2. **مرحلة 2 — تصنيف (ذكي، لاحقاً):** نموذج TFLite صغير (MobileNet/أو YOLO-nano) يميّز «محصول/عشب ضار/تربة». نضع خطّافاً (hook) جاهزاً، ونبدأ بالمرحلة 1.

### الكود — `spider/vision/weeds.py`
```python
# spider/vision/weeds.py
import numpy as np

def analyze_frame(bgr):
    """يحلّل إطار BGR ويعيد تقييم خطر مبدئي بمؤشّر ExG.
    يرجع: {green_ratio, suspicious_ratio, risk}"""
    import cv2
    b, g, r = cv2.split(bgr.astype(np.float32))
    exg = 2*g - r - b                       # Excess Green
    veg = exg > 30                          # ⚠️ عتبة قابلة للضبط
    green_ratio = float(veg.mean())
    # مناطق خضراء كثيفة خارج صفوف المحصول = مشبوهة (تقريب: كتل خضراء صغيرة معزولة)
    veg_u8 = (veg*255).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(veg_u8, 8)
    small = sum(1 for i in range(1,n) if 50 < stats[i, cv2.CC_STAT_AREA] < 1500)
    suspicious_ratio = small / max(1, n)
    if green_ratio > 0.15 and suspicious_ratio > 0.3:
        risk = "high"
    elif green_ratio > 0.08:
        risk = "medium"
    else:
        risk = "low"
    return {"green_ratio": round(green_ratio,3),
            "suspicious_ratio": round(suspicious_ratio,3),
            "risk": risk}

# خطّاف نموذج ذكي (المرحلة 2) — اتركه None حتى تجهّز TFLite
TFLITE = None
def classify_weed(bgr):
    """يُرجع (label, confidence) أو None. يُملأ لاحقاً بنموذج TFLite."""
    if TFLITE is None:
        return None
    # ... تشغيل النموذج ...
    return None
```

### Route — تحليل + وسم
```python
from spider.vision.weeds import analyze_frame, classify_weed

@app.route("/api/weeds/sample", methods=["POST"])
def weeds_sample():
    """يلتقط إطاراً من الكاميرا، يحلّله، ويضيف نقطة خطر للخريطة."""
    from spider.sensors.camera import rgb_cam
    frame = rgb_cam.read()
    if frame is None:
        return jsonify({"ok": False, "error": "no camera"})
    res = analyze_frame(frame)
    cls = classify_weed(frame)
    if cls: res["weed"] = cls[0]; res["conf"] = cls[1]
    g = gps.data
    if not g["fix"]:
        return jsonify({"ok": True, "analysis": res, "mapped": False})
    poi = store.add_poi("weed", g["lat"], g["lon"], res)
    return jsonify({"ok": True, "analysis": res, "poi": poi})
```

على الخريطة تظهر نقطة `weed` بوسم «خطر: high» (ولونها يتغيّر حسب الخطورة — أضف لون الأيقونة في `map.html` حسب `props.risk`).

---

## (ج) المسح الآلي أثناء المشي

خيط يأخذ عيّنة رطوبة + تحليل عشب كل مسافة محدّدة (حسب GPS) أثناء تفعيل وضع المسح:

```python
import threading, time, math
_survey = {"on": False, "min_dist_m": 1.0, "last": None}

def _survey_loop():
    while True:
        if _survey["on"]:
            g = gps.data
            if g["fix"]:
                last = _survey["last"]
                moved = 999 if last is None else _haversine(last, (g["lat"],g["lon"]))
                if moved >= _survey["min_dist_m"]:
                    _survey["last"] = (g["lat"], g["lon"])
                    # رطوبة
                    store.add_poi("soil", g["lat"], g["lon"], {"moisture": soil.read_percent()})
                    # عشب
                    try:
                        from spider.sensors.camera import rgb_cam
                        f = rgb_cam.read()
                        if f is not None:
                            store.add_poi("weed", g["lat"], g["lon"], analyze_frame(f))
                    except Exception: pass
        time.sleep(0.5)

def _haversine(a, b):
    R=6371000; la1,lo1,la2,lo2=map(math.radians,[a[0],a[1],b[0],b[1]])
    h=math.sin((la2-la1)/2)**2+math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*R*math.asin(math.sqrt(h))

threading.Thread(target=_survey_loop, daemon=True).start()

@app.route("/api/survey/<state>", methods=["POST"])
def survey_toggle(state):
    _survey["on"] = (state == "on")
    if state == "on": _survey["last"] = None
    return jsonify({"ok": True, "survey": _survey["on"]})
```

### واجهة (تبويب الحساسات)
```html
<div class="card">
  <h2>🌱 المسح الزراعي</h2>
  <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
    <div class="imu-val"><div class="label">الرطوبة الآن</div>
      <div class="num" id="soilNow" style="color:#3fb950">—%</div></div>
    <div class="imu-val"><div class="label">خطر النبات</div>
      <div class="num" id="weedNow" style="color:#f0883e">—</div></div>
  </div>
  <div style="text-align:center;margin-top:8px">
    <button class="btn btn-green" onclick="soilSample()">💧 قِس رطوبة هنا</button>
    <button class="btn btn-orange" onclick="weedSample()">🌿 حلّل العشب هنا</button>
    <button class="btn btn-blue" id="survBtn" onclick="toggleSurvey()">▶ مسح آلي</button>
    <a class="btn btn-purple" href="/map" target="_blank" style="padding:8px 14px">🗺️ الخريطة</a>
  </div>
</div>
<script>
let surveyOn=false;
async function soilNow(){
  const d=await (await fetch('/api/soil/read')).json();
  if(d.ok) document.getElementById('soilNow').textContent = d.moisture+'%';
}
async function soilSample(){ const r=await (await fetch('/api/soil/sample',{method:'POST'})).json();
  alert(r.ok?`أُضيفت نقطة رطوبة ${r.poi.props.moisture}%`:'لا إشارة GPS'); }
async function weedSample(){ const r=await (await fetch('/api/weeds/sample',{method:'POST'})).json();
  if(r.ok){document.getElementById('weedNow').textContent=r.analysis.risk;
    alert('خطر: '+r.analysis.risk+(r.mapped===false?' (بلا GPS)':' — أُضيف للخريطة'));} }
async function toggleSurvey(){ surveyOn=!surveyOn;
  await fetch('/api/survey/'+(surveyOn?'on':'off'),{method:'POST'});
  document.getElementById('survBtn').textContent = surveyOn?'⏹ إيقاف المسح':'▶ مسح آلي'; }
setInterval(soilNow, 2000); soilNow();
</script>
```

---

## ملاحظات
- **ADS1115 على نفس I2C:** استخدم `i2c_lock`. عنوانه 0x48 ≠ المحركات (0x40/0x44) ≠ الحراري (0x33) — لا تضارب عناوين.
- **دقّة موقع النقاط:** GPS عادي دقّته ~2–5م. للزراعة الدقيقة لاحقاً فكّر بـ RTK-GPS.
- **معايرة الرطوبة إلزامية** (dry_raw/wet_raw) لكل نوع تربة.
- **نموذج الأعشاب الذكي (TFLite):** يحتاج بيانات تدريب لمحصولك. ابدأ بـ ExG، ثم اجمع صوراً موسومة من نفس الكاميرا وحقلك ودرّب MobileNet — الخطّاف `classify_weed` جاهز.
- **تصدير التقرير:** أضف `/api/survey/export` يحفظ كل POI كـ GeoJSON/CSV لفتحه في QGIS أو Excel. (إضافة بسيطة لاحقاً.)

---

## معايير القبول
- ✅ قراءة رطوبة % معايرة، ووسم نقطة على الخريطة بالنسبة.
- ✅ تحليل صورة يعطي تصنيف خطر، ووسم نقطة عشب على الخريطة.
- ✅ وضع مسح آلي يأخذ عيّنات كل مسافة أثناء المشي.
- ✅ كل النقاط في نموذج POI موحّد وتُحفظ وتُعرض.
- ✅ لا تضارب I2C مع المحركات.
```
