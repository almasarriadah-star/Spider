# spider/sensors/lidar.py
"""قارئ ليدار:
- kind="tfmini": ليدار نقطة واحدة عبر UART (TFmini / TF-Luna / Benewake)
  يقرأ المسافة الأمامية من منفذ السيريال على الراسبيري (GPIO14/15, /dev/serial0).
- kind="rplidar": ليدار دوّار 360° عبر USB (RPLIDAR A1/A2).
- simulate: محاكاة غرفة مستطيلة + عائق متحرّك (بلا عتاد).

⚠️ توصيل TFmini/TF-Luna على الراسبيري:
   الليدار VCC(+)   → 5V الراسبيري (TF-Luna يقبل 3.3~5V، TFmini يحتاج 5V)
   الليدار GND(-)   → GND الراسبيري
   الليدار TX       → الراسبيري RX = GPIO15 = الطرف رقم 10
   الليدار RX       → الراسبيري TX = GPIO14 = الطرف رقم 8 (اختياري، لإرسال أوامر)
   لقراءة بيانات الليدار يجب أن يدخل خط TX للليدار على RX للراسبيري (GPIO15/طرف 10).
   فعّل الـ UART:  sudo raspi-config → Interface → Serial → (Login shell: No, Hardware: Yes)
   ثم استخدم المنفذ /dev/serial0 بسرعة 115200.
"""
import threading
import time
import math
import random


