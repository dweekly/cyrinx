#!/usr/bin/env python3
"""Reference codec and golden-vector generator for Cyrinx chat envelope v1.

Layout, error names, and required-coverage list are pinned by
`Apps/Chat/ENVELOPE.md` (which itself transcribes the C3-28 design brief).
This script is a THIRD independent implementation of the envelope v1 codec,
kept in the repository permanently as the cross-language oracle -- see
AGENTS.md / project convention "ship the spike": standalone validators stay
in the tree as reproducible diagnostics, not just scaffolding to throw away.
The Swift codec (`Apps/Chat/CyrinxChatKit`) and the Kotlin codec
(`Apps/Chat/android/chatkit`) are checked against the fixture this script
emits, not against each other directly.

Usage
-----
    .venv/bin/python Apps/Chat/fixtures/tools/generate_golden.py --self-test
        Decode every "decode" vector and check every field; re-encode every
        decoded value and check byte-identity (canonical encoding); attempt
        to decode every "error" vector and check it raises exactly the named
        error. Does NOT write the fixture file. Exits 1 on any mismatch.

    .venv/bin/python Apps/Chat/fixtures/tools/generate_golden.py
        Run the same self-test first (a fixture is never written after a
        failing self-test), then write
        Apps/Chat/fixtures/chat-envelope-golden.json.

    .venv/bin/python Apps/Chat/fixtures/tools/generate_golden.py --out PATH
        Override the output path (mainly for testing this script itself).

Regeneration policy (see Apps/Chat/ENVELOPE.md "Fixture provenance and
regeneration policy"): regenerating chat-envelope-golden.json is permitted
only alongside a spec change to this file/ENVELOPE.md. The emitted JSON is
committed and diffed in review like any other source file -- it is not a
build artifact.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Wire constants
#
# Every numeric constant below is pinned by the byte-layout table in
# Apps/Chat/ENVELOPE.md Section 1 (itself transcribed from the C3-28 design
# brief). None of these are free parameters of this script; changing one
# here without a matching spec change is a bug.
# ---------------------------------------------------------------------------

VERSION_1 = 0x01
"""Envelope wire version 1 (offset 0). The only version this codec accepts
on decode. Future versions bump this byte -- ENVELOPE.md is explicit that
version bumps never use flag bits."""

KIND_TEXT = 0x01
"""The only message kind defined in v1 (offset 1)."""

FLAG_REPLY_TO_PRESENT = 0x01
"""Bit 0 of the flags byte (offset 2). Every other bit must be zero; a set
unknown bit is `malformed`, not silently ignored, so a future flag can be
added without breaking old decoders' error reporting."""

MESSAGE_ID_LEN = 16
"""128-bit message ID, offset 3 (fixed width, always present)."""

REPLY_TO_ID_LEN = 16
"""128-bit reply-to ID, present iff FLAG_REPLY_TO_PRESENT is set."""

MIN_SENDER_ID_LEN = 1
MAX_SENDER_ID_LEN = 32
"""senderIdLen valid range is 1..32 inclusive; 0 or >32 is `malformed`."""

MAX_BODY_LEN = 2048
"""Body cap in bytes (2 KiB), matching the Phase F product-scope cap.
bodyLen > MAX_BODY_LEN is `oversizeBody`, checked before any attempt to read
body bytes -- see the "range before availability" parse rule below."""

BODY_LEN_FIELD_WIDTH = 2
"""bodyLen is a big-endian u16 on the wire (max representable 65535), but the
*semantic* max is MAX_BODY_LEN (2048); the two are independent constants."""

# Fixed non-variable-length header size when replyTo is absent:
# version(1) + kind(1) + flags(1) + messageId(16) + senderIdLen(1) = 20
# bytes before the variable-length senderId. Not used directly by the codec
# (the codec reads fields sequentially) but useful for arithmetic checks in
# ENVELOPE.md and in this file's own vector-length assertions below.
FIXED_HEADER_LEN_NO_REPLY = 1 + 1 + 1 + MESSAGE_ID_LEN + 1

