#!/usr/bin/env python3
"""Minimal UDP SIP auto-answer service for the GXW4108 inbound-call POC."""

import argparse
import logging
import random
import re
import socket
import struct
import time
from pathlib import Path


def header(message: str, name: str) -> str:
    match = re.search(rf"(?im)^{re.escape(name)}:\s*(.+?)\s*$", message)
    return match.group(1) if match else ""


def tagged_to(value: str, tag: str) -> str:
    return value if re.search(r";\s*tag=", value, re.I) else f"{value};tag={tag}"


def response(
    message: str,
    code: int,
    reason: str,
    tag: str,
    body: str = "",
    contact: str = "",
) -> bytes:
    lines = [f"SIP/2.0 {code} {reason}"]
    lines.extend(f"Via: {value}" for value in re.findall(r"(?im)^Via:\s*(.+?)\s*$", message))
    lines.extend(
        [
            f"From: {header(message, 'From')}",
            f"To: {tagged_to(header(message, 'To'), tag)}",
            f"Call-ID: {header(message, 'Call-ID')}",
            f"CSeq: {header(message, 'CSeq')}",
            "Server: re8ch-gxw-autoanswer/1.0",
        ]
    )
    if contact:
        lines.append(f"Contact: <{contact}>")
    if body:
        lines.extend(["Content-Type: application/sdp", f"Content-Length: {len(body)}", "", body])
    else:
        lines.extend(["Content-Length: 0", "", ""])
    return "\r\n".join(lines).encode()


def media_target(invite: str, peer_ip: str) -> tuple[str, int]:
    address = re.search(r"(?im)^c=IN IP4\s+(\S+)", invite)
    port = re.search(r"(?im)^m=audio\s+(\d+)", invite)
    if not port:
        raise ValueError("INVITE has no audio media port")
    return (address.group(1) if address else peer_ip, int(port.group(1)))


def request_uri(invite: str, peer_ip: str, peer_port: int) -> str:
    contact = header(invite, "Contact")
    match = re.search(r"<(sip:[^>]+)>", contact)
    return match.group(1) if match else f"sip:{peer_ip}:{peer_port}"


def send_announcement(rtp: socket.socket, target: tuple[str, int], audio: bytes) -> None:
    sequence = random.randrange(0, 65536)
    timestamp = random.randrange(0, 2**32)
    ssrc = random.randrange(1, 2**32)
    started = time.monotonic()
    for offset in range(0, len(audio), 160):
        payload = audio[offset : offset + 160]
        if len(payload) < 160:
            payload += b"\xff" * (160 - len(payload))
        packet = struct.pack("!BBHII", 0x80, 0, sequence, timestamp, ssrc) + payload
        rtp.sendto(packet, target)
        sequence = (sequence + 1) & 0xFFFF
        timestamp = (timestamp + 160) & 0xFFFFFFFF
        deadline = started + (offset // 160 + 1) * 0.020
        time.sleep(max(0, deadline - time.monotonic()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-ip", default="192.0.2.97")
    parser.add_argument("--sip-port", type=int, default=5160)
    parser.add_argument("--rtp-port", type=int, default=40002)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--ring-seconds", type=float, default=0.8)
    parser.add_argument("--post-audio-seconds", type=float, default=0.5)
    parser.add_argument("--allowed-peer", default="192.0.2.208")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    audio = args.audio.read_bytes()
    sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sip.bind((args.listen_ip, args.sip_port))
    rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rtp.bind((args.listen_ip, args.rtp_port))
    logging.info("listening SIP UDP %s:%d", args.listen_ip, args.sip_port)

    while True:
        data, peer = sip.recvfrom(65535)
        message = data.decode(errors="replace")
        if not message.startswith("INVITE "):
            continue
        if args.allowed_peer and peer[0] != args.allowed_peer:
            logging.warning("rejected INVITE from unexpected peer=%s", peer[0])
            sip.sendto(response(message, 403, "Forbidden", "rejected"), peer)
            continue
        call_id = header(message, "Call-ID")
        tag = f"{random.randrange(2**48):012x}"
        try:
            target = media_target(message, peer[0])
            sip.sendto(response(message, 100, "Trying", tag), peer)
            sip.sendto(response(message, 180, "Ringing", tag), peer)
            time.sleep(args.ring_seconds)
            sdp = (
                "v=0\r\n"
                f"o=autoanswer 1 1 IN IP4 {args.listen_ip}\r\n"
                "s=GXW auto answer\r\n"
                f"c=IN IP4 {args.listen_ip}\r\n"
                "t=0 0\r\n"
                f"m=audio {args.rtp_port} RTP/AVP 0\r\n"
                "a=rtpmap:0 PCMU/8000\r\n"
                "a=ptime:20\r\n"
                "a=sendonly\r\n"
            )
            contact = f"sip:autoanswer@{args.listen_ip}:{args.sip_port}"
            sip.sendto(response(message, 200, "OK", tag, sdp, contact), peer)
            logging.info("answered call_id=%s media=%s:%d", call_id, *target)
            sip.settimeout(2.0)
            try:
                while True:
                    ack, _ = sip.recvfrom(65535)
                    if ack.startswith(b"ACK "):
                        break
            except TimeoutError:
                logging.warning("ACK timeout call_id=%s; playing anyway", call_id)
            finally:
                sip.settimeout(None)

            send_announcement(rtp, target, audio)
            time.sleep(args.post_audio_seconds)
            branch = f"z9hG4bK-{random.randrange(2**48):012x}"
            bye = (
                f"BYE {request_uri(message, *peer)} SIP/2.0\r\n"
                f"Via: SIP/2.0/UDP {args.listen_ip}:{args.sip_port};branch={branch};rport\r\n"
                f"From: {tagged_to(header(message, 'To'), tag)}\r\n"
                f"To: {header(message, 'From')}\r\n"
                f"Call-ID: {call_id}\r\n"
                "CSeq: 1 BYE\r\n"
                f"Contact: <sip:autoanswer@{args.listen_ip}:{args.sip_port}>\r\n"
                "Max-Forwards: 70\r\n"
                "Content-Length: 0\r\n\r\n"
            ).encode()
            sip.sendto(bye, peer)
            logging.info("announcement complete; sent BYE call_id=%s", call_id)
        except Exception:
            logging.exception("call failed call_id=%s", call_id)


if __name__ == "__main__":
    main()
