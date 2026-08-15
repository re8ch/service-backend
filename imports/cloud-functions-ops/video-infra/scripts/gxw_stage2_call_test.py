#!/usr/bin/env python3
"""GXW4108 stage-2 diagnostic call with RFC2833 DTMF and PCMU capture."""

import argparse
import random
import re
import secrets
import socket
import struct
import time
import wave


EVENTS = {str(i): i for i in range(10)} | {"*": 10, "#": 11}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", nargs="?", help="authorized PSTN number")
    parser.add_argument("--local-ip", default="192.0.2.169")
    parser.add_argument("--local-port", type=int, default=5160)
    parser.add_argument("--gateway-ip", default="192.0.2.4")
    parser.add_argument("--gateway-port", type=int, default=5060)
    parser.add_argument("--rtp-port", type=int, default=40000)
    parser.add_argument("--port-selector", default="991")
    parser.add_argument("--dialtone-seconds", type=float, default=3.0)
    parser.add_argument("--post-dial-seconds", type=float, default=20.0)
    parser.add_argument("--tone-ms", type=int, default=150)
    parser.add_argument("--gap-ms", type=int, default=150)
    parser.add_argument("--wav", required=True)
    parser.add_argument("--confirm-call", action="store_true")
    parser.add_argument(
        "--no-dial",
        action="store_true",
        help="off-hook and record dial tone without sending DTMF",
    )
    parser.add_argument(
        "--confirm-round-robin",
        action="store_true",
        help="acknowledge that GXW stage 2 may ignore the 99+port selector",
    )
    args = parser.parse_args()
    if not args.confirm_call:
        parser.error("refusing to place a call without --confirm-call")
    if not args.no_dial and not args.confirm_round_robin:
        parser.error(
            "stage 2 may round-robin to any FXO; "
            "refusing without --confirm-round-robin"
        )
    if args.no_dial and args.destination:
        parser.error("destination must be omitted with --no-dial")
    if not args.no_dial and not args.destination:
        parser.error("destination is required unless --no-dial is present")
    if args.destination and not re.fullmatch(r"[0-9*#]+", args.destination):
        parser.error("destination must contain only DTMF digits")
    return args


def ulaw_to_linear(sample):
    sample = ~sample & 0xFF
    sign = sample & 0x80
    exponent = (sample >> 4) & 0x07
    mantissa = sample & 0x0F
    value = ((mantissa << 3) + 0x84) << exponent
    value -= 0x84
    return -value if sign else value


