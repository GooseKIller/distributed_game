from __future__ import annotations

import argparse
import sys
import time
import tkinter as tk
from fractions import Fraction
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple


def _resource_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[1]


RESOURCE_ROOT = _resource_root()
if __package__ in (None, "") and not getattr(sys, "frozen", False):
    sys.path.insert(0, str(RESOURCE_ROOT))

try:
    from game.game import GameWorld
    from server.Transport import Role
    from server.node import Node
except ImportError:
    from distributed_game.game.game import GameWorld
    from distributed_game.server.Transport import Role
    from distributed_game.server.node import Node


def trim_transparency(image: tk.PhotoImage) -> tk.PhotoImage:
    width = image.width()
    height = image.height()
    left = 0
    while left < width and all(image.transparency_get(left, y) for y in range(height)):
        left += 1
    if left >= width:
        return image

    right = width - 1
    while right >= left and all(image.transparency_get(right, y) for y in range(height)):
        right -= 1

    top = 0
    while top < height and all(image.transparency_get(x, top) for x in range(width)):
        top += 1

    bottom = height - 1
    while bottom >= top and all(image.transparency_get(x, bottom) for x in range(width)):
        bottom -= 1

    trimmed = tk.PhotoImage()
    trimmed.tk.call(str(trimmed), "copy", str(image), "-from", left, top, right + 1, bottom + 1)
    return trimmed


def scale_image(image: tk.PhotoImage, *, width: Optional[int] = None, height: Optional[int] = None) -> tk.PhotoImage:
    original_w = image.width()
    original_h = image.height()
    if not width and not height:
        return image

    if width and height:
        scale = min(float(width) / original_w, float(height) / original_h)
    elif width:
        scale = float(width) / original_w
    else:
        scale = float(height) / original_h

    if scale <= 0:
        return image

    ratio = Fraction(scale).limit_denominator(32)
    if ratio.numerator <= 0:
        return image
    return image.zoom(ratio.numerator, ratio.numerator).subsample(ratio.denominator, ratio.denominator)


def flip_image_horizontal(image: tk.PhotoImage) -> tk.PhotoImage:
    flipped = tk.PhotoImage()
    flipped.tk.call(str(flipped), "copy", str(image), "-subsample", -1, 1)
    return flipped


def load_sprite(filename: str, *, width: Optional[int] = None, height: Optional[int] = None,
                trim: bool = True) -> Optional[tk.PhotoImage]:
    path = RESOURCE_ROOT / "assets" / filename
    if not path.exists():
        return None
    image = tk.PhotoImage(file=str(path))
    if trim:
        image = trim_transparency(image)
    if width or height:
        image = scale_image(image, width=width, height=height)
    return image


def is_port_in_use_error(error: OSError) -> bool:
    if getattr(error, "winerror", None) == 10048:
        return True
    if getattr(error, "errno", None) in {48, 98, 10048}:
        return True
    return "only one usage" in str(error).lower() or "address already in use" in str(error).lower()


