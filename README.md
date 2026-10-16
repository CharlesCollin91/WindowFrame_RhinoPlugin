# WindowFrame_CC

[Français](README.fr.md) · **English**

A Rhino 8 Python script that builds a rectangular-section **window frame** along the outer edge of any selected geometry (curves, surfaces, polysurfaces, meshes, SubD, hatches). It opens a small floating window: select geometry, set the widths, press **Run**. One Ctrl+Z undoes a run, and your input objects are never modified.

![Input outlines (black) and the generated window frames (green) in the Rhino viewport](pictures/1235.JPG)

Version **1.12** · License: [GPL-3.0-or-later](LICENSE) · Code walkthrough: [ARCHITECTURE.md](ARCHITECTURE.md)

## Screenshots
![The WindowFrame_CC window](pictures/window.jpg)

## Requirements
Windows, Rhino 8 or later (built-in Python 3). No extra packages.

## Install
1. Download `WindowFrame_CC.py` and put it in any folder.
2. In Rhino, create a toolbar button with this macro (adjust the path):
   ```
   ! _-RunPythonScript "C:\Tools\WindowFrame_CC\WindowFrame_CC.py"
   ```

## Inputs
Curves (open or closed), surfaces, polysurfaces, extrusions, meshes, SubD and hatches. Only the outer edge is used; holes are ignored, and one frame is made per outline. Touching curves selected together are joined first, and open outlines can be closed automatically.

## Contributing
Improvements are welcome. Start with [ARCHITECTURE.md](ARCHITECTURE.md), which explains how the single file is organized and where each step lives. To report a bug, open an issue and attach the matching file from the `logs` folder the plugin creates next to the script. Version history: [CHANGELOG.md](CHANGELOG.md).

## License
GNU General Public License v3.0 or later: you can use, study, modify and share this code, and any distributed modified version must stay under the same license. See [LICENSE](LICENSE).
