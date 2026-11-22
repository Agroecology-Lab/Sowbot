"""Interface to the robot motion stack. Implement Driver for Nav2, ArduPilot, etc."""


class Driver:
    """Implement this against the robot's motion stack (ROS 2 Nav2 action, ArduPilot, etc.)."""

    async def drive(self, a, b):
        """Drive along edge a -> b and return when the robot has arrived at b.

        The base implementation raises NotImplementedError.
        """
        raise NotImplementedError


    async def retreat(self, a, b):
        """Return to a after `drive(a, b)` was cancelled part-way along the edge.

        Only needed when the agent runs with react=True. The base implementation raises
        NotImplementedError.
        """
        raise NotImplementedError
