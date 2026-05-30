# spider/moves.py
"""الحركات الاستعراضية (23 حركة) + body_move مع حماية التحكيم."""
import math
import threading
import time
import os
import json

from spider.safety import arbiter
from spider.constants import _STAND

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
MOTION_FILE = os.path.join(CONFIG_DIR, "motion.json")

_motion = {}

def reload_motion():
    global _motion
    try:
        if os.path.exists(MOTION_FILE):
            with open(MOTION_FILE, "r", encoding="utf-8") as f:
                _motion = json.load(f)
    except Exception as e:
        pass

# التحميل المبدئي
reload_motion()


# ────── مساعد تشغيل الحركات بأمان ──────
def run_special(move_name, fn, speed):
    """يشغّل حركة استعراضية بأمان: يحجز الملكية، وإلا يرفض لو مشغول."""
    if not arbiter.acquire("special"):
        return False   # مشي/توازن شغّال — ارفض

    def _wrap():
        try:
            fn(speed)
        finally:
            # ارجع للوقوف ثم حرّر
            _smooth_to_stand()
            arbiter.release("special")

    threading.Thread(target=_wrap, daemon=True).start()
    return True


def run_body(move_name, targets, steps, delay):
    """يشغّل body_move بأمان مع حجز الملكية."""
    if not arbiter.acquire("body"):
        return False

    def _wrap():
        try:
            _smooth_move(list(targets.keys()), targets, steps=steps, delay=delay)
        finally:
            arbiter.release("body")

    threading.Thread(target=_wrap, daemon=True).start()
    return True


# ────── مساعد الحركة السلسة (نسخة محلية مستقلة) ──────
def _smooth_move(keys, targets, steps=10, delay=0.03):
    """تحريك سلس للسيرفوات — تستخدم فقط داخل هذا الملف."""
    from spider.safety import current, _force_stop_ref
    starts = {k: current[k] for k in keys}
    for step in range(1, steps + 1):
        if _force_stop_ref():
            return
        t = step / steps
        t_eased = t * t * (3 - 2 * t)   # smoothstep
        batch = {}
        for k in keys:
            if k in targets:
                angle = starts[k] + (targets[k] - starts[k]) * t_eased
                batch[k] = round(angle)
        arbiter.set_target(arbiter.owner(), batch)
        time.sleep(delay)


def _smooth_to_stand(steps=10, delay=0.03):
    _smooth_move(list(_STAND.keys()), _STAND, steps=steps, delay=delay)


# ────── body_move logic ──────

def get_body_targets(move, base=None):
    """يعيد dict الأهداف لحركة جسم معينة."""
    delta = _motion.get("body", {}).get("delta", 12)
    b = base or dict(_STAND)
    moves = {
        'body_up':      {k: b[k] - delta if k[1] in ('1','4','7') else b[k] for k in b},
        'body_down':    {k: b[k] + delta if k[1] in ('1','4','7') else b[k] for k in b},
        'lean_forward': {**b,
                         'R1': b['R1']+delta, 'L1': b['L1']+delta,
                         'R7': b['R7']-delta, 'L7': b['L7']-delta},
        'lean_back':    {**b,
                         'R1': b['R1']-delta, 'L1': b['L1']-delta,
                         'R7': b['R7']+delta, 'L7': b['L7']+delta},
        'lean_left':    {**b,
                         'L1': b['L1']+delta, 'L4': b['L4']+delta, 'L7': b['L7']+delta,
                         'R1': b['R1']-delta, 'R4': b['R4']-delta, 'R7': b['R7']-delta},
        'lean_right':   {**b,
                         'R1': b['R1']+delta, 'R4': b['R4']+delta, 'R7': b['R7']+delta,
                         'L1': b['L1']-delta, 'L4': b['L4']-delta, 'L7': b['L7']-delta},
        'twist_left':   {**b,
                         'R0': b['R0']+delta, 'L0': b['L0']-delta,
                         'R6': b['R6']-delta, 'L6': b['L6']+delta},
        'twist_right':  {**b,
                         'R0': b['R0']-delta, 'L0': b['L0']+delta,
                         'R6': b['R6']+delta, 'L6': b['L6']-delta},
        'stand':        dict(b),
    }
    return moves.get(move)


# ────── الـ 23 حركة استعراضية ──────

