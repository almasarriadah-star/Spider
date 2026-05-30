# spider/vision/weeds.py
"""كشف الأعشاب الضارة بمؤشّر ExG — بلا نموذج ML، يعمل فوراً."""
import numpy as np


def analyze_frame(bgr):
    """يحلّل إطار BGR ويعيد تقييم خطر بمؤشّر ExG (Excess Green).
    يرجع: {green_ratio, suspicious_ratio, risk}"""
    try:
        import cv2
    except ImportError:
        return {"green_ratio": 0, "suspicious_ratio": 0, "risk": "unknown", "error": "cv2 missing"}

    b, g, r = cv2.split(bgr.astype(np.float32))
    exg = 2 * g - r - b                        # مؤشّر الخضرة الزائدة
    veg = exg > 30                              # عتبة قابلة للضبط في config
    green_ratio = float(veg.mean())

    # كتل خضراء معزولة صغيرة = أعشاب بين المحصول
    veg_u8 = (veg * 255).astype("uint8")
    n, labels, stats, _ = cv2.connectedComponentsWithStats(veg_u8, 8)
    small = sum(1 for i in range(1, n)
                if 50 < stats[i, cv2.CC_STAT_AREA] < 1500)
    suspicious_ratio = small / max(1, n)

    if green_ratio > 0.15 and suspicious_ratio > 0.3:
        risk = "high"
    elif green_ratio > 0.08:
        risk = "medium"
    else:
        risk = "low"

    return {
        "green_ratio": round(green_ratio, 3),
        "suspicious_ratio": round(suspicious_ratio, 3),
        "risk": risk
    }


# خطّاف نموذج TFLite (المرحلة 2) — يُملأ لاحقاً بنموذج مدرَّب
TFLITE = None


def classify_weed(bgr):
    """يُرجع (label, confidence) أو None. يُربط بنموذج TFLite لاحقاً."""
    if TFLITE is None:
        return None
    return None
