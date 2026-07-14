from __future__ import annotations


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def write_single_coil_frame(device_address: int, coil: int, enabled: bool) -> bytes:
    if not 0 <= device_address <= 0xFF:
        raise ValueError(f"device_address out of range: {device_address}")
    if not 0 <= coil <= 0xFFFF:
        raise ValueError(f"coil out of range: {coil}")
    value = 0xFF00 if enabled else 0x0000
    payload = bytes([
        device_address,
        0x05,
        (coil >> 8) & 0xFF,
        coil & 0xFF,
        (value >> 8) & 0xFF,
        value & 0xFF,
    ])
    crc = crc16_modbus(payload)
    return payload + bytes([crc & 0xFF, (crc >> 8) & 0xFF])
