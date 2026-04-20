from typing import Dict
import threading
import json
import hashlib

class GameWorld:
    def __init__(self):
        self.tick = 0
        self.players: Dict[str, dict] = {}
        self.blocks: Dict[str, dict] = {}
        self.last_input_seq: Dict[str, int] = {}
        self._lock = threading.Lock()

    def ensure_player(self, player_key: str):
        with self._lock:
            if player_key not in self.players:
                self.players[player_key] = {"x": 0.0, "y": 0.0, "vx": 0.0, "vy": 0.0, "on_ground": True}
            self.last_input_seq.setdefault(player_key, 0)

    def _place_block(self, x: int, y: int, kind: int = 1):
        self.blocks[f"{x}:{y}"] = {"x": x, "y": y, "kind": kind}

    def _remove_block(self, x: int, y: int):
        self.blocks.pop(f"{x}:{y}", None)

    def apply_input(self, player_key: str, inp: dict, dt: float, authoritative: bool):
        self.ensure_player(player_key)
        with self._lock:
            p = self.players[player_key]
            move_x = max(-1.0, min(1.0, float(inp.get("move_x", 0.0))))
            speed = float(inp.get("speed", 6.0))
            jump = bool(inp.get("jump", False))

            p["x"] += move_x * speed * dt
            if jump and p["on_ground"]:
                p["vy"] = float(inp.get("jump_velocity", 9.0))
                p["on_ground"] = False

            if authoritative:
                if inp.get("place_block"):
                    b = inp["place_block"]
                    self._place_block(int(b.get("x", 0)), int(b.get("y", 0)), int(b.get("kind", 1)))
                if inp.get("remove_block"):
                    b = inp["remove_block"]
                    self._remove_block(int(b.get("x", 0)), int(b.get("y", 0)))

    def step(self, dt: float):
        gravity = 18.0
        with self._lock:
            for p in self.players.values():
                if not p["on_ground"]:
                    p["vy"] -= gravity * dt
                    p["y"] += p["vy"] * dt
                    if p["y"] <= 0.0:
                        p["y"] = 0.0
                        p["vy"] = 0.0
                        p["on_ground"] = True

    def snapshot(self) -> dict:
        with self._lock:
            players = {k: {**v} for k, v in sorted(self.players.items())}
            blocks = sorted(
                ({"x": int(v["x"]), "y": int(v["y"]), "kind": int(v["kind"])} for v in self.blocks.values()),
                key=lambda b: (b["x"], b["y"], b["kind"])
            )
            return {
                "tick": self.tick,
                "players": players,
                "blocks": blocks,
                "last_input_seq": dict(self.last_input_seq),
            }

    def load_snapshot(self, snapshot: dict):
        with self._lock:
            self.tick = int(snapshot.get("tick", self.tick))
            self.players = {
                k: {
                    "x": float(v.get("x", 0.0)),
                    "y": float(v.get("y", 0.0)),
                    "vx": float(v.get("vx", 0.0)),
                    "vy": float(v.get("vy", 0.0)),
                    "on_ground": bool(v.get("on_ground", True)),
                }
                for k, v in snapshot.get("players", {}).items()
            }
            self.blocks.clear()
            for b in snapshot.get("blocks", []):
                x, y = int(b["x"]), int(b["y"])
                self.blocks[f"{x}:{y}"] = {"x": x, "y": y, "kind": int(b.get("kind", 1))}
            self.last_input_seq = {k: int(v) for k, v in snapshot.get("last_input_seq", {}).items()}

    def state_hash(self) -> str:
        snap = self.snapshot()
        raw = json.dumps(snap, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

