# spider/sensors/digital.py
"""حساس غاز رقمي (MQ135 DO) + DHT22 (حرارة/رطوبة الجو) — مع محاكاة على ويندوز."""
import random

from spider.config import GAS_DO_GPIO, GAS_ACTIVE_LOW, DHT22_GPIO


class GasSensor:
    """خرج DO رقمي (HIGH/LOW حسب عتبة الموديول). ⚠️ مقسّم جهد إلزامي إن كان DO=5V."""

    def __init__(self, gpio=GAS_DO_GPIO, active_low=GAS_ACTIVE_LOW, simulate=False):
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
        """True عند تجاوز عتبة الغاز."""
        if self.simulate:
            return random.random() < 0.1
        raw = self._gpio.input(self.gpio)
        return (raw == 0) if self.active_low else (raw == 1)


class DHT22Sensor:
    """حرارة (°C) + رطوبة الجو (%) — بروتوكول سلك-واحد عبر adafruit_dht."""

    def __init__(self, gpio=DHT22_GPIO, simulate=False):
        self.gpio = gpio
        self.simulate = simulate
        self._dev = None
        self._last = {"temp": None, "humidity": None}
        if not simulate:
            try:
                import board
                import adafruit_dht
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
            pass   # DHT22 يفشل أحياناً بقراءة منفردة — نُبقي آخر قيمة صالحة
        return dict(self._last)


# نسخ وحيدة — رجوع تلقائي للمحاكاة بلا عتاد.
gas = GasSensor(simulate=False)
dht22 = DHT22Sensor(simulate=False)
