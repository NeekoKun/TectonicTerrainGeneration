import logging
import random
import json
import tqdm
import sys
import math
from collections import Counter
from noise import pnoise2
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPolygonF, QBrush, QColor, QPen, QPainter, QImage
from PySide6.QtWidgets import QApplication, QComboBox, QGraphicsScene, QGraphicsView, QGraphicsPolygonItem, QMainWindow, QMenu, QToolButton

###############
## Constants ##
###############

GRID_SIZE = GRID_WIDTH, GRID_HEIGHT = 200, 200

COLORS = {
    "ocean_deep": "#2980b9",
    "ocean_shallow": "#3498db",
    "plains": "#2ecc71",
    "mountains": "#95a5a6",

    "ocean_plate": "#b0309d",
    "continental_plate": "#ffd512",

    "border_tile": "#2c3e50",
    "interior_tile": "#ecf0f1",
    "hex_border": "#2c3e50",
}

PLATE_COUNT = 30
GRID_IMPORT_PATH = Path(__file__).resolve().parent / ".json"

with open(Path(__file__).resolve().parent / "settings.json", "r", encoding="utf-8") as config_file:
    settings = json.load(config_file)

logging.basicConfig(level=logging.INFO, format='[%(asctime)s - %(levelname)s]: %(message)s')

def height_to_color(height: float):
    if height < settings["colors"]["negative_altitudes"]["min"]:
        return settings["colors"]["negative_altitudes"]["palette"][0]
    elif height > settings["colors"]["positive_altitudes"]["max"]:
        return settings["colors"]["positive_altitudes"]["palette"][0]
    elif height < 0:
        step = settings["colors"]["negative_altitudes"]["min"] / len(settings["colors"]["negative_altitudes"]["palette"])
        index = int(height / step)
        try:
            return settings["colors"]["negative_altitudes"]["palette"][-index]
        except IndexError:
            logging.warning("Height to color mapping failed for height %s: step=%s, index=%s", height, step, index)
            return settings["colors"]["negative_altitudes"]["palette"][-1]
    else:
        step = settings["colors"]["positive_altitudes"]["max"] / len(settings["colors"]["positive_altitudes"]["palette"])
        index = len(settings["colors"]["positive_altitudes"]["palette"]) - 1 - int(height / step)
        try:
            return settings["colors"]["positive_altitudes"]["palette"][index]
        except IndexError:
            logging.warning("Height to color mapping failed for height %s: step=%s, index=%s", height, step, index)
            return settings["colors"]["positive_altitudes"]["palette"][0]

class Tile:
    def __init__(self, col: int, row: int, index: int, color_str: str):
        self.col: int = col      # X-axis on a square grid
        self.row: int = row      # Y-axis on a square grid
        self.index: int = index
        self.color: str = color_str
        self.plate_color: str = color_str
        self.plate_center: list[int] | None = None
        self.plate_id: int | None = None
        self.is_oceanic_plate: bool = False
        self.is_border: bool = True
        self.type: str = ""
        self.height: float | None = None
        self.movement_vector: tuple[float, float] = (0.0, 0.0)

class Plate:
    def __init__(self, id: int, color_str: str, oceanic=False, movement_vector=(0.0, 0.0)):
        self.id: int = id
        self.color: str = color_str
        self.oceanic: bool = oceanic
        self.size: int = 0

        if self.oceanic:
            self.height: float = random.gauss(settings["height_statistics"]["average_sea_height"], settings["height_statistics"]["sea_height_variance"])
        else:
            self.height: float = random.gauss(settings["height_statistics"]["average_land_height"], settings["height_statistics"]["land_height_variance"])
        
        self.movement_vector: tuple[float, float] = movement_vector
        self.tiles = []
    
    def __setattr__(self, name, value):
        super().__setattr__(name, value)

        if name == "oceanic":
            self.height = random.gauss(settings["height_statistics"]["average_sea_height"], settings["height_statistics"]["sea_height_variance"]) if value else random.gauss(settings["height_statistics"]["average_land_height"], settings["height_statistics"]["land_height_variance"])

            for tile in getattr(self, "tiles", []):
                tile.is_oceanic_plate = value
                tile.height = self.height
                tile.type = "seafloor" if value else "continent"

    def add_tile(self, tile: Tile):
        tile.plate_color = self.color
        tile.color = self.color  # Assign the plate's color to the tile
        tile.plate_id = self.id 
        tile.is_oceanic_plate = self.oceanic
        tile.movement_vector = self.movement_vector
        tile.height = self.height

        if self.tiles and self.tiles[0].plate_center is not None:
            tile.plate_center = [self.tiles[0].plate_center[0], self.tiles[0].plate_center[1]]
        else:
            tile.plate_center = [tile.col, tile.row]

        self.tiles.append(tile)

    def remove_tile(self, tile: Tile):
        tile.plate_color = tile.color
        tile.plate_id = None
        tile.is_oceanic_plate = False
        tile.movement_vector = (0.0, 0.0)
        tile.height = None
        self.tiles.remove(tile)


