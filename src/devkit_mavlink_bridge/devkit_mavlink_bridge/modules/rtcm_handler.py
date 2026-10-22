"""Split an RTCM3 frame into GPS_RTCM_DATA (#233) fields.

Plain Python, no rclpy or pymavlink, so it can be unit tested without ROS.

One GPS_RTCM_DATA message carries at most 180 bytes. A longer frame is sent as up to four
fragments. The flags byte is laid out as:

  bit 0      1 if the frame is fragmented
  bits 1-2   fragment number, 0 to 3
  bits 3-7   sequence number, shared by every fragment of one frame

ArduPilot learns how many fragments there are from the first one shorter than 180 bytes, so a
frame whose length is an exact multiple of 180 needs a final zero-length fragment. The one
exception is four full fragments (720 bytes), where it stops after the fourth.
"""

MAX_FRAGMENT_BYTES = 180
MAX_FRAGMENTS = 4
MAX_FRAME_BYTES = MAX_FRAGMENT_BYTES * MAX_FRAGMENTS
SEQUENCE_MODULUS = 32


def rtcm_to_gps_rtcm_data(frame: bytes, sequence: int) -> list[dict]:
    """Return the fields of each GPS_RTCM_DATA message needed to send one RTCM3 frame.

    Each dict holds flags, len and data, ready for pymavlink's
    mav.gps_rtcm_data_send(**fields). data is a list of 180 ints, padded with zeros.
    The sequence number is reduced modulo 32.
    Raise ValueError if the frame is empty or longer than 720 bytes.
    """
    if not frame:
        raise ValueError('RTCM frame is empty')
    if len(frame) > MAX_FRAME_BYTES:
        raise ValueError(f'RTCM frame is {len(frame)} bytes, more than the {MAX_FRAME_BYTES} that fit')
    sequence_bits = (sequence % SEQUENCE_MODULUS) << 3
    if len(frame) <= MAX_FRAGMENT_BYTES:
        return [_fields(sequence_bits, frame)]
    chunks = [frame[i:i + MAX_FRAGMENT_BYTES] for i in range(0, len(frame), MAX_FRAGMENT_BYTES)]
    if len(chunks[-1]) == MAX_FRAGMENT_BYTES and len(chunks) < MAX_FRAGMENTS:
        chunks.append(b'')
    return [_fields(sequence_bits | (number << 1) | 1, chunk) for number, chunk in enumerate(chunks)]


def _fields(flags: int, chunk: bytes) -> dict:
    """Build the message fields for one fragment, padding the data to 180 bytes."""
    return {
        'flags': flags,
        'len': len(chunk),
        'data': list(chunk) + [0] * (MAX_FRAGMENT_BYTES - len(chunk)),
    }
