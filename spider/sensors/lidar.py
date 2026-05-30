# spider/sensors/lidar.py
"""قارئ Lidar دوّار (RPLIDAR) مع محاكاة غرفة مستطيلة + عائق متحرّك."""
import threading
import time
import math
import random


class Lidar:
    def __init__(self, port="/dev/ttyUSB0", simulate=True):
        self.simulate = simulate
        self.scan = {}          # {angle_deg: distance_mm}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.ready = False
        self._dev = None

        if not simulate:
            try:
                from rplidar import RPLidar
                self._dev = RPLidar(port)
                self.ready = True
            except Exception:
                self.simulate = True

    def start(self):
        threading.Thread(target=self._loop, name="LidarThread", daemon=True).start()

    def stop(self):
        self._stop.set()
        if self._dev:
            try:
                self._dev.stop()
                self._dev.disconnect()
            except Exception:
                pass

    def _loop(self):
        if self.simulate:
            self._sim_loop()
        else:
            self._real_loop()

    def _real_loop(self):
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


# نسخة وحيدة عامة — simulate=True حتى توصيل الجهاز
lidar = Lidar(simulate=True)
