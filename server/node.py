import random
import threading
import time
from typing import Dict, Tuple, List, Optional

try:
    from game.game import GameWorld
    from server.Transport import Neighbor, Endpoint, Role, Transport, Message, MessageType
except ImportError:
    from distributed_game.game.game import GameWorld
    from distributed_game.server.Transport import Neighbor, Endpoint, Role, Transport, Message, MessageType


class Membership:
    def __init__(self):
        self._lock = threading.Lock()
        self._nodes: Dict[Tuple[str, int], Neighbor] = {}
        self.version = 0

    def upsert(self, ep: Endpoint, role: Role, bump_version: bool = False):
        key = (ep.ip, ep.port)
        changed = False
        with self._lock:
            if key not in self._nodes:
                self._nodes[key] = Neighbor(ep, role)
                changed = True
            else:
                node = self._nodes[key]
                if node.role != role:
                    node.role = role
                    changed = True
                node.last_seen = time.time()

            if changed and bump_version:
                self.version += 1

    def touch(self, ep: Endpoint):
        with self._lock:
            node = self._nodes.get((ep.ip, ep.port))
            if node is not None:
                node.last_seen = time.time()

    def remove(self, ep: Endpoint):
        key = (ep.ip, ep.port)
        with self._lock:
            if key in self._nodes:
                self._nodes.pop(key)
                self.version += 1

    def replace_from_snapshot(self, version: int, nodes: List[dict]):
        with self._lock:
            if version < self.version:
                return
            new_nodes = {}
            for item in nodes:
                ep = Endpoint(item["ip"], int(item["port"]))
                role = Role(item["role"])
                key = (ep.ip, ep.port)
                last_seen = self._nodes.get(key, Neighbor(ep, role)).last_seen
                new_nodes[key] = Neighbor(ep, role, last_seen)
            self._nodes = new_nodes
            self.version = version

    def snapshot(self) -> dict:
        with self._lock:
            nodes_list = sorted(self._nodes.values(), key=lambda n: (n.endpoint.ip, n.endpoint.port))
            return {
                "version": self.version,
                "nodes": [{"ip": n.endpoint.ip, "port": n.endpoint.port, "role": n.role.value} for n in nodes_list],
            }

    def all(self) -> List[Neighbor]:
        with self._lock:
            return list(self._nodes.values())

    def clients(self) -> List[Neighbor]:
        with self._lock:
            return [n for n in self._nodes.values() if n.role == Role.CLIENT]

    def server(self) -> Optional[Neighbor]:
        with self._lock:
            for n in self._nodes.values():
                if n.role == Role.SERVER:
                    return n
        return None



