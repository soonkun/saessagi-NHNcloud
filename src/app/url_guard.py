# src/app/url_guard.py
"""OLLAMA_BASE_URL 화이트리스트 검증 — loopback / RFC1918 사설 대역만 허용."""

import ipaddress
from urllib.parse import urlparse

from loguru import logger

from .errors import PrivacyViolationError

_ALLOWED_SCHEMES = {"http", "https", "ws", "wss", "bolt", "neo4j"}  # bolt/neo4j: M_19 GraphRAG

_PRIVATE_NETWORKS_V4 = [
    ipaddress.IPv4Network("127.0.0.0/8"),  # loopback
    ipaddress.IPv4Network("10.0.0.0/8"),  # RFC1918 class A
    ipaddress.IPv4Network("172.16.0.0/12"),  # RFC1918 class B
    ipaddress.IPv4Network("192.168.0.0/16"),  # RFC1918 class C
    ipaddress.IPv4Network("169.254.0.0/16"),  # link-local
]

_PRIVATE_NETWORKS_V6 = [
    ipaddress.IPv6Network("::1/128"),  # loopback
    ipaddress.IPv6Network("fc00::/7"),  # ULA
    ipaddress.IPv6Network("fe80::/10"),  # link-local
]


def is_private_or_loopback(url: str) -> bool:
    """다음 조건 중 하나 이상을 만족할 때 True.

    - scheme in {"http","https","ws","wss"} 이면서 host가
        * 127.0.0.0/8
        * ::1
        * 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 (RFC1918)
        * fc00::/7 (IPv6 ULA)
        * fe80::/10 (link-local)
        * 169.254.0.0/16 (link-local, 특수 환경 대응)
        * "localhost" (정확히 일치)
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False

    hostname = parsed.hostname
    if hostname is None:
        return False

    if hostname.lower() == "localhost":
        return True

    # IPv6 주소는 대괄호로 감싸지므로 hostname이 이미 strip된 상태
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # FQDN — DNS 조회 없이 거부
        return False

    if isinstance(addr, ipaddress.IPv4Address):
        return any(addr in net for net in _PRIVATE_NETWORKS_V4)
    else:  # IPv6Address
        return any(addr in net for net in _PRIVATE_NETWORKS_V6)


def enforce_private_url(url: str, *, field_name: str = "OLLAMA_BASE_URL") -> None:
    """is_private_or_loopback 위반 시 PrivacyViolationError 발생.
    포트 범위(1~65535) 검증도 수행.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise PrivacyViolationError(f"{field_name}: URL 파싱 실패: {url}") from exc

    try:
        port = parsed.port
    except ValueError as exc:
        # urllib.parse가 포트 범위 초과 시 ValueError를 던짐
        raise PrivacyViolationError(f"{field_name}: 포트 범위 위반 (1~65535): {exc}") from exc
    if port is not None and not (1 <= port <= 65535):
        raise PrivacyViolationError(f"{field_name}: 포트 범위 위반 (1~65535): {port}")

    if not is_private_or_loopback(url):
        logger.error(f"{field_name} 화이트리스트 위반: {url}")
        raise PrivacyViolationError(f"{field_name} must be loopback or RFC1918 private: {url}")
