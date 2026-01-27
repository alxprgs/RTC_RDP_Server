def clamp(v: int, lo: int, hi: int) -> int:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def deadzone(v: int, dz: int) -> int:
    return 0 if abs(v) < dz else v


def mix_tank(x: int, y: int) -> tuple[int, int]:
    a = y + x
    b = y - x
    return clamp(a, -255, 255), clamp(b, -255, 255)
