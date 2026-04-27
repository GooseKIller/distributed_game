from __future__ import annotations

import hashlib
import json
import math
import threading
from typing import Dict, Iterable, List, Optional


class GameWorld:
    """Authoritative state and physics for the cooperative platformer.

    The world is deliberately sprite-free: everything is represented as
    rectangles/triangles so art can be dropped in later without changing the
    server protocol.
    """

    WORLD_WIDTH = 2600
    WORLD_HEIGHT = 720
    FLOOR_Y = 650

    PLAYER_W = 72
    PLAYER_H = 30
    SPAWN_X = 48
    PLAYER_AVATAR_COUNT = 5
    SPIKE_W = 60
    SPIKE_H = 24
    ENEMY_W = 56
    ENEMY_H = 38

    GRAVITY = 1750.0
    MOVE_SPEED = 285.0
    JUMP_SPEED = 680.0
    JUMP_PAD_SPEED = 940.0
    MAX_FALL_SPEED = 1050.0

    FLAT = 1
    JUMP = 2
    STAIR = 3

    PLATFORM_NAMES = {
        FLAT: "flat",
        JUMP: "jump",
        STAIR: "stair",
    }

    PLATFORM_LIMIT = 90
    PLATFORM_LIMIT_PER_KIND = 3
    GRID = 24

    def __init__(self):
        self.tick = 0
        self.players: Dict[str, dict] = {}
        self.platforms: Dict[str, dict] = {}
        self.blocks = self.platforms
        self.last_input_seq: Dict[str, int] = {}
        self.enemies: Dict[str, dict] = {}
        self.spikes: List[dict] = []
        self.goal = {
            "x": self.WORLD_WIDTH - 126,
            "y": self.FLOOR_Y - 72,
            "w": 84,
            "h": 56,
        }
        self._platform_counter = 0
        self._platform_order: List[str] = []
        self.platform_place_counts = self._empty_platform_counts()
        self._lock = threading.RLock()
        self._build_static_map()

    # ---------------------------------------------------------------------
    # Static map setup
    # ---------------------------------------------------------------------
    def _build_static_map(self):
        self.spikes = [
            {"x": 360, "y": self.FLOOR_Y - self.SPIKE_H, "w": self.SPIKE_W, "h": self.SPIKE_H},
            {"x": 444, "y": self.FLOOR_Y - self.SPIKE_H, "w": self.SPIKE_W, "h": self.SPIKE_H},
            {"x": 700, "y": self.FLOOR_Y - self.SPIKE_H, "w": self.SPIKE_W, "h": self.SPIKE_H},
            {"x": 784, "y": self.FLOOR_Y - self.SPIKE_H, "w": self.SPIKE_W, "h": self.SPIKE_H},
            {"x": 868, "y": self.FLOOR_Y - self.SPIKE_H, "w": self.SPIKE_W, "h": self.SPIKE_H},
            {"x": 1260, "y": self.FLOOR_Y - self.SPIKE_H, "w": self.SPIKE_W, "h": self.SPIKE_H},
            {"x": 1344, "y": self.FLOOR_Y - self.SPIKE_H, "w": self.SPIKE_W, "h": self.SPIKE_H},
            {"x": 1875, "y": self.FLOOR_Y - self.SPIKE_H, "w": self.SPIKE_W, "h": self.SPIKE_H},
            {"x": 1959, "y": self.FLOOR_Y - self.SPIKE_H, "w": self.SPIKE_W, "h": self.SPIKE_H},
            {"x": 2200, "y": self.FLOOR_Y - self.SPIKE_H, "w": self.SPIKE_W, "h": self.SPIKE_H},
        ]
        self.enemies = {
            "enemy-1": {
                "id": "enemy-1",
                "x": 910.0,
                "y": self.FLOOR_Y - self.ENEMY_H,
                "w": self.ENEMY_W,
                "h": self.ENEMY_H,
                "min_x": 880.0,
                "max_x": 1160.0,
                "speed": 96.0,
                "dir": 1,
            },
            "enemy-2": {
                "id": "enemy-2",
                "x": 1510.0,
                "y": self.FLOOR_Y - self.ENEMY_H,
                "w": self.ENEMY_W,
                "h": self.ENEMY_H,
                "min_x": 1460.0,
                "max_x": 1775.0,
                "speed": 122.0,
                "dir": -1,
            },
            "enemy-3": {
                "id": "enemy-3",
                "x": 2240.0,
                "y": self.FLOOR_Y - self.ENEMY_H,
                "w": self.ENEMY_W,
                "h": self.ENEMY_H,
                "min_x": 2075.0,
                "max_x": 2360.0,
                "speed": 105.0,
                "dir": 1,
            },
        }

    # ---------------------------------------------------------------------
    # Player helpers
    # ---------------------------------------------------------------------
    def _pick_avatar_id(self, exclude_player: Optional[str] = None) -> int:
        used = {
            int(player.get("avatar_id", -1))
            for key, player in self.players.items()
            if key != exclude_player and not player.get("builder", False) and int(player.get("avatar_id", -1)) >= 0
        }
        for avatar_id in range(self.PLAYER_AVATAR_COUNT):
            if avatar_id not in used:
                return avatar_id
        seed = sum(ord(char) for char in (exclude_player or ""))
        return seed % self.PLAYER_AVATAR_COUNT

    def _new_player(self, builder: bool = False) -> dict:
        return {
            "x": float(self.SPAWN_X),
            "y": float(self.FLOOR_Y - self.PLAYER_H),
            "vx": 0.0,
            "vy": 0.0,
            "move_x": 0.0,
            "on_ground": True,
            "alive": True,
            "finished": False,
            "builder": bool(builder),
            "avatar_id": -1 if builder else self._pick_avatar_id(),
            "facing": 1,
            "respawn_timer": 0.0,
            "deaths": 0,
        }

    def _ensure_avatar(self, player_key: str):
        player = self.players[player_key]
        if player.get("builder", False):
            player["avatar_id"] = -1
            return
        if int(player.get("avatar_id", -1)) >= 0:
            return
        player["avatar_id"] = self._pick_avatar_id(exclude_player=player_key)

    def ensure_player(self, player_key: str, builder: bool = False):
        with self._lock:
            if player_key not in self.players:
                self.players[player_key] = self._new_player(builder=builder)
            elif builder:
                self.players[player_key]["builder"] = True
            self._ensure_avatar(player_key)
            self.last_input_seq.setdefault(player_key, 0)

    def set_builder_player(self, player_key: str):
        with self._lock:
            self.ensure_player(player_key, builder=True)
            for key, player in self.players.items():
                player["builder"] = key == player_key
                if player["builder"]:
                    player["vx"] = 0.0
                    player["vy"] = 0.0
                    player["move_x"] = 0.0
                    player["avatar_id"] = -1
                else:
                    self._ensure_avatar(key)

    def clear_builder(self, player_key: Optional[str] = None):
        with self._lock:
            for key, player in self.players.items():
                if player_key is None or key == player_key:
                    player["builder"] = False
                    self._ensure_avatar(key)

    def _reset_player(self, player: dict, keep_deaths: bool = True):
        deaths = int(player.get("deaths", 0)) if keep_deaths else 0
        builder = bool(player.get("builder", False))
        avatar_id = int(player.get("avatar_id", -1))
        facing = int(player.get("facing", 1))
        player.clear()
        player.update(self._new_player(builder=builder))
        player["deaths"] = deaths
        player["facing"] = 1 if facing >= 0 else -1
        if builder:
            player["avatar_id"] = -1
        elif avatar_id >= 0:
            player["avatar_id"] = avatar_id

    def _kill_player(self, player: dict):
        if not player.get("alive", True) or player.get("builder", False):
            return
        player["alive"] = False
        player["respawn_timer"] = 0.75
        player["vx"] = 0.0
        player["vy"] = 0.0
        player["move_x"] = 0.0
        player["deaths"] = int(player.get("deaths", 0)) + 1

    # ---------------------------------------------------------------------
    # Platform placement
    # ---------------------------------------------------------------------
    def _snap(self, value: float) -> int:
        return int(round(float(value) / self.GRID) * self.GRID)

    def _next_platform_id(self, prefix: str = "p") -> str:
        self._platform_counter += 1
        return f"{prefix}-{self._platform_counter}"

    def _add_platform(self, platform: dict):
        platform_id = str(platform["id"])
        self.platforms[platform_id] = platform
        self._platform_order.append(platform_id)
        while len(self._platform_order) > self.PLATFORM_LIMIT:
            oldest = self._platform_order.pop(0)
            self.platforms.pop(oldest, None)

    def _empty_platform_counts(self) -> dict:
        return {str(kind): 0 for kind in self.PLATFORM_NAMES}

    def _normalize_platform_kind(self, kind: int) -> int:
        kind = int(kind)
        if kind not in self.PLATFORM_NAMES:
            return self.FLAT
        return kind

    def _placement_count(self, kind: int) -> int:
        return int(self.platform_place_counts.get(str(self._normalize_platform_kind(kind)), 0))

    def _record_platform_placement(self, kind: int):
        key = str(self._normalize_platform_kind(kind))
        self.platform_place_counts[key] = self._placement_count(kind) + 1

    def can_place_platform(self, kind: int) -> bool:
        return self._placement_count(kind) < self.PLATFORM_LIMIT_PER_KIND

    def platform_counts_snapshot(self) -> dict:
        return {
            str(kind): {
                "name": name,
                "used": self._placement_count(kind),
                "max": self.PLATFORM_LIMIT_PER_KIND,
                "remaining": max(0, self.PLATFORM_LIMIT_PER_KIND - self._placement_count(kind)),
            }
            for kind, name in self.PLATFORM_NAMES.items()
        }

    def _place_platform(self, x: float, y: float, kind: int = FLAT):
        kind = self._normalize_platform_kind(kind)
        if not self.can_place_platform(kind):
            return False

        snapped_x = self._snap(x)
        snapped_y = self._snap(y)
        snapped_x = max(0, min(self.WORLD_WIDTH - 40, snapped_x))
        snapped_y = max(110, min(self.FLOOR_Y - 42, snapped_y))

        group = self._next_platform_id("platform")
        self._record_platform_placement(kind)

        if kind == self.STAIR:
            step_w = 58
            step_h = 18
            rise = 30
            run = 50
            base_x = snapped_x - step_w // 2
            base_y = snapped_y - step_h // 2
            for index in range(4):
                px = base_x + index * run
                py = base_y - index * rise
                self._add_platform(
                    {
                        "id": f"{group}-{index}",
                        "group": group,
                        "x": float(px),
                        "y": float(py),
                        "w": float(step_w),
                        "h": float(step_h),
                        "kind": self.STAIR,
                    }
                )
            return True

        if kind == self.JUMP:
            width, height = 128, 18
        else:
            width, height = 164, 22

        self._add_platform(
            {
                "id": group,
                "group": group,
                "x": float(snapped_x - width // 2),
                "y": float(snapped_y - height // 2),
                "w": float(width),
                "h": float(height),
                "kind": kind,
            }
        )
        return True

    def _remove_platform(self, x: float, y: float):
        hit_id = self._platform_at_point(float(x), float(y))
        if hit_id is None:
            return
        group = self.platforms[hit_id].get("group", hit_id)
        remove_ids = [pid for pid, item in self.platforms.items() if item.get("group", pid) == group]
        for pid in remove_ids:
            self.platforms.pop(pid, None)
            if pid in self._platform_order:
                self._platform_order.remove(pid)

    def _platform_at_point(self, x: float, y: float) -> Optional[str]:
        nearest_id = None
        nearest_distance = 999999.0
        for platform_id, platform in self.platforms.items():
            px = float(platform["x"])
            py = float(platform["y"])
            pw = float(platform["w"])
            ph = float(platform["h"])
            if px <= x <= px + pw and py <= y <= py + ph:
                return platform_id
            cx = min(max(x, px), px + pw)
            cy = min(max(y, py), py + ph)
            distance = math.hypot(x - cx, y - cy)
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_id = platform_id
        if nearest_distance <= 44:
            return nearest_id
        return None

    # Old server prototype method names are kept as aliases.
    def _place_block(self, x: int, y: int, kind: int = FLAT):
        return self._place_platform(float(x), float(y), int(kind))

    def _remove_block(self, x: int, y: int):
        self._remove_platform(float(x), float(y))

    # ---------------------------------------------------------------------
    # Input and simulation
    # ---------------------------------------------------------------------
    def apply_input(self, player_key: str, inp: dict, dt: float, authoritative: bool):
        self.ensure_player(player_key)
        with self._lock:
            player = self.players[player_key]

            if player.get("builder", False):
                player["vx"] = 0.0
                player["vy"] = 0.0
                player["move_x"] = 0.0
                if authoritative:
                    if inp.get("place_platform"):
                        item = inp["place_platform"]
                        self._place_platform(
                            float(item.get("x", 0.0)),
                            float(item.get("y", 0.0)),
                            int(item.get("kind", self.FLAT)),
                        )
                    if inp.get("remove_platform"):
                        item = inp["remove_platform"]
                        self._remove_platform(float(item.get("x", 0.0)), float(item.get("y", 0.0)))
                    if inp.get("place_block"):
                        item = inp["place_block"]
                        self._place_block(int(item.get("x", 0)), int(item.get("y", 0)), int(item.get("kind", self.FLAT)))
                    if inp.get("remove_block"):
                        item = inp["remove_block"]
                        self._remove_block(int(item.get("x", 0)), int(item.get("y", 0)))
                return

            if inp.get("respawn"):
                self._reset_player(player)
                return

            if player.get("finished", False):
                player["vx"] = 0.0
                player["move_x"] = 0.0
                return

            if not player.get("alive", True):
                return

            move_x = max(-1.0, min(1.0, float(inp.get("move_x", 0.0))))
            speed = float(inp.get("speed", self.MOVE_SPEED))
            player["move_x"] = move_x
            player["vx"] = move_x * speed
            if move_x > 0:
                player["facing"] = 1
            elif move_x < 0:
                player["facing"] = -1

            if bool(inp.get("jump", False)) and bool(player.get("on_ground", False)):
                player["vy"] = -float(inp.get("jump_velocity", self.JUMP_SPEED))
                player["on_ground"] = False

    def step(self, dt: float):
        with self._lock:
            self._step_enemies(dt)
            for player in self.players.values():
                self._step_player(player, dt)

    def _step_enemies(self, dt: float):
        for enemy in self.enemies.values():
            direction = 1 if int(enemy.get("dir", 1)) >= 0 else -1
            enemy["x"] = float(enemy["x"]) + direction * float(enemy["speed"]) * dt
            if enemy["x"] < enemy["min_x"]:
                enemy["x"] = float(enemy["min_x"])
                enemy["dir"] = 1
            elif enemy["x"] + enemy["w"] > enemy["max_x"]:
                enemy["x"] = float(enemy["max_x"]) - float(enemy["w"])
                enemy["dir"] = -1

    def _step_player(self, player: dict, dt: float):
        if player.get("builder", False):
            return

        if not player.get("alive", True):
            player["respawn_timer"] = max(0.0, float(player.get("respawn_timer", 0.0)) - dt)
            if player["respawn_timer"] <= 0.0:
                self._reset_player(player)
            return

        if player.get("finished", False):
            player["vx"] = 0.0
            player["vy"] = 0.0
            return

        old_x = float(player["x"])
        old_y = float(player["y"])
        player["x"] = max(0.0, min(self.WORLD_WIDTH - self.PLAYER_W, old_x + float(player["vx"]) * dt))
        self._resolve_horizontal_platform_collisions(player)

        old_y = float(player["y"])
        player["vy"] = min(self.MAX_FALL_SPEED, float(player["vy"]) + self.GRAVITY * dt)
        player["y"] = old_y + float(player["vy"]) * dt
        player["on_ground"] = False
        self._resolve_vertical_platform_collisions(player, old_y)
        self._resolve_floor_collision(player)

        if player["y"] > self.WORLD_HEIGHT + 200:
            self._kill_player(player)
            return

        if self._touches_hazard(player):
            self._kill_player(player)
            return

        if self._rects_overlap(self._player_rect(player), self.goal):
            player["finished"] = True
            player["vx"] = 0.0
            player["vy"] = 0.0
            player["on_ground"] = True

    def _resolve_horizontal_platform_collisions(self, player: dict):
        player_rect = self._player_rect(player)
        for platform in self._solid_platforms():
            if not self._rects_overlap(player_rect, platform):
                continue
            if float(player["vx"]) > 0:
                player["x"] = float(platform["x"]) - self.PLAYER_W
            elif float(player["vx"]) < 0:
                player["x"] = float(platform["x"]) + float(platform["w"])
            player_rect = self._player_rect(player)

    def _resolve_vertical_platform_collisions(self, player: dict, old_y: float):
        player_rect = self._player_rect(player)
        old_bottom = old_y + self.PLAYER_H
        for platform in sorted(self._solid_platforms(), key=lambda item: float(item["y"])):
            if not self._rects_overlap(player_rect, platform):
                continue

            platform_y = float(platform["y"])
            platform_bottom = platform_y + float(platform["h"])
            if float(player["vy"]) >= 0 and old_bottom <= platform_y + 6:
                player["y"] = platform_y - self.PLAYER_H
                player["vy"] = 0.0
                player["on_ground"] = True
                if int(platform.get("kind", self.FLAT)) == self.JUMP:
                    player["vy"] = -self.JUMP_PAD_SPEED
                    player["on_ground"] = False
                player_rect = self._player_rect(player)
            elif float(player["vy"]) < 0 and old_y >= platform_bottom - 6:
                player["y"] = platform_bottom
                player["vy"] = 0.0
                player_rect = self._player_rect(player)

    def _resolve_floor_collision(self, player: dict):
        if float(player["y"]) + self.PLAYER_H >= self.FLOOR_Y:
            player["y"] = float(self.FLOOR_Y - self.PLAYER_H)
            player["vy"] = 0.0
            player["on_ground"] = True

    def _touches_hazard(self, player: dict) -> bool:
        player_rect = self._player_rect(player)
        for spike in self.spikes:
            if self._rects_overlap(player_rect, spike):
                return True
        for enemy in self.enemies.values():
            if self._rects_overlap(player_rect, enemy):
                return True
        return False

    def _player_rect(self, player: dict) -> dict:
        return {
            "x": float(player["x"]),
            "y": float(player["y"]),
            "w": float(self.PLAYER_W),
            "h": float(self.PLAYER_H),
        }

    def _solid_platforms(self) -> Iterable[dict]:
        return self.platforms.values()

    @staticmethod
    def _rects_overlap(a: dict, b: dict) -> bool:
        return (
            float(a["x"]) < float(b["x"]) + float(b["w"])
            and float(a["x"]) + float(a["w"]) > float(b["x"])
            and float(a["y"]) < float(b["y"]) + float(b["h"])
            and float(a["y"]) + float(a["h"]) > float(b["y"])
        )

    # ---------------------------------------------------------------------
    # Snapshots
    # ---------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            players = {k: {**v} for k, v in sorted(self.players.items())}
            platforms = sorted(
                ({**platform} for platform in self.platforms.values()),
                key=lambda item: (float(item["x"]), float(item["y"]), str(item["id"])),
            )
            enemies = sorted(
                ({**enemy} for enemy in self.enemies.values()),
                key=lambda item: str(item["id"]),
            )
            return {
                "tick": self.tick,
                "players": players,
                "platforms": platforms,
                "blocks": platforms,
                "spikes": [{**spike} for spike in self.spikes],
                "enemies": enemies,
                "goal": {**self.goal},
                "map": {
                    "world_width": self.WORLD_WIDTH,
                    "world_height": self.WORLD_HEIGHT,
                    "floor_y": self.FLOOR_Y,
                    "player_w": self.PLAYER_W,
                    "player_h": self.PLAYER_H,
                },
                "last_input_seq": dict(self.last_input_seq),
                "platform_counter": self._platform_counter,
                "platform_order": list(self._platform_order),
                "platform_counts": self.platform_counts_snapshot(),
            }

    def load_snapshot(self, snapshot: dict):
        with self._lock:
            self.tick = int(snapshot.get("tick", self.tick))
            self.players = {
                key: self._normalize_player(value)
                for key, value in snapshot.get("players", {}).items()
            }
            self.platforms.clear()
            platform_items = snapshot.get("platforms", snapshot.get("blocks", []))
            for raw in platform_items:
                platform = self._normalize_platform(raw)
                self.platforms[str(platform["id"])] = platform
            self.blocks = self.platforms

            if "spikes" in snapshot:
                self.spikes = [{**item} for item in snapshot.get("spikes", [])]
            if "enemies" in snapshot:
                self.enemies = {
                    str(item.get("id", f"enemy-{index}")): {**item}
                    for index, item in enumerate(snapshot.get("enemies", []), start=1)
                }
            if "goal" in snapshot:
                self.goal = {**snapshot["goal"]}

            self.last_input_seq = {k: int(v) for k, v in snapshot.get("last_input_seq", {}).items()}
            self._platform_counter = int(snapshot.get("platform_counter", self._platform_counter))
            self._platform_order = [
                pid for pid in snapshot.get("platform_order", list(self.platforms)) if pid in self.platforms
            ]
            for pid in self.platforms:
                if pid not in self._platform_order:
                    self._platform_order.append(pid)
            self.platform_place_counts = self._normalize_platform_counts(snapshot)

    def _normalize_player(self, value: dict) -> dict:
        player = self._new_player(builder=bool(value.get("builder", False)))
        for key in ("x", "y", "vx", "vy", "move_x", "respawn_timer"):
            player[key] = float(value.get(key, player[key]))
        for key in ("on_ground", "alive", "finished", "builder"):
            player[key] = bool(value.get(key, player[key]))
        player["avatar_id"] = int(value.get("avatar_id", player["avatar_id"]))
        player["facing"] = 1 if int(value.get("facing", player["facing"])) >= 0 else -1
        player["deaths"] = int(value.get("deaths", player["deaths"]))
        return player

    def _normalize_platform(self, raw: dict) -> dict:
        x = float(raw.get("x", 0.0))
        y = float(raw.get("y", 0.0))
        kind = int(raw.get("kind", self.FLAT))
        width = float(raw.get("w", 164 if kind == self.FLAT else 128))
        height = float(raw.get("h", 22 if kind == self.FLAT else 18))
        platform_id = str(raw.get("id", f"{int(x)}:{int(y)}:{kind}"))
        return {
            "id": platform_id,
            "group": str(raw.get("group", platform_id)),
            "x": x,
            "y": y,
            "w": width,
            "h": height,
            "kind": kind,
        }

    def _normalize_platform_counts(self, snapshot: dict) -> dict:
        counts = self._empty_platform_counts()
        raw_counts = snapshot.get("platform_counts", snapshot.get("platform_place_counts", {}))
        if raw_counts:
            for kind in self.PLATFORM_NAMES:
                item = raw_counts.get(str(kind), raw_counts.get(kind, 0))
                if isinstance(item, dict):
                    item = item.get("used", 0)
                counts[str(kind)] = max(0, int(item))
            return counts

        seen_groups = set()
        for platform in self.platforms.values():
            group = platform.get("group", platform["id"])
            kind = self._normalize_platform_kind(int(platform.get("kind", self.FLAT)))
            key = (kind, group)
            if key in seen_groups:
                continue
            seen_groups.add(key)
            counts[str(kind)] += 1
        return counts

    def state_hash(self) -> str:
        snap = self.snapshot()
        raw = json.dumps(snap, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
