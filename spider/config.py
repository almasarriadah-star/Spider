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
