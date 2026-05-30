# خطة 07 — GPS وعرض موقع الروبوت على الخريطة

> الأولوية: 🟡 متوسطة. تُجهَّز الآن، وتُربط بشريحة GPS لاحقاً (بغض النظر عن التوصيل الكهربائي).

---

## ملخص تنفيذي

تجهيز كامل لاستقبال GPS وعرض مسار الروبوت حيّاً على خريطة. نبني:
1. **قارئ GPS** (NMEA عبر UART/USB، مثل NEO-6M/NEO-M8N) مع وضع **محاكاة** ليُختبر بلا عتاد.
2. **مخزن تتبّع (track store)** يحفظ المسار + الإحداثي الحالي + السرعة/الاتجاه.
3. **خريطة Leaflet** في الواجهة (تعمل أوفلاين عبر بلاطات محلية أو OSM) تعرض موقع الروبوت ومساره وتتحدّث حيّاً.
4. **بثّ حيّ** عبر SSE.

> هذه الخطة تؤسّس «طبقة الخريطة» التي تبني فوقها الخطط 08 (ليدار) و09 (رطوبة/أعشاب) نقاطها.

---

## المعمارية

```
spider/sensors/gps.py        → قراءة NMEA / محاكاة → {lat,lon,fix,sats,speed,course,ts}
spider/telemetry.py          → TrackStore: المسار + آخر موقع + نقاط الاهتمام (POI)
routes: /api/gps/now, /api/gps/track, /api/stream (SSE), /map (صفحة)
templates/map.html           → Leaflet
```

نموذج بيانات موحّد للنقاط (تستخدمه كل الخطط):
```python
POI = {
  "id": str, "lat": float, "lon": float, "ts": float,
  "type": "soil" | "weed" | "lidar_obstacle" | "marker",
  "props": { ... }   # مثل {"moisture": 42} أو {"risk":"high","weed":"..."}
}
```

---

## الكود — `spider/sensors/gps.py`

```python
# spider/sensors/gps.py
import threading, time, math, random

class GPS:
    def __init__(self, port="/dev/serial0", baud=9600, simulate=False,
                 sim_origin=(31.9539, 35.9106)):   # عمّان كمثال
        self.simulate = simulate
        self.data = {"lat":None,"lon":None,"fix":False,"sats":0,
                     "speed":0.0,"course":0.0,"ts":0}
        self._stop = threading.Event()
        self._ser = None
        self._sim = {"lat":sim_origin[0],"lon":sim_origin[1],"course":0.0}
        if not simulate:
            try:
                import serial
                self._ser = serial.Serial(port, baud, timeout=1)
            except Exception:
                self.simulate = True   # fallback للمحاكاة

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.is_set():
            if self.simulate:
                self._sim_step()
            else:
                self._read_nmea()
            time.sleep(1.0)

    # ── محاكاة: يتحرّك الروبوت ببطء حسب أمر المشي الحالي ──
    def _sim_step(self):
        try:
            from spider.gait import gait
            vx = gait.cmd.get("vx",0); vy = gait.cmd.get("vy",0)
        except Exception:
            vx = vy = 0
        # حوّل مم/دورة لتغيّر تقريبي بالإحداثيات (تقريب فجّ للعرض فقط)
        speed = math.hypot(vx, vy) / 1000.0          # م/ث تقريبي
        self._sim["course"] = (self._sim["course"] + (gait.cmd.get("omega",0)*10 if 'gait' in dir() else 0)) % 360
        dist_m = speed * 1.0
        dlat = (dist_m * math.cos(math.radians(self._sim["course"]))) / 111111.0
        dlon = (dist_m * math.sin(math.radians(self._sim["course"]))) / (111111.0*math.cos(math.radians(self._sim["lat"])))
        self._sim["lat"] += dlat; self._sim["lon"] += dlon
        self.data = {"lat":round(self._sim["lat"],7),"lon":round(self._sim["lon"],7),
                     "fix":True,"sats":9,"speed":round(speed,2),
                     "course":round(self._sim["course"],1),"ts":time.time()}

    # ── قراءة NMEA حقيقية (GPRMC/GPGGA) ──
    def _read_nmea(self):
        try:
            line = self._ser.readline().decode("ascii", "ignore").strip()
        except Exception:
            return
        if line.startswith("$GPRMC") or line.startswith("$GNRMC"):
            p = line.split(",")
            if len(p) > 8 and p[2] == "A":
                self.data.update({
                    "lat": self._nmea_deg(p[3], p[4]),
                    "lon": self._nmea_deg(p[5], p[6]),
                    "fix": True,
                    "speed": float(p[7] or 0) * 0.514,   # عقدة→م/ث
                    "course": float(p[8] or 0),
                    "ts": time.time()})
        elif line.startswith("$GPGGA") or line.startswith("$GNGGA"):
            p = line.split(",")
            if len(p) > 7:
                try: self.data["sats"] = int(p[7] or 0)
                except: pass

    @staticmethod
    def _nmea_deg(val, hemi):
        if not val: return None
        deg = int(float(val)/100)
        minutes = float(val) - deg*100
        d = deg + minutes/60
        return -d if hemi in ("S","W") else round(d,7)
```

## مخزن التتبّع — `spider/telemetry.py`

