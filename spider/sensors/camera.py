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
    """كاميرا حرارية MLX90640 (32×24).
    - port=None → I2C مباشر (adafruit_mlx90640) باستخدام i2c_lock.
    - port=... → موديول بخرج UART (متحكّم على اللوحة يجسر الشريحة إلى تسلسلي).
    - simulate=True → مصفوفة وهمية للاختبار بلا عتاد.
    في كل الأوضاع تبقى الواجهة موحّدة: read_matrix() يُرجع مصفوفة 24×32 °C أو None.
    """

    def __init__(self, i2c_lock=None, port=None, baud=115200, simulate=False,
                 rows=24, cols=32):
        self.lock = i2c_lock
        self.ready = False
        self.last_frame = None          # مصفوفة 24×32 درجات مئوية
        self.shape = (rows, cols)
        self.mode = None
        self.simulate = simulate
        self._ser = None
        self.mlx = None
        self._stop = threading.Event()

        if simulate:
            self.mode = "sim"
            self.ready = True
            threading.Thread(target=self._sim_loop, daemon=True).start()
        elif port:
            try:
                import serial
                self._ser = serial.Serial(port, baud, timeout=0.5)
                self.mode = "uart"
                self.ready = True
                threading.Thread(target=self._uart_loop, daemon=True).start()
            except Exception:
                self.ready = False      # بلا عتاد (مثلاً ويندوز) — تبقى الإطارات None
        else:
            try:
                import board
                import busio
                import adafruit_mlx90640
                i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
                self.mlx = adafruit_mlx90640.MLX90640(i2c)
                self.mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_8_HZ
                self.mode = "i2c"
                self.ready = True
            except Exception:
                self.ready = False

    def stop(self):
        self._stop.set()
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass

    def _uart_loop(self):
        """يقرأ إطارات الموديول التسلسلي ويملأ last_frame.
        ⚠️ صيغة الإطار يحدّدها مصنّع الموديول — أكمل الفك أدناه حسب ورقته.
        النمط العام: مزامنة رأس → قراءة rows*cols قيمة → تحويل لمصفوفة °C."""
        import numpy as np
        rows, cols = self.shape
        npix = rows * cols
        try:
            while not self._stop.is_set():
                # TODO: استبدل هذا بالفك الحقيقي حسب بروتوكول موديولك.
                # مثال شائع: 0x5A 0x5A ثم npix قيمة uint16 (درجة×100) little-endian:
                #   raw = self._ser.read(2 + npix*2)
                #   vals = struct.unpack_from("<%dH" % npix, raw, 2)
                #   m = np.array(vals, float).reshape(self.shape) / 100.0
                #   self.last_frame = m
                line = self._ser.readline()       # قراءة آمنة افتراضية حتى يُكمَّل الفك
                if not line:
                    continue
                self.ready = True
        except Exception:
            self.ready = False

    def _sim_loop(self):
        import numpy as np
        import time as _t
        rows, cols = self.shape
        t = 0
        while not self._stop.is_set():
            m = 22 + np.random.rand(rows, cols) * 4
            hr, hc = t % rows, (t * 2) % cols     # بقعة ساخنة متحرّكة
            m[hr, hc] = 38 + np.random.rand() * 6
            self.last_frame = m
            t += 1
            _t.sleep(0.2)

    def read_matrix(self):
        if self.mode == "i2c":
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
        # uart / sim — الخيوط تحدّث last_frame
        return self.last_frame

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
