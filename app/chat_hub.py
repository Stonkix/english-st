"""Подключения WebSocket чата в памяти процесса (он один, см. englishst.service) и рассылка по ним.

У каждого подключения своя очередь и задача-писатель: события уходят по порядку, а медленный телефон
не задерживает остальных. Публиковать можно из любого потока (маршруты с БД работают в пуле потоков).
"""

import asyncio
import logging
import threading

from fastapi import WebSocket

QUEUE_LIMIT = 200  # столько событий может ждать отправки; кто отстал сильнее — отключается и догрузит пропущенное


class Connection:
    def __init__(self, websocket: WebSocket, user_id: int):
        self.websocket = websocket
        self.user_id = user_id
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=QUEUE_LIMIT)

    def push(self, payload: dict | None) -> None:
        """Только в потоке цикла событий подключения."""
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            logging.warning("Чат: очередь пользователя %s переполнена, отключаем", self.user_id)
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(None)

    async def writer(self) -> None:
        while True:
            payload = await self.queue.get()
            if payload is None:
                await self.websocket.close()
                return
            await self.websocket.send_json(payload)


class Hub:
    def __init__(self):
        self._connections: dict[int, set[Connection]] = {}
        self._lock = threading.Lock()

    def add(self, conn: Connection) -> None:
        with self._lock:
            self._connections.setdefault(conn.user_id, set()).add(conn)

    def remove(self, conn: Connection) -> None:
        with self._lock:
            conns = self._connections.get(conn.user_id)
            if conns is not None:
                conns.discard(conn)
                if not conns:
                    del self._connections[conn.user_id]

    def send(self, payloads: dict[int, dict]) -> None:
        """Событие каждому получателю (у каждого может быть свой вариант: модератор видит исходный текст)."""
        with self._lock:
            targets = [(c, payloads[uid]) for uid in payloads for c in self._connections.get(uid, ())]
        for conn, payload in targets:
            try:
                conn.loop.call_soon_threadsafe(conn.push, payload)
            except RuntimeError:  # цикл уже остановлен (выключение сервера)
                pass

    def send_one(self, conn: Connection, payload: dict) -> None:
        conn.loop.call_soon_threadsafe(conn.push, payload)


hub = Hub()
