# spider/sensors/gps.py
"""قارئ GPS (NMEA/UART) مع وضع محاكاة كامل لاختبار بلا عتاد."""
import threading
import time
import math


class GPS:
    def __init__(self, port="/dev/serial0", baud=9600, simulate=True,
                 sim_origin=(31.9539, 35.9106)):
        self.simulate = simulate
        self.data = {
            "lat": sim_origin[0], "lon": sim_origin[1],
            "fix": False, "sats": 0,
            "speed": 0.0, "course": 0.0, "ts": 0
        }
        self._stop = threading.Event()
        self._ser = None
        self._sim = {"lat": sim_origin[0], "lon": sim_origin[1], "course": 0.0}
        self._cmd = {"vx": 0.0, "vy": 0.0, "omega": 0.0}   # أمر الحركة الحالي (للمحاكاة)

        if not simulate:
            try:
                import serial
                self._ser = serial.Serial(port, baud, timeout=1)
            except Exception:
                self.simulate = True  # fallback لوضع المحاكاة

    def start(self):
        threading.Thread(target=self._loop, name="GPSThread", daemon=True).start()

    def stop(self):
        self._stop.set()

    def set_motion(self, vx=0.0, vy=0.0, omega=0.0):
        """يحدّث أمر الحركة الحالي — تستدعيها /api/move لتحريك الموقع المحاكى."""
        self._cmd = {"vx": float(vx), "vy": float(vy), "omega": float(omega)}

    def _loop(self):
        while not self._stop.is_set():
            if self.simulate:
                self._sim_step()
            else:
                self._read_nmea()
            time.sleep(1.0)

    def _sim_step(self):
        """يحرّك موقع الروبوت المحاكى حسب أمر الحركة الحالي (يُضبط عبر set_motion)."""
        vx = self._cmd.get("vx", 0.0)
        vy = self._cmd.get("vy", 0.0)
        om = self._cmd.get("omega", 0.0)

        # سرعة تقريبية بالمتر/ثانية (مقياس عرض فقط)
        speed = math.hypot(vx, vy) * 0.5
        self._sim["course"] = (self._sim["course"] + om * 15) % 360
        dist_m = speed * 1.0
        dlat = (dist_m * math.cos(math.radians(self._sim["course"]))) / 111111.0
        dlon = (dist_m * math.sin(math.radians(self._sim["course"]))) / \
               (111111.0 * math.cos(math.radians(self._sim["lat"])) + 1e-9)
        self._sim["lat"] += dlat
        self._sim["lon"] += dlon
        self.data = {
            "lat": round(self._sim["lat"], 7),
            "lon": round(self._sim["lon"], 7),
            "fix": True,
            "sats": 9,
            "speed": round(speed, 2),
            "course": round(self._sim["course"], 1),
            "ts": time.time()
        }

    def _read_nmea(self):
        """يقرأ جملة NMEA حقيقية من المنفذ التسلسلي."""
        try:
            line = self._ser.readline().decode("ascii", "ignore").strip()
        except Exception:
            return
        if line.startswith(("$GPRMC", "$GNRMC")):
            p = line.split(",")
            if len(p) > 8 and p[2] == "A":
                self.data.update({
                    "lat": self._nmea_deg(p[3], p[4]),
                    "lon": self._nmea_deg(p[5], p[6]),
                    "fix": True,
                    "speed": float(p[7] or 0) * 0.514,  # عقدة → م/ث
                    "course": float(p[8] or 0),
                    "ts": time.time()
                })
        elif line.startswith(("$GPGGA", "$GNGGA")):
            p = line.split(",")
            if len(p) > 7:
                try:
                    self.data["sats"] = int(p[7] or 0)
                except Exception:
                    pass

    @staticmethod
    def _nmea_deg(val, hemi):
        if not val:
            return None
        deg = int(float(val) / 100)
        minutes = float(val) - deg * 100
        d = deg + minutes / 60
        return -d if hemi in ("S", "W") else round(d, 7)


# نسخة وحيدة عامة — simulate=True حتى توصيل الشريحة
gps = GPS(simulate=True)
