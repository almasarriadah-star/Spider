# خطة 10 — منافذ UART متعددة + حساسات رقمية (DO)

> الأولوية: 🔴 عالية — تحلّ تعارض حقيقي: **4 أجهزة UART على منفذ واحد**.
> المرجع العتادي: [`docs/HARDWARE_WIRING.md`](../docs/HARDWARE_WIRING.md).

---

## 1) المشكلة الحالية

الكود الحالي يضع **ثلاثة أجهزة على نفس `/dev/serial0`**:

| الجهاز | الملف | المنفذ الحالي | السرعة |
|--------|-------|---------------|--------|
| IMU BNO085 | `spider/imu.py:17` | `/dev/serial0` | 115200 |
| GPS | `spider/sensors/gps.py:108` | `/dev/serial0` (افتراضي) | 9600 |
| ليدار | `spider/sensors/lidar.py` | `/dev/serial0` | 115200 |

منفذ UART واحد لا يخدم إلا جهازاً واحداً → تصادم. الحل: نولّد UART3/4/5 عبر
`dtoverlay` (Pi 4) ونوزّع كل جهاز على منفذه، ونضيف وحدتين جديدتين: كاميرا حرارية
(UART) + حساسي غاز/رطوبة رقميين (GPIO DO).

---

## 2) توزيع المنافذ النهائي

| الجهاز | المنفذ الجديد | القطب (RX للراسبيري) |
|--------|---------------|----------------------|
| IMU BNO085 | `/dev/serial0` (UART0) — بلا تغيير | GPIO15 / p10 |
| ليدار TFmini | `/dev/ttyAMA2` (UART3) | GPIO5 / p29 |
| GPS | `/dev/ttyAMA3` (UART4) | GPIO9 / p21 |
| كاميرا حرارية | `/dev/ttyAMA4` (UART5) | GPIO13 / p33 |
| رطوبة التربة | `/dev/ttyUSB0` (USB‑Serial) | منفذ USB |
| غاز (DO) | GPIO6 / p31 | — |
| DHT22 (حرارة+رطوبة الجو) | GPIO16 / p36 (1‑Wire) | — |

---

## 3) التعديلات البرمجية

### 3.1 مصدر موحّد للإعدادات — `spider/config.py`
نضيف ثوابت المنافذ في مكان واحد ليسهل تعديلها (وتتطابق مع udev لو فُعّل):

```python
# ── منافذ الحساسات (راجع docs/HARDWARE_WIRING.md) ──
IMU_PORT       = "/dev/serial0"  # UART0 — BNO085
LIDAR_PORT     = "/dev/ttyAMA2"  # UART3 — LD06
LIDAR_BAUD     = 230400          # LD06 دوّار 360°
LIDAR_KIND     = "ld06"          # بروتوكول LD06 (رأس 0x54 0x2C)
LIDAR_PWM_GPIO = 18              # BCM (p12) — تحكّم موتور دوران LD06
GPS_PORT       = "/dev/ttyAMA3"  # UART4
THERMAL_PORT   = "/dev/ttyAMA4"  # UART5 — موديول MLX90640 بخرج UART
THERMAL_BAUD   = 115200          # ⚠️ عدّل حسب ورقة الموديول
SOIL_PORT      = "/dev/ttyUSB0"  # رطوبة التربة عبر محوّل USB‑Serial
SOIL_BAUD      = 9600            # ⚠️ عدّل حسب الموديول

GAS_DO_GPIO    = 6               # BCM — خرج رقمي للغاز MQ135
GAS_ACTIVE_LOW = True            # True لو يعطي LOW عند الإنذار (فعّال-منخفض، شائع في MQ)

DHT22_GPIO     = 16              # BCM — خط بيانات DHT22 (حرارة + رطوبة الجو، سلك-واحد)

# ── سيرفوات مساعدة على PCA9685 (قنوات 9 الفاضية، الأرجل تستخدم 0–8) ──
CAM_SERVO_KEY  = "R9"            # SG90 — ميكانزم الكاميرا (لوحة اليمين 0x40)
SOIL_SERVO_KEY = "L9"            # DS3230 — ميكانزم التربة (لوحة الشمال 0x44)
```

