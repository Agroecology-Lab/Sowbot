"""RTCM3 frames are split into GPS_RTCM_DATA fragments as ArduPilot reassembles them."""

import unittest

from devkit_mavlink_bridge.modules.rtcm_handler import rtcm_to_gps_rtcm_data


def _frame(size):
    """Return a frame of the given size whose bytes are distinguishable."""
    return bytes(i % 251 for i in range(size))


def _reassemble(messages):
    """Rebuild a frame the way ArduPilot does and return it with the fragment count it saw."""
    joined = b''
    for message in messages:
        joined += bytes(message['data'][:message['len']])
    return joined


class RtcmHandlerTest(unittest.TestCase):
    """Fragment layout, padding, sequence numbers and limits."""

    def test_a_short_frame_is_one_unfragmented_message(self):
        """Verify a frame up to 180 bytes is sent whole, with the fragment bit clear."""
        for size in (1, 100, 180):
            with self.subTest(size=size):
                (message,) = rtcm_to_gps_rtcm_data(_frame(size), sequence=5)
                self.assertEqual(message['flags'], 5 << 3)
                self.assertEqual(message['len'], size)
                self.assertEqual(len(message['data']), 180)
                self.assertEqual(bytes(message['data'][:size]), _frame(size))
                self.assertEqual(message['data'][size:], [0] * (180 - size))

    def test_a_long_frame_is_split_into_numbered_fragments(self):
        """Verify fragments carry the fragment bit, their number and one shared sequence."""
        messages = rtcm_to_gps_rtcm_data(_frame(400), sequence=9)
        self.assertEqual([m['len'] for m in messages], [180, 180, 40])
        self.assertEqual([m['flags'] for m in messages],
                         [(9 << 3) | (n << 1) | 1 for n in range(3)])
        self.assertEqual(_reassemble(messages), _frame(400))

    def test_an_exact_multiple_of_180_ends_with_an_empty_fragment(self):
        """Verify the receiver is told where the frame ends when no short fragment does so."""
        for size, lengths in ((360, [180, 180, 0]), (540, [180, 180, 180, 0])):
            with self.subTest(size=size):
                messages = rtcm_to_gps_rtcm_data(_frame(size), sequence=0)
                self.assertEqual([m['len'] for m in messages], lengths)
                self.assertEqual(_reassemble(messages), _frame(size))

    def test_four_full_fragments_need_no_empty_one(self):
        """Verify the largest frame stops after fragment 3, as ArduPilot expects."""
        messages = rtcm_to_gps_rtcm_data(_frame(720), sequence=0)
        self.assertEqual([m['len'] for m in messages], [180, 180, 180, 180])
        self.assertEqual(_reassemble(messages), _frame(720))

    def test_sequence_wraps_at_32(self):
        """Verify the sequence number stays within its five bits."""
        (message,) = rtcm_to_gps_rtcm_data(_frame(10), sequence=33)
        self.assertEqual(message['flags'], 1 << 3)
        self.assertEqual(rtcm_to_gps_rtcm_data(_frame(10), sequence=31)[0]['flags'], 31 << 3)

    def test_empty_and_oversized_frames_are_rejected(self):
        """Verify frames that cannot be sent raise ValueError."""
        for frame in (b'', _frame(721)):
            with self.subTest(size=len(frame)):
                with self.assertRaises(ValueError):
                    rtcm_to_gps_rtcm_data(frame, sequence=0)


if __name__ == '__main__':
    unittest.main()
