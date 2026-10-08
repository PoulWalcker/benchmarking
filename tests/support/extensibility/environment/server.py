"""A private beacon world with its own calibration tool protocol."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets


class World:
    def __init__(self):
        self.initial = {
            "bearing": secrets.randbelow(360),
            "offset": secrets.randbelow(359) + 1,
            "seal": secrets.token_hex(16),
            "station": "north",
        }
        self.calls = []

    def sample(self, request):
        if request["operation"] != "beacon.sample" or request["arguments"] != {"station": "north"}:
            raise ValueError("Unknown beacon request")
        value = {
            "angle": (self.initial["bearing"] + self.initial["offset"]) % 360,
            "station": "north",
            "seal": self.initial["seal"],
        }
        self.calls.append({"request": request, "response": value})
        return {"ok": True, "value": value}


world = World()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        value = {"ready": True} if self.path == "/health" else {"initial": world.initial, "calls": world.calls}
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/tools" or self.headers.get("Authorization") != "Bearer beacon-private-tool":
            self.send_error(403)
            return
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        body = json.dumps(world.sample(request)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
