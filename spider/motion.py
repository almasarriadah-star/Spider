# spider/motion.py
"""مساعدات الحركة: الانتقال السلس والبث المستمر للأهداف."""
import time
from spider.safety import arbiter, current

def goto(owner, targets, timeout=4.0, tol=1.5):
    """يضع الهدف وينتظر وصوله (أو timeout). الحركة الفعلية يديرها MotionArbiter
    بسرعة آمنة. يُرجع True لو وصل."""
    if not arbiter.set_target(owner, targets):
        return False
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if arbiter.in_estop():
            return False
        # فحص مدى قرب جميع الزوايا من أهدافها
        if all(abs(current[k] - a) <= tol for k, a in targets.items() if k in current):
            return True
        time.sleep(0.02)
    return False

def stream(owner, target_fn, fps=25):
    """للمشي المستمر: يستدعي target_fn() للحصول على dict أهداف، ويبثّها للمحكم.
    target_fn يُرجع None لإنهاء الحركة."""
    period = 1.0 / fps
    while True:
        if arbiter.in_estop():
            return
        tgt = target_fn()
        if tgt is None:
            return
        arbiter.set_target(owner, tgt)
        time.sleep(period)