MAX_ENVELOPE_LEN = (
    1  # version
    + 1  # kind
    + 1  # flags
    + MESSAGE_ID_LEN
    + REPLY_TO_ID_LEN
    + 1  # senderIdLen
    + MAX_SENDER_ID_LEN
    + BODY_LEN_FIELD_WIDTH
    + MAX_BODY_LEN
)
assert MAX_ENVELOPE_LEN == 2118, "max envelope size arithmetic drifted from ENVELOPE.md"


# ---------------------------------------------------------------------------
# Error taxonomy
#
# Six error classes, identical *names* (via `error_name`) across all three
# implementations: Swift `enum ChatEnvelopeError: Error, Equatable`, Kotlin
# sealed class, and these Python exception classes (oracle only -- Python's
# own exception hierarchy is not meant to be mirrored, only `error_name`).
# ---------------------------------------------------------------------------


class ChatEnvelopeError(Exception):
    """Base class for every envelope v1 decode/encode error.

    `error_name` is the cross-language wire name written into the "error"
    field of golden vectors and matched (by name only) against the Swift
    and Kotlin error types.
    """

    error_name: str = "unspecified"


class UnknownVersionError(ChatEnvelopeError):
    error_name = "unknownVersion"


class UnknownKindError(ChatEnvelopeError):
    error_name = "unknownKind"


class MalformedError(ChatEnvelopeError):
    error_name = "malformed"


class TruncatedError(ChatEnvelopeError):
    error_name = "truncated"


class OversizeBodyError(ChatEnvelopeError):
    error_name = "oversizeBody"


class InvalidUtf8Error(ChatEnvelopeError):
    error_name = "invalidUtf8"


ERROR_CLASSES_BY_NAME = {
    cls.error_name: cls
    for cls in (
        UnknownVersionError,
        UnknownKindError,
        MalformedError,
        TruncatedError,
        OversizeBodyError,
        InvalidUtf8Error,
    )
}


# ---------------------------------------------------------------------------
# Decoded value
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class DecodedEnvelope:
    version: int
    kind: str  # "text" is the only value v1 defines
    message_id: bytes
    reply_to_id: Optional[bytes]
    sender_id: bytes
    body: str  # already UTF-8-validated

    def to_json_dict(self) -> dict:
        """Field order matches the "decoded" object shape in ENVELOPE.md's
        golden-vector JSON schema exactly."""
        return {
            "version": self.version,
            "kind": self.kind,
            "messageIdHex": self.message_id.hex(),
            "replyToIdHex": self.reply_to_id.hex() if self.reply_to_id is not None else None,
            "senderIdHex": self.sender_id.hex(),
            "body": self.body,
        }


# ---------------------------------------------------------------------------
# Strict sequential decoder
#
# Field order and the two "range check before availability check" rules
# (senderIdLen, bodyLen) are spelled out in ENVELOPE.md's "Strict sequential
# parse order" section; this function is the executable version of that
# prose and must not drift from it.
# ---------------------------------------------------------------------------


