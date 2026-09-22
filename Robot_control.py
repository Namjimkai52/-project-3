import os
import time
import struct
import threading
from datetime import datetime
import cv2
from flask import Flask, Response, render_template_string, send_file, jsonify

app = Flask(__name__)

# โฟลเดอร์เก็บภาพถ่าย (เก็บถาวรบน Pi จนกว่าจะลบเอง)
IMAGE_DIR = "captures"
os.makedirs(IMAGE_DIR, exist_ok=True)

# ---------------- ตั้งค่าที่ปรับได้ ----------------
# ลองปรับ 2 ค่านี้ถ้าภาพหมุนผิดทิศ / กลับด้านผิด
#   ROTATE_MODE ตัวเลือก: None, cv2.ROTATE_90_CLOCKWISE,
#                          cv2.ROTATE_90_COUNTERCLOCKWISE, cv2.ROTATE_180
#   FLIP_MODE   ตัวเลือก: None (ไม่พลิก), 0 (พลิกบน-ล่าง), 1 (พลิกซ้าย-ขวา), -1 (พลิกทั้งคู่)
ROTATE_MODE = None
FLIP_MODE = 1

# ค่านี้คือตำแหน่ง (%) ที่ "จุดตรงหน้ากล้องจริง" ปรากฏอยู่ในเฟรมดิบ ได้มาจากการคลิก
# calibrate ด้วย camera_center_test.py โปรแกรมจะ crop ภาพให้จุดนี้เลื่อนมาอยู่กึ่งกลาง
# เฟรมโดยอัตโนมัติ (เสียขอบภาพด้านที่เบี่ยงไปนิดหน่อย แลกกับความแม่นยำของจุดกึ่งกลาง)
RECENTER_X_PERCENT = 12.9
RECENTER_Y_PERCENT = 43.1

# กากบาทแสดงผลกึ่งกลางเสมอ เพราะภาพถูก recenter มาให้ตรงแล้วด้วยค่าด้านบน
CROSSHAIR_X_PERCENT = 50
CROSSHAIR_Y_PERCENT = 50

# ตัวแปรสถานะ
latest_photo_name = None
latest_frame = None
frame_lock = threading.Lock()
take_photo_flag = False

# เปิดใช้งานกล้อง USB
cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))


def recenter_frame(frame):
    """
    Crop ภาพแบบไม่สมมาตร ให้จุดที่ระบุด้วย RECENTER_X/Y_PERCENT เลื่อนมาอยู่กึ่งกลาง
    เฟรมพอดี แล้ว resize กลับเป็นขนาดเดิม เพื่อให้ทุกเฟรมมีขนาดคงที่เท่ากันเสมอ
    """
    h, w = frame.shape[:2]
    px = int(w * RECENTER_X_PERCENT / 100.0)
    py = int(h * RECENTER_Y_PERCENT / 100.0)

    # ระยะจาก "จุดที่ต้องการ" ไปถึงขอบภาพแต่ละด้าน เอาค่าที่สั้นกว่ามาทำครึ่งความกว้าง/สูง
    # ของกรอบ crop เพื่อให้จุดนั้นอยู่กึ่งกลางเป๊ะหลัง crop เสมอ
    half_w = min(px, w - px)
    half_h = min(py, h - py)

    if half_w <= 0 or half_h <= 0:
        return frame  # กันพังถ้าค่า percent ผิดเพี้ยนจนคำนวณไม่ได้

    x1, x2 = px - half_w, px + half_w
    y1, y2 = py - half_h, py + half_h

    cropped = frame[y1:y2, x1:x2]
    recentered = cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)
    return recentered


def process_frame(raw_frame):
    """ปรับทิศทางภาพ (หมุน/พลิก) แล้ว crop ให้ recenter ตามจุดที่ calibrate ไว้"""
    frame = raw_frame
    if ROTATE_MODE is not None:
        frame = cv2.rotate(frame, ROTATE_MODE)
    if FLIP_MODE is not None:
        frame = cv2.flip(frame, FLIP_MODE)
    frame = recenter_frame(frame)
    return frame


