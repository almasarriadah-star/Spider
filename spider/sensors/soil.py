# spider/sensors/soil.py
"""حساس رطوبة التربة عبر ADS1115 (I2C) مع محاكاة."""
import random


class SoilSensor:
    def __init__(self, i2c_lock=None, channel=0,
                 dry_raw=26000, wet_raw=11000, simulate=True):
        """
        dry_raw / wet_raw: قراءات ADC الخام عند 0% و100%.
        ⚠️ عايرهما لكل نوع تربة وحساس.
        """
        self.lock = i2c_lock
        self.channel = channel
        self.dry = dry_raw
        self.wet = wet_raw
        self.simulate = simulate
        self.ready = False
        self._chan = None

        if not simulate:
            try:
                import board
                import busio
                import adafruit_ads1x15.ads1115 as ADS
                from adafruit_ads1x15.analog_in import AnalogIn
                i2c = busio.I2C(board.SCL, board.SDA)
                ads = ADS.ADS1115(i2c)
                self._chan = AnalogIn(ads, getattr(ADS, f"P{channel}"))
                self.ready = True
            except Exception:
                self.simulate = True

    def read_raw(self):
        if self.simulate:
            return random.randint(self.wet, self.dry)
        try:
            if self.lock:
                self.lock.acquire()
            return self._chan.value
        finally:
            if self.lock:
                self.lock.release()

    def read_percent(self):
        """يُرجع نسبة الرطوبة 0–100%."""
        raw = self.read_raw()
        # capacitive: رطوبة أعلى = قراءة أقل
        pct = (self.dry - raw) / max(1, (self.dry - self.wet)) * 100.0
        return max(0.0, min(100.0, round(pct, 1)))


# نسخة وحيدة — simulate=True حتى توصيل ADS1115
soil = SoilSensor(simulate=True)
