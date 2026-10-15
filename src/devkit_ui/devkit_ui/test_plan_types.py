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


if __name__ == '__main__':
    unittest.main()
