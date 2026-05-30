# خطة 08 — الليدار (Lidar) وعرض المسح

> الأولوية: 🟡 متوسطة. تعتمد على طبقة الخريطة/التتبّع (الخطة 07).

---

## ملخص تنفيذي

تجهيز قراءة ليدار دوّار (مثل **RPLIDAR A1/A2** أو **YDLIDAR**) وعرضه بطريقتين:
1. **عرض قطبي حيّ (Polar/Radar)** في الواجهة — مسح 360° حول الروبوت (Canvas).
2. **إسقاط العوائق على الخريطة** كـ POI من نوع `lidar_obstacle` بإحداثيات عالمية (دمج موضع GPS + اتجاه IMU).

نضيف وضع **محاكاة** ليُختبر بلا عتاد.

---

## المعمارية

```
spider/sensors/lidar.py
  ├── Lidar         → خيط يقرأ (angle, distance, quality) → scan dict {angle:dist}
  ├── obstacles_world(scan, robot_lat, robot_lon, heading) → نقاط عالمية
  └── routes: /api/lidar/scan (JSON), /api/lidar/status
templates: لوحة رادار في تبويب الحساسات + إسقاط على map.html
```

---

## الكود — `spider/sensors/lidar.py`

```python
# spider/sensors/lidar.py
import threading, time, math, random

class Lidar:
    def __init__(self, port="/dev/ttyUSB0", simulate=False):
        self.simulate = simulate
        self.scan = {}            # {angle_deg(int): distance_mm}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.ready = False
        self._dev = None
        if not simulate:
            try:
                from rplidar import RPLidar
                self._dev = RPLidar(port)
                self.ready = True
            except Exception:
                self.simulate = True

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        if self.simulate:
            self._sim_loop()
        else:
            self._real_loop()

    def _real_loop(self):
        try:
            for scan in self._dev.iter_scans(max_buf_meas=500):
                if self._stop.is_set(): break
                new = {}
                for quality, angle, dist in scan:
                    if dist > 0:
                        new[int(angle) % 360] = dist     # مم
                with self._lock:
                    self.scan = new
                self.ready = True
        except Exception:
            self.ready = False

    def _sim_loop(self):
        """محاكاة غرفة مستطيلة + عائق متحرّك."""
        self.ready = True
        t = 0
        while not self._stop.is_set():
            new = {}
            for a in range(0, 360, 1):
                r = math.radians(a)
                # جدران مستطيلة افتراضية
                walls = []
                for d, n in [(2500,(1,0)),(2500,(-1,0)),(2000,(0,1)),(2000,(0,-1))]:
                    denom = math.cos(r)*n[0] + math.sin(r)*n[1]
                    if denom > 1e-3: walls.append(d/denom)
                dist = min([w for w in walls if w>0] + [4000])
                # عائق متحرّك
                obs_ang = (t*2) % 360
                if abs(((a - obs_ang + 180) % 360) - 180) < 8:
                    dist = min(dist, 800)
                new[a] = dist + random.uniform(-20,20)
            with self._lock:
                self.scan = new
            t += 1
            time.sleep(0.1)

    def get_scan(self):
        with self._lock:
            return dict(self.scan)

    def stop(self):
        self._stop.set()
        if self._dev:
            try: self._dev.stop(); self._dev.disconnect()
            except: pass


def obstacles_world(scan, lat, lon, heading_deg, max_mm=2000, step=10):
    """يحوّل مسح الليدار لنقاط عوائق عالمية (للخريطة).
    heading_deg من IMU (yaw). يأخذ نقطة كل `step` درجة وأقرب من max_mm."""
    pts = []
    for a in range(0, 360, step):
        d = scan.get(a)
        if not d or d > max_mm: continue
        world_ang = math.radians((heading_deg + a) % 360)
        dn = (d/1000.0) * math.cos(world_ang)   # شمال (متر)
        de = (d/1000.0) * math.sin(world_ang)   # شرق (متر)
        plat = lat + dn/111111.0
        plon = lon + de/(111111.0*math.cos(math.radians(lat)))
        pts.append({"lat":round(plat,7),"lon":round(plon,7),"dist":round(d)})
    return pts
```

## Routes