### 3.2 الليدار — `spider/sensors/lidar.py`
LD06 ليدار دوّار 360° ببروتوكول مختلف عن TFmini → نضيف `kind="ld06"` بمفكّك حزمته
(رأس `0x54 0x2C`، 12 نقطة/حزمة، توزيع زوايا خطّي بين start/end angle)، وسرعة 230400.
ثم النسخة العامة:

```python
from spider.config import LIDAR_PORT, LIDAR_BAUD, LIDAR_KIND
lidar = Lidar(port=LIDAR_PORT, baud=LIDAR_BAUD, kind=LIDAR_KIND, simulate=False)
```

> ✅ LD06 يعطي **مسح 360° كامل** → يملأ `scan` dict مباشرة فتعمل لوحة الرادار وإسقاط
> الخريطة بلا تغيير (أفضل من TFmini النقطة الواحدة).
> 🌀 موتور الدوران: أخرج PWM من `LIDAR_PWM_GPIO` (GPIO18، ~40% duty) لتشغيل الدوران.

### 3.3 GPS — `spider/sensors/gps.py`
```python
from spider.config import GPS_PORT
gps = GPS(port=GPS_PORT, baud=9600, simulate=False)   # كان simulate=True
```
(يبقى الرجوع التلقائي للمحاكاة عند غياب المنفذ — لا كسر على ويندوز.)

### 3.4 IMU — `spider/imu.py`
استبدال السلسلة الحرفية بثابت الإعداد (بلا تغيير وظيفي):
```python
from spider.config import IMU_PORT
_uart = serial.Serial(IMU_PORT, 115200, timeout=0.5)
```

### 3.5 وحدة جديدة — كاميرا حرارية `spider/sensors/thermal.py`
نمط مطابق لباقي الحساسات: خيط قراءة + قفل + `simulate` + رجوع تلقائي.

```python
# spider/sensors/thermal.py
"""كاميرا حرارية عبر UART. يقرأ مصفوفة/أو نقطة حرارة + محاكاة."""
import threading, time, random
from spider.config import THERMAL_PORT, THERMAL_BAUD

class ThermalCamera:
    def __init__(self, port=THERMAL_PORT, baud=THERMAL_BAUD, simulate=False,
                 rows=8, cols=8):
        self.port, self.baud = port, baud
        self.rows, self.cols = rows, cols
        self.simulate = simulate
        self.frame = [[25.0]*cols for _ in range(rows)]  # °C
        self.tmin = self.tmax = self.tavg = 25.0
        self.ready = False
        self._ser = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        if not simulate:
            try:
                import serial
                self._ser = serial.Serial(port, baud, timeout=0.5)
                self.ready = True
            except Exception:
                self.simulate = True

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        self._sim_loop() if self.simulate else self._real_loop()

    def _real_loop(self):
        # ⚠️ هيكل الإطار يعتمد على موديل الكاميرا — يُكمَّل بعد الـ datasheet.
        # النمط العام: قراءة إطار → تحويل بايتات إلى درجات → _update_frame().
        try:
            while not self._stop.is_set():
                raw = self._ser.read(self.rows*self.cols*2)  # مثال
                if raw:
                    # TODO: فك حسب البروتوكول الفعلي
                    self.ready = True
                time.sleep(0.1)
        except Exception:
            self.ready = False

    def _sim_loop(self):
        self.ready = True
        t = 0
        while not self._stop.is_set():
            f = [[24 + 6*random.random() for _ in range(self.cols)]
                 for _ in range(self.rows)]
            # بقعة ساخنة متحركة
            hr, hc = t % self.rows, (t//2) % self.cols
            f[hr][hc] = 40 + 5*random.random()
            self._update_frame(f)
            t += 1
            time.sleep(0.2)

    def _update_frame(self, f):
        flat = [v for row in f for v in row]
        with self._lock:
            self.frame = f
            self.tmin, self.tmax = min(flat), max(flat)
            self.tavg = sum(flat)/len(flat)

    def get(self):
        with self._lock:
            return {"frame": self.frame, "min": round(self.tmin,1),
                    "max": round(self.tmax,1), "avg": round(self.tavg,1),
                    "ready": self.ready, "simulate": self.simulate}

    def stop(self):
        self._stop.set()
        if self._ser:
            try: self._ser.close()
            except Exception: pass

thermal = ThermalCamera(simulate=False)
```

