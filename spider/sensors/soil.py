# spider/sensors/soil.py
"""حساس تربة صناعي عبر Modbus RTU (USB→RS485) — رطوبة + حرارة + ملوحة (EC).

الحساس موديول صناعي يتكلّم Modbus RTU عبر محوّل USB→RS485 على /dev/ttyUSB0، يُقرأ بمكتبة
`minimalmodbus`. المعايير المكتشَفة فعلياً (راجع ~/soil_monitor.py على الراسبيري):
  slave_id=1، baud=4800، 8N1، السجل 0 = الرطوبة ÷10 (%)، السجل 1 = الحرارة ÷10 (°C)،
  السجل 2 = EC الخام (الملوحة، µS/cm). كلّها قابلة للضبط من config/sensors.json → soil.

على ويندوز (تطوير) لا تتوفّر minimalmodbus/المنفذ ⇒ رجوع تلقائي للمحاكاة كبقية الحساسات.
"""
import threading
import time
import random

from spider import config
from spider.config import SOIL_PORT, SOIL_BAUD, SOIL_SLAVE_ID


class SoilSensor:
    def __init__(self, port=SOIL_PORT, slave_id=SOIL_SLAVE_ID, baud=SOIL_BAUD,
                 simulate=False):
        self.port = port
        self.slave_id = slave_id
        self.baud = baud
        self.simulate = simulate
        self.ready = False
        self._dev = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

        # خريطة السجلات/المعاملات من الإعدادات (دمج جزئي يضمن وجودها).
        soil_cfg = config.SENSORS["soil"]
        self._registers = soil_cfg["registers"]      # {"moisture":0,"temp":1,"ec":2}
        self._scales = soil_cfg["scales"]            # {"moisture":0.1,"temp":0.1,"ec":1.0}
        self.ec_unit = soil_cfg.get("ec_unit", "uS/cm")
        self._poll_s = float(soil_cfg.get("poll_s", 1.0))

        # آخر قيم صالحة (تبقى عند فشل قراءة مفردة — نمط DHT22).
        self._vals = {"moisture": 0.0, "temp": 0.0, "ec": 0.0}

        if not simulate:
            try:
                import minimalmodbus
                import serial
                dev = minimalmodbus.Instrument(port, slave_id)
                dev.serial.baudrate = baud
                dev.serial.bytesize = 8
                dev.serial.parity = serial.PARITY_NONE
                dev.serial.stopbits = 1
                dev.serial.timeout = 0.5
                self._dev = dev
                self.ready = True
            except Exception:
                self.simulate = True   # رجوع تلقائي للمحاكاة (ويندوز/بلا حساس)

    def start(self):
        threading.Thread(target=self._loop, name="SoilThread", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            if self.simulate:
                with self._lock:
                    self._vals["moisture"] = round(random.uniform(20, 60), 1)
                    self._vals["temp"] = round(random.uniform(15, 30), 1)
                    self._vals["ec"] = round(random.uniform(200, 1500), 0)
            else:
                # قراءة كل سجل على حدة؛ فشل واحد لا يُسقط الباقي ويُبقي آخر قيمة صالحة.
                for name, reg in self._registers.items():
                    try:
                        v = self._dev.read_register(reg, 0) * self._scales.get(name, 1.0)
                        with self._lock:
                            self._vals[name] = round(v, 1)
                        self.ready = True
                    except Exception:
                        pass
            self._stop.wait(self._poll_s)

    # ── واجهات القراءة ──
    def read_all(self):
        """كل قراءات التربة دفعة: رطوبة % + ملوحة (EC) + حرارة °C."""
        with self._lock:
            v = dict(self._vals)
        return {
            "moisture_pct": v["moisture"],
            "salinity": v["ec"],          # الملوحة = EC (التوصيلية الكهربائية)
            "temp_c": v["temp"],
            "ec_unit": self.ec_unit,
            "ready": self.ready,
            "simulate": self.simulate,
        }

    def read_percent(self):
        """نسبة الرطوبة 0–100% (للتوافق مع النداءات القائمة)."""
        with self._lock:
            return self._vals["moisture"]

    def read_raw(self):
        """قيمة خام للرطوبة (×10) — للتوافق فقط."""
        with self._lock:
            return int(round(self._vals["moisture"] * 10))


# نسخة وحيدة — Modbus RTU، رجوع تلقائي للمحاكاة بلا عتاد. يلزم استدعاء soil.start().
soil = SoilSensor(simulate=False)
