# spider/sensors/camera.py
"""كاميرا RGB (Picamera2/OpenCV) + كاميرا حرارية (MLX90640) مع MJPEG stream."""
import time
import threading


class RGBCamera:
    def __init__(self, width=640, height=480, src=0):
        self.w, self.h = width, height
        self.cap = None
        self.backend = None
        try:
            from picamera2 import Picamera2
            self.cam = Picamera2()
            self.cam.configure(self.cam.create_video_configuration(
                main={"size": (width, height), "format": "RGB888"}))
            self.cam.start()
            self.backend = "picamera2"
        except Exception:
            try:
                import cv2
                self.cv2 = cv2
                self.cap = cv2.VideoCapture(src)
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                if self.cap.isOpened():
                    self.backend = "opencv"
                else:
                    self.cap.release(); self.cap = None
            except Exception:
                pass

    def read(self):
        """يُرجع frame كـ ndarray BGR، أو None إن لم تكن الكاميرا متاحة."""
        if self.backend == "picamera2":
            rgb = self.cam.capture_array()
            import cv2
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        elif self.backend == "opencv":
            ok, frame = self.cap.read()
            return frame if ok else None
        return None

    def mjpeg(self):
        """Generator: يبثّ JPEG frames بصيغة multipart/x-mixed-replace."""
        import cv2
        while True:
            frame = self.read()
            if frame is None:
                time.sleep(0.05)
                continue
            ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + jpg.tobytes() + b"\r\n")
            time.sleep(1 / 20)


class ThermalCamera:
    """كاميرا حرارية MLX90640 (32×24) عبر I2C. تستخدم i2c_lock لتفادي تشويش ناقل المحركات."""

    def __init__(self, i2c_lock=None):
        self.lock = i2c_lock
        self.ready = False
        self.last_frame = None  # مصفوفة 24×32 درجات مئوية
        self.shape = (24, 32)
        try:
            import board
            import busio
            import adafruit_mlx90640
            i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
            self.mlx = adafruit_mlx90640.MLX90640(i2c)
            self.mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_8_HZ
            self.ready = True
        except Exception:
            self.ready = False

    def read_matrix(self):
        if not self.ready:
            return self.last_frame
        import numpy as np
        buf = [0] * 768
        try:
            if self.lock:
                self.lock.acquire()
            self.mlx.getFrame(buf)
        except Exception:
            return self.last_frame
        finally:
            if self.lock:
                self.lock.release()
        m = np.array(buf, dtype=float).reshape(self.shape)
        self.last_frame = m
        return m

    def colorized(self, scale=16):
        """يحوّل المصفوفة لصورة ملوّنة مكبّرة بـ colormap Inferno."""
        import cv2
        import numpy as np
        m = self.read_matrix()
        if m is None:
            return None
        lo = float(np.percentile(m, 2))
        hi = float(np.percentile(m, 98))
        norm = np.clip((m - lo) / max(1e-3, hi - lo), 0, 1)
        img = (norm * 255).astype("uint8")
        img = cv2.resize(img, (self.shape[1] * scale, self.shape[0] * scale),
                         interpolation=cv2.INTER_CUBIC)
        img = cv2.applyColorMap(img, cv2.COLORMAP_INFERNO)
        cv2.putText(img, f"{hi:.1f}C", (5, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        cv2.putText(img, f"{lo:.1f}C", (5, img.shape[0] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        return img

    def mjpeg(self):
        import cv2
        while True:
            img = self.colorized()
            if img is None:
                time.sleep(0.2)
                continue
            ok, jpg = cv2.imencode(".jpg", img)
            if ok:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + jpg.tobytes() + b"\r\n")
            time.sleep(1 / 8)