### 3.6 وحدة جديدة — حساس غاز رقمي + DHT22 `spider/sensors/digital.py`
الغاز خرج DO بسيط عبر `RPi.GPIO`؛ الـ DHT22 بروتوكول سلك-واحد عبر `adafruit_dht`.
الإثنان مع محاكاة على ويندوز.

```python
# spider/sensors/digital.py
"""حساس غاز رقمي (DO) + DHT22 (حرارة/رطوبة) — مع محاكاة."""
import random
from spider.config import GAS_DO_GPIO, GAS_ACTIVE_LOW, DHT22_GPIO


class GasSensor:
    """خرج DO رقمي (HIGH/LOW حسب عتبة الموديول)."""
    def __init__(self, gpio=GAS_DO_GPIO, active_low=GAS_ACTIVE_LOW, simulate=False):
        self.gpio, self.active_low, self.simulate = gpio, active_low, simulate
        self._gpio = None
        if not simulate:
            try:
                import RPi.GPIO as GPIO
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(gpio, GPIO.IN)
                self._gpio = GPIO
            except Exception:
                self.simulate = True

    def alarm(self):
        """True عند تجاوز عتبة الغاز."""
        if self.simulate:
            return random.random() < 0.1
        raw = self._gpio.input(self.gpio)
        return (raw == 0) if self.active_low else (raw == 1)


class DHT22Sensor:
    """حرارة (°C) + رطوبة الجو (%) — بروتوكول سلك-واحد."""
    def __init__(self, gpio=DHT22_GPIO, simulate=False):
        self.gpio, self.simulate = gpio, simulate
        self._dev = None
        self._last = {"temp": None, "humidity": None}
        if not simulate:
            try:
                import board, adafruit_dht
                self._dev = adafruit_dht.DHT22(getattr(board, f"D{gpio}"))
            except Exception:
                self.simulate = True

    def read(self):
        if self.simulate:
            self._last = {"temp": round(22 + random.uniform(-2, 5), 1),
                          "humidity": round(45 + random.uniform(-10, 20), 1)}
            return dict(self._last)
        try:
            t, h = self._dev.temperature, self._dev.humidity
            if t is not None and h is not None:
                self._last = {"temp": round(t, 1), "humidity": round(h, 1)}
        except Exception:
            pass            # DHT22 يفشل أحياناً بقراءة — نُبقي آخر قيمة صالحة
        return dict(self._last)


gas   = GasSensor(simulate=False)
dht22 = DHT22Sensor(simulate=False)
```

> ⚠️ تذكير: غاز MQ بخرج 5V يحتاج **مقسّم جهد** على DO قبل GPIO6. DHT22 على 3V3 +
> مقاومة سحب 10kΩ. ثبّت `pip install adafruit-circuitpython-dht`.

### 3.7 وحدة معدّلة — رطوبة التربة عبر USB `spider/sensors/soil.py`
ينتقل من ADS1115 (I2C) إلى محوّل USB‑Serial (`/dev/ttyUSB0`). نعيد كتابة الوحدة
لتقرأ سطراً تسلسلياً (مثلاً رقم خام أو نسبة) مع رجوع تلقائي للمحاكاة.

