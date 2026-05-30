from flask import Flask, render_template, request, jsonify
import json
import math
import os
import time
import threading

app = Flask(__name__)

# ── Hardware Setup ──
from spider.hardware import HARDWARE, right_pca, left_pca, i2c_lock

# ── Constants (من spider.constants) ──
from spider.constants import (
    MIN_ANGLE, MAX_ANGLE,
    DEFAULT_RIGHT, DEFAULT_LEFT,
    SERVO_NAMES, LEG_GROUPS, _STAND,
)

# ── IMU (من spider.imu) ──
from spider.imu import (
    BNO_READY, read_bno_single,
    bno_zero_roll, bno_zero_pitch,
    startup_imu_zero, imu_zero_manual, imu_stream,
)
import spider.imu as _imu_mod   # مرجع مباشر لتحديث bno_zero_*

BASE_DIR = os.path.dirname(__file__)
POSITIONS_FILE = os.path.join(BASE_DIR, "positions.json")
DEFAULTS_FILE  = os.path.join(BASE_DIR, "leg_defaults.json")
PRESETS_FILE   = os.path.join(BASE_DIR, "leg_presets.json")

# ── Gait State ──
GAIT_FILE          = os.path.join(BASE_DIR, "config", "gait_params.json")
BALANCE_CONFIG_FILE = os.path.join(BASE_DIR, "config", "balance_config.json")
gait_running      = False
gait_event        = threading.Event()
gait_thread       = None
gait_lock         = threading.Lock()
gait_step_count   = 0
gait_phase        = "A"
gait_current_phase = "idle"

# ── State ──
import spider
current = spider.current
from spider.safety import arbiter, set_force_stop

_servos_initialized = False


def limit_angle(a):
    return max(MIN_ANGLE, min(MAX_ANGLE, int(a)))


def set_servo(key, angle):
    owner = arbiter.owner()
    arbiter.set_target(owner, {key: angle})
    from spider.config import servo_limit
    return servo_limit(key, angle)


def set_servos_batch(updates):
    """يكتب عدة محركات دفعة واحدة عبر محكّم الحركة الآمن"""
    owner = arbiter.owner()
    arbiter.set_target(owner, updates)


