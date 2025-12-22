from fleetcommand import companion

"""
🚀 Companion Automation API Examples

This file demonstrates the various ways to interact with Companion using Python.

Event-based decorators:
  @companion.on_change(connection, variable="var_name")
  @companion.on_change(connection, prefix="prefix_")
  @companion.on_change(connection, suffix="_suffix")
  @companion.on_change(connection, regex=r"^pattern.*$")
  @companion.on_connect(connection)
  @companion.requires(connection1, connection2, ...)

Utility decorators:
  @companion.debounce(min_delay=0.5, group_by="key")
  @companion.repeat_with_reset(attempts=3, delay=0.1)

API methods:
  companion.action(connection, action_id, options={})
  companion.var(connection, var_name, default=None)
  companion.enable_cast(*connections)
  await companion.action_multi(action1, action2, ...)

Custom buttons:
  Inherit from companion.Button and implement:
    - async def on_init(self)
    - async def on_down(self)
    - async def on_up(self)
    - async def on_rotate(self, direction: bool)

💡 Tip: Create additional Python files in this directory to organize your automations!
"""

# Enable smart type casting for all connections
# This converts string variables like "123" → 123, "true" → True, etc.
companion.enable_cast()


# =============================================================================
# EXAMPLE 1: Variable Change Handler
# =============================================================================

@companion.on_change("internal", variable="time_s")
async def log_uptime_every_second(event):
    """Log Companion uptime every second."""
    uptime = companion.var("internal", "uptime")
    print(f"🕒 Companion uptime: {uptime}s")


# =============================================================================
# EXAMPLE 2: Prefix Matching
# =============================================================================

@companion.on_change("vmix", prefix="input_")
async def vmix_input_change(event):
    """React to any VMix input variable change."""
    print(f"🎬 VMix variable changed: {event.variable} = {event.value}")


# =============================================================================
# EXAMPLE 3: Custom Button Class
# =============================================================================

class DemoButton(companion.Button):
    """
    Example software-defined button for Companion.

    To use this button:
    1. In Companion, create a "Custom" button
    2. Set the button's "Python Class ID" option to "DemoButton"
    3. The button will automatically connect to this class

    Features demonstrated:
    - Initialization with custom appearance
    - Press/release handling with visual feedback
    - Displaying dynamic data (uptime, iteration number)
    - Rotation handling for encoders
    """

    async def on_init(self):
        """Called once when the button is first created."""
        print(f"✨ DemoButton initialized at page={self.page} ({self.page_name}), row={self.row}, col={self.col}")
        await self.set_text("Ready")
        await self.set_bg_color(0.2, 0.2, 0.2)  # Dark gray
        await self.set_text_color(1, 1, 1)  # White text

    async def on_down(self):
        """Called when the button is pressed."""
        uptime = companion.var("internal", "uptime", default="?")
        print(f"🔽 DemoButton pressed [page={self.page}, row={self.row}, col={self.col}, iteration={self.iteration}]")

        # Update button appearance
        await self.set_text(f"{uptime}s")
        await self.set_bg_color(1, 0, 0)  # Red when pressed

        # Example: Trigger a Companion action (commented out - uncomment to use)
        # await companion.action("vmix", "videoActions", {
        #     "input": "1",
        #     "inputType": False,
        #     "functionID": "Pause"
        # })

    async def on_up(self):
        """Called when the button is released."""
        print(f"🔼 DemoButton released [iteration={self.iteration}]")

        # Show iteration number on release
        await self.set_text(str(self.iteration))
        await self.set_bg_color(0, 0, 1)  # Blue when released

    async def on_rotate(self, direction: bool):
        """Called when an encoder is rotated."""
        rotation = "right ➡️" if direction else "left ⬅️"
        print(f"🔄 DemoButton rotated {rotation} [page={self.page}, row={self.row}, col={self.col}, iteration={self.iteration}]")

        # Visual feedback for rotation direction
        if direction:
            await self.set_bg_color(0, 1, 0)  # Green for right
        else:
            await self.set_bg_color(1, 1, 0)  # Yellow for left


# =============================================================================
# TODO: Future Enhancements
# =============================================================================

# TODO: Live button feedback and rendering
#   - Buttons could subscribe to variable changes and auto-update
#   - Queue button updates to avoid race conditions
#   - Batch updates for better performance

# TODO: Button state management
#   - Persist button state across restarts
#   - Share state between button instances
#   - Implement toggle/counter/state machine patterns

# TODO: Advanced button patterns
#   - Multi-action buttons (short press vs long press)
#   - Button groups (radio buttons, linked controls)
#   - Dynamic button creation based on external data
