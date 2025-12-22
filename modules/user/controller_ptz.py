from fleetcommand import companion
import math


class PTZ:
    def __init__(self, visca_connection, pan_speed, tilt_speed, zoom_speed):
        self.visca_connection_name = visca_connection
        self.max_pan_speed = pan_speed
        self.max_tilt_speed = tilt_speed
        self.max_zoom_speed = zoom_speed

    @staticmethod
    def _axis_to_dir_speed(value: float, max_speed: int):
        """
        Convert a gamepad axis in [-1.0, 1.0] to (direction, speed).
        direction: 'pos', 'neg', or 'stop'
        speed: 0..max_speed
        """
        if abs(value) == 0:
            return "stop", 0

        direction = "pos" if value > 0 else "neg"
        speed = int(round(abs(value) * max_speed))
        if speed == 0:
            speed = 1  # if we're outside deadzone, at least speed 1
        speed = max(1, min(max_speed, speed))
        return direction, speed

    @staticmethod
    def build_visca_pan_tilt_cmd(pan_dir: str, pan_speed: int, tilt_dir: str, tilt_speed: int) -> str:
        """
        Build a VISCA Pan/Tilt Drive command as a hex string.
        Fill the direction bytes according to PTZOptics VISCA docs.
        This returns something like:
          '81 01 06 01 pp tt xx yy FF'
        where:
          pp = pan speed byte (hex)
          tt = tilt speed byte
          xx = pan direction nibble(s)
          yy = tilt direction nibble(s)
        """
        # Example mapping – replace with real direction codes from the docs:
        pan_dir_byte = {
            "neg": "01",  # e.g. left
            "pos": "02",  # e.g. right
            "stop": "03",  # stop / no pan
        }[pan_dir]

        tilt_dir_byte = {
            "neg": "01",  # e.g. down
            "pos": "02",  # e.g. up
            "stop": "03",  # stop / no tilt
        }[tilt_dir]

        # Format speed bytes as two-digit hex (e.g. 0x0A -> '0A')
        pan_speed_hex = f"{pan_speed:02X}"
        tilt_speed_hex = f"{tilt_speed:02X}"

        # Replace '81 01 06 01 ... FF' structure with the exact one from PTZOptics
        cmd = f"81 01 06 01 {pan_speed_hex} {tilt_speed_hex} {pan_dir_byte} {tilt_dir_byte} FF"
        return cmd

    @staticmethod
    def build_visca_zoom_cmd(zoom_dir: str, zoom_speed: int) -> str:
        """
        Build a VISCA Zoom command as a hex string.
        Something like:
          '81 01 04 07 zz FF'
        where zz encodes direction + speed.
        """
        if zoom_dir == "stop" or zoom_speed == 0:
            # Replace with your camera's "zoom stop" command
            return "81 01 04 07 00 FF"

        # Example encoding – you’ll fill this according to the VISCA spec
        # Often the high nibble is direction (tele/wide) and low nibble is speed.
        # e.g. 0x2n for tele, 0x3n for wide, where n is 0..7
        if zoom_dir == "pos":  # e.g. zoom in / tele
            base = 0x20
        else:  # e.g. zoom out / wide
            base = 0x30

        speed_nibble = max(0, min(7, zoom_speed))  # camera-specific range
        zoom_byte = base + speed_nibble

        zoom_hex = f"{zoom_byte:02X}"
        cmd = f"81 01 04 07 {zoom_hex} FF"
        return cmd

    @companion.debounce(min_delay=0.1)
    @companion.repeat_with_reset(attempts=5, delay=0.5)
    async def control_ptz(self, pan_ctrl, tilt_ctrl, zoom_ctrl):
        # --- Pan / Tilt ---
        pan_dir, pan_speed = self._axis_to_dir_speed(pan_ctrl, self.max_pan_speed)
        tilt_dir, tilt_speed = self._axis_to_dir_speed(tilt_ctrl, self.max_tilt_speed)

        if pan_dir == "stop" and tilt_dir == "stop":
            # optional: send an explicit "stop" command
            cmd = self.build_visca_pan_tilt_cmd("stop", 0, "stop", 0)
        else:
            cmd = self.build_visca_pan_tilt_cmd(pan_dir, pan_speed, tilt_dir, tilt_speed)

        await companion.action(self.visca_connection_name, "custom", options={"custom": cmd, "command_parameters": ""})

        # --- Zoom ---
        zoom_dir, zoom_speed = self._axis_to_dir_speed(zoom_ctrl, self.max_zoom_speed)
        cmd = self.build_visca_zoom_cmd(zoom_dir, zoom_speed)
        await companion.action(self.visca_connection_name, "custom", options={"custom": cmd, "command_parameters": ""})


ptzs = [
    PTZ(visca_connection='ptzoptics1-visca', pan_speed=0x18, tilt_speed=0x14, zoom_speed=0x07)
]

SELECTED_PTZ = 0

@companion.on_change("gamepad-io", variable="axis_0_val")
@companion.on_change("gamepad-io", variable="axis_1_val")
@companion.on_change("gamepad-io", variable="axis_5_val")
@companion.on_change("gamepad-io", variable="button_0_val")
async def get_ptz_vars(payload):
    pan = float(companion.var("gamepad-io", var="axis_0_val") or 0)
    tilt = float(companion.var("gamepad-io", var="axis_1_val") or 0)
    zoom = float(companion.var("gamepad-io", var="axis_5_val") or 0)

    if companion.var("gamepad-io", var="button_0_val", default=False):
        await ptzs[SELECTED_PTZ].control_ptz(pan, tilt, zoom)
    else:
        await ptzs[SELECTED_PTZ].control_ptz(pan / 2, tilt / 2, zoom / 2)
