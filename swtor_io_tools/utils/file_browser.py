# <pep8 compliant>

"""
Helpers for importers that open the File Browser from their own invoke().

Blender reopens a File Browser in the folder the operator last used only if the
operator's `filepath` property still holds something from that run. These importers
used to blank it as soon as they had read it (so a failed import couldn't leave a stale
path behind, and so invoke() could tell a script's `filepath` from a leftover), which
made every browser open at Blender's default folder instead.

The two helpers keep both behaviours: remember_folder() leaves the FOLDER behind, and
is_file_call() tells a real file path from that remembered folder.
"""

import os


def remember_folder(operator, paths):
    # type: (object, list) -> None
    """
    Call once the paths have been read from the operator. Clears the selected files and
    leaves the folder they came from in `filepath`, so the next File Browser opens there.
    """
    folder = operator.directory or (os.path.dirname(paths[0]) if paths else "")
    operator.files.clear()
    operator.filepath = os.path.join(folder, "") if folder else ""


def is_file_call(filepath):
    # type: (str) -> bool
    """
    True if FILEPATH is a path to a file, as a script passes it. False when it is empty or
    just a folder (the one remember_folder() left behind), so invoke() opens the browser.
    """
    return bool(filepath) and not filepath.endswith(("/", "\\")) and not os.path.isdir(filepath)