def decode_envelope(data: bytes) -> DecodedEnvelope:
    pos = 0

    def take(n: int) -> bytes:
        nonlocal pos
        if len(data) - pos < n:
            raise TruncatedError(
                f"need {n} byte(s) at offset {pos}, only {len(data) - pos} remain"
            )
        chunk = data[pos : pos + n]
        pos += n
        return chunk

    version = take(1)[0]
    if version != VERSION_1:
        raise UnknownVersionError(f"unsupported envelope version {version:#04x}")

    kind_byte = take(1)[0]
    if kind_byte != KIND_TEXT:
        raise UnknownKindError(f"unsupported message kind {kind_byte:#04x}")

    flags = take(1)[0]
    if flags & ~FLAG_REPLY_TO_PRESENT:
        raise MalformedError(f"flags byte {flags:#04x} sets undefined bit(s)")
    reply_to_present = bool(flags & FLAG_REPLY_TO_PRESENT)

    message_id = take(MESSAGE_ID_LEN)

    reply_to_id: Optional[bytes] = None
    if reply_to_present:
        reply_to_id = take(REPLY_TO_ID_LEN)

    sender_id_len = take(1)[0]
    if sender_id_len < MIN_SENDER_ID_LEN or sender_id_len > MAX_SENDER_ID_LEN:
        # Range is checked BEFORE attempting to read that many bytes, so a
        # crafted out-of-range length is `malformed` even when the buffer
        # could never have held that many bytes (see "senderIdLen=33"
        # golden vector, which supplies zero trailing bytes).
        raise MalformedError(
            f"senderIdLen {sender_id_len} outside {MIN_SENDER_ID_LEN}..{MAX_SENDER_ID_LEN}"
        )
    sender_id = take(sender_id_len)

    body_len = int.from_bytes(take(BODY_LEN_FIELD_WIDTH), "big")
    if body_len > MAX_BODY_LEN:
        # Same rule as senderIdLen: the range check happens immediately
        # after parsing bodyLen, before attempting to read that many body
        # bytes. This is what makes "bodyLen=2049" `oversizeBody` rather
        # than `truncated` even when zero body bytes are supplied.
        raise OversizeBodyError(f"bodyLen {body_len} exceeds {MAX_BODY_LEN}")
    body_bytes = take(body_len)

    try:
        body = body_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise InvalidUtf8Error(str(exc)) from exc

    if pos != len(data):
        raise MalformedError(f"{len(data) - pos} trailing byte(s) after a complete envelope")

    return DecodedEnvelope(
        version=version,
        kind="text",
        message_id=message_id,
        reply_to_id=reply_to_id,
        sender_id=sender_id,
        body=body,
    )


# ---------------------------------------------------------------------------
# Canonical (strict, validating) encoder
#
# Used both to build every "decode" golden vector's bytes and, in
# --self-test, to re-encode each decoded value and check byte-identity
# (the "canonical encoding" rule in ENVELOPE.md). This function refuses to
# emit an out-of-range envelope; it is not used to build "error" vectors
# (see `pack_raw` below for that).
# ---------------------------------------------------------------------------


def encode_strict(
    *,
    version: int,
    kind: int,
    message_id: bytes,
    reply_to_id: Optional[bytes],
    sender_id: bytes,
    body: bytes,
) -> bytes:
    if version != VERSION_1:
        raise UnknownVersionError(f"encode: unsupported version {version}")
    if kind != KIND_TEXT:
        raise UnknownKindError(f"encode: unsupported kind {kind}")
    if len(message_id) != MESSAGE_ID_LEN:
        raise MalformedError(f"encode: messageId must be exactly {MESSAGE_ID_LEN} bytes")
    if reply_to_id is not None and len(reply_to_id) != REPLY_TO_ID_LEN:
        raise MalformedError(f"encode: replyToId must be exactly {REPLY_TO_ID_LEN} bytes")
    if not (MIN_SENDER_ID_LEN <= len(sender_id) <= MAX_SENDER_ID_LEN):
        raise MalformedError(
            f"encode: senderId length {len(sender_id)} outside "
            f"{MIN_SENDER_ID_LEN}..{MAX_SENDER_ID_LEN}"
        )
    if len(body) > MAX_BODY_LEN:
        raise OversizeBodyError(f"encode: body length {len(body)} exceeds {MAX_BODY_LEN}")
    try:
        body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise InvalidUtf8Error(str(exc)) from exc

    flags = FLAG_REPLY_TO_PRESENT if reply_to_id is not None else 0
    out = bytearray()
    out.append(version)
    out.append(kind)
    out.append(flags)
    out += message_id
    if reply_to_id is not None:
        out += reply_to_id
    out.append(len(sender_id))
    out += sender_id
    out += len(body).to_bytes(BODY_LEN_FIELD_WIDTH, "big")
    out += body
    return bytes(out)


