"""自定义连接把 DNS、TCP/TLS、写请求和读响应都绑到同一个绝对 deadline。urllib3
的单阶段 timeout 不足以覆盖 DNS 卡住或 SDK 在超时后继续等待的情况，所以这里
在连接层增加可中止的 socket/timer 机制。
"""

import socket
import sys
from threading import BoundedSemaphore, Event, Thread, Timer
from time import monotonic
from typing import Any

from opensearchpy import Urllib3HttpConnection
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.exceptions import (
    LocationParseError,
    NameResolutionError,
    NewConnectionError,
    ReadTimeoutError,
)
from urllib3.util.connection import allowed_gai_family

from app.core.deadline import current_request_deadline


_DNS_RESOLVER_WORKERS = 10
# getaddrinfo cannot be cancelled, so bound active daemon workers and never queue.
_DNS_RESOLVER_SLOTS = BoundedSemaphore(_DNS_RESOLVER_WORKERS)


class _DeadlineConnectionMixin:
    """给 urllib3 HTTP/HTTPS 连接的 DNS、连接、写入和读取阶段施加同一绝对 deadline。

    urllib3 的单阶段 timeout 无法覆盖不可取消的 DNS 或阻塞 socket。该 mixin 用有界
    resolver worker 和到期时关闭 socket 的方式尽量中止等待，并保持原库的异常类型契约。
    """

    def request(self, *args: Any, **kwargs: Any) -> None:
        """在请求发送阶段执行父类实现，若达到总 deadline 则中止当前 socket。"""
        # request 阶段可能包含连接建立和请求体写入；统一走 timer 包装，避免只
        # 限制 response 读取而让前面的 DNS/发送无限等待。
        deadline = self._phase_deadline()
        if deadline is None:
            return super().request(*args, **kwargs)

        self._run_with_deadline(deadline, super().request, *args, **kwargs)

    def getresponse(self) -> Any:
        """在响应读取阶段执行父类实现，并拒绝 deadline 后才到达的结果。"""
        # 响应返回后再次检查 deadline。即使底层恰好返回一个 response，也不能在
        # 应用总预算已经耗尽时把它当成成功。
        deadline = self._phase_deadline()
        if deadline is None:
            return super().getresponse()

        response = self._run_with_deadline(deadline, super().getresponse)
        if monotonic() >= deadline:
            response.close()
            raise self._deadline_error()
        return response

    def _new_conn(self) -> socket.socket:
        """解析主机并依序尝试地址列表，且每次尝试重新计算剩余连接时间。"""
        # 手动遍历 getaddrinfo 返回的地址，给每个连接尝试重新计算剩余时间；多地址
        # 主机不能因为第一个地址失败就耗尽整个请求预算。
        deadline = self._phase_deadline()
        if deadline is None:
            return super()._new_conn()

        host = self._dns_host
        if host.startswith("["):
            host = host.strip("[]")
        try:
            host.encode("idna")
        except UnicodeError:
            raise LocationParseError(f"'{host}', label empty or too long") from None

        try:
            addresses = self._resolve_addresses(
                host,
                self.port,
                allowed_gai_family(),
                deadline,
            )
            error = None
            for family, socktype, proto, _, address in addresses:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise socket.timeout("OpenSearch connection deadline exhausted")

                sock = None
                try:
                    sock = socket.socket(family, socktype, proto)
                    self.sock = sock
                    for option in self.socket_options or ():
                        sock.setsockopt(*option)
                    sock.settimeout(remaining)
                    if self.source_address:
                        sock.bind(self.source_address)
                    sock.connect(address)
                    error = None
                    break
                except OSError as exc:
                    error = exc
                    if sock is not None:
                        sock.close()
                    if self.sock is sock:
                        self.sock = None
            else:
                if error is not None:
                    raise error
                raise OSError("getaddrinfo returns an empty list")
        except socket.gaierror as exc:
            raise NameResolutionError(self.host, self, exc) from exc
        except socket.timeout as exc:
            raise self._connection_timeout_error() from exc
        except OSError as exc:
            raise NewConnectionError(
                self,
                f"Failed to establish a new connection: {exc}",
            ) from exc

        sys.audit("http.client.connect", self, self.host, self.port)
        return sock

    @staticmethod
    def _resolve_addresses(
        host: str,
        port: int,
        family: socket.AddressFamily,
        deadline: float,
    ) -> list[Any]:
        """在不排队的有界 daemon worker 中运行不可取消的 ``getaddrinfo``。

        调用方超时后仅放弃本次等待，解析线程会在系统调用返回时自行释放槽位；这避免
        DNS 卡住时无限制造线程或让后续请求排在不可取消工作之后。
        """
        # getaddrinfo 本身不可取消，因此使用有界 daemon worker，并且不排队等待
        # resolver 槽位。超时只放弃本次等待，worker 仍会在系统调用返回后自行结束。
        remaining = deadline - monotonic()
        if remaining <= 0 or not _DNS_RESOLVER_SLOTS.acquire(timeout=remaining):
            raise socket.timeout("OpenSearch DNS deadline exhausted")

        completed = Event()
        result: list[list[Any]] = []
        failure: list[BaseException] = []

        def resolve() -> None:
            try:
                result.append(
                    socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
                )
            except BaseException as exc:
                failure.append(exc)
            finally:
                _DNS_RESOLVER_SLOTS.release()
                completed.set()

        worker = Thread(target=resolve, name="opensearch-dns", daemon=True)
        try:
            worker.start()
        except BaseException:
            _DNS_RESOLVER_SLOTS.release()
            raise

        remaining = deadline - monotonic()
        if remaining <= 0 or not completed.wait(remaining):
            raise socket.timeout("OpenSearch DNS deadline exhausted")
        worker.join()
        if failure:
            raise failure[0]
        return result[0]

    def _run_with_deadline(
        self,
        deadline: float,
        callback: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """在 deadline 前执行一个阻塞阶段；超时则关闭阶段 socket 并抛读超时。

        Timer 只负责触发中止，真正的异常仍由当前调用栈返回时转换，避免在线程间直接
        注入异常或让已完成操作被悬挂定时器误杀。
        """
        # Timer 到点会关闭当前 socket，使阻塞中的 urllib3 调用尽快返回；finally
        # 取消 timer，避免一个已经完成的请求被旧定时器误杀。
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise self._deadline_error()

        expired = Event()
        phase_socket = self.sock
        timer = Timer(remaining, self._abort_socket, args=(expired, phase_socket))
        timer.daemon = True
        timer.start()
        try:
            result = callback(*args, **kwargs)
        except Exception as exc:
            if expired.is_set() or monotonic() >= deadline:
                self._abort_socket(expired, phase_socket)
                raise self._deadline_error() from exc
            raise
        finally:
            timer.cancel()
            timer.join()

        if expired.is_set() or monotonic() >= deadline:
            close_result = getattr(result, "close", None)
            if close_result is not None:
                close_result()
            self._abort_socket(expired, phase_socket)
            raise self._deadline_error()
        return result

    def _phase_deadline(self) -> float | None:
        # 连接自身 timeout 和 HTTP 请求总 deadline 取较早者，确保 transport 配置
        # 不能把单阶段等待放宽到应用预算之外。
        now = monotonic()
        deadline = current_request_deadline()
        timeout = self.timeout
        if isinstance(timeout, (int, float)) and not isinstance(timeout, bool):
            transport_deadline = now + float(timeout)
            deadline = (
                transport_deadline
                if deadline is None
                else min(deadline, transport_deadline)
            )
        return deadline

    def _abort_socket(self, expired: Event, phase_socket: Any = None) -> None:
        """标记当前阶段超时并尽力关闭阶段 socket 与最新连接 socket。"""
        # 关闭阶段 socket 和当前 socket，兼容连接对象在 request/getresponse 期间
        # 切换 self.sock 的情况；关闭失败不应掩盖真正的 timeout。
        expired.set()
        sockets = (phase_socket, self.sock)
        for index, sock in enumerate(sockets):
            if sock is None or (index == 1 and sock is phase_socket):
                continue
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _deadline_error() -> ReadTimeoutError:
        return ReadTimeoutError(None, None, "OpenSearch request deadline exhausted")

    @staticmethod
    def _connection_timeout_error() -> ReadTimeoutError:
        return ReadTimeoutError(None, None, "OpenSearch connection timed out")


class _DeadlineHTTPConnection(_DeadlineConnectionMixin, HTTPConnection):
    """将 deadline mixin 应用于 urllib3 的明文 HTTP 连接。"""

    pass


class _DeadlineHTTPSConnection(_DeadlineConnectionMixin, HTTPSConnection):
    """将 deadline mixin 应用于 HTTPS 连接，并把 TLS 握手纳入总预算。"""

    def connect(self) -> None:
        """执行父类 TCP/TLS 连接流程，或在 deadline 到期时通过 mixin 中止。"""
        deadline = self._phase_deadline()
        if deadline is None:
            return super().connect()

        self._run_with_deadline(deadline, super().connect)


class DeadlineUrllib3HttpConnection(Urllib3HttpConnection):
    """供 opensearch-py 使用的连接工厂，只替换底层 urllib3 ConnectionCls。"""

    def _create_urllib3_pool(self) -> None:
        """保留 SDK 的池/认证/SSL 配置，仅注入带 deadline 的 HTTP(S) 连接类型。"""
        # 只替换连接池使用的 ConnectionCls，保留 opensearch-py 的 pool/auth/SSL
        # 配置，尽量把 deadline 能力限制在 transport 层。
        super()._create_urllib3_pool()
        if self.pool is not None:
            self.pool.ConnectionCls = (
                _DeadlineHTTPSConnection if self.use_ssl else _DeadlineHTTPConnection
            )
