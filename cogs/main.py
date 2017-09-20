import datetime

from discord.ext import commands
import asyncpg
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

###################
#                 #
# PLONKING        #
#                 #
###################

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def plonk(self, ctx, user: discord.Member):
        """Adds a user to the bot's blacklist"""
        try:
            async with ctx.con.transaction():
                await ctx.con.execute("""
                    INSERT INTO plonks (guild_id, user_id) VALUES ($1, $2)
                    """, ctx.guild.id, user.id)
        except asyncpg.UniqueViolationError:
            await ctx.send('User is already plonked.')
        else:
            await ctx.send('User has been plonked.')

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def unplonk(self, ctx, user: discord.Member):
        """Removes a user from the bot's blacklist"""
        async with ctx.con.transaction():
            res = await ctx.con.execute("""
                DELETE FROM plonks WHERE guild_id = $1 and user_id = $2
                """, ctx.guild.id, user.id)
        deleted = int(res.split()[-1])
        if deleted:
            await ctx.send('User is no longer plonked.')
        else:
            await ctx.send('User is not plonked.')


def setup(bot):
    bot.add_cog(Main(bot))
