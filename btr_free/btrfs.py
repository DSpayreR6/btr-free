from __future__ import annotations

import hashlib
import re
import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Snapshot:
    subvol_id: int
    path: str
    group: str
    otime: datetime | None
    rfer: int = 0
    excl: int = 0


def _run(*args: str) -> str:
    result = subprocess.run(list(args), capture_output=True, text=True, check=True)
    return result.stdout


def list_snapshots() -> list[Snapshot]:
    """Parse 'btrfs subvolume list -s /' – returns only snapper-style snapshots."""
    output = _run('btrfs', 'subvolume', 'list', '-s', '/')
    snapshots: list[Snapshot] = []
    for line in output.splitlines():
        m = re.match(
            r'ID\s+(\d+)\s+gen\s+\d+\s+cgen\s+\d+\s+top level\s+\d+\s+'
            r'otime\s+(\S+\s+\S+)\s+path\s+(.+)',
            line.strip(),
        )
        if not m:
            continue
        subvol_id = int(m.group(1))
        path = m.group(3).strip()
        if not re.search(r'(^|/)\.snapshots/\d+/snapshot$', path):
            continue
        try:
            otime: datetime | None = datetime.strptime(m.group(2), '%Y-%m-%d %H:%M:%S')
        except ValueError:
            otime = None
        if '/.snapshots/' in path:
            group = path.split('/.snapshots/')[0].strip('/').split('/')[-1]
        else:
            try:
                show = _run('btrfs', 'subvolume', 'show', '/')
                group = show.splitlines()[0].strip().split('/')[-1] or 'root'
            except Exception:
                group = 'root'
        snapshots.append(Snapshot(subvol_id=subvol_id, path=path, group=group, otime=otime))
    return snapshots


def get_qgroup_data() -> dict[int, tuple[int, int]]:
    """Returns {subvol_id: (rfer_bytes, excl_bytes)} for all level-0 qgroups."""
    output = _run('btrfs', 'qgroup', 'show', '--raw', '/')
    data: dict[int, tuple[int, int]] = {}
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        m = re.match(r'^0/(\d+)$', parts[0])
        if not m:
            continue
        try:
            data[int(m.group(1))] = (int(parts[1]), int(parts[2]))
        except ValueError:
            continue
    return data


def get_fingerprint() -> str:
    """SHA256[:16] of hostname + CPU model – detects PC changes."""
    cpu = ''
    try:
        with open('/proc/cpuinfo') as f:
            for line in f:
                if line.startswith('model name'):
                    cpu = line.split(':', 1)[1].strip()
                    break
    except OSError:
        pass
    raw = f'{socket.gethostname()}:{cpu}'.encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def get_subvol_count() -> int:
    output = _run('btrfs', 'subvolume', 'list', '/')
    return sum(1 for line in output.splitlines() if line.strip())


def get_fstab_btrfs_mounts() -> dict[str, str]:
    """Parse /etc/fstab → {subvol_name: mount_point} for all btrfs entries."""
    mounts: dict[str, str] = {}
    try:
        with open('/etc/fstab') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) < 4 or parts[2] != 'btrfs':
                    continue
                mountpoint, options = parts[1], parts[3]
                for opt in options.split(','):
                    if opt.startswith('subvol='):
                        sv = opt[7:].strip('/')
                        if sv:
                            mounts[sv] = mountpoint
                        break
    except OSError:
        pass
    return mounts


def delete_snapshot(subvol_id: int, btrfs_path: str) -> None:
    # Safety 1: must match snapper path pattern
    if not re.search(r'/\.snapshots/\d+/snapshot$', btrfs_path):
        raise ValueError(f'Refusing to delete non-snapshot path: {btrfs_path}')

    # Safety 2: refuse if the path itself is a mounted fstab subvolume
    if btrfs_path in get_fstab_btrfs_mounts():
        raise ValueError(f'Refusing to delete mounted subvolume: {btrfs_path}')

    result = subprocess.run(
        ['btrfs', 'subvolume', 'delete', '--subvolid', str(subvol_id), '/'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'btrfs delete failed [subvolid={subvol_id}]: '
            f'{result.stderr.strip() or result.stdout.strip()}'
        )


def rescan_wait() -> None:
    _run('btrfs', 'quota', 'rescan', '-w', '/')


def _destroy_qgroup(qgroup: str) -> None:
    # First remove any existing child assignments (destroy fails when children exist)
    result = subprocess.run(
        ['btrfs', 'qgroup', 'show', '--raw', '-p', '/'],
        capture_output=True, text=True,
    )
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 4 and parts[3] == qgroup:
            subprocess.run(
                ['btrfs', 'qgroup', 'remove', parts[0], qgroup, '/'],
                capture_output=True,
            )
    subprocess.run(
        ['btrfs', 'qgroup', 'destroy', qgroup, '/'],
        capture_output=True,
    )


def calc_freed_space(
    subvol_ids: list[int],
    progress_cb: Callable[[str], None] | None = None,
    eta_hint: str = '',
) -> int:
    """
    Creates temp qgroup 1/9999, assigns selected snapshot subvolumes,
    runs quota rescan, returns the excl value (bytes that would be freed).
    Always cleans up the temp qgroup on exit.
    """
    qgroup = '1/9999'
    _destroy_qgroup(qgroup)
    try:
        if progress_cb:
            progress_cb('Creating temporary qgroup...')
        _run('btrfs', 'qgroup', 'create', qgroup, '/')

        for sid in subvol_ids:
            try:
                _run('btrfs', 'qgroup', 'create', f'0/{sid}', '/')
            except subprocess.CalledProcessError:
                pass
            try:
                _run('btrfs', 'qgroup', 'assign', f'0/{sid}', qgroup, '/')
            except subprocess.CalledProcessError:
                pass

        if progress_cb:
            eta_part = f' ({eta_hint})' if eta_hint else ''
            progress_cb(f'Running quota rescan{eta_part}...')
        rescan_wait()

        if progress_cb:
            progress_cb('Reading result...')
        output = _run('btrfs', 'qgroup', 'show', '--raw', '/')
        for line in output.splitlines():
            parts = line.strip().split()
            if parts and parts[0] == qgroup and len(parts) >= 3:
                return int(parts[2])
        return 0
    finally:
        _destroy_qgroup(qgroup)
