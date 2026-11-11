import os
import sys

# ------------------------------------------------------------------------------
# Path Setup
# ------------------------------------------------------------------------------
# Every package under src/ is found by docs/_packages.py. A ROS 2 ament_python
# package nests its importable module one level below its colcon folder
# (src/<pkg>/<pkg>/...), and a plain Python package such as devkit-meshcore-avoid
# nests it under src/<pkg>/pkg/<module>/. Either way, the folder that holds the
# module goes on sys.path, so "import devkit_driver" or "import sowbot_avoid" works.
# Packages with no importable module (launch files, config scripts) get a generated
# page of source listings, _generated/launch_files.rst.
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _packages import discover, write_file_pages  # noqa: E402

_IMPORTABLE, _FILE_ONLY = discover()
for _root in sorted(set(_IMPORTABLE.values()), key=str):
    sys.path.insert(0, str(_root))
write_file_pages(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_generated"), _FILE_ONLY)

# autodoc actually imports these modules to pull docstrings. The CI runner has
# no ROS 2 install and no access to the hardware-specific / simulation deps
# below, so autodoc's import will fail without mocking them out.
autodoc_mock_imports = [
    "rclpy",
    "ament_index_python",
    "launch",
    "launch_ros",
    "sensor_msgs",
    "geometry_msgs",
    "nav_msgs",
    "std_msgs",
    "std_srvs",
    "diagnostic_msgs",
    "rtcm_msgs",
    "nav2_msgs",
    "nav2_simple_commander",
    "tf2_ros",
    "lifecycle_msgs",
    "rosgraph_msgs",
    "topological_navigation_msgs",
    "ublox_ubx_msgs",
    "pymavlink",
    "rosys",
    "nicegui",
    "fields2cover",
    "feldfreund_devkit",
    "sowbot_sim",
    "topological_nav_simulator",
    "meshcore",
]

# ------------------------------------------------------------------------------
# Project Information
# ------------------------------------------------------------------------------
project = "Feldfreund DevKit ROS"
copyright = "2026, Agroecology Lab"
author = "Agroecology Lab"
release = "caatinga-dev"

# ------------------------------------------------------------------------------
# General Configuration
# ------------------------------------------------------------------------------
# Sphinx Extensions
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "breathe",
    "myst_parser",
]

# Generate autosummary pages automatically
autosummary_generate = True

# Docstring configuration (Napoleon for Google & NumPy docstrings)
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True

# Autodoc default options
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}

# Source file suffixes
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# MyST-Parser Configuration for Markdown extensions
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
]
myst_heading_anchors = 3

master_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "xml"]

# ------------------------------------------------------------------------------
# Breathe Configuration (Doxygen C++ / ROS Integration)
# ------------------------------------------------------------------------------
# Points directly to the xml folder generated inside docs/ by Doxygen (OUTPUT_DIRECTORY = docs)
breathe_projects = {
    "feldfreund_devkit_ros": os.path.abspath("xml")
}
breathe_default_project = "feldfreund_devkit_ros"

# ------------------------------------------------------------------------------
# HTML Output Options (Read the Docs Theme)
# ------------------------------------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "navigation_depth": 4,
    "collapse_navigation": False,
    "sticky_navigation": True,
}
