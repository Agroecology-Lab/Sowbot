"""Verify the Plan form values map onto a PlanRequest the way the form always did."""
import unittest

from devkit_ui.plan_types import PlanRequest

CORNERS = [(51.5, -2.5), (51.5, -2.49), (51.51, -2.49)]
RING = [(51.505, -2.495), (51.505, -2.494), (51.506, -2.494)]


def form(**overrides):
    """Return filled-in form values, with overrides replacing individual widgets."""
    values = {'tool_width': 2.0, 'angle_deg': 30, 'headland_m': 1.5, 'snake': True,
              'obstacle_pad_m': 0.5, 'contour': False, 'recon_path': '/tmp/recon.csv',
              'dem_res': 2.0}
    values.update(overrides)
    return values


class TestPlanRequestFromForm(unittest.TestCase):
    def test_widget_values_reach_the_request(self) -> None:
        """Verify every widget value lands on the matching request field."""
        request = PlanRequest.from_form(CORNERS, [RING], **form(contour=True))

        self.assertEqual(request, PlanRequest(
            corners_ll=CORNERS, obstacle_rings=[RING], tool_width=2.0, angle_deg=30.0,
            headland_m=1.5, snake=True, obstacle_pad_m=0.5, contour=True,
            recon_path='/tmp/recon.csv', dem_res=2.0))

    def test_empty_or_zero_numbers_take_the_form_defaults(self) -> None:
        """Verify None and 0 widgets plan with 1.2 m wide tools, no angle or headland, 1 m grid."""
        for empty in (None, 0, ''):
            with self.subTest(empty=empty):
                request = PlanRequest.from_form(CORNERS, [], **form(
                    tool_width=empty, angle_deg=empty, headland_m=empty,
                    obstacle_pad_m=empty, dem_res=empty, recon_path=empty))

                self.assertEqual((request.tool_width, request.angle_deg, request.headland_m,
                                  request.obstacle_pad_m, request.dem_res, request.recon_path),
                                 (1.2, 0.0, 0.0, 0.0, 1.0, ''))

    def test_checkbox_values_become_bools(self) -> None:
        """Verify snake and contour are plain bools, not the raw widget values."""
        request = PlanRequest.from_form(CORNERS, [], **form(snake=None, contour=1))

        self.assertIs(request.snake, False)
        self.assertIs(request.contour, True)

    def test_corners_are_copied(self) -> None:
        """Verify later edits to the drawn boundary do not change a request already made."""
        drawn = list(CORNERS)
        request = PlanRequest.from_form(drawn, [], **form())

        drawn.append((51.52, -2.5))

        self.assertEqual(request.corners_ll, CORNERS)

    def test_numeric_strings_are_converted_without_treating_zero_as_empty(self) -> None:
        """Keep explicit string zeros, unlike the numeric zero widget defaults."""
        request = PlanRequest.from_form(CORNERS, [], **form(
            tool_width='0', angle_deg=' -12.5 ', headland_m='2.75',
            obstacle_pad_m='0.25', dem_res='0'))

        self.assertEqual((request.tool_width, request.angle_deg, request.headland_m,
                          request.obstacle_pad_m, request.dem_res),
                         (0.0, -12.5, 2.75, 0.25, 0.0))
        for value in (request.tool_width, request.angle_deg, request.headland_m,
                      request.obstacle_pad_m, request.dem_res):
            self.assertIsInstance(value, float)

    def test_invalid_numbers_raise_for_each_numeric_field(self) -> None:
        """Reject invalid and unsupported values instead of silently using defaults."""
        for field in ('tool_width', 'angle_deg', 'headland_m', 'obstacle_pad_m', 'dem_res'):
            for value, error in (('not a number', ValueError), (object(), TypeError)):
                with self.subTest(field=field, value=value), self.assertRaises(error):
                    PlanRequest.from_form(CORNERS, [], **form(**{field: value}))

    def test_obstacle_rings_remain_the_supplied_collection(self) -> None:
        """Retain obstacle rings while taking an independent boundary list."""
        rings = [list(RING)]

        request = PlanRequest.from_form(CORNERS, rings, **form())

        self.assertIs(request.obstacle_rings, rings)
        self.assertIsNot(request.corners_ll, CORNERS)


if __name__ == '__main__':
    unittest.main()