def camera_loop():
    """เธรดดึงภาพจากกล้องต่อเนื่อง"""
    global latest_frame, latest_photo_name, take_photo_flag
    while True:
        success, raw_frame = cap.read()
        if not success:
            continue

        frame = process_frame(raw_frame)

        with frame_lock:
            latest_frame = frame.copy()

        if take_photo_flag:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"capture_{timestamp}.jpg"
            filepath = os.path.join(IMAGE_DIR, filename)

            cv2.imwrite(filepath, frame)
            latest_photo_name = filename
            take_photo_flag = False
            print(f"\n[CAMERA] บันทึกภาพถ่ายสำเร็จ: {filepath}")

        time.sleep(0.01)


def joystick_loop():
    """เธรดตรวจจับปุ่มจากจอยสติ๊ก PS4"""
    global take_photo_flag
    device_path = "/dev/input/js0"

    try:
        js = open(device_path, "rb")
        print("[JOYSTICK] เชื่อมต่อสำเร็จ! กดปุ่ม Circle (O) เพื่อถ่ายรูป")
    except FileNotFoundError:
        print(f"[JOYSTICK] ไม่พบอุปกรณ์ {device_path}")
        return

    event_format = 'IhBB'
    event_size = struct.calcsize(event_format)

    while True:
        try:
            event_data = js.read(event_size)
            if not event_data:
                break
            time_ms, value, ev_type, number = struct.unpack(event_format, event_data)

            # Button Event (0x01): กดลง (value == 1) ที่ปุ่ม Circle (number == 1)
            if (ev_type & 0x01) and value == 1 and number == 1:
                print("\n[JOYSTICK] กดปุ่ม Circle (O) -> สั่งบันทึกภาพ")
                take_photo_flag = True
        except Exception:
            break


HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Robot Camera Center Stream</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            background-color: #121212;
            color: #fff;
            font-family: Arial, sans-serif;
            text-align: center;
            margin: 0;
            padding: 20px;
        }
        .container {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 24px;
            margin-top: 15px;
        }
        .card {
            background: #1e1e1e;
            padding: 15px;
            border-radius: 12px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.5);
            display: inline-block;
        }
        .stream-wrapper {
            position: relative;
            display: inline-block;
            width: 560px;
            max-width: 100%;
        }
        .crosshair-x {
            position: absolute;
            top: {{ crosshair_y }}%;
            left: 0;
            width: 100%;
            height: 2px;
            background-color: rgba(0, 255, 128, 0.7);
            transform: translateY(-50%);
            pointer-events: none;
        }
        .crosshair-y {
            position: absolute;
            top: 0;
            left: {{ crosshair_x }}%;
            width: 2px;
            height: 100%;
            background-color: rgba(0, 255, 128, 0.7);
            transform: translateX(-50%);
            pointer-events: none;
        }
        .center-circle {
            position: absolute;
            top: {{ crosshair_y }}%;
            left: {{ crosshair_x }}%;
            width: 32px;
            height: 32px;
            border: 2px solid rgba(0, 255, 128, 0.9);
            border-radius: 50%;
            transform: translate(-50%, -50%);
            pointer-events: none;
        }
        img {
            width: 560px;
            max-width: 100%;
            height: auto;
            border-radius: 8px;
            display: block;
            background: #000;
        }
        .file-label {
            margin-top: 10px;
            font-size: 14px;
            color: #00ff80;
        }
        a.gallery-link {
            display: inline-block;
            margin-top: 20px;
            color: #00ff80;
            text-decoration: none;
            font-size: 15px;
        }
        a.gallery-link:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <h2>🤖 Robot Camera Stream & Center Target</h2>
    <p>กดปุ่ม <b>Circle (O)</b> บนจอย PS4 เพื่อถ่ายรูป | เส้นกากบาทสีเขียวคือจุดศูนย์กลางกล้อง</p>

    <div class="container">
        <div class="card">
            <h3>🔴 ภาพสดเล็งเป้า (Live Stream)</h3>
            <div class="stream-wrapper">
                <img src="/video_feed" alt="Live Camera">
                <div class="crosshair-x"></div>
                <div class="crosshair-y"></div>
                <div class="center-circle"></div>
            </div>
        </div>
        <div class="card">
            <h3>📸 ภาพถ่ายล่าสุด (Latest Capture)</h3>
            <img id="captured-img" src="/latest_photo" alt="ยังไม่มีภาพถ่าย">
            <div id="file-name" class="file-label">รอคำสั่งถ่ายรูป...</div>
        </div>
    </div>

    <a class="gallery-link" href="/gallery">📁 ดูภาพที่ถ่ายไว้ทั้งหมด / ดาวน์โหลด</a>

    <script>
        let lastFile = "";
        setInterval(() => {
            fetch('/check_status')
                .then(res => res.json())
                .then(data => {
                    if (data.latest && data.latest !== lastFile) {
                        lastFile = data.latest;
                        document.getElementById('captured-img').src = '/latest_photo?t=' + new Date().getTime();
                        document.getElementById('file-name').innerText = 'ไฟล์: ' + lastFile;
                    }
                });
        }, 800);
    </script>
