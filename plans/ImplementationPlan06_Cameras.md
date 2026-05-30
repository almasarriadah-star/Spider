# خطة 06 — الكاميرا العادية والكاميرا الحرارية

> الأولوية: 🟡 متوسطة. مستقلّة عن الحركة — يمكن تطويرها بالتوازي.

---

## ملخص تنفيذي

تجهيز بثّ كاميرتين معاً في الواجهة:
1. **كاميرا عادية (RGB):** Pi Camera أو USB (مثل Logitech) عبر MJPEG stream.
2. **كاميرا حرارية:** حساس مثل **MLX90640** (32×24، I2C) أو **AMG8833** (8×8) — نقرأ مصفوفة الحرارة ونلوّنها (colormap) ونبثّها كصورة.
3. عرضهما جنباً لجنب في الواجهة مع طبقة شفافة اختيارية (دمج RGB + حراري).

> ملاحظة I2C: الكاميرا الحرارية على نفس ناقل الـ PCA9685. عناوينها مختلفة (MLX90640=0x33) فلا تضارب، لكن استخدم `i2c_lock` عند القراءة لتفادي تشويش الناقل أثناء كتابة المحركات.

---

## المعمارية

```
spider/sensors/camera.py
  ├── RGBCamera   → generator يبثّ JPEG frames (MJPEG)
  ├── ThermalCamera → يقرأ مصفوفة، يلوّنها، يبثّ JPEG
  └── routes: /video/rgb (MJPEG), /video/thermal (MJPEG), /api/thermal/frame (JSON خام)
```

---

## الكود — `spider/sensors/camera.py`

```python
# spider/sensors/camera.py
import time, threading, io
import numpy as np

# ── الكاميرا العادية (Picamera2 أو OpenCV/USB) ──
class RGBCamera:
    def __init__(self, width=640, height=480, src=0):
        self.w, self.h = width, height
        self.cap = None
        self.backend = None
        try:
            from picamera2 import Picamera2
            self.cam = Picamera2()
            self.cam.configure(self.cam.create_video_configuration(
                main={"size": (width, height), "format": "RGB888"}))
            self.cam.start()
            self.backend = "picamera2"
        except Exception:
            try:
                import cv2
                self.cv2 = cv2
                self.cap = cv2.VideoCapture(src)
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                self.backend = "opencv"
            except Exception:
                self.backend = None

    def read(self):
        """يُرجع frame كـ ndarray BGR، أو None."""
        if self.backend == "picamera2":
            rgb = self.cam.capture_array()
            import cv2
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        elif self.backend == "opencv":
            ok, frame = self.cap.read()
            return frame if ok else None
        return None

    def mjpeg(self):
        import cv2
        while True:
            frame = self.read()
            if frame is None:
                time.sleep(0.05); continue
            ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + jpg.tobytes() + b"\r\n")
            time.sleep(1/20)


# ── الكاميرا الحرارية (MLX90640) ──
class ThermalCamera:
    def __init__(self, i2c_lock=None):
        self.lock = i2c_lock
        self.ready = False
        self.last_frame = None      # مصفوفة 24×32 درجات مئوية
        try:
            import board, busio, adafruit_mlx90640
            i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
            self.mlx = adafruit_mlx90640.MLX90640(i2c)
            self.mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_8_HZ
            self.shape = (24, 32)
            self.ready = True
        except Exception:
            self.ready = False

    def read_matrix(self):
        if not self.ready:
            return None
        buf = [0]*768
        try:
            if self.lock: self.lock.acquire()
            self.mlx.getFrame(buf)
        except Exception:
            return self.last_frame
        finally:
            if self.lock: self.lock.release()
        m = np.array(buf, dtype=np.float32).reshape(self.shape)
        self.last_frame = m
        return m

    def colorized(self, scale=16):
        """يحوّل المصفوفة لصورة ملوّنة (colormap) مكبّرة."""
        import cv2
        m = self.read_matrix()
        if m is None:
            return None
        lo, hi = np.percentile(m, 2), np.percentile(m, 98)
        norm = np.clip((m - lo) / max(1e-3, hi - lo), 0, 1)
        img = (norm * 255).astype(np.uint8)
        img = cv2.resize(img, (m.shape[1]*scale, m.shape[0]*scale),
                         interpolation=cv2.INTER_CUBIC)
        img = cv2.applyColorMap(img, cv2.COLORMAP_INFERNO)
        # اكتب أعلى/أدنى حرارة
        cv2.putText(img, f"{hi:.1f}C", (5,20), cv2.FONT_HERSHEY_SIMPLEX, .6, (255,255,255), 1)
        cv2.putText(img, f"{lo:.1f}C", (5, img.shape[0]-8), cv2.FONT_HERSHEY_SIMPLEX, .6, (255,255,255), 1)
        return img

    def mjpeg(self):
        import cv2
        while True:
            img = self.colorized()
            if img is None:
                time.sleep(0.2); continue
            ok, jpg = cv2.imencode(".jpg", img)
            if ok:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + jpg.tobytes() + b"\r\n")
            time.sleep(1/8)
```

