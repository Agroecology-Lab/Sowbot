=====================
Python API Reference
=====================

Auto-generated reference for the Python packages under ``src/``.
Each entry below recurses into every submodule, class and function and
generates its own page (see ``autosummary_generate`` / ``:recursive:``
in ``conf.py``).

.. note::
   ``devkit_bringup`` and ``devkit_simulation`` have no importable Python
   modules (launch files, URDF, worlds and config only). Their Python files
   are listed as source on :doc:`_generated/launch_files`.
   ``devkit-meshcore-avoid`` is imported as ``sowbot_avoid``. Its
   ``sweeps/`` scripts are not part of the installed package and are not
   documented here. ``docs/check_coverage.py`` fails the CI build if a
   package under ``src/`` is missing from these pages.

.. autosummary::
   :toctree: _autosummary
   :recursive:

   devkit_driver
   devkit_f2c_planner
   devkit_mavlink_bridge
   devkit_ui
   sowbot_avoid
