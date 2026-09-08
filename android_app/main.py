"""
AllVsStrain Comparison - Android app (Kivy)

- Uses Android's SAF file picker (via plyer) to pick the All_final folder.
- Recursively finds AllVsStrain.txt / AllVsStrainTruncated.txt.
- Checkbox lists for compositions and Y columns (same detection logic
  as the desktop version).
- Plot rendered with matplotlib, then displayed inside a Kivy Scatter
  widget so pinch-to-zoom and one/two-finger pan behave exactly like
  zooming a photo in the Android Gallery app.
- Layout automatically switches between a portrait (stacked) and
  landscape (side-by-side) arrangement based on Window.width/height,
  which only changes when Android's auto-rotate is enabled (if
  auto-rotate is off, Android keeps the app pinned to one orientation
  and Window size never flips, so this naturally satisfies the
  "landscape only when auto-rotate is on" requirement without extra
  code).
"""

import os
import re
import io

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.image import Image as KivyImage
from kivy.uix.scatterlayout import Scatter
from kivy.core.window import Window
from kivy.core.image import Image as CoreImage
from kivy.properties import ObjectProperty
from kivy.clock import Clock

import pandas as pd
import matplotlib
matplotlib.use("Agg")  # render to an in-memory image, no GUI backend needed
import matplotlib.pyplot as plt


# ============================================================
# Same path/name detection logic as the desktop version
# ============================================================

COMPOSITION_RE = re.compile(r"(?i)(?:Ti|Zr)(?:_)?\d+")
LOADING_RE = re.compile(r"(?i)(tensile|tension|compressive|compression)s?")


def detect_composition(path):
    parts = os.path.normpath(path).split(os.sep)
    matches = [p for p in parts if COMPOSITION_RE.findall(p)]
    return matches[-1] if matches else "Unknown composition"


def detect_loading(path):
    parts = os.path.normpath(path).split(os.sep)
    for part in reversed(parts):
        m = LOADING_RE.search(part)
        if m:
            word = m.group(1).lower()
            if word.startswith("tens"):
                return "Tension"
            if word.startswith("compress"):
                return "Compression"
    return "Unknown loading"


def make_display_name(filepath):
    directory = os.path.dirname(filepath)
    return f"{detect_composition(directory)} - {detect_loading(directory)}"


class Dataset:
    def __init__(self, filepath):
        self.filepath = filepath
        self.name = make_display_name(filepath)
        self.df = None
        self.error = None

    def load(self):
        try:
            self.df = pd.read_csv(self.filepath, sep=r"\s+", engine="python")
            self.df = self.df.loc[:, ~self.df.columns.astype(str).str.startswith("Unnamed:")]
            self.df.columns = [str(c).strip() for c in self.df.columns]
            if "Strain" not in self.df.columns:
                self.error = "No 'Strain' column"
                return False
            for col in self.df.columns:
                self.df[col] = pd.to_numeric(self.df[col], errors="coerce")
            return True
        except Exception as exc:
            self.error = str(exc)
            return False


# ============================================================
# Pinch-zoom / pan image viewer (Gallery-app style)
# ============================================================

class ZoomableImage(Scatter):
    """
    A Scatter containing an Image. Scatter natively supports two-finger
    pinch-to-zoom and one-finger drag-to-pan on Android touch input,
    which is exactly the Gallery-app-style interaction requested.
    do_rotation is disabled so the plot doesn't spin when pinching.
    """
    def __init__(self, **kwargs):
        super().__init__(
            do_rotation=False,
            do_translation=True,
            do_scale=True,
            scale_min=0.5,
            scale_max=8.0,
            **kwargs,
        )
        self.img = KivyImage(allow_stretch=True, keep_ratio=True)
        self.add_widget(self.img)

    def set_image_data(self, png_bytes):
        core_img = CoreImage(io.BytesIO(png_bytes), ext="png")
        self.img.texture = core_img.texture
        self.size = self.img.texture.size
        self.img.size = self.size


