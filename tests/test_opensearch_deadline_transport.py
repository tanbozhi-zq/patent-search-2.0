"""验证自定义 OpenSearch 传输对读写、DNS 与多地址连接的总截止时间约束。"""

import json
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Lock, Thread, Timer, enumerate as enumerate_threads
from textwrap import dedent
from time import monotonic, sleep

import pytest

from app.core.config import Settings
from app.core.deadline import request_deadline
from app.core.exceptions import SearchDependencyTimeoutError
from app.repositories.opensearch_repo import OpenSearchRepository


EMPTY_SEARCH_RESPONSE = {"hits": {"total": {"value": 0}, "hits": []}}
RESPONSE_BODY = json.dumps(EMPTY_SEARCH_RESPONSE, separators=(",", ":")).encode()


def _repository(host: str, port: int) -> OpenSearchRepository:
    return OpenSearchRepository(
        settings=Settings(
            _env_file=None,
            opensearch_host=host,
            opensearch_port=port,
            opensearch_use_https=False,
            opensearch_timeout_seconds=5,
            opensearch_max_retries=0,
            patent_search_deadline_seconds=5,
        )
    )


class TrickleThenFastHandler(BaseHTTPRequestHandler):
    calls = 0
    calls_lock = Lock()

    def do_POST(self):
        with self.calls_lock:
            self.__class__.calls += 1
            call_number = self.__class__.calls

        body = RESPONSE_BODY + (b" " * 64 if call_number == 1 else b"")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            if call_number == 1:
                for byte in body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    sleep(0.03)
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


def test_native_client_interrupts_trickle_response_and_recovers():
    TrickleThenFastHandler.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), TrickleThenFastHandler)
    server.daemon_threads = True
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    repository = _repository("127.0.0.1", server.server_port)

    try:
        started = monotonic()
        with request_deadline(0.2):
            with pytest.raises(SearchDependencyTimeoutError):
                repository.search({"query": {"match_all": {}}})
        assert monotonic() - started < 1.0

        with request_deadline(1):
            assert repository.search({"query": {"match_all": {}}}) == EMPTY_SEARCH_RESPONSE
    finally:
        repository.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)


def test_native_client_deadline_includes_dns_resolution(monkeypatch):
    release_dns = Event()
    release_timer = Timer(0.25, release_dns.set)

    def slow_dns(*args, **kwargs):
        release_dns.wait()
        raise socket.gaierror(socket.EAI_NONAME, "simulated slow DNS")

    monkeypatch.setattr(socket, "getaddrinfo", slow_dns)
    repository = _repository("slow-dns.invalid", 9200)
    release_timer.start()

    try:
        started = monotonic()
        with request_deadline(0.05):
            with pytest.raises(SearchDependencyTimeoutError):
                repository.search({"query": {"match_all": {}}})
        assert monotonic() - started < 0.15
    finally:
        release_dns.set()
        release_timer.cancel()
        release_timer.join()
        repository.close()


def test_native_client_deadline_is_shared_across_resolved_addresses(monkeypatch):
    addresses = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (host, 9200))
        for host in ("127.0.0.1", "127.0.0.2", "127.0.0.3")
    ]
    connect_timeouts = []

    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: addresses)

    def slow_connect(sock, address):
        timeout = sock.gettimeout()
        connect_timeouts.append(timeout)
        sleep(timeout)
        raise socket.timeout("simulated connect timeout")

    monkeypatch.setattr(socket.socket, "connect", slow_connect)
    repository = _repository("multi-address.invalid", 9200)

    try:
        started = monotonic()
        with request_deadline(0.05):
            with pytest.raises(SearchDependencyTimeoutError):
                repository.search({"query": {"match_all": {}}})
        assert monotonic() - started < 0.15
        assert len(connect_timeouts) == 1
    finally:
        repository.close()


def test_repeated_dns_deadlines_keep_resolver_threads_bounded(monkeypatch):
    release_dns = Event()

    def blocked_dns(*args, **kwargs):
        release_dns.wait()
        raise socket.gaierror(socket.EAI_NONAME, "simulated blocked DNS")

    monkeypatch.setattr(socket, "getaddrinfo", blocked_dns)
    repository = _repository("blocked-dns.invalid", 9200)

    def exhaust_deadlines() -> None:
        for _ in range(20):
            with request_deadline(0.005):
                with pytest.raises(SearchDependencyTimeoutError):
                    repository.search({"query": {"match_all": {}}})

    def resolver_thread_count() -> int:
        return sum(
            thread.name.startswith("opensearch-dns")
            for thread in enumerate_threads()
        )

    try:
        exhaust_deadlines()
        first_count = resolver_thread_count()
        exhaust_deadlines()

        assert first_count > 0
        assert first_count <= 10
        assert resolver_thread_count() == first_count
    finally:
        release_dns.set()
        sleep(0.05)
        repository.close()


def test_native_connect_timeout_maps_to_50401(monkeypatch):
    addresses = [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("127.0.0.1", 9200),
        )
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: addresses)

    def connect_timeout(sock, address):
        raise socket.timeout("simulated native connect timeout")

    monkeypatch.setattr(socket.socket, "connect", connect_timeout)
    repository = _repository("connect-timeout.invalid", 9200)

    try:
        with request_deadline(1):
            with pytest.raises(SearchDependencyTimeoutError) as captured:
                repository.search({"query": {"match_all": {}}})
        assert int(captured.value.code) == 50401
    finally:
        repository.close()


def test_blocked_dns_does_not_prevent_process_exit():
    script = dedent(
        """\
        import logging
        import socket
        from threading import Event

        from app.core.config import Settings
        from app.core.deadline import request_deadline
        from app.core.exceptions import SearchDependencyTimeoutError
        from app.repositories.opensearch_repo import OpenSearchRepository

        logging.disable(logging.CRITICAL)
        blocked = Event()
        socket.getaddrinfo = lambda *args, **kwargs: blocked.wait()
        repository = OpenSearchRepository(settings=Settings(
            _env_file=None,
            opensearch_host="blocked-dns.invalid",
            opensearch_port=9200,
            opensearch_use_https=False,
            opensearch_timeout_seconds=5,
            opensearch_max_retries=0,
            patent_search_deadline_seconds=5,
        ))
        try:
            with request_deadline(0.02):
                repository.search({"query": {"match_all": {}}})
        except SearchDependencyTimeoutError:
            pass
        repository.close()
        print("reached_process_exit", flush=True)
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )

    assert completed.stdout.strip() == "reached_process_exit"
