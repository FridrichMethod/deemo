"""Loopback HTTP fixtures shared by tests/test_fetchers.py and tests/test_fetch_wikis.py, which load this file with
importlib as they load the scripts: servers on 127.0.0.1 that stop when their test ends, and an adapter that sends
every request to one of them. Nothing leaves the machine."""

import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from requests.adapters import HTTPAdapter


class Loopback(HTTPAdapter):
    """Sends every request, whatever host its URL names, over plain HTTP to the loopback server on port; the response
    keeps the URL that was requested. Nothing leaves the machine. Each response is kept in responses, so a test can
    close one that requests raised on before it released the connection."""

    def __init__(self, port, responses):
        super().__init__()
        self.port, self.responses = port, responses

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        return self.poolmanager.connection_from_host("127.0.0.1", self.port, scheme="http")

    def build_response(self, req, resp):
        response = super().build_response(req, resp)
        self.responses.append(response)
        return response


def serve(test, handler):
    """Serves the BaseHTTPRequestHandler class handler on a free port of 127.0.0.1 until test ends; returns the port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server.server_address[1]


def serve_redirect_bodies(test):
    """Serves the redirects of a RedirectBodyTests case until test ends; returns the port. A path with "moved" is
    redirected with the status test.status to the same path with "final", with a body of test.BODY bytes; any other
    path is answered with test.PAYLOAD. The server puts on the queue test.sent the number of bytes of each such body it
    could send before the client closed the connection."""
    test.sent = queue.Queue()
    test.status = 302  # the redirect status the server answers with

    class Handler(BaseHTTPRequestHandler):
        def do_GET(handler):
            if "moved" not in handler.path:
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(test.PAYLOAD)))
                handler.end_headers()
                handler.wfile.write(test.PAYLOAD)
                return
            handler.send_response(test.status)
            handler.send_header("Location", handler.path.replace("moved", "final"))
            handler.send_header("Content-Length", str(test.BODY))
            handler.end_headers()
            sent, chunk = 0, bytes(64 * 1024)
            try:
                while sent < test.BODY:
                    handler.wfile.write(chunk)
                    sent += len(chunk)
            except OSError:  # the client closed the connection
                pass
            finally:
                test.sent.put(sent)

        def log_message(handler, *args):
            pass

    return serve(test, Handler)
