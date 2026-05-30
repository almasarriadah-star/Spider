# spider/balance.py
"""BalanceController + SimplePID — توازن الجسم عبر IMU."""
import json
import os
import threading
import time

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CONFIG_DIR          = os.path.join(BASE_DIR, "config")
BALANCE_CONFIG_FILE = os.path.join(CONFIG_DIR, "balance_config.json")

# خريطة القنوات
LEG_FEMUR_CHANNEL = {
    "RF": "R1", "RM": "R4", "RR": "R7",
    "LF": "L1", "LM": "L4", "LR": "L7",
}

# أوزان توزيع التوازن
BALANCE_WEIGHTS = {
    "RF": (+0.5, -0.5),
    "RM": (+0.5,  0.0),
    "RR": (+0.5, +0.5),
    "LF": (-0.5, -0.5),
    "LM": (-0.5,  0.0),
    "LR": (-0.5, +0.5),
}

# اتجاه Femur
FEMUR_DIRECTION = {
    "RF": -1, "RM": -1, "RR": -1,
    "LF": +1, "LM": +1, "LR": +1,
}


class SimplePID:
    """PID controller بسيط مع anti-windup"""
    def __init__(self, kp=0.8, ki=0.0, kd=0.3, output_limit=12.0):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_limit = output_limit
        self._prev_error = 0.0
        self._integral   = 0.0
        self._last_time  = None

    def reset(self):
        self._prev_error = 0.0
        self._integral   = 0.0
        self._last_time  = None

    def update(self, error):
        now = time.monotonic()
        if self._last_time is None:
            dt = 0.033
        else:
            dt = max(now - self._last_time, 0.001)
        self._last_time = now

        p_term = self.kp * error
        self._integral += error * dt
        if self.ki > 0:
            max_integral = self.output_limit / self.ki
            self._integral = max(-max_integral, min(max_integral, self._integral))
        i_term = self.ki * self._integral
        derivative = (error - self._prev_error) / dt
        d_term = self.kd * derivative
        self._prev_error = error

        output = p_term + i_term + d_term
        return max(-self.output_limit, min(self.output_limit, output))


