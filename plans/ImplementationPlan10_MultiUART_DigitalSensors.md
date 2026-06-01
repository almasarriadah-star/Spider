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
| غاز (DO) | GPIO6 / p31 | — |
| رطوبة (DO) | GPIO16 / p36 | — |

---

## 3) التعديلات البرمجية

### 3.1 مصدر موحّد للإعدادات — `spider/config.py`
نضيف ثوابت المنافذ في مكان واحد ليسهل تعديلها (وتتطابق مع udev لو فُعّل):

```python
# ── منافذ الحساسات (راجع docs/HARDWARE_WIRING.md) ──
IMU_PORT      = "/dev/serial0"   # UART0 — BNO085
LIDAR_PORT    = "/dev/ttyAMA2"   # UART3
GPS_PORT      = "/dev/ttyAMA3"   # UART4
THERMAL_PORT  = "/dev/ttyAMA4"   # UART5
THERMAL_BAUD  = 115200           # ⚠️ عدّل حسب موديل الكاميرا

GAS_DO_GPIO       = 6            # BCM — خرج رقمي للغاز
HUMIDITY_DO_GPIO  = 16           # BCM — خرج رقمي للرطوبة
# منطق الخرج: True لو الحساس يعطي LOW عند الإنذار (فعّال-منخفض، شائع في MQ)
GAS_ACTIVE_LOW      = True
HUMIDITY_ACTIVE_LOW = True
```

### 3.2 الليدار — `spider/sensors/lidar.py`
تغيير المنفذ الافتراضي للنسخة العامة فقط:

```python
from spider.config import LIDAR_PORT
lidar = Lidar(port=LIDAR_PORT, baud=115200, kind="tfmini", simulate=False)
```

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

### 3.6 وحدة جديدة — حساسات رقمية `spider/sensors/digital.py`
قراءة DO عبر `RPi.GPIO` مع محاكاة على ويندوز.

```python
# spider/sensors/digital.py
"""حساسات رقمية DO (غاز/رطوبة) عبر GPIO + محاكاة."""
import random
from spider.config import (GAS_DO_GPIO, HUMIDITY_DO_GPIO,
                           GAS_ACTIVE_LOW, HUMIDITY_ACTIVE_LOW)

class DigitalSensor:
    def __init__(self, gpio, active_low=True, simulate=False):
        self.gpio = gpio
        self.active_low = active_low
        self.simulate = simulate
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
        """True عند تجاوز العتبة (إنذار)."""
        if self.simulate:
            return random.random() < 0.1
        raw = self._gpio.input(self.gpio)   # 0/1
        return (raw == 0) if self.active_low else (raw == 1)

gas      = DigitalSensor(GAS_DO_GPIO, GAS_ACTIVE_LOW, simulate=False)
humidity = DigitalSensor(HUMIDITY_DO_GPIO, HUMIDITY_ACTIVE_LOW, simulate=False)
```

> ⚠️ تذكير: حساس الغاز MQ بخرج 5V يحتاج **مقسّم جهد** على DO قبل GPIO6 (الحد 3.3V).

### 3.7 مسارات الـ API — `web_controller.py`
```python
# كاميرا حرارية
from spider.sensors.thermal import thermal as _thermal
_thermal.start()

@app.route("/api/thermal")
def thermal_read():
    return jsonify(_thermal.get())

# حساسات رقمية
from spider.sensors.digital import gas as _gas, humidity as _humidity

@app.route("/api/digital")
def digital_read():
    return jsonify({
        "gas_alarm": _gas.alarm(),
        "humidity_alarm": _humidity.alarm(),
        "gas_sim": _gas.simulate,
        "humidity_sim": _humidity.simulate,
    })
```

### 3.8 الواجهة — `templates/dashboard.html`
- بطاقة كاميرا حرارية: شبكة 8×8 ملوّنة (heatmap) + min/max/avg، تستهلك `/api/thermal`.
- مؤشّرات إنذار غاز/رطوبة (لمبة حمراء/خضراء) من `/api/digital` كل ~1s.

---

## 4) خطوات النظام (على الراسبيري)

راجع قسم «تفعيل UART الإضافية» في `docs/HARDWARE_WIRING.md`:
1. أضف `dtoverlay=uart3/uart4/uart5` و `enable_uart=1` في `config.txt`.
2. حرّر منفذ الكونسول (raspi-config) وأعد التشغيل.
3. `ls -l /dev/ttyAMA*` للتأكّد، وعدّل الثوابت في `config.py` لو اختلفت الأسماء (أو فعّل udev).
4. `pip install pyserial RPi.GPIO`.

---

## 5) معايير القبول
- ✅ كل جهاز UART على منفذه دون تصادم (IMU/Lidar/GPS/Thermal).
- ✅ قراءة الكاميرا الحرارية (إطار + min/max) وعرضها.
- ✅ إنذار غاز/رطوبة رقمي يظهر في الواجهة.
- ✅ كل شيء يعمل بالمحاكاة على ويندوز (رجوع تلقائي).
- ✅ لا تعارض مع ناقل I2C للسيرفوات.

---

## 6) مخاطر ومحاذير
- **أسماء ttyAMAx غير مضمونة الترتيب** → استخدم udev للأسماء الثابتة.
- **منطق 5V على DO** يُتلف GPIO → مقسّم جهد/مبدّل مستوى إلزامي للغاز.
- **بروتوكول الكاميرا الحرارية مجهول** → `_real_loop` فيها `TODO` يُكمَّل بعد الـ datasheet.
- **الأرضي المشترك** بين كل المصادر شرط لعمل UART.
```