def encode_decoded(decoded: DecodedEnvelope) -> bytes:
    """Re-encode a DecodedEnvelope for the self-test's canonical-encoding
    (round-trip) check."""
    return encode_strict(
        version=decoded.version,
        kind=KIND_TEXT if decoded.kind == "text" else -1,
        message_id=decoded.message_id,
        reply_to_id=decoded.reply_to_id,
        sender_id=decoded.sender_id,
        body=decoded.body.encode("utf-8"),
    )


# ---------------------------------------------------------------------------
# Raw (non-validating) byte packer -- fixture construction only
#
# The "error" golden vectors must contain bytes that VIOLATE the spec on
# purpose (an out-of-range senderIdLen, a truncated buffer, an unknown
# version, ...). encode_strict() would refuse to build them. pack_raw
# concatenates already-formed byte pieces with no validation at all; every
# call site below documents in a comment exactly which field is being made
# invalid and why.
# ---------------------------------------------------------------------------


def pack_raw(*pieces: bytes) -> bytes:
    return b"".join(pieces)


def u16be(n: int) -> bytes:
    """Big-endian u16, allowing out-of-range/oversize values on purpose
    (Python's `int.to_bytes` would refuse anything >= 65536, which every
    required bodyLen test value is well under, so plain big-endian packing
    via struct semantics is enough -- no wraparound tricks needed)."""
    return n.to_bytes(2, "big")


# ---------------------------------------------------------------------------
# Fixed hand-chosen ID constants used across multiple vectors
#
# No randomness anywhere in this file: every ID below is a literal,
# hand-chosen byte sequence, deliberately patterned (ascending / descending
# hex runs, or a recognizable constant like 0xDEADBEEF) so a reviewer can
# recompute the hex-to-bytes mapping by eye without a decoder.
# ---------------------------------------------------------------------------

MESSAGE_ID_A = bytes.fromhex("0102030405060708090a0b0c0d0e0f10")
"""16 bytes, ascending 0x01..0x10. Reused as "the" message ID across
several unrelated vectors -- vectors are independent byte strings, so
reusing an ID across them is not a collision in any meaningful sense."""
assert len(MESSAGE_ID_A) == MESSAGE_ID_LEN

MESSAGE_ID_B = bytes.fromhex("2021222324252627202122232425262a")
"""A second, visually distinct 16-byte ID (mostly 0x20-0x27 repeating, with
a final 0x2a) used where a vector wants to demonstrate a message ID that is
NOT MESSAGE_ID_A (e.g. duplicateIncoming-style narrative, or just to avoid
implying that every vector must reuse the exact same ID)."""
assert len(MESSAGE_ID_B) == MESSAGE_ID_LEN

REPLY_TO_ID_A = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
"""16 bytes, ascending 0xf0..0xff."""
assert len(REPLY_TO_ID_A) == REPLY_TO_ID_LEN

SENDER_ID_4 = bytes.fromhex("deadbeef")
"""4-byte sender ID: the classic 0xDEADBEEF constant. Used wherever a
vector needs *a* valid senderId and the specific value is not the point of
the test."""
assert MIN_SENDER_ID_LEN <= len(SENDER_ID_4) <= MAX_SENDER_ID_LEN

SENDER_ID_32 = bytes(range(32))
"""32-byte (maximum-length) sender ID: bytes 0x00..0x1f in order. Used by
the vector that also exercises the maximum body length, together
demonstrating the full 2118-byte MAX_ENVELOPE_LEN arithmetic."""
assert len(SENDER_ID_32) == MAX_SENDER_ID_LEN


