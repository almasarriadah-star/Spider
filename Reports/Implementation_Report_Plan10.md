# تقرير تنفيذ — خطة 10 + API خارجي (للـ Agent على السيرفر)

> هذا التقرير موجّه لوكيل (Agent) يصلح المشروع على الراسبيري عند حدوث مشاكل.
> يشرح **ما الذي تغيّر، أين، وما الذي يمكن تعديله بأمان**. المرجع العتادي:
> [`docs/HARDWARE_WIRING.md`](../docs/HARDWARE_WIRING.md)، وواجهة القراءة:
> [`docs/API.md`](../docs/API.md).

---

## 1) الخلاصة التنفيذية

أُضيف/عُدِّل دعم حساسات وعتاد جديد على Raspberry Pi 4 مع حلّ تعارض «4 أجهزة UART
على منفذ واحد»، وتوحيد الإعدادات في ملف JSON، وبناء API خارجي للقراءة.

**العتاد المدعوم الآن:** IMU BNO085 (UART0)، ليدار **LD06** دوّار 360° (UART3 @230400)،
GPS (UART4)، كاميرا حرارية **MLX90640 بخرج UART** (UART5)، رطوبة تربة عبر **USB**،
غاز **MQ135** (DO رقمي GPIO6)، **DHT22** (GPIO16)، وسيرفوان مساعدان على PCA9685
(كاميرا `R9`، تربة `L9`).

---

## 2) أين تُعدّل الإعدادات (الأهم لك)

كل المتغيّرات (منافذ، سرعات، أقطاب، معايرة) في **`config/sensors.json`** — عدّلها بلا
لمس الكود ثم أعد تشغيل السيرفر. الكود يقرأها عبر `spider/config.py` (دمج جزئي فوق
الافتراضيات في `SENSORS_DEFAULT`). معاينة الفعّال: `GET /api/v1/config`.

| العَرَض | المفتاح في sensors.json |
|---------|--------------------------|
| الليدار لا يُقرأ / بايتات مشوّشة | `lidar.port` أو `lidar.baud` (LD06 = 230400) |
| GPS صامت | `gps.port` / `gps.baud` (عادة 9600) |
| الكاميرا الحرارية فارغة | `thermal.port` / `thermal.baud` (حسب الموديول) |
| رطوبة التربة ثابتة | `soil.port` (ttyUSB0/1) ومعايرة `soil.dry_raw`/`wet_raw` |
| إنذار الغاز معكوس | `gas.active_low` (true/false) |
| المنفذ خطأ بعد reboot | راجع `ls /dev/ttyAMA*` وعدّل المنافذ (انظر §6) |

> تغيير منفذ يتطلب إعادة تشغيل (يُفتح عند الإقلاع). قيم المعايرة/المنطق يمكن إعادة
> تحميلها حياً: `python -c "from spider import config; config.reload_sensors()"` لكن
> السيرفر يقرأها مرة واحدة — الأضمن إعادة التشغيل.

---

## 3) الملفات المعدّلة/الجديدة

| الملف | الحالة | الدور |
|-------|--------|------|
| `config/sensors.json` | 🆕 | مصدر الإعدادات القابل للتعديل |
| `spider/config.py` | عُدّل | تحميل JSON + تصدير ثوابت + `reload_sensors()` |
| `spider/sensors/lidar.py` | عُدّل | أوضاع `ld06`/`tfmini`/`rplidar`/محاكاة + PWM موتور |
| `spider/sensors/gps.py` | عُدّل | منفذ من config، عتاد حقيقي + رجوع محاكاة |
| `spider/imu.py` | عُدّل | منفذ من config |
| `spider/sensors/soil.py` | أُعيد كتابته | قارئ USB‑Serial (كان ADS1115/I2C) |
| `spider/sensors/digital.py` | 🆕 | `GasSensor` (DO) + `DHT22Sensor` |
| `spider/sensors/aux_servo.py` | 🆕 | سيرفو الكاميرا/التربة على PCA |
| `spider/sensors/camera.py` | عُدّل | `ThermalCamera` يدعم UART + I2C + محاكاة |
| `web_controller.py` | عُدّل | مسارات `/api/environment`، `/api/aux_servo`، و`/api/v1/*` |
| `templates/dashboard.html` | عُدّل | رادار 360°، بطاقة بيئة، منزلقات السيرفو |