def load_json(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_positions():
    return load_json(POSITIONS_FILE)


def save_positions(data):
    save_json(POSITIONS_FILE, data)


def load_leg_defaults():
    return load_json(DEFAULTS_FILE)


def save_leg_defaults(data):
    save_json(DEFAULTS_FILE, data)


def load_leg_presets():
    return load_json(PRESETS_FILE)


def save_leg_presets(data):
    save_json(PRESETS_FILE, data)


def apply_startup_calibration():
    """يحرّك المحركات لوضعية التوازن — ينطبق فقط أول مرة يُطلب فيها"""
    global _servos_initialized
    if _servos_initialized:
        return
    try:
        arbiter.start()
    except Exception as e:
        print(f"Error starting arbiter: {e}")

    defaults = load_leg_defaults()
    print("Applying startup calibration using MotionArbiter...")

    targets = {}
    for group, keys in LEG_GROUPS.items():
        for key in keys:
            base = DEFAULT_RIGHT[int(key[1:])] if key[0] == "R" else DEFAULT_LEFT[int(key[1:])]
            targets[key] = defaults.get(group, {}).get(key, base)

    if arbiter.acquire("startup"):
        from spider import goto
        goto("startup", targets, timeout=8.0)
        arbiter.release("startup")

    _servos_initialized = True
    print("Startup calibration applied - servos holding position")


def _ensure_servos_on():
    """تأكد إن المحركات شغّالة — أول command يفعّلها"""
    if not _servos_initialized:
        apply_startup_calibration()


# ── Gait Helpers ──
_force_stop = False

# ── Easing & Gait Functions (الاستيراد من spider.gaits) ──
from spider.gaits import (
    ease_smoothstep, ease_cosine, smooth_move_leg, set_ease, get_ease_name,
    _run_forward_gait, _run_lateral_gait, _run_ripple_gait, _run_smooth_ripple,
    _load_gait_params, _load_ripple_params, get_leg_angles
)
_ease_func = ease_smoothstep

class GlobalRef:
    def __init__(self, name):
        self.name = name

    def __getitem__(self, item):
        return globals()[self.name]

    def __setitem__(self, item, value):
        globals()[self.name] = value


def _sync_force_stop(val: bool):
    """يضبط _force_stop محلياً وفي spider.safety معاً."""
    global _force_stop
    _force_stop = val
    set_force_stop(val)





def execute_gait(params, speed, max_steps):
    """Execute tripod gait in background thread"""
    global gait_running, gait_step_count, gait_phase
    # Tripod groups
    GROUP_A = ["RF", "LM", "LR"]  # legs 1,3,5
    GROUP_B = ["LF", "RM", "RR"]  # legs 2,4,6

    gait_step_count = 0
    base_steps = 10  # interpolation steps
    base_delay = 0.03

    actual_steps = max(3, int(base_steps / speed))
    actual_delay = max(0.01, base_delay / speed)

    while gait_event.is_set() and not arbiter.in_estop() and (max_steps == 0 or gait_step_count < max_steps):
        # Phase 1: Group A swings forward, Group B pushes back
        gait_phase = "A"
        # Group A: stance -> lift -> swing_fwd
        for group in GROUP_A:
            if group in params:
                leg_keys = LEG_GROUPS[group]
                smooth_move_leg(leg_keys, params[group].get("lift", params[group].get("stance", {})), actual_steps, actual_delay)
                smooth_move_leg(leg_keys, params[group].get("swing_fwd", params[group].get("stance", {})), actual_steps, actual_delay)
        # Group B: stance -> push_back (already on ground, push back)
        for group in GROUP_B:
            if group in params:
                leg_keys = LEG_GROUPS[group]
                smooth_move_leg(leg_keys, params[group].get("push_back", params[group].get("stance", {})), actual_steps, actual_delay)

        gait_step_count += 1
        if not gait_event.is_set():
            break

        # Phase 2: Group B swings forward, Group A pushes back
        gait_phase = "B"
        # Group B: stance -> lift -> swing_fwd
        for group in GROUP_B:
            if group in params:
                leg_keys = LEG_GROUPS[group]
                smooth_move_leg(leg_keys, params[group].get("lift", params[group].get("stance", {})), actual_steps, actual_delay)
                smooth_move_leg(leg_keys, params[group].get("swing_fwd", params[group].get("stance", {})), actual_steps, actual_delay)
        # Group A: stance -> push_back
        for group in GROUP_A:
            if group in params:
                leg_keys = LEG_GROUPS[group]
                smooth_move_leg(leg_keys, params[group].get("push_back", params[group].get("stance", {})), actual_steps, actual_delay)

        gait_step_count += 1

    # Return to stance
    for group in list(GROUP_A) + list(GROUP_B):
        if group in params:
            smooth_move_leg(LEG_GROUPS[group], params[group].get("stance", {}), actual_steps, actual_delay)

    gait_running = False
    gait_event.clear()





# ════════════════════════════════════════════════════════════════
# ── Balance System (من spider.balance) ──
# ════════════════════════════════════════════════════════════════
from spider.balance import BalanceController, SimplePID, BALANCE_WEIGHTS, LEG_FEMUR_CHANNEL


# ── إنشاء الـ balance controller (مع حقن dependencies) ──
balance = BalanceController()
balance.configure(
    set_servo_fn=lambda ch, angle: set_servo(ch, angle),
    current_ref=current,
    gait_event_ref=gait_event,
)


# ── Routes ──
@app.route("/")
def main_page():
    return render_template("main.html")

@app.route("/motors")
def index():
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def get_status():
    defaults = load_leg_defaults()
    status = {}
    for key in current:
        group = None
        for g, keys in LEG_GROUPS.items():
            if key in keys:
                group = g
                break
        base_default = (DEFAULT_RIGHT if key[0] == "R" else DEFAULT_LEFT).get(int(key[1:]), 90)
        # Check if user has overridden default for this leg
        custom_default = None
        if group and group in defaults:
            custom_default = defaults[group].get(key)
        final_default = custom_default if custom_default is not None else base_default
        status[key] = {
            "angle": current[key],
            "name": SERVO_NAMES.get(key, key),
            "default": final_default
        }
    return jsonify({
        "servos": status,
        "hardware": HARDWARE,
        "min": MIN_ANGLE,
        "max": MAX_ANGLE,
        "leg_groups": LEG_GROUPS,
        "defaults": defaults
    })


@app.route("/api/servo", methods=["POST"])
def api_set_servo():
    data = request.json
    key = data.get("key", "")
    angle = data.get("angle")
    if key in current and angle is not None:
        a = set_servo(key, angle)
        return jsonify({"ok": True, "key": key, "angle": a})
    return jsonify({"ok": False, "error": "invalid"}), 400


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """تصفير _force_stop — طوارئ إذا ظل True"""
    global _force_stop
    _force_stop = False
    gait_event.clear()
    return jsonify({"ok": True, "message": "force_stop cleared"})


@app.route("/api/health", methods=["GET"])
def api_health():
    """فحص سريع لحالة النظام"""
    return jsonify({
        "ok": True,
        "imu": "connected" if BNO_READY else "disconnected",
        "gait_running": gait_event.is_set(),
        "force_stop": _force_stop,
        "servos_initialized": _servos_initialized,
        "balance": balance.enabled
    })


@app.route("/api/calibrate", methods=["POST"])
def api_calibrate():
    defaults = load_leg_defaults()
    for ch in range(9):
        rk = f"R{ch}"
        lk = f"L{ch}"
        rv = defaults.get("RF", {}).get(rk) if rk in LEG_GROUPS.get("RF", []) else None
        lv = defaults.get("LF", {}).get(lk) if lk in LEG_GROUPS.get("LF", []) else None
        # Find the right group for each channel
        r_default = DEFAULT_RIGHT[ch]
        l_default = DEFAULT_LEFT[ch]
        for g, keys in LEG_GROUPS.items():
            if rk in keys and g in defaults:
                r_default = defaults[g][rk]
            if lk in keys and g in defaults:
                l_default = defaults[g][lk]
        set_servo(rk, r_default)
        set_servo(lk, l_default)
    return jsonify({"ok": True, "msg": "Default calibration applied"})


@app.route("/api/off", methods=["POST"])
def api_off():
    """إطفاء نظيف: وقوف على _STAND (مفاتيح صحيحة) ثم تصفير PWM."""
    global gait_running
    _sync_force_stop(True)
    gait_running = False
    gait_event.clear()
    time.sleep(0.1)
    _sync_force_stop(False)

    if HARDWARE:
        try:
            # ارجع لوضعية الوقوف الصحيحة عبر _STAND (R0..R8, L0..L8 — مفاتيح صحيحة)
            smooth_move_leg(list(_STAND.keys()), _STAND, steps=8, delay=0.03)
            time.sleep(0.1)
        except Exception:
            pass
        try:
            # صفّر PWM — ترخية السيرفوات
            from spider.hardware import pwm_release_all
            pwm_release_all()
        except Exception:
            pass
    return jsonify({"ok": True, "msg": "Stance then servos off"})


# ── الإيقاف الطارئ البرمجي ──
@app.route("/api/estop", methods=["POST"])
def api_estop():
    """إيقاف طارئ فوري: يوقف كل threads الحركة + يصفّر PWM (ترخية السيرفوات)."""
    global gait_running, _force_stop
    _force_stop = True
    gait_running = False
    gait_event.clear()          # أوقف حلقات المشي
    balance.stop()              # أوقف التوازن لو شغّال
    arbiter.emergency_stop()    # يصفّر PWM + يفرّغ الأهداف + يمنع أي كتابة جديدة
    return jsonify({"ok": True, "estop": True})


@app.route("/api/estop/clear", methods=["POST"])
def api_estop_clear():
    """إلغاء الطوارئ: يعيد المحكّم للعمل بهدف=الوضع الحالي (لا حركة مفاجئة)."""
    global _force_stop
    _force_stop = False
    arbiter.clear_estop()
    return jsonify({"ok": True, "estop": False})


# ── Per-Leg Default Routes ──
@app.route("/api/leg/defaults", methods=["GET"])
def api_leg_defaults():
    return jsonify({"defaults": load_leg_defaults()})


@app.route("/api/leg/default/apply", methods=["POST"])
def api_leg_default_apply():
    """Reset one leg group to its default values."""
    data = request.json
    group = data.get("group", "")
    if group not in LEG_GROUPS:
        return jsonify({"ok": False, "error": "invalid group"}), 400
    defaults = load_leg_defaults()
    for key in LEG_GROUPS[group]:
        val = DEFAULT_RIGHT[int(key[1:])] if key[0] == "R" else DEFAULT_LEFT[int(key[1:])]
        if group in defaults and key in defaults[group]:
            val = defaults[group][key]
        set_servo(key, val)
    return jsonify({"ok": True, "msg": f"Group {group} reset to default"})


@app.route("/api/leg/default/set", methods=["POST"])
def api_leg_default_set():
    """Set current position of one leg group as new default."""
    data = request.json
    group = data.get("group", "")
    if group not in LEG_GROUPS:
        return jsonify({"ok": False, "error": "invalid group"}), 400
    defaults = load_leg_defaults()
    defaults[group] = {key: current[key] for key in LEG_GROUPS[group]}
    save_leg_defaults(defaults)
    return jsonify({"ok": True, "msg": f"Group {group} default saved", "values": defaults[group]})


# ── Per-Leg Preset Routes (1, 2, 3) ──
@app.route("/api/leg/presets", methods=["GET"])
def api_leg_presets():
    return jsonify({"presets": load_leg_presets()})


@app.route("/api/leg/preset/save", methods=["POST"])
def api_leg_preset_save():
    """Save current position of a leg group into preset slot (1-3)."""
    data = request.json
    group = data.get("group", "")
    slot = str(data.get("slot", ""))
    if group not in LEG_GROUPS or slot not in ("1", "2", "3"):
        return jsonify({"ok": False, "error": "invalid group or slot"}), 400
    presets = load_leg_presets()
    if group not in presets:
        presets[group] = {}
    presets[group][slot] = {key: current[key] for key in LEG_GROUPS[group]}
    save_leg_presets(presets)
    return jsonify({"ok": True, "msg": f"Group {group} preset {slot} saved", "values": presets[group][slot]})


@app.route("/api/leg/preset/load", methods=["POST"])
def api_leg_preset_load():
    """Load a preset for a leg group."""
    data = request.json
    group = data.get("group", "")
    slot = str(data.get("slot", ""))
    if group not in LEG_GROUPS or slot not in ("1", "2", "3"):
        return jsonify({"ok": False, "error": "invalid group or slot"}), 400
    presets = load_leg_presets()
    if group not in presets or slot not in presets[group]:
        return jsonify({"ok": False, "error": "preset not found"}), 404
    for key, angle in presets[group][slot].items():
        if key in current:
            set_servo(key, angle)
    return jsonify({"ok": True, "msg": f"Group {group} preset {slot} loaded"})


# ── Global Positions ──
@app.route("/api/positions", methods=["GET"])
def api_list_positions():
    return jsonify({"positions": load_positions()})


@app.route("/api/positions/save", methods=["POST"])
def api_save_position():
    data = request.json
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    positions = load_positions()
    positions[name] = dict(current)
    save_positions(positions)
    return jsonify({"ok": True, "msg": f"Position '{name}' saved"})


@app.route("/api/positions/load", methods=["POST"])
def api_load_position():
    data = request.json
    name = data.get("name", "").strip()
    positions = load_positions()
    if name not in positions:
        return jsonify({"ok": False, "error": "not found"}), 404
    pos = positions[name]
    for key, angle in pos.items():
        if key in current:
            set_servo(key, angle)
    return jsonify({"ok": True, "msg": f"Position '{name}' loaded"})


@app.route("/api/positions/delete", methods=["POST"])
def api_delete_position():
    data = request.json
    name = data.get("name", "").strip()
    positions = load_positions()
    if name in positions:
        del positions[name]
        save_positions(positions)
        return jsonify({"ok": True, "msg": f"Position '{name}' deleted"})
    return jsonify({"ok": False, "error": "not found"}), 404


# ── Easing Routes ──
@app.route('/api/easing/set', methods=['POST'])
def set_easing():
    global _ease_func
    data = request.json or {}
    name = data.get("type", "smoothstep")
    if name == "linear":
        _ease_func = lambda t: t
    elif name == "cosine":
        _ease_func = ease_cosine
    elif name == "smoothstep":
        _ease_func = ease_smoothstep
    else:
        return jsonify({"error": f"Unknown easing: {name}"}), 400
    return jsonify({"easing": name})

@app.route('/api/easing/get', methods=['GET'])
def get_easing():
    name = _ease_func.__name__ if hasattr(_ease_func, '__name__') else "custom"
    return jsonify({"easing": name})


# ── Balance Routes ──
@app.route('/api/balance/start', methods=['POST'])
def balance_start():
    data = request.json or {}
    kp = data.get("kp")
    ki = data.get("ki")
    kd = data.get("kd")
    if any(v is not None for v in [kp, ki, kd]):
        balance.tune(kp=kp, ki=ki, kd=kd)
    balance.start()
    return jsonify({"status": "started", **balance.get_status()})

@app.route('/api/balance/stop', methods=['POST'])
def balance_stop():
    balance.stop()
    return jsonify({"status": "stopped"})

@app.route('/api/balance/status', methods=['GET'])
def balance_status():
    return jsonify(balance.get_status())

@app.route('/api/balance/tune', methods=['POST'])
def balance_tune():
    data = request.json or {}
    balance.tune(
        kp=data.get("kp"),
        ki=data.get("ki"),
        kd=data.get("kd"),
        axis=data.get("axis", "both")
    )
    return jsonify({"status": "tuned", **balance.get_status()})

@app.route('/api/balance/debug', methods=['GET'])
def balance_debug():
    return jsonify({"history": balance.debug_history})


# ── BNO / IMU Routes ──
@app.route("/api/imu/status", methods=["GET"])
def api_imu_status():
    return jsonify({"ready": BNO_READY})


@app.route("/api/imu/read", methods=["GET"])
def api_imu_read():
    reading = read_bno_single()
    if reading is None:
        return jsonify({"ok": False, "error": "BNO not available"})
    return jsonify({"ok": True, **reading, "zero_roll": bno_zero_roll, "zero_pitch": bno_zero_pitch})


@app.route("/api/imu/zero", methods=["POST"])
def api_imu_zero():
    global bno_zero_roll, bno_zero_pitch
    rolls, pitches = [], []
    for _ in range(20):
        r = read_bno_single()  # بياخد bno_lock داخلياً
        if r:
            rolls.append(r["raw_roll"])
            pitches.append(r["raw_pitch"])
        time.sleep(0.06)
    if not rolls:
        return jsonify({"ok": False, "error": "Cannot read BNO"})
    bno_zero_roll = sum(rolls) / len(rolls)
    bno_zero_pitch = sum(pitches) / len(pitches)
    return jsonify({
        "ok": True,
        "zero_roll": round(bno_zero_roll, 2),
        "zero_pitch": round(bno_zero_pitch, 2)
    })


@app.route("/api/imu/stream", methods=["GET"])
def api_imu_stream():
    count = int(request.args.get("count", 30))
    samples = []
    for _ in range(count):
        r = read_bno_single()  # بياخد bno_lock داخلياً
        if r:
            r["t"] = round(time.time() * 1000)
            samples.append(r)
        time.sleep(0.05)
    return jsonify({"ok": True, "samples": samples, "zero_roll": bno_zero_roll, "zero_pitch": bno_zero_pitch})


# ── Gait Planner Routes ──
@app.route("/gait")
def gait_page():
    return render_template("spider_gait_controller.html")

@app.route("/sim")
def spider_sim():
    return render_template("spider_sim.html")


@app.route("/api/gait/params", methods=["GET"])
def api_gait_params():
    """Load all saved gait parameters"""
    return jsonify({"plans": load_json(GAIT_FILE)})


@app.route("/api/gait/params/save", methods=["POST"])
def api_gait_params_save():
    """Save gait parameters as a named plan"""
    data = request.json
    name = data.get("name", "").strip()
    params = data.get("params", {})
    if not name or not params:
        return jsonify({"ok": False, "error": "name and params required"}), 400
    plans = load_json(GAIT_FILE)
    plans[name] = {"params": params}
    save_json(GAIT_FILE, plans)
    return jsonify({"ok": True, "msg": f"Plan '{name}' saved"})


@app.route("/api/gait/params/load", methods=["POST"])
def api_gait_params_load():
    """Load a named gait plan"""
    data = request.json
    name = data.get("name", "").strip()
    plans = load_json(GAIT_FILE)
    if name not in plans:
        return jsonify({"ok": False, "error": "Plan not found"}), 404
    return jsonify({"ok": True, "name": name, "params": plans[name]["params"]})


@app.route("/api/gait/plans", methods=["GET"])
def api_gait_plans():
    """List all saved plan names"""
    plans = load_json(GAIT_FILE)
    return jsonify({"ok": True, "plans": list(plans.keys())})


@app.route("/api/gait/params/delete", methods=["POST"])
def api_gait_params_delete():
    """Delete a named gait plan"""
    data = request.json
    name = data.get("name", "").strip()
    plans = load_json(GAIT_FILE)
    if name in plans:
        del plans[name]
        save_json(GAIT_FILE, plans)
        return jsonify({"ok": True, "msg": f"Plan '{name}' deleted"})
    return jsonify({"ok": False, "error": "Plan not found"}), 404


@app.route("/api/gait/set-leg-pose", methods=["POST"])
def api_gait_set_leg_pose():
    """Apply a specific pose to a leg immediately (for testing)"""
    data = request.json
    group = data.get("group", "")
    pose = data.get("pose", {})
    if group not in LEG_GROUPS:
        return jsonify({"ok": False, "error": "invalid group"}), 400
    if not pose:
        return jsonify({"ok": False, "error": "pose required"}), 400
    leg_keys = LEG_GROUPS[group]
    smooth_move_leg(leg_keys, pose)
    return jsonify({"ok": True, "msg": f"Group {group} pose applied"})


@app.route("/api/gait/auto-start", methods=["POST"])
def api_gait_auto_start():
    """Start auto walking with a named plan"""
    global gait_running, gait_thread
    if gait_event.is_set():
        return jsonify({"ok": False, "error": "Already running"})
    data = request.json
    name = data.get("name", "")
    speed = float(data.get("speed", 1.0))
    max_steps = int(data.get("steps", 0))

    plans = load_json(GAIT_FILE)
    if name not in plans:
        return jsonify({"ok": False, "error": "Plan not found"})

    gait_running = True
    gait_event.set()
    gait_thread = threading.Thread(target=execute_gait, args=(plans[name]["params"], speed, max_steps), daemon=True)
    gait_thread.start()
    return jsonify({"ok": True, "msg": "Gait started"})


@app.route("/api/gait/auto-stop", methods=["POST"])
def api_gait_auto_stop():
    """Stop auto walking immediately"""
    global gait_running
    gait_running = False
    gait_event.clear()
    # Wait briefly for thread to finish current moves
    time.sleep(0.1)
    return jsonify({"ok": True, "msg": "Gait stopped"})


@app.route("/api/gait/status", methods=["GET"])
def api_gait_status():
    """Is gait running?"""
    return jsonify({
        "running": gait_running,
        "step_count": gait_step_count,
        "current_phase": gait_phase
    })





@app.route("/api/gait/ripple/start", methods=["POST"])
def ripple_gait_start():
    """تشغيل Ripple Gait — مشي العنكبوت المتناغم."""
    global gait_running, gait_thread, gait_step_count

    if gait_event.is_set():
        return jsonify({"ok": False, "error": "already running"})

    data = request.json or {}
    speed = float(data.get("speed", 1.0))
    max_cycles = int(data.get("cycles", 0))
    direction = data.get("direction", "forward")

    params = _load_ripple_params()
    if not params:
        return jsonify({"ok": False, "error": "ripple_gait_params.json not found"})

    gait_running = True
    gait_event.set()
    gait_step_count = 0
    gait_current_phase = "ripple_starting"

    if direction in ('strafe_left', 'strafe_right'):
        lat_dir = 'left' if direction == 'strafe_left' else 'right'
        tripod_params = _load_gait_params("forward_tripod")
        gait_thread = threading.Thread(
            target=_run_lateral_gait,
            args=(tripod_params or params, speed, max_cycles, lat_dir),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    else:
        gait_thread = threading.Thread(
            target=_run_ripple_gait,
            args=(params, speed, max_cycles, direction),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    gait_thread.start()
    return jsonify({"ok": True, "status": "ripple started", "speed": speed, "direction": direction})


@app.route("/api/gait/ripple/stop", methods=["POST"])
def ripple_gait_stop():
    """إيقاف Ripple Gait."""
    global gait_running
    gait_running = False
    gait_event.clear()
    return jsonify({"ok": True, "status": "ripple stopping..."})


@app.route("/api/gait/ripple/status", methods=["GET"])
def ripple_gait_status():
    """حالة Ripple Gait."""
    return jsonify({
        "running": gait_running,
        "step_count": gait_step_count,
        "current_phase": gait_current_phase
    })


# ── Forward Tripod Gait Endpoints ──
@app.route("/api/gait/forward/start", methods=["POST"])
def gait_forward_start():
    global gait_running, gait_thread, gait_step_count

    if gait_event.is_set():
        return jsonify({"ok": False, "error": "already running"})

    data = request.json or {}
    speed = float(data.get("speed", 1.0))
    max_cycles = int(data.get("cycles", 0))
    gait_type = data.get("type", "forward")

    params = _load_gait_params("forward_tripod")
    if not params:
        return jsonify({"ok": False, "error": "gait_params.json not found or invalid"})

    gait_running = True
    gait_event.set()
    gait_step_count = 0
    gait_current_phase = "starting"

    lateral_types = {
        'shift_left': 'left', 'shift_right': 'right',
        'strafe_left': 'left', 'strafe_right': 'right',
        'crab_walk': 'left',
    }

    if gait_type in lateral_types:
        gait_thread = threading.Thread(
            target=_run_lateral_gait,
            args=(params, speed, max_cycles, lateral_types[gait_type]),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    else:
        gait_thread = threading.Thread(
            target=_run_forward_gait,
            args=(params, speed, max_cycles, gait_type),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    gait_thread.start()
    return jsonify({"ok": True, "status": "forward started", "speed": speed, "type": gait_type})


@app.route("/api/gait/forward/stop", methods=["POST"])
def gait_forward_stop():
    global gait_running
    gait_running = False
    gait_event.clear()
    return jsonify({"ok": True, "status": "stopping..."})


@app.route("/api/gait/forward/status", methods=["GET"])
def gait_forward_status():
    return jsonify({
        "running": gait_running,
        "step_count": gait_step_count,
        "current_phase": gait_current_phase
    })


# ── Gait Once Endpoint (N cycles then stop) ──
@app.route("/api/gait/once", methods=["POST"])
def gait_once():
    """ينفذ N دورة من نوع معين ثم يوقف."""
    data      = request.json or {}
    gait_type = data.get('type', 'rotate_cw')
    speed     = float(data.get('speed', 0.8))
    cycles    = int(data.get('cycles', 4))   # 4 دورات ≈ 90°

    params = _load_gait_params('forward_tripod')
    if not params:
        return jsonify({'ok': False, 'error': 'params not found'})

    global gait_running, gait_thread
    if gait_event.is_set():
        return jsonify({'ok': False, 'error': 'already running'})

    gait_running = True
    gait_event.set()

    lateral_types = {
        'shift_left': 'left', 'shift_right': 'right',
        'strafe_left': 'left', 'strafe_right': 'right',
        'crab_walk': 'left',
    }

    if gait_type in lateral_types:
        gait_thread = threading.Thread(
            target=_run_lateral_gait,
            args=(params, speed, cycles, lateral_types[gait_type]),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    else:
        gait_thread = threading.Thread(
            target=_run_forward_gait,
            args=(params, speed, cycles, gait_type),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    gait_thread.start()
    return jsonify({'ok': True, 'type': gait_type, 'cycles': cycles})


# ── وضعية الوقوف الأساسية (من DEFAULT_RIGHT/LEFT) ──
_STAND = {}
for ch, angle in DEFAULT_RIGHT.items():
    _STAND[f"R{ch}"] = angle
for ch, angle in DEFAULT_LEFT.items():
    _STAND[f"L{ch}"] = angle


# ── Body Move Endpoint (محمي بالتحكيم — ثغرة #1) ──
@app.route("/api/body/move", methods=["POST"])
def body_move():
    """يحرّك الجسم بتغيير Femur أو Coxa — الأرجل تبقى على الأرض."""
    from spider.moves import get_body_targets, run_body
    data  = request.json or {}
    move  = data.get('move', 'stand')
    speed = float(data.get('speed', 1.0))

    targets_raw = get_body_targets(move)
    if targets_raw is None:
        return jsonify({'ok': False, 'error': f'unknown move: {move}'})

    targets = {k: max(45, min(135, v)) for k, v in targets_raw.items()}
    steps   = max(4, int(10 / speed))
    delay   = max(0.01, 0.03 / speed)

    if not run_body(move, targets, steps, delay):
        return jsonify({'ok': False, 'error': 'busy — أوقف المشي أولاً'}), 409
    return jsonify({'ok': True, 'move': move})


# ── Special Move Functions ──────────────────────────────




# ── Special Move Endpoint (محمي بالتحكيم — ثغرة #1) ──
@app.route("/api/special/<move_name>", methods=["POST"])
def special_move(move_name):
    from spider.moves import SPECIALS, run_special
    data  = request.json or {}
    speed = float(data.get('speed', 1.0))

    fn = SPECIALS.get(move_name)
    if not fn:
        return jsonify({'ok': False, 'error': f'unknown special: {move_name}'}), 404

    if not run_special(move_name, fn, speed):
        return jsonify({'ok': False, 'error': 'busy — أوقف المشي أولاً'}), 409
    return jsonify({'ok': True, 'move': move_name})


# ════════════════════════════════════════════════════════════════



@app.route('/api/gait/smooth-ripple/start', methods=['POST'])
def smooth_ripple_start():
    """تشغيل Smooth Ripple Gait — Foot Trajectory + Tibia ديناميكي."""
    global gait_running, gait_thread, gait_step_count

    if gait_event.is_set():
        return jsonify({"ok": False, "error": "already running"})

    data = request.json or {}
    speed = float(data.get("speed", 1.0))
    max_cycles = int(data.get("cycles", 0))
    direction = data.get("direction", "forward")

    params = _load_ripple_params()
    if not params:
        return jsonify({"ok": False, "error": "ripple_gait_params.json not found"})

    gait_running = True
    gait_event.set()
    gait_step_count = 0
    gait_current_phase = "smooth_ripple_starting"

    if direction in ('strafe_left', 'strafe_right'):
        lat_dir = 'left' if direction == 'strafe_left' else 'right'
        tripod_params = _load_gait_params("forward_tripod")
        gait_thread = threading.Thread(
            target=_run_lateral_gait,
            args=(tripod_params or params, speed, max_cycles, lat_dir),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    else:
        gait_thread = threading.Thread(
            target=_run_smooth_ripple,
            args=(params, speed, max_cycles, direction),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    gait_thread.start()
    return jsonify({"ok": True, "status": "smooth ripple started", "speed": speed, "direction": direction})


@app.route('/api/gait/smooth-ripple/stop', methods=['POST'])
def smooth_ripple_stop():
    """إيقاف Smooth Ripple Gait."""
    global gait_running
    gait_running = False
    gait_event.clear()
    return jsonify({"ok": True, "status": "smooth ripple stopping..."})

# ── Live Tuning and Config Management (Plan 04) ──
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

def _cfg_path(name):
    allowed = ["servo_limits", "gait_params", "ripple_gait_params", "balance_config", "motion"]
    if name not in allowed:
        return None
    return os.path.join(CONFIG_DIR, name + ".json")

def _reload_config(name):
    """إعادة تحميل حيّ بلا إعادة تشغيل السيرفر."""
    if name == "servo_limits":
        from spider import config as _c
        _c.reload()
    elif name == "balance_config":
        balance.reload()
    elif name == "motion":
        from spider import safety, gaits, moves
        safety.reload_motion()
        gaits.reload_motion()
        moves.reload_motion()
    # gait_params/ripple تُقرأ عند بدء كل مشي، فلا تحتاج reload خاص

@app.route("/api/config/<name>", methods=["GET"])
def config_get(name):
    p = _cfg_path(name)
    if not p or not os.path.exists(p):
        return jsonify({"ok": False, "error": f"Config {name} not found"}), 404
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/config/<name>", methods=["POST"])
def config_set(name):
    import shutil
    p = _cfg_path(name)
    if not p or not os.path.exists(p):
        return jsonify({"ok": False, "error": f"Config {name} not found"}), 404
    try:
        # نسخة احتياطية قبل الكتابة
        shutil.copy(p, p + ".bak")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(request.json, f, indent=2, ensure_ascii=False)
        _reload_config(name)
        return jsonify({"ok": True, "saved": name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/config/<name>/restore", methods=["POST"])
def config_restore(name):
    import shutil
    p = _cfg_path(name)
    if not p:
        return jsonify({"ok": False, "error": "Invalid config name"}), 400
    bak = p + ".bak"
    if os.path.exists(bak):
        try:
            shutil.copy(bak, p)
            _reload_config(name)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": False, "error": "no backup found"}), 404

@app.route("/api/limit/nudge", methods=["POST"])
def limit_nudge():
    """حرّك محركاً واحداً لزاوية (للمعايرة بالملاحظة). يتجاوز حدوده مؤقتاً بحذر."""
    d = request.json
    key = d.get("key")
    angle = d.get("angle")
    if key is None or angle is None:
        return jsonify({"ok": False, "error": "Missing key or angle"}), 400
    
    # حماية التحكيم
    if not arbiter.acquire("limit_tune"):
        return jsonify({"ok": False, "error": "Arbiter busy"}), 409
        
    try:
        angle = int(angle)
        # كتابة مباشرة للمحرك دون حواجز الحدود لأننا بصدد المعايرة
        from spider.hardware import write_batch_raw
        write_batch_raw({key: angle})
        # وتحديث current لتسجيل الحالة الحالية
        from spider.safety import current
        current[key] = angle
        return jsonify({"ok": True, "key": key, "angle": angle})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        arbiter.release("limit_tune")

@app.route("/api/limit/set", methods=["POST"])
def limit_set():
    """يحفظ حدّاً آمناً لمحرك في servo_limits.json."""
    d = request.json
    key = d.get("key")
    mn = d.get("min")
    mx = d.get("max")
    if key is None or mn is None or mx is None:
        return jsonify({"ok": False, "error": "Missing parameters"}), 400
        
    p = _cfg_path("servo_limits")
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "overrides" not in data:
            data["overrides"] = {}
        data["overrides"][key] = {"min": int(mn), "max": int(mx)}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        from spider import config as _c
        _c.reload()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── startup IMU zero ──
def _startup_imu_zero():
    """يصفّر الـ IMU عند بداية التشغيل"""
    if not BNO_READY:
        return
    print("Zeroing IMU...")
    rolls, pitches = [], []
    for _ in range(20):
        r = read_bno_single()  # تستخدم bno_lock داخلياً — ما نستخدم with هنا
        if r:
            rolls.append(r["raw_roll"])
            pitches.append(r["raw_pitch"])
        time.sleep(0.06)
    if rolls:
        global bno_zero_roll, bno_zero_pitch
        bno_zero_roll = sum(rolls) / len(rolls)
        bno_zero_pitch = sum(pitches) / len(pitches)
        print(f"IMU zeroed: roll={bno_zero_roll:.2f} pitch={bno_zero_pitch:.2f}")


if __name__ == "__main__":
    print(f"Hardware: {'CONNECTED' if HARDWARE else 'SIMULATION MODE'}")
    print(f"IMU (BNO085): {'READY' if BNO_READY else 'NOT FOUND'}")
    _startup_imu_zero()
    apply_startup_calibration()  # ← المحركات تشتغل بالبداية زي ما كانت
    print("Spider Web Controller → http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
