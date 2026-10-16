# WindowFrame_CC

[Français](README.fr.md) · **English**

A Rhino 8 Python script that builds a rectangular-section **window frame** along the outer edge of any selected geometry (curves, surfaces, polysurfaces, meshes, SubD, hatches). It opens a small floating window; select geometry, set the widths, press **Run**.

Version **1.11** · License: [GPL-3.0-or-later](LICENSE) · Code walkthrough: [ARCHITECTURE.md](ARCHITECTURE.md)

## Requirements
- Windows, Rhino 8 or later (the built-in Python 3 is used)
- No extra packages

## Install
1. Download `WindowFrame_CC.py` (green **Code** button > **Download ZIP**, or just the single file) and put it in any folder you like, for example `C:\Tools\WindowFrame_CC\`.
2. In Rhino, create a toolbar button with this macro (adjust the path):
   ```
   ! _-RunPythonScript "C:\Tools\WindowFrame_CC\WindowFrame_CC.py"
   ```
   Or run it once from Rhino's script editor (`ScriptEditor` command > open the file > Run).
3. Click the button: the WindowFrame window opens. Closing and reopening the window is enough to load a new version of the file.

The plugin writes its session logs in a `logs` folder next to the script. Delete the folder to uninstall.

## Use
1. Select the geometry whose outline should become a frame.
2. Set the widths, offset, depth and output (table below).
3. Press **Run**. One Ctrl+Z undoes the whole run. Your input objects are never modified.

## Inputs
Curves (open or closed, one or many segments), surfaces, polysurfaces, extrusions, meshes, SubD, hatches. Only the outer edge is used; holes are ignored. One frame is made per outline.
- Touching curves selected together are always joined first (needed for clean junctions).
- `Close open outlines` (on by default): an open outline is closed (tiny gap: snapped; otherwise a straight segment). Off: the outline gets an open frame, ends cut square.
- Input is cleaned first: collinear / co-circular pieces are merged. If more than 8 segments remain (mesh edges, exploded polylines), each run between real corners (35 degrees or more) is rebuilt as one line or one smooth curve.

## Settings (mm, converted to document units)
| Setting | Effect |
|---|---|
| Sides / Top / Bottom | Member width in the outline plane. `Link` = one value for all three. |
| Offset: Inside / Outside / Center | The outline is the outer face / the inner face / the middle of the frame. |
| Depth + Front / Back / Center | Thickness perpendicular to the outline plane. Front = toward the active view camera (world -Y, +X or +Z when the camera is edge-on). |
| Output: Solid / Mitered 45 / Mitered 90 | **Solid**: one single piece. **Mitered 45**: one piece per side, diagonal joints. **Mitered 90**: one piece per side, square joints (top and bottom run full width, sides stop between them). |
| Ungroup / Group / Group all | No group; one group per frame; one group per frame plus an outer group around all frames of the run. |

Side / top / bottom recognition: "up" is world Z projected in the outline plane (world Y for a flat outline). A segment within 45 degrees of up is a side; otherwise it is top if above the outline centre, bottom if below.

## Built-in test
`Test set-1` builds 20 reference inputs on a layer named `WindowFrame_CC_TEST`, away from the model, and runs 27 combinations (Offset x Output x Depth) on each: 540 runs. Results are laid out in a grid coloured green / amber / red (PASS / WARN / FAIL). Checks: valid solid, closed, expected number of members, face count, plausible volume. Full results go to `logs\TEST_<date>.json`. One Ctrl+Z undoes the test.

## Known limits
- Rectangular section only (no custom moulding profile yet).
- Mitered outputs are built member by member (no boolean split). On non-planar outlines the depth follows the average plane; twisted outlines leave a small step at the corners.
- Solid on a non-planar outline is one continuous skin; if that fails, the union of the 45-degree pieces is used (the report says so).
- Butt joints: when two members of the same kind meet, the longer one runs through.
- A closed outline with no corner (circle, ellipse) is built as two halves cut square in Mitered outputs.
- Several closed outlines in Mitered mode are less reliable for now.
- Closed solids (no free edge) and closed meshes are refused.
- A single straight line has no inside: Center is used.
- Mesh outlines are polylines: each small segment is classified on its own, so different top/side widths can alternate on curved mesh edges.
- Very sharp corners (under about 6 degrees): no miter, a straight bridge is used.
- A width larger than a short segment or a tight arc: that outline is refused.

## Contributing
Improvements are welcome. Start with [ARCHITECTURE.md](ARCHITECTURE.md), which explains how the single file is organized and where each step of the pipeline lives. Please open an issue to describe a bug (attach the matching file from `logs\`) or an idea before a large change. See [CHANGELOG.md](CHANGELOG.md) for the version history.

## License
GNU General Public License v3.0 or later. You can use, study, modify and share this code; any distributed modified version must stay under the same license. See [LICENSE](LICENSE).
