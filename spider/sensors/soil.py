# spider/sensors/soil.py
"""حساس رطوبة التربة عبر USB‑Serial (/dev/ttyUSB0) مع محاكاة.

انتقل من ADS1115 (I2C) إلى محوّل USB‑UART مستقل — يحرّر ناقل I2C ولا يزاحم السيرفوات.
الموديول يُفترض أن يُرسل سطراً نصياً يحوي قراءة خام (رقم) كل فترة. ⚠️ عدّل الفك في
`_loop` حسب صيغة موديولك (رقم خام أو نسبة جاهزة).
"""
import threading
import time
import random

from spider.config import SOIL_PORT, SOIL_BAUD, SOIL_DRY_RAW, SOIL_WET_RAW


class SoilSensor:
    def __init__(self, port=SOIL_PORT, baud=SOIL_BAUD, simulate=False,
                 dry_raw=26000, wet_raw=11000):
        """dry_raw / wet_raw: قراءات ADC الخام عند 0% و100%. ⚠️ عايرهما لكل تربة."""
        self.port = port
        self.baud = baud
        self.dry = dry_raw
        self.wet = wet_raw
        self.simulate = simulate
        self.ready = False
        self._raw = dry_raw
        self._ser = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

        if not simulate:
            try:
                import serial
                self._ser = serial.Serial(port, baud, timeout=1)
                self.ready = True
            except Exception:
                self.simulate = True   # رجوع تلقائي للمحاكاة (مثلاً على ويندوز)

    def start(self):
        threading.Thread(target=self._loop, name="SoilThread", daemon=True).start()

    def stop(self):
        self._stop.set()
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass

    def _loop(self):
        while not self._stop.is_set():
            if self.simulate:
                with self._lock:
                    self._raw = random.randint(self.wet, self.dry)
                time.sleep(1.0)
            else:
                try:
                    line = self._ser.readline().decode("ascii", "ignore").strip()
                    # ⚠️ TODO: عدّل حسب صيغة موديولك (رقم خام أو نسبة مباشرة).
                    if line:
                        with self._lock:
                            self._raw = int(float(line))
                        self.ready = True
                except Exception:
                    pass

    def read_raw(self):
        with self._lock:
            return self._raw

    def read_percent(self):
        """يُرجع نسبة الرطوبة 0–100%."""
        raw = self.read_raw()
        # capacitive: رطوبة أعلى = قراءة أقل
        pct = (self.dry - raw) / max(1, (self.dry - self.wet)) * 100.0
        return max(0.0, min(100.0, round(pct, 1)))


# نسخة وحيدة — USB‑Serial، رجوع تلقائي للمحاكاة بلا عتاد. يلزم استدعاء soil.start().
soil = SoilSensor(simulate=False, dry_raw=SOIL_DRY_RAW, wet_raw=SOIL_WET_RAW)
