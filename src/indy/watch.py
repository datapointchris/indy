"""The filesystem half of `indy watch`: which paths changed, batched and queued.

Knows nothing about the index. service.watch() decides what each path means for it.
"""

import threading
from collections import OrderedDict
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from pathlib import Path

from watchfiles import watch

# Quiet time before a burst of events is handed over. An editor's atomic save writes a
# temporary file and renames it over the original, and both land well inside this.
QUIET_MS = 300

# How long a watch waits for an event before handing over an empty batch instead.
IDLE_MS = 1000


class WorkQueue:
    """Paths waiting to be indexed, each at most once, newest first.

    A save is what someone is about to search for, so it goes ahead of the backlog a catch-up
    scan queued. A path already waiting moves to the front rather than repeating.
    """

    def __init__(self) -> None:
        self._paths: OrderedDict[Path, None] = OrderedDict()
        self._ready = threading.Condition()

    def push_newest(self, paths: Iterable[Path]) -> None:
        with self._ready:
            for path in paths:
                self._paths[path] = None
                self._paths.move_to_end(path, last=False)
            self._ready.notify()

    def push_oldest(self, paths: Iterable[Path]) -> None:
        with self._ready:
            for path in paths:
                self._paths.setdefault(path, None)
            self._ready.notify()

    def pop(self, timeout: float) -> Path | None:
        """The newest waiting path, or None if none arrives within timeout."""
        with self._ready:
            if not self._paths:
                self._ready.wait(timeout)
            return self._paths.popitem(last=False)[0] if self._paths else None

    def __len__(self) -> int:
        with self._ready:
            return len(self._paths)


def watch_changes(roots: list[Path], keep: Callable[[Path], bool], stop: threading.Event) -> Iterator[set[Path]]:
    """Each batch of changed paths under roots that keep accepts, until stop is set.

    A batch is handed over once QUIET_MS pass with no further event, so a save is one batch.
    An empty batch is handed over after IDLE_MS with none. The first batch of either kind
    comes only once every directory under roots is registered, which can take many seconds
    over a large tree. From then on no change is missed, and a change before then may be.
    """
    batches = watch(
        *roots,
        watch_filter=lambda _change, path: keep(Path(path)),
        step=QUIET_MS,
        stop_event=stop,
        rust_timeout=IDLE_MS,
        yield_on_timeout=True,
        ignore_permission_denied=True,
    )
    for changes in batches:
        yield {Path(path) for _change, path in changes}
