from enum import Enum
import json
from dataclasses import dataclass, field
import socket
import time

class Role(Enum):
    SERVER = "server"
    CLIENT = "client"


class MessageType(Enum):
    HEARTBEAT = "HEARTBEAT"
    INPUT = "INPUT"
    STATE = "STATE"
    NODES = "NODES"
    RESYNC = "RESYNC"


@dataclass(frozen=True, order=True)
class Endpoint:
    ip: str
    port: int

    def key(self) -> str:
        return f"{self.ip}:{self.port}"

    def to_dict(self) -> dict:
        return {"ip": self.ip, "port": self.port}

    @staticmethod
    def from_dict(data: dict) -> "Endpoint":
        return Endpoint(data["ip"], int(data["port"]))


@dataclass
class Neighbor:
    endpoint: Endpoint
    role: Role
    last_seen: float = field(default_factory=time.time)


@dataclass
class Message:
    type: MessageType
    sender: Endpoint
    term: int = 0
    tick: int = 0
    seq: int = 0
    payload: dict = field(default_factory=dict)

    def encode(self) -> bytes:
        return json.dumps(
            {
                "type": self.type.value,
                "sender": self.sender.to_dict(),
                "term": self.term,
                "tick": self.tick,
                "seq": self.seq,
                "payload": self.payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @staticmethod
    def decode(raw: bytes) -> "Message":
        data = json.loads(raw.decode("utf-8"))
        return Message(
            type=MessageType(data["type"]),
            sender=Endpoint.from_dict(data["sender"]),
            term=int(data.get("term", 0)),
            tick=int(data.get("tick", 0)),
            seq=int(data.get("seq", 0)),
            payload=data.get("payload", {}),
        )


class Transport:
    def __init__(self, ip: str, port: int):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((ip, port))

    def send(self, ep: Endpoint, msg: Message):
        try:
            self.sock.sendto(msg.encode(), (ep.ip, ep.port))
        except Exception as e:
            print(f"[ERROR] send to {ep.key()} failed: {e}")

    def recv(self) -> Message:
        raw, _ = self.sock.recvfrom(65535)
        return Message.decode(raw)

    def close(self):
        try:
            self.sock.close()
        except Exception as e:
            print(f"[ERROR] closing socket: {e}")
