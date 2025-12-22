from fleetcommand import companion


class TurnOff(companion.Button):

    async def on_down(self):
        for key in filter(lambda k: k.startswith("surface_streamdeck_") and k.endswith("_name"), companion.variables['internal'].keys()):
            await companion.action("internal", "set_brightness", options={
                "controller_from_variable": False, "controller": companion.variables['internal'][key], "controller_variable": "self", "brightness": 0
            })


class TurnOn(companion.Button):

    async def on_down(self):
        for key in filter(lambda k: k.startswith("surface_streamdeck_") and k.endswith("_name"), companion.variables['internal'].keys()):
            await companion.action("internal", "set_brightness", options={
                "controller_from_variable": False, "controller": companion.variables['internal'][key], "controller_variable": "self", "brightness": 100
            })
