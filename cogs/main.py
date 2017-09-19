import datetime

from discord.ext import commands
import discord


###################
#                 #
# MAIN            #
#                 #
###################


class Main:
    def __init__(self, bot):
        self.bot = bot

###################
#                 #
# MISCELLANEOUS   #
#                 #
###################

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def playing(self, ctx, *, status: str):
        """Sets the 'Playing' message for the bot."""
        await self.bot.change_presence(game=discord.Game(name=status))

    def get_bot_uptime(self, *, brief=False):
        now = datetime.datetime.utcnow()
        delta = now - self.bot.uptime
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        days, hours = divmod(hours, 24)

        if not brief:
            if days:
                fmt = '{d} days, {h} hours, {m} minutes, and {s} seconds'
            else:
                fmt = '{h} hours, {m} minutes, and {s} seconds'
        else:
            fmt = '{h}h {m}m {s}s'
            if days:
                fmt = '{d}d ' + fmt

        return fmt.format(d=days, h=hours, m=minutes, s=seconds)

    @commands.command()
    async def uptime(self, ctx):
        """Tells you how long the bot has been up for."""
        await ctx.send('Uptime: **{}**'.format(self.get_bot_uptime()))


def setup(bot):
    bot.add_cog(Main(bot))