def peer_display_name(peer_id: bytes) -> str:
    """"Peer-XXXX" from the first two id bytes, uppercase hex -- pinned by
    the ChatPeer contract in Apps/Chat/CONTRACT.md. Not used by the codec
    itself; included here only because a couple of vector comments cite it
    for context."""
    return f"Peer-{peer_id[:2].hex().upper()}"


# ---------------------------------------------------------------------------
# Golden vector construction
#
# Each _vec_* function returns a dict matching the schema in ENVELOPE.md:
# {"name", "expect", "bytes_hex", "decoded"|"error", "comment"}.
# Decode vectors call encode_strict() so the fixture's bytes are always
# generated the same way real callers would generate them. Error vectors
# hand-assemble bytes with pack_raw() and document exactly what is wrong.
# ---------------------------------------------------------------------------


def _decode_vec(name: str, comment: str, *, message_id, reply_to_id, sender_id, body: str) -> dict:
    body_bytes = body.encode("utf-8")
    encoded = encode_strict(
        version=VERSION_1,
        kind=KIND_TEXT,
        message_id=message_id,
        reply_to_id=reply_to_id,
        sender_id=sender_id,
        body=body_bytes,
    )
    decoded = DecodedEnvelope(
        version=VERSION_1,
        kind="text",
        message_id=message_id,
        reply_to_id=reply_to_id,
        sender_id=sender_id,
        body=body,
    )
    return {
        "name": name,
        "expect": "decode",
        "bytes_hex": encoded.hex(),
        "decoded": decoded.to_json_dict(),
        "comment": comment,
    }


def _error_vec(name: str, comment: str, *, error: str, bytes_value: bytes) -> dict:
    assert error in ERROR_CLASSES_BY_NAME, f"unknown error name {error!r} in vector {name!r}"
    return {
        "name": name,
        "expect": "error",
        "bytes_hex": bytes_value.hex(),
        "error": error,
        "comment": comment,
    }