## Routes

```python
from flask import Response
from spider.sensors.camera import RGBCamera, ThermalCamera
from spider.hardware import i2c_lock

rgb_cam = RGBCamera()
thermal_cam = ThermalCamera(i2c_lock=i2c_lock)

@app.route("/video/rgb")
def video_rgb():
    return Response(rgb_cam.mjpeg(),
        mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/video/thermal")
def video_thermal():
    return Response(thermal_cam.mjpeg(),
        mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/api/thermal/frame")
def thermal_frame():
    m = thermal_cam.read_matrix()
    if m is None:
        return jsonify({"ok": False})
    return jsonify({"ok": True, "shape": list(m.shape),
                    "min": float(m.min()), "max": float(m.max()),
                    "data": m.round(1).tolist()})

@app.route("/api/cameras/status")
def cameras_status():
    return jsonify({"rgb": rgb_cam.backend is not None,
                    "thermal": thermal_cam.ready})
```

## واجهة العرض (تبويب «حساسات وخريطة»)

```html
<div class="card">
  <h2>📷 الكاميرات</h2>
  <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:center">
    <div>
      <div class="section-title">عادية (RGB)</div>
      <img id="rgbView" src="/video/rgb" style="width:320px;border-radius:10px;background:#000">
    </div>
    <div>
      <div class="section-title">حرارية</div>
      <img id="thView" src="/video/thermal" style="width:320px;border-radius:10px;background:#000">
    </div>
  </div>
  <div style="text-align:center;margin-top:8px">
    <label><input type="checkbox" id="overlay" onchange="toggleOverlay()"> دمج حراري فوق العادية</label>
    <span id="camStatus" style="font-size:11px;color:#666"></span>
  </div>
  <!-- وضع الدمج: الحرارية شفافة فوق العادية -->
  <div id="overlayWrap" style="display:none;position:relative;width:320px;margin:10px auto">
    <img src="/video/rgb" style="width:320px;border-radius:10px">
    <img src="/video/thermal" style="width:320px;border-radius:10px;position:absolute;
         top:0;left:0;opacity:0.45;mix-blend-mode:screen">
  </div>
</div>
<script>
async function camInit(){
  const s = await (await fetch('/api/cameras/status')).json();
  document.getElementById('camStatus').textContent =
    `RGB:${s.rgb?'🟢':'🔴'} حراري:${s.thermal?'🟢':'🔴'}`;
}
function toggleOverlay(){
  const on = document.getElementById('overlay').checked;
  document.getElementById('overlayWrap').style.display = on ? '' : 'none';
}
camInit();
</script>
```

---

## نقاط أداء/أمان

- MJPEG يستهلك CPU؛ على Pi استخدم دقة 640×480 وجودة 70 وحدّ 20fps للـ RGB، 8fps للحراري.
- لا تفتح أكثر من اتصال MJPEG واحد لكل كاميرا (الـ generator حالة مشتركة). للأكثر، أضف frame buffer مشترك مع threading.
- إطار الالتقاط الحراري يأخذ `i2c_lock` — قد يضيف زمناً بسيطاً للمشي؛ إن لزم، شغّل الحراري على ناقل I2C ثانٍ (`board.SCL/SDA` بديل) أو خفّض `refresh_rate`.
- صور الـ RGB ستُستخدم لاحقاً في تحليل الأعشاب الضارة (الخطة 09).

---

## معايير القبول
- ✅ بثّ RGB وحراري معاً في الواجهة.
- ✅ تلوين حراري مع قيم min/max.
- ✅ وضع دمج شفّاف.
- ✅ مؤشّر حالة لكل كاميرا (لا يتعطّل النظام لو غابت كاميرا).
- ✅ القراءة الحرارية لا تشوّش ناقل I2C للمحركات.
```
