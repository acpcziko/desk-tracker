"""Checks the NVR login against each configured camera via RTSP DESCRIBE. Never prints passwords.
Run from the desk-tracker folder:  venv\\Scripts\\python.exe tools\\rtsp_check.py"""
import base64
import hashlib
import re
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os  # noqa: E402

from app import config  # noqa: E402  (also loads .env)

cfg = config.load()
nvr = cfg["nvr"]
user, pw = os.environ.get("NVR_USER", ""), os.environ.get("NVR_PASSWORD", "")
try:
    with socket.create_connection((nvr["host"], nvr["rtsp_port"]), 5):
        print("Recorder RTSP port is reachable.")
except OSError as exc:
    sys.exit(f"Recorder RTSP connection failed: {exc}")


def describe(url: str, auth: str | None = None) -> tuple[int, str]:
    s = socket.create_connection((nvr["host"], nvr["rtsp_port"]), 5)
    s.settimeout(5)
    req = f"DESCRIBE {url} RTSP/1.0\r\nCSeq: 2\r\nAccept: application/sdp\r\n"
    if auth:
        req += f"Authorization: {auth}\r\n"
    s.sendall((req + "\r\n").encode())
    data = b""
    try:
        while b"\r\n\r\n" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        s.close()
    head = data.decode(errors="replace")
    m = re.match(r"RTSP/1.0 (\d+)", head)
    return (int(m.group(1)) if m else 0), head


def digest(head: str, url: str) -> str | None:
    m = re.search(r'Digest realm="([^"]+)",\s*nonce="([^"]+)"', head)
    if not m:
        return None
    realm, nonce = m.groups()
    ha1 = hashlib.md5(f"{user}:{realm}:{pw}".encode()).hexdigest()
    ha2 = hashlib.md5(f"DESCRIBE:{url}".encode()).hexdigest()
    resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    return f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{url}", response="{resp}"'


MEANING = {200: "OK - login and channel work", 401: "REFUSED - wrong user/password or no live-view permission",
           403: "FORBIDDEN - user has no permission for this channel", 404: "NOT FOUND - wrong channel/path",
           0: "no answer"}

for cam in cfg["cameras"]:
    for stream in (nvr["stream"], 0 if nvr["stream"] else 1):
        path = nvr["rtsp_path"].format(channel=cam["channel"], stream=stream)
        url = f"rtsp://{nvr['host']}:{nvr['rtsp_port']}{path}"
        try:
            code, head = describe(url)
            if code == 401:
                auth = digest(head, url)
                code, head = describe(url, auth) if auth else (code, head)
                if code == 401:
                    basic = base64.b64encode(f"{user}:{pw}".encode()).decode()
                    code, _ = describe(url, f"Basic {basic}")
        except OSError as e:
            code = 0
            print(f"  {cam['name']:<20} {path:<28} connection error: {e}")
            continue
        print(f"  {cam['name']:<20} {path:<28} {code} {MEANING.get(code, '')}")
