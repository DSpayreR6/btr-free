from __future__ import annotations

import time
from datetime import date, datetime

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Label,
    LoadingIndicator,
    Static,
)

from . import btrfs, cache

_CHECK   = '✓'
_UNCHECK = '□'
_MiB     = 1024 ** 2
_GiB     = 1024 ** 3


def _fmt(b: int) -> str:
    if b == 0:
        return '0 B'
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if abs(b) < 1024.0:
            return f'{b:.2f} {unit}'
        b /= 1024.0
    return f'{b:.2f} PiB'


def _fmt_eta(seconds: float) -> str:
    if seconds < 60:
        return f'{seconds:.0f}s'
    return f'{seconds / 60:.1f} min'


def _excl_text(b: int) -> Text:
    s = _fmt(b)
    if b >= _GiB:
        return Text(s, style='bold red')
    elif b >= _MiB:
        return Text(s, style='yellow')
    return Text(s, style='green')


# ─── Group Select Screen ──────────────────────────────────────────────────────

class GroupSelectScreen(Screen):
    BINDINGS = [
        Binding('q', 'quit', 'Quit'),
    ]
    DEFAULT_CSS = """
    GroupSelectScreen { align: center middle; }
    #box {
        padding: 2 4;
        border: round $primary;
        width: 42;
        height: auto;
        background: $surface;
    }
    #box Label  { margin-bottom: 1; text-align: center; width: 100%; }
    #box Button { margin-top: 1; width: 100%; }
    """

    def __init__(self) -> None:
        super().__init__()
        try:
            snaps = btrfs.list_snapshots()
            self._groups = sorted({s.group for s in snaps})
        except Exception:
            self._groups = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Center():
            with Vertical(id='box'):
                yield Label('Which subvolume groups to show?')
                for g in self._groups:
                    yield Checkbox(g, value=False, id=f'cb-{g}')
                yield Button('Continue →', variant='primary', id='btn-next')
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != 'btn-next':
            return
        groups = [g for g in self._groups if self.query_one(f'#cb-{g}', Checkbox).value]
        if groups:
            self.app.push_screen(SnapshotScreen(groups))

    def action_quit(self) -> None:
        self.app.exit()


# ─── Confirm Delete Screen ────────────────────────────────────────────────────

class ConfirmDeleteScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmDeleteScreen { align: center middle; }
    #confirm-box {
        padding: 2 4;
        border: round $error;
        background: $surface;
        width: 60;
        height: auto;
    }
    #confirm-box Label   { margin-bottom: 1; }
    .snap-path           { color: $text-muted; margin-left: 2; }
    #confirm-btns        { margin-top: 1; align: center middle; height: 3; }
    #btn-yes             { margin-right: 2; }
    """

    def __init__(self, snapshots: list[btrfs.Snapshot]) -> None:
        super().__init__()
        self._snapshots = snapshots

    def compose(self) -> ComposeResult:
        n = len(self._snapshots)
        with Vertical(id='confirm-box'):
            yield Label(f'Permanently delete {n} snapshot(s)?', id='confirm-title')
            for s in self._snapshots[:6]:
                d = s.otime.strftime('%Y-%m-%d') if s.otime else '?'
                yield Label(f'{s.group}  {d}', classes='snap-path')
            if n > 6:
                yield Label(f'  … and {n - 6} more', classes='snap-path')
            with Horizontal(id='confirm-btns'):
                yield Button('Delete', variant='error',   id='btn-yes')
                yield Button('Cancel', variant='primary', id='btn-no')

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == 'btn-yes')


# ─── Snapshot Screen ──────────────────────────────────────────────────────────

class SnapshotScreen(Screen):
    BINDINGS = [
        Binding('space',  'toggle_row',    'Select'),
        Binding('S', 'range_select', 'Select range', priority=True),
        Binding('d',      'deselect_all',  'Deselect all'),
        Binding('delete', 'delete_selected', 'Delete selected'),
        Binding('c',      'calculate',     'Calculate'),
        Binding('escape', 'app.pop_screen','Back'),
        Binding('q',      'quit',          'Quit'),
    ]
    DEFAULT_CSS = """
    SnapshotScreen { layout: vertical; }
    #table   { height: 1fr; }
    #msg     { height: 1; padding: 0 1; background: $surface; }
    #spinner { height: 3; }
    #bar {
        height: auto;
        background: $panel;
        border-top: solid $primary;
        padding: 0 1;
        align: left middle;
    }
    #bar Static { width: auto; margin-right: 3; }
    #btn-calc   { width: auto; margin-right: 3; }
    #stat-hint  { height: 1; padding: 0 1; color: $warning; background: $panel; }
    """

    def __init__(self, groups: list[str]) -> None:
        super().__init__()
        self.groups   = groups
        self._snapshots: list[btrfs.Snapshot] = []
        self._selected: set[int] = set()
        self._anchor:   int = -1

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id='table', cursor_type='row', zebra_stripes=True)
        yield Static('Initializing...', id='msg')
        yield LoadingIndicator(id='spinner')
        with Horizontal(id='bar'):
            yield Static('Selected: 0',      id='stat-n')
            yield Static('Lower bound: 0 B', id='stat-lower')
            yield Button('Calculate', variant='primary', id='btn-calc')
            yield Static('Calculated: ---',  id='stat-result')
        yield Static('', id='stat-hint')
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one('#table', DataTable)
        t.add_column('Date',       key='date',  width=12)
        t.add_column('Subvolume',  key='group', width=12)
        t.add_column('Referenced', key='rfer',  width=14)
        t.add_column('Exclusive',  key='excl',  width=14)
        t.add_column('✓',          key='check', width=3)
        self._set_spinner(False)
        self._load_data()

    # ── Loading ───────────────────────────────────────────────────────────────

    @work(thread=True)
    def _load_data(self) -> None:
        app = self.app

        def msg(text: str) -> None:
            app.call_from_thread(self.query_one('#msg', Static).update, text)

        if cache.needs_refresh():
            fingerprint  = btrfs.get_fingerprint()
            subvol_count = btrfs.get_subvol_count()
            estimated    = cache.get_estimated_duration(fingerprint, subvol_count)
            eta          = f'~{_fmt_eta(estimated)}' if estimated else 'may take several minutes'
            msg(f'Cache outdated – running quota rescan ({eta})...')
            app.call_from_thread(self._set_spinner, True)
            t0 = time.monotonic()
            try:
                btrfs.rescan_wait()
                cache.save_timing(fingerprint, time.monotonic() - t0, subvol_count)
            except Exception as e:
                msg(f'Rescan error: {e}')
            app.call_from_thread(self._set_spinner, False)

        msg('Loading snapshots...')
        try:
            snapshots = btrfs.list_snapshots()
            qdata     = btrfs.get_qgroup_data()
        except Exception as e:
            msg(f'Error: {e}')
            return

        snapshots = [s for s in snapshots if s.group in self.groups]
        today     = date.today()

        for s in snapshots:
            if s.subvol_id in qdata:
                s.rfer, s.excl = qdata[s.subvol_id]

        old = [s for s in snapshots if s.otime and s.otime.date() < today]
        if old:
            cache.save(old)

        snapshots.sort(key=lambda s: s.otime or datetime.min, reverse=True)
        app.call_from_thread(self._fill_table, snapshots)

    def _fill_table(self, snapshots: list[btrfs.Snapshot]) -> None:
        self._snapshots = snapshots
        t = self.query_one('#table', DataTable)
        t.clear()
        self._selected.clear()
        for s in snapshots:
            d = s.otime.strftime('%Y-%m-%d') if s.otime else '?'
            t.add_row(d, s.group, _fmt(s.rfer), _excl_text(s.excl), _UNCHECK,
                      key=str(s.subvol_id))
        self.query_one('#msg', Static).update(f'{len(snapshots)} snapshots loaded')

    # ── Selection ─────────────────────────────────────────────────────────────

    def action_toggle_row(self) -> None:
        t   = self.query_one('#table', DataTable)
        idx = t.cursor_row
        if idx < 0 or idx >= len(self._snapshots):
            return
        s = self._snapshots[idx]
        if s.subvol_id in self._selected:
            self._selected.discard(s.subvol_id)
            mark = _UNCHECK
        else:
            self._selected.add(s.subvol_id)
            mark = _CHECK
        t.update_cell(str(s.subvol_id), 'check', mark, update_width=False)
        self._anchor = idx
        self._refresh_stats()

    def action_range_select(self) -> None:
        t   = self.query_one('#table', DataTable)
        idx = t.cursor_row
        if idx < 0 or idx >= len(self._snapshots):
            return
        anchor = self._anchor if self._anchor >= 0 else idx
        lo, hi = min(anchor, idx), max(anchor, idx)
        for i in range(lo, hi + 1):
            s = self._snapshots[i]
            self._selected.add(s.subvol_id)
            t.update_cell(str(s.subvol_id), 'check', _CHECK, update_width=False)
        self._anchor = idx
        self._refresh_stats()

    def action_deselect_all(self) -> None:
        t = self.query_one('#table', DataTable)
        for s in self._snapshots:
            if s.subvol_id in self._selected:
                t.update_cell(str(s.subvol_id), 'check', _UNCHECK, update_width=False)
        self._selected.clear()
        self._anchor = -1
        self._refresh_stats()

    def action_delete_selected(self) -> None:
        if not self._selected:
            self.query_one('#msg', Static).update('No snapshots selected.')
            return
        to_delete = [s for s in self._snapshots if s.subvol_id in self._selected]
        self.app.push_screen(ConfirmDeleteScreen(to_delete), self._on_delete_confirmed)

    def _on_delete_confirmed(self, confirmed: bool) -> None:
        if confirmed:
            self._delete_snapshots()

    @work(thread=True)
    def _delete_snapshots(self) -> None:
        app      = self.app
        w_msg    = self.query_one('#msg', Static)
        to_delete = [s for s in self._snapshots if s.subvol_id in self._selected]

        errors: list[str] = []
        for i, s in enumerate(to_delete, 1):
            app.call_from_thread(
                w_msg.update, f'Deleting {i}/{len(to_delete)}: {s.path}...'
            )
            try:
                btrfs.delete_snapshot(s.subvol_id, s.path)
            except Exception as e:
                errors.append(f'{s.path}: {e}')

        if errors:
            app.call_from_thread(
                w_msg.update,
                f'{len(to_delete) - len(errors)} deleted, {len(errors)} failed: {errors[0]}'
            )
        else:
            app.call_from_thread(
                w_msg.update, f'Deleted {len(to_delete)} snapshot(s).'
            )
        app.call_from_thread(self._reload_after_delete)

    def _reload_after_delete(self) -> None:
        self._selected.clear()
        self._anchor = -1
        self.query_one('#stat-result', Static).update('Calculated: ---')
        self._refresh_stats()
        self._load_data()

    def _refresh_stats(self) -> None:
        lower = sum(s.excl for s in self._snapshots if s.subvol_id in self._selected)
        n     = len(self._selected)
        self.query_one('#stat-n',     Static).update(f'Selected: {n}')
        self.query_one('#stat-lower', Static).update(f'Lower bound: {_fmt(lower)}')

    # ── Calculation ───────────────────────────────────────────────────────────

    def action_calculate(self) -> None:
        if not self._selected:
            self.query_one('#msg', Static).update('No snapshots selected.')
            return
        self._calculate()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != 'btn-calc':
            return
        if not self._selected:
            self.query_one('#msg', Static).update('No snapshots selected.')
            return
        self._calculate()

    @work(thread=True)
    def _calculate(self) -> None:
        app      = self.app
        excl_sum = sum(s.excl for s in self._snapshots if s.subvol_id in self._selected)

        # resolve widget refs on main thread to avoid cross-thread DOM access
        w_msg    = self.query_one('#msg',         Static)
        w_result = self.query_one('#stat-result', Static)
        w_hint   = self.query_one('#stat-hint',   Static)
        w_btn    = self.query_one('#btn-calc',    Button)

        try:
            fingerprint  = btrfs.get_fingerprint()
            subvol_count = btrfs.get_subvol_count()
            estimated    = cache.get_estimated_duration(fingerprint, subvol_count)
        except Exception:
            fingerprint = subvol_count = None
            estimated   = None

        eta_hint = f'~{_fmt_eta(estimated)}' if estimated else 'may take several minutes'

        def msg(text: str) -> None:
            app.call_from_thread(w_msg.update, text)

        app.call_from_thread(self._set_spinner, True)
        app.call_from_thread(setattr, w_btn, 'disabled', True)
        try:
            t0         = time.monotonic()
            calculated = btrfs.calc_freed_space(
                list(self._selected), progress_cb=msg, eta_hint=eta_hint
            )
            duration   = time.monotonic() - t0
            if fingerprint and subvol_count:
                cache.save_timing(fingerprint, duration, subvol_count)
            delta      = calculated - excl_sum
            sign       = '+' if delta >= 0 else '-'
            result_txt = (
                f'Calculated: {_fmt(calculated)}'
                f'  (Σexcl: {_fmt(excl_sum)}, Δ: {sign}{_fmt(abs(delta))})'
            )
            app.call_from_thread(w_result.update, result_txt)
            app.call_from_thread(w_hint.update, '⚠ Actual freed space will typically be 5–8 % higher (btrfs metadata overhead not included in calculation)')
            msg(f'Done – will free: {_fmt(calculated)}')
        except Exception as e:
            msg(f'Error: {e}')
        finally:
            app.call_from_thread(self._set_spinner, False)
            app.call_from_thread(setattr, w_btn, 'disabled', False)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_spinner(self, visible: bool) -> None:
        self.query_one('#spinner', LoadingIndicator).display = visible

    def action_quit(self) -> None:
        self.app.exit()


# ─── App ──────────────────────────────────────────────────────────────────────

class BtrFreeApp(App):
    TITLE = 'btr-free'

    def on_mount(self) -> None:
        self.push_screen(GroupSelectScreen())


def main() -> None:
    BtrFreeApp().run()
