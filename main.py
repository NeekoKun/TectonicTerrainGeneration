import logging
import json
import random
import tqdm
import time
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

GRID_SIZE = GRID_WIDTH, GRID_HEIGHT = 100, 100

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
GRID_IMPORT_PATH = Path(__file__).resolve().parent / "grid_20260603_205338.json"

logging.basicConfig(level=logging.INFO, format='[%(asctime)s - %(levelname)s]: %(message)s')

class Tile:
    def __init__(self, col: int, row: int, index: int, color_str: str):
        self.col = col      # X-axis on a square grid
        self.row = row      # Y-axis on a square grid
        self.index = index
        self.color = color_str
        self.plate_color = color_str
        self.plate_id: int | None = None
        self.is_oceanic_plate = False
        self.is_border = True
        self.height: float = 0.0
        self.movement_vector: tuple[float, float] = (0.0, 0.0)

class Plate:
    def __init__(self, id: int, color_str: str, oceanic=False, movement_vector=(0.0, 0.0)):
        self.id = id
        self.color = color_str
        self.oceanic = oceanic
        self.movement_vector: tuple[float, float] = movement_vector
        self.tiles = []
    
    def add_tile(self, tile: Tile):
        tile.plate_color = self.color
        tile.color = self.color  # Assign the plate's color to the tile
        tile.plate_id = self.id 
        tile.is_oceanic_plate = self.oceanic
        tile.movement_vector = self.movement_vector
        self.tiles.append(tile)


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
        self.setToolTip(f"Grid: ({tile.col}, {tile.row})")

    def update_color(self, color_mode: str):
        if color_mode == "plates":
            color = QColor(self.tile.plate_color)
        elif color_mode == "oceanic":
            color = QColor(COLORS["ocean_plate"] if self.tile.is_oceanic_plate else COLORS["continental_plate"])
        elif color_mode == "height":
            color = blend_colors(QColor(COLORS["plains"]), QColor(COLORS["mountains"]), self.tile.height)
        elif color_mode == "border":
            color = QColor(COLORS["border_tile"] if self.tile.is_border else COLORS["interior_tile"])
        elif color_mode == "direction":
            h = math.atan2(self.tile.movement_vector[1], self.tile.movement_vector[0]) / (2 * math.pi) + 0.5
            s = math.hypot(self.tile.movement_vector[0], self.tile.movement_vector[1])
            l = 0.5
            color = QColor.fromHslF(h, s, l)
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
        self.color_mode_selector.addItem("Height", "height")
        self.color_mode_selector.addItem("Border", "border")
        self.color_mode_selector.addItem("Direction", "direction")
        self.color_mode_selector.setFixedSize(110, 32)
        self.color_mode_selector.currentIndexChanged.connect(self._on_color_mode_changed)
        self.color_mode_selector.raise_()

        self._position_save_button()
        self._position_controls()

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
                oceanic = random.choices([True, False], weights=[7, 3])[0],
                movement_vector=(math.cos(math.radians(movement_angle)) * movement_intensity, math.sin(math.radians(movement_angle)) * movement_intensity)
            )

            while plate.tiles == []:
                target = random.choice(self.tiles)
                if target.plate_id is None:
                    plate.add_tile(target)

            self.plates.append(plate)

        logging.info("Grid and plates generation complete.")
        logging.info("Assigning tiles to plates...")

        perlin_noise_map = [[pnoise2(i * 2 * math.cos(math.radians(30)) + 0.5 * (j % 2), j * (1 + math.sin(math.radians(30))), octaves=10) for i in range(GRID_WIDTH)] for j in range(GRID_HEIGHT)]

        while any(tile.plate_id is None for tile in self.tiles):
            current_plate = random.choice(self.plates)

            neighboring_tiles = set()

            for tile in current_plate.tiles:
                if not tile.is_border:
                    continue

                neighbors = self.get_neighbors(tile.index)

                if all(neighbor.plate_id is tile.plate_id for neighbor in neighbors):
                    tile.is_border = False
                    continue

                neighbors = [neighbor for neighbor in neighbors if neighbor.plate_id is None]
                neighboring_tiles.update(neighbors)

            if neighboring_tiles:
                next_tile = min(neighboring_tiles, key=lambda t: perlin_noise_map[t.row][t.col])
                current_plate.add_tile(next_tile)

        logging.info("Tile assignment complete.")
        logging.info("Smoothing borders...")

        for _ in tqdm.tqdm(range(len(self.tiles) // 2), desc="Smoothing borders"):
            for tile in self.tiles:
                if not tile.is_border:
                    continue

                neighbors = self.get_neighbors(tile.index)

                if sum(1 for neighbor in neighbors if neighbor.plate_id != tile.plate_id) >= 5:
                    candidate_plate_ids = [neighbor.plate_id for neighbor in neighbors if neighbor.plate_id is not None and neighbor.plate_id != tile.plate_id]
                    if candidate_plate_ids:
                        self.plates[random.choice(candidate_plate_ids)].add_tile(tile)

        logging.info("Border smoothing complete.")
        logging.info("Creating height map based on plate forces...")

        for plate in self.plates:
            angle = random.uniform(0, 360)
            intensity = random.uniform(0, 1)
            plate.movement_vector = (math.cos(math.radians(angle)) * intensity, math.sin(math.radians(angle)) * intensity)

        logging.info("Preparing tiles for force simulation...")

        for tile in tqdm.tqdm(self.tiles, desc="Preparing tiles"):
            tile.movement_vector = self.plates[tile.plate_id].movement_vector

            if any(neighbor.plate_id != tile.plate_id for neighbor in self.get_neighbors(tile.index, distance=2)):
                tile.is_border = True
            else:
                tile.is_border = False

        logging.info("Simulating tectonic forces...")

        for tile in self.tiles:
            if not tile.is_border:
                continue

            self.get_neighbors(tile.index, distance=2)

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

        def direct_neighbors(tile_index: int) -> list[Tile]:
            direct = []
            col, row = self.index_to_coords(tile_index)

            # Right
            direct.append(self.tiles[self.coords_to_index((col + 1) % GRID_WIDTH, row)])

            # Left
            direct.append(self.tiles[self.coords_to_index((col - 1) % GRID_WIDTH, row)])

            # Upper Right
            if row % 2 == 0:  # Even row
                direct.append(self.tiles[self.coords_to_index(col, (row - 1) % GRID_HEIGHT)])
            else:  # Odd row
                direct.append(self.tiles[self.coords_to_index((col + 1) % GRID_WIDTH, (row - 1) % GRID_HEIGHT)])

            # Upper Left
            if row % 2 == 0:  # Even row
                direct.append(self.tiles[self.coords_to_index((col - 1) % GRID_WIDTH, (row - 1) % GRID_HEIGHT)])
            else:  # Odd row
                direct.append(self.tiles[self.coords_to_index(col, (row - 1) % GRID_HEIGHT)])

            # Lower Right
            if row % 2 == 0:  # Even row
                direct.append(self.tiles[self.coords_to_index(col, (row + 1) % GRID_HEIGHT)])
            else:  # Odd row
                direct.append(self.tiles[self.coords_to_index((col + 1) % GRID_WIDTH, (row + 1) % GRID_HEIGHT)])

            # Lower Left
            if row % 2 == 0:  # Even row
                direct.append(self.tiles[self.coords_to_index((col - 1) % GRID_WIDTH, (row + 1) % GRID_HEIGHT)])
            else:  # Odd row
                direct.append(self.tiles[self.coords_to_index(col, (row + 1) % GRID_HEIGHT)])

            return direct

        neighbors = []
        seen = {index}
        frontier = [index]

        for _ in range(distance):
            next_frontier = []
            for tile_index in frontier:
                for neighbor in direct_neighbors(tile_index):
                    if neighbor.index in seen:
                        continue
                    seen.add(neighbor.index)
                    neighbors.append(neighbor)
                    next_frontier.append(neighbor.index)
            frontier = next_frontier

        return neighbors

    def index_to_coords(self, index: int) -> list[int]:
        col = index % GRID_WIDTH
        row = index // GRID_WIDTH
        return [col, row]
    
    def coords_to_index(self, col: int, row: int) -> int:
        return row * GRID_WIDTH + col

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