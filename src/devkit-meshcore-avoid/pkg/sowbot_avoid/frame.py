"""Frame codec for the legacy scheme. See docs/packet-format.md. No dependencies."""
from dataclasses import dataclass

DATA_TYPE = 0xFFFF
HELLO, ENTER, BEACON, ARRIVED, FINISHED = range(5)
KIND_NAMES = ["HELLO", "ENTER", "BEACON", "ARRIVED", "FINISHED"]
NODES_FOR_KIND = {HELLO: 0, ENTER: 2, BEACON: 2, ARRIVED: 1, FINISHED: 0}


@dataclass(frozen=True)
class Frame:
    rid: int
    seq: int
    kind: int
    resend: int = 0
    nclaims: int = 0
    retreat: bool = False
    nodes: tuple = ()

    def encode(self):
        flags = self.kind | (self.resend << 3) | (self.nclaims << 5) | (int(self.retreat) << 7)
        return bytes([self.rid, self.seq & 255, flags]) + b"".join(n.to_bytes(2, "little") for n in self.nodes)

    @staticmethod
    def decode(b):
        if len(b) < 3 or b[0] == 0:
            return None
        kind, resend, nclaims, retreat = b[2] & 7, (b[2] >> 3) & 3, (b[2] >> 5) & 3, bool(b[2] >> 7)
        if kind > FINISHED or nclaims > 2 or (kind in (HELLO, FINISHED) and nclaims):
            return None
        need = NODES_FOR_KIND[kind] + nclaims
        if len(b) != 3 + 2 * need:
            return None
        nodes = tuple(int.from_bytes(b[3 + 2 * i:5 + 2 * i], "little") for i in range(need))
        return Frame(b[0], b[1], kind, resend, nclaims, retreat, nodes)

    def hold(self):
        """Node or edge the sender occupies, as an (a, b) pair; (n, n) for a node. None if no hold."""
        if self.kind in (ENTER, BEACON):
            return (self.nodes[0], self.nodes[1])
        if self.kind == ARRIVED:
            return (self.nodes[0], self.nodes[0])
        return None

    def claims(self):
        """The sender's next edges (chained after the hold)."""
        if self.kind not in (ENTER, BEACON, ARRIVED):
            return ()
        s = 1 if self.kind in (ENTER, BEACON) else 0
        return tuple((self.nodes[s + i], self.nodes[s + i + 1]) for i in range(self.nclaims))

