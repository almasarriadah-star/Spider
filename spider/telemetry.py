# spider/telemetry.py
"""مخزن التتبّع: المسار + نقاط الاهتمام (POI) لكل الخطط (رطوبة/أعشاب/ليدار/علامات)."""
import threading
import time
import json
import os
import uuid

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")


class TrackStore:
    """يحفظ مسار الروبوت + نقاط الاهتمام. نموذج POI موحّد لكل الخطط."""

    def __init__(self, path=None, max_points=5000):
        self.path = path or os.path.join(DATA_DIR, "track.json")
        self.max = max_points
        self.track = []    # [{lat, lon, ts}]
        self.pois = []     # [{id, type, lat, lon, ts, props}]
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._load()

    def add_fix(self, lat, lon, ts=None):
        """يضيف نقطة مسار من قراءة GPS."""
        if lat is None or lon is None:
            return
        with self._lock:
            self.track.append({"lat": lat, "lon": lon, "ts": ts or time.time()})
            if len(self.track) > self.max:
                self.track = self.track[-self.max:]

    def add_poi(self, type_, lat, lon, props=None):
        """يضيف نقطة اهتمام للخريطة (soil/weed/lidar_obstacle/marker)."""
        poi = {
            "id": uuid.uuid4().hex[:8],
            "type": type_,
            "lat": lat,
            "lon": lon,
            "ts": time.time(),
            "props": props or {}
        }
        with self._lock:
            self.pois.append(poi)
            self._save()
        return poi

    def clear_pois(self, type_filter=None):
        """يمسح نقاط الاهتمام (كلها أو نوع محدّد)."""
        with self._lock:
            if type_filter:
                self.pois = [p for p in self.pois if p["type"] != type_filter]
            else:
                self.pois = []
            self._save()

    def snapshot(self):
        """يُرجع نسخة من المسار + النقاط للإرسال للواجهة."""
        with self._lock:
            return {"track": list(self.track), "pois": list(self.pois)}

    def _save(self):
        try:
            json.dump({"pois": self.pois},
                      open(self.path, "w", encoding="utf-8"),
                      ensure_ascii=False)
        except Exception:
            pass

    def _load(self):
        if os.path.exists(self.path):
            try:
                data = json.load(open(self.path, encoding="utf-8"))
                self.pois = data.get("pois", [])
            except Exception:
                pass


# نسخة وحيدة عامة
store = TrackStore()
