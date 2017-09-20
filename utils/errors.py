from discord.ext import commands


class PokemonNotFound(Exception):
    """The Pokemon could not be constructed."""
    pass


class WrongChannel(commands.CheckFailure):
    def __init__(self, channel=None):
        self.channel = channel