```python
# spider/telemetry.py
import threading, time, json, os, uuid

class TrackStore:
    def __init__(self, path="data/track.json", max_points=5000):
        self.path = path
        self.max = max_points
        self.track = []     # [{lat,lon,ts}]
        self.pois  = []     # نقاط الاهتمام (soil/weed/obstacle/marker)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._load()

    def add_fix(self, lat, lon, ts=None):
        if lat is None or lon is None: return
        with self._lock:
            self.track.append({"lat":lat,"lon":lon,"ts":ts or time.time()})
            if len(self.track) > self.max:
                self.track = self.track[-self.max:]

    def add_poi(self, type_, lat, lon, props=None):
        poi = {"id":uuid.uuid4().hex[:8],"type":type_,"lat":lat,"lon":lon,
               "ts":time.time(),"props":props or {}}
        with self._lock:
            self.pois.append(poi); self._save()
        return poi

    def snapshot(self):
        with self._lock:
            return {"track":list(self.track),"pois":list(self.pois)}

    def _save(self):
        json.dump({"pois":self.pois}, open(self.path,"w"), ensure_ascii=False)
    def _load(self):
        if os.path.exists(self.path):
            try: self.pois = json.load(open(self.path)).get("pois",[])
            except: pass

store = TrackStore()
```

## ربط GPS بالمخزن + Routes + SSE

```python
from spider.sensors.gps import GPS
from spider.telemetry import store
import json, time
from flask import Response

gps = GPS(simulate=True)   # ⚠️ غيّرها لـ False عند توصيل الشريحة
gps.start()

def _gps_to_store():
    import threading
    def loop():
        while True:
            d = gps.data
            if d["fix"]: store.add_fix(d["lat"], d["lon"], d["ts"])
            time.sleep(1.0)
    threading.Thread(target=loop, daemon=True).start()
_gps_to_store()

@app.route("/api/gps/now")
def gps_now():
    return jsonify(gps.data)

@app.route("/api/gps/track")
def gps_track():
    return jsonify(store.snapshot())

@app.route("/api/stream")
def stream():
    """SSE: يبثّ موقع + حالة كل ثانية."""
    def gen():
        while True:
            payload = {"gps": gps.data, "gait": getattr(__import__('spider.gait',fromlist=['gait']),'gait').status()}
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(1.0)
    return Response(gen(), mimetype="text/event-stream")

@app.route("/map")
def map_page():
    return render_template("map.html")
```

## الخريطة — `templates/map.html` (Leaflet)

```html
<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>#map{height:100vh}body{margin:0}</style></head><body>
<div id="map"></div>
<script>
const map = L.map('map').setView([31.9539,35.9106], 18);
// أوفلاين: استبدل الرابط ببلاطات محلية mbtiles لو لا إنترنت في الحقل
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:22}).addTo(map);

const robot = L.marker([31.9539,35.9106]).addTo(map).bindPopup('🕷️ الروبوت');
const trackLine = L.polyline([], {color:'#58a6ff', weight:3}).addTo(map);
const poiLayer = L.layerGroup().addTo(map);

function poiIcon(type){
  const m={soil:'💧',weed:'🌿',lidar_obstacle:'⛔',marker:'📍'};
  return L.divIcon({html:`<div style="font-size:22px">${m[type]||'📍'}</div>`,className:''});
}
async function refreshTrack(){
  const d = await (await fetch('/api/gps/track')).json();
  trackLine.setLatLngs(d.track.map(p=>[p.lat,p.lon]));
  poiLayer.clearLayers();
  d.pois.forEach(p=>{
    let label = p.type;
    if(p.type==='soil')  label = `رطوبة ${p.props.moisture}%`;
    if(p.type==='weed')  label = `خطر: ${p.props.risk||''} ${p.props.weed||''}`;
    L.marker([p.lat,p.lon],{icon:poiIcon(p.type)}).addTo(poiLayer)
      .bindTooltip(label,{permanent:true,direction:'top',className:'poiLbl'});
  });
}
// بثّ حيّ للموقع
const es = new EventSource('/api/stream');
es.onmessage = e=>{
  const d = JSON.parse(e.data);
  if(d.gps && d.gps.fix){
    const ll=[d.gps.lat,d.gps.lon];
    robot.setLatLng(ll);
    if(!map._centeredOnce){ map.setView(ll,18); map._centeredOnce=true; }
  }
};
refreshTrack(); setInterval(refreshTrack, 3000);
</script></body></html>
```

> **أوفلاين في الحقل:** غالباً لا إنترنت. جهّز بلاطات الخريطة للمنطقة مسبقاً كـ `.mbtiles` وقدّمها عبر route محلي، أو استخدم صورة جوية ثابتة كطبقة `L.imageOverlay`. (إضافة لاحقة بسيطة.)

---

## خطوات التطبيق
1. أضف `spider/sensors/gps.py` و`spider/telemetry.py` (وضع `simulate=True`).
2. أضف routes + `map.html`. اختبر المسار يتحرّك بالمحاكاة مع أوامر المشي.
3. عند وصول الشريحة: وصّلها على UART (`/dev/serial0`)، اضبط `simulate=False`، تحقّق من `$GxRMC`.
4. ⚠️ تعارض UART مع BNO085: الـ IMU يستخدم `/dev/serial0` حالياً. وصّل GPS على منفذ USB-TTL (`/dev/ttyUSB0`) أو UART برمجي مختلف. حدّث `port` تبعاً لذلك.

---

## معايير القبول
- ✅ موقع الروبوت يظهر ويتحرّك حيّاً على الخريطة.
- ✅ المسار يُرسم تراكمياً.
- ✅ يعمل بالمحاكاة بلا عتاد، ويتحوّل لـ GPS حقيقي بتغيير علم واحد.
- ✅ نموذج POI موحّد جاهز للخطط 08/09.
- ✅ بثّ حيّ عبر SSE بلا إعادة تحميل.
```
