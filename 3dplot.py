import pandas as pd
import plotly.graph_objects as go
import numpy as np
from scipy.interpolate import griddata

df = pd.read_csv("map.csv")

# build a regular grid covering the same extent
xi = np.linspace(df["x"].min(), df["x"].max(), 300)
yi = np.linspace(df["y"].min(), df["y"].max(), 300)
xi_grid, yi_grid = np.meshgrid(xi, yi)

# interpolate hex points onto regular grid
zi_grid = griddata(
    points=(df["x"].values, df["y"].values),
    values=df["height"].values,
    xi=(xi_grid, yi_grid),
    method="cubic"  # or "cubic" for smoother result
)

h_min = df["height"].min()
h_max = df["height"].max()
sea_frac = abs(h_min) / (h_max - h_min)

def lerp(a, b, t):
    return a + (b - a) * t

def sea_stop(depth_frac):
    # depth_frac: 0 = deepest, 1 = sea level
    return sea_frac * depth_frac

def land_stop(height_frac):
    # height_frac: 0 = sea level, 1 = highest peak
    return sea_frac + (1 - sea_frac) * height_frac

colorscale = [
    # ocean — deep to shallow
    [sea_stop(0.0),  "rgb(0,   10,  70)"],   # hadal / deep ocean
    [sea_stop(0.3),  "rgb(0,   30, 120)"],   # abyssal
    [sea_stop(0.6),  "rgb(0,   80, 160)"],   # mid ocean
    [sea_stop(0.85), "rgb(30, 140, 200)"],   # shelf
    [sea_stop(1.0),  "rgb(80, 190, 220)"],   # coastal shallow

    # land — low to high
    [land_stop(0.0),  "rgb(80, 190, 220)"],  # dummy repeat to avoid gap
    [land_stop(0.02), "rgb(200, 230, 170)"], # coastal lowland
    [land_stop(0.12), "rgb(140, 195, 100)"], # low land / plains
    [land_stop(0.28), "rgb(180, 185,  90)"], # upland
    [land_stop(0.45), "rgb(180, 145,  60)"], # high plateau
    [land_stop(0.65), "rgb(160, 110,  50)"], # mountains
    [land_stop(0.82), "rgb(130,  80,  40)"], # high mountains
    [land_stop(0.93), "rgb(200, 185, 170)"], # alpine / rock
    [land_stop(1.0),  "rgb(255, 255, 255)"], # snow / peaks
]

fig = go.Figure(data=[go.Surface(
    x=xi,
    y=yi,
    z=zi_grid,
    colorscale=colorscale,
    colorbar=dict(title="Height (m)")
)])

x_range = df["x"].max() - df["x"].min()
y_range = df["y"].max() - df["y"].min()
scale = max(x_range, y_range)

fig.update_layout(scene=dict(
    aspectmode="manual",
    aspectratio=dict(
        x=x_range / scale,
        y=y_range / scale,
        z=0.05
    )  # tune z to taste
))

water_x = np.linspace(df["x"].min(), df["x"].max(), 2)
water_y = np.linspace(df["y"].min(), df["y"].max(), 2)
water_x, water_y = np.meshgrid(water_x, water_y)
water_z = np.zeros_like(water_x)

fig.add_trace(go.Surface(
    x=water_x,
    y=water_y,
    z=water_z,
    colorscale=[[0, "rgba(0, 80, 180, 0.4)"], [1, "rgba(0, 80, 180, 0.4)"]],
    showscale=False,
    name="Sea level"
))

fig.show()