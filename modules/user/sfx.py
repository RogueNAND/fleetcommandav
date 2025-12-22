from fleetcommand import companion


class PlaySfx(companion.Button):

    async def on_down(self):
        # Launch an application (e.g., notepad.exe)
        await companion.action("scriptlauncher-fleetcommand", "windowsLaunchApp", options={
            "app": "ffplay.exe", "args": r"-nodisp -autoexit -loglevel quiet C:\Users\rogue\Music\SFX\Lizard.mp3"
        })

    async def on_rotate(self, direction: bool):
        await self.on_down()
