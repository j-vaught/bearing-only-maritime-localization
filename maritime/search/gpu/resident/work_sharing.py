"""Cost-gated terminal scoring handoffs. Scheduling only; no host map queries."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Condition, Event
from time import perf_counter
from typing import Any


@dataclass
class ScorePacket:
    identifier: int
    source: int
    destination: int
    begin: int
    end: int
    count: int
    cells: Any = None
    ready: Any = None
    done: Event = field(default_factory=Event)
    error: BaseException | None = None


class ScoreBroker:
    """Move a cell once, only to an idle peer, within a fixed byte budget.

    All geometric and assignment work remains on the recipient GPU. The
    forecast learns score milliseconds per cell and observed transfer cost.
    A recipient has at most one reserved/active packet. It cannot forward it.
    """

    def __init__(
        self,
        devices: tuple[int, ...],
        *,
        min_cells: int = 32,
        max_cells: int = 256,
        byte_budget: int = 32 * 1024**2,
        min_savings_ms: float = 2.0,
        cooldown_s: float = 0.002,
    ) -> None:
        self.condition = Condition()
        self.devices = tuple(devices)
        self.min_cells, self.max_cells = min_cells, max_cells
        self.byte_budget, self.min_savings_ms = byte_budget, min_savings_ms
        self.cooldown_s = cooldown_s
        self.idle: set[int] = set()
        self.busy: set[int] = set()
        self.pending: dict[int, ScorePacket] = {}
        self.last_transfer = {device: -float("inf") for device in devices}
        self.score_ms_per_cell = 0.1
        self.transfer_ms = 0.1
        self.bytes_reserved = 0
        self.identifiers = 0
        self.records: list[dict[str, Any]] = []
        self.rejections: dict[str, int] = {}

    def mark_idle(self, device: int) -> None:
        with self.condition:
            if device not in self.busy:
                self.idle.add(device)
            self.condition.notify_all()

    def reserve(
        self, source: int, count: int, begin: int, end: int, peers: set[int]
    ) -> list[ScorePacket]:
        with self.condition:
            now = perf_counter()
            destinations = sorted(
                d
                for d in self.idle
                if d != source and d in peers and now - self.last_transfer[d] >= self.cooldown_s
            )
            destinations = destinations[: max(0, count // self.min_cells - 1)]
            chunk = min(self.max_cells, count // (len(destinations) + 1))
            reason = None
            if not destinations:
                reason = "no_idle_peer"
            elif chunk < self.min_cells:
                reason = "small_batch"
            elif (
                count * self.score_ms_per_cell
                - max(
                    (count - chunk * len(destinations)) * self.score_ms_per_cell,
                    chunk * self.score_ms_per_cell + self.transfer_ms,
                )
                < self.min_savings_ms
            ):
                reason = "forecast_savings_too_small"
            if reason is not None:
                self.rejections[reason] = self.rejections.get(reason, 0) + 1
                return []
            packets = []
            for destination in destinations:
                size = chunk * 4 * 8
                if self.bytes_reserved + size > self.byte_budget:
                    self.rejections["byte_budget"] = self.rejections.get("byte_budget", 0) + 1
                    break
                packet = ScorePacket(self.identifiers, source, destination, begin, end, chunk)
                self.identifiers += 1
                self.bytes_reserved += size
                self.idle.remove(destination)
                self.busy.add(destination)
                self.last_transfer[destination] = now
                packets.append(packet)
            return packets

    def publish(self, packet: ScorePacket, cells: Any, ready: Any) -> None:
        with self.condition:
            if len(cells) != packet.count or packet.destination in self.pending:
                raise RuntimeError("Invalid or duplicate score packet")
            packet.cells, packet.ready = cells, ready
            self.pending[packet.destination] = packet
            self.condition.notify_all()

    def take(self, device: int) -> ScorePacket | None:
        with self.condition:
            return self.pending.pop(device, None)

    def observe_local(self, count: int, elapsed_ms: float) -> None:
        if not count:
            return
        with self.condition:
            self.score_ms_per_cell = 0.8 * self.score_ms_per_cell + 0.2 * elapsed_ms / count

    def finish(
        self,
        packet: ScorePacket,
        *,
        copy_ms: float,
        score_ms: float,
        error: BaseException | None = None,
    ) -> None:
        with self.condition:
            packet.error = error
            self.busy.remove(packet.destination)
            self.idle.add(packet.destination)
            self.transfer_ms = 0.8 * self.transfer_ms + 0.2 * copy_ms
            if error is None:
                self.score_ms_per_cell = (
                    0.8 * self.score_ms_per_cell + 0.2 * score_ms / packet.count
                )
            self.records.append(
                {
                    "packet": packet.identifier,
                    "source": packet.source,
                    "destination": packet.destination,
                    "cells": packet.count,
                    "bytes": packet.count * 32,
                    "copy_ms": copy_ms,
                    "score_ms": score_ms,
                    "hops": 1,
                    "failed": error is not None,
                }
            )
            packet.cells = None
            packet.done.set()
            self.condition.notify_all()

    def wait(self, packet: ScorePacket, stop: Event) -> None:
        while not packet.done.wait(0.01):
            if stop.is_set():
                raise RuntimeError("Stopped while waiting for a scoring peer")
        if packet.error is not None:
            raise RuntimeError("Peer scoring failed") from packet.error

    def pause(self) -> None:
        with self.condition:
            self.condition.wait(0.002)

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "mode": "terminal scoring to idle peer GPUs",
            "map_bytes_moved": 0,
            "candidate_bytes_moved": self.bytes_reserved,
            "byte_budget": self.byte_budget,
            "max_hops_per_cell": 1,
            "handoffs": len(self.records),
            "records": self.records,
            "forecast_score_ms_per_cell": self.score_ms_per_cell,
            "forecast_copy_ms": self.transfer_ms,
            "skipped_handoffs": self.rejections,
        }