class LauncherWindow:
    VIEW_W = 1100
    VIEW_H = 720

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Distributed Platformer")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.canvas = tk.Canvas(
            self.root,
            width=self.VIEW_W,
            height=self.VIEW_H,
            highlightthickness=0,
            bg="#151a38",
        )
        self.canvas.pack(fill="both", expand=True)

        self.selected_mode: Optional[str] = None
        self.result: Optional[Tuple[Node, str]] = None
        self.background = load_sprite("background.png", width=1440, height=720, trim=False)

        self.bind_ip_var = tk.StringVar()
        self.bind_port_var = tk.StringVar()
        self.server_ip_var = tk.StringVar()
        self.server_port_var = tk.StringVar()
        self.warning_var = tk.StringVar(value="Choose Host or Launch to start.")
        self.header_var = tk.StringVar(value="Choose how you want to start.")
        self.submit_var = tk.StringVar(value="Select a mode first")

        self._build_scene()

    def run(self) -> Optional[Tuple[Node, str]]:
        self.root.mainloop()
        return self.result

    def _build_scene(self):
        if self.background is not None:
            self.canvas.create_image(self.VIEW_W / 2, self.VIEW_H / 2, image=self.background, anchor="center")
        self.canvas.create_rectangle(0, 0, self.VIEW_W, self.VIEW_H, fill="#000000", stipple="gray50", outline="")
        self.canvas.create_rectangle(0, 0, self.VIEW_W, self.VIEW_H, fill="#000000", stipple="gray50", outline="")

        self.canvas.create_text(
            self.VIEW_W / 2,
            92,
            text="Distributed Platformer",
            fill="#f8fafc",
            font=("Segoe UI", 28, "bold"),
        )
        self.canvas.create_text(
            self.VIEW_W / 2,
            132,
            text="Co-op cat climb with sprite launcher",
            fill="#cbd5e1",
            font=("Segoe UI", 12),
        )

        button_frame = tk.Frame(self.root, bg="#0f172a")
        self.host_button = tk.Button(
            button_frame,
            text="Host",
            width=14,
            font=("Segoe UI", 12, "bold"),
            bg="#f8fafc",
            fg="#0f172a",
            activebackground="#dbeafe",
            command=lambda: self._select_mode("host"),
        )
        self.launch_button = tk.Button(
            button_frame,
            text="Launch",
            width=14,
            font=("Segoe UI", 12, "bold"),
            bg="#f8fafc",
            fg="#0f172a",
            activebackground="#dbeafe",
            command=lambda: self._select_mode("launch"),
        )
        self.host_button.pack(side="left", padx=10, pady=8)
        self.launch_button.pack(side="left", padx=10, pady=8)
        self.canvas.create_window(self.VIEW_W / 2, 190, window=button_frame)

        self.card = tk.Frame(self.root, bg="#0f172a", padx=22, pady=18, bd=0, highlightthickness=2, highlightbackground="#dbeafe")
        self.canvas.create_window(self.VIEW_W / 2, 430, window=self.card, width=540, height=330)

        tk.Label(
            self.card,
            textvariable=self.header_var,
            bg="#0f172a",
            fg="#f8fafc",
            font=("Segoe UI", 15, "bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        tk.Label(self.card, text="Local IP", bg="#0f172a", fg="#cbd5e1", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=(16, 4))
        self.bind_ip_entry = tk.Entry(self.card, textvariable=self.bind_ip_var, font=("Segoe UI", 11), width=28)
        self.bind_ip_entry.grid(row=2, column=0, sticky="ew", padx=(0, 12))

        tk.Label(self.card, text="Local Port", bg="#0f172a", fg="#cbd5e1", font=("Segoe UI", 10, "bold")).grid(row=1, column=1, sticky="w", pady=(16, 4))
        self.bind_port_entry = tk.Entry(self.card, textvariable=self.bind_port_var, font=("Segoe UI", 11), width=18)
        self.bind_port_entry.grid(row=2, column=1, sticky="ew")

        self.server_ip_label = tk.Label(self.card, text="Server IP", bg="#0f172a", fg="#cbd5e1", font=("Segoe UI", 10, "bold"))
        self.server_ip_entry = tk.Entry(self.card, textvariable=self.server_ip_var, font=("Segoe UI", 11), width=28)
        self.server_port_label = tk.Label(self.card, text="Server Port", bg="#0f172a", fg="#cbd5e1", font=("Segoe UI", 10, "bold"))
        self.server_port_entry = tk.Entry(self.card, textvariable=self.server_port_var, font=("Segoe UI", 11), width=18)

        self.server_ip_label.grid(row=3, column=0, sticky="w", pady=(14, 4))
        self.server_ip_entry.grid(row=4, column=0, sticky="ew", padx=(0, 12))
        self.server_port_label.grid(row=3, column=1, sticky="w", pady=(14, 4))
        self.server_port_entry.grid(row=4, column=1, sticky="ew")

        self.warning_label = tk.Label(
            self.card,
            textvariable=self.warning_var,
            bg="#0f172a",
            fg="#fca5a5",
            font=("Segoe UI", 10, "bold"),
            justify="left",
            wraplength=480,
            anchor="w",
        )
        self.warning_label.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(18, 14))

        self.submit_button = tk.Button(
            self.card,
            textvariable=self.submit_var,
            font=("Segoe UI", 11, "bold"),
            bg="#60a5fa",
            fg="#0f172a",
            activebackground="#93c5fd",
            command=self._attempt_launch,
            width=18,
        )
        self.submit_button.grid(row=6, column=0, columnspan=2)

        self.card.grid_columnconfigure(0, weight=1)
        self.card.grid_columnconfigure(1, weight=1)
        self._apply_mode_visibility()

    def _select_mode(self, mode: str):
        self.selected_mode = mode
        if mode == "host":
            self.header_var.set("Host a game on this machine")
            self.submit_var.set("Start Host")
            self.bind_ip_var.set("127.0.0.1")
            self.bind_port_var.set("5005")
            self.server_ip_var.set("")
            self.server_port_var.set("")
            self.warning_var.set("Host mode keeps the builder on this machine.")
            self.host_button.configure(bg="#93c5fd")
            self.launch_button.configure(bg="#f8fafc")
        else:
            self.header_var.set("Launch as a joining player")
            self.submit_var.set("Launch Player")
            self.bind_ip_var.set("127.0.0.1")
            self.bind_port_var.set("5006")
            self.server_ip_var.set("127.0.0.1")
            self.server_port_var.set("5005")
            self.warning_var.set("If the local join port is busy, choose another port and try again.")
            self.host_button.configure(bg="#f8fafc")
            self.launch_button.configure(bg="#93c5fd")
        self._apply_mode_visibility()

    def _apply_mode_visibility(self):
        show_server = self.selected_mode == "launch"
        state = "normal" if show_server else "disabled"
        fg = "#cbd5e1" if show_server else "#64748b"
        self.server_ip_label.configure(fg=fg)
        self.server_port_label.configure(fg=fg)
        self.server_ip_entry.configure(state=state)
        self.server_port_entry.configure(state=state)

    def _attempt_launch(self):
        if not self.selected_mode:
            self.warning_var.set("Pick Host or Launch first.")
            return

        bind_ip = self.bind_ip_var.get().strip() or "127.0.0.1"
        try:
            bind_port = self._parse_port(self.bind_port_var.get().strip(), "Local port")
        except ValueError as error:
            self.warning_var.set(str(error))
            return

        try:
            if self.selected_mode == "host":
                node = Node(ip=bind_ip, port=bind_port, role=Role.SERVER)
                title = f"Distributed Platformer - host builder :{bind_port}"
            else:
                server_ip = self.server_ip_var.get().strip() or "127.0.0.1"
                server_port = self._parse_port(self.server_port_var.get().strip(), "Server port")
                node = Node(ip=bind_ip, port=bind_port, role=Role.CLIENT, server_info=(server_ip, server_port))
                title = f"Distributed Platformer - runner :{bind_port} -> {server_ip}:{server_port}"
        except OSError as error:
            if is_port_in_use_error(error):
                if self.selected_mode == "launch":
                    self.warning_var.set(
                        f"Local join port {bind_port} is already in use. Pick a different local port and try again."
                    )
                else:
                    self.warning_var.set(
                        f"Host port {bind_port} is already in use. Pick another port and try again."
                    )
            else:
                self.warning_var.set(f"Could not start networking: {error}")
            return
        except ValueError as error:
            self.warning_var.set(str(error))
            return

        self.result = (node, title)
        self.root.destroy()

    @staticmethod
    def _parse_port(raw: str, label: str) -> int:
        if not raw:
            raise ValueError(f"{label} is required.")
        value = int(raw)
        if not 1 <= value <= 65535:
            raise ValueError(f"{label} must be between 1 and 65535.")
        return value

    def _close(self):
        self.result = None
        self.root.destroy()


class PlatformerApp:
    VIEW_W = 1100
    VIEW_H = 720
    SEND_MS = 50
    FRAME_MS = 16

    PLAYER_COLORS = [
        "#3b82f6",
        "#22c55e",
        "#f59e0b",
        "#ec4899",
        "#14b8a6",
        "#f97316",
        "#84cc16",
        "#06b6d4",
    ]

    CAT_FILES = [
        "dnp_p1.png",
        "dnp_p2.png",
        "dnp_p3.png",
        "dnp_p4.png",
        "dnp_p5.png",
    ]

    KIND_LABELS = {
        GameWorld.FLAT: "Flat",
        GameWorld.JUMP: "Jump pad",
        GameWorld.STAIR: "Stairs",
    }

    def __init__(self, node: Node, title: str):
        self.node = node
        self.local_key = node.endpoint.key()
        self.root = tk.Tk()
        self.root.title(title)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.canvas = tk.Canvas(
            self.root,
            width=self.VIEW_W,
            height=self.VIEW_H,
            bg="#dbeafe",
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self.keys = set()
        self.jump_buffer = False
        self.respawn_buffer = False
        self.selected_kind = GameWorld.FLAT
        self.camera_x = 0.0
        self.mouse_screen = (self.VIEW_W // 2, self.VIEW_H // 2)
        self.last_frame = time.perf_counter()
        self.closed = False
        self.sprites = self._load_sprites()

        self._bind_events()

    def run(self):
        self.node.start()
        self.root.after(self.SEND_MS, self._send_local_input)
        self.root.after(self.FRAME_MS, self._frame)
        self.root.mainloop()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.node.stop()
        self.root.destroy()

    def _load_sprites(self) -> dict:
        flat_platform = load_sprite("dnp_cloud.png", width=176, height=94)
        stair_platform = load_sprite("dnp_cloud.png", width=96, height=52)
        jump_platform = load_sprite("dnp_balls.png", width=132, height=134)
        spike = load_sprite("dnp_danger.png", width=76, height=46)
        reward = load_sprite("dnp_reward.png", width=108, height=70)
        enemy = load_sprite("dnp_enemy.png", width=82, height=74)
        cats = [load_sprite(filename, width=96, height=62) for filename in self.CAT_FILES]
        return {
            "background": load_sprite("background.png", width=1440, height=720, trim=False),
            "flat_platform": flat_platform,
            "stair_platform": stair_platform,
            "jump_platform": jump_platform,
            "spike": spike,
            "reward": reward,
            "enemy": {
                "right": enemy,
                "left": flip_image_horizontal(enemy) if enemy is not None else None,
            },
            "cats": [
                {
                    "right": cat,
                    "left": flip_image_horizontal(cat) if cat is not None else None,
                }
                for cat in cats
            ],
        }

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------
    def _bind_events(self):
        self.root.bind("<KeyPress>", self._on_key_press)
        self.root.bind("<KeyRelease>", self._on_key_release)
        self.canvas.bind("<Motion>", self._on_mouse_motion)
        self.canvas.bind("<Button-1>", self._on_left_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.focus_set()

    def _on_key_press(self, event):
        key = event.keysym.lower()
        self.keys.add(key)
        if key in {"space", "w", "up"}:
            self.jump_buffer = True
        elif key == "r":
            self.respawn_buffer = True
        elif key == "1":
            self.selected_kind = GameWorld.FLAT
        elif key == "2":
            self.selected_kind = GameWorld.JUMP
        elif key == "3":
            self.selected_kind = GameWorld.STAIR
        elif key == "q":
            self._cycle_kind(-1)
        elif key == "e":
            self._cycle_kind(1)

    def _on_key_release(self, event):
        self.keys.discard(event.keysym.lower())

    def _on_mouse_motion(self, event):
        self.mouse_screen = (event.x, event.y)

    def _on_left_click(self, event):
        if not self._is_builder():
            return
        if self._platform_used_up(self.node.world.snapshot(), self.selected_kind):
            return
        world_x, world_y = self._screen_to_world(event.x, event.y)
        self.node.send_input(
            {
                "place_platform": {
                    "x": world_x,
                    "y": world_y,
                    "kind": self.selected_kind,
                }
            }
        )

    def _on_right_click(self, event):
        if not self._is_builder():
            return
        world_x, world_y = self._screen_to_world(event.x, event.y)
        self.node.send_input({"remove_platform": {"x": world_x, "y": world_y}})

    def _on_mouse_wheel(self, event):
        if not self._is_builder():
            return
        self.camera_x -= event.delta * 0.45
        self.camera_x = self._clamp_camera(self.camera_x)

    def _cycle_kind(self, direction: int):
        kinds = [GameWorld.FLAT, GameWorld.JUMP, GameWorld.STAIR]
        index = kinds.index(self.selected_kind)
        self.selected_kind = kinds[(index + direction) % len(kinds)]

    def _send_local_input(self):
        if self.closed:
            return

        if not self._is_builder():
            move_x = 0.0
            if self.keys.intersection({"a", "left"}):
                move_x -= 1.0
            if self.keys.intersection({"d", "right"}):
                move_x += 1.0
            self.node.send_input(
                {
                    "move_x": move_x,
                    "jump": self.jump_buffer,
                    "respawn": self.respawn_buffer,
                }
            )
            self.jump_buffer = False
            self.respawn_buffer = False

        self.root.after(self.SEND_MS, self._send_local_input)

    def _is_builder(self) -> bool:
        return self.node.role == Role.SERVER

    # ------------------------------------------------------------------
    # Frame and camera
    # ------------------------------------------------------------------
    def _frame(self):
        if self.closed:
            return

        now = time.perf_counter()
        dt = min(0.05, now - self.last_frame)
        self.last_frame = now

        snapshot = self.node.world.snapshot()
        self._update_camera(snapshot, dt)
        self._draw(snapshot)
        self.root.after(self.FRAME_MS, self._frame)

    def _update_camera(self, snapshot: dict, dt: float):
        if self._is_builder():
            pan = 0.0
            if self.keys.intersection({"a", "left"}):
                pan -= 1.0
            if self.keys.intersection({"d", "right"}):
                pan += 1.0
            if pan:
                self.camera_x += pan * 620.0 * dt
        else:
            player = snapshot.get("players", {}).get(self.local_key)
            if player:
                target = float(player.get("x", 0.0)) - self.VIEW_W * 0.35
                self.camera_x += (target - self.camera_x) * min(1.0, dt * 8.0)

        self.camera_x = self._clamp_camera(self.camera_x)

    def _clamp_camera(self, value: float) -> float:
        return max(0.0, min(GameWorld.WORLD_WIDTH - self.VIEW_W, float(value)))

    def _screen_to_world(self, x: float, y: float) -> Tuple[float, float]:
        return self.camera_x + float(x), float(y)

    def _world_to_screen(self, x: float, y: float) -> Tuple[float, float]:
        return float(x) - self.camera_x, float(y)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def _draw(self, snapshot: dict):
        self.canvas.delete("all")
        self._draw_background()
        self._draw_goal(snapshot.get("goal", {}))
        self._draw_platforms(snapshot.get("platforms", []))
        self._draw_spikes(snapshot.get("spikes", []))
        self._draw_enemies(snapshot.get("enemies", []))
        self._draw_players(snapshot.get("players", {}))
        self._draw_builder_preview(snapshot)
        self._draw_hud(snapshot)

    def _draw_background(self):
        self.canvas.create_rectangle(0, 0, self.VIEW_W, self.VIEW_H, fill="#5a63a9", outline="")
        background = self.sprites.get("background")
        if background is not None:
            max_shift = max(0, background.width() - self.VIEW_W)
            if GameWorld.WORLD_WIDTH <= self.VIEW_W:
                bg_x = 0
            else:
                bg_x = -int(max_shift * (self.camera_x / (GameWorld.WORLD_WIDTH - self.VIEW_W)))
            self.canvas.create_image(bg_x, 0, image=background, anchor="nw")

        floor_y = GameWorld.FLOOR_Y
        self.canvas.create_rectangle(
            -self.camera_x,
            floor_y,
            GameWorld.WORLD_WIDTH - self.camera_x,
            self.VIEW_H,
            fill="#7f93d3",
            outline="",
        )
        self.canvas.create_rectangle(
            -self.camera_x,
            floor_y,
            GameWorld.WORLD_WIDTH - self.camera_x,
            floor_y + 18,
            fill="#e5efff",
            outline="",
        )
        self.canvas.create_line(
            -self.camera_x,
            floor_y + 18,
            GameWorld.WORLD_WIDTH - self.camera_x,
            floor_y + 18,
            fill="#9ab1e7",
            width=4,
        )
        self.canvas.create_text(
            84 - self.camera_x,
            floor_y - 18,
            text="SPAWN",
            fill="#f8fafc",
            font=("Segoe UI", 11, "bold"),
        )

    def _draw_goal(self, goal: dict):
        if not goal:
            return
        x, y = self._world_to_screen(goal["x"], goal["y"])
        w, h = float(goal["w"]), float(goal["h"])
        if x > self.VIEW_W + 100 or x + w < -100:
            return

        self.canvas.create_oval(
            x - 12,
            y - 8,
            x + w + 12,
            y + h + 18,
            fill="#fef3c7",
            outline="",
        )
        reward = self.sprites.get("reward")
        if reward is not None:
            self.canvas.create_image(x + w / 2, y + h + 6, image=reward, anchor="s")
        else:
            self.canvas.create_rectangle(x, y, x + w, y + h, fill="#facc15", outline="#a16207", width=3)
        self.canvas.create_text(
            x + w / 2,
            y - 14,
            text="GOAL",
            fill="#f8fafc",
            font=("Segoe UI", 10, "bold"),
        )

    def _draw_platforms(self, platforms: Iterable[dict]):
        for platform in platforms:
            x, y = self._world_to_screen(platform["x"], platform["y"])
            w, h = float(platform["w"]), float(platform["h"])
            if x > self.VIEW_W + 120 or x + w < -120:
                continue

            kind = int(platform.get("kind", GameWorld.FLAT))
            sprite = self._platform_sprite(kind)
            if sprite is None:
                self._draw_platform_fallback(x, y, w, h, kind)
                continue

            left = x + (w - sprite.width()) / 2
            if kind == GameWorld.JUMP:
                top = y - sprite.height() * 0.76
            elif kind == GameWorld.STAIR:
                top = y - sprite.height() * 0.42
            else:
                top = y - sprite.height() * 0.40
            self.canvas.create_image(left, top, image=sprite, anchor="nw")

    def _draw_platform_fallback(self, x: float, y: float, w: float, h: float, kind: int):
        if kind == GameWorld.JUMP:
            fill, outline = "#67e8f9", "#0e7490"
        elif kind == GameWorld.STAIR:
            fill, outline = "#cbd5e1", "#475569"
        else:
            fill, outline = "#e2e8f0", "#64748b"
        self.canvas.create_rectangle(x, y, x + w, y + h, fill=fill, outline=outline, width=2)

    def _draw_spikes(self, spikes: Iterable[dict]):
        spike_sprite = self.sprites.get("spike")
        for spike in spikes:
            x, y = self._world_to_screen(spike["x"], spike["y"])
            w, h = float(spike["w"]), float(spike["h"])
            if x > self.VIEW_W + 100 or x + w < -100:
                continue

            if spike_sprite is None:
                self.canvas.create_polygon(
                    x,
                    y + h,
                    x + w / 2,
                    y,
                    x + w,
                    y + h,
                    fill="#ef4444",
                    outline="#991b1b",
                    width=2,
                )
                continue

            left = x + (w - spike_sprite.width()) / 2
            top = y - spike_sprite.height() * 0.33
            self.canvas.create_image(left, top, image=spike_sprite, anchor="nw")

    def _draw_enemies(self, enemies: Iterable[dict]):
        enemy_sprite_bundle = self.sprites.get("enemy", {})
        for enemy in enemies:
            x, y = self._world_to_screen(enemy["x"], enemy["y"])
            w, h = float(enemy["w"]), float(enemy["h"])
            if x > self.VIEW_W + 120 or x + w < -120:
                continue

            enemy_sprite = enemy_sprite_bundle.get("right")
            if int(enemy.get("dir", 1)) < 0:
                enemy_sprite = enemy_sprite_bundle.get("left") or enemy_sprite
            if enemy_sprite is None:
                self.canvas.create_rectangle(x, y, x + w, y + h, fill="#dc2626", outline="#7f1d1d", width=3)
                continue

            left = x + (w - enemy_sprite.width()) / 2
            top = y - enemy_sprite.height() * 0.38
            self.canvas.create_image(left, top, image=enemy_sprite, anchor="nw")

    def _draw_players(self, players: Dict[str, dict]):
        for key, player in players.items():
            if player.get("builder", False):
                continue
            x, y = self._world_to_screen(player["x"], player["y"])
            if x > self.VIEW_W + 120 or x + GameWorld.PLAYER_W < -120:
                continue

            sprite = self._player_sprite(key, player)
            sprite_left = x
            sprite_top = y
            if sprite is not None:
                sprite_left = x + (GameWorld.PLAYER_W - sprite.width()) / 2
                sprite_top = y + GameWorld.PLAYER_H - sprite.height() - 2
                self.canvas.create_image(sprite_left, sprite_top, image=sprite, anchor="nw")
            else:
                color = self._player_color(key)
                self.canvas.create_rectangle(
                    x,
                    y,
                    x + GameWorld.PLAYER_W,
                    y + GameWorld.PLAYER_H,
                    fill=color,
                    outline="#111827",
                    width=3,
                )

            halo_left = sprite_left - 4
            halo_top = sprite_top - 4
            halo_right = sprite_left + (sprite.width() if sprite is not None else GameWorld.PLAYER_W) + 4
            halo_bottom = sprite_top + (sprite.height() if sprite is not None else GameWorld.PLAYER_H) + 4

            if key == self.local_key:
                self.canvas.create_oval(
                    halo_left,
                    halo_top,
                    halo_right,
                    halo_bottom,
                    outline="#f8fafc",
                    width=2,
                )
            if player.get("finished", False):
                self.canvas.create_oval(
                    halo_left - 6,
                    halo_top - 6,
                    halo_right + 6,
                    halo_bottom + 6,
                    outline="#22c55e",
                    width=3,
                )
            elif not player.get("alive", True):
                self.canvas.create_line(halo_left, halo_top, halo_right, halo_bottom, fill="#ef4444", width=3)
                self.canvas.create_line(halo_left, halo_bottom, halo_right, halo_top, fill="#ef4444", width=3)

            label = "YOU" if key == self.local_key else key.split(":")[-1]
            if player.get("finished", False):
                label = f"{label} OK"
            elif not player.get("alive", True):
                label = f"{label} x{int(player.get('deaths', 0))}"
            self.canvas.create_text(
                x + GameWorld.PLAYER_W / 2,
                sprite_top - 10,
                text=label,
                fill="#f8fafc",
                font=("Segoe UI", 9, "bold"),
            )

    def _draw_builder_preview(self, snapshot: dict):
        if not self._is_builder():
            return
        world_x, world_y = self._screen_to_world(*self.mouse_screen)
        outline = "#dc2626" if self._platform_used_up(snapshot, self.selected_kind) else "#f8fafc"
        for rect in self._preview_rects(world_x, world_y, self.selected_kind):
            x, y = self._world_to_screen(rect["x"], rect["y"])
            self.canvas.create_rectangle(
                x,
                y,
                x + rect["w"],
                y + rect["h"],
                outline=outline,
                dash=(5, 4),
                width=2,
            )
        self.canvas.create_line(
            self.mouse_screen[0] - 10,
            self.mouse_screen[1],
            self.mouse_screen[0] + 10,
            self.mouse_screen[1],
            fill=outline,
            width=2,
        )
        self.canvas.create_line(
            self.mouse_screen[0],
            self.mouse_screen[1] - 10,
            self.mouse_screen[0],
            self.mouse_screen[1] + 10,
            fill=outline,
            width=2,
        )

    def _draw_hud(self, snapshot: dict):
        role_text = "HOST BUILDER" if self._is_builder() else "RUNNER"
        selected_used, selected_max = self._platform_count_values(snapshot, self.selected_kind)
        selected = f"{self.KIND_LABELS.get(self.selected_kind, 'Flat')} {selected_used}/{selected_max}"
        if self._is_builder() and selected_used >= selected_max:
            selected = f"{selected} FULL"
        runners = [
            player
            for player in snapshot.get("players", {}).values()
            if not player.get("builder", False)
        ]
        finished = sum(1 for player in runners if player.get("finished", False))
        total = len(runners)
        counts_text = self._platform_counts_text(snapshot)

        self.canvas.create_rectangle(14, 12, 572, 130, fill="#10162d", outline="#dbeafe", width=2)
        self.canvas.create_text(30, 30, anchor="w", text=role_text, fill="#f8fafc", font=("Segoe UI", 13, "bold"))
        self.canvas.create_text(
            30,
            56,
            anchor="w",
            text=f"Tick {snapshot.get('tick', 0)} | Players finished {finished}/{total}",
            fill="#cbd5e1",
            font=("Segoe UI", 10),
        )
        if self._is_builder():
            controls = f"Mouse: place/remove | 1/2/3 or Q/E: {selected} | A/D: pan"
        else:
            controls = "A/D or arrows: move | W/Up/Space: jump | R: respawn"
        self.canvas.create_text(30, 82, anchor="w", text=controls, fill="#93c5fd", font=("Segoe UI", 10))
        self.canvas.create_text(30, 108, anchor="w", text=counts_text, fill="#fde68a", font=("Segoe UI", 10, "bold"))

        local_player = snapshot.get("players", {}).get(self.local_key)
        if local_player and local_player.get("finished", False):
            self.canvas.create_rectangle(340, 246, 760, 334, fill="#dcfce7", outline="#15803d", width=4)
            self.canvas.create_text(
                550,
                290,
                text="You reached the reward!",
                fill="#14532d",
                font=("Segoe UI", 22, "bold"),
            )

    def _preview_rects(self, world_x: float, world_y: float, kind: int):
        snapped_x = round(world_x / GameWorld.GRID) * GameWorld.GRID
        snapped_y = round(world_y / GameWorld.GRID) * GameWorld.GRID
        snapped_y = max(110, min(GameWorld.FLOOR_Y - 42, snapped_y))
        if kind == GameWorld.STAIR:
            step_w = 58
            step_h = 18
            rise = 30
            run = 50
            base_x = snapped_x - step_w // 2
            base_y = snapped_y - step_h // 2
            return [
                {"x": base_x + index * run, "y": base_y - index * rise, "w": step_w, "h": step_h}
                for index in range(4)
            ]
        if kind == GameWorld.JUMP:
            width, height = 128, 18
        else:
            width, height = 164, 22
        return [{"x": snapped_x - width // 2, "y": snapped_y - height // 2, "w": width, "h": height}]

    def _platform_counts_text(self, snapshot: dict) -> str:
        parts = []
        for kind in (GameWorld.FLAT, GameWorld.JUMP, GameWorld.STAIR):
            used, maximum = self._platform_count_values(snapshot, kind)
            label = self.KIND_LABELS[kind]
            parts.append(f"{label}: {used}/{maximum}")
        return "Placed lifetime | " + " | ".join(parts)

    def _platform_count_values(self, snapshot: dict, kind: int) -> Tuple[int, int]:
        counts = snapshot.get("platform_counts", {})
        item = counts.get(str(kind), counts.get(kind, {}))
        if isinstance(item, dict):
            used = int(item.get("used", 0))
            maximum = int(item.get("max", GameWorld.PLATFORM_LIMIT_PER_KIND))
        else:
            used = int(item or 0)
            maximum = GameWorld.PLATFORM_LIMIT_PER_KIND
        return used, maximum

    def _platform_used_up(self, snapshot: dict, kind: int) -> bool:
        used, maximum = self._platform_count_values(snapshot, kind)
        return used >= maximum

    def _platform_sprite(self, kind: int) -> Optional[tk.PhotoImage]:
        if kind == GameWorld.JUMP:
            return self.sprites.get("jump_platform")
        if kind == GameWorld.STAIR:
            return self.sprites.get("stair_platform")
        return self.sprites.get("flat_platform")

    def _player_sprite(self, key: str, player: dict) -> Optional[tk.PhotoImage]:
        cats = self.sprites.get("cats", [])
        if not cats:
            return None
        avatar_id = int(player.get("avatar_id", -1))
        if avatar_id < 0:
            bundle = cats[sum(ord(char) for char in key) % len(cats)]
        else:
            bundle = cats[avatar_id % len(cats)]
        facing = 1 if int(player.get("facing", 1)) >= 0 else -1
        sprite = bundle.get("right")
        if facing < 0:
            sprite = bundle.get("left") or sprite
        return sprite

    def _player_color(self, key: str) -> str:
        index = sum(ord(char) for char in key) % len(self.PLAYER_COLORS)
        return self.PLAYER_COLORS[index]


def parse_join(value: str) -> Tuple[str, int]:
    if ":" not in value:
        return value, 5005
    host, port = value.rsplit(":", 1)
    return host, int(port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pure Python cooperative platformer prototype")
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument("--host", action="store_true", help="Start as host/builder/server")
    mode.add_argument("--join", metavar="HOST[:PORT]", help="Join a host as a runner")
    parser.add_argument("--ip", default="127.0.0.1", help="Local bind IP")
    parser.add_argument("--port", type=int, default=5005, help="Local UDP port")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.host:
        node = Node(ip=args.ip, port=args.port, role=Role.SERVER)
        title = f"Distributed Platformer - host builder :{args.port}"
    elif args.join:
        server_host, server_port = parse_join(args.join)
        node = Node(ip=args.ip, port=args.port, role=Role.CLIENT, server_info=(server_host, server_port))
        title = f"Distributed Platformer - runner :{args.port} -> {server_host}:{server_port}"
    else:
        launcher = LauncherWindow()
        result = launcher.run()
        if result is None:
            return
        node, title = result

    app = PlatformerApp(node, title)
    app.run()


if __name__ == "__main__":
    main()