def build_vectors() -> list:
    vectors = []

    # -- Required coverage item: "ascii minimal" ---------------------------
    vectors.append(
        _decode_vec(
            "text_ascii_min",
            "Smallest realistic envelope: no replyTo, 4-byte senderId, "
            "2-byte ASCII body 'hi'. This is the vector hand-verified "
            "byte-by-byte in the C3-28 spec-stage report.",
            message_id=MESSAGE_ID_A,
            reply_to_id=None,
            sender_id=SENDER_ID_4,
            body="hi",
        )
    )

    # -- Required coverage item: "empty body" -------------------------------
    vectors.append(
        _decode_vec(
            "empty_body",
            "bodyLen=0 is explicitly valid per ENVELOPE.md ('zero length is "
            "valid'); body decodes to the empty string.",
            message_id=MESSAGE_ID_A,
            reply_to_id=None,
            sender_id=SENDER_ID_4,
            body="",
        )
    )

    # -- Required coverage item: "body exactly 2048 bytes" ------------------
    vectors.append(
        _decode_vec(
            "body_max_2048",
            "bodyLen at the exact maximum (2048 ASCII 'A' bytes). Also uses "
            "the maximum-length (32-byte) senderId and a present replyTo, so "
            "the encoded length is exactly MAX_ENVELOPE_LEN = 2118 bytes -- "
            "the concrete instance of the 3+16+16+1+32+2+2048 arithmetic in "
            "ENVELOPE.md.",
            message_id=MESSAGE_ID_A,
            reply_to_id=REPLY_TO_ID_A,
            sender_id=SENDER_ID_32,
            body="A" * MAX_BODY_LEN,
        )
    )

    # -- Required coverage item: "crafted bodyLen=2049 -> oversizeBody" -----
    _hdr_no_reply = bytes([VERSION_1, KIND_TEXT, 0x00])
    vectors.append(
        _error_vec(
            "reject_oversize_body_len_2049",
            "bodyLen field set to 2049 (one over MAX_BODY_LEN) with ZERO "
            "body bytes supplied afterward. Proves the bodyLen range check "
            "fires before any attempt to read body bytes: this must be "
            "oversizeBody, not truncated, even though 2049 body bytes were "
            "never actually present.",
            error="oversizeBody",
            bytes_value=pack_raw(
                _hdr_no_reply,
                MESSAGE_ID_A,
                bytes([len(SENDER_ID_4)]),
                SENDER_ID_4,
                u16be(MAX_BODY_LEN + 1),
                # (no body bytes follow)
            ),
        )
    )

    # -- Required coverage item: Unicode emoji + CJK + combining marks ------
    unicode_body = (
        "Café"  # "café" as base 'e' + COMBINING ACUTE ACCENT U+0301
        " 日本語"  # CJK "日本語" ("Japanese language")
        " \U0001f600"  # emoji GRINNING FACE U+1F600
    )
    vectors.append(
        _decode_vec(
            "unicode_mixed_emoji_cjk_combining",
            "Body mixes a combining-mark grapheme (e + U+0301), CJK "
            "characters, and an astral-plane emoji, to exercise multi-byte "
            "and 4-byte UTF-8 sequences together.",
            message_id=MESSAGE_ID_B,
            reply_to_id=None,
            sender_id=SENDER_ID_4,
            body=unicode_body,
        )
    )

    # -- Required coverage item: invalid UTF-8 -------------------------------
    # The brief's required-coverage line groups three distinct invalid-UTF-8
    # shapes in one parenthetical ("0xFF byte; truncated multibyte; overlong
    # encoding"). DECISION (not pinned by the brief): treated as three
    # separate vectors, one per shape, rather than one vector picking a
    # single example -- flagged in the C3-28 report as a judgment call.
    _hdr_no_reply_small = bytes([VERSION_1, KIND_TEXT, 0x00])

    def _invalid_body_vec(name: str, comment: str, body_bytes: bytes) -> dict:
        return _error_vec(
            name,
            comment,
            error="invalidUtf8",
            bytes_value=pack_raw(
                _hdr_no_reply_small,
                MESSAGE_ID_A,
                bytes([len(SENDER_ID_4)]),
                SENDER_ID_4,
                u16be(len(body_bytes)),
                body_bytes,
            ),
        )

    vectors.append(
        _invalid_body_vec(
            "reject_invalid_utf8_0xff_byte",
            "Body is a single 0xFF byte. 0xFF is never a valid UTF-8 lead "
            "byte under RFC 3629.",
            bytes([0xFF]),
        )
    )
    vectors.append(
        _invalid_body_vec(
            "reject_invalid_utf8_truncated_multibyte",
            "Body is 0xE4 0xB8 -- the first two bytes of the valid 3-byte "
            "encoding of U+4E00 (0xE4 0xB8 0x80, '一'), with the trailing "
            "continuation byte dropped.",
            bytes([0xE4, 0xB8]),
        )
    )
    vectors.append(
        _invalid_body_vec(
            "reject_invalid_utf8_overlong_encoding",
            "Body is 0xC0 0xAF: the canonical overlong 2-byte encoding of "
            "U+002F ('/'), which a strict UTF-8 decoder must reject even "
            "though the bit pattern superficially looks well-formed.",
            bytes([0xC0, 0xAF]),
        )
    )

    # -- Required coverage item: "unknown version 0x02" ----------------------
    vectors.append(
        _error_vec(
            "reject_unknown_version",
            "Single byte 0x02. Version is the first field parsed; an "
            "unrecognized value fails immediately (ENVELOPE.md: 'stop'), so "
            "no further bytes are needed to prove unknownVersion.",
            error="unknownVersion",
            bytes_value=bytes([0x02]),
        )
    )

    # -- Required coverage item: "unknown kind 0x7F" -------------------------
    vectors.append(
        _error_vec(
            "reject_unknown_kind",
            "version=0x01 (valid), kind=0x7F (undefined). Kind is checked "
            "immediately after being read; parsing stops there.",
            error="unknownKind",
            bytes_value=bytes([VERSION_1, 0x7F]),
        )
    )

    # -- Required coverage item: "flags bit1 set -> malformed" ---------------
    vectors.append(
        _error_vec(
            "reject_flags_unknown_bit",
            "flags=0x02: bit0 (replyTo-present) is clear, but bit1 is set. "
            "Only bit0 is defined in v1; any other set bit is malformed.",
            error="malformed",
            bytes_value=bytes([VERSION_1, KIND_TEXT, 0x02]),
        )
    )

    # -- Required coverage item: "replyTo present valid" ----------------------
    vectors.append(
        _decode_vec(
            "replyto_present_valid",
            "flags bit0 set; a 16-byte replyToId follows messageId. This is "
            "the second vector hand-verified byte-by-byte in the C3-28 "
            "spec-stage report.",
            message_id=MESSAGE_ID_A,
            reply_to_id=REPLY_TO_ID_A,
            sender_id=SENDER_ID_4,
            body="ack",
        )
    )

    # -- Required coverage item: "senderIdLen=0 -> malformed" -----------------
    vectors.append(
        _error_vec(
            "reject_sender_id_len_zero",
            "senderIdLen byte is 0x00. Valid range is 1..32; 0 is out of "
            "range on the low side. No senderId bytes follow (range is "
            "checked before the length is used to read further bytes).",
            error="malformed",
            bytes_value=pack_raw(
                _hdr_no_reply,
                MESSAGE_ID_A,
                bytes([0x00]),
            ),
        )
    )

    # -- Required coverage item: "senderIdLen=33 -> malformed" ----------------
    vectors.append(
        _error_vec(
            "reject_sender_id_len_33",
            "senderIdLen byte is 0x21 (33 decimal), one over "
            "MAX_SENDER_ID_LEN=32. No senderId bytes follow: the range "
            "check fires before 33 bytes are ever requested.",
            error="malformed",
            bytes_value=pack_raw(
                _hdr_no_reply,
                MESSAGE_ID_A,
                bytes([33]),
            ),
        )
    )

    # -- Required coverage item: "truncated after version byte" ---------------
    vectors.append(
        _error_vec(
            "reject_truncated_after_version",
            "Buffer is exactly one byte: 0x01 (a VALID version). The next "
            "field (kind) cannot be read, so this isolates truncated from "
            "unknownVersion -- if the version byte itself were invalid this "
            "vector would be ambiguous between the two errors.",
            error="truncated",
            bytes_value=bytes([VERSION_1]),
        )
    )

    # -- Required coverage item: "truncated mid-messageId" --------------------
    vectors.append(
        _error_vec(
            "reject_truncated_mid_message_id",
            "version, kind, flags=0x00 present, then only 5 of the required "
            "16 messageId bytes.",
            error="truncated",
            bytes_value=pack_raw(
                _hdr_no_reply,
                MESSAGE_ID_A[:5],
            ),
        )
    )

    # -- Required coverage item: "truncated mid-senderId" ----------------------
    vectors.append(
        _error_vec(
            "reject_truncated_mid_sender_id",
            "senderIdLen declares 4 (in range), but only 2 sender-id bytes "
            "are actually supplied.",
            error="truncated",
            bytes_value=pack_raw(
                _hdr_no_reply,
                MESSAGE_ID_A,
                bytes([len(SENDER_ID_4)]),
                SENDER_ID_4[:2],
            ),
        )
    )

    # -- Required coverage item: bodyLen > remaining bytes -> truncated -------
    vectors.append(
        _error_vec(
            "reject_body_len_exceeds_remaining",
            "bodyLen declares 5 (well within the 0..2048 range, so this is "
            "NOT oversizeBody), but only 2 body bytes are actually "
            "supplied -- distinguishes this from the bodyLen=2049 vector "
            "above, which fails on the range check alone.",
            error="truncated",
            bytes_value=pack_raw(
                _hdr_no_reply,
                MESSAGE_ID_A,
                bytes([len(SENDER_ID_4)]),
                SENDER_ID_4,
                u16be(5),
                bytes([0x61, 0x62]),  # "ab" -- only 2 of the declared 5 bytes
            ),
        )
    )

    # -- Required coverage item: "single trailing byte -> malformed" ----------
    _valid_minimal = encode_strict(
        version=VERSION_1,
        kind=KIND_TEXT,
        message_id=MESSAGE_ID_A,
        reply_to_id=None,
        sender_id=SENDER_ID_4,
        body=b"hi",
    )
    vectors.append(
        _error_vec(
            "reject_single_trailing_byte",
            "A complete, otherwise-valid 'text_ascii_min'-shaped envelope "
            "with exactly one extra 0x00 byte appended after the body.",
            error="malformed",
            bytes_value=_valid_minimal + bytes([0x00]),
        )
    )

    return vectors


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def run_self_test(vectors: list) -> bool:
    ok = True
    decode_checked = 0
    reencode_checked = 0
    error_checked = 0

    for vec in vectors:
        name = vec["name"]
        raw = bytes.fromhex(vec["bytes_hex"])

        if vec["expect"] == "decode":
            try:
                decoded = decode_envelope(raw)
            except ChatEnvelopeError as exc:
                print(f"FAIL [{name}] expected decode, got {exc.error_name}: {exc}")
                ok = False
                continue

            expected = vec["decoded"]
            actual = decoded.to_json_dict()
            if actual != expected:
                print(f"FAIL [{name}] decoded field mismatch")
                print(f"       expected: {expected}")
                print(f"       actual:   {actual}")
                ok = False
                continue
            decode_checked += 1

            # (b) canonical encoding: re-encode must reproduce input bytes.
            reencoded = encode_decoded(decoded)
            if reencoded != raw:
                print(f"FAIL [{name}] re-encode is not byte-identical (canonical encoding)")
                print(f"       expected: {raw.hex()}")
                print(f"       actual:   {reencoded.hex()}")
                ok = False
                continue
            reencode_checked += 1

        elif vec["expect"] == "error":
            expected_error = vec["error"]
            try:
                decode_envelope(raw)
            except ChatEnvelopeError as exc:
                if exc.error_name != expected_error:
                    print(
                        f"FAIL [{name}] expected error {expected_error!r}, "
                        f"got {exc.error_name!r}: {exc}"
                    )
                    ok = False
                    continue
                error_checked += 1
            else:
                print(f"FAIL [{name}] expected error {expected_error!r}, decode succeeded")
                ok = False
                continue
        else:
            print(f"FAIL [{name}] unknown 'expect' value {vec['expect']!r}")
            ok = False

    print(
        f"self-test: {len(vectors)} vector(s); "
        f"{decode_checked} decode-checked, {reencode_checked} re-encode-checked, "
        f"{error_checked} error-checked"
    )
    return ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SCHEMA_NAME = "cyrinx-chat-envelope-golden-v1"
GENERATOR_PATH = "fixtures/tools/generate_golden.py"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "chat-envelope-golden.json"


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run the self-test only; do not write the fixture file.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Fixture output path (default: {DEFAULT_OUT})",
    )
    args = parser.parse_args(argv)

    vectors = build_vectors()
    names = [v["name"] for v in vectors]
    if len(names) != len(set(names)):
        print("FAIL: duplicate vector name(s) detected", file=sys.stderr)
        return 1

    passed = run_self_test(vectors)
    if not passed:
        print("self-test FAILED", file=sys.stderr)
        return 1
    print("self-test PASSED")

    if args.self_test:
        return 0

    document = {
        "schema": SCHEMA_NAME,
        "generator": GENERATOR_PATH,
        "vectors": vectors,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, ensure_ascii=False, sort_keys=False)
        f.write("\n")
    print(f"wrote {len(vectors)} vector(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
