from typing import Dict, List
from fastapi import WebSocket
import json

class ConnectionManager:

  def __init__(self):
    self.connections: Dict[str, List[WebSocket]] = {}

  async def connect(self, user_id: str, ws: WebSocket):
    await ws.accept()

    if user_id not in self.connections:
      self.connections[user_id] = []

    self.connections[user_id].append(ws)

  def disconnect(self, user_id: str, ws: WebSocket):
    if user_id in self.connections:
      if ws in self.connections[user_id]:
        self.connections[user_id].remove(ws)

      if not self.connections[user_id]:
        del self.connections[user_id]

  async def send_to_user(self, user_id: str, payload: dict):
    if user_id not in self.connections:
      return

    dead_connections = []

    for ws in list(self.connections[user_id]):
      try:
        await ws.send_json(payload)
      except Exception:
        dead_connections.append(ws)

    for ws in dead_connections:
      self.disconnect(user_id, ws)

manager = ConnectionManager()