class Lidar:
    def __init__(self, port="/dev/serial0", baud=115200, kind="tfmini",
                 simulate=False, forward_cone=20):
        self.kind = kind
        self.port = port
        self.baud = baud
        self.simulate = simulate
        self.forward_cone = forward_cone   # عرض المخروط الأمامي بالدرجات للعرض على الرادار
        self.scan = {}                     # {angle_deg(int): distance_mm}
        self.distance_mm = 0               # المسافة الأمامية (TFmini) بالمليمتر
        self.strength = 0                  # قوّة الإشارة (TFmini)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.ready = False
        self._dev = None
        self._ser = None
        self._pwm = None

        if not simulate:
            if kind == "rplidar":
                ok = self._open_rplidar()
            else:                       # tfmini / ld06 — كلاهما UART تسلسلي
                ok = self._open_serial()
            if not ok:
                self.simulate = True   # رجوع تلقائي للمحاكاة عند غياب العتاد (مثلاً على ويندوز)

    # ── فتح الأجهزة ──
    def _open_serial(self):
        try:
            import serial
            self._ser = serial.Serial(self.port, self.baud, timeout=0.2)
            self.ready = True
            return True
        except Exception:
            return False

    def _open_rplidar(self):
        try:
            from rplidar import RPLidar
            self._dev = RPLidar(self.port)
            self.ready = True
            return True
        except Exception:
            return False

    # ── دورة الحياة ──
    def start(self):
        if self.kind == "ld06" and not self.simulate:
            self._start_motor()
        threading.Thread(target=self._loop, name="LidarThread", daemon=True).start()

    def _start_motor(self):
        """يُخرج PWM لتشغيل موتور دوران LD06 (GPIO18)."""
        try:
            import RPi.GPIO as GPIO
            from spider.config import LIDAR_PWM_GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(LIDAR_PWM_GPIO, GPIO.OUT)
            self._pwm = GPIO.PWM(LIDAR_PWM_GPIO, 10000)   # ~10kHz
            self._pwm.start(40)                            # 40% duty للدوران الافتراضي
        except Exception:
            self._pwm = None   # على ويندوز/بلا عتاد — يُتجاهَل

    def stop(self):
        self._stop.set()
        if self._pwm:
            try:
                self._pwm.stop()
            except Exception:
                pass
        if self._dev:
            try:
                self._dev.stop()
                self._dev.disconnect()
            except Exception:
                pass
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass

    def _loop(self):
        if self.simulate:
            self._sim_loop()
        elif self.kind == "ld06":
            self._real_loop_ld06()
        elif self.kind == "tfmini":
            self._real_loop_tfmini()
        else:
            self._real_loop_rplidar()

    # ── قراءة TFmini/TF-Luna عبر UART ──
    def _update_point(self, dist_mm, strength=0):
        """يخزّن نقطة أمامية واحدة موزّعة على مخروط صغير لتظهر على الرادار."""
        self.distance_mm = dist_mm
        self.strength = strength
        new = {}
        if dist_mm > 0:
            half = max(0, self.forward_cone // 2)
            for a in range(-half, half + 1):
                new[a % 360] = dist_mm     # 0° = للأمام
        with self._lock:
            self.scan = new

    def _real_loop_tfmini(self):
        """يفك إطار TFmini المكوّن من 9 بايت: 0x59 0x59 DistL DistH StrL StrH ResL ResH Checksum.
        المسافة بالسنتيمتر = DistL + (DistH<<8)."""
        ser = self._ser
        buf = b""
        try:
            while not self._stop.is_set():
                chunk = ser.read(9)
                if chunk:
                    buf += chunk
                while len(buf) >= 9:
                    if buf[0] == 0x59 and buf[1] == 0x59:
                        frame = buf[:9]
                        if (sum(frame[:8]) & 0xFF) == frame[8]:
                            dist_cm = frame[2] | (frame[3] << 8)
                            strength = frame[4] | (frame[5] << 8)
                            # قراءة غير صالحة: قوّة منخفضة جداً أو مسافة صفر
                            if dist_cm > 0 and strength > 0:
                                self._update_point(dist_cm * 10, strength)
                            else:
                                self._update_point(0, strength)
                            self.ready = True
                            buf = buf[9:]
                        else:
                            buf = buf[1:]   # checksum خاطئ — أعد المزامنة بايت واحد
                    else:
                        buf = buf[1:]       # ابحث عن رأس الإطار
        except Exception:
            self.ready = False
        finally:
            try:
                ser.close()
            except Exception:
                pass

    # ── قراءة LD06 دوّار 360° عبر UART (230400) ──
    def _real_loop_ld06(self):
        """LD06: حزمة 47 بايت، رأس 0x54 0x2C، 12 نقطة/حزمة.
        كل حزمة تغطّي قطاعاً صغيراً → نُراكم في scan لبناء 360° كاملة."""
        ser = self._ser
        PKT = 47
        buf = b""
        try:
            while not self._stop.is_set():
                chunk = ser.read(PKT)
                if chunk:
                    buf += chunk
                while len(buf) >= PKT:
                    if buf[0] == 0x54 and buf[1] == 0x2C:
                        self._parse_ld06(buf[:PKT])
                        self.ready = True
                        buf = buf[PKT:]
                    else:
                        buf = buf[1:]   # ابحث عن رأس الحزمة
        except Exception:
            self.ready = False
        finally:
            try:
                ser.close()
            except Exception:
                pass

    def _parse_ld06(self, pkt):
        import struct
        start = struct.unpack_from("<H", pkt, 4)[0]    # 0.01 درجة
        end = struct.unpack_from("<H", pkt, 42)[0]     # 0.01 درجة
        n = 12
        span = (end - start) % 36000                   # يعالج التفاف 360°
        step = span / (n - 1) if n > 1 else 0
        new = {}
        for i in range(n):
            off = 6 + i * 3
            dist = struct.unpack_from("<H", pkt, off)[0]   # مم
            if dist <= 0:
                continue
            ang = ((start + step * i) / 100.0) % 360.0
            new[int(ang) % 360] = dist
        if new:
            with self._lock:
                self.scan.update(new)                      # دمج تدريجي
                self.distance_mm = self.scan.get(0, self.distance_mm)

    # ── قراءة RPLIDAR دوّار عبر USB ──
    def _real_loop_rplidar(self):
        try:
            for scan in self._dev.iter_scans(max_buf_meas=500):
                if self._stop.is_set():
                    break
                new = {}
                for quality, angle, dist in scan:
                    if dist > 0:
                        new[int(angle) % 360] = dist   # مم
                with self._lock:
                    self.scan = new
                self.ready = True
        except Exception:
            self.ready = False

    # ── المحاكاة ──
    def _sim_loop(self):
        """محاكاة غرفة مستطيلة + عائق متحرّك."""
        self.ready = True
        t = 0
        while not self._stop.is_set():
            new = {}
            for a in range(0, 360, 1):
                r = math.radians(a)
                walls = []
                # أربعة جدران (جهات N/S/E/W)
                for dist, nx, ny in [
                    (2500, 1, 0), (2500, -1, 0),
                    (2000, 0, 1), (2000, 0, -1)
                ]:
                    denom = math.cos(r) * nx + math.sin(r) * ny
                    if denom > 1e-3:
                        walls.append(dist / denom)
                d = min([w for w in walls if w > 0], default=4000)
                # عائق متحرّك دائري
                obs_ang = (t * 2) % 360
                if abs(((a - obs_ang + 180) % 360) - 180) < 8:
                    d = min(d, 800)
                new[a] = d + random.uniform(-20, 20)
            with self._lock:
                self.scan = new
            self.distance_mm = new.get(0, 0)
            t += 1
            time.sleep(0.1)

    def get_scan(self):
        with self._lock:
            return dict(self.scan)


def obstacles_world(scan, lat, lon, heading_deg, max_mm=2000, step=10):
    """يحوّل مسح الليدار لنقاط عوائق بإحداثيات عالمية (دمج GPS + IMU heading)."""
    pts = []
    for a in range(0, 360, step):
        d = scan.get(a)
        if not d or d > max_mm:
            continue
        world_ang = math.radians((heading_deg + a) % 360)
        dn = (d / 1000.0) * math.cos(world_ang)   # شمال (متر)
        de = (d / 1000.0) * math.sin(world_ang)   # شرق (متر)
        plat = lat + dn / 111111.0
        plon = lon + de / (111111.0 * math.cos(math.radians(lat)) + 1e-9)
        pts.append({"lat": round(plat, 7), "lon": round(plon, 7), "dist": round(d)})
    return pts


# نسخة وحيدة عامة — LD06 دوّار 360° عبر UART3 (/dev/ttyAMA2 @ 230400).
# تحاول قراءة العتاد فعلياً، وترجع للمحاكاة تلقائياً إن لم يتوفّر المنفذ (مثلاً على ويندوز).
from spider.config import LIDAR_PORT, LIDAR_BAUD, LIDAR_KIND
lidar = Lidar(port=LIDAR_PORT, baud=LIDAR_BAUD, kind=LIDAR_KIND, simulate=False)
