"""Host scheduling unit checks. No geometry, CUDA or search is performed."""

from threading import Event

import pytest

from maritime.search.gpu.resident.work_sharing import ScoreBroker


def test_balances_ready_cells_only_once_and_respects_one_packet_per_peer():
    broker = ScoreBroker((0, 1, 2))
    broker.mark_idle(1)
    broker.mark_idle(2)
    packets = broker.reserve(0, 171, 0, 128, {1, 2})
    assert [p.count for p in packets] == [57, 57]
    assert broker.reserve(0, 512, 0, 128, {1, 2}) == []
    for packet in packets:
        broker.publish(packet, list(range(packet.count)), object())
        assert broker.take(packet.destination) is packet
        assert broker.take(packet.destination) is None
        broker.finish(packet, copy_ms=0.05, score_ms=10)
        broker.wait(packet, Event())
    report = broker.summary()
    assert report["candidate_bytes_moved"] == 114 * 32
    assert report["map_bytes_moved"] == 0
    assert report["max_hops_per_cell"] == 1
    assert all(p.cells is None for p in packets)


def test_small_packets_and_transfer_cost_cannot_trigger_migration():
    broker = ScoreBroker((0, 1))
    broker.mark_idle(1)
    assert broker.reserve(0, 31, 0, 1, {1}) == []
    broker.transfer_ms = 1000
    assert broker.reserve(0, 512, 0, 1, {1}) == []
    assert broker.summary()["candidate_bytes_moved"] == 0


def test_byte_limit_and_peer_capability_are_hard_bounds():
    broker = ScoreBroker((0, 1, 2), byte_budget=57 * 32)
    broker.mark_idle(1)
    broker.mark_idle(2)
    assert broker.reserve(0, 171, 0, 1, set()) == []
    packets = broker.reserve(0, 171, 0, 1, {1, 2})
    assert len(packets) == 1
    assert broker.bytes_reserved == broker.byte_budget


def test_two_way_sharing_does_not_drop_small_third_chunk():
    broker = ScoreBroker((0, 1, 2))
    broker.mark_idle(1)
    broker.mark_idle(2)
    packets = broker.reserve(0, 80, 0, 1, {1, 2})
    assert len(packets) == 1 and packets[0].count == 40


def test_failure_propagates_and_stopped_wait_never_accepts_incomplete_results():
    broker = ScoreBroker((0, 1))
    broker.mark_idle(1)
    packet = broker.reserve(0, 128, 0, 1, {1})[0]
    broker.publish(packet, [0] * packet.count, object())
    broker.take(1)
    broker.finish(packet, copy_ms=0, score_ms=0, error=ValueError("failure"))
    with pytest.raises(RuntimeError, match="Peer scoring failed"):
        broker.wait(packet, Event())
    broker = ScoreBroker((0, 1))
    broker.mark_idle(1)
    packet = broker.reserve(0, 128, 0, 1, {1})[0]
    stop = Event()
    stop.set()
    with pytest.raises(RuntimeError, match="Stopped"):
        broker.wait(packet, stop)
