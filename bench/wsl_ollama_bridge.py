"""Forward local WSL Ollama requests to the Windows Ollama service."""
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def forward(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        command = ["/mnt/c/Windows/System32/curl.exe", "-sS", "--max-time", "600",
                   "-X", self.command, "--write-out", "\n__HTTP_STATUS__:%{http_code}",
                   "http://127.0.0.1:11434" + self.path]
        if self.command == "POST":
            command += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
        result = subprocess.run(command, input=body, capture_output=True)
        response, marker, status = result.stdout.rpartition(b"\n__HTTP_STATUS__:")
        if result.returncode or not marker or not status.isdigit():
            response, code = result.stderr or b"Ollama bridge failed", 502
        else:
            code = int(status)
        self.send_response(code)
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    do_GET = forward
    do_POST = forward

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 11434), Handler).serve_forever()
