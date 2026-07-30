#!/usr/bin/env python3
"""Minimal SIP-to-GXW4108 call probe.

This script can place a real external telephone call. It refuses to transmit
unless --confirm-call is present. Use a lab/authorized destination only.
"""

import argparse
import re
import secrets
import socket
import time


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", help="GXW Request-URI user, e.g. 991 + number")
    parser.add_argument("--local-ip", default="192.0.2.169")
    parser.add_argument("--local-port", type=int, default=5160)
    parser.add_argument("--gateway-ip", default="192.0.2.4")
    parser.add_argument("--gateway-port", type=int, default=5060)
    parser.add_argument("--rtp-port", type=int, default=40000)
    parser.add_argument("--ring-seconds", type=int, default=15)
    parser.add_argument("--answered-seconds", type=int, default=3)
    parser.add_argument(
        "--confirm-call",
        action="store_true",
        help="required acknowledgement that this may place a real call",
    )
    args = parser.parse_args()
    if not args.confirm_call:
        parser.error("refusing to place a call without --confirm-call")
    return args


def first_line(message):
    return message.split("\r\n", 1)[0]


def main():
    args = parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.local_ip, args.local_port))
    sock.settimeout(1)

    call_id = f"{secrets.token_hex(8)}@{args.local_ip}"
    from_tag = secrets.token_hex(6)
    branch = f"z9hG4bK-{secrets.token_hex(8)}"
    uri = f"sip:{args.destination}@{args.gateway_ip}:{args.gateway_port}"
    sdp = (
        "v=0\r\n"
        f"o=gxw-poc 1 1 IN IP4 {args.local_ip}\r\n"
        "s=GXW4108 diagnostic call\r\n"
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
        f"Via: SIP/2.0/UDP {args.local_ip}:{args.local_port};"
        f"branch={branch};rport\r\n"
        "Max-Forwards: 70\r\n"
        f"From: <sip:gxw-poc@{args.local_ip}>;tag={from_tag}\r\n"
        f"To: <{uri}>\r\n"
        f"Call-ID: {call_id}\r\n"
        "CSeq: 1 INVITE\r\n"
        f"Contact: <sip:gxw-poc@{args.local_ip}:{args.local_port}>\r\n"
        "Allow: INVITE, ACK, CANCEL, BYE, OPTIONS\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(sdp.encode())}\r\n\r\n{sdp}"
    )

    def send(message):
        sock.sendto(message.encode(), (args.gateway_ip, args.gateway_port))

    print(
        f"Sending INVITE to {args.gateway_ip}:{args.gateway_port}; "
        f"Request-URI user={args.destination}"
    )
    send(invite)
    started = time.time()
    to_header = f"To: <{uri}>"
    answered = False
    last_status = ""

    while time.time() - started < args.ring_seconds:
        try:
            data, peer = sock.recvfrom(65535)
        except socket.timeout:
            continue
        message = data.decode(errors="replace")
        status = first_line(message)
        if status != last_status:
            print(f"{peer[0]}:{peer[1]} -> {status}")
            last_status = status
        match = re.search(r"^To:\s*(.+)$", message, re.I | re.M)
        if match:
            to_header = "To: " + match.group(1).strip()
        if status.startswith("SIP/2.0 2"):
            answered = True
            ack = (
                f"ACK {uri} SIP/2.0\r\n"
                f"Via: SIP/2.0/UDP {args.local_ip}:{args.local_port};"
                f"branch=z9hG4bK-{secrets.token_hex(8)};rport\r\n"
                "Max-Forwards: 70\r\n"
                f"From: <sip:gxw-poc@{args.local_ip}>;tag={from_tag}\r\n"
                f"{to_header}\r\n"
                f"Call-ID: {call_id}\r\n"
                "CSeq: 1 ACK\r\n"
                f"Contact: <sip:gxw-poc@{args.local_ip}:{args.local_port}>\r\n"
                "Content-Length: 0\r\n\r\n"
            )
            send(ack)
            time.sleep(args.answered_seconds)
            bye = ack.replace("ACK ", "BYE ", 1).replace(
                "CSeq: 1 ACK", "CSeq: 2 BYE"
            )
            send(bye)
            print(f"Sent BYE after {args.answered_seconds} seconds.")
            break
        if re.match(r"SIP/2.0 [456]\d\d", status):
            break

    if not answered:
        cancel = invite.split("\r\n\r\n", 1)[0]
        cancel = cancel.replace("INVITE ", "CANCEL ", 1)
        cancel = re.sub(r"CSeq: 1 INVITE", "CSeq: 1 CANCEL", cancel)
        cancel = re.sub(r"Content-Type:.*\r\n", "", cancel)
        cancel = re.sub(r"Content-Length:.*", "Content-Length: 0", cancel)
        send(cancel + "\r\n\r\n")
        print("Sent CANCEL after test window.")


if __name__ == "__main__":
    main()

