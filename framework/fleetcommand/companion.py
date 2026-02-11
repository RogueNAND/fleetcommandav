import asyncio
import functools
import itertools
import json
import re
import traceback
import websockets
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Hashable, List, Optional

CONNECTION_STATUS_OK = ('good', 'ok')

@dataclass
class _DebounceState:
    last_args: tuple = field(default_factory=tuple)
    last_kwargs: dict = field(default_factory=dict)
    waiters: List[asyncio.Future] = field(default_factory=list)
    worker_task: Optional[asyncio.Task] = None
    next_allowed_time: float = 0.0

@dataclass
class _RepeatState:
    task: Optional[asyncio.Task] = None
    future: Optional[asyncio.Future] = None

class Event:

    def __init__(self, connection=None, var=None, value=None, last_vars=None):
        self.connection: str = connection
        self.variable: str = var
        self.value = value
        self.last = (last_vars or {}).get(var, None)
        self.lasts: dict[str, dict] = last_vars or defaultdict(dict)

    def __repr__(self):
        return f"Event<{self.connection}.{self.variable} | {self.value}>"

iteration_generators = {
    'page': lambda b: b.page,
    'col': lambda b: b.col,
    'row': lambda b: b.row,
    'manual': lambda b: b.iteration,
}

class Companion:

    class Button:

        def __init__(self, companion, data: dict, compute_iterators=True):
            self._last_data = data

            self.id = self.__class__.__name__
            self.companion: "Companion" = companion
            self.control_id: str = data['controlId']
            self.page: int
            self.row: int
            self.col: int
            self.iterator: str
            self.iteration: int = data['options'].get('manualIteration') or 0
            self.update_control(data)

            companion.companion_buttons[self.control_id] = self
            companion.companion_buttons_by_page_id_rowcol[self.page][self.id][(self.row, self.col)] = self

            if compute_iterators:
                companion.recompute_button_iterations(self.page, apply_button_id=self.id)

        def update_control(self, data):
            _options = data['options']
            _location = data['location']
            self.page: int = int(_location['pageNumber'])
            self.row: int = int(_location['row'])
            self.col: int = int(_location['column'])
            self.iterator = _options['iterator'] or 'manual'
            if self.iterator == 'manual':
                self.iteration: int = _options.get('manualIteration') or 0

        @property
        def page_name(self):
            return self.companion.var("internal", f"page_number_{self.page}_name")

        async def on_init(self):
            pass

        async def on_down(self):
            pass

        async def on_up(self):
            pass

        async def on_rotate(self, direction: bool):
            pass

        async def set_bg_color(self, r, g, b):
            await self.companion.action("internal", "bgcolor", options={"color": str(self._rgb_to_int(r, g, b)), "location_target": "text", "location_text": f"{self.page}/{self.row}/{self.col}"}, wait=False)

        async def set_text(self, text: str):
            await self.companion.action("internal", "button_text", options={"label": str(text), "location_target": "text", "location_text": f"{self.page}/{self.row}/{self.col}"}, wait=False)

        async def set_text_color(self, r, g, b):
            await self.companion.action("internal", "textcolor", options={"color": str(self._rgb_to_int(r, g, b)), "location_target": "text", "location_text": f"{self.page}/{self.row}/{self.col}"}, wait=False)

        async def trigger_press(self, force=False):
            await self.companion.action("internal", "button_press", options={"location_target": "this", "location_text": "", "location_expression": "", "force": force})

        async def trigger_press_release(self, force):
            await self.companion.action("internal", "button_pressrelease", options={"location_target": "this", "location_text": "", "location_expression": "", "force": force})

        async def trigger_release(self, force):
            await self.companion.action("internal", "button_release", options={"location_target": "this", "location_text": "", "location_expression": "", "force": force})

        async def trigger_rotate_left(self):
            await self.companion.action("internal", "button_rotate_left", options={"location_target": "this", "location_text": "", "location_expression": ""})

        async def trigger_rotate_right(self):
            await self.companion.action("internal", "button_rotate_right", options={"location_target": "this", "location_text": "", "location_expression": ""})

        @staticmethod
        def _rgb_to_int(r, g, b) -> int:
            r = int(round(max(0, min(1, r) * 255)))
            g = int(round(max(0, min(1, g) * 255)))
            b = int(round(max(0, min(1, b) * 255)))
            res = (r << 16) | (g << 8) | b
            return res

        @classmethod
        def _build_classes(cls, classes: dict[str, type["Companion.Button"]]):
            # Add self
            class_name = cls.__name__
            if class_name in classes:
                raise RuntimeError(f"Button {class_name} already exists")

            # Recursively add subclasses
            classes[class_name] = cls
            for subclass in cls.__subclasses__():
                subclass._build_classes(classes)

    def __init__(self, url="ws://127.0.0.1:16621"):
        self.url = url
        self.variables = {}
        self._cast_connections = set()

        # handler registries
        self._var_change_handlers = defaultdict(list)      # (connection, type, key) -> list[handlers]
        self._connect_handlers = defaultdict(list)         # connection -> handlers
        self._connect_state = {}                           # connection -> last known status ("good", etc.)
        self._snippet_regen_task = None

        # custom button index
        self.button_classes = {}
        self.companion_buttons: dict[str, "Companion.Button"] = {}  # control_id -> button
        # Internally used for mapping out iterations
        self.companion_buttons_by_page_id_rowcol = defaultdict(lambda: defaultdict(dict))  # page -> button_id -> (row, col) -> button

        # requests and communication
        self._pending = {}
        self._id_counter = itertools.count(10)
        self._send_queue = asyncio.Queue()
        self._run_queue = asyncio.Queue()
        self._ws = None

        # running tasks
        self._sender_task = None
        self._receiver_task = None
        self._run_task = None



    """ Event Trigger Decorators """

    def on_change(self, connection, *, variable=None, prefix=None, suffix=None, regex=None):
        options = [variable, prefix, suffix, regex]
        if sum(1 for o in options if o is not None) != 1:
            raise ValueError("on_change requires exactly one of: variable, prefix, suffix, regex")

        def decorator(func):
            if variable:
                self._var_change_handlers[(connection, "variable", variable)].append(func)
            elif prefix:
                self._var_change_handlers[(connection, "prefix", prefix)].append(func)
            elif suffix:
                self._var_change_handlers[(connection, "suffix", suffix)].append(func)
            elif regex:
                compiled = re.compile(regex)
                self._var_change_handlers[(connection, "regex", compiled)].append(func)
            return func

        return decorator

    def on_connect(self, connection):
        """
        Fires when a Companion connection enters status 'good'.
        Triggers on:
        - initial snapshot
        - reconnect inside Companion
        - Python websocket reconnect
        - status flipping to 'good' via variable updates
        """

        def decorator(func):
            self._connect_handlers[connection].append(func)
            return func

        return decorator



    """ Utility Decorators """

    def requires(self, *connections):
        """
        Decorator that short-circuits the wrapped function unless
        all specified connections have status 'ok'.

        Usage:
            @companion.requires("vmix")
            async def handler(...): ...

            @companion.requires("vmix", "atem")
            async def handler(...): ...

            @companion.requires(["vmix", "atem"])
            async def handler(...): ...
        """

        if len(connections) == 1 and isinstance(connections[0], (list, tuple, set, frozenset)):
            required = tuple(connections[0])
        else:
            required = tuple(connections)

        def decorator(func):
            @functools.wraps(func)
            async def wrapper(arg):
                if not self._are_connections_ready(required):
                    print(f"⏭ requires skipped {func.__name__}, missing: {','.join(required)}")  # TODO: debug log
                    return None
                if asyncio.iscoroutinefunction(func):
                    return await func(arg)
                else:
                    return func(arg)

            # Optionally store metadata for introspection
            wrapper._companion_requires = required

            return wrapper

        return decorator

    @staticmethod
    def debounce(*, min_delay: float = 0.0, group_by: Optional[str] = None,) -> Callable:
        """
        Debounce/throttle an async function.

        Per key (see `group_by`):

          - Only one execution at a time (no overlapping awaits).
          - At least `min_delay` seconds between executions.
          - While execution is pending or waiting for delay:
              * Calls update the latest args/kwargs for that key.
              * All callers awaiting in that window receive the *same* result
                from the next execution.

        `group_by`:
          - None: all calls share a single debounce bucket.
          - str: name of a keyword argument to use as the key.
                 (Callers must pass that argument by name.)
        """

        def decorator(fn: Callable):
            if not asyncio.iscoroutinefunction(fn):
                raise TypeError("@debounce_async can only wrap async functions")

            states: Dict[Hashable, _DebounceState] = {}
            states_lock = asyncio.Lock()

            async def _worker(key: Hashable):
                loop = asyncio.get_running_loop()

                while True:
                    async with states_lock:
                        state = states.get(key)
                        if state is None or not state.waiters:
                            # Nothing more to do for this key
                            if state is not None:
                                state.worker_task = None
                            states.pop(key, None)
                            return

                        # Snapshot latest args/kwargs and current waiters
                        args = state.last_args
                        kwargs = state.last_kwargs
                        waiters = state.waiters
                        state.waiters = []

                        # Compute how long until we're allowed to run
                        now = loop.time()
                        delay = max(0.0, state.next_allowed_time - now)

                    # Sleep outside the lock
                    if delay > 0.0:
                        await asyncio.sleep(delay)

                    # Execute the function
                    try:
                        result = await fn(*args, **kwargs)
                    except Exception as e:
                        for fut in waiters:
                            if not fut.done():
                                fut.set_exception(e)
                    else:
                        for fut in waiters:
                            if not fut.done():
                                fut.set_result(result)

                    # Update next allowed time
                    async with states_lock:
                        state = states.get(key)
                        if state is None:
                            # State removed while we were running; stop
                            return
                        state.next_allowed_time = loop.time() + min_delay

            @functools.wraps(fn)
            async def wrapper(*args, **kwargs) -> Any:
                loop = asyncio.get_running_loop()
                fut: asyncio.Future = loop.create_future()

                # Determine key
                if group_by is None:
                    key: Hashable = "__default__"
                else:
                    if group_by not in kwargs:
                        raise ValueError(
                            f"debounce_async(group_by='{group_by}') requires that "
                            f"argument to be passed as a keyword argument"
                        )
                    key = kwargs[group_by]

                async with states_lock:
                    state = states.get(key)
                    if state is None:
                        state = _DebounceState()
                        states[key] = state

                    # Update latest call data and register this caller
                    state.last_args = args
                    state.last_kwargs = kwargs
                    state.waiters.append(fut)

                    # Ensure worker task exists
                    if state.worker_task is None or state.worker_task.done():
                        state.worker_task = loop.create_task(_worker(key))

                # Each caller awaits the result of the execution it’s tied to
                return await fut

            return wrapper

        return decorator

    @staticmethod
    def repeat_with_reset(*, attempts: int = 3, delay: float = 0.1, group_by: Optional[str] = None,) -> Callable:
        """
        Repeat an async function a fixed number of times, resetting if called again.

        Per key (see `group_by`):

          - Each call starts a sequence:
              * Call the wrapped function up to `attempts` times.
              * Wait `delay` seconds between attempts.
              * Attempts run regardless of success or failure.
          - If a *new* call arrives for the same key while a sequence is running:
              * Cancel the existing sequence's task.
              * Previous caller's await gets asyncio.CancelledError.
              * Start a new sequence with the new args/kwargs.

          - The caller's await resolves to:
              * The result of the **first successful attempt** (and ignores later ones), or
              * The **last exception** if all attempts fail, or
              * asyncio.CancelledError if superseded by a later call.

        `group_by`:
          - None: all calls share a single repeat state.
          - str: name of a keyword argument to use as the key.
                 (Callers must pass that argument by name.)
        """

        if attempts < 1:
            raise ValueError("attempts must be >= 1")

        def decorator(fn: Callable):
            if not asyncio.iscoroutinefunction(fn):
                raise TypeError("@repeat_with_reset can only wrap async functions")

            states: Dict[Hashable, _RepeatState] = {}
            states_lock = asyncio.Lock()

            async def _run_sequence(key: Hashable, args: tuple, kwargs: dict):
                nonlocal states

                async with states_lock:
                    state = states.get(key)
                    future = state.future if state else None

                for i in range(attempts):
                    # If the future was cancelled or resolved externally, stop
                    if future is None or future.done():
                        return

                    try:
                        result = await fn(*args, **kwargs)

                        # First successful attempt wins for the caller
                        if not future.done():
                            future.set_result(result)
                        # Keep going with remaining attempts for reliability,
                        # but don't touch the future again.
                    except asyncio.CancelledError:
                        # Sequence cancelled because a newer call started
                        return
                    except Exception as e:
                        # If this was the last attempt and we never succeeded:
                        if i == attempts - 1 and not future.done():
                            future.set_exception(e)

                    # Delay between attempts, but not after the last one
                    if i < attempts - 1:
                        await asyncio.sleep(delay)

                # Cleanup if this task is still the active one
                async with states_lock:
                    state = states.get(key)
                    if state and state.task is asyncio.current_task():
                        state.task = None
                        if state.future is None or state.future.done():
                            states.pop(key, None)

            @functools.wraps(fn)
            async def wrapper(*args, **kwargs) -> Any:
                loop = asyncio.get_running_loop()
                caller_future: asyncio.Future = loop.create_future()

                # Determine key
                if group_by is None:
                    key: Hashable = "__default__"
                else:
                    if group_by not in kwargs:
                        raise ValueError(
                            f"repeat_with_reset(group_by='{group_by}') requires that "
                            f"argument to be passed as a keyword argument"
                        )
                    key = kwargs[group_by]

                async with states_lock:
                    state = states.get(key)
                    if state is None:
                        state = _RepeatState()
                        states[key] = state

                    # Cancel any existing sequence for this key
                    if state.task is not None and not state.task.done():
                        state.task.cancel()
                        # Previous caller gets cancelled/superseded
                        if state.future is not None and not state.future.done():
                            state.future.set_exception(asyncio.CancelledError())

                    # This call becomes the current one for this key
                    state.future = caller_future
                    state.task = loop.create_task(_run_sequence(key, args, kwargs))

                return await caller_future

            return wrapper

        return decorator

    def _are_connections_ready(self, required_connections):
        """
        Return True if all required connections have status 'ok'.
        """
        for conn in required_connections:
            status = self._connect_state.get(conn)
            if status not in CONNECTION_STATUS_OK:
                return False
        return True



    """ Companion Callbacks """

    async def action(self, connection, action_id, options=None, wait=True):
        try:
            return await self._call(
                "runConnectionAction",
                wait=wait,
                connectionName=connection,
                actionId=action_id,
                options=options or {},
                extras={"surfaceId": "python-direct"}
            )
        except TimeoutError as e:
            print(f"⏱️  Timeout: {e}")

    @staticmethod
    async def action_multi(*coroutines, allow_partial: bool = False):
        """
        Run multiple Companion actions concurrently.

        *coroutines: One or more awaitable objects (usually companion.action(...)).
        allow_partial: If True, all actions are awaited even if some fail.
                        Exceptions are returned as items in the result list
                        instead of cancelling the other actions.

        Returns:
            List of results in the same order as the provided coroutines.
            When allow_partial=True, failed items will be Exception instances.
        """

        if not coroutines:
            return []

        if allow_partial:
            # Don't cancel other actions if one fails
            results = await asyncio.gather(*coroutines, return_exceptions=True)
            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"⚠️ action_multi item {idx} failed: {result}")
            return results

        else:
            return await asyncio.gather(*coroutines)

    async def _call(self, method, wait=True, **params):
        if not self._ws:
            raise RuntimeError("WebSocket not connected yet")

        req_id = next(self._id_counter)
        message = {"id": req_id, "method": method, "params": params}

        if wait:
            fut = asyncio.get_event_loop().create_future()
            self._pending[req_id] = fut

            await self._send_queue.put(message)

            try:
                return await asyncio.wait_for(fut, timeout=1)
            except asyncio.TimeoutError:
                self._pending.pop(req_id, None)
                raise TimeoutError(f"Timeout waiting for response to '{method}'")
        else:
            self._pending[req_id] = None
            return await self._send_queue.put(message)

    async def _dispatch(self, connection, updates, last_vars):
        for var, value in updates.items():
            for (conn, match_type, key), handlers in self._var_change_handlers.items():
                if conn != connection:
                    continue
                matched = False
                if match_type == "variable" and var == key:
                    matched = True
                elif match_type == "prefix" and var.startswith(key):
                    matched = True
                elif match_type == "suffix" and var.endswith(key):
                    matched = True
                elif match_type == "regex" and key.match(var):
                    matched = True

                if matched:
                    for h in handlers:
                        event = Event(
                            connection=connection,
                            var=var,
                            value=value,
                            last_vars=last_vars,
                        )
                        asyncio.create_task(self._safe_handler_call(h, event))

    @staticmethod
    async def _safe_handler_call(handler, event: Event):
        """Run handler safely in its own task."""
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        except Exception:
            handler_name = getattr(handler, "__qualname__", getattr(handler, "__name__", repr(handler)))
            print(f"⚠️ Handler error in {handler_name}")
            try:
                print(f"   Payload: {repr(event)}")
            except Exception:
                print("   Payload: <unrepresentable>")

            print("   Traceback:")
            traceback.print_exc()



    """ Library API """

    def var(self, connection, var, default=None):
        return self.variables.get(connection, {}).get(var, default)

    def enable_cast(self, *connections):
        """
        Enable smart casting for specific connection labels, or all connections if left empty

        When enabled, string values that look like booleans or numbers will
        be converted to bool/int/float on updates.
        """

        if not connections:
            self._cast_connections = None

        if self._cast_connections is not None:
            for connection in connections:
                self._cast_connections.add(connection)

                # Cast any existing variables
                for k, v in self.variables.get(connection, {}).items():
                    self.variables[connection][k] = self._smart_cast(v)

    async def _update_variables(self, variables: dict[str, dict], dispatch=True):
        last_vars = self.variables.copy()

        for connection, vars_dict in variables.items():

            # Update connection status
            for var, value in vars_dict.items():
                if var.startswith("connection_") and var.endswith("_status"):
                    conn_name = var[len("connection_"):-len("_status")]
                    self._handle_connection_status_update(conn_name, value, var, last_vars)

            # Cast variable (if applicable)
            if (self._cast_connections is None) or (connection in self._cast_connections):
                for var, value in vars_dict.items():
                    vars_dict[var] = self._smart_cast(value)

            self.variables.setdefault(connection, {}).update(vars_dict)

        if dispatch:
            for connection, vars_dict in variables.items():
                await self._dispatch(connection, vars_dict, last_vars)

    @staticmethod
    def _smart_cast(value):
        """
        Conservative smart cast:
        - leave non-strings alone
        - 'true'/'false' (case-insensitive) -> bool
        - '1'/'0' -> int
        - pure digit / simple float strings -> int/float
        Everything else stays as-is.
        """
        if value is None:
            return value
        if not isinstance(value, str):
            return value

        s = value.strip()
        if not s:
            return value

        lower = s.lower()

        # Booleans
        if lower == "true":
            return True
        if lower == "false":
            return False

        # Simple integer (no sign, no decimal)
        if re.fullmatch(r"\d+", s):
            try:
                return int(s)
            except ValueError:
                return value

        # Simple float (optional leading digits, one dot, optional trailing digits)
        if re.fullmatch(r"\d+\.\d+|\d+\.\d*|\.\d+", s):
            try:
                return float(s)
            except ValueError:
                return value

        return value

    def _handle_connection_status_update(self, connection, new_status, var_name, last_vars):
        """Internal handler for connection status variables."""
        old_status = self._connect_state.get(connection)

        # Only fire when entering "ok"
        if new_status in CONNECTION_STATUS_OK and old_status not in CONNECTION_STATUS_OK:
            self._connect_state[connection] = new_status
            for h in self._connect_handlers.get(connection, []):
                event = Event(
                    connection=connection,
                    var=var_name,
                    value=new_status,
                    last_vars=last_vars,
                )
                asyncio.create_task(self._safe_handler_call(h, event))
        else:
            self._connect_state[connection] = new_status



    """ Communication Loops """

    async def _send_loop(self):
        while self._ws:
            msg = await self._send_queue.get()
            try:
                await self._ws.send(json.dumps(msg))
            except Exception as e:
                fut = self._pending.pop(msg["id"], None)
                if fut and not fut.done():
                    fut.set_exception(e)

    async def _recv_loop(self):
        async for raw in self._ws:
            data = json.loads(raw)

            # resolve pending futures
            if "id" in data and data["id"] in self._pending:
                fut = self._pending.pop(data["id"])
                if fut is None:
                    continue
                if "result" in data:
                    fut.set_result(data["result"])
                elif "error" in data:
                    fut.set_exception(RuntimeError(data["error"]))
                else:
                    fut.set_result(None)
                continue

            # variable snapshot
            if data.get("id") == 1 and "result" in data:
                result = data["result"]
                if isinstance(result, dict):
                    await self._update_variables(result, dispatch=False)
                    self.generate_snippets()
                print(f"📥 Cached variables for {len(self.variables)} connections")
                continue

            # controls snapshot
            if data.get("id") == 2 and "result" in data:
                result = data["result"]
                await self._build_buttons(result)
                continue

            # variable change
            if data.get("event") == "variablesChanged":
                payload = data.get("payload", {})
                variables_created = False
                for connection, updates in payload.items():
                    # check if any new keys were added, for generating snippets
                    existing_vars = self.variables.setdefault(connection, {})
                    variables_created = bool(set(updates.keys()) - set(existing_vars.keys()))

                await self._update_variables(payload)

                if variables_created:
                    print("📝 Detected new variables — regenerating snippets")
                    self.generate_snippets()

            # interaction events
            elif data.get("event") == "interaction":
                payload = data.get("payload", {})
                control_id = payload.get("controlId")
                companion_button = self.companion_buttons.get(control_id)
                if not companion_button:
                    continue

                event_type = payload.get("event")
                value = payload.get("value")

                if event_type == "press":
                    if value:
                        await self._run_queue.put(companion_button.on_down())
                    else:
                        await self._run_queue.put(companion_button.on_up())
                elif event_type == "rotate":
                    await self._run_queue.put(companion_button.on_rotate(value))
                continue

            elif data.get("event") == "controlReplaced":
                if payload := data.get("payload"):
                    await self._replace_button(
                        old_button_id=payload.get("oldControlId"),
                        new_button_data=payload.get("newControl")
                    )

            elif data.get("event") == "controlUpdated":
                if payload := data.get("payload"):
                    control_id = payload['controlId']
                    existing_button = self.companion_buttons.get(control_id)

                    if existing_button.id == payload.get('options', {}).get('pythonClassId'):
                        existing_button.update_control(payload)
                    else:
                        await self._replace_button(control_id, payload)

            # error returned
            elif msg := data.get('error'):
                print(f"❌ Companion error: {msg}")

            # unknown
            else:
                print("🔔 Unknown event:", data)

    async def _replace_button(self, old_button_id: str, new_button_data: dict):
        # Delete old button
        if old_button := self.companion_buttons.get(old_button_id):
            del self.companion_buttons[old_button.control_id]
            del self.companion_buttons_by_page_id_rowcol[old_button.page][old_button.id][(old_button.row, old_button.col)]
            self.recompute_button_iterations(old_button.page, apply_button_id=old_button.id)

        if new_control := new_button_data:
            await self._add_button(new_control, compute_iterators=True)

    async def _run_loop(self):
        """
        Drain queued interaction coroutines and schedule them immediately.
        This does NOT wait for them to finish; it just fires them off.
        """
        while True:
            coro = await self._run_queue.get()
            try:
                # Fire-and-forget: run concurrently, don't block this loop
                asyncio.create_task(coro)
            except Exception:
                print("❌ Failed to schedule interaction coroutine")
            finally:
                self._run_queue.task_done()

    async def _build_buttons(self, controls: list[dict[str, Any]]):
        self.companion_buttons.clear()
        self.companion_buttons_by_page_id_rowcol.clear()

        for data in controls:
            await self._add_button(data, compute_iterators=False)

        for page in self.companion_buttons_by_page_id_rowcol.keys():
            self.recompute_button_iterations(page)

    async def _add_button(self, data: dict[str, Any], compute_iterators=True):
        python_id = data.get('options', {}).get('pythonClassId')
        if button_class := self.button_classes.get(python_id):
            button = button_class(self, data, compute_iterators=compute_iterators)
            await button.on_init()
        else:
            print(f"🔘 Button [{python_id}] not found, ignoring")

    def recompute_button_iterations(self, page: int, apply_button_id: str = None):

        for button_id, page_locations in self.companion_buttons_by_page_id_rowcol[page].items():
            if apply_button_id is not None and apply_button_id != button_id:
                continue

            rows_cols = sorted(page_locations.keys())
            cols_rows = sorted((col, row) for row, col in page_locations.keys())

            i = 0
            for row_col in rows_cols:
                if (button := page_locations[row_col]).iterator == 'pagelrtb':
                    button.iteration = i
                    i += 1
                else:
                    button.iteration = iteration_generators.get(button.iterator, lambda b: b.iteration)(button)

            i = 0
            for col_row in [(row, col) for col, row in cols_rows]:
                if (button := page_locations[col_row]).iterator == 'pagetblr':
                    button.iteration = i
                    i += 1



    """ Code Snippets """

    def generate_snippets(self, delay=2.0):
        if self._snippet_regen_task and not self._snippet_regen_task.done():
            return

        async def regen():
            await asyncio.sleep(delay)
            await self._generate_snippets()

        self._snippet_regen_task = asyncio.create_task(regen())

    async def _generate_snippets(self):

        snippets = await self._generate_variable_snippets() | await self._generate_action_snippets(await self._call("queryActions"))

        snippet_path = Path("/workspace-vscode/python.code-snippets")
        snippet_path.parent.mkdir(parents=True, exist_ok=True)

        def write_file():
            with snippet_path.open("w") as f:
                json.dump(snippets, f, indent=2)
            print(f"🧩 Snippets updated ({len(snippets)} total)")

        await asyncio.to_thread(write_file)

    async def _generate_variable_snippets(self):
        variables = {
            connection: list(vars_dict.keys())
            for connection, vars_dict in self.variables.items()
            if vars_dict
        }

        snippets = {}

        # Add variable update handler snippets
        for connection, variables in variables.items():
            joined_vars = ",".join(variables)
            snippets[f"companion_on_change_{connection}"] = {
                "prefix": f"@companion.on_change_{connection}",
                "body": [
                    f"@companion.on_change(\"{connection}\", variable=\"${{1|{joined_vars}|}}\")\n"
                    f"async def ${{4:handler_name}}(payload):\n"
                    f"    ${{5:pass # TODO: handle change}}"
                ],
                "description": f"on_change decorator with autocomplete for {connection}"
            }
            snippets[f"companion_var_{connection}"] = {
                "prefix": f"companion.var_{connection}",
                "body": [
                    f"companion.var(\"{connection}\", var=\"${{1|{joined_vars}|}}\", default=${{2:None}})"
                ],
                "description": f"variable reference with autocomplete for {connection}"
            }

        # Add button handler snippets
        snippets["companion_button"] = {
            "prefix": f"companion.Button",
            "body": [
                "class ${1:button_name}(companion.Button):\n\n"
                "    async def on_init(self):\n"
                "        pass\n\n"
                "    async def on_down(self):\n"
                "        pass\n\n"
                "    async def on_up(self):\n"
                "        pass\n\n"
                "    async def on_rotate(self, direction: bool):\n"
                "        pass\n"
            ],
            "description": f"Software defined companion button"
        }

        return  snippets

    @staticmethod
    async def _generate_action_snippets(actions_json: dict):
        """
        Generate VSCode snippets for all available Companion actions.
        Each snippet lets you call: companion.action("<connection>", "<action>", options={...})
        """

        snippets = {}

        connections = sorted(actions_json.keys())
        if connections:
            # VSCode choice list: ${1|vmix,atem,internal|}
            connection_choices = ",".join(connections)

            snippets["companion_on_connect"] = {
                "prefix": "@companion.on_connect",
                "body": [
                    f"@companion.on_connect(\"${{1|{connection_choices}|}}\")\n"
                    f"async def ${{2:handler_name}}(connection):\n"
                    f"    ${{3:pass}}"
                ],
                "description": "on_connect decorator with connection dropdown",
            }

            snippets["companion_requires"] = {
                "prefix": "@companion.requires",
                "body": [
                    f"@companion.requires(\"${{1|{connection_choices}|}}\")"
                ],
                "description": "requires decorator with connection dropdown",
            }

            snippets["companion_cast"] = {
                "prefix": "companion.enable_cast",
                "body": [
                    "# Auto-cast variables for connection(s) (leave empty for all)\n"
                    f"companion.enable_cast(\"${{1|{connection_choices}|}}\")"
                ],
                "description": "set connection(s) to auto-cast variables",
            }

            snippets["companion_action_multi"] = {
                "prefix": "companion.action_multi",
                "body": [
                    "# Run multiple simultaneous actions (or any coroutine)\n"
                    f"companion.action_multi(\n    ${{1:}}\n)"
                ],
                "description": "Run multiple simultaneous actions",
            }

            snippets["companion_debounce"] = {
                "prefix": "@companion.debounce",
                "body": [
                    f"@companion.debounce(min_delay=\"${{1:}}\", group_by=\"${{2:}}\")  # Buffer function calls"
                ],
                "description": "Buffer function calls",
            }

            snippets["companion_repeat_with_reset"] = {
                "prefix": "@companion.repeat_with_reset",
                "body": [
                    f"@companion.repeat_with_reset(attempts=${{1:}}, delay=${{2:}}, group_by=\"${{3:}}\")  # Repeat function after initial call"
                ],
                "description": "Repeat function after initial call",
            }

        for connection, actions in actions_json.items():
            for action_id, action_def in actions.items():
                # label = action_def.get("label", action_id)
                desc = action_def.get("description", "")
                options = action_def.get("options", [])

                # Build the options object body for the snippet
                option_lines = []
                tab_index = 1
                for opt in options:
                    opt_id = opt.get("id")
                    opt_type = opt.get("type", "textinput")
                    default = opt.get("default", "")
                    choices = opt.get("choices", [])

                    # Convert dropdowns to VSCode choice lists
                    if opt_type == "dropdown" and choices:
                        choice_str = ",".join(str(c["id"]).replace('true', 'True').replace('false', 'False') for c in choices)
                        line = f'"{opt_id}": "${{{tab_index}|{choice_str}|}}"'
                    elif opt_type == "checkbox":
                        line = f'"{opt_id}": ${{{tab_index}:False}}'
                    elif opt_type == "number":
                        line = f'"{opt_id}": ${{{tab_index}:{default if default != "" else 0}}}'
                    else:  # textinput or anything else
                        default_val = json.dumps(default)[1:-1]  # escape quotes safely
                        line = f'"{opt_id}": "${{{tab_index}:{default_val}}}"'

                    option_lines.append(line)
                    tab_index += 1

                options_block = ", ".join(option_lines) if option_lines else ""
                snippet_body = (
                    f'# {desc}\nawait companion.action("{connection}", "{action_id}", options={{\n    {options_block}\n}})'
                )

                snippets[f"{connection}.{action_id}"] = {
                    "prefix": [
                        f"companion.action_{connection} | {desc}",
                    ],
                    "body": [snippet_body],
                    "description": desc,
                }

        return snippets



    """ Main """

    async def run(self):
        for cls in self.Button.__subclasses__():
            cls._build_classes(self.button_classes)

        reconnect_delay = 1
        while True:
            try:
                async with websockets.connect(self.url) as ws:
                    self._ws = ws
                    print("✅ Connected to Companion WebSocketBridge")

                    self._sender_task = asyncio.create_task(self._send_loop())
                    self._receiver_task = asyncio.create_task(self._recv_loop())
                    self._run_task = asyncio.create_task(self._run_loop())

                    # initial variable snapshot
                    await self._send_queue.put({
                        "id": 1,
                        "method": "queryVariables"
                    })
                    await self._send_queue.put({
                        "id": 2,
                        "method": "queryCustomControls"
                    })

                    await asyncio.gather(self._sender_task, self._receiver_task, self._run_task)

            except (OSError, websockets.exceptions.ConnectionClosedError) as e:
                print(f"⚠️ Connection lost: {e}")
                await asyncio.sleep(min(reconnect_delay, 5))
                reconnect_delay = min(reconnect_delay + 1, 10)

            except Exception as e:
                print(f"❌ Unexpected error: {e}")
                await asyncio.sleep(min(reconnect_delay, 5))

            finally:
                if self._ws:
                    await self._ws.close()
                self._ws = None
                self._sender_task = None
                self._receiver_task = None
                self._run_task = None
