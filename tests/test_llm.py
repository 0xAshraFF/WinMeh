import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from winmeh.config import Settings
from winmeh.core.assistant import Assistant
from winmeh.core.llm import LLM


class FakeOpenAI(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOpenAI.seen.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for tok in ["Hel", "lo", "!"]:
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': tok}}]})}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *a):
        pass


def test_streams_tokens_and_sends_machine_context():
    srv = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = Settings()
    s.llm_url = f"http://127.0.0.1:{srv.server_port}/v1"
    s.llm_autostart = False
    llm = LLM(s)
    assert llm.ensure() and llm.status == "ready"
    a = Assistant(s, llm)
    a.profile = {"gpus": [{"name": "RTX 4070", "vram_gb": 12.0}], "ram_gb": 32.0}
    toks = []
    a.handle("tell me a joke", lambda k, p: toks.append(p) if k == "token" else None)
    srv.shutdown()
    assert "".join(toks) == "Hello!"
    system = FakeOpenAI.seen[-1]["messages"][0]["content"]
    assert "RTX 4070" in system and FakeOpenAI.seen[-1]["stream"] is True
