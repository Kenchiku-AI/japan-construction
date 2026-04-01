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

  async def send_to_user(self, user_id: str, payload: dict):

    if user_id not in self.connections:
      return

    for ws in self.connections[user_id]:
      await ws.send_json(payload)

manager = ConnectionManager()