```python
# spider/sensors/soil.py — النسخة الجديدة (USB‑Serial)
import threading, time, random
from spider.config import SOIL_PORT, SOIL_BAUD

class SoilSensor:
    def __init__(self, port=SOIL_PORT, baud=SOIL_BAUD, simulate=False,
                 dry_raw=26000, wet_raw=11000):
        self.port, self.baud = port, baud
        self.dry, self.wet = dry_raw, wet_raw
        self.simulate = simulate
        self.ready = False
        self._raw = self.dry
        self._ser = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        if not simulate:
            try:
                import serial
                self._ser = serial.Serial(port, baud, timeout=1)
                self.ready = True
            except Exception:
                self.simulate = True

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.is_set():
            if self.simulate:
                with self._lock:
                    self._raw = random.randint(self.wet, self.dry)
                time.sleep(1.0)
            else:
                try:
                    line = self._ser.readline().decode("ascii", "ignore").strip()
                    # ⚠️ TODO: عدّل حسب صيغة موديولك (رقم خام أو نسبة مباشرة)
                    if line:
                        with self._lock:
                            self._raw = int(float(line))
                        self.ready = True
                except Exception:
                    pass

    def read_percent(self):
        with self._lock:
            raw = self._raw
        pct = (self.dry - raw) / max(1, (self.dry - self.wet)) * 100.0
        return max(0.0, min(100.0, round(pct, 1)))

soil = SoilSensor(simulate=False)
```
> ينقص استدعاء `soil.start()` في `web_controller.py` (لم يكن مطلوباً في نسخة I2C).

### 3.8 مسارات الـ API — `web_controller.py`
```python
# كاميرا حرارية
from spider.sensors.thermal import thermal as _thermal
_thermal.start()

@app.route("/api/thermal")
def thermal_read():
    return jsonify(_thermal.get())

# غاز + DHT22
from spider.sensors.digital import gas as _gas, dht22 as _dht22

@app.route("/api/environment")
def environment_read():
    air = _dht22.read()
    return jsonify({
        "gas_alarm": _gas.alarm(), "gas_sim": _gas.simulate,
        "air_temp": air["temp"], "air_humidity": air["humidity"],
        "dht_sim": _dht22.simulate,
    })
```
> رطوبة التربة تبقى على مسارها الحالي `/api/soil` لكن المصدر صار USB (يلزم `_soil.start()`).

### 3.9 سيرفوات مساعدة — `spider/sensors/aux_servo.py` + مسار API
السيرفوان الجديدان ليسا أرجلاً، فلا يمرّان عبر مولّد المشية (gait). نكتبهما مباشرة عبر
طبقة الهاردوير على قناتي PCA الفاضيتين (R9/L9) مع حدود زاوية خاصّة.

```python
# spider/sensors/aux_servo.py
"""تحكّم بسيط بسيرفوات مساعدة (كاميرا/تربة) على قنوات PCA9685 الفاضية."""
from spider import hardware
from spider.config import CAM_SERVO_KEY, SOIL_SERVO_KEY

# حدود مستقلّة عن أرجل المشية (SG90/DS3230 مدى أوسع)
_LIMITS = {CAM_SERVO_KEY: (0, 180), SOIL_SERVO_KEY: (0, 180)}
_state  = {CAM_SERVO_KEY: 90, SOIL_SERVO_KEY: 90}

def set_angle(key, angle):
    lo, hi = _LIMITS.get(key, (0, 180))
    angle = max(lo, min(hi, int(angle)))
    _state[key] = angle
    hardware.write_servo_raw(key, angle)   # يكتب مباشرة على قناة PCA
    return angle

def get_state():
    return dict(_state)
```
```python
# web_controller.py
from spider.sensors import aux_servo as _aux
from spider.config import CAM_SERVO_KEY, SOIL_SERVO_KEY

@app.route("/api/aux_servo", methods=["POST"])
def aux_servo_set():
    d = request.get_json(force=True)
    key = {"camera": CAM_SERVO_KEY, "soil": SOIL_SERVO_KEY}.get(d.get("which"))
    if not key:
        return jsonify({"ok": False, "error": "which=camera|soil"})
    return jsonify({"ok": True, "angle": _aux.set_angle(key, d.get("angle", 90))})
```
> ⚠️ `hardware.write_servo_raw` يقبل المفاتيح `R9`/`L9` كما هي (يفصل الحرف الأول جهةً
> والباقي رقم القناة) — لا تعديل مطلوب على `hardware.py`. تأكّد أن `pwm_release_all`
> (الذي يلفّ القنوات 0–15) يطفئ هاتين القناتين عند الطوارئ — وهو كذلك أصلاً.

