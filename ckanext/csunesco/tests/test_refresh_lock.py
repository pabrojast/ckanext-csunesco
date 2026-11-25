"""The portal repair loop must treat a lock held by another worker as busy."""
import errno
import fcntl

import pytest

from ckanext.csunesco.logic import snapshots


def fail_with(monkeypatch, error):
    def flock(lock, flags):
        assert flags == fcntl.LOCK_EX | fcntl.LOCK_NB
        raise error
    monkeypatch.setattr(fcntl, 'flock', flock)


def test_a_free_lock_is_taken(tmp_path):
    with (tmp_path / 'refresh.lock').open('a') as lock:
        assert snapshots.take_refresh_lock(lock) is True


def test_a_lock_held_on_a_local_disk_is_busy(tmp_path):
    path = tmp_path / 'refresh.lock'
    with path.open('a') as holder, path.open('a') as other:
        assert snapshots.take_refresh_lock(holder) is True
        assert snapshots.take_refresh_lock(other) is False


def test_a_lock_held_on_an_smb_share_is_busy_not_an_error(monkeypatch, tmp_path):
    # Azure Files answers EACCES ("Permission denied") for a lock held elsewhere.
    fail_with(monkeypatch, PermissionError(errno.EACCES, 'Permission denied'))
    with (tmp_path / 'refresh.lock').open('a') as lock:
        assert snapshots.take_refresh_lock(lock) is False


def test_any_other_lock_error_is_still_raised(monkeypatch, tmp_path):
    fail_with(monkeypatch, OSError(errno.ENOLCK, 'No locks available'))
    with (tmp_path / 'refresh.lock').open('a') as lock:
        with pytest.raises(OSError):
            snapshots.take_refresh_lock(lock)
