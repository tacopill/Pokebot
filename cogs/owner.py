from discord.ext import commands
import discord

from utils.orm import FoundPokemon
from utils import checks


class Owner:
    def __init__(self, bot):
        self.bot = bot

    async def __local_check(self, ctx):
        return await self.bot.is_owner(ctx.author)

    @commands.command()
    async def playing(self, ctx, *, status: str):
        """Sets the 'Playing' message for the bot."""
        await self.bot.change_presence(game=discord.Game(name=status))

###################
#                 #
# COGS            #
#                 #
###################

    @commands.command(hidden=True)
    async def reload(self, ctx, *, ext):
        """Reload a cog."""
        if not ext.startswith('cogs.'):
            ext = f'cogs.{ext}'
        try:
            self.bot.unload_extension(ext)
        except:
            pass
        try:
            self.bot.load_extension(ext)
        except Exception as e:
            await ctx.send(e)
        else:
            await ctx.send(f'Cog {ext} reloaded.')

    @commands.command(hidden=True)
    async def load(self, ctx, *, ext):
        """Load a cog."""
        if not ext.startswith('cogs.'):
            ext = f'cogs.{ext}'
        try:
            self.bot.load_extension(ext)
        except Exception as e:
            await ctx.send(e)
        else:
            await ctx.send(f'Cog {ext} loaded.')

    @commands.command(hidden=True)
    async def unload(self, ctx, *, ext):
        """Unload a cog."""
        if not ext.startswith('cogs.'):
            ext = f'cogs.{ext}'
        try:
            self.bot.unload_extension(ext)
        except:
            await ctx.send(f'Cog {ext} is not loaded.')
        else:
            await ctx.send(f'Cog {ext} unloaded.')

###################
#                 #
# DATABASE        #
#                 #
###################

    @checks.no_delete
    @commands.command(hidden=True, name='execute')
    async def _execute(self, ctx, *, sql: str):
        await ctx.con.execute(sql)
        await ctx.message.add_reaction('\N{WHITE HEAVY CHECK MARK}')

    @checks.no_delete
    @commands.command(hidden=True, name='fetchval')
    async def _fetchval(self, ctx, *, sql: str):
        val = await ctx.con.fetchval(sql)
        await ctx.send(val)

    @commands.command()
    async def test(self, ctx, num: int):
        p = await FoundPokemon.from_id(ctx, num)
        print(p)


def setup(bot):
    bot.add_cog(Owner(bot))