def blend_colors(start_color: QColor, end_color: QColor, mix: float) -> QColor:
    mix = max(0.0, min(1.0, mix))
    return QColor.fromRgbF(
        start_color.redF() + (end_color.redF() - start_color.redF()) * mix,
        start_color.greenF() + (end_color.greenF() - start_color.greenF()) * mix,
        start_color.blueF() + (end_color.blueF() - start_color.blueF()) * mix,
        start_color.alphaF() + (end_color.alphaF() - start_color.alphaF()) * mix,
    )

class HexItem(QGraphicsPolygonItem):
    def __init__(self, tile: Tile, radius: float, on_click=None):
        super().__init__()
        self.tile = tile
        self.r = radius
        self.on_click = on_click
        self.base_color = QColor(tile.color)
        self.border_color = QColor(COLORS["hex_border"])
        self.highlight_color = QColor("#f1c40f")

        
        # Hex dimensions
        width = math.sqrt(3) * self.r
        height = 2 * self.r
        
        # 1. Calculate Pixel Y (Vertical spacing is 3/4 of height)
        cy = tile.row * (1.5 * self.r)
        
        # 2. Calculate Pixel X (Shift odd rows right by 0.5 * width)
        cx = tile.col * width
        if tile.row % 2 != 0:  # If it's an odd row, shift it
            cx += 0.5 * width
            
        # Generate the 6 vertices of a POINTY-TOPPED hexagon
        points = []
        for i in range(6):
            angle_rad = math.radians(60 * i + 30)  # +30 degrees rotates it to pointy-top
            x = cx + self.r * math.cos(angle_rad)
            y = cy + self.r * math.sin(angle_rad)
            points.append(QPointF(x, y))
            
        self.setPolygon(QPolygonF(points))
        
        # Styling
        self.setBrush(QBrush(self.base_color))
        self.setPen(QPen(self.border_color, 1, Qt.PenStyle.SolidLine))
        self.setAcceptHoverEvents(True)
        self.setToolTip(self._tooltip_text())

    def _tooltip_text(self) -> str:
        
        if self.tile.height is None:
            height = "N/A"
        else:
            height = f"{int(self.tile.height)}"
        
        plate_id = self.tile.plate_id if self.tile.plate_id is not None else "N/A"
        
        if self.tile.type == "":
            type = "N/A"
        else:
            type = self.tile.type.capitalize().replace("_", " ")

        return f"Height: {height}\nPlate: {plate_id}\nType: {type}"

    def hoverEnterEvent(self, event):
        # Refresh tooltip text when the mouse first enters the item
        self.setToolTip(self._tooltip_text())
        super().hoverEnterEvent(event)

    def update_color(self, color_mode: str):
        if color_mode == "plates":
            color = QColor(self.tile.plate_color)
        elif color_mode == "oceanic":
            color = QColor(COLORS["ocean_plate"] if self.tile.is_oceanic_plate else COLORS["continental_plate"])
        elif color_mode == "topology":
            color = QColor(height_to_color(self.tile.height)) if self.tile.height is not None else QColor("#101010")
        elif color_mode == "border":
            color = QColor(COLORS["border_tile"] if self.tile.is_border else COLORS["interior_tile"])
        elif color_mode == "direction":
            h = math.atan2(self.tile.movement_vector[1], self.tile.movement_vector[0]) / (2 * math.pi) + 0.5
            s = math.hypot(self.tile.movement_vector[0], self.tile.movement_vector[1])
            l = 0.5
            color = QColor.fromHslF(h, s, l)
        elif color_mode == "terrain_type":
            color = QColor(settings["colors"]["tile_types"][self.tile.type])
        else:
            color = QColor(self.tile.color)

        self.tile.color = color.name()
        self.base_color = color
        self.setBrush(QBrush(self.base_color))

    def set_highlighted(self, highlighted: bool):
        self.setPen(QPen(self.highlight_color if highlighted else self.border_color, 1, Qt.PenStyle.SolidLine))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.on_click is not None:
            self.on_click(self)
        super().mousePressEvent(event)

