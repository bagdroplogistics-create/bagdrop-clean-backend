import json
from http.server import BaseHTTPRequestHandler

class handler(BaseHTTPRequestHandler):
    bookings = []

    def _set_headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _send_json(self, status, data):
        self._set_headers(status)
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self._set_headers(200)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/health":
            return self._send_json(200, {"ok": True})

        if path.startswith("/api/track/"):
            code = path.replace("/api/track/", "", 1)
            booking = next((b for b in self.bookings if b.get("tracking_code") == code), None)
            if not booking:
                return self._send_json(404, {"error": "Booking not found"})
            return self._send_json(200, {"success": True, "booking": booking})

        if path == "/api/bookings":
            return self._send_json(200, {"success": True, "bookings": self.bookings})

        return self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        path = self.path.split("?")[0]

        if path != "/api/bookings":
            return self._send_json(404, {"error": "Not found"})

        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8")
        body = json.loads(raw_body) if raw_body else {}

        booking_id = str(len(self.bookings) + 1)
        tracking_code = f"BD{1000 + len(self.bookings) + 1}"

        booking = {
            "id": booking_id,
            "tracking_code": tracking_code,
            "client_id": body.get("client_id"),
            "name": body.get("name"),
            "phone": body.get("phone"),
            "address": body.get("address"),
            "status": "created"
        }

        self.bookings.append(booking)
        return self._send_json(201, {"success": True, "booking": booking})

    def do_PATCH(self):
        path = self.path.split("?")[0]

        if not path.endswith("/status"):
            return self._send_json(404, {"error": "Not found"})

        parts = path.split("/")
        if len(parts) < 5:
            return self._send_json(404, {"error": "Not found"})

        booking_id = parts[3]

        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8")
        body = json.loads(raw_body) if raw_body else {}

        booking = next((b for b in self.bookings if b["id"] == booking_id), None)
        if not booking:
            return self._send_json(404, {"error": "Booking not found"})

        booking["status"] = body.get("status", booking["status"])
        return self._send_json(200, {"success": True, "booking": booking})

    def do_DELETE(self):
        path = self.path.split("?")[0]
        parts = path.split("/")

        if len(parts) < 4 or parts[1] != "api" or parts[2] != "bookings":
            return self._send_json(404, {"error": "Not found"})

        booking_id = parts[3]
        before = len(self.bookings)
        self.bookings = [b for b in self.bookings if b["id"] != booking_id]

        if len(self.bookings) == before:
            return self._send_json(404, {"error": "Booking not found"})

        return self._send_json(200, {"success": True, "message": "Booking deleted"})
