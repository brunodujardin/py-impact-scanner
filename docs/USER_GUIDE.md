# Target Impact Analyser – User Guide

The Target Impact Analyser measures your shooting groups from a photo of the target.
You open the photo, tell the app the scale, let it find the bullet holes, correct
anything it got wrong, and save the result. A photo can hold several groups (a card
with many small diagrams, or several series on one target): each group is measured on
its own, and the photo gets the mean and spread of its groups. Over time the saved
records give you the average group size per weapon, with its standard deviation.

## 1. Installation (Windows)

Requirements: Windows 10/11 and Python 3.10 or newer (https://www.python.org/downloads/,
tick *Add python.exe to PATH* during installation).

1. Open PowerShell in the application folder (right-click the folder, *Open in Terminal*).
2. Run the setup once:

   ```powershell
   .\setup.ps1
   ```

   It creates a private Python environment (`.venv`) and downloads the libraries.
3. Start the application:

   ```powershell
   .\run.ps1
   ```

   `run.ps1` runs the setup by itself if `.venv` is missing.

If PowerShell refuses to run scripts, allow local scripts once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Your data is stored in the file `targets.db` next to the scripts, and a copy of every
saved photo goes to the `images\` folder next to it (JPEG, reduced to 2500 pixels on
the longest side). Back up the database file and the `images\` folder together. To
use a different database file: `.\run.ps1 C:\path\to\other.db` (its `images\` folder
is created next to that file).

## 2. Taking a good photo

The detector works on any phone photo, but these habits make it reliable:

- Shoot the target **flat and from the front**, with the camera parallel to the paper.
  A strong angle distorts distances.
- **Even light** without hard shadows across the target (no phone shadow, no window
  glare). Soft shadows are tolerated.
- Keep the **whole target** in the frame with some paper margin around it.
- Accepted formats: JPEG, JPG, PNG, HEIC/HEIF (iPhone), BMP, TIFF, WebP. The photo
  is rotated automatically according to the phone's orientation data.

## 3. The main window

The window has two tabs: **Analyse** and **Records & averages**.

The Analyse tab has three areas:

| Area | Content |
|---|---|
| Left column | The workflow buttons: open image, scale tools, detection parameters, manual correction tools. |
| Centre | The photo. Click on it when a tool is active. |
| Right column | The group list with its buttons, the measurements of the selected group, the summary of the photo, the display unit and the save form. |

## 4. Step by step

### 4.1 Open the photo

Click **1. Open image…** and choose the photo. The file name and pixel size appear
in the status line at the bottom right.

### 4.2 Set the scale

The app needs to know how many pixels make a centimetre. Two ways:

**Measure 2 points (recommended when you know a real distance)**

1. Click **Measure 2 points**.
2. Click two points on the photo whose real distance you know, for example the two
   edges of the black circle, or two ring lines, or a ruler laid on the target.
   A yellow line joins the points.
3. In the dialog, type the real distance and choose **cm** or **inch**.

**Auto: from outer black circle**

The app finds the outer edge of the black part of the target. Type its real diameter
(the default is 20 cm, the black disc of a 25 m precision pistol target). Check the
green circle drawn on the photo: if it does not follow the black edge, use the 2-point
method instead.

The scale label turns green once the scale is set. Set the scale **before** analysing:
the app uses it to tell single holes from torn multi-shot regions.

### 4.3 Analyse the impacts

Optionally type the number of **Shots fired**. Then click **Analyse impacts**.

Each detected shot is drawn as a numbered circle in the colour of its group. A dashed
rectangle shows each group's extent and a cross marks its centre. Detected torn
regions that contain several shots are split into several points.

If the photo shows **several target diagrams** (a card of 10 or 25 small targets), the
app finds every black circle and makes one group per diagram: the circles are drawn in
the group colours with their names (G1, G2, … in reading order) and every shot is
assigned to the nearest diagram. Diagrams without any shot appear greyed in the group
list. A photo with a single target gives a single group G1.

### 4.4 Correct the detection

Automatic detection is good but not perfect. Fix it with the click tools:

- **Add impact (click)**: click on each hole the app missed. These are drawn in
  orange. The tool stays active until you choose another one.
- **Remove impact (click)**: click on a wrong circle to delete it.
- **Clear all impacts**: start again from zero (useful if you prefer to mark all
  shots by hand). The groups are kept.
- **Move impact to selected group (click)**: select the destination group in the list,
  then click the shots to move there (for example a shot that landed between two
  diagrams and was assigned to the wrong one).

Typical things to correct:

- A big torn hole whose shot count was estimated wrong (remove or add points).
- Coloured pasters or marker pen marks taken for holes.
- Dirt, folds or a strong shadow edge taken for holes.
- A hole on the white paper far outside the rings that was missed.

The measurements update immediately after every correction.

### 4.5 Groups

The **Groups** list at the top right shows every group with its number of shots and
its width, height and diameter. Click a row to select a group: it is highlighted on
the photo, its values appear under *Selected group*, and the *Add impact* and *Move
impact* tools work on it.

- **New**: creates a group (you can name it; the default is the next G number) and
  switches to *Add impact* so you can click its shots right away. Use it when one
  target carries several series shot one after the other, or when the automatic
  grouping missed a diagram.
- **Rename**: changes the name of the selected group (for example the cell number
  printed on the card).
- **Delete**: removes the selected group **and its shots** after confirmation. To keep
  the shots, move them to another group first with *Move impact to selected group*.
- **Auto-group: target circles**: redoes the automatic grouping by printed circle on
  the current shots (after you added shots by hand, for example). Needs at least two
  circles in the photo.
- **Auto-group: distance**: groups the shots by proximity. Shots closer than the
  distance you type (default 5 cm) end up in the same group. Use it for several
  series on a plain target without printed circles; pick a distance smaller than the
  gap between the series and larger than the spread inside one.

Empty groups are shown in grey, are not measured and are not saved.

### 4.6 Read the results

The right column shows, in the chosen **display unit** (cm or inch), for the
**selected group**:

| Value | Meaning |
|---|---|
| Shots | Number of impact points in the group. |
| Width | Horizontal spread, centre of the leftmost hole to centre of the rightmost. |
| Height | Vertical spread, centre to centre. |
| Diameter (extreme spread) | Largest centre-to-centre distance between two holes. This is the usual "group size". |

All values are centre to centre. To get an edge-to-edge group size, add one bullet
diameter to the diameter value.

Under **This image** you get the same figures over all the groups of the photo that
contain shots: number of groups and total shots, then the **mean ± standard
deviation** of the width, height and diameter, and the **P90 diameter** (90 % of the
groups are at or below it). With a single group the means equal the group values and
the deviations are 0.

### 4.7 Save the record

Fill in the form (date, weapon, distance, ammunition, notes) and click
**Save to database**. One record is saved for the photo with all its groups and shots;
the status line confirms the record number, the number of groups and the name of the
photo copy stored in `images\`. The original photo is no longer needed afterwards. The
weapon name is used later for filtering, so write it the same way every time (for
example `45` or `SP01`).

## 5. Records & averages

The second tab lists every saved record (one row per photo) with its number of
groups, total shots and the **mean** width, height and diameter of its groups, in the
current display unit.

- **Weapon filter**: show only one weapon. Leave empty for all.
- **Refresh**: reload the list (it also refreshes when you open the tab).
- **Average of selection**: select one or more rows (Ctrl-click or Shift-click) and
  press the button. The box below shows, for width, height, diameter and shots per
  group, the **mean ± sample standard deviation** over **all the groups** of the
  selected records, in **both cm and inch** side by side (whatever the display unit).
  A card with 25 groups therefore counts 25 times, a single-group photo once. The line
  **Diameter P90** is the 90th percentile of the group diameters: 90 % of the groups
  have a diameter at or below this value, which makes it a good "what I can count on"
  figure. Selecting one record gives the statistics of that photo's groups.
- **Average of all listed**: the same over every row currently listed (so with a
  weapon filter, the average for that weapon).
- **Load record**: select exactly one row and reopen the stored photo copy with its
  groups, impacts, scale and form fields, for checking or re-measuring.
- **Delete selected**: removes the selected records after confirmation, together with
  their stored photo copies in `images\` when no other record uses them. This cannot
  be undone.

## 6. Detection parameters

The defaults work for the target styles the app was tuned on: targets with a solid
black disc (25 m precision pistol), ring targets with a white centre (10 m air rifle /
pistol cards) and cards of 25 small ring diagrams (.22 "Hunter"), on grey or wooden
backing. Change a parameter and click **Analyse impacts** again to see its effect.

| Parameter | Default | When to change it |
|---|---|---|
| Hole max brightness (% of paper) | 84 | Holes appear as grey or brown backing darker than the paper. Raise it (e.g. 88) if light-grey holes are missed. Lower it (e.g. 78) if shadows or dirt are detected as holes. |
| Colour saturation (0-255) | 95 | Wood backing is detected by its orange colour. Lower it if brown holes are missed; raise it if warm-lit paper or coloured pasters are detected. |
| Open kernel (px) | 11 | Removes ring lines, digits and print edges. Raise it if lines or digits are detected; lower it if small holes disappear. |
| Min area (px²) | 150 | Smaller spots are ignored. Raise it to drop dust and small specks. |
| Hole core Ø (mm) | 4.0 | The size of the clean part of one hole on your targets. With the scale set, this tells single holes from torn multi-shot regions. For .45 ACP on paper the visible core is about 4 mm; air rifle pellets about 4.5 mm. |
| Shots fired | empty | Type the real number when you know it: the torn regions are then split into exactly the missing number of shots. |

## 7. Troubleshooting

| Symptom | What to do |
|---|---|
| *No black circle found* when using the auto scale | Use **Measure 2 points** instead. |
| Measurements show "- (set scale)" | Set the scale (step 4.2). |
| Many false circles along a shadow edge | Retake the photo without the hard shadow, or lower *Hole max brightness*, or remove them with the Remove tool. |
| Every hole is split into several points | Set the scale before analysing, and check *Hole core Ø*. Or type *Shots fired*. |
| Holes on the white paper outside the rings are missed on a solid-disc target | Add them with **Add impact**. |
| Holes showing a nearly black backing (black board, very dark wood in a shadow) are missed | They cannot be told from the print by colour. Select the group and add them with **Add impact**. |
| A card gives fewer groups than diagrams | A circle broken by the grid line or by writing is not found. Press **New** to create the missing group and click its shots, or move them with **Move impact to selected group**. |
| A shot between two diagrams went to the wrong group | Select the right group and use **Move impact to selected group**. |
| Cannot open a `.heic` file | Run `.\setup.ps1` again; it installs the `pillow-heif` package. |
| The app does not start | Run `.\setup.ps1` and read its last lines; it checks every library. |
