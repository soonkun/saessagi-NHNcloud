#!/usr/bin/env python3
"""WebSocket 텍스트 대화 테스트 스크립트.

CR-38 이후 /client-ws도 인증 대상이므로, 인증이 켜져 있으면 먼저 로그인해
세션 쿠키를 핸드셰이크에 실어 보낸다. 이게 없으면 HTTP 403으로 거부된다.
"""

import asyncio
import json
import sys
from pathlib import Path

import websockets

_ROOT = Path(__file__).resolve().parent.parent


def _session_cookie(port: int) -> str | None:
    """conf.yaml의 비밀번호로 로그인해 세션 쿠키를 얻는다. 인증 off면 None."""
    conf = _ROOT / "conf.yaml"
    if not conf.exists():
        return None
    try:
        import yaml

        cfg = yaml.safe_load(conf.read_text(encoding="utf-8")) or {}
        web = (cfg.get("app") or {}).get("web") or {}
        if not web.get("auth_enabled"):
            return None
        password = str(web.get("auth_password") or "")
        if not password:
            return None

        import httpx

        res = httpx.post(
            f"http://127.0.0.1:{port}/api/auth/login",
            json={"password": password},
            timeout=30,
        )
        if res.status_code != 200:
            print(f"[경고] 로그인 실패({res.status_code}) — 인증 없이 시도합니다.")
            return None
        token = res.cookies.get("saessagi_session")
        return f"saessagi_session={token}" if token else None
    except Exception as exc:  # 인증이 없거나 서버가 구버전일 수 있다
        print(f"[경고] 로그인 생략: {exc!r}")
        return None


async def test_text_chat(message: str = "안녕하세요!", port: int = 12393) -> None:
    uri = f"ws://127.0.0.1:{port}/client-ws"
    print(f"[연결] {uri}")

    cookie = _session_cookie(port)
    headers = {"Cookie": cookie} if cookie else {}
    if cookie:
        print("[인증] 세션 쿠키 획득")

    async with websockets.connect(
        uri, max_size=16 * 1024 * 1024, additional_headers=headers
    ) as ws:  # 16MB limit
        # 초기 메시지 수신 (start-mic까지)
        print("[대기] 서버 초기화...")
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
                mtype = msg.get("type", "?")
                if mtype not in ("group-update",):
                    print(f"  [{mtype}] {str(msg.get('text', msg))[:80]}")
                if mtype == "control" and msg.get("text") == "start-mic":
                    break
        except asyncio.TimeoutError:
            print("  [타임아웃] 초기화 대기 종료")

        # 텍스트 입력 전송
        print(f"\n[전송] {message!r}")
        await ws.send(json.dumps({"type": "text-input", "text": message}))

        # 응답 수신
        print("[대기] AI 응답...\n")
        synth_count = 0
        audio_count = 0
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=90.0))
                mtype = msg.get("type", "?")

                if mtype == "full-text":
                    txt = msg.get("text", "")
                    if txt != "Thinking...":
                        print(f"  [AI 전체응답] {txt[:200]}")
                elif mtype == "sentence":
                    print(f"  [문장] {msg.get('text', '')[:120]}")
                elif mtype == "audio":
                    audio_count += 1
                    audio_b64 = msg.get("audio", "")
                    print(f"  [오디오#{audio_count}] base64 길이={len(audio_b64)}")
                elif mtype == "backend-synth-complete":
                    synth_count += 1
                    print(f"  [synth-complete #{synth_count}] → frontend-playback-complete 전송")
                    await ws.send(json.dumps({"type": "frontend-playback-complete"}))
                elif mtype == "tool_call_status":
                    print(f"  [툴콜] {msg.get('tool_name')} status={msg.get('status')}")
                elif mtype == "control":
                    ctrl = msg.get("text", "")
                    print(f"  [제어] {ctrl}")
                    if ctrl == "conversation-chain-end":
                        break
                elif mtype == "error":
                    print(f"  [오류] {msg.get('message', msg)}")
                elif mtype in ("force-new-message", "set-model-and-conf"):
                    pass  # 무시
                else:
                    print(f"  [{mtype}] {str(msg)[:80]}")
        except asyncio.TimeoutError:
            print("  [타임아웃] 응답 대기 종료")

        print(f"\n[완료] 오디오={audio_count}개, synth-complete={synth_count}개")


if __name__ == "__main__":
    args = sys.argv[1:]
    port = 12393
    if args and args[-1].isdigit():
        port = int(args.pop())
    msg = " ".join(args) if args else "안녕하세요!"
    asyncio.run(test_text_chat(msg, port=port))