---

## 4) مبدأ التصميم: «رجوع تلقائي للمحاكاة»

كل وحدة تحاول فتح العتاد، وإن فشلت تضبط `simulate=True` (أو `ready=False`) وتولّد بيانات
وهمية. **لذلك الكود لا ينكسر على ويندوز/بلا عتاد.** للتحقق من مصدر حقيقي مقابل محاكاة
استخدم حقول `*_sim` / `*_mode` في `GET /api/v1/health`.

> ⚠️ إن أردت تشخيص «لماذا يعمل بالمحاكاة رغم توصيل العتاد؟» فالسبب غالباً: المنفذ خطأ،
> صلاحيات (`dialout`)، الكونسول يحتلّ UART0، أو مكتبة ناقصة. انظر §6.

---

## 5) نقاط تحتاج ضبطاً (إعدادات لا كوداً)

1. **الكاميرا الحرارية (`camera.py → _uart_loop`)** — ✅ مكتمل ومضبوط لموديول
   **GY‑MCU90640 (HY‑18)** المؤكَّد: إطار **1544 بايت**، رأس `5A5A0206`، 768×**int16** LE
   (°C×100) عند الإزاحة 4، حرارة محيطة عند 1540، @**115200**. يرسل الكود أوامر بدء البث
   تلقائياً (`A5 25 01 CB` = 4Hz، `A5 35 02 DC` = تشغيل تلقائي) عبر Pi‑TX→Cam‑RX.
   المفكّك مرن (تخطّي قمامة + إعادة مزامنة) ويُضبط من `sensors.json → thermal`:
   - `header` (hex)، `encoding` (`i16|i16be|u16|u16be|f32`)، `scale`، `init` (أوامر hex).
   إن ظهرت الصورة مشوّشة/مقلوبة الحرارة فجرّب `encoding` أو `scale`، وإن بقيت فارغة فتأكّد
   من توصيل **Pi‑TX (p32) → Cam‑RX** ليصل أمر بدء البثّ، أو أن `baud` مطابق (قد يكون 460800).
2. **رطوبة التربة (`soil.py → _loop`)** — يفترض سطراً نصياً برقم خام عبر USB. عدّل الفك
   لو موديولك يرسل صيغة مختلفة (نسبة جاهزة/CSV).

**LD06** مفكّكه مكتمل (رأس `0x54 0x2C`، 12 نقطة/حزمة، 47 بايت) ولا يحتاج تعديلاً إلا إن
اختلف الموديل.

---

## 6) دليل تشخيص سريع (على الراسبيري)

```bash
# 1) المنافذ ظاهرة؟
ls -l /dev/ttyAMA*    # المتوقّع ttyAMA0/2/3/4
ls -l /dev/ttyUSB*    # رطوبة التربة
i2cdetect -y 1        # PCA9685: 0x40 و 0x44

# 2) صلاحيات السيريال
groups | grep dialout || sudo usermod -aG dialout $USER   # ثم سجّل خروج/دخول

# 3) UART0 محرّر من الكونسول؟ (للـ IMU)
#    raspi-config → Serial → login shell: No, hardware: Yes
#    وأزل console=serial0 من /boot/firmware/cmdline.txt

# 4) overlays مفعّلة؟  /boot/firmware/config.txt يجب أن يحوي:
#    enable_uart=1  /  dtoverlay=uart3  /  dtoverlay=uart4  /  dtoverlay=uart5

# 5) المكتبات
pip install pyserial RPi.GPIO adafruit-circuitpython-dht \
            adafruit-circuitpython-mlx90640 adafruit-circuitpython-bno08x

# 6) فحص حيّ لكل المصادر
curl http://localhost:5000/api/v1/health
```

**إن انعكس منطق إشارة (غاز/قاطع تغذية):** عدّل `gas.active_low` في sensors.json، أو
`POWER_ACTIVE_HIGH` في `spider/hardware.py`.

**إن لم يدُر الليدار LD06:** قطب PWM (GPIO18) لا يخرج إشارة — `RPi.GPIO` PWM برمجي قد
لا يكفي؛ جرّب `pigpio` لـ PWM عتادي، أو تحقّق من تغذية 5V للموتور.

---

## 7) ما الذي يمكن تعديله بأمان مقابل الحذر

