import os
import re
import time
import struct
import threading
from datetime import datetime
import cv2
import pytesseract
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
FLIP_MODE = None

# ตัวแปรสถานะ
latest_photo_name = None
latest_frame = None
latest_detected_number = None   # เลขล่าสุดที่ OCR อ่านได้ (None ถ้ายังไม่ถ่าย หรืออ่านไม่เจอ)
latest_capture_time = None      # เวลาที่ถ่ายภาพล่าสุด (แสดงผลแบบอ่านง่าย)
frame_lock = threading.Lock()
take_photo_flag = False

# เปิดใช้งานกล้อง USB
cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))


def process_frame(raw_frame):
    """ปรับทิศทางภาพตามค่า ROTATE_MODE / FLIP_MODE ด้านบน"""
    frame = raw_frame
    if ROTATE_MODE is not None:
        frame = cv2.rotate(frame, ROTATE_MODE)
    if FLIP_MODE is not None:
        frame = cv2.flip(frame, FLIP_MODE)
    return frame


def read_4digit_number(frame):
    """
    อ่านเลข 4 หลักจากภาพด้วย Tesseract OCR
    คืนค่าเป็น string เลข 4 หลักตัวแรกที่เจอ หรือ None ถ้าไม่เจอ
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # ขยายภาพ 2 เท่า + threshold ช่วยให้ OCR อ่านตัวเลขได้แม่นขึ้น
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    config = "--psm 6 -c tessedit_char_whitelist=0123456789"
    text = pytesseract.image_to_string(thresh, config=config)

    match = re.search(r"\d{4}", text)
    return match.group(0) if match else None


def camera_loop():
    """เธรดดึงภาพจากกล้องต่อเนื่อง"""
    global latest_frame, latest_photo_name, take_photo_flag
    global latest_detected_number, latest_capture_time
    while True:
        success, raw_frame = cap.read()
        if not success:
            continue

        frame = process_frame(raw_frame)

        with frame_lock:
            latest_frame = frame.copy()

        if take_photo_flag:
            now = datetime.now()
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            filename = f"capture_{timestamp}.jpg"
            filepath = os.path.join(IMAGE_DIR, filename)

            cv2.imwrite(filepath, frame)
            latest_photo_name = filename
            latest_capture_time = now.strftime("%d/%m/%Y %H:%M:%S")
            take_photo_flag = False
            print(f"\n[CAMERA] บันทึกภาพถ่ายสำเร็จ: {filepath} เวลา {latest_capture_time}")

            # อ่านเลข 4 หลักจากภาพที่เพิ่งถ่าย แล้วเก็บไว้ให้เว็บดึงไปแสดงผลทันที
            number = read_4digit_number(frame)
            if number:
                print(f"[OCR] อ่านเลขได้: {number}")
                latest_detected_number = number
            else:
                print("[OCR] ไม่พบเลข 4 หลักในภาพนี้")
                latest_detected_number = "ไม่พบตัวเลข"

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
    <title>PILOT VIEW // CAM-01</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap" rel="stylesheet">
    <style>
        :root {
            --void: #05080C;
            --cyan: #4DF0FF;
            --cyan-dim: rgba(77, 240, 255, 0.35);
            --panel: rgba(5, 10, 14, 0.68);
            --warn: #FF5D5D;
        }
        * { box-sizing: border-box; }
        html, body {
            margin: 0;
            height: 100%;
            background: var(--void);
            color: var(--cyan);
            font-family: 'Share Tech Mono', monospace;
            overflow: hidden;
        }
        .stage {
            position: relative;
            width: 100vw;
            height: 100vh;
        }
        .feed {
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            object-fit: cover;
            filter: contrast(1.06) saturate(1.08);
            background: #000;
        }
        .vignette {
            position: absolute;
            inset: 0;
            background: radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.6) 100%);
            pointer-events: none;
        }
        .corner {
            position: absolute;
            width: 34px;
            height: 34px;
            border-color: var(--cyan);
            opacity: 0.85;
            pointer-events: none;
        }
        .corner-tl { top: 16px; left: 16px; border-top: 3px solid; border-left: 3px solid; }
        .corner-tr { top: 16px; right: 16px; border-top: 3px solid; border-right: 3px solid; }
        .corner-bl { bottom: 16px; left: 16px; border-bottom: 3px solid; border-left: 3px solid; }
        .corner-br { bottom: 16px; right: 16px; border-bottom: 3px solid; border-right: 3px solid; }

        .hud-top {
            position: absolute;
            top: env(safe-area-inset-top, 20px);
            left: 50%;
            transform: translateX(-50%);
            display: flex;
            align-items: center;
            gap: 12px;
            background: var(--panel);
            border: 1px solid var(--cyan-dim);
            border-radius: 4px;
            padding: 8px 18px;
            font-size: 13px;
            letter-spacing: 2px;
            backdrop-filter: blur(6px);
            white-space: nowrap;
        }
        .rec-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--warn);
            box-shadow: 0 0 8px var(--warn);
            animation: blink 1.4s infinite;
        }
        @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.15; } }

        .capture-panel {
            position: absolute;
            bottom: max(20px, env(safe-area-inset-bottom, 20px));
            left: 20px;
            width: 230px;
            max-width: 42vw;
            background: var(--panel);
            border: 1px solid var(--cyan-dim);
            border-radius: 6px;
            padding: 12px;
            backdrop-filter: blur(8px);
        }
        .capture-panel .label {
            font-size: 11px;
            letter-spacing: 2px;
            opacity: 0.7;
            margin-bottom: 6px;
        }
        .capture-thumb {
            width: 100%;
            border-radius: 3px;
            border: 1px solid var(--cyan-dim);
            display: block;
            background: #000;
        }
        .capture-meta {
            font-size: 11px;
            opacity: 0.75;
            margin-top: 6px;
        }
        .digits {
            font-size: 30px;
            letter-spacing: 5px;
            margin-top: 6px;
            text-shadow: 0 0 10px var(--cyan-dim);
        }

        a.archive-btn {
            position: absolute;
            bottom: max(20px, env(safe-area-inset-bottom, 20px));
            right: 20px;
            background: var(--panel);
            border: 1px solid var(--cyan-dim);
            color: var(--cyan);
            text-decoration: none;
            font-size: 13px;
            letter-spacing: 2px;
            padding: 12px 18px;
            border-radius: 6px;
            backdrop-filter: blur(8px);
        }
        a.archive-btn:active { transform: translateY(2px); }

        @media (max-width: 640px) {
            .capture-panel { width: 46vw; }
            .digits { font-size: 24px; letter-spacing: 3px; }
            .hud-top { font-size: 11px; padding: 6px 12px; gap: 8px; }
        }
    </style>
</head>
<body>
    <div class="stage">
        <img class="feed" src="/video_feed" alt="Live Camera">
        <div class="vignette"></div>

        <div class="corner corner-tl"></div>
        <div class="corner corner-tr"></div>
        <div class="corner corner-bl"></div>
        <div class="corner corner-br"></div>

        <div class="hud-top">
            <span class="rec-dot"></span>
            <span>LIVE · CAM-01</span>
            <span id="clock">--:--:--</span>
        </div>

        <div class="capture-panel">
            <div class="label">LAST CAPTURE</div>
            <img id="captured-img" class="capture-thumb" src="/latest_photo" alt="no capture yet">
            <div id="capture-time" class="capture-meta">🕒 -</div>
            <div id="number-digits" class="digits" style="display:none;">----</div>
        </div>

        <a class="archive-btn" href="/gallery">▤ ARCHIVE</a>
    </div>

    <script>
        function tickClock() {
            document.getElementById('clock').innerText =
                new Date().toLocaleTimeString('th-TH', { hour12: false });
        }
        tickClock();
        setInterval(tickClock, 1000);

        let lastFile = "";
        setInterval(() => {
            fetch('/check_status')
                .then(res => res.json())
                .then(data => {
                    if (data.latest && data.latest !== lastFile) {
                        lastFile = data.latest;
                        document.getElementById('captured-img').src = '/latest_photo?t=' + new Date().getTime();
                        document.getElementById('capture-time').innerText = '🕒 ' + (data.time || '-');

                        const digitsEl = document.getElementById('number-digits');
                        digitsEl.style.display = 'block';
                        digitsEl.innerText = data.number || '----';
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
    <title>MISSION ARCHIVE // CAM-01</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap" rel="stylesheet">
    <style>
        :root {
            --void: #05080C;
            --panel: #0C1319;
            --cyan: #4DF0FF;
            --cyan-dim: rgba(77, 240, 255, 0.35);
            --warn: #FF5D5D;
        }
        * { box-sizing: border-box; }
        body {
            background: var(--void);
            color: var(--cyan);
            font-family: 'Share Tech Mono', monospace;
            margin: 0;
            padding: 24px 20px 60px;
        }
        .top-bar {
            max-width: 1100px;
            margin: 0 auto 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 12px;
            border-bottom: 1px solid var(--cyan-dim);
            padding-bottom: 14px;
        }
        a.back-link {
            color: var(--cyan);
            text-decoration: none;
            border: 1px solid var(--cyan-dim);
            padding: 9px 16px;
            border-radius: 4px;
            font-size: 13px;
            letter-spacing: 1px;
        }
        h2 {
            font-size: 16px;
            letter-spacing: 3px;
            margin: 0;
            font-weight: normal;
        }
        .grid {
            max-width: 1100px;
            margin: 24px auto 0;
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
            gap: 16px;
        }
        .photo-card {
            background: var(--panel);
            border: 1px solid var(--cyan-dim);
            border-radius: 4px;
            padding: 10px;
        }
        .photo-card img {
            width: 100%;
            display: block;
            border: 1px solid var(--cyan-dim);
            border-radius: 2px;
        }
        .photo-name {
            font-size: 11px;
            opacity: 0.65;
            margin-top: 8px;
            word-break: break-all;
        }
        .photo-time {
            font-size: 11px;
            opacity: 0.85;
            margin-top: 2px;
        }
        .photo-actions {
            display: flex;
            justify-content: space-between;
            gap: 8px;
            margin-top: 10px;
        }
        .photo-card a.download-chip {
            flex: 1;
            text-align: center;
            border: 1px solid var(--cyan-dim);
            color: var(--cyan);
            text-decoration: none;
            font-size: 11px;
            padding: 7px 8px;
            border-radius: 3px;
            letter-spacing: 1px;
        }
        .delete-btn {
            flex: 1;
            background: transparent;
            border: 1px solid rgba(255, 93, 93, 0.5);
            color: var(--warn);
            font-size: 11px;
            cursor: pointer;
            padding: 7px 8px;
            border-radius: 3px;
            font-family: inherit;
            letter-spacing: 1px;
        }
        .empty-state {
            max-width: 1100px;
            margin: 60px auto;
            text-align: center;
            opacity: 0.6;
            font-size: 13px;
            letter-spacing: 1px;
        }
    </style>
</head>
<body>
    <div class="top-bar">
        <a class="back-link">&larr; PILOT VIEW</a>
        <h2>▤ MISSION ARCHIVE · {{ count }} LOGGED</h2>
    </div>

    {% if count == 0 %}
    <div class="empty-state">// NO CAPTURES LOGGED — กด Circle (O) บนจอยเพื่อบันทึกภาพแรก</div>
    {% endif %}

    <div class="grid">
        {% for photo in photos %}
        <div class="photo-card" id="card-{{ photo.name }}">
            <img src="/photo/{{ photo.name }}" alt="{{ photo.name }}">
            <div class="photo-name">{{ photo.name }}</div>
            <div class="photo-time">🕒 {{ photo.time }}</div>
            <div class="photo-actions">
                <a class="download-chip" href="/photo/{{ photo.name }}" download>↓ SAVE</a>
                <button class="delete-btn" onclick="deletePhoto('{{ photo.name }}')">✕ DEL</button>
            </div>
        </div>
        {% endfor %}
    </div>

    <script>
        document.querySelector('.back-link').addEventListener('click', () => { window.location.href = '/'; });

        function deletePhoto(filename) {
            if (!confirm('ลบภาพ ' + filename + ' ใช่ไหม? กู้คืนไม่ได้')) return;

            fetch('/delete_photo/' + filename, { method: 'DELETE' })
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById('card-' + filename).remove();
                    } else {
                        alert('ลบไม่สำเร็จ: ' + (data.error || 'ไม่ทราบสาเหตุ'));
                    }
                })
                .catch(() => alert('เกิดข้อผิดพลาดในการลบภาพ'));
        }
    </script>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(HTML_PAGE)


def parse_capture_time(filename):
    """แปลงชื่อไฟล์ capture_YYYYMMDD_HHMMSS.jpg เป็นข้อความเวลาอ่านง่าย"""
    try:
        name_part = filename.rsplit('.', 1)[0]          # ตัดนามสกุลออก
        timestamp_part = name_part.replace('capture_', '')
        dt = datetime.strptime(timestamp_part, "%Y%m%d_%H%M%S")
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return "ไม่ทราบเวลา"


@app.route('/gallery')
def gallery():
    files = sorted(os.listdir(IMAGE_DIR), reverse=True)
    files = [f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    photos = [{"name": f, "time": parse_capture_time(f)} for f in files]
    return render_template_string(GALLERY_PAGE, photos=photos, count=len(photos))


@app.route('/photo/<filename>')
def photo(filename):
    filepath = os.path.join(IMAGE_DIR, filename)
    if os.path.isfile(filepath):
        return send_file(filepath)
    return Response(status=404)


@app.route('/delete_photo/<filename>', methods=['DELETE'])
def delete_photo(filename):
    global latest_photo_name, latest_detected_number, latest_capture_time

    # กันชื่อไฟล์หลอกให้ออกไปลบไฟล์นอกโฟลเดอร์ captures (path traversal)
    safe_name = os.path.basename(filename)
    filepath = os.path.join(IMAGE_DIR, safe_name)

    if not os.path.isfile(filepath):
        return jsonify({"success": False, "error": "ไม่พบไฟล์นี้"}), 404

    try:
        os.remove(filepath)
    except OSError as e:
        return jsonify({"success": False, "error": str(e)}), 500

    # ถ้าลบภาพที่กำลังแสดงเป็น "ภาพถ่ายล่าสุด" อยู่ ให้เคลียร์สถานะหน้าเว็บด้วย
    if latest_photo_name == safe_name:
        latest_photo_name = None
        latest_detected_number = None
        latest_capture_time = None

    return jsonify({"success": True})


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
    return jsonify({
        "latest": latest_photo_name,
        "number": latest_detected_number,
        "time": latest_capture_time,
    })


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