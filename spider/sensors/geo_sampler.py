# spider/sensors/geo_sampler.py
"""جامع عينات جغرافية — يربط GPS بكل الحساسات ويسجّل قراءات مع إحداثيات.

كل عينة تحتوي:
  lat, lon, ts,
  soil_moisture, air_temp, air_humidity, gas_alarm,
  lidar_nearest, thermal_avg

يُستخدم لرسم خريطة حرارية على الويب.
"""
import threading
import time
import json
import os
import math


class GeoSampler:
    def __init__(self, store_path=None):
        self.samples = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.auto = False          # أخذ تلقائي أثناء الحركة
        self.min_dist_m = 0.5      # مسافة أقل ما بين عينتين (متر)
        self._last_pos = None

        if store_path is None:
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            store_path = os.path.join(base, "data", "geo_samples.json")
        self.store_path = store_path
        os.makedirs(os.path.dirname(store_path), exist_ok=True)
        self._load()

    # ── حفظ/تحميل ──
    def _load(self):
        try:
            if os.path.exists(self.store_path):
                with open(self.store_path, "r", encoding="utf-8") as f:
                    self.samples = json.load(f)
        except Exception:
            self.samples = []

    def _save(self):
        try:
            with open(self.store_path, "w", encoding="utf-8") as f:
                json.dump(self.samples[-2000:], f, ensure_ascii=False)
        except Exception:
            pass

    # ── أخذ عينة يدوية ──
    def take(self, gps_data, soil_pct, air_temp, air_humidity, gas_alarm,
             lidar_nearest=None, thermal_avg=None):
        """يسجّل عينة واحدة ويرجعها."""
        if not gps_data.get("fix"):
            return None
        sample = {
            "lat": round(gps_data["lat"], 7),
            "lon": round(gps_data["lon"], 7),
            "ts": time.time(),
            "soil_moisture": soil_pct,
            "air_temp": air_temp,
            "air_humidity": air_humidity,
            "gas_alarm": gas_alarm,
            "lidar_nearest": lidar_nearest,
            "thermal_avg": thermal_avg,
        }
        with self._lock:
            self.samples.append(sample)
            if len(self.samples) > 2000:
                self.samples = self.samples[-2000:]
        self._save()
        return sample

    def get_all(self):
        with self._lock:
            return list(self.samples)

    def clear(self):
        with self._lock:
            self.samples = []
        self._save()

    def get_bounds(self):
        """يرجع إحداثيات أصغر/أكبر لتحديد حدود الخريطة."""
        with self._lock:
            if not self.samples:
                return None
            lats = [s["lat"] for s in self.samples]
            lons = [s["lon"] for s in self.samples]
            return {
                "min_lat": min(lats), "max_lat": max(lats),
                "min_lon": min(lons), "max_lon": max(lons),
            }

    # ── أخذ تلقائي أثناء الحركة ──
    def _auto_loop(self, get_gps, get_readings):
        while not self._stop.is_set():
            if self.auto:
                g = get_gps()
                if g.get("fix"):
                    pos = (g["lat"], g["lon"])
                    if self._last_pos is None:
                        moved = 999
                    else:
                        moved = _haversine(self._last_pos, pos)
                    if moved >= self.min_dist_m:
                        self._last_pos = pos
                        r = get_readings()
                        self.take(g, r["soil"], r["air_temp"],
                                  r["air_humidity"], r["gas_alarm"],
                                  r.get("lidar_nearest"), r.get("thermal_avg"))
            time.sleep(0.5)

    def start_auto(self, get_gps, get_readings):
        """يبدأ خيط أخذ العينات التلقائي."""
        threading.Thread(target=self._auto_loop,
                         args=(get_gps, get_readings),
                         name="GeoSamplerAuto", daemon=True).start()

    def stop(self):
        self._stop.set()


def _haversine(a, b):
    R = 6371000
    la1, lo1, la2, lo2 = (math.radians(x) for x in [a[0], a[1], b[0], b[1]])
    h = (math.sin((la2 - la1) / 2) ** 2 +
         math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


# نسخة وحيدة عامة
sampler = GeoSampler()