</body>
</html>
"""

GALLERY_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>คลังภาพที่ถ่ายไว้</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            background-color: #121212;
            color: #fff;
            font-family: Arial, sans-serif;
            text-align: center;
            padding: 20px;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
            gap: 16px;
            margin-top: 20px;
        }
        .photo-card {
            background: #1e1e1e;
            border-radius: 10px;
            padding: 10px;
        }
        .photo-card img {
            width: 100%;
            border-radius: 6px;
        }
        .photo-card a {
            display: inline-block;
            margin-top: 8px;
            color: #00ff80;
            text-decoration: none;
            font-size: 13px;
        }
        a.back-link {
            color: #00ff80;
            text-decoration: none;
        }
    </style>
</head>
<body>
    <a class="back-link" href="/">&larr; กลับหน้าหลัก</a>
    <h2>📁 คลังภาพที่ถ่ายไว้ทั้งหมด ({{ count }} ภาพ)</h2>
    <div class="grid">
        {% for name in photos %}
        <div class="photo-card">
            <img src="/photo/{{ name }}" alt="{{ name }}">
            <div>{{ name }}</div>
            <a href="/photo/{{ name }}" download>⬇ ดาวน์โหลด</a>
        </div>
        {% endfor %}
    </div>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(
        HTML_PAGE,
        crosshair_x=CROSSHAIR_X_PERCENT,
        crosshair_y=CROSSHAIR_Y_PERCENT,
    )


@app.route('/gallery')
def gallery():
    files = sorted(os.listdir(IMAGE_DIR), reverse=True)
    files = [f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    return render_template_string(GALLERY_PAGE, photos=files, count=len(files))


@app.route('/photo/<filename>')
def photo(filename):
    filepath = os.path.join(IMAGE_DIR, filename)
    if os.path.isfile(filepath):
        return send_file(filepath)
    return Response(status=404)


def generate_frames():
    while True:
        with frame_lock:
            if latest_frame is None:
                continue
            ret, buffer = cv2.imencode('.jpg', latest_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if not ret:
                continue
            frame_bytes = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        time.sleep(0.03)


@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/check_status')
def check_status():
    return jsonify({"latest": latest_photo_name})


@app.route('/latest_photo')
def latest_photo():
    if latest_photo_name:
        return send_file(os.path.join(IMAGE_DIR, latest_photo_name))
    return Response(status=404)


if __name__ == '__main__':
    t_cam = threading.Thread(target=camera_loop, daemon=True)
    t_cam.start()

    t_joy = threading.Thread(target=joystick_loop, daemon=True)
    t_joy.start()

    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)