def _move_wave(speed=1.0):
    """تلويح — RF ترتفع وتتأرجح يمين يسار 3 مرات."""
    s  = max(4, int(8 / speed))
    dl = max(0.01, 0.02 / speed)
    _smooth_move(['R0','R1','R2'], {'R0':90,'R1':119,'R2':101}, steps=s, delay=dl)
    for _ in range(3):
        _smooth_move(['R0'], {'R0':115}, steps=s, delay=dl)
        _smooth_move(['R0'], {'R0':65},  steps=s, delay=dl)
    _smooth_move(['R0','R1','R2'], _STAND, steps=s, delay=dl)


def _move_dance(speed=1.0):
    """رقص — تأرجح الجسم يمين/يسار مع رفع أرجل متناوبة."""
    s  = max(3, int(6 / speed))
    dl = max(0.01, 0.02 / speed)
    for _ in range(2):
        _smooth_move(
            ['R1','R4','R7','L1','L4','L7','R0','R1'],
            {'R1':77,'R4':67,'R7':75,'L1':44,'L4':64,'L7':54,'R0':110},
            steps=s, delay=dl)
        time.sleep(0.1)
        _smooth_move(
            ['R1','R4','R7','L1','L4','L7','L0','L1'],
            {'R1':57,'R4':47,'R7':55,'L1':64,'L4':84,'L7':74,'L0':70},
            steps=s, delay=dl)
        time.sleep(0.1)
    _smooth_to_stand(steps=8, delay=0.03)


def _move_shake(speed=1.0):
    """مصافحة — RF تمتد للأمام وتهتز."""
    s  = max(4, int(8 / speed))
    dl = max(0.01, 0.02 / speed)
    _smooth_move(['R0','R1','R2'], {'R0':125,'R1':90,'R2':70}, steps=s, delay=dl)
    for _ in range(3):
        _smooth_move(['R1'], {'R1':100}, steps=3, delay=0.02)
        _smooth_move(['R1'], {'R1':80},  steps=3, delay=0.02)
    _smooth_move(['R0','R1','R2'], _STAND, steps=s, delay=dl)


def _move_salute(speed=1.0):
    """تحية — RF ترتفع وتلمس الجانب الأيمن للجسم."""
    s  = max(5, int(10 / speed))
    dl = max(0.01, 0.03 / speed)
    _smooth_move(['R0','R1','R2'], {'R0':60,'R1':125,'R2':130}, steps=s, delay=dl)
    time.sleep(0.5 / speed)
    _smooth_move(['R0','R1','R2'], _STAND, steps=s, delay=dl)


def _move_roar(speed=1.0):
    """تهديد — كل الأرجل الأمامية ترتفع والجسم ينخفض."""
    s  = max(4, int(8 / speed))
    dl = max(0.01, 0.02 / speed)
    low = {k: v-10 if k in ('R1','R4','R7','L1','L4','L7') else v for k,v in _STAND.items()}
    _smooth_move(list(low.keys()), low, steps=s, delay=dl)
    _smooth_move(['R0','R1','L0','L1'], {'R0':130,'R1':119,'L0':50,'L1':127}, steps=s, delay=dl)
    time.sleep(0.4 / speed)
    _smooth_to_stand(steps=8, delay=0.03)


def _move_spin(speed=1.0):
    """دوران كامل 360° — 8 دورات turn_right."""
    from spider.gaits import _load_gait_params, _run_forward_gait
    import threading
    params = _load_gait_params('forward_tripod')
    if params:
        _run_forward_gait(params, speed * 1.2, 8, 'turn_right')


def _move_bow(speed=1.0):
    """انحناء — الأرجل الأمامية ترفع الجسم، الخلفية تنخفض."""
    s  = max(5, int(10 / speed))
    dl = max(0.01, 0.03 / speed)
    bow = {**_STAND,
           'R1': _STAND['R1']+20, 'L1': _STAND['L1']+20,
           'R7': _STAND['R7']-15, 'L7': _STAND['L7']-15}
    _smooth_move(list(bow.keys()), bow, steps=s, delay=dl)
    time.sleep(0.6 / speed)
    _smooth_to_stand(steps=s, delay=dl)


def _move_stretch(speed=1.0):
    """تمدد — كل الأرجل تمتد للخارج إلى أقصى مدى."""
    s  = max(5, int(10 / speed))
    dl = max(0.01, 0.03 / speed)
    stretch = {**_STAND,
               'R0':70,'R3':70,'R6':70,
               'L0':110,'L3':110,'L6':110,
               'R1':_STAND['R1']-10,'R4':_STAND['R4']-10,'R7':_STAND['R7']-10,
               'L1':_STAND['L1']-10,'L4':_STAND['L4']-10,'L7':_STAND['L7']-10}
    _smooth_move(list(stretch.keys()), stretch, steps=s, delay=dl)
    time.sleep(0.5 / speed)
    _smooth_to_stand(steps=s, delay=dl)


