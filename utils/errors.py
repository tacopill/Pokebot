from discord.ext import commands


class WrongChannel(commands.CheckFailure):
    def __init__(self, channel=None):
        self.channel = channel
