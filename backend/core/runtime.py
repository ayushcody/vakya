"""One object that stands for the running backend: the `b` that the phone and web-call code passes around, and the `env` the
tests fake things on.

Attribute reads and writes go to the module that owns the name (`runtime.turn_voice` is `turn_pipeline.turn_voice`,
`runtime.local_db` is `state.local_db`), looked up at call time, so a replacement made through the runtime (a test's
`monkeypatch.setattr(env, "turn_voice", fake)`) is seen by every caller of that module. The modules are imported on first
use, because routers load this one while the services they point to are still being imported.
"""

import importlib
import importlib.util
from collections.abc import Mapping

# Where the pipeline's moving parts live, first match wins. `backend` itself (the app and its wiring) comes last.
OWNERS = (
    "core.state", "core.engines", "core.ratelimit", "core.deps",
    "services.storage", "services.synthesis", "services.asr", "services.voice_store", "services.speaking",
    "services.voice_clone", "services.agent_store", "services.llm_limits", "services.knowledge", "services.agent_prompt",
    "services.turn_pipeline", "services.turn_stream",
    "routers.system", "routers.auth", "routers.voices", "routers.agents", "routers.documents", "routers.turns",
    "routers.warmup", "backend",
)


def _modules():
    for name in OWNERS:
        if importlib.util.find_spec(name) is None:
            continue
        yield importlib.import_module(name)


class _Namespace(Mapping):
    """What `runtime.__dict__` shows: every name the backend has, with its current value. A tool that saves and restores an
    attribute by reading the instance dictionary (pytest's `monkeypatch.setattr`) then finds the real value to put back."""

    def __getitem__(self, name):
        try:
            return getattr(runtime, name)
        except AttributeError:
            raise KeyError(name) from None

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0


class Runtime:
    """Reads and writes go to the module that owns the name; a name nothing owns is kept on the runtime itself."""

    _hidden: set = set()     # names deleted on purpose (a test removing a hook to exercise the fallback)
    _extra: dict = {}        # names nothing owns (a test hanging a helper on the backend)

    @property
    def __dict__(self):   # noqa: A003
        return _Namespace()

    def __getattr__(self, name: str):
        if name.startswith("__") or name in ("_hidden", "_extra"):
            raise AttributeError(name)
        if name in self._hidden:
            raise AttributeError(name)
        if name in self._extra:
            return self._extra[name]
        for mod in _modules():
            if hasattr(mod, name):
                return getattr(mod, name)
        raise AttributeError(f"the backend has no '{name}'")

    def __setattr__(self, name: str, value) -> None:
        self._hidden.discard(name)
        for mod in _modules():
            if name in vars(mod):
                setattr(mod, name, value)
                return
        self._extra[name] = value

    def __delattr__(self, name: str) -> None:
        if name in self._extra:
            del self._extra[name]
        elif hasattr(self, name):
            self._hidden.add(name)
        else:
            raise AttributeError(name)


runtime = Runtime()