def main():
    args = parse_args()
    sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sip.bind((args.local_ip, args.local_port))
    sip.settimeout(1)
    rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rtp.bind((args.local_ip, args.rtp_port))
    rtp.setblocking(False)

    call_id = f"{secrets.token_hex(8)}@{args.local_ip}"
    from_tag = secrets.token_hex(6)
    branch = f"z9hG4bK-{secrets.token_hex(8)}"
    uri = f"sip:{args.port_selector}@{args.gateway_ip}:{args.gateway_port}"
    sdp = (
        "v=0\r\n"
        f"o=gxw-stage2 1 1 IN IP4 {args.local_ip}\r\n"
        "s=GXW4108 stage-2 diagnostic\r\n"
        f"c=IN IP4 {args.local_ip}\r\n"
        "t=0 0\r\n"
        f"m=audio {args.rtp_port} RTP/AVP 0 101\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=rtpmap:101 telephone-event/8000\r\n"
        "a=fmtp:101 0-16\r\n"
        "a=sendrecv\r\n"
    )
    invite = (
        f"INVITE {uri} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP {args.local_ip}:{args.local_port};branch={branch};rport\r\n"
        "Max-Forwards: 70\r\n"
        f"From: <sip:gxw-stage2@{args.local_ip}>;tag={from_tag}\r\n"
        f"To: <{uri}>\r\n"
        f"Call-ID: {call_id}\r\n"
        "CSeq: 1 INVITE\r\n"
        f"Contact: <sip:gxw-stage2@{args.local_ip}:{args.local_port}>\r\n"
        "Allow: INVITE, ACK, CANCEL, BYE, OPTIONS\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(sdp.encode())}\r\n\r\n{sdp}"
    )

    def send_sip(message):
        sip.sendto(message.encode(), (args.gateway_ip, args.gateway_port))

    print(f"Sending stage-2 INVITE; selector={args.port_selector}")
    send_sip(invite)
    to_header = f"To: <{uri}>"
    remote_rtp = None
    answered = False
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            data, peer = sip.recvfrom(65535)
        except socket.timeout:
            continue
        message = data.decode(errors="replace")
        status = message.split("\r\n", 1)[0]
        print(f"{peer[0]}:{peer[1]} -> {status}")
        match = re.search(r"^To:\s*(.+)$", message, re.I | re.M)
        if match:
            to_header = "To: " + match.group(1).strip()
        if status.startswith("SIP/2.0 2"):
            address = re.search(r"^c=IN IP4\s+(\S+)", message, re.M)
            port = re.search(r"^m=audio\s+(\d+)", message, re.M)
            if not port:
                raise RuntimeError("GXW 2xx response has no audio port")
            remote_rtp = (
                address.group(1) if address else args.gateway_ip,
                int(port.group(1)),
            )
            ack = (
                f"ACK {uri} SIP/2.0\r\n"
                f"Via: SIP/2.0/UDP {args.local_ip}:{args.local_port};"
                f"branch=z9hG4bK-{secrets.token_hex(8)};rport\r\n"
                "Max-Forwards: 70\r\n"
                f"From: <sip:gxw-stage2@{args.local_ip}>;tag={from_tag}\r\n"
                f"{to_header}\r\nCall-ID: {call_id}\r\nCSeq: 1 ACK\r\n"
                f"Contact: <sip:gxw-stage2@{args.local_ip}:{args.local_port}>\r\n"
                "Content-Length: 0\r\n\r\n"
            )
            send_sip(ack)
            answered = True
            break
        if re.match(r"SIP/2.0 [456]\d\d", status):
            break

    if not answered:
        raise RuntimeError("stage-2 session did not reach 2xx")

    pcm = bytearray()

    def receive_audio(duration):
        until = time.time() + duration
        while time.time() < until:
            try:
                packet, _ = rtp.recvfrom(65535)
            except BlockingIOError:
                time.sleep(0.005)
                continue
            if len(packet) >= 12 and (packet[1] & 0x7F) == 0:
                header_len = 12 + 4 * (packet[0] & 0x0F)
                for value in packet[header_len:]:
                    pcm.extend(struct.pack("<h", ulaw_to_linear(value)))

    print(f"Recording dial tone for {args.dialtone_seconds:.1f}s")
    receive_audio(args.dialtone_seconds)

    sequence = random.randrange(65536)
    timestamp = random.randrange(2**32)
    ssrc = random.randrange(2**32)
    tone_ticks = args.tone_ms * 8
    step_ticks = 400
    for digit in args.destination or "":
        event = EVENTS[digit]
        duration = 0
        first = True
        while duration < tone_ticks:
            duration = min(duration + step_ticks, tone_ticks)
            marker = 0x80 if first else 0
            header = struct.pack(
                "!BBHII", 0x80, marker | 101, sequence, timestamp, ssrc
            )
            payload = struct.pack("!BBH", event, 10, duration)
            rtp.sendto(header + payload, remote_rtp)
            sequence = (sequence + 1) & 0xFFFF
            first = False
            receive_audio(step_ticks / 8000)
        for _ in range(3):
            header = struct.pack("!BBHII", 0x80, 101, sequence, timestamp, ssrc)
            payload = struct.pack("!BBH", event, 0x80 | 10, tone_ticks)
            rtp.sendto(header + payload, remote_rtp)
            sequence = (sequence + 1) & 0xFFFF
        print(f"Sent RFC2833 digit {digit}")
        receive_audio(args.gap_ms / 1000)
        timestamp = (timestamp + (args.tone_ms + args.gap_ms) * 8) & 0xFFFFFFFF

    if args.no_dial:
        print("No DTMF sent; ending off-hook validation.")
    else:
        print(f"Recording post-dial audio for {args.post_dial_seconds:.1f}s")
        receive_audio(args.post_dial_seconds)
    with wave.open(args.wav, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(pcm)
    print(f"Saved {len(pcm) // 2 / 8000:.2f}s PCMU audio to {args.wav}")

    bye = (
        f"BYE {uri} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP {args.local_ip}:{args.local_port};"
        f"branch=z9hG4bK-{secrets.token_hex(8)};rport\r\n"
        "Max-Forwards: 70\r\n"
        f"From: <sip:gxw-stage2@{args.local_ip}>;tag={from_tag}\r\n"
        f"{to_header}\r\nCall-ID: {call_id}\r\nCSeq: 2 BYE\r\n"
        f"Contact: <sip:gxw-stage2@{args.local_ip}:{args.local_port}>\r\n"
        "Content-Length: 0\r\n\r\n"
    )
    send_sip(bye)
    print("Sent BYE.")


if __name__ == "__main__":
    main()