✅ **آمن:** قيم `config/sensors.json`، حدود السيرفو في `config/servo_limits.json`، عتبات
المعايرة، إضافة مسارات `GET` جديدة في قسم «خطة 11» بنهاية `web_controller.py`.

⚠️ **بحذر:** منطق المشية/التوازن (`spider/motion.py`, `gaits.py`, `balance.py`,
`safety.py`) — مرتبط بأمان الحركة. لا تغيّر قنوات PCA للأرجل (0–8). السيرفوان المساعدان
على القناة 9 فقط (`R9`/`L9`).

🚫 **لا تفعل:** كتابة على قنوات السيرفو مباشرة خارج `MotionArbiter`/`aux_servo`، أو تعطيل
قاطع التغذية/الـ software e‑stop.

---

## 8) واجهة القراءة الخارجية (لجهاز التحليل)

أُضيفت `/api/v1/*` (موثّقة بالكامل في `docs/API.md`). الأهم:
- `GET /api/v1/readings` — كل القراءات دفعة (IMU/GPS/Lidar/Soil/Environment/Thermal/Servos).
- `GET /api/v1/camera/rgb.jpg` و `.../thermal.jpg` — لقطات JPEG مفردة.
- `GET /api/v1/health` — لتمييز الحقيقي عن المحاكاة قبل التحليل.

كلها GET للقراءة (عدا `POST /api/v1/aux_servo`).

---

## 9.5) سجلّ النشر الميداني على الراسبيري (2026‑06‑01)

تشخيص وإصلاح فعلي عبر SSH على Pi 4 (kernel 6.12):

**أُصلِح:**
- الـ UARTs الإضافية لم تكن تعمل: `config.txt` فيه الـ overlays لكن مع كومنتات عربية على
  نفس السطر + البلوتوث يحتلّ `serial0`. الحل: تنظيف الأسطر + **`dtoverlay=disable-bt`** +
  إعادة تشغيل → ظهرت `ttyAMA0/3/4/5`.
- **الأسماء الفعلية**: `uart3→ttyAMA3` (LD06) · `uart4→ttyAMA4` (GPS) · `uart5→ttyAMA5`
  (كاميرا) · `serial0→ttyAMA0` (IMU). صُحِّح `sensors.json` تبعاً لذلك.
- **DHT22**: مكتبة `adafruit_dht` كانت ناقصة → ثُبِّتت (`pip install --user
  --break-system-packages adafruit-circuitpython-dht`) → صار بوضع حقيقي.

**يعمل حقيقي الآن:** LD06 (360 نقطة) · GPS (المنفذ، بانتظار fix) · غاز MQ135 · سيرفوات ·
كاميرا RGB · DHT22 (المكتبة جاهزة).

**معلّق على العتاد (قراءة خام = 0 بايت بعد إيقاف الخدمة — تشخيص نظيف):**
- **IMU (`ttyAMA0`)**: المنفذ يفتح لكن لا بيانات → الأرجح BNO085 ليس بوضع **UART‑RVC**
  (أقطاب PS) أو TX ليس على GPIO15 (p10).
- **الكاميرا الحرارية (`ttyAMA5`)**: المنفذ يفتح ونرسل أمر البدء، لكن لا بيانات → تحقّق من
  Cam‑TX→p33، Cam‑RX→p32، التغذية، واحتمال baud=460800.
- **التربة**: لا يوجد `/dev/ttyUSB0` → محوّل USB غير موصول.
- **GPS**: يقرأ لكن `fix=false` → يحتاج سماء مكشوفة/وقت (ليس خطأ برمجياً).

**كيف يعمل التطبيق:** خدمة `spider-web.service` (systemd) تشغّل
`/home/tariq_alku/Spider/web_controller.py`. بعد تعديل `sensors.json`:
`sudo systemctl restart spider-web.service`.

## 9) التحقق المنجز
- استيراد كل الوحدات ينجح بالمحاكاة على ويندوز (رجوع تلقائي).
- فك حزمة LD06 مختبر (12 نقطة، 0°=المسافة الصحيحة).
- كل مسارات `/api/v1/*` ترجع 200/JSON عبر Flask test client.
- `web_controller.py` يتجمّع بلا أخطاء، والإعدادات تُقرأ من JSON.
