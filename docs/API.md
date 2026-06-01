# 🌐 واجهة API للروبوت العنكبوتي — للقراءة الخارجية

> مرجع كامل للأجهزة/الخدمات الخارجية (مثل جهاز تحليل الذكاء الصناعي) لسحب قراءات
> الروبوت. كل المسارات تحت `/api/v1/` للقراءة (GET) إلا ما ذُكر. لا مصادقة حالياً
> (شبكة محليّة) — راجع [الأمان](#الأمان).

- **Base URL:** `http://<robot-ip>:5000`
- **الصيغة:** JSON (UTF‑8)، وللصور `image/jpeg`.
- **النسخة:** `1.0` (تظهر في `api_version`).
- الإعدادات القابلة للتعديل (المنافذ/السرعات): `config/sensors.json` — انظر [الإعدادات](#الإعدادات).

---

## جدول سريع

| الطريقة | المسار | يُرجع |
|---------|--------|-------|
| GET | `/api/v1/health` | حالة المصادر والاتصال |
| GET | `/api/v1/readings` | **كل القراءات دفعة واحدة** (الأنسب للسحب الدوري) |
| GET | `/api/v1/imu` | اتجاه/ميلان BNO085 |
| GET | `/api/v1/gps` | الموقع/السرعة/الاتجاه |
| GET | `/api/v1/lidar` | مسح LD06 360° + أقرب عائق |
| GET | `/api/v1/soil` | رطوبة التربة % |
| GET | `/api/v1/environment` | غاز MQ135 + حرارة/رطوبة الجو (DHT22) |
| GET | `/api/v1/thermal?full=1` | إحصاء حراري (والمصفوفة 24×32 عند full) |
| GET | `/api/v1/servos` | زوايا الأرجل + السيرفوات المساعدة |
| GET | `/api/v1/camera/rgb.jpg?q=80` | **لقطة JPEG مفردة** من الكاميرا العادية |
| GET | `/api/v1/camera/thermal.jpg?q=85` | لقطة JPEG ملوّنة من الكاميرا الحرارية |
| GET/POST | `/api/v1/aux_servo` | حالة/ضبط سيرفو الكاميرا أو التربة |
| GET | `/api/v1/config` | الإعدادات الفعّالة (sensors.json) |

> بثّ فيديو مستمر (MJPEG، ليس صورة مفردة): `GET /video/rgb` و `GET /video/thermal`.

---

## التفاصيل

### `GET /api/v1/health`
```json
{
  "ok": true, "api_version": "1.0", "ts": 1748736000.12, "hardware": false,
  "sources": {
    "imu": true, "gps_sim": false, "lidar_sim": false, "soil_sim": false,
    "gas_sim": false, "dht_sim": false, "rgb_cam": true, "thermal_mode": "uart"
  }
}
```
حقول `*_sim` / `*_mode`: تخبر إن كان المصدر عتاداً حقيقياً أم محاكاة (مفيد للتحليل).

### `GET /api/v1/readings`
لقطة مجمّعة. مفاتيحها: `imu, gps, lidar, soil, environment, thermal, servos` + `ts`.
استخدمها للسحب الدوري (مثلاً كل ثانية) بدل ضرب كل مسار على حدة.

### `GET /api/v1/imu`
```json
{ "ready": true, "roll": 0.4, "pitch": -1.2, "yaw": 137.8,
  "ax": 0.01, "ay": -0.02, "az": 0.98 }
```
الزوايا بالدرجات، التسارع بوحدات g. `ready=false` إن لم يتصل الـ IMU.

### `GET /api/v1/gps`
```json
{ "lat": 31.9539, "lon": 35.9106, "fix": true, "sats": 9,
  "speed": 0.0, "course": 0.0, "ts": 1748736000.0, "simulate": false }
```
`speed` م/ث، `course`/heading بالدرجات. عند `fix=false` الإحداثيات قد تكون آخر/افتراضية.

### `GET /api/v1/lidar`
```json
{ "ready": true, "simulate": false, "kind": "ld06", "points": 358,
  "nearest_mm": 412, "scan": { "0": 1024, "1": 1030, "...": 0 } }
```
`scan`: قاموس `زاوية(0–359)": مسافة_مم`. `nearest_mm` أقرب عائق. 0° = أمام الروبوت.

### `GET /api/v1/soil`
```json
{ "moisture_pct": 62.5, "raw": 18450, "ready": true, "simulate": false }
```

### `GET /api/v1/environment`
```json
{ "gas_alarm": false, "gas_simulate": false,
  "air_temp_c": 24.6, "air_humidity_pct": 51.2, "dht_simulate": false }
```
`gas_alarm` منطقي (تجاوز عتبة MQ135). `air_*` من DHT22 (قد تكون `null` قبل أول قراءة ناجحة).

### `GET /api/v1/thermal`  ·  `?full=1`
```json
{ "ready": true, "mode": "uart", "shape": [24,32],
  "min_c": 21.8, "max_c": 33.4, "avg_c": 24.9 }
```
مع `?full=1` يُضاف `"matrix": [[...],[...]]` (24 صفّاً × 32 عمود، °C). للصورة الملوّنة
استخدم `/api/v1/camera/thermal.jpg`.

> **فك إطار موديول UART:** المفكّك مرن وقابل للضبط من `config/sensors.json → thermal`:
> `header` (بايتات مزامنة hex)، `encoding` (`u16` | `u16be` | `f32`)، `scale` (للقسمة
> على القيمة الصحيحة لإخراج °C مع u16). طابقها مع داتاشيت موديولك.

### `GET /api/v1/servos`
```json
{ "legs": { "R0": 90, "R1": 67, "...": 0 },
  "aux": { "camera": 45, "soil": 90 } }
```

### `GET /api/v1/camera/rgb.jpg`  ·  `GET /api/v1/camera/thermal.jpg`
يُرجعان **صورة JPEG مفردة** (`Content-Type: image/jpeg`). معامل اختياري `q` = جودة 1–100.
عند غياب الكاميرا: `503` + `{"ok": false, "error": "..."}`.

### `GET/POST /api/v1/aux_servo`
- `GET` → `{ "ok": true, "state": { "camera": 45, "soil": 90 } }`
- `POST` (JSON) → `{ "which": "camera"|"soil", "angle": 0..180 }`
  ```json
  { "ok": true, "angle": 45, "state": { "camera": 45, "soil": 90 } }
  ```

### `GET /api/v1/config`
يُرجع `{ "ok": true, "sensors": { ... } }` — محتوى `config/sensors.json` الفعّال.

---

## أمثلة الاستخدام (جهاز التحليل)

### cURL
```bash
curl http://192.168.1.50:5000/api/v1/readings
curl "http://192.168.1.50:5000/api/v1/thermal?full=1"
curl http://192.168.1.50:5000/api/v1/camera/rgb.jpg     --output rgb.jpg
curl http://192.168.1.50:5000/api/v1/camera/thermal.jpg --output thermal.jpg
curl -X POST http://192.168.1.50:5000/api/v1/aux_servo \
     -H "Content-Type: application/json" -d '{"which":"camera","angle":120}'
```

### Python (للسحب الدوري والتحليل)
```python
import requests, time

ROBOT = "http://192.168.1.50:5000"

def poll():
    r = requests.get(f"{ROBOT}/api/v1/readings", timeout=2).json()
    print("yaw=", r["imu"].get("yaw"),
          "nearest_mm=", r["lidar"]["nearest_mm"],
          "soil%=", r["soil"]["moisture_pct"],
          "gas=", r["environment"]["gas_alarm"])

def grab_rgb(path="frame.jpg"):
    img = requests.get(f"{ROBOT}/api/v1/camera/rgb.jpg", timeout=3)
    if img.headers.get("Content-Type") == "image/jpeg":
        open(path, "wb").write(img.content)

while True:
    poll(); grab_rgb(); time.sleep(1.0)
```

---

## الإعدادات

عدّل `config/sensors.json` (بلا لمس الكود) ثم أعد تشغيل السيرفر:
```json
{
  "lidar": { "port": "/dev/ttyAMA2", "baud": 230400, "kind": "ld06", "pwm_gpio": 18 },
  "gps":   { "port": "/dev/ttyAMA3", "baud": 9600 },
  "thermal": { "port": "/dev/ttyAMA4", "baud": 115200,
               "rows": 24, "cols": 32, "header": "5A5A", "encoding": "u16", "scale": 100.0 },
  "soil":  { "port": "/dev/ttyUSB0", "baud": 9600, "dry_raw": 26000, "wet_raw": 11000 },
  "gas":   { "gpio": 6, "active_low": true },
  "dht22": { "gpio": 16 }
}
```
- أي مفتاح غير مذكور يأخذ الافتراضي تلقائياً (دمج جزئي).
- تغيير المنافذ يتطلب إعادة تشغيل (المنفذ يُفتح عند الإقلاع). القيم غير المرتبطة بمنفذ
  مفتوح (مثل `dry_raw`/`active_low`) يمكن إعادة تحميلها حياً عبر `config.reload_sensors()`.
- معاينة الإعدادات الجارية: `GET /api/v1/config`.

---

## الأمان

- لا مصادقة افتراضياً — شغّله على شبكة موثوقة فقط، أو ضع بروكسي (Nginx) بـ Basic‑Auth/توكن.
- المسارات تحت `/api/v1/` للقراءة فقط عدا `POST /api/v1/aux_servo` (حركة فيزيائية بسيطة).
- مسارات التحكّم بالحركة/المشية خارج `/api/v1/` (راجع كود `web_controller.py`).

---

## ملاحظات للتحليل بالذكاء الصناعي
- استخدم `*_sim`/`*_mode` في `/health` لتمييز البيانات الحقيقية عن المحاكاة قبل التدريب.
- `thermal` يعطي مصفوفة خام (°C) مع `?full=1` — أفضل للتحليل من الصورة الملوّنة.
- `lidar.scan` خام بالمليمتر لكل درجة — مناسب لبناء خريطة عوائق.
- وحّد التوقيت عبر حقل `ts` (epoch ثوانٍ) المرافق لكل لقطة.
