"""Plain types shared by the Plan tab, its view model and its application service (no ROS imports)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

MIN_CORNERS = 3


@dataclass(frozen=True)
class PlanRequest:
    """What the user asked the field planner for.

    corners_ll are the field boundary corners as (lat, lon). obstacle_rings are lat/lon rings
    to plan around, padded by obstacle_pad_m. angle_deg is ignored when contour is set, which
    follows the elevation log at recon_path on a dem_res metre grid; an empty recon_path means
    the default log.
    """
    corners_ll: list
    obstacle_rings: list = field(default_factory=list)
    tool_width: float = 1.2
    angle_deg: float = 0.0
    headland_m: float = 0.0
    snake: bool = True
    obstacle_pad_m: float = 0.0
    contour: bool = False
    recon_path: str = ''
    dem_res: float = 1.0

    @classmethod
    def from_form(cls, corners_ll: list, obstacle_rings: list, *, tool_width, angle_deg,
                  headland_m, snake, obstacle_pad_m, contour, recon_path, dem_res) -> PlanRequest:
        """Build a request from the raw Plan form values.

        None, empty strings and numeric zero values use the defaults: 1.2 meters for
        tool width, 1 meter for grid resolution, and zero for angle, headland and padding.
        Numeric strings are converted with float; invalid strings raise ValueError and
        unsupported values raise TypeError. Checkbox values use their truthiness.
        Copy the boundary list; retain the supplied obstacle rings by reference.
        """
        return cls(
            corners_ll=list(corners_ll),
            obstacle_rings=obstacle_rings,
            tool_width=float(tool_width or 1.2),
            angle_deg=float(angle_deg or 0),
            headland_m=float(headland_m or 0.0),
            snake=bool(snake),
            obstacle_pad_m=float(obstacle_pad_m or 0.0),
            contour=bool(contour),
            recon_path=recon_path or '',
            dem_res=float(dem_res or 1.0),
        )


class PlanMode(StrEnum):
    """How a successful run produced its rows."""
    STRAIGHT = 'straight'
    CONTOUR = 'contour'
    FLAT_FALLBACK = 'flat_fallback'  # contour was asked for, the field is flat, rows are straight


class PlanFailure(StrEnum):
    """Why a run produced no rows."""
    TOO_FEW_CORNERS = 'too_few_corners'
    PLANNING = 'planning'      # an expected error: bad field, missing elevation log
    UNEXPECTED = 'unexpected'  # anything else; logged by the service


@dataclass(frozen=True)
class PlanOutcome:
    """Result of a planning run, as plain data with no display text.

    A successful run has planned rows and a mode. A failed one has a failure kind and the
    error text in detail, if any. Whoever called plan() decides whether to keep an earlier
    draft; the service keeps none.
    """
    planned: PlannedRows | None = None
    mode: PlanMode | None = None
    failure: PlanFailure | None = None
    detail: str = ''

    @property
    def ok(self) -> bool:
        """Return True when the run produced rows."""
        return self.failure is None


@dataclass(frozen=True)
class ImportOutcome:
    """Result of importing a web-planner plan: planned rows, or the reason there are none."""
    planned: PlannedRows | None = None
    error: str = ''

    @property
    def ok(self) -> bool:
        """Return True when the plan was imported."""
        return not self.error


@dataclass(frozen=True)
class PlannedRows:
    """Swaths waiting to be saved as topology rows, with what saving needs to know about them.

    origin_ll is the (lat, lon) the swaths were projected from. break_after lists the swath
    indices that must not get a headland edge to the next swath.
    """
    swaths: list
    origin_ll: tuple[float, float] | None
    contour_used: bool = False
    break_after: frozenset[int] = frozenset()
