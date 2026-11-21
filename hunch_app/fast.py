"""The faster way of running the same 4-bit weights (copied from the server's `hunch_server/fast.py`).

The published 4-bit graph leaves ONNX Runtime's `accuracy_level` unset on its `MatMulNBits` nodes. On an Arm
processor that means unpacking the weights to full precision on every call. Level 4 keeps the same weights and
multiplies in 8-bit whole numbers: on a Raspberry Pi 5 it took the typed build from 72 ms to 38 ms a sentence,
and 5 of 2,974 test answers changed (18 Nov 2026).

The copy is a second, small graph file beside the original (`model_q4_al4.onnx`) that shares the original's
data file, so the weights are not duplicated. It is made here by editing the graph file's bytes directly
(the ONNX file format is a protocol buffer), so the app needs no ONNX library: only the top-level graph's
`MatMulNBits` nodes change, and every other byte is kept as it was.
"""

from __future__ import annotations

import os
from pathlib import Path

ORIGINAL = "model_q4.onnx"
FAST = "model_q4_al4.onnx"
LEVEL = 4

# Field numbers from onnx.proto: ModelProto.graph, GraphProto.node, NodeProto.op_type and .attribute,
# AttributeProto.name, .i and .type (AttributeType INT is 2).
MODEL_GRAPH, GRAPH_NODE, NODE_OP_TYPE, NODE_ATTRIBUTE = 7, 1, 4, 5
ATTR_NAME, ATTR_INT, ATTR_TYPE, TYPE_INT = 1, 3, 20, 2
VARINT, FIXED64, LENGTH, FIXED32 = 0, 1, 2, 5


def _varint(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if at >= len(data):
            raise ValueError("the graph file ends inside a number")
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, at
        shift += 7
        if shift > 63:
            raise ValueError("the graph file holds a number that is too long")


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def fields(data: bytes) -> list[tuple[int, int, bytes, bytes]]:
    """A message's fields in order: (field number, wire type, the field's whole bytes, its payload)."""
    out, at = [], 0
    while at < len(data):
        start = at
        key, at = _varint(data, at)
        number, wire = key >> 3, key & 7
        if wire == VARINT:
            begin = at
            _, at = _varint(data, at)
            payload = data[begin:at]
        elif wire == FIXED64:
            at += 8
            payload = data[at - 8:at]
        elif wire == FIXED32:
            at += 4
            payload = data[at - 4:at]
        elif wire == LENGTH:
            size, at = _varint(data, at)
            at += size
            payload = data[at - size:at]
        else:
            raise ValueError(f"the graph file uses wire type {wire}, which ONNX files do not")
        if at > len(data):
            raise ValueError("the graph file ends inside a field")
        out.append((number, wire, data[start:at], payload))
    return out


def _length_field(number: int, payload: bytes) -> bytes:
    return _encode_varint(number << 3 | LENGTH) + _encode_varint(len(payload)) + payload


def _level_attribute(level: int) -> bytes:
    body = (_length_field(ATTR_NAME, b"accuracy_level") + _encode_varint(ATTR_INT << 3 | VARINT)
            + _encode_varint(level) + _encode_varint(ATTR_TYPE << 3 | VARINT) + _encode_varint(TYPE_INT))
    return _length_field(NODE_ATTRIBUTE, body)


def _attribute_name(payload: bytes) -> bytes | None:
    for number, wire, _, value in fields(payload):
        if number == ATTR_NAME and wire == LENGTH:
            return value
    return None


def _node(payload: bytes, level: int) -> tuple[bytes, bool]:
    parts = fields(payload)
    if not any(n == NODE_OP_TYPE and w == LENGTH and v == b"MatMulNBits" for n, w, _, v in parts):
        return payload, False
    kept = [whole for n, w, whole, v in parts
            if not (n == NODE_ATTRIBUTE and w == LENGTH and _attribute_name(v) == b"accuracy_level")]
    return b"".join(kept) + _level_attribute(level), True


def _graph(payload: bytes, level: int) -> tuple[bytes, int]:
    out, changed = [], 0
    for number, wire, whole, value in fields(payload):
        if number == GRAPH_NODE and wire == LENGTH:
            node, done = _node(value, level)
            changed += done
            out.append(_length_field(GRAPH_NODE, node) if done else whole)
        else:
            out.append(whole)
    return b"".join(out), changed


def with_level(model: bytes, level: int = LEVEL) -> tuple[bytes, int]:
    """The graph file's bytes with every top-level `MatMulNBits` node set to `accuracy_level` = level, and how
    many nodes that is."""
    out, changed = [], 0
    for number, wire, whole, value in fields(model):
        if number == MODEL_GRAPH and wire == LENGTH:
            graph, count = _graph(value, level)
            changed += count
            out.append(_length_field(MODEL_GRAPH, graph))
        else:
            out.append(whole)
    return b"".join(out), changed


def node_levels(model: bytes) -> list[tuple[bytes, int | None]]:
    """For a check: each top-level node's operator and its `accuracy_level` (None when unset)."""
    out = []
    for number, wire, _, graph in fields(model):
        if number != MODEL_GRAPH or wire != LENGTH:
            continue
        for n, w, _, node in fields(graph):
            if n != GRAPH_NODE or w != LENGTH:
                continue
            parts = fields(node)
            op = next((v for k, t, _, v in parts if k == NODE_OP_TYPE and t == LENGTH), b"")
            level = None
            for k, t, _, attribute in parts:
                if k == NODE_ATTRIBUTE and t == LENGTH and _attribute_name(attribute) == b"accuracy_level":
                    level = next((_varint(value, 0)[0] for a, b, _, value in fields(attribute)
                                  if a == ATTR_INT and b == VARINT), None)
            out.append((op, level))
    return out


def fast_copy(folder: Path) -> Path | None:
    """The fast copy of `folder`'s 4-bit graph, made if it is missing or not exactly what the original gives.

    Returns its path, or None when it cannot be written (a read-only folder); the caller then runs the
    original. The copy is written whole to a temporary name and renamed in one step, so a copy half written is
    never loaded."""
    original = folder / ORIGINAL
    wanted, changed = with_level(original.read_bytes())
    if not changed:
        raise ValueError(f"{original} has no 4-bit layers to speed up")
    out = folder / FAST
    try:
        if out.read_bytes() == wanted:
            return out
    except OSError:
        pass
    part = folder / (FAST + ".part")
    try:
        part.write_bytes(wanted)
        os.replace(part, out)
    except OSError:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return out
