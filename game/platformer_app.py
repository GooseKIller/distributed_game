from __future__ import annotations

import argparse
import sys
import time
import tkinter as tk
from pathlib import Path
from typing import Dict, Iterable, Tuple


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from game.game import GameWorld
    from server.Transport import Role
    from server.node import Node
except ImportError:
    from distributed_game.game.game import GameWorld
    from distributed_game.server.Transport import Role
    from distributed_game.server.node import Node


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

    def _leading_runner_x(self, snapshot: dict) -> float:
        runners = [
            float(player.get("x", 0.0))
            for player in snapshot.get("players", {}).values()
            if not player.get("builder", False)
        ]
        return max(runners) if runners else 0.0

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
        self._draw_builder_preview()
        self._draw_hud(snapshot)

    def _draw_background(self):
        self.canvas.create_rectangle(0, 0, self.VIEW_W, self.VIEW_H, fill="#dbeafe", outline="")
        self.canvas.create_rectangle(0, 0, self.VIEW_W, 210, fill="#bfdbfe", outline="")
        self.canvas.create_oval(90, 58, 230, 105, fill="#eff6ff", outline="")
        self.canvas.create_oval(710, 82, 890, 132, fill="#eff6ff", outline="")

        start_x = int(self.camera_x // 120) * 120
        for world_x in range(start_x, start_x + self.VIEW_W + 240, 120):
            screen_x, _ = self._world_to_screen(world_x, 0)
            self.canvas.create_line(screen_x, 0, screen_x, self.VIEW_H, fill="#c7d2fe", dash=(2, 10))

        floor_y = GameWorld.FLOOR_Y
        self.canvas.create_rectangle(
            -self.camera_x,
            floor_y,
            GameWorld.WORLD_WIDTH - self.camera_x,
            self.VIEW_H,
            fill="#6b4f32",
            outline="#4b3521",
        )
        self.canvas.create_rectangle(
            -self.camera_x,
            floor_y,
            GameWorld.WORLD_WIDTH - self.camera_x,
            floor_y + 18,
            fill="#78a948",
            outline="",
        )
        self.canvas.create_text(
            76 - self.camera_x,
            floor_y - 18,
            text="SPAWN",
            fill="#1e3a8a",
            font=("Segoe UI", 11, "bold"),
        )

    def _draw_goal(self, goal: dict):
        if not goal:
            return
        x, y = self._world_to_screen(goal["x"], goal["y"])
        w, h = float(goal["w"]), float(goal["h"])
        if x > self.VIEW_W + 80 or x + w < -80:
            return
        self.canvas.create_rectangle(x - 18, y - 28, x + w + 28, GameWorld.FLOOR_Y, fill="#fde68a", outline="")
        self.canvas.create_polygon(
            x,
            y + h / 2,
            x - 24,
            y + 10,
            x - 24,
            y + h - 10,
            fill="#f97316",
            outline="#9a3412",
        )
        self.canvas.create_oval(x, y, x + w, y + h, fill="#facc15", outline="#a16207", width=3)
        self.canvas.create_oval(x + w - 20, y + 12, x + w - 12, y + 20, fill="#111827", outline="")
        self.canvas.create_text(x + w / 2, y - 16, text="FISH GOAL", fill="#92400e", font=("Segoe UI", 12, "bold"))

    def _draw_platforms(self, platforms: Iterable[dict]):
        for platform in platforms:
            x, y = self._world_to_screen(platform["x"], platform["y"])
            w, h = float(platform["w"]), float(platform["h"])
            if x > self.VIEW_W + 80 or x + w < -80:
                continue

            kind = int(platform.get("kind", GameWorld.FLAT))
            if kind == GameWorld.JUMP:
                fill, outline, label = "#67e8f9", "#0e7490", "JUMP"
            elif kind == GameWorld.STAIR:
                fill, outline, label = "#fbbf24", "#92400e", ""
            else:
                fill, outline, label = "#94a3b8", "#334155", ""

            self.canvas.create_rectangle(x, y, x + w, y + h, fill=fill, outline=outline, width=2)
            self.canvas.create_line(x + 4, y + 4, x + w - 4, y + 4, fill="#f8fafc")
            if label:
                self.canvas.create_text(x + w / 2, y + h / 2, text=label, fill="#155e75", font=("Segoe UI", 8, "bold"))

    def _draw_spikes(self, spikes: Iterable[dict]):
        for spike in spikes:
            x, y = self._world_to_screen(spike["x"], spike["y"])
            w, h = float(spike["w"]), float(spike["h"])
            if x > self.VIEW_W + 50 or x + w < -50:
                continue
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

    def _draw_enemies(self, enemies: Iterable[dict]):
        for enemy in enemies:
            x, y = self._world_to_screen(enemy["x"], enemy["y"])
            w, h = float(enemy["w"]), float(enemy["h"])
            if x > self.VIEW_W + 80 or x + w < -80:
                continue
            self.canvas.create_rectangle(x, y, x + w, y + h, fill="#dc2626", outline="#7f1d1d", width=3)
            self.canvas.create_rectangle(x + 7, y + 8, x + 14, y + 15, fill="#111827", outline="")
            self.canvas.create_rectangle(x + w - 14, y + 8, x + w - 7, y + 15, fill="#111827", outline="")

    def _draw_players(self, players: Dict[str, dict]):
        for key, player in players.items():
            if player.get("builder", False):
                continue
            x, y = self._world_to_screen(player["x"], player["y"])
            if x > self.VIEW_W + 80 or x + GameWorld.PLAYER_W < -80:
                continue

            color = self._player_color(key)
            outline = "#f8fafc" if key == self.local_key else "#111827"
            if not player.get("alive", True):
                color = "#64748b"
            if player.get("finished", False):
                color = "#16a34a"

            self.canvas.create_rectangle(
                x,
                y,
                x + GameWorld.PLAYER_W,
                y + GameWorld.PLAYER_H,
                fill=color,
                outline=outline,
                width=3,
            )
            self.canvas.create_oval(x + 8, y + 10, x + 14, y + 16, fill="#111827", outline="")
            self.canvas.create_oval(x + 21, y + 10, x + 27, y + 16, fill="#111827", outline="")

            label = "YOU" if key == self.local_key else key.split(":")[-1]
            if player.get("finished", False):
                label = f"{label} OK"
            elif not player.get("alive", True):
                label = f"{label} x{int(player.get('deaths', 0))}"
            self.canvas.create_text(
                x + GameWorld.PLAYER_W / 2,
                y - 12,
                text=label,
                fill="#0f172a",
                font=("Segoe UI", 9, "bold"),
            )

    def _draw_builder_preview(self):
        if not self._is_builder():
            return
        world_x, world_y = self._screen_to_world(*self.mouse_screen)
        outline = "#dc2626" if self._platform_used_up(self.node.world.snapshot(), self.selected_kind) else "#111827"
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
            fill="#111827",
            width=2,
        )
        self.canvas.create_line(
            self.mouse_screen[0],
            self.mouse_screen[1] - 10,
            self.mouse_screen[0],
            self.mouse_screen[1] + 10,
            fill="#111827",
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

        self.canvas.create_rectangle(14, 12, 548, 130, fill="#0f172a", outline="#334155", width=2)
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
            self.canvas.create_rectangle(350, 250, 750, 330, fill="#dcfce7", outline="#15803d", width=4)
            self.canvas.create_text(
                550,
                290,
                text="You reached the fish!",
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
    mode = parser.add_mutually_exclusive_group(required=True)
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
    else:
        server_host, server_port = parse_join(args.join)
        node = Node(ip=args.ip, port=args.port, role=Role.CLIENT, server_info=(server_host, server_port))
        title = f"Distributed Platformer - runner :{args.port} -> {server_host}:{server_port}"

    app = PlatformerApp(node, title)
    app.run()


if __name__ == "__main__":
    main()
