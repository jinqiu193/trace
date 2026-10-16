"""生成 6 张 DigUp 同主题文字截图（tests/testbed/img/）。

UI 风格还原 DigUp evals/synthetic.json 的 screenshot 类素材：
支付报错 / Python traceback / 航班预订 / 聊天记录 / 天气预报 / 代码截图。
用于验证：① 视觉编码器语义通道 ② OCR 关键词通道（DigUp：OCR 只进 FTS 不进向量）。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "testbed" / "img"
OUT.mkdir(parents=True, exist_ok=True)

W, H = 720, 480


def _font(size: int):
    for name in ("segoeui.ttf", "arial.ttf", "consola.ttf", "DejaVuSans.ttf"):
        for root in ("C:/Windows/Fonts", "/usr/share/fonts"):
            import os

            p = os.path.join(root, name)
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
    return ImageFont.load_default()


MONO = _font(15)
SANS = _font(16)
BOLD = _font(20)


def _terminal(lines, bg=(24, 26, 32), fg=(220, 222, 228), path="shot_payment_error.png"):
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    y = 24
    for text, color in lines:
        d.text((24, y), text, font=MONO, fill=color)
        y += 26
    img.save(OUT / path)


def shot_payment_error():
    _terminal([
        ("$ ./checkout --confirm", (120, 200, 120)),
        ("Connecting to payment gateway ...", (150, 150, 160)),
        ("POST /v2/charges  HTTP/1.1", (150, 150, 160)),
        ("", (0, 0, 0)),
        ("ERROR: payment declined", (240, 90, 90)),
        ("  code: card_declined", (240, 150, 150)),
        ("  reason: insufficient funds", (240, 150, 150)),
        ("  request_id: req_9f2c81aa", (150, 150, 160)),
        ("", (0, 0, 0)),
        ("The charge was not completed. Please try another card.", (200, 200, 210)),
    ])


def shot_python_traceback():
    _terminal([
        ("$ python train.py --data ./samples", (120, 200, 120)),
        ("loading dataset ...", (150, 150, 160)),
        ("Traceback (most recent call last):", (240, 90, 90)),
        ('  File "train.py", line 12, in <module>', (200, 200, 210)),
        ("    from sklearn.metrics import f1_score", (200, 200, 210)),
        ("ModuleNotFoundError: No module named 'sklearn'", (240, 120, 120)),
        ("", (0, 0, 0)),
        ("pip install scikit-learn to fix this problem.", (120, 180, 240)),
    ], path="shot_python_traceback.png")


def shot_flight_booking():
    img = Image.new("RGB", (W, H), (245, 247, 250))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 64], fill=(30, 90, 200))
    d.text((24, 18), "Booking Confirmed", font=BOLD, fill=(255, 255, 255))
    rows = [
        ("Flight", "JL 026  Shanghai  ->  Tokyo Haneda"),
        ("Date", "Friday, 14 Nov 2026   09:35 - 13:05"),
        ("Passenger", "QIU / MS"),
        ("Seat", "32A  (window)"),
        ("Booking ref", "K7Q2LM"),
    ]
    y = 100
    for k, v in rows:
        d.text((24, y), k, font=SANS, fill=(110, 116, 128))
        d.text((180, y), v, font=SANS, fill=(25, 28, 35))
        y += 42
    d.rectangle([24, y + 8, W - 24, y + 96], outline=(210, 214, 222), width=2)
    d.text((40, y + 26), "Payment: 4,820 CNY  -  Visa  ending 4242", font=SANS, fill=(60, 64, 72))
    img.save(OUT / "shot_flight_booking.png")


def shot_chat_meeting():
    img = Image.new("RGB", (W, H), (235, 238, 242))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 56], fill=(45, 50, 60))
    d.text((20, 16), "# team-product  (6 members)", font=BOLD, fill=(240, 240, 245))
    bubbles = [
        (False, "maya:  morning! the design review is still on for Thursday?", 40),
        (True,   "you:   yes, 10:00. I moved the design review to room 4B.", 20),
        (False, "maya:  perfect. can we move the design review 30 min later?", 60),
        (True,   "you:   sure, let's move the design review to 10:30 then.", 10),
    ]
    y = 84
    for mine, text, xoff in bubbles:
        w = d.textlength(text, font=SANS)
        bx = W - 24 - w - 24 if mine else xoff
        color = (70, 130, 240) if mine else (255, 255, 255)
        tcolor = (255, 255, 255) if mine else (30, 34, 40)
        d.rounded_rectangle([bx - 14, y - 10, bx + w + 14, y + 30], radius=12, fill=color)
        d.text((bx, y), text, font=SANS, fill=tcolor)
        y += 62
    img.save(OUT / "shot_chat_meeting.png")


def shot_weather():
    img = Image.new("RGB", (W, H), (200, 214, 232))
    d = ImageDraw.Draw(img)
    d.text((24, 20), "Shanghai  -  Weekly Forecast", font=BOLD, fill=(20, 40, 70))
    days = [("Mon", "rainy", "18/14 C"), ("Tue", "heavy rain", "17/13 C"),
            ("Wed", "cloudy", "20/15 C"), ("Thu", "sunny", "24/16 C"),
            ("Fri", "showers", "21/15 C")]
    x = 24
    for day, cond, temp in days:
        d.rounded_rectangle([x, 90, x + 124, 250], radius=10, fill=(255, 255, 255))
        d.text((x + 18, 106), day, font=SANS, fill=(90, 100, 115))
        d.text((x + 18, 140), cond, font=SANS, fill=(40, 80, 160))
        d.text((x + 18, 180), temp, font=SANS, fill=(60, 66, 76))
        x += 136
    d.text((24, 290), "Rain expected all Monday and Tuesday.", font=SANS, fill=(40, 70, 110))
    d.text((24, 316), "Take an umbrella. Flood warning for low streets.", font=SANS, fill=(40, 70, 110))
    img.save(OUT / "shot_weather.png")


def shot_swift_code():
    img = Image.new("RGB", (W, H), (28, 30, 38))
    d = ImageDraw.Draw(img)
    code = [
        ("import VectorSearch", (198, 120, 221)),
        ("", (0, 0, 0)),
        ("func embedQuery(_ text: String) throws -> [Float] {", (97, 175, 239)),
        ("    let prefixed = \"task: search result | query: \\(text)\"", (152, 195, 121)),
        ("    let tokens = try tokenizer.encode(prefixed)", (224, 108, 117)),
        ("    let feats = try model.run(tokens)", (224, 108, 117)),
        ("    return l2Normalize(feats.sentenceEmbedding)", (229, 192, 123)),
        ("}", (97, 175, 239)),
    ]
    y = 24
    for text, color in code:
        d.text((20, y), text, font=MONO, fill=color)
        y += 30
    img.save(OUT / "shot_swift_code.png")


if __name__ == "__main__":
    shot_payment_error()
    shot_python_traceback()
    shot_flight_booking()
    shot_chat_meeting()
    shot_weather()
    shot_swift_code()
    print(f"6 screenshots -> {OUT}")
