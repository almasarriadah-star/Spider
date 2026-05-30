# spider/imu.py
"""وحدة BNO085 IMU — قراءة واحدة، تصفير، وبث."""
import threading
import time

# ── متغيرات الحالة ──
BNO_READY = False
bno = None
bno_lock = threading.Lock()
bno_zero_roll = 0.0
bno_zero_pitch = 0.0

# ── محاولة الاتصال بالـ IMU ──
try:
    import serial
    from adafruit_bno08x_rvc import BNO08x_RVC
    _uart = serial.Serial("/dev/serial0", 115200, timeout=0.5)
    bno = BNO08x_RVC(_uart)
    BNO_READY = True
except Exception:
    _uart = None


def read_bno_single():
    """يقرأ قيمة واحدة من BNO085. يعيد dict أو None."""
    if not BNO_READY:
        return None
    acquired = bno_lock.acquire(timeout=0.2)
    if not acquired:
        return None
    try:
        old_timeout = _uart.timeout
        _uart.timeout = 0.1   # 100ms max — ما نعلّق أبداً
        try:
            yaw, pitch, roll, ax, ay, az = bno.heading
            return {
                "roll":      round(roll - bno_zero_roll, 2),
                "pitch":     round(pitch - bno_zero_pitch, 2),
                "yaw":       round(yaw, 2),
                "raw_roll":  round(roll, 2),
                "raw_pitch": round(pitch, 2),
                "ax":        round(ax, 3),
                "ay":        round(ay, 3),
                "az":        round(az, 3),
            }
        except Exception:
            return None
        finally:
            _uart.timeout = old_timeout
    finally:
        bno_lock.release()


def startup_imu_zero():
    """يصفّر الـ IMU عند بداية التشغيل (20 عينة)."""
    global bno_zero_roll, bno_zero_pitch
    if not BNO_READY:
        return
    print("Zeroing IMU...")
    rolls, pitches = [], []
    for _ in range(20):
        r = read_bno_single()
        if r:
            rolls.append(r["raw_roll"])
            pitches.append(r["raw_pitch"])
        time.sleep(0.06)
    if rolls:
        bno_zero_roll = sum(rolls) / len(rolls)
        bno_zero_pitch = sum(pitches) / len(pitches)
        print(f"IMU zeroed: roll={bno_zero_roll:.2f} pitch={bno_zero_pitch:.2f}")


def imu_zero_manual():
    """تصفير يدوي (من API) — يعيد القيم الجديدة."""
    global bno_zero_roll, bno_zero_pitch
    rolls, pitches = [], []
    for _ in range(20):
        r = read_bno_single()
        if r:
            rolls.append(r["raw_roll"])
            pitches.append(r["raw_pitch"])
        time.sleep(0.06)
    if not rolls:
        return None
    bno_zero_roll = sum(rolls) / len(rolls)
    bno_zero_pitch = sum(pitches) / len(pitches)
    return {"zero_roll": round(bno_zero_roll, 2), "zero_pitch": round(bno_zero_pitch, 2)}


def imu_stream(count=30):
    """يجمع count عينات من IMU ويعيدها كقائمة."""
    import time as _time
    samples = []
    for _ in range(count):
        r = read_bno_single()
        if r:
            r["t"] = round(_time.time() * 1000)
            samples.append(r)
        _time.sleep(0.05)
    return samples
