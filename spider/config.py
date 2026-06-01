# spider/config.py
"""إعدادات المشروع وتحميل حدود المحركات."""
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
LIMITS_FILE = os.path.join(CONFIG_DIR, "servo_limits.json")

_LIM_DEFAULT = {
    "default": {"min": 45, "max": 135},
    "overrides": {
        "L3": {"min": 60, "max": 120},
        "R3": {"min": 55, "max": 125}
    }
}


def _load_limits():
    try:
        with open(LIMITS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return dict(_LIM_DEFAULT)


_LIM = _load_limits()


def servo_limit(key, angle):
    """حساب الحدود الآمنة لكل محرك."""
    d = _LIM["default"]
    o = _LIM["overrides"].get(key, {})
    lo = o.get("min", d["min"])
    hi = o.get("max", d["max"])
    return max(lo, min(hi, int(round(angle))))


def reload():
    """إعادة تحميل حدود المحركات حياً بلا إعادة تشغيل السيرفر."""
    global _LIM
    _LIM = _load_limits()
    print("[config] servo_limits reloaded")


# ════════════════════════════════════════════════════════════════
# ── إعدادات الحساسات (قابلة للتعديل من config/sensors.json) ──
#    راجع docs/HARDWARE_WIRING.md. عدّل JSON بلا لمس الكود ثم أعد التشغيل
#    (أو نادِ config.reload_sensors() حياً).
# ════════════════════════════════════════════════════════════════
SENSORS_FILE = os.path.join(CONFIG_DIR, "sensors.json")

SENSORS_DEFAULT = {
    "imu":     {"port": "/dev/serial0", "baud": 115200},
    "lidar":   {"port": "/dev/ttyAMA3", "baud": 230400, "kind": "ld06", "pwm_gpio": 18},
    "gps":     {"port": "/dev/ttyAMA4", "baud": 9600,
                "lat": 34.068448, "lon": 36.746721},   # موقع افتراضي عند غياب fix
    # موديول GY-MCU90640 (HY-18): إطار 1544 بايت، رأس 5A5A0206، 768×int16 (°C×100).
    # init: أوامر تُرسَل للموديول لبدء البث (4Hz ثم تشغيل تلقائي).
    "thermal": {"port": "/dev/ttyAMA5", "baud": 115200,
                "rows": 24, "cols": 32,
                "header": "5A5A0206", "encoding": "i16", "scale": 100.0,
                "init": ["A52501CB", "A53502DC"]},
    "soil":    {"port": "/dev/ttyUSB0", "baud": 9600, "dry_raw": 26000, "wet_raw": 11000},
    "gas":     {"gpio": 6, "active_low": True},
    "dht22":   {"gpio": 16},
    "aux_servo": {"camera_key": "R9", "soil_key": "L9", "min": 0, "max": 180},
    "server":  {"host": "0.0.0.0", "port": 5000},
}


def _deep_merge(base, over):
    """يدمج قاموس JSON الجزئي فوق الافتراضي (نُسَخ متداخلة)."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_sensors():
    cfg = _deep_merge(SENSORS_DEFAULT, {})
    try:
        with open(SENSORS_FILE, "r", encoding="utf-8") as f:
            cfg = _deep_merge(SENSORS_DEFAULT, json.load(f))
    except Exception:
        pass
    return cfg


SENSORS = _load_sensors()


def _export_sensor_constants():
    """يحدّث الثوابت المسطّحة من SENSORS (لتوافق `from spider.config import LIDAR_BAUD`)."""
    g = globals()
    g["IMU_PORT"]       = SENSORS["imu"]["port"]
    g["IMU_BAUD"]       = SENSORS["imu"]["baud"]
    g["LIDAR_PORT"]     = SENSORS["lidar"]["port"]
    g["LIDAR_BAUD"]     = SENSORS["lidar"]["baud"]
    g["LIDAR_KIND"]     = SENSORS["lidar"]["kind"]
    g["LIDAR_PWM_GPIO"] = SENSORS["lidar"]["pwm_gpio"]
    g["GPS_PORT"]       = SENSORS["gps"]["port"]
    g["GPS_BAUD"]       = SENSORS["gps"]["baud"]
    g["GPS_FALLBACK_LAT"] = SENSORS["gps"]["lat"]
    g["GPS_FALLBACK_LON"] = SENSORS["gps"]["lon"]
    g["THERMAL_PORT"]   = SENSORS["thermal"]["port"]
    g["THERMAL_BAUD"]   = SENSORS["thermal"]["baud"]
    g["SOIL_PORT"]      = SENSORS["soil"]["port"]
    g["SOIL_BAUD"]      = SENSORS["soil"]["baud"]
    g["SOIL_DRY_RAW"]   = SENSORS["soil"]["dry_raw"]
    g["SOIL_WET_RAW"]   = SENSORS["soil"]["wet_raw"]
    g["GAS_DO_GPIO"]    = SENSORS["gas"]["gpio"]
    g["GAS_ACTIVE_LOW"] = SENSORS["gas"]["active_low"]
    g["DHT22_GPIO"]     = SENSORS["dht22"]["gpio"]
    g["CAM_SERVO_KEY"]  = SENSORS["aux_servo"]["camera_key"]
    g["SOIL_SERVO_KEY"] = SENSORS["aux_servo"]["soil_key"]
    g["AUX_SERVO_MIN"]  = SENSORS["aux_servo"]["min"]
    g["AUX_SERVO_MAX"]  = SENSORS["aux_servo"]["max"]
    g["SERVER_HOST"]    = SENSORS["server"]["host"]
    g["SERVER_PORT"]    = SENSORS["server"]["port"]


_export_sensor_constants()


def reload_sensors():
    """إعادة تحميل config/sensors.json حياً. ملاحظة: الوحدات التي قرأت القيم عند
    الإقلاع (المنافذ المفتوحة) تحتاج إعادة تشغيل السيرفر ليسري تغيير المنفذ."""
    global SENSORS
    SENSORS = _load_sensors()
    _export_sensor_constants()
    print("[config] sensors.json reloaded")
