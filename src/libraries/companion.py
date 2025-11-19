import asyncio
import itertools
import json
import re
import traceback
import websockets
from collections import defaultdict
from pathlib import Path

CONNECTION_STATUS_OK = ('good', 'ok')

class Companion:
    def __init__(self, url="ws://127.0.0.1:16621"):
        self.url = url
        self.variables = {}

        # handler registries
        self._var_change_handlers = defaultdict(list)  # (connection, type, key) -> list[handlers]
        self._button_handlers = defaultdict(list)      # (page, x, y, type) -> list[handlers]
        self._connect_handlers = defaultdict(list)     # connection -> handlers
        self._connect_state = {}                       # connection -> last known status ("good", etc.)
        self._snippet_regen_task = None

        # requests and communication
        self._pending = {}
        self._id_counter = itertools.count(10)
        self._send_queue = asyncio.Queue()
        self._ws = None

        # running tasks
        self._sender_task = None
        self._receiver_task = None

    # ----------------------------------------------------------------------
    # Decorators
    # ----------------------------------------------------------------------

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
        regex = re.compile(pattern)
        def decorator(func):
            self._regex[connection].append((regex, func))
            return func
        return decorator

    def on_button_down(self, page, x, y):
        def decorator(func):
            self._button_handlers[(page, x, y, "down")].append(func)
            return func
        return decorator

    def on_button_up(self, page, x, y):
        def decorator(func):
            self._button_handlers[(page, x, y, "up")].append(func)
            return func
        return decorator

    def on_rotate(self, page, x, y):
        def decorator(func):
            self._button_handlers[(page, x, y, "rotate")].append(func)
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

    async def action(self, connection, action_id, options=None):
        return await self._call(
            "runConnectionAction",
            connectionName=connection,
            actionId=action_id,
            options=options or {},
            extras={"surfaceId": "python-direct"}
        )

    def var(self, connection, var, default=None):
        return self.variables.get(connection, {}).get(var, default)

    # ----------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------

    async def _query(self, path, **params):
        return await self._call("query", path=path, **params)

    async def _call(self, method, **params):
        if not self._ws:
            raise RuntimeError("WebSocket not connected yet")

        req_id = next(self._id_counter)
        fut = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut

        message = {"id": req_id, "method": method, "params": params}
        await self._send_queue.put(message)

        try:
            return await asyncio.wait_for(fut, timeout=1)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise RuntimeError(f"Timeout waiting for response to '{method}'")

    # ----------------------------------------------------------------------
    # Internal Dispatch
    # ----------------------------------------------------------------------

    async def _dispatch(self, connection, updates):
        for var, value in updates.items():
            if var.startswith("connection_") and var.endswith("_status"):
                connection_name = var[len("connection_"):-len("_status")]
                self._handle_connection_status_update(connection_name, value)
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
                        asyncio.create_task(self._safe_handler_call(h, (var, value)))

    def _handle_connection_status_update(self, connection, new_status):
        """Internal handler for connection status variables."""
        old_status = self._connect_state.get(connection)

        # Only fire when entering "ok"
        if new_status in CONNECTION_STATUS_OK and old_status not in CONNECTION_STATUS_OK:
            self._connect_state[connection] = new_status
            self._trigger_connect_handlers(connection)
        else:
            self._connect_state[connection] = new_status

    def _trigger_connect_handlers(self, connection):
        for h in self._connect_handlers.get(connection, []):
            asyncio.create_task(self._safe_handler_call(h, connection))

    async def _safe_handler_call(self, handler, arg):
        """Run handler safely in its own task."""
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(arg)
            else:
                handler(arg)
        except Exception as e:
            handler_name = getattr(handler, "__qualname__", getattr(handler, "__name__", repr(handler)))
            print(f"⚠️ Handler error in {handler_name}")
            try:
                print(f"   Payload: {repr(arg)}")
            except Exception:
                print("   Payload: <unrepresentable>")

            print("   Traceback:")
            traceback.print_exc()

    # ----------------------------------------------------------------------
    # Communication Loops
    # ----------------------------------------------------------------------

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
                    # Update connection status
                    for connection, vars_dict in result.items():
                        for var, value in vars_dict.items():
                            if var.startswith("connection_") and var.endswith("_status"):
                                conn_name = var[len("connection_"):-len("_status")]
                                self._handle_connection_status_update(conn_name, value)

                    self.variables.update(result)
                    self.generate_snippets()
                print(f"📥 Cached variables for {len(self.variables)} connections")
                continue

            # variable change
            if data.get("event") == "variables_changed":
                payload = data.get("payload", {})
                variables_created = False
                for connection, updates in payload.items():
                    # check if any new keys were added, for generating snippets
                    existing_vars = self.variables.setdefault(connection, {})
                    variables_created = bool(set(updates.keys()) - set(existing_vars.keys()))

                    # update internal variable states
                    self.variables.setdefault(connection, {}).update(updates)
                    await self._dispatch(connection, updates)

                if variables_created:
                    print("📝 Detected new variables — regenerating snippets")
                    self.generate_snippets()

            # button events
            elif data.get("event") == "updateButtonState":
                print(f"🎛 Button update: {data.get('payload')}")

            # interaction events
            elif data.get("event") == "interaction":
                payload = data.get("payload", {})
                page = payload.get("page")
                x = payload.get("x")
                y = payload.get("y")
                event_type = payload.get("event")
                value = payload.get("value")

                if event_type == "press":
                    type_key = "down" if value else "up"
                elif event_type == "rotate":
                    type_key = "rotate"
                else:
                    type_key = None

                if type_key:
                    for h in self._button_handlers.get((page, x, y, type_key), []):
                        asyncio.create_task(self._safe_handler_call(h, payload))

            # unknown
            else:
                print("🔔 Event:", data.get("event"), data.get("payload"))

    # ----------------------------------------------------------------------
    # Code Snippets
    # ----------------------------------------------------------------------

    def generate_snippets(self, delay=2.0):
        if self._snippet_regen_task and not self._snippet_regen_task.done():
            return

        async def regen():
            await asyncio.sleep(delay)
            await self._generate_snippets()

        self._snippet_regen_task = asyncio.create_task(regen())

    async def _generate_snippets(self):

        snippets = await self._generate_variable_snippets() | await self._generate_action_snippets(await self._call("queryActions"))

        SNIPPET_PATH = Path("/indirector/snippets/python.json")
        SNIPPET_PATH.parent.mkdir(parents=True, exist_ok=True)

        def write_file():
            with SNIPPET_PATH.open("w") as f:
                json.dump(snippets, f, indent=2)
            print(f"🧩 Snippets updated ({len(snippets)} total)")

        await asyncio.to_thread(write_file)

    async def _generate_variable_snippets(self):
        vars = {
            connection: list(vars_dict.keys())
            for connection, vars_dict in self.variables.items()
            if vars_dict
        }

        snippets = {}

        # Add variable update handler snippets
        for connection, variables in vars.items():
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
        for event in ("button_down", "button_up", "rotate"):
            prefix_base = f"on_{event}"
            snippets[prefix_base] = {
                "prefix": f"@companion.{prefix_base}",
                "body": [
                    f"@companion.{prefix_base}(page=\"${{1:page_name}}\", x=${{2:0}}, y=${{3:0}})\n"
                    f"async def ${{4:handler_name}}(payload):\n"
                    f"    ${{5:pass # handle button {event}}}"
                ],
                "description": f"{prefix_base} decorator"
            }

        return  snippets

    async def _generate_action_snippets(self, actions_json: dict):
        """
        Generate VSCode snippets for all available Companion actions.
        Each snippet lets you call: companion.action("<connection>", "<action>", options={...})
        """

        snippets = {}

        connections = sorted(actions_json.keys())
        if connections:
            # VSCode choice list: ${1|vmix,atem,internal|}
            choice_str = ",".join(connections)

            snippets["companion_on_connect"] = {
                "prefix": "@companion.on_connect",
                "body": [
                    f"@companion.on_connect(\"${{1|{choice_str}|}}\")\n"
                    f"async def ${{2:handler_name}}(connection):\n"
                    f"    ${{3:pass}}"
                ],
                "description": "on_connect decorator with connection dropdown",
            }

        for connection, actions in actions_json.items():
            for action_id, action_def in actions.items():
                label = action_def.get("label", action_id)
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

    # ----------------------------------------------------------------------
    # Main entry
    # ----------------------------------------------------------------------

    async def run(self):
        reconnect_delay = 1
        while True:
            try:
                async with websockets.connect(self.url) as ws:
                    self._ws = ws
                    print("✅ Connected to Companion WebSocketBridge")

                    self._sender_task = asyncio.create_task(self._send_loop())
                    self._receiver_task = asyncio.create_task(self._recv_loop())

                    # initial variable snapshot
                    await self._send_queue.put({
                        "id": 1,
                        "method": "queryVariables"
                    })

                    await asyncio.gather(self._sender_task, self._receiver_task)

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