class Node:
    def __init__(
        self,
        ip: str = "127.0.0.1",
        port: int = 5005,
        role: Role = Role.CLIENT,
        server_info: Optional[Tuple[str, int]] = None,
        heartbeat_interval: float = 0.3,
        tick_interval: float = 0.05,
        timeout: float = 6.0,
        election_jitter: Tuple[float, float] = (0.4, 1.2),
    ):
        self.endpoint = Endpoint(ip, port)
        self.role = role

        self.heartbeat_interval = heartbeat_interval
        self.tick_interval = tick_interval
        self.timeout = timeout
        self.election_jitter = election_jitter

        self.transport = Transport(ip, port)
        self.membership = Membership()
        self.world = GameWorld()

        self.term = 0
        self.highest_term_seen = 0
        self.current_server: Optional[Endpoint] = None
        self.last_server_heartbeat = time.time()
        self.running = False

        self._state_lock = threading.Lock()
        self._election_lock = threading.Lock()
        self.election_in_progress = False

        self._incoming_inputs: Dict[str, Dict[int, dict]] = {}
        self._pending_inputs: Dict[int, dict] = {}
        self._next_input_seq = 0
        self._last_membership_version_sent = -1

        # Инициализация
        if role == Role.SERVER:
            self.term = 1
            self.highest_term_seen = 1
            self.current_server = self.endpoint
            self.membership.upsert(self.endpoint, Role.SERVER, bump_version=True)
            self.world.set_builder_player(self.endpoint.key())
        elif server_info is not None:
            server_ep = Endpoint(*server_info)
            self.current_server = server_ep
            self.membership.upsert(server_ep, Role.SERVER)
            self.last_server_heartbeat = time.time()
            self.world.ensure_player(self.endpoint.key())

    def start(self):
        self.running = True
        threading.Thread(target=self._recv_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._server_tick_loop, daemon=True).start()

        print(f"[{self.role.value.upper()}] {self.endpoint.key()} started")

    def stop(self):
        self.running = False
        self.transport.close()

    def send_input(self, input_data: dict):
        with self._state_lock:
            self._next_input_seq += 1
            seq = self._next_input_seq
            player_key = self.endpoint.key()

            if self.role == Role.CLIENT:
                self._pending_inputs[seq] = input_data.copy()
                self.world.apply_input(player_key, input_data, self.tick_interval, authoritative=False)
                if self.current_server:
                    msg = Message(
                        type=MessageType.INPUT,
                        sender=self.endpoint,
                        term=self.term,
                        seq=seq,
                        payload={"input": input_data}
                    )
                    self.transport.send(self.current_server, msg)
            else:
                self.world.apply_input(player_key, input_data, self.tick_interval, authoritative=True)
                self.world.last_input_seq[player_key] = seq

    # ================== HELPERS ==================
    def _update_term(self, incoming_term: int):
        if incoming_term > self.highest_term_seen:
            self.highest_term_seen = incoming_term
        if incoming_term > self.term:
            self.term = incoming_term

    def _server_snapshot(self) -> dict:
        state = self.world.snapshot()
        return {
            "state": state,
            "hash": self.world.state_hash(),
            "membership": self.membership.snapshot(),
        }

    def _broadcast(self, msg: Message, only_clients: bool = True):
        targets = self.membership.clients() if only_clients else self.membership.all()
        for n in targets:
            if n.endpoint == self.endpoint:
                continue
            self.transport.send(n.endpoint, msg)

    def _broadcast_membership(self):
        snap = self.membership.snapshot()
        if snap["version"] == self._last_membership_version_sent:
            return
        self._last_membership_version_sent = snap["version"]
        msg = Message(type=MessageType.NODES, sender=self.endpoint, term=self.term,
                      tick=self.world.tick, payload=snap)
        self._broadcast(msg, only_clients=False)

    def _broadcast_state(self):
        snap = self._server_snapshot()
        msg = Message(type=MessageType.STATE, sender=self.endpoint, term=self.term,
                      tick=self.world.tick, payload=snap)
        self._broadcast(msg, only_clients=False)

    def _send_state_to(self, ep: Endpoint):
        msg = Message(type=MessageType.STATE, sender=self.endpoint, term=self.term,
                      tick=self.world.tick, payload=self._server_snapshot())
        self.transport.send(ep, msg)

    def _send_membership_to(self, ep: Endpoint):
        msg = Message(type=MessageType.NODES, sender=self.endpoint, term=self.term,
                      tick=self.world.tick, payload=self.membership.snapshot())
        self.transport.send(ep, msg)

    def _become_server(self):
        with self._election_lock:
            if self.role == Role.SERVER:
                return
            self.role = Role.SERVER
            self.term = max(self.term, self.highest_term_seen) + 1
            self.highest_term_seen = self.term
            self.current_server = self.endpoint

            old = self.membership.server()
            if old and old.endpoint != self.endpoint:
                self.membership.remove(old.endpoint)

            self.membership.upsert(self.endpoint, Role.SERVER, bump_version=True)
            self.world.set_builder_player(self.endpoint.key())
            self.election_in_progress = False
            self._last_membership_version_sent = -1

            print(f"[ELECTION] {self.endpoint.key()} became SERVER at term {self.term}")
            self._broadcast_membership()
            self._broadcast_state()

    def _pick_leader(self) -> Endpoint:
        candidates = {n.endpoint for n in self.membership.all()}
        candidates.add(self.endpoint)
        return min(candidates, key=lambda e: (e.ip, e.port))

    def _trigger_election(self):
        with self._election_lock:
            if self.election_in_progress or self.role == Role.SERVER:
                return
            self.election_in_progress = True

        time.sleep(random.uniform(*self.election_jitter))

        if self.role == Role.SERVER:
            self.election_in_progress = False
            return

        if self._pick_leader() == self.endpoint:
            self._become_server()
        else:
            self.election_in_progress = False

    def _start_failover_if_needed(self):
        if self.role == Role.SERVER or not self.current_server:
            return
        if time.time() - self.last_server_heartbeat < self.timeout:
            return

        stale_server = self.current_server
        print(f"[CLIENT] No heartbeat from {stale_server.key()} for >{self.timeout}s -> election")
        self.membership.remove(stale_server)
        if self.current_server == stale_server:
            self.current_server = None
        self._trigger_election()

    def _queue_input(self, sender: Endpoint, seq: int, input_data: dict):
        self._incoming_inputs.setdefault(sender.key(), {})[seq] = self._sanitize_client_input(input_data)

    def _sanitize_client_input(self, input_data: dict) -> dict:
        sanitized = input_data.copy()
        for key in ("place_platform", "remove_platform", "place_block", "remove_block"):
            sanitized.pop(key, None)
        return sanitized

    def _apply_queued_inputs(self):
        for player_key, seq_map in list(self._incoming_inputs.items()):
            last = self.world.last_input_seq.get(player_key, 0)
            for seq in sorted(s for s in seq_map if s > last):
                inp = seq_map.pop(seq)
                self.world.apply_input(player_key, inp, self.tick_interval, authoritative=True)
                self.world.last_input_seq[player_key] = seq
            if not seq_map:
                self._incoming_inputs.pop(player_key, None)

    # ================== LOOPS ==================
    def _recv_loop(self):
        while self.running:
            try:
                msg = self.transport.recv()
                self._handle(msg)
            except Exception as e:
                if getattr(e, "winerror", None) == 10054:
                    continue
                if self.running:
                    print(f"[RECV ERROR] {e}")

    def _heartbeat_loop(self):
        while self.running:
            try:
                if self.role == Role.SERVER:
                    self._remove_dead_clients()
                    self._broadcast_membership()
                    hb = Message(type=MessageType.HEARTBEAT, sender=self.endpoint,
                                 term=self.term, tick=self.world.tick, payload={"role": "server"})
                    self._broadcast(hb)
                else:
                    if self.current_server:
                        hb = Message(type=MessageType.HEARTBEAT, sender=self.endpoint,
                                     term=self.term, tick=self.world.tick, payload={"role": "client"})
                        self.transport.send(self.current_server, hb)
                    self._start_failover_if_needed()

                time.sleep(self.heartbeat_interval)
            except Exception as e:
                if self.running:
                    print(f"[HEARTBEAT ERROR] {e}")
                time.sleep(self.heartbeat_interval)

    def _server_tick_loop(self):
        while self.running:
            start = time.time()
            try:
                if self.role != Role.SERVER:
                    time.sleep(self.tick_interval)
                    continue
                with self._state_lock:
                    self.world.tick += 1
                    self._apply_queued_inputs()
                    self.world.step(self.tick_interval)
                    self._broadcast_state()
                time.sleep(max(0.0, self.tick_interval - (time.time() - start)))
            except Exception as e:
                if self.running:
                    print(f"[SERVER TICK ERROR] {e}")
                time.sleep(self.tick_interval)

    def _remove_dead_clients(self):
        now = time.time()
        dead = [n.endpoint for n in self.membership.clients()
                if n.endpoint != self.endpoint and now - n.last_seen > self.timeout]
        if dead:
            for ep in dead:
                print(f"[SERVER] removing dead client {ep.key()}")
                self.membership.remove(ep)
            self._last_membership_version_sent = -1
            self._broadcast_membership()

    # ================== MESSAGE HANDLING ==================
    def _handle(self, msg: Message):
        self._update_term(msg.term)

        if self.role == Role.SERVER and msg.term > self.term and msg.sender != self.endpoint:
            self._step_down(msg.term, msg.sender)

        if self.role == Role.SERVER:
            self._handle_server(msg)
        else:
            self._handle_client(msg)

    def _handle_server(self, msg: Message):
        self.membership.touch(msg.sender)

        if msg.type == MessageType.HEARTBEAT:
            self.membership.upsert(msg.sender, Role.CLIENT)
            self.world.ensure_player(msg.sender.key())
            self._send_membership_to(msg.sender)
            self._send_state_to(msg.sender)

        elif msg.type == MessageType.INPUT:
            self.membership.upsert(msg.sender, Role.CLIENT)
            self.world.ensure_player(msg.sender.key())
            self._queue_input(msg.sender, int(msg.seq), msg.payload.get("input", {}))
            self._send_state_to(msg.sender)

        elif msg.type == MessageType.NODES:
            p = msg.payload
            v = int(p.get("version", 0))
            if v >= self.membership.version:
                self.membership.replace_from_snapshot(v, p.get("nodes", []))

    def _handle_client(self, msg: Message):
        if msg.type in (MessageType.HEARTBEAT, MessageType.STATE):
            self.last_server_heartbeat = time.time()
            self._accept_server(msg.sender, msg.term)

        if msg.type == MessageType.NODES:
            p = msg.payload
            v = int(p.get("version", 0))
            self.membership.replace_from_snapshot(v, p.get("nodes", []))
            self._accept_server(msg.sender, msg.term)

        if msg.type == MessageType.STATE:
            self._apply_authoritative_state(msg)

    def _accept_server(self, server_ep: Endpoint, term: int):
        if term < self.term or self.role == Role.SERVER:
            return

        changed = (self.current_server != server_ep)
        self.current_server = server_ep
        self.highest_term_seen = max(self.highest_term_seen, term)
        self.term = max(self.term, term)
        self.membership.upsert(server_ep, Role.SERVER)
        self.election_in_progress = False

        if changed and server_ep != self.endpoint:
            print(f"[CLIENT] Accepted server {server_ep.key()} at term {term}")

    def _apply_authoritative_state(self, msg: Message):
        payload = msg.payload
        if payload.get("membership"):
            m = payload["membership"]
            self.membership.replace_from_snapshot(int(m.get("version", 0)), m.get("nodes", []))

        with self._state_lock:
            self.world.load_snapshot(payload.get("state", {}))
            player_key = self.endpoint.key()
            ack = self.world.last_input_seq.get(player_key, 0)
            for seq in list(self._pending_inputs):
                if seq <= ack:
                    self._pending_inputs.pop(seq, None)
            for seq in sorted(self._pending_inputs):
                self.world.apply_input(player_key, self._pending_inputs[seq], self.tick_interval, authoritative=False)

    def _step_down(self, new_term: int, new_server: Optional[Endpoint] = None):
        self.role = Role.CLIENT
        self.term = new_term
        self.highest_term_seen = max(self.highest_term_seen, new_term)
        self.election_in_progress = False
        self.world.clear_builder(self.endpoint.key())
        if new_server:
            self.current_server = new_server


# ================== MAIN ==================
if __name__ == "__main__":
    role_input = input("ROLE [S/C]: ").strip().upper()
    port = int(input("Your Port: "))

    if role_input == "S":
        node = Node(port=port, role=Role.SERVER)
    else:
        server_port = int(input("Server Port: "))
        node = Node(port=port, role=Role.CLIENT, server_info=("127.0.0.1", server_port))

    node.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
        node.stop()