class BalanceController:
    def __init__(self):
        try:
            with open(BALANCE_CONFIG_FILE) as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

        roll_cfg  = cfg.get("roll_pid", {})
        pitch_cfg = cfg.get("pitch_pid", {})
        max_corr  = cfg.get("max_correction", 12)

        self.roll_pid  = SimplePID(
            kp=roll_cfg.get("kp", 0.8), ki=roll_cfg.get("ki", 0.0),
            kd=roll_cfg.get("kd", 0.3), output_limit=max_corr)
        self.pitch_pid = SimplePID(
            kp=pitch_cfg.get("kp", 0.8), ki=pitch_cfg.get("ki", 0.0),
            kd=pitch_cfg.get("kd", 0.3), output_limit=max_corr)

        self.max_correction  = max_corr
        self.frequency       = cfg.get("frequency_hz", 30)
        self.smoothing_n     = cfg.get("imu_smoothing_samples", 5)
        self.deadzone        = cfg.get("deadzone_deg", 1.5)
        self.enabled         = False
        self._thread         = None
        self._stop_event     = threading.Event()

        self.last_roll             = 0.0
        self.last_pitch            = 0.0
        self.last_roll_correction  = 0.0
        self.last_pitch_correction = 0.0
        self.debug_history         = []
        self.MAX_HISTORY           = 100
        self._roll_buf             = []
        self._pitch_buf            = []
        self.femur_offsets         = {leg: 0.0 for leg in BALANCE_WEIGHTS}
        self._base_femur           = {}

        # مرجع دالة للـ set_servo (يُحقن من web_controller)
        self._set_servo_fn   = None
        self._current_ref    = None
        self._gait_event_ref = None

    def configure(self, set_servo_fn, current_ref, gait_event_ref):
        """يحقن dependencies من web_controller."""
        self._set_servo_fn   = set_servo_fn
        self._current_ref    = current_ref
        self._gait_event_ref = gait_event_ref

    def start(self):
        if self.enabled:
            return
        self.enabled = True
        self._stop_event.clear()
        self.roll_pid.reset()
        self.pitch_pid.reset()
        self._roll_buf.clear()
        self._pitch_buf.clear()
        if self._current_ref:
            self._base_femur = {leg: self._current_ref[LEG_FEMUR_CHANNEL[leg]]
                                for leg in BALANCE_WEIGHTS}
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.enabled = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        for leg in BALANCE_WEIGHTS:
            ch = LEG_FEMUR_CHANNEL[leg]
            if self._current_ref and ch in self._current_ref:
                base = self._base_femur.get(leg, self._current_ref[ch])
                if self._set_servo_fn:
                    self._set_servo_fn(ch, base)
        self.femur_offsets         = {leg: 0.0 for leg in BALANCE_WEIGHTS}
        self.last_roll_correction  = 0.0
        self.last_pitch_correction = 0.0

    def _smooth(self, buf, new_val, n):
        buf.append(new_val)
        if len(buf) > n:
            buf.pop(0)
        return sum(buf) / len(buf)

    def _loop(self):
        interval = 1.0 / self.frequency
        while not self._stop_event.is_set():
            loop_start = time.monotonic()
            try:
                self._balance_tick()
            except Exception as e:
                print(f"[Balance] Error: {e}")
            elapsed    = time.monotonic() - loop_start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                self._stop_event.wait(sleep_time)

    def _balance_tick(self):
        from spider.imu import read_bno_single
        imu = read_bno_single()
        if imu is None:
            return

        roll  = self._smooth(self._roll_buf,  imu["roll"],  self.smoothing_n)
        pitch = self._smooth(self._pitch_buf, imu["pitch"], self.smoothing_n)
        self.last_roll  = round(roll, 2)
        self.last_pitch = round(pitch, 2)

        error_roll  = 0.0 if abs(roll)  < self.deadzone else -roll
        error_pitch = 0.0 if abs(pitch) < self.deadzone else -pitch

        corr_roll  = self.roll_pid.update(error_roll)
        corr_pitch = self.pitch_pid.update(error_pitch)
        self.last_roll_correction  = round(corr_roll, 2)
        self.last_pitch_correction = round(corr_pitch, 2)

        for leg, (rw, pw) in BALANCE_WEIGHTS.items():
            offset    = corr_roll * rw + corr_pitch * pw
            direction = FEMUR_DIRECTION[leg]
            self.femur_offsets[leg] = round(offset * direction, 1)

        is_gait_running = self._gait_event_ref and self._gait_event_ref.is_set()
        if not is_gait_running:
            self._apply_corrections()

        entry = {
            "t":      round(time.monotonic(), 3),
            "roll":   self.last_roll,
            "pitch":  self.last_pitch,
            "corr_r": self.last_roll_correction,
            "corr_p": self.last_pitch_correction,
        }
        self.debug_history.append(entry)
        if len(self.debug_history) > self.MAX_HISTORY:
            self.debug_history.pop(0)

    def _apply_corrections(self):
        if not self._set_servo_fn:
            return
        for leg, offset in self.femur_offsets.items():
            ch     = LEG_FEMUR_CHANNEL[leg]
            base   = self._base_femur.get(leg, 90)
            target = max(50, min(130, base + offset))
            self._set_servo_fn(ch, round(target))

    def tune(self, kp=None, ki=None, kd=None, axis="both"):
        pids = []
        if axis in ("both", "roll"):
            pids.append(self.roll_pid)
        if axis in ("both", "pitch"):
            pids.append(self.pitch_pid)
        for pid in pids:
            if kp is not None: pid.kp = kp
            if ki is not None: pid.ki = ki
            if kd is not None: pid.kd = kd

    def get_status(self):
        return {
            "enabled":          self.enabled,
            "roll":             self.last_roll,
            "pitch":            self.last_pitch,
            "roll_correction":  self.last_roll_correction,
            "pitch_correction": self.last_pitch_correction,
            "femur_offsets":    self.femur_offsets,
            "pid_roll":  {"kp": self.roll_pid.kp,  "ki": self.roll_pid.ki,  "kd": self.roll_pid.kd},
            "pid_pitch": {"kp": self.pitch_pid.kp, "ki": self.pitch_pid.ki, "kd": self.pitch_pid.kd},
        }

    def reload(self):
        """إعادة تحميل إعدادات balance_config.json حياً — يطبّق PID الجديد فوراً."""
        try:
            with open(BALANCE_CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"[balance] reload failed: {e}")
            return False

        roll_cfg  = cfg.get("roll_pid", {})
        pitch_cfg = cfg.get("pitch_pid", {})
        max_corr  = cfg.get("max_correction", self.max_correction)

        self.roll_pid.kp  = roll_cfg.get("kp",  self.roll_pid.kp)
        self.roll_pid.ki  = roll_cfg.get("ki",  self.roll_pid.ki)
        self.roll_pid.kd  = roll_cfg.get("kd",  self.roll_pid.kd)
        self.roll_pid.output_limit = max_corr

        self.pitch_pid.kp = pitch_cfg.get("kp", self.pitch_pid.kp)
        self.pitch_pid.ki = pitch_cfg.get("ki", self.pitch_pid.ki)
        self.pitch_pid.kd = pitch_cfg.get("kd", self.pitch_pid.kd)
        self.pitch_pid.output_limit = max_corr

        self.max_correction  = max_corr
        self.frequency       = cfg.get("frequency_hz",         self.frequency)
        self.smoothing_n     = cfg.get("imu_smoothing_samples", self.smoothing_n)
        self.deadzone        = cfg.get("deadzone_deg",          self.deadzone)

        print(f"[balance] reloaded: kp={self.roll_pid.kp}, kd={self.roll_pid.kd}, deadzone={self.deadzone}")
        return True