# ============================================================
# Main app layout
# ============================================================

class RootWidget(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", **kwargs)

        self.datasets = []
        self.dataset_checks = {}   # filepath -> (CheckBox, Dataset)
        self.y_checks = {}         # column name -> CheckBox
        self.root_dir = None

        self._build_toolbar()

        # main content area: rebuilt on orientation change
        self.content_area = BoxLayout(orientation="vertical")
        self.add_widget(self.content_area)

        self._build_controls_panel()
        self._build_plot_panel()
        self._layout_for_orientation()

        Window.bind(size=lambda *a: self._layout_for_orientation())

    # --------------------------------------------------------
    # Toolbar
    # --------------------------------------------------------

    def _build_toolbar(self):
        toolbar = BoxLayout(size_hint_y=None, height="48dp", spacing="4dp", padding="4dp")

        pick_btn = Button(text="Select Folder")
        pick_btn.bind(on_release=lambda *a: self.choose_root())
        toolbar.add_widget(pick_btn)

        refresh_btn = Button(text="Refresh")
        refresh_btn.bind(on_release=lambda *a: self.refresh())
        toolbar.add_widget(refresh_btn)

        plot_btn = Button(text="PLOT")
        plot_btn.bind(on_release=lambda *a: self.plot())
        toolbar.add_widget(plot_btn)

        self.status_label = Label(text="Select a folder", size_hint_x=2)
        toolbar.add_widget(self.status_label)

        self.add_widget(toolbar)

    # --------------------------------------------------------
    # Controls panel (checkbox lists)
    # --------------------------------------------------------

    def _build_controls_panel(self):
        self.controls_panel = BoxLayout(orientation="vertical", spacing="4dp", padding="4dp")

        self.controls_panel.add_widget(Label(text="Compositions", size_hint_y=None, height="28dp", bold=True))
        ds_scroll = ScrollView(size_hint_y=0.5)
        self.dataset_grid = GridLayout(cols=1, size_hint_y=None, spacing="2dp")
        self.dataset_grid.bind(minimum_height=self.dataset_grid.setter("height"))
        ds_scroll.add_widget(self.dataset_grid)
        self.controls_panel.add_widget(ds_scroll)

        self.controls_panel.add_widget(Label(text="Y columns", size_hint_y=None, height="28dp", bold=True))
        y_scroll = ScrollView(size_hint_y=0.5)
        self.y_grid = GridLayout(cols=1, size_hint_y=None, spacing="2dp")
        self.y_grid.bind(minimum_height=self.y_grid.setter("height"))
        y_scroll.add_widget(self.y_grid)
        self.controls_panel.add_widget(y_scroll)

    # --------------------------------------------------------
    # Plot panel
    # --------------------------------------------------------

    def _build_plot_panel(self):
        self.plot_scroll_container = BoxLayout()
        self.zoom_image = ZoomableImage(size_hint=(None, None))
        self.plot_scroll_container.add_widget(self.zoom_image)

    # --------------------------------------------------------
    # Orientation-aware layout
    # --------------------------------------------------------

    def _layout_for_orientation(self):
        self.content_area.clear_widgets()

        landscape = Window.width > Window.height

        if landscape:
            # side-by-side: controls on the left, plot on the right
            self.content_area.orientation = "horizontal"
            self.controls_panel.size_hint_x = 0.35
            self.plot_scroll_container.size_hint_x = 0.65
        else:
            # stacked: controls on top, plot below
            self.content_area.orientation = "vertical"
            self.controls_panel.size_hint_y = 0.45
            self.plot_scroll_container.size_hint_y = 0.55

        self.content_area.add_widget(self.controls_panel)
        self.content_area.add_widget(self.plot_scroll_container)

    # --------------------------------------------------------
    # Folder picking (Android SAF via plyer)
    # --------------------------------------------------------

    def choose_root(self):
        try:
            from plyer import filechooser
            filechooser.choose_dir(on_selection=self._on_folder_chosen)
        except Exception as exc:
            self.status_label.text = f"Folder picker error: {exc}"

    def _on_folder_chosen(self, selection):
        if not selection:
            return
        self.root_dir = selection[0]
        self.status_label.text = self.root_dir
        self.refresh()

    # --------------------------------------------------------
    # Scanning
    # --------------------------------------------------------

    def refresh(self):
        if not self.root_dir:
            self.status_label.text = "Select a folder first."
            return

        self.datasets = []
        for root, dirs, files in os.walk(self.root_dir):
            for fname in ("AllVsStrain.txt", "AllVsStrainTruncated.txt"):
                if fname in files:
                    fp = os.path.join(root, fname)
                    d = Dataset(fp)
                    if d.load():
                        self.datasets.append(d)

        self.datasets.sort(key=lambda d: d.name.lower())
        self._populate_dataset_checks()
        self.status_label.text = f"Found {len(self.datasets)} files."

    def _populate_dataset_checks(self):
        self.dataset_grid.clear_widgets()
        self.dataset_checks = {}

        for d in self.datasets:
            row = BoxLayout(size_hint_y=None, height="36dp")
            cb = CheckBox(size_hint_x=None, width="36dp")
            cb.bind(active=lambda inst, val: self._update_y_columns())
            row.add_widget(cb)
            row.add_widget(Label(text=d.name, halign="left", valign="middle"))
            self.dataset_grid.add_widget(row)
            self.dataset_checks[d.filepath] = (cb, d)

        self._update_y_columns()

    def _get_selected_datasets(self):
        return [d for cb, d in self.dataset_checks.values() if cb.active]

    def _update_y_columns(self):
        selected = self._get_selected_datasets()
        self.y_grid.clear_widgets()
        self.y_checks = {}

        if not selected:
            return

        common = list(selected[0].df.columns)
        for d in selected[1:]:
            common = [c for c in common if c in d.df.columns]

        if "Strain" in common:
            common.remove("Strain")

        for col in common:
            row = BoxLayout(size_hint_y=None, height="36dp")
            cb = CheckBox(size_hint_x=None, width="36dp")
            row.add_widget(cb)
            row.add_widget(Label(text=col, halign="left", valign="middle"))
            self.y_grid.add_widget(row)
            self.y_checks[col] = cb

    def _get_selected_y_columns(self):
        return [col for col, cb in self.y_checks.items() if cb.active]

    # --------------------------------------------------------
    # Plotting
    # --------------------------------------------------------

    def plot(self):
        selected = self._get_selected_datasets()
        y_columns = self._get_selected_y_columns()

        if not selected or not y_columns:
            self.status_label.text = "Select composition(s) and Y column(s) first."
            return

        fig, ax = plt.subplots(figsize=(9, 6), dpi=150)

        cmap = plt.get_cmap("turbo")
        total_curves = len(selected) * len(y_columns)
        colors = (
            [cmap(0.5)] if total_curves == 1
            else [cmap(i / (total_curves - 1)) for i in range(total_curves)]
        )

        idx = 0
        for d in selected:
            for y_col in y_columns:
                if y_col not in d.df.columns:
                    continue
                temp = d.df[["Strain", y_col]].dropna()
                if temp.empty:
                    continue
                ax.plot(
                    temp["Strain"], temp[y_col],
                    label=f"{d.name} | {y_col}",
                    color=colors[idx], linewidth=1.6,
                )
                idx += 1

        ax.set_xlabel("Strain")
        ax.set_ylabel(", ".join(y_columns))
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8, loc="best")
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)

        self.zoom_image.set_image_data(buf.getvalue())
        self.status_label.text = f"Plotted {idx} curves."


class AllVsStrainApp(App):
    def build(self):
        return RootWidget()


if __name__ == "__main__":
    AllVsStrainApp().run()
