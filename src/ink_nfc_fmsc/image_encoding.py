from dataclasses import dataclass


@dataclass(frozen=True)
class RgbaFrame:
    width: int
    height: int
    data: bytes

    def __post_init__(self):
        if self.width < 1 or self.height < 1 or len(self.data) != self.width * self.height * 4:
            raise ValueError("RGBA dimensions/byte count mismatch")