```python
from spider.sensors.lidar import Lidar, obstacles_world
lidar = Lidar(simulate=True)   # ⚠️ False عند التوصيل
lidar.start()

@app.route("/api/lidar/scan")
def lidar_scan():
    return jsonify({"ready": lidar.ready, "scan": lidar.get_scan()})

@app.route("/api/lidar/status")
def lidar_status():
    return jsonify({"ready": lidar.ready, "points": len(lidar.get_scan())})

@app.route("/api/lidar/project", methods=["POST"])
def lidar_project():
    """يُسقط العوائق الحالية على الخريطة كـ POI (لقطة)."""
    from spider.sensors.gps import gps
    from spider.telemetry import store
    g = gps.data
    heading = 0.0
    try:
        from spider.sensors.imu import read_yaw   # أو read_bno_single
        heading = read_yaw() or 0.0
    except Exception: pass
    if not g["fix"]: return jsonify({"ok": False, "error": "no gps fix"})
    pts = obstacles_world(lidar.get_scan(), g["lat"], g["lon"], heading)
    for p in pts:
        store.add_poi("lidar_obstacle", p["lat"], p["lon"], {"dist": p["dist"]})
    return jsonify({"ok": True, "added": len(pts)})
```

## لوحة الرادار — واجهة (Canvas قطبي)

```html
<div class="card">
  <h2>📡 الليدار (مسح 360°)</h2>
  <canvas id="radar" width="320" height="320" style="background:#05080a;border-radius:12px;display:block;margin:auto"></canvas>
  <div style="text-align:center;margin-top:6px">
    <span id="lidarStatus" style="font-size:11px;color:#666"></span>
    <button class="btn btn-blue" onclick="lidarProject()" style="padding:6px 12px">📍 أسقِط على الخريطة</button>
  </div>
</div>
<script>
const rc = document.getElementById('radar'), rx = rc.getContext('2d');
const RC=160, MAXD=3000; // مم لكامل نصف القطر
async function drawRadar(){
  const d = await (await fetch('/api/lidar/scan')).json();
  document.getElementById('lidarStatus').textContent =
    d.ready ? `🟢 ${Object.keys(d.scan).length} نقطة` : '🔴 غير متصل';
  rx.clearRect(0,0,320,320);
  // حلقات
  rx.strokeStyle='#1a2a2a';
  for(let r=40;r<=160;r+=40){rx.beginPath();rx.arc(RC,RC,r,0,7);rx.stroke();}
  rx.fillStyle='#3fb950'; rx.beginPath(); rx.arc(RC,RC,4,0,7); rx.fill(); // الروبوت
  // نقاط
  rx.fillStyle='#f0883e';
  for(const [a,dist] of Object.entries(d.scan)){
    const rr = Math.min(dist/MAXD,1)*RC;
    const ang = (parseInt(a)-90)*Math.PI/180;  // 0°=أمام=أعلى
    rx.beginPath(); rx.arc(RC+rr*Math.cos(ang), RC+rr*Math.sin(ang), 2, 0, 7); rx.fill();
  }
}
async function lidarProject(){
  const r = await (await fetch('/api/lidar/project',{method:'POST'})).json();
  alert(r.ok ? `أُضيفت ${r.added} عائق للخريطة` : 'خطأ: '+(r.error||''));
}
setInterval(drawRadar, 200); drawRadar();
</script>
```

وعلى `map.html`: نقاط `lidar_obstacle` تظهر تلقائياً (نموذج POI من الخطة 07).

---

## نقاط مهمة
- **التغذية والـ USB:** الليدار يسحب تياراً معتبراً ومحرّكه على USB منفصل — لا تشغّله من نفس مصدر السيرفوات.
- **دقّة الإسقاط تعتمد على heading من IMU:** عاير yaw الـ BNO085 مع شمال البوصلة قبل الإسقاط، وإلا تنحرف العوائق.
- **التزامن مع المشي:** الليدار على USB لا I2C، فلا يزاحم ناقل المحركات — ميزة.
- لاحقاً: بناء خريطة احتلال (occupancy grid) وتفادي عوائق تلقائي (يدخل أمر `vx,vy,ω` للخطة 03). خارج نطاق هذه الخطة.

---

## معايير القبول
- ✅ عرض رادار قطبي حيّ 360°.
- ✅ إسقاط العوائق على الخريطة كـ POI عالمية.
- ✅ يعمل بالمحاكاة بلا عتاد.
- ✅ لا يزاحم ناقل I2C للمحركات.
```
