#!/usr/bin/env python3
"""Post-deploy check: submit the shop preview form exactly like a browser does,
with the optional Payment QR field left empty. Uses only the standard library.
It only calls /shop/preview (no shop is created); the two tiny test images it
uploads are removed by the orphan cleanup after 24 hours.

    python3 scripts/check_preview_upload.py https://avigronix.com
"""
import base64, sys, urllib.request, urllib.error, uuid
site = sys.argv[1].rstrip("/")
png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
b = "----check" + uuid.uuid4().hex
fields = {"shopName": "Deploy Check", "domain": "deploy-check-" + uuid.uuid4().hex[:6], "about": "check", "category": "Retail",
          "businessType": "Retail", "contactNumber": "+919876543210", "email": "check@example.com",
          "address": "x", "city": "x", "state": "x", "pincode": "000000"}
body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
for name, fn, data in [("logo", "l.png", png), ("banner", "b.png", png), ("paymentQR", "", b"")]:  # paymentQR left empty, like a browser
    body += f'--{b}\r\nContent-Disposition: form-data; name="{name}"; filename="{fn}"\r\nContent-Type: image/png\r\n\r\n'.encode() + data + b"\r\n"
body += f"--{b}--\r\n".encode()
req = urllib.request.Request(site + "/shop/preview", data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"})
try:
    r = urllib.request.urlopen(req); html = r.read().decode()
    print("preview with empty Payment QR:", r.status, "OK" if "Publish Website Now" in html else "UNEXPECTED PAGE")
except urllib.error.HTTPError as e:
    print("preview with empty Payment QR: FAILED", e.code, e.read()[:200])