# 3. Infinite View (Same as before)
class InfiniteCanvasView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._panning = False
        self._pan_start = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._panning = True
            self._pan_start = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            delta = event.position().toPoint() - self._pan_start
            self._pan_start = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton and self._panning:
            self._panning = False
            self._pan_start = None
            self.unsetCursor()
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        factor = 1.1 if event.angleDelta().y() > 0 else 0.9
        old_pos = self.mapToScene(event.position().toPoint())
        self.scale(factor, factor)
        new_pos = self.mapToScene(event.position().toPoint())
        self.translate((old_pos - new_pos).x(), (old_pos - new_pos).y())

# 4. Window Setup
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Square-Indexed Hex Canvas")
        self.resize(1000, 750)

        self.scene = QGraphicsScene(self)
        
        self.tiles = []
        self.plates = []
        self.hex_items = []
        self.selected_hex = None
        self.color_mode = "plates"

        if GRID_IMPORT_PATH.exists():
            logging.info("Found existing grid data at %s", GRID_IMPORT_PATH)
            self._load_grid_from_file(GRID_IMPORT_PATH)
        else:
            logging.info("No existing grid data found at %s", GRID_IMPORT_PATH)
            self._generate_grid()

        self._build_scene()

        self.save_button = QToolButton(self)
        self.save_button.setText("Save")
        self.save_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.save_menu = QMenu(self)
        self.save_menu.addAction("save as image", self.save_grid_image)
        self.save_menu.addAction("save grid data", self.save_grid_data)
        self.save_button.setMenu(self.save_menu)
        self.save_button.setFixedSize(110, 32)
        self.save_button.raise_()

        self.color_mode_selector = QComboBox(self)
        self.color_mode_selector.addItem("Plates", "plates")
        self.color_mode_selector.addItem("Oceanic", "oceanic")
        self.color_mode_selector.addItem("Topology", "topology")
        self.color_mode_selector.addItem("Border", "border")
        self.color_mode_selector.addItem("Direction", "direction")
        self.color_mode_selector.addItem("Terrain Type", "terrain_type")
        self.color_mode_selector.setFixedSize(110, 32)
        self.color_mode_selector.currentIndexChanged.connect(self._on_color_mode_changed)
        self.color_mode_selector.raise_()

        self._position_save_button()
        self._position_controls()

    def _refresh_border_flags(self, border_width: int = 2):
        border_tiles = set()

        for tile in self.tiles:
            if any(neighbor.plate_id != tile.plate_id for neighbor in self.get_neighbors(tile.index)):
                border_tiles.add(tile.index)

        frontier = set(border_tiles)
        for _ in range(max(0, border_width - 1)):
            next_frontier = set()

            for tile_index in frontier:
                for neighbor in self.get_neighbors(tile_index):
                    if neighbor.index in border_tiles:
                        continue

                    border_tiles.add(neighbor.index)
                    next_frontier.add(neighbor.index)

            frontier = next_frontier

        for tile in self.tiles:
            tile.is_border = tile.index in border_tiles

    def _generate_grid(self):
        logging.info("Generating grid and plates...")

        for row in range(GRID_HEIGHT):
            for col in range(GRID_WIDTH):
                color = COLORS["ocean_shallow"]
                self.tiles.append(Tile(col, row, len(self.tiles), color))

        for i in range(PLATE_COUNT):
            hsl_color = [i * 360 / PLATE_COUNT, 90, 90]
            movement_angle = random.uniform(0, 360)
            movement_intensity = random.uniform(0, 1.0)
            plate = Plate(
                i,
                QColor.fromHsl(*hsl_color).name(),
                oceanic = False,
                movement_vector=(math.cos(math.radians(movement_angle)) * movement_intensity, math.sin(math.radians(movement_angle)) * movement_intensity)
            )

            plate.size = random.randint(50, 80)

            while plate.tiles == []:
                target = random.choice(self.tiles)
                if target.plate_id is None:
                    plate.add_tile(target)
                    target.plate_center = [target.col, target.row]

            self.plates.append(plate)

        logging.info("Grid and plates generation complete.")
        logging.info("Assigning tiles to plates...")
        
        def voronoi(title: str):
            origins = []

            # Ghost origin generation
            for plate in self.plates:
                origins.append({"id": plate.id, "coors": [plate.tiles[0].col, plate.tiles[0].row]})

                # Upper Left ghost
                origins.append({"id": plate.id, "coors": [plate.tiles[0].col - GRID_WIDTH, plate.tiles[0].row - GRID_HEIGHT]})

                # Upper ghost
                origins.append({"id": plate.id, "coors": [plate.tiles[0].col, plate.tiles[0].row - GRID_HEIGHT]})

                # Upper Right ghost
                origins.append({"id": plate.id, "coors": [plate.tiles[0].col + GRID_WIDTH, plate.tiles[0].row - GRID_HEIGHT]})

                # Left ghost
                origins.append({"id": plate.id, "coors": [plate.tiles[0].col - GRID_WIDTH, plate.tiles[0].row]})

                # Right ghost
                origins.append({"id": plate.id, "coors": [plate.tiles[0].col + GRID_WIDTH, plate.tiles[0].row]})

                # Lower Left ghost
                origins.append({"id": plate.id, "coors": [plate.tiles[0].col - GRID_WIDTH, plate.tiles[0].row + GRID_HEIGHT]})

                # Lower ghost
                origins.append({"id": plate.id, "coors": [plate.tiles[0].col, plate.tiles[0].row + GRID_HEIGHT]})

                # Lower Right ghost
                origins.append({"id": plate.id, "coors": [plate.tiles[0].col + GRID_WIDTH, plate.tiles[0].row + GRID_HEIGHT]})

            for tile in tqdm.tqdm(self.tiles, desc=title):
                if tile.plate_id is not None:
                    continue

                # Sample noise on a clean uniform grid, no hex correction inside
                raw_col = tile.col * settings["perlin"]["scale"]
                raw_row = tile.row * settings["perlin"]["scale"] * 0.866  # correct for hex row spacing

                warp_q = pnoise2(raw_col,          raw_row,          octaves=settings["perlin"]["octaves"]) * 20
                warp_r = pnoise2(raw_col + 214.21, raw_row + 213.12, octaves=settings["perlin"]["octaves"]) * 20

                # Apply hex stagger offset only here, to the final warped coordinates
                hex_offset = 0.5 * int(tile.row % 2)
                warped_col = tile.col + hex_offset + warp_q
                warped_row = tile.row              + warp_r

                closest_plate_id = min(
                    origins,
                    key=lambda origin: math.hypot(
                        origin["coors"][0] - warped_col,
                        origin["coors"][1] - warped_row
                    ) - self.plates[origin["id"]].size
                )["id"]         
                self.plates[closest_plate_id].add_tile(tile)
                tile.plate_center = [self.plates[closest_plate_id].tiles[0].col, self.plates[closest_plate_id].tiles[0].row]

        # Initial Voronoi assignment
        voronoi("Assigning tiles to plates")

        for i in range(settings["simulation"]["relaxation_iterations"]):
            ## Relaxation
            for plate in self.plates:
                delta_coordinates: list[int] = [0, 0]

                for tile in plate.tiles:
                    try:
                        delta_coordinates[0] += tile.col - tile.plate_center[0]
                        delta_coordinates[1] += tile.row - tile.plate_center[1]
                    except TypeError:
                        logging.warning("Tile %s has invalid plate center %s", tile.index, tile.plate_center)
                        continue

                delta_coordinates[0] //= len(plate.tiles)
                delta_coordinates[1] //= len(plate.tiles)

                center_plate = self.tiles[self.coords_to_index(plate.tiles[0].col + delta_coordinates[0], plate.tiles[0].row + delta_coordinates[1])]

                while len(plate.tiles) > 0:
                    plate.tiles[0].plate_center = None
                    plate.remove_tile(plate.tiles[0])
                
                plate.add_tile(center_plate)
                center_plate.plate_center = [center_plate.col, center_plate.row]

            voronoi(f"Relaxation iteration {i + 1}/{settings['simulation']['relaxation_iterations']}")

        logging.info("Tile assignment complete.")

        logging.info("Smoothing plate borders...")

        for tile in tqdm.tqdm(self.tiles, desc="Preparing tiles"):
            tile.movement_vector = self.plates[tile.plate_id].movement_vector

        self._refresh_border_flags(border_width=2)

        for i in range(settings["simulation"]["border_smoothing_iterations"]):
            count = 0

            for tile in tqdm.tqdm(self.tiles, desc="Smoothing plate borders (iteration {})".format(i + 1)):
                if not tile.is_border:
                    continue

                neighbor_plate_ids = [neighbor.plate_id for neighbor in self.get_neighbors(tile.index) if neighbor.plate_id is not None and neighbor.plate_id != tile.plate_id]

                if len(neighbor_plate_ids) == 0:
                    continue

                if len(neighbor_plate_ids) >= 5:
                    count += 1
                    self.plates[tile.plate_id].remove_tile(tile)
                    self.plates[random.choice(neighbor_plate_ids)].add_tile(tile)
            
            if count == 0:
                logging.info("No border tiles were changed in this iteration, stopping smoothing early.")
                break

        logging.info("Calculating plate oceanicity based on size...")

        oceanic_percentage = settings["simulation"]["oceanic_percentage"]
        target_tile_count = oceanic_percentage * len(self.tiles)

        best_error = len(self.tiles)
        best_attempt: int = 0
        tile_count = 0

        def get_bit(value: int, bit_index: int) -> bool:
            return ((value >> bit_index) & 1) != 0

        for i in range(2 ** PLATE_COUNT):
            if best_error == 0:
                break

            tile_count = 0

            for plate in self.plates:
                if get_bit(i, plate.id):
                    tile_count += len(plate.tiles)
            
            error = abs(tile_count - target_tile_count)
            
            if error < best_error:
                best_error = error
                best_attempt = i

        logging.info("Best oceanic plate configuration has %s tiles (error of %s from target %s tiles).", tile_count, best_error, target_tile_count)

        for plate in self.plates:
            if get_bit(best_attempt, plate.id):
                plate.oceanic = True
            else:
                plate.oceanic = False

        logging.info("Creating height map based on plate forces...")

        for plate in self.plates:
            angle = random.uniform(0, 360)
            intensity = random.uniform(0, 1)
            plate.movement_vector = (math.cos(math.radians(angle)) * intensity, math.sin(math.radians(angle)) * intensity)

        logging.info("Preparing tiles for force simulation...")

        for tile in tqdm.tqdm(self.tiles, desc="Preparing tiles"):
            tile.movement_vector = self.plates[tile.plate_id].movement_vector

        self._refresh_border_flags(border_width=2)

        logging.info("Simulating tectonic forces...")

        neighbor_offsets_map = [ # The distances are actuallly normalized to [-1, 1] because reasons
                            [-0.498, 0.867],    [0, 1],     [0.498, 0.867],

                    [-0.866, 0.5],  [-0.5, 0.866],  [0.5, 0.866],   [0.866, 0.5],

            [-1, 0],        [-1, 0],                                [1, 0],     [1, 0],

                    [-0.866, -0.5], [-0.5, -0.866], [0.5, -0.866],  [0.866, -0.5],

                            [-0.498, -0.867],   [0, -1],    [0.498, -0.867]
        ]

        for plate in self.plates:
            if plate.oceanic:
                for tile in plate.tiles:
                    contact_border = False

                    if not tile.is_border:
                        continue

                    factors = []

                    ## For each neighboring tile, save force delta and direction
                    for i, neighbor in enumerate(self.get_neighbors(tile.index, distance=2)):
                        if neighbor.plate_id == plate.id:
                            continue

                        if i in [4, 5, 8, 9, 12, 13]:
                            contact_border = True

                        relative_force = [
                            neighbor.movement_vector[0] - tile.movement_vector[0],
                            neighbor.movement_vector[1] - tile.movement_vector[1],
                        ]

                        ## Neighbors are returned left to right, top to bottom, so we can use the index to determine direction
                        delta = neighbor_offsets_map[i][0], -neighbor_offsets_map[i][1]

                        ## Rotate force by delta angle (delta is already normalized)

                        rotation_matrix = [
                            [delta[0], -delta[1]],
                            [delta[1], delta[0]]
                        ]

                        rotated_force = [
                            rotation_matrix[0][0] * relative_force[0] + rotation_matrix[0][1] * relative_force[1],
                            rotation_matrix[1][0] * relative_force[0] + rotation_matrix[1][1] * relative_force[1]
                        ]

                        factors.append({"id": neighbor.plate_id, "force": rotated_force})

                    # Needs to be classified into either Oceanic Rift, Accretionary Wedge, Trench or Volcanic Arc

                    # Only consider the plate with the most neighboring tiles
                    most_common_neighbor_id, _ = Counter(factor["id"] for factor in factors).most_common(1)[0]

                    final_factor = [sum(factor["force"][i] for factor in factors if factor["id"] == most_common_neighbor_id) for i in range(2)]

                    if self.plates[most_common_neighbor_id].oceanic:
                        if abs(final_factor[1]) > 3 * abs(final_factor[0]):
                            # Shearing between oceanic plates
                            tile.type = "transform_fault"
                        else:
                            if final_factor[0] > 0:
                                # Divergence between oceanic plates
                                tile.type = "rift"
                            else:
                                tile.type = "trench" if tile.plate_id < most_common_neighbor_id else "volcanic_arc"
                    else:
                        if final_factor[0] > 0:
                            tile.type = "rift"
                        else:
                            tile.type = "accretionary_wedge" if contact_border else "trench"
            else:
                for tile in plate.tiles:
                    contact_border = False

                    if not tile.is_border:
                        continue

                    factors = []

                    ## For each neighboring tile, save force delta and direction
                    for i, neighbor in enumerate(self.get_neighbors(tile.index, distance=2)):
                        if neighbor.plate_id == plate.id:
                            continue

                        if i in [4, 5, 8, 9, 12, 13]:
                            contact_border = True

                        relative_force = [
                            neighbor.movement_vector[0] - tile.movement_vector[0],
                            neighbor.movement_vector[1] - tile.movement_vector[1],
                        ]

                        ## Neighbors are returned left to right, top to bottom, so we can use the index to determine direction
                        delta = neighbor_offsets_map[i][0], -neighbor_offsets_map[i][1]

                        ## Rotate force by delta angle (delta is already normalized)

                        rotation_matrix = [
                            [delta[0], -delta[1]],
                            [delta[1], delta[0]]
                        ]

                        rotated_force = [
                            rotation_matrix[0][0] * relative_force[0] + rotation_matrix[0][1] * relative_force[1],
                            rotation_matrix[1][0] * relative_force[0] + rotation_matrix[1][1] * relative_force[1]
                        ]

                        factors.append({"id": neighbor.plate_id, "force": rotated_force})

                    # Needs to be classified into either Oceanic Rift, Accretionary Wedge, Trench or Volcanic Arc

                    # Only consider the plate with the most neighboring tiles
                    most_common_neighbor_id, _ = Counter(factor["id"] for factor in factors).most_common(1)[0]

                    final_factor = [sum(factor["force"][i] for factor in factors if factor["id"] == most_common_neighbor_id) for i in range(2)]

                    if not self.plates[most_common_neighbor_id].oceanic: # The other plate is continental
                        if abs(final_factor[1]) > 3 * abs(final_factor[0]):
                            # Shearing between continental plates
                            tile.type = "continental_transform_fault"
                        else:
                            if final_factor[0] > 0:
                                # Divergence between continental plates
                                tile.type = "proto_rift"
                            else:
                                tile.type = "mountain_range"
                    else:
                        if final_factor[0] > 0:
                            tile.type = "proto_rift"
                        else:
                            tile.type = "continental_volcanic_arc"

        for tile in tqdm.tqdm(self.tiles, desc="Smoothing terrain types"):
            neighbors = self.get_neighbors(tile.index, distance=1)

            if not any(neighbor.type == tile.type for neighbor in neighbors):
                # This tile type is isolated and HAS to change
                most_common_type, _ = Counter(neighbor.type for neighbor in neighbors if neighbor.plate_id == tile.plate_id).most_common(1)[0]

                tile.type = most_common_type

            neighbors = self.get_neighbors(tile.index, distance=1)

            # If most neighbors have the same type, assign that type to the tile as well (This is to smooth out isolated tiles with weird classifications)
            most_common_type, count = Counter(neighbor.type for neighbor in neighbors if neighbor.plate_id == tile.plate_id).most_common(1)[0]

            if count >= 4:
                tile.type = most_common_type
        
        # TODO: Try to move this into the main classification loop
        for tile in tqdm.tqdm(self.tiles, desc="Finalizing border classification"):
            close_neighbors = self.get_neighbors(tile.index, distance=1)

            # Need to assign Rift Basin, Rift Shoulders, Continental Back Arc Basins and Oceanic Back Arc Basins

            if tile.type == "proto_rift":
                # Can be either a rift (if making contact), or a rift basin (if not making contact)
                if any(neighbor.plate_id != tile.plate_id for neighbor in close_neighbors):
                    tile.type = "proto_rift"
                else:
                    tile.type = "proto_rift_basin"
            
            if tile.type == "rift":
                # Can be either a rift (if making contact), ora a rift basin (if not making contact)
                if any(neighbor.plate_id != tile.plate_id for neighbor in close_neighbors):
                    tile.type = "rift"
                else:
                    tile.type = "rift_basin"
        
        for tile in tqdm.tqdm(self.tiles, desc="Finalizing main land classification"):  
            close_neighbors = self.get_neighbors(tile.index, distance=1)
            large_neighbors = self.get_neighbors(tile.index, distance=2)

            if tile.type == "continent":
                if any(neighbor.type == "continental_volcanic_arc" for neighbor in large_neighbors):
                    tile.type = "continental_back_arc_basin"
                elif any(neighbor.type == "proto_rift_basin" for neighbor in close_neighbors):
                    tile.type = "proto_rift_shoulder"
            elif tile.type == "seafloor":
                if any(neighbor.type == "volcanic_arc" for neighbor in large_neighbors):
                    tile.type = "oceanic_back_arc_basin"
                elif any(neighbor.type == "rift_basin" for neighbor in close_neighbors):
                    tile.type = "rift_shoulder"

        logging.info("Tectonic force simulation complete.")

        logging.info("Calculating border heights...")

        def generate_height_delta(type: str) -> float:
            low, high = settings["height_statistics"]["ranges"][type]
            avg = (low + high) / 2
            variance = (high - low) / 6  # 99.7% of
            return max(min(random.gauss(avg, variance), high), low)

        for plate in self.plates:
            for tile in plate.tiles:
                if tile.type == "seafloor" or tile.type == "continent": 
                    continue

                height = plate.height

                height += generate_height_delta(tile.type)
                
                tile.height = height

        logging.info("Border height calculation complete.")

        logging.info("Running IDW interpolation for internal tiles...")

        for plate in self.plates:
            for current_tile in plate.tiles:
                if current_tile.type != "continent" and current_tile.type != "seafloor":
                    continue

                tiles = [tile for tile in plate.tiles if tile.type != current_tile.type]

                height_sum = sum(tile.height / (math.hypot(tile.col + 0.5 * int(tile.row % 2) - 0.5 * int(current_tile.row % 2) - current_tile.col, tile.row - current_tile.row) ** 2) for tile in tiles if tile.height is not None and (tile.col != current_tile.col or tile.row != current_tile.row))
                weight_sum = sum(1 / (math.hypot(tile.col + 0.5 * int(tile.row % 2) - 0.5 * int(current_tile.row % 2) - current_tile.col, tile.row - current_tile.row) ** 2) for tile in tiles if tile.height is not None and (tile.col != current_tile.col or tile.row != current_tile.row))

                current_tile.height = height_sum / weight_sum if weight_sum > 0 else 0


    def _load_grid_from_file(self, grid_path: Path):
        logging.info("Loading grid data from %s...", grid_path)

        try:
            with grid_path.open("r", encoding="utf-8") as file_handle:
                data = json.load(file_handle)
        except (OSError, json.JSONDecodeError) as exc:
            logging.warning("Failed to load grid data from %s (%s); regenerating instead.", grid_path, exc)
            self._generate_grid()
            return

        self.tiles = []
        for tile_data in sorted(data.get("tiles", []), key=lambda item: item["index"]):
            tile = Tile(tile_data["col"], tile_data["row"], tile_data["index"], tile_data.get("color", COLORS["ocean_shallow"]))
            tile.plate_color = tile_data.get("plate_color", tile.color)
            tile.plate_id = tile_data.get("plate_id")
            tile.is_oceanic_plate = tile_data.get("is_oceanic_plate", False)
            tile.is_border = tile_data.get("is_border", False)
            tile.height = tile_data.get("height", 0.0)
            tile.movement_vector = tuple(tile_data.get("movement_vector", (0.0, 0.0)))
            self.tiles.append(tile)

        self.plates = []
        plates_by_id = {}
        for plate_data in sorted(data.get("plates", []), key=lambda item: item["id"]):
            plate = Plate(plate_data["id"], plate_data.get("color", COLORS["continental_plate"]), oceanic=plate_data.get("oceanic", False))
            plates_by_id[plate.id] = plate
            self.plates.append(plate)

        for tile in self.tiles:
            plate = plates_by_id.get(tile.plate_id)
            if plate is not None:
                plate.tiles.append(tile)

        logging.info("Loaded %s tiles and %s plates from saved grid.", len(self.tiles), len(self.plates))

    def _build_scene(self):
        hex_radius = 30.0
        for tile in self.tiles:
            hex_item = HexItem(tile, hex_radius, self.select_hex)
            hex_item.update_color(self.color_mode)
            self.hex_items.append(hex_item)
            self.scene.addItem(hex_item)

        self.view = InfiniteCanvasView(self.scene)
        self.setCentralWidget(self.view)

    def get_neighbors(self, index: int, distance: int = 1) -> list[Tile]:
        if distance < 1:
            return []

        center_col, center_row = self.index_to_coords(index)

        def offset_to_cube(col: int, row: int) -> tuple[int, int, int]:
            cube_x = col - (row - (row & 1)) // 2
            cube_z = row
            cube_y = -cube_x - cube_z
            return cube_x, cube_y, cube_z

        def cube_to_offset(cube_x: int, cube_z: int) -> tuple[int, int]:
            row = cube_z
            col = cube_x + (row - (row & 1)) // 2
            return col % GRID_WIDTH, row % GRID_HEIGHT

        def wrapped_delta(value: int, origin: int, size: int) -> int:
            delta = value - origin
            half_size = size // 2
            if delta > half_size:
                delta -= size
            elif delta < -half_size:
                delta += size
            return delta

        def neighbor_sort_key(tile: Tile) -> tuple[float, float]:
            row_delta = wrapped_delta(tile.row, center_row, GRID_HEIGHT)
            col_delta = wrapped_delta(tile.col, center_col, GRID_WIDTH)

            x_offset = col_delta + (((center_row + row_delta) % 2) - (center_row % 2)) * 0.5
            y_offset = row_delta

            return (y_offset, x_offset)

        def wrapped_neighbor_indices(tile_index: int) -> set[int]:
            tile_col, tile_row = self.index_to_coords(tile_index)
            cube_x, _, cube_z = offset_to_cube(tile_col, tile_row)

            neighbor_indices = set()
            for delta_x, delta_y, delta_z in (
                (1, -1, 0),
                (1, 0, -1),
                (0, 1, -1),
                (-1, 1, 0),
                (-1, 0, 1),
                (0, -1, 1),
            ):
                candidate_col, candidate_row = cube_to_offset(cube_x + delta_x, cube_z + delta_z)
                neighbor_indices.add(self.coords_to_index(candidate_col, candidate_row))

            return neighbor_indices

        frontier = {index}
        seen = {index}

        for _ in range(distance):
            next_frontier = set()

            for tile_index in frontier:
                next_frontier.update(wrapped_neighbor_indices(tile_index))

            next_frontier.difference_update(seen)
            seen.update(next_frontier)
            frontier = next_frontier

        seen.discard(index)

        neighbors = [self.tiles[tile_index] for tile_index in seen]
        neighbors.sort(key=neighbor_sort_key)
        return neighbors

    def index_to_coords(self, index: int) -> list[int]:
        col = index % GRID_WIDTH
        row = index // GRID_WIDTH
        return [col, row]
    
    def coords_to_index(self, col: int, row: int) -> int:
        return (row % GRID_HEIGHT) * GRID_WIDTH + (col % GRID_WIDTH)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_save_button()
        self._position_controls()

    def _position_save_button(self):
        margin = 12
        x = self.width() - self.save_button.width() - margin
        y = margin
        self.save_button.move(x, y)

    def _position_controls(self):
        margin = 12
        x = self.width() - self.save_button.width() - self.color_mode_selector.width() - (margin * 2)
        y = margin
        self.color_mode_selector.move(x, y)

    def _on_color_mode_changed(self, *_):
        self.color_mode = self.color_mode_selector.currentData()
        for hex_item in self.hex_items:
            hex_item.update_color(self.color_mode)

    def save_grid_image(self):
        source_rect = self.scene.itemsBoundingRect().adjusted(-20, -20, 20, 20)
        image = QImage(
            max(1, int(math.ceil(source_rect.width()))),
            max(1, int(math.ceil(source_rect.height()))),
            QImage.Format.Format_ARGB32,
        )
        image.fill(QColor("white"))

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.scene.render(painter, target=QRectF(image.rect()), source=source_rect)
        painter.end()

        output_path = Path(__file__).resolve().parent / f"grid_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        image.save(str(output_path))

    def save_grid_data(self):
        output_path = Path(__file__).resolve().parent / f"grid_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        data = {
            "grid_size": {
                "width": GRID_WIDTH,
                "height": GRID_HEIGHT,
            },
            "plates": [
                {
                    "id": plate.id,
                    "color": plate.color,
                    "oceanic": plate.oceanic,
                    "tile_indices": [tile.index for tile in plate.tiles],
                }
                for plate in self.plates
            ],
            "tiles": [
                {
                    "index": tile.index,
                    "col": tile.col,
                    "row": tile.row,
                    "color": tile.color,
                    "plate_color": tile.plate_color,
                    "plate_id": tile.plate_id,
                    "is_oceanic_plate": tile.is_oceanic_plate,
                    "is_border": tile.is_border,
                    "movement_vector": tile.movement_vector,
                    "height": tile.height,
                }
                for tile in self.tiles
            ],
        }

        with output_path.open("w", encoding="utf-8") as file_handle:
            json.dump(data, file_handle, indent=2)

    def select_hex(self, hex_item: HexItem):
        if self.selected_hex is hex_item:
            return

        if self.selected_hex is not None:
            self.selected_hex.set_highlighted(False)

        self.selected_hex = hex_item
        self.selected_hex.set_highlighted(True)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())