def _move_idle_sway(speed=1.0):
    """تأرجح خفيف — حركة هادئة للاسترخاء (5 دورات)."""
    s  = max(6, int(12 / speed))
    dl = max(0.02, 0.04 / speed)
    for _ in range(5):
        sway_r = {**_STAND,
                  'R1':_STAND['R1']+8,'R4':_STAND['R4']+8,'R7':_STAND['R7']+8,
                  'L1':_STAND['L1']-8,'L4':_STAND['L4']-8,'L7':_STAND['L7']-8}
        sway_l = {**_STAND,
                  'L1':_STAND['L1']+8,'L4':_STAND['L4']+8,'L7':_STAND['L7']+8,
                  'R1':_STAND['R1']-8,'R4':_STAND['R4']-8,'R7':_STAND['R7']-8}
        _smooth_move(list(sway_r.keys()), sway_r, steps=s, delay=dl)
        _smooth_move(list(sway_l.keys()), sway_l, steps=s, delay=dl)
    _smooth_to_stand(steps=s, delay=dl)


def _move_wake_up(speed=1.0):
    """إيقاظ — ينزل ببطء من وضعية نوم لوضعية وقوف."""
    s  = max(8, int(15 / speed))
    dl = max(0.02, 0.04 / speed)
    sleep_pos = {k: v+40 if k[1] in ('1','4','7') else v for k,v in _STAND.items()}
    _smooth_move(list(_STAND.keys()), sleep_pos, steps=2, delay=0.01)
    _smooth_to_stand(steps=s, delay=dl)


def _move_sleep_pose(speed=1.0):
    """وضعية نوم — تنخفض الأرجل لينام الجسم على الأرض."""
    s  = max(8, int(15 / speed))
    dl = max(0.02, 0.04 / speed)
    sleep_pos = {k: v+40 if k[1] in ('1','4','7') else v for k,v in _STAND.items()}
    _smooth_move(list(sleep_pos.keys()), sleep_pos, steps=s, delay=dl)


def _move_flatten(speed=1.0):
    """تسطيح — الجسم ينزل ببطء شديد حتى يلامس الأرض (3 مراحل)."""
    s  = max(20, int(40 / speed))
    dl = max(0.03, 0.05 / speed)
    mid = {k: v+20 if k[1] in ('1','4','7') else v for k,v in _STAND.items()}
    _smooth_move(list(mid.keys()), mid, steps=s, delay=dl)
    low = {}
    for k, v in _STAND.items():
        if k[1] in ('1','4','7'):
            low[k] = v+40
        elif k[1] in ('0','3','6'):
            low[k] = max(55,v-15) if k[0]=='R' else min(125,v+15)
        else:
            low[k] = v
    _smooth_move(list(low.keys()), low, steps=s, delay=dl)
    flat = {}
    for k, v in _STAND.items():
        ch = k[1]
        if ch in ('1','4','7'):
            flat[k] = min(130, v+58)
        elif ch in ('0','3','6'):
            flat[k] = max(55,v-25) if k[0]=='R' else min(125,v+25)
        elif ch in ('2','5','8'):
            flat[k] = max(50, v-25)
        else:
            flat[k] = v
    _smooth_move(list(flat.keys()), flat, steps=s, delay=dl)


def _move_push_up(speed=1.0):
    """ضغط — 3 تمارين ضغط."""
    s_down = max(8, int(15 / speed))
    s_up   = max(6, int(10 / speed))
    dl     = max(0.02, 0.03 / speed)
    down   = {k: v+30 if k[1] in ('1','4','7') else v for k,v in _STAND.items()}
    for _ in range(3):
        _smooth_move(list(down.keys()), down, steps=s_down, delay=dl)
        time.sleep(0.15 / speed)
        _smooth_to_stand(steps=s_up, delay=dl)
        time.sleep(0.15 / speed)


def _move_tippy_toes(speed=1.0):
    """على الأطراف — الجسم يرتفع لأقصى ارتفاع."""
    s  = max(12, int(20 / speed))
    dl = max(0.02, 0.04 / speed)
    high = {}
    for k, v in _STAND.items():
        if k[1] in ('1','4','7'):
            high[k] = max(45, v-15)
        elif k[1] in ('2','5','8'):
            high[k] = max(50, v-10)
        else:
            high[k] = v
    _smooth_move(list(high.keys()), high, steps=s, delay=dl)
    time.sleep(1.0 / speed)
    _smooth_to_stand(steps=s, delay=dl)


def _move_look_around(speed=1.0):
    """تلفت — الجسم يدور ببطء يمين ويسار."""
    s  = max(8, int(15 / speed))
    dl = max(0.02, 0.04 / speed)
    look_l = {**_STAND,
              'R0':_STAND['R0']+20,'L0':_STAND['L0']-20,
              'R3':_STAND['R3']+10,'L3':_STAND['L3']-10,
              'R6':_STAND['R6']-15,'L6':_STAND['L6']+15}
    _smooth_move(list(look_l.keys()), look_l, steps=s, delay=dl)
    time.sleep(0.5 / speed)
    look_r = {**_STAND,
              'R0':_STAND['R0']-20,'L0':_STAND['L0']+20,
              'R3':_STAND['R3']-10,'L3':_STAND['L3']+10,
              'R6':_STAND['R6']+15,'L6':_STAND['L6']-15}
    _smooth_move(list(look_r.keys()), look_r, steps=s, delay=dl)
    time.sleep(0.5 / speed)
    _smooth_to_stand(steps=s, delay=dl)


def _move_pounce(speed=1.0):
    """انقضاض — ينخفض ويتحفز ثم يقفز للأمام."""
    dl = max(0.01, 0.03 / speed)
    crouch = {k: v+25 if k[1] in ('1','4','7') else v for k,v in _STAND.items()}
    _smooth_move(list(crouch.keys()), crouch, steps=max(10, int(18/speed)), delay=dl)
    time.sleep(0.4 / speed)
    spring = {**_STAND,
              'R0':120,'L0':60,
              'R1':max(45,_STAND['R1']-10),'L1':max(45,_STAND['L1']-10),
              'R7':max(45,_STAND['R7']-10),'L7':max(45,_STAND['L7']-10)}
    _smooth_move(list(spring.keys()), spring, steps=max(3, int(5/speed)), delay=0.01)
    time.sleep(0.3 / speed)
    _smooth_to_stand(steps=max(10, int(15/speed)), delay=dl)


def _move_stomp(speed=1.0):
    """دوس — ضرب الأرجل بالأرض بالتناوب."""
    s      = max(3, int(5 / speed))
    dl     = max(0.01, 0.02 / speed)
    legs_f = ['R1','L1','R7','L7','R4','L4']
    for fk in legs_f:
        _smooth_move([fk], {fk: _STAND[fk]+35}, steps=s, delay=dl)
        _smooth_move([fk], {fk: max(45, _STAND[fk]-8)}, steps=2, delay=0.008)
        time.sleep(0.04)
        _smooth_move([fk], {fk: _STAND[fk]}, steps=s, delay=dl)
    time.sleep(0.1)


def _move_scared(speed=1.0):
    """خوف — انسحاب سريع ثم انكماش ثم تعافي بطيء."""
    dl   = max(0.01, 0.02 / speed)
    jump = {}
    for k, v in _STAND.items():
        if k[1] in ('0','3','6'):
            jump[k] = max(55,v-20) if k[0]=='R' else min(125,v+20)
        else:
            jump[k] = v
    _smooth_move(list(jump.keys()), jump, steps=3, delay=0.01)
    curl = dict(jump)
    for k in curl:
        if k[1] in ('1','4','7'):
            curl[k] = _STAND[k]+30
    _smooth_move(list(curl.keys()), curl, steps=max(5, int(8/speed)), delay=dl)
    time.sleep(0.8 / speed)
    _smooth_to_stand(steps=max(12, int(20/speed)), delay=0.03)


def _move_dizzy(speed=1.0):
    """دوخة — تأرجح عشوائي كأن الروبوت فقد توازنه."""
    import random
    s  = max(4, int(7 / speed))
    dl = max(0.01, 0.02 / speed)
    for _ in range(6):
        w = {}
        for k, v in _STAND.items():
            if k[1] in ('1','4','7'):
                w[k] = max(50, min(130, v + random.randint(-10, 18)))
            elif k[1] in ('0','3','6'):
                w[k] = max(50, min(130, v + random.randint(-18, 18)))
            else:
                w[k] = v
        _smooth_move(list(w.keys()), w, steps=s, delay=dl)
    _smooth_to_stand(steps=8, delay=0.03)


def _move_wiggle(speed=1.0):
    """هز — الجزء الخلفي يتأرجح يمين ويسار."""
    s  = max(4, int(7 / speed))
    dl = max(0.01, 0.02 / speed)
    for _ in range(4):
        wr = {**_STAND,
              'R6':_STAND['R6']+20,'L6':_STAND['L6']-20,
              'R7':_STAND['R7']+8, 'L7':_STAND['L7']-8}
        wl = {**_STAND,
              'R6':_STAND['R6']-20,'L6':_STAND['L6']+20,
              'R7':_STAND['R7']-8, 'L7':_STAND['L7']+8}
        _smooth_move(list(wr.keys()), wr, steps=s, delay=dl)
        _smooth_move(list(wl.keys()), wl, steps=s, delay=dl)
    _smooth_to_stand(steps=s, delay=dl)


def _move_peek(speed=1.0):
    """تطلع — يميل للأمام ببطء ثم يتلفت يمين ويسار."""
    s  = max(10, int(18 / speed))
    dl = max(0.02, 0.04 / speed)
    peek = {**_STAND,
            'R0':_STAND['R0']+15,'L0':_STAND['L0']-15,
            'R1':_STAND['R1']+20,'L1':_STAND['L1']+20,
            'R7':max(45,_STAND['R7']-12),'L7':max(45,_STAND['L7']-12)}
    _smooth_move(list(peek.keys()), peek, steps=s, delay=dl)
    time.sleep(0.5 / speed)
    s2 = max(6, int(10 / speed))
    pr = dict(peek); pr['R0']=_STAND['R0']-10; pr['L0']=_STAND['L0']+10
    _smooth_move(list(pr.keys()), pr, steps=s2, delay=dl)
    time.sleep(0.3 / speed)
    pl = dict(peek); pl['R0']=_STAND['R0']+25; pl['L0']=_STAND['L0']-25
    _smooth_move(list(pl.keys()), pl, steps=s2, delay=dl)
    time.sleep(0.3 / speed)
    _smooth_to_stand(steps=s, delay=dl)


def _move_gallop(speed=1.0):
    """عدو — مشي سريع بخطوات كبيرة."""
    from spider.gaits import _load_gait_params, _run_forward_gait
    params = _load_gait_params('forward_tripod')
    if params:
        _run_forward_gait(params, speed * 2.0, 6, 'forward')


def _move_moonwalk(speed=1.0):
    """مون ووك — مشي خلفي أنيق مع تأرجح."""
    s  = max(5, int(8 / speed))
    dl = max(0.01, 0.02 / speed)
    for _ in range(3):
        sway_r = {**_STAND,
                  'R1':_STAND['R1']+6,'R4':_STAND['R4']+6,'R7':_STAND['R7']+6,
                  'L1':_STAND['L1']-6,'L4':_STAND['L4']-6,'L7':_STAND['L7']-6}
        _smooth_move(list(sway_r.keys()), sway_r, steps=s, delay=dl)
        sway_l = {**_STAND,
                  'L1':_STAND['L1']+6,'L4':_STAND['L4']+6,'L7':_STAND['L7']+6,
                  'R1':_STAND['R1']-6,'R4':_STAND['R4']-6,'R7':_STAND['R7']-6}
        _smooth_move(list(sway_l.keys()), sway_l, steps=s, delay=dl)
    _smooth_move(list(_STAND.keys()), _STAND, steps=4, delay=0.02)
    from spider.gaits import _load_gait_params, _run_forward_gait
    params = _load_gait_params('forward_tripod')
    if params:
        _run_forward_gait(params, speed * 0.6, 4, 'backward')


# ── فهرس الحركات ──
SPECIALS = {
    'wave':        _move_wave,
    'dance':       _move_dance,
    'shake':       _move_shake,
    'salute':      _move_salute,
    'roar':        _move_roar,
    'spin':        _move_spin,
    'bow':         _move_bow,
    'stretch':     _move_stretch,
    'idle_sway':   _move_idle_sway,
    'wake_up':     _move_wake_up,
    'sleep_pose':  _move_sleep_pose,
    'flatten':     _move_flatten,
    'push_up':     _move_push_up,
    'tippy_toes':  _move_tippy_toes,
    'look_around': _move_look_around,
    'pounce':      _move_pounce,
    'stomp':       _move_stomp,
    'scared':      _move_scared,
    'dizzy':       _move_dizzy,
    'wiggle':      _move_wiggle,
    'peek':        _move_peek,
    'gallop':      _move_gallop,
    'moonwalk':    _move_moonwalk,
}