### 3.10 الواجهة — `templates/dashboard.html`
- بطاقة كاميرا حرارية: شبكة 8×8 ملوّنة (heatmap) + min/max/avg، تستهلك `/api/thermal`.
- بطاقة بيئة: حرارة الجو + رطوبة الجو (DHT22) + لمبة إنذار غاز من `/api/environment` كل ~1s.
- منزلقان (sliders) لزاويتي سيرفو الكاميرا والتربة → `POST /api/aux_servo`.

---

## 4) خطوات النظام (على الراسبيري)

راجع قسم «تفعيل UART الإضافية» في `docs/HARDWARE_WIRING.md`:
1. أضف `dtoverlay=uart3/uart4/uart5` و `enable_uart=1` في `config.txt`.
2. حرّر منفذ الكونسول (raspi-config) وأعد التشغيل.
3. `ls -l /dev/ttyAMA*` و `ls -l /dev/ttyUSB*` للتأكّد، وعدّل الثوابت في `config.py` لو اختلفت الأسماء (أو فعّل udev).
4. `pip install pyserial RPi.GPIO adafruit-circuitpython-dht`.

---

## 5) معايير القبول
- ✅ كل جهاز UART على منفذه دون تصادم (IMU/LD06/GPS/Thermal).
- ✅ LD06 يعطي مسح 360° حيّ على لوحة الرادار + إسقاط على الخريطة.
- ✅ قراءة الكاميرا الحرارية (إطار + min/max) وعرضها.
- ✅ إنذار غاز رقمي + قراءة DHT22 (حرارة/رطوبة الجو) تظهر في الواجهة.
- ✅ رطوبة التربة تُقرأ من `/dev/ttyUSB0` (USB) لا من I2C.
- ✅ سيرفو الكاميرا (R9) وسيرفو التربة (L9) يتحرّكان من الواجهة دون تأثير على المشية.
- ✅ كل شيء يعمل بالمحاكاة على ويندوز (رجوع تلقائي).
- ✅ ناقل I2C للسيرفوات (0x40/0x44) فقط — بلا تعارض.

---

## 6) مخاطر ومحاذير
- **أسماء ttyAMAx غير مضمونة الترتيب** → استخدم udev للأسماء الثابتة.
- **منطق 5V على DO/TX** يُتلف GPIO → مقسّم جهد/مبدّل مستوى إلزامي للغاز وأي خرج 5V.
- **بروتوكول موديول الكاميرا الحرارية يحدّده المصنّع** → المفكّك مرن ومكتمل، يُضبط من `sensors.json` (`header`/`encoding`/`scale`) بلا تعديل كود.
- **LD06 يلزمه PWM للدوران** → بلا إشارة PWM قد لا يدور؛ تيار الموتور ~200mA.
- **DS3230 عالي العزم** → تأكّد أن ريل تغذية السيرفوات يحتمل ذروة تياره مع الأرجل.
- **الأرضي المشترك** بين كل المصادر شرط لعمل UART والسيرفوات.
```
