"""Frame codec. See docs/packet-format.md. No dependencies."""
from dataclasses import dataclass

DATA_TYPE = 0xFFFF
MAX_NODES = 3       # the node we stand on, then two intended nodes


@dataclass(frozen=True)
class Frame:
    """One announcement: the sender stands on nodes[0] and intends to drive through nodes[1:]."""
    rid: int
    seq: int
    nodes: tuple = ()

    def encode(self):
        """Encode as robot id, sequence, then little-endian 16-bit nodes.

        The sequence is reduced modulo 256. A robot ID outside the byte range raises
        ValueError; out-of-range nodes raise OverflowError.
        """
        return bytes([self.rid, self.seq & 255]) + b"".join(n.to_bytes(2, "little") for n in self.nodes)

    @staticmethod
    def decode(b):
        """Decode a frame, returning None for robot ID zero or a length that is not 1 to 3 nodes."""
        if len(b) < 4 or len(b) % 2 or len(b) > 2 + 2 * MAX_NODES or b[0] == 0:
            return None
        nodes = tuple(int.from_bytes(b[i:i + 2], "little") for i in range(2, len(b), 2))
        return Frame(b[0], b[1], nodes)

    def hold(self):
        """The node the sender stands on, as an (n, n) pair."""
        return (self.nodes[0], self.nodes[0])

    def claims(self):
        """The sender's next edges, chained after the node it stands on."""
        return tuple(zip(self.nodes, self.nodes[1